from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from app.models import ProxyConfig


DEFAULT_TOOL_PATH = Path("tools") / "tun2socks" / "tun2socks.exe"


@dataclass
class Tun2SocksStatus:
    available: bool
    message: str


class Tun2SocksEngine:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self._process: Optional[subprocess.Popen[str]] = None
        self._proxy_key: Optional[str] = None
        self._last_error: Optional[str] = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def last_error(self) -> Optional[str]:
        if self._process is not None and self._process.poll() not in (None, 0):
            return self._process_output() or self._last_error
        return self._last_error

    def status(self) -> Tun2SocksStatus:
        executable = self._executable()
        if executable is None:
            return Tun2SocksStatus(
                False,
                "tun2socks.exe was not found. Put it in tools\\tun2socks or set TUN2SOCKS_EXE.",
            )
        wintun = executable.parent / "wintun.dll"
        if not wintun.is_file() and shutil.which("wintun.dll") is None:
            return Tun2SocksStatus(
                False,
                "wintun.dll was not found next to tun2socks.exe or on PATH.",
            )
        return Tun2SocksStatus(True, f"tun2socks ready: {executable}")

    def start(self, proxy: ProxyConfig) -> None:
        proxy_key = proxy.connection_url
        if self.running and self._proxy_key == proxy_key:
            return
        self.stop()

        status = self.status()
        if not status.available:
            raise RuntimeError(status.message)

        executable = self._executable()
        if executable is None:
            raise RuntimeError(status.message)

        command = [
            str(executable),
            "--device",
            "wintun",
            "--proxy",
            self._proxy_url(proxy),
        ]
        interface_name = os.environ.get("TUN2SOCKS_INTERFACE", "").strip()
        if interface_name:
            command.extend(["--interface", interface_name])

        self._last_error = None
        self._process = subprocess.Popen(
            command,
            cwd=str(executable.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self._proxy_key = proxy_key
        try:
            self._process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            return
        output = self._process_output()
        self._process = None
        self._proxy_key = None
        self._last_error = output or "tun2socks exited immediately"
        raise RuntimeError(self._last_error)

    def stop(self) -> None:
        process = self._process
        self._process = None
        self._proxy_key = None
        if process is None:
            return
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _process_output(self) -> str:
        if self._process is None:
            return ""
        stdout = ""
        stderr = ""
        if self._process.stdout is not None:
            try:
                stdout = self._process.stdout.read()
            except OSError:
                stdout = ""
        if self._process.stderr is not None:
            try:
                stderr = self._process.stderr.read()
            except OSError:
                stderr = ""
        return "\n".join(part.strip() for part in (stdout, stderr) if part.strip())

    def _executable(self) -> Optional[Path]:
        configured = os.environ.get("TUN2SOCKS_EXE", "").strip()
        if configured:
            path = Path(configured)
            if path.is_file():
                return path
        local = self.project_root / DEFAULT_TOOL_PATH
        if local.is_file():
            return local
        discovered = shutil.which("tun2socks.exe") or shutil.which("tun2socks")
        return Path(discovered) if discovered else None

    @staticmethod
    def _proxy_url(proxy: ProxyConfig) -> str:
        credentials = ""
        if proxy.username:
            credentials = quote(proxy.username, safe="")
            if proxy.password is not None:
                credentials += f":{quote(proxy.password, safe='')}"
            credentials += "@"
        return f"socks5://{credentials}{proxy.host}:{proxy.port}"
