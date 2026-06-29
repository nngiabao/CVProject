from __future__ import annotations

import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, TextIO

from app.models import ProxyConfig


TOOL_DIR = Path("tools") / "tun2socks"
TUN2SOCKS_EXE = "tun2socks.exe"
WINTUN_DLL = "wintun.dll"
TUN_NAME = "GrowStoneTun"
TUN_ADDR = "198.18.0.1"
TUN_MASK = "255.255.255.0"
ROUTE_A = "0.0.0.0"
ROUTE_B = "128.0.0.0"
ROUTE_MASK = "128.0.0.0"
START_TIMEOUT_SECONDS = 10.0


@dataclass
class Tun2SocksStatus:
    available: bool
    message: str


class Tun2SocksEngine:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.tool_dir = project_root / TOOL_DIR
        self.exe = self.tool_dir / TUN2SOCKS_EXE
        self.wintun = self.tool_dir / WINTUN_DLL
        self._process: Optional[subprocess.Popen[str]] = None
        self._proxy: Optional[ProxyConfig] = None
        self._instance_indexes: set[int] = set()
        self._proxy_ip: Optional[str] = None
        self._gateway: Optional[str] = None
        self._last_error: Optional[str] = None
        self._log_handle: Optional[TextIO] = None
        self._log_path: Optional[Path] = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def last_error(self) -> Optional[str]:
        if self._process is not None and self._process.poll() not in (None, 0):
            return self._last_error or f"tun2socks exited with code {self._process.returncode}"
        return self._last_error

    def status(self) -> Tun2SocksStatus:
        if not self.exe.exists():
            return Tun2SocksStatus(False, f"Missing {self.exe}")
        if not self.wintun.exists():
            return Tun2SocksStatus(False, f"Missing {self.wintun}")
        return Tun2SocksStatus(True, "tun2socks + Wintun ready")

    def start_many(self, instance_index: int, pids: set[int], proxy: ProxyConfig) -> None:
        status = self.status()
        if not status.available:
            raise RuntimeError(status.message)

        with self._lock:
            if self.running:
                if self._proxy is not None and self._proxy.connection_url != proxy.connection_url:
                    raise RuntimeError(
                        "The Wintun tunnel is already running with another proxy. "
                        "Stop proxy routing before switching proxies."
                    )
                self._instance_indexes.add(instance_index)
                return

            self._last_error = None
            self._proxy = proxy
            self._instance_indexes = {instance_index}
            self._start_tunnel(proxy)

    def stop(self, instance_index: int) -> None:
        with self._lock:
            self._instance_indexes.discard(instance_index)
            if not self._instance_indexes:
                self._stop_tunnel()

    def stop_all(self) -> None:
        with self._lock:
            self._instance_indexes.clear()
            self._stop_tunnel()

    def _start_tunnel(self, proxy: ProxyConfig) -> None:
        self._gateway = _default_gateway()
        self._proxy_ip = _resolve_ipv4(proxy.host)
        if not self._gateway:
            raise RuntimeError("Could not find the current default gateway")
        if not self._proxy_ip:
            raise RuntimeError(f"Could not resolve proxy host {proxy.host}")

        self._delete_tunnel_routes()
        command = [
            str(self.exe),
            "--device",
            f"tun://{TUN_NAME}",
            "--proxy",
            proxy.connection_url,
            "--loglevel",
            "info",
        ]
        log_dir = self.tool_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = log_dir / f"tun2socks-{int(time.time())}.log"
        self._log_handle = self._log_path.open("w", encoding="utf-8")
        self._process = subprocess.Popen(
            command,
            cwd=str(self.tool_dir),
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        try:
            interface_index = _wait_for_ipv4_interface(
                TUN_NAME,
                START_TIMEOUT_SECONDS,
                self._process,
                self._log_path,
                self._log_handle,
            )
            _set_tunnel_address(interface_index)
            _add_route(self._proxy_ip, "mask", "255.255.255.255", self._gateway, "metric", "1")
            _add_route(ROUTE_A, "mask", ROUTE_MASK, TUN_ADDR, "metric", "1")
            _add_route(ROUTE_B, "mask", ROUTE_MASK, TUN_ADDR, "metric", "1")
        except Exception:
            self._stop_tunnel()
            raise

    def _stop_tunnel(self) -> None:
        self._delete_tunnel_routes()
        if self._proxy_ip and self._gateway:
            _run_route_ignore_error("delete", self._proxy_ip, "mask", "255.255.255.255", self._gateway)
            _run_route_ignore_error("delete", self._proxy_ip, "mask", "255.255.255.255")

        if self._process is not None:
            if self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=4)
            if self._process.returncode not in (0, None):
                self._last_error = self._last_error or f"tun2socks exited with code {self._process.returncode}"
            self._process = None
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        self._log_path = None

        self._proxy = None
        self._proxy_ip = None
        self._gateway = None

    def _delete_tunnel_routes(self) -> None:
        _run_route_ignore_error("delete", ROUTE_A, "mask", ROUTE_MASK, TUN_ADDR)
        _run_route_ignore_error("delete", ROUTE_B, "mask", ROUTE_MASK, TUN_ADDR)
        _run_route_ignore_error("delete", ROUTE_A, "mask", ROUTE_MASK)
        _run_route_ignore_error("delete", ROUTE_B, "mask", ROUTE_MASK)


