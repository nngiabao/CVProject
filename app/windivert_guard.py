from __future__ import annotations

import ipaddress
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from app.windows_netstat import tcp_owner_pids, udp_owner_pids


SOCKET_REFRESH_SECONDS = 1.0


@dataclass(slots=True)
class GuardStats:
    forwarded: int = 0
    blocked: int = 0
    protected_pids: int = 0
    last_error: str | None = None


@dataclass(slots=True)
class WinDivertGuard:
    protected_pids: set[int] = field(default_factory=set)
    stats: GuardStats = field(default_factory=GuardStats)
    _thread: threading.Thread | None = None
    _stop_event: threading.Event = field(default_factory=threading.Event)
    _handle: Any | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, protected_pids: set[int]) -> None:
        self.stop()
        with self._lock:
            self.protected_pids = {pid for pid in protected_pids if pid > 0}
            self.stats = GuardStats()
            self.stats.protected_pids = len(self.protected_pids)
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

    def update_pids(self, protected_pids: set[int]) -> None:
        with self._lock:
            self.protected_pids = {pid for pid in protected_pids if pid > 0}
            self.stats.protected_pids = len(self.protected_pids)

    def _run(self) -> None:
        tcp_map: dict[tuple[str, int], int] = {}
        udp_map: dict[tuple[str, int], int] = {}
        last_refresh = 0.0

        try:
            import pydivert

            self._handle = pydivert.WinDivert("outbound and !loopback and (tcp or udp)")
            self._handle.open()
            while not self._stop_event.is_set():
                now = time.monotonic()
                if now - last_refresh >= SOCKET_REFRESH_SECONDS:
                    tcp_map = tcp_owner_pids()
                    udp_map = udp_owner_pids()
                    last_refresh = now

                packet = self._handle.recv()
                owner_pid = self._owner_pid(packet, tcp_map, udp_map)
                if owner_pid in self._protected_pids_snapshot() and self._is_public_destination(packet.dst_addr):
                    self.stats.blocked += 1
                    continue

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

    @staticmethod
    def _owner_pid(packet: object, tcp_map: dict[tuple[str, int], int], udp_map: dict[tuple[str, int], int]) -> int | None:
        source_key = (packet.src_addr, packet.src_port)
        destination_key = (packet.dst_addr, packet.dst_port)
        if packet.tcp is not None:
            return tcp_map.get(source_key) or tcp_map.get(destination_key)
        if packet.udp is not None:
            return udp_map.get(source_key) or udp_map.get(destination_key)
        return None

    @staticmethod
    def _is_public_destination(address: str | None) -> bool:
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
