from __future__ import annotations

import ipaddress
import select
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.models import ProxyConfig
from app.routing import BUFFER_SIZE, SocksConnectRequest, _open_upstream_socks5
from app.windows_netstat import tcp_owner_pids


RELAY_HOST = "127.0.0.1"
SOCKET_REFRESH_SECONDS = 0.25
NAT_TTL_SECONDS = 120.0


@dataclass
class PythonRedirectStatus:
    available: bool
    message: str


@dataclass
class NatEntry:
    original_dst_addr: str
    original_dst_port: int
    proxy: ProxyConfig
    updated_at: float


class PythonRedirectEngine:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self._pid_proxies: dict[int, ProxyConfig] = {}
        self._instance_pids: dict[int, set[int]] = {}
        self._nat: dict[tuple[str, int], NatEntry] = {}
        self._last_error: Optional[str] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._handle: Optional[Any] = None
        self._relay = TransparentTcpRelay(self)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def status(self) -> PythonRedirectStatus:
        try:
            import pydivert  # noqa: F401
        except Exception as exc:
            return PythonRedirectStatus(False, f"pydivert import failed: {exc}")
        return PythonRedirectStatus(True, "Python WinDivert redirector ready")

    def start_many(self, instance_index: int, pids: set[int], proxy: ProxyConfig) -> None:
        if not pids:
            raise RuntimeError("No NAT PID is mapped for this instance")
        status = self.status()
        if not status.available:
            raise RuntimeError(status.message)

        self._relay.start()
        with self._lock:
            for pid in pids:
                existing = self._pid_proxies.get(pid)
                if existing is not None and existing.connection_url != proxy.connection_url:
                    raise RuntimeError(f"PID {pid} is already routed by another proxy")
            for pid in pids:
                self._pid_proxies[pid] = proxy
            self._instance_pids.setdefault(instance_index, set()).update(pids)

        if not self.running:
            self._last_error = None
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, name="python-windivert-redirect", daemon=True)
            self._thread.start()

    def stop(self, instance_index: int) -> None:
        with self._lock:
            pids = self._instance_pids.pop(instance_index, set())
            still_used = set().union(*self._instance_pids.values()) if self._instance_pids else set()
            for pid in pids - still_used:
                self._pid_proxies.pop(pid, None)
            self._nat = {
                key: entry
                for key, entry in self._nat.items()
                if entry.proxy.connection_url in {proxy.connection_url for proxy in self._pid_proxies.values()}
            }
        if not self._pid_proxies:
            self.stop_all()

    def stop_all(self) -> None:
        self._stop_event.set()
        if self._handle is not None:
            try:
                self._handle.close()
            except Exception:
                pass
            self._handle = None
        self._relay.stop()
        with self._lock:
            self._pid_proxies.clear()
            self._instance_pids.clear()
            self._nat.clear()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def original_for_client(self, client_addr: str, client_port: int) -> Optional[NatEntry]:
        now = time.monotonic()
        with self._lock:
            self._expire_nat_locked(now)
            entry = self._nat.get((client_addr, client_port)) or self._nat.get(("", client_port))
            if entry is not None:
                entry.updated_at = now
            return entry

    def _run(self) -> None:
        tcp_map: dict[tuple[str, int], int] = {}
        last_refresh = 0.0

        try:
            import pydivert

            self._handle = pydivert.WinDivert("ip and tcp and not impostor")
            self._handle.open()
            while not self._stop_event.is_set():
                now = time.monotonic()
                if now - last_refresh >= SOCKET_REFRESH_SECONDS:
                    tcp_map = tcp_owner_pids()
                    last_refresh = now

                packet = self._handle.recv()
                if self._handle_packet(packet, self._handle, tcp_map):
                    continue
                self._handle.send(packet)
        except Exception as exc:
            self._last_error = str(exc)
        finally:
            if self._handle is not None:
                try:
                    self._handle.close()
                except Exception:
                    pass
                self._handle = None

    def _handle_packet(self, packet: object, handle: object, tcp_map: dict[tuple[str, int], int]) -> bool:
        if packet.tcp is None:
            return False

        if _is_outbound(packet) and self._maybe_redirect_egress(packet, tcp_map):
            handle.send(packet)
            return True

        if _is_inbound(packet) and self._maybe_rewrite_reply(packet):
            handle.send(packet)
            return True

        return False

    def _maybe_redirect_egress(self, packet: object, tcp_map: dict[tuple[str, int], int]) -> bool:
        if packet.dst_addr == RELAY_HOST or packet.dst_port == self._relay.port:
            return False
        if not _is_public_destination(packet.dst_addr):
            return False

        owner_pid = tcp_map.get((packet.src_addr, packet.src_port))
        proxy = self._proxy_for_pid(owner_pid)
        if proxy is None:
            return False

        relay_port = self._relay.port
        if relay_port is None:
            return False

        with self._lock:
            entry = NatEntry(packet.dst_addr, int(packet.dst_port), proxy, time.monotonic())
            self._nat[(packet.src_addr, int(packet.src_port))] = entry
            self._nat[("", int(packet.src_port))] = entry

        packet.dst_addr = RELAY_HOST
        packet.dst_port = relay_port
        return True

    def _maybe_rewrite_reply(self, packet: object) -> bool:
        relay_port = self._relay.port
        if relay_port is None:
            return False
        if packet.src_addr != RELAY_HOST or packet.src_port != relay_port:
            return False

        entry = self.original_for_client(packet.dst_addr, int(packet.dst_port))
        if entry is None:
            return False

        packet.src_addr = entry.original_dst_addr
        packet.src_port = entry.original_dst_port
        return True

    def _proxy_for_pid(self, pid: Optional[int]) -> Optional[ProxyConfig]:
        if pid is None:
            return None
        with self._lock:
            return self._pid_proxies.get(pid)

    def _expire_nat_locked(self, now: float) -> None:
        stale = [key for key, entry in self._nat.items() if now - entry.updated_at > NAT_TTL_SECONDS]
        for key in stale:
            self._nat.pop(key, None)