def _resolve_ipv4(host: str) -> Optional[str]:
    try:
        return socket.gethostbyname(host)
    except OSError:
        return None


def _default_gateway() -> Optional[str]:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "$cfg = Get-NetIPConfiguration | "
            "Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | "
            "Select-Object -First 1; "
            "if ($cfg) { $cfg.IPv4DefaultGateway.NextHop }"
        ),
    ]
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=10,
    )
    gateway = result.stdout.strip().splitlines()
    return gateway[0].strip() if gateway else None


def _wait_for_ipv4_interface(
    name: str,
    timeout: float,
    process: subprocess.Popen[str],
    log_path: Optional[Path],
    log_handle: Optional[TextIO],
) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            log_tail = _read_log_tail(log_path, log_handle)
            message = f"tun2socks exited before Wintun adapter {name} appeared"
            if log_tail:
                message += f": {log_tail}"
            raise RuntimeError(message)

        interface_index = _ipv4_interface_index(name)
        if interface_index is not None:
            return interface_index
        time.sleep(0.25)

    log_tail = _read_log_tail(log_path, log_handle)
    message = f"Wintun IPv4 interface {name} did not appear"
    if log_tail:
        message += f": {log_tail}"
    raise RuntimeError(message)


def _ipv4_interface_index(name: str) -> Optional[int]:
    result = subprocess.run(
        ["netsh", "interface", "ipv4", "show", "interfaces"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=5,
    )
    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        if parts[-1].lower() != name.lower():
            continue
        try:
            return int(parts[0])
        except ValueError:
            return None
    return None


def _read_log_tail(log_path: Optional[Path], log_handle: Optional[TextIO]) -> str:
    if log_handle is not None:
        try:
            log_handle.flush()
        except OSError:
            pass
    if log_path is None:
        return ""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " | ".join(lines[-5:])


def _run_netsh(*args: str) -> None:
    _run_checked(["netsh", *args])


def _set_tunnel_address(interface_index: int) -> None:
    result = _run_capture(
        [
            "netsh",
            "interface",
            "ipv4",
            "set",
            "address",
            f"name={interface_index}",
            "static",
            TUN_ADDR,
            TUN_MASK,
        ]
    )
    if result.returncode == 0:
        return
    message = (result.stderr or result.stdout or "command failed").strip()
    if "already exists" in message.lower():
        return
    raise RuntimeError(
        "netsh interface ipv4 set address "
        f"name={interface_index} static {TUN_ADDR} {TUN_MASK}: {message}"
    )


def _run_route(*args: str) -> None:
    _run_checked(["route", *args])


def _add_route(*args: str) -> None:
    result = _run_capture(["route", "add", *args])
    if result.returncode == 0:
        return
    message = (result.stderr or result.stdout or "command failed").strip()
    if "object already exists" in message.lower() or "already exists" in message.lower():
        return
    raise RuntimeError(f"route add {' '.join(args)}: {message}")


def _run_route_ignore_error(*args: str) -> None:
    subprocess.run(
        ["route", *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=10,
    )


def _run_checked(command: list[str]) -> None:
    result = _run_capture(command)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "command failed").strip()
        raise RuntimeError(f"{' '.join(command)}: {message}")


def _run_capture(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=10,
    )
