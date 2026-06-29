from __future__ import annotations

import ipaddress
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from app.windows_netstat import tcp_owner_pids, udp_owner_pids


SOCKET_REFRESH_SECONDS = 1.0
WINDIVERT_FILTERS = (
    "outbound and !loopback and (tcp or udp)",
    "outbound and (tcp or udp)",
    "outbound",
)


@dataclass
class GuardStats:
    forwarded: int = 0
    blocked: int = 0
    blocked_tcp: int = 0
    blocked_udp: int = 0
    allowed_dns: int = 0
    protected_pids: int = 0
    block_public_tcp: bool = True
    last_error: Optional[str] = None


@dataclass
class WinDivertGuard:
    protected_pids: set[int] = field(default_factory=set)
    block_public_tcp: bool = True
    stats: GuardStats = field(default_factory=GuardStats)
    _thread: Optional[threading.Thread] = None
    _stop_event: threading.Event = field(default_factory=threading.Event)
    _handle: Optional[Any] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, protected_pids: set[int], block_public_tcp: bool = True) -> None:
        self.stop()
        with self._lock:
            self.protected_pids = {pid for pid in protected_pids if pid > 0}
            self.block_public_tcp = block_public_tcp
            self.stats = GuardStats()
            self.stats.protected_pids = len(self.protected_pids)
            self.stats.block_public_tcp = block_public_tcp
        if not self.protected_pids:
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="windivert-guard", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._handle is not None:
            try:
                self._handle.close()
            except Exception:
                pass
            self._handle = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def update_pids(self, protected_pids: set[int], block_public_tcp: Optional[bool] = None) -> None:
        with self._lock:
            self.protected_pids = {pid for pid in protected_pids if pid > 0}
            self.stats.protected_pids = len(self.protected_pids)
            if block_public_tcp is not None:
                self.block_public_tcp = block_public_tcp
                self.stats.block_public_tcp = block_public_tcp

    def _run(self) -> None:
        tcp_map: dict[tuple[str, int], int] = {}
        udp_map: dict[tuple[str, int], int] = {}
        last_refresh = 0.0

        try:
            import pydivert

            self._handle = _open_windivert_guard(pydivert)
            while not self._stop_event.is_set():
                now = time.monotonic()
                if now - last_refresh >= SOCKET_REFRESH_SECONDS:
                    tcp_map = tcp_owner_pids()
                    udp_map = udp_owner_pids()
                    last_refresh = now

                packet = self._handle.recv()
                owner_pid = self._owner_pid(packet, tcp_map, udp_map)
                if owner_pid in self._protected_pids_snapshot() and self._should_block(packet):
                    self.stats.blocked += 1
                    if packet.tcp is not None:
                        self.stats.blocked_tcp += 1
                    elif packet.udp is not None:
                        self.stats.blocked_udp += 1
                    continue
                if owner_pid in self._protected_pids_snapshot() and self._is_dns_packet(packet):
                    self.stats.allowed_dns += 1

                self._handle.send(packet)
                self.stats.forwarded += 1
        except Exception as exc:
            self.stats.last_error = str(exc)
        finally:
            if self._handle is not None:
                try:
                    self._handle.close()
                except Exception:
                    pass
                self._handle = None

    def _protected_pids_snapshot(self) -> set[int]:
        with self._lock:
            return set(self.protected_pids)

    def _block_public_tcp_snapshot(self) -> bool:
        with self._lock:
            return self.block_public_tcp

    def _should_block(self, packet: object) -> bool:
        if packet.udp is not None:
            return not self._is_dns_packet(packet)
        if not self._block_public_tcp_snapshot():
            return False
        if not self._is_public_destination(packet.dst_addr):
            return False
        return packet.tcp is not None

    @staticmethod
    def _owner_pid(packet: object, tcp_map: dict[tuple[str, int], int], udp_map: dict[tuple[str, int], int]) -> Optional[int]:
        source_key = (packet.src_addr, packet.src_port)
        destination_key = (packet.dst_addr, packet.dst_port)
        if packet.tcp is not None:
            return tcp_map.get(source_key) or tcp_map.get(destination_key)
        if packet.udp is not None:
            return udp_map.get(source_key) or udp_map.get(destination_key)
        return None

    @staticmethod
    def _is_dns_packet(packet: object) -> bool:
        return packet.udp is not None and (packet.dst_port == 53 or packet.src_port == 53)

    @staticmethod
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


def _open_windivert_guard(pydivert: Any) -> Any:
    errors: list[str] = []
    for filter_text in WINDIVERT_FILTERS:
        try:
            handle = pydivert.WinDivert(filter_text)
            handle.open()
            return handle
        except Exception as exc:
            errors.append(f"{filter_text}: {exc}")
    raise RuntimeError("WinDivert guard open failed. " + " | ".join(errors))