class TransparentTcpRelay:
    def __init__(self, engine: PythonRedirectEngine) -> None:
        self.engine = engine
        self.port: Optional[int] = None
        self._server: Optional[socket.socket] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((RELAY_HOST, 0))
        server.listen(256)
        server.settimeout(0.5)
        self.port = int(server.getsockname()[1])
        self._server = server
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._serve, name="python-transparent-relay", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self.port = None

    def _serve(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self._server is None:
                    return
                client, address = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle_client, args=(client, address), daemon=True).start()

    def _handle_client(self, client: socket.socket, address: tuple[str, int]) -> None:
        with client:
            client.settimeout(20)
            entry = self.engine.original_for_client(address[0], int(address[1]))
            if entry is None:
                return
            try:
                request = _socks_request_for_ip(entry.original_dst_addr, entry.original_dst_port)
                upstream = _open_upstream_socks5(entry.proxy, request)
            except OSError:
                return
            with upstream:
                _relay(client, upstream, self._stop_event)


def _relay(left: socket.socket, right: socket.socket, stop_event: threading.Event) -> None:
    sockets = [left, right]
    while not stop_event.is_set():
        try:
            readable, _, _ = select.select(sockets, [], [], 0.5)
        except OSError:
            return
        for source in readable:
            target = right if source is left else left
            try:
                data = source.recv(BUFFER_SIZE)
                if not data:
                    return
                target.sendall(data)
            except OSError:
                return


def _socks_request_for_ip(address: str, port: int) -> SocksConnectRequest:
    ip = ipaddress.ip_address(address)
    atyp = 0x04 if ip.version == 6 else 0x01
    return SocksConnectRequest(atyp, ip.packed, port.to_bytes(2, "big"))


def _is_public_destination(address: Optional[str]) -> bool:
    if not address:
        return False
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    )


def _is_outbound(packet: object) -> bool:
    value = getattr(packet, "is_outbound", None)
    if value is not None:
        return bool(value)
    direction = str(getattr(packet, "direction", "")).lower()
    return "outbound" in direction


def _is_inbound(packet: object) -> bool:
    value = getattr(packet, "is_inbound", None)
    if value is not None:
        return bool(value)
    direction = str(getattr(packet, "direction", "")).lower()
    return "inbound" in direction
