from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from app.models import ProxyConfig


DEFAULT_TOOL_DIR = Path("tools") / "tqk_redirector"
DEFAULT_EXE_NAME = "TqkLibrary.WinDivert.Demo.exe"
DEFAULT_DLL_NAME = "TqkLibrary.WinDivert.Demo.dll"
DEFAULT_DOH_ENDPOINT = "https://1.1.1.1/dns-query"


@dataclass
class TqkRedirectStatus:
    available: bool
    message: str


class TqkRedirectEngine:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self._processes: dict[int, subprocess.Popen[str]] = {}
        self._last_error: Optional[str] = None
        self._log_dir = project_root / "tools" / "tqk_redirector" / "logs"

    @property
    def running(self) -> bool:
        self._cleanup_finished()
        return bool(self._processes)

    @property
    def last_error(self) -> Optional[str]:
        for process in list(self._processes.values()):
            if process.poll() not in (None, 0):
                return self._process_output(process) or self._last_error
        return self._last_error

    def status(self) -> TqkRedirectStatus:
        command, cwd = self._base_command()
        if command is None or cwd is None:
            return TqkRedirectStatus(
                False,
                "Tqk redirector was not found. Put the built helper in tools\\tqk_redirector or set TQK_REDIRECTOR_EXE.",
            )
        missing = [
            name
            for name in ("WinDivert.dll", "WinDivert64.sys")
            if not (cwd / name).is_file() and shutil.which(name) is None
        ]
        if missing:
            return TqkRedirectStatus(
                False,
                f"Missing {', '.join(missing)} next to the Tqk redirector.",
            )
        return TqkRedirectStatus(True, f"Tqk redirector ready: {' '.join(command)}")

    def start(self, instance_index: int, pid: int, proxy: ProxyConfig) -> None:
        current = self._processes.get(instance_index)
        if current is not None and current.poll() is None:
            return
        self.stop(instance_index)

        status = self.status()
        if not status.available:
            raise RuntimeError(status.message)

        command, cwd = self._base_command()
        if command is None or cwd is None:
            raise RuntimeError(status.message)

        command = command + [
            "proxy",
            "--proxy",
            self._proxy_url(proxy),
            "--process",
            str(pid),
            "--follow-children",
            "--secure-dns",
            "--doh",
            os.environ.get("TQK_DOH_ENDPOINT", DEFAULT_DOH_ENDPOINT),
            "--exit-when-process-gone",
        ]
        if os.environ.get("TQK_SUSPEND_ON_ATTACH", "").strip().lower() in {"1", "true", "yes"}:
            command.append("--suspend-on-attach")

        self._last_error = None
        self._log_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["WINDIVERT_LOG"] = str(
            self._log_dir / f"windivert-instance-{instance_index}-pid-{pid}-{int(time.time())}.log"
        )
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self._processes[instance_index] = process
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            return

        output = self._process_output(process)
        self._processes.pop(instance_index, None)
        self._last_error = output or "Tqk redirector exited immediately"
        raise RuntimeError(self._last_error)

    def stop(self, instance_index: int) -> None:
        process = self._processes.pop(instance_index, None)
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def stop_all(self) -> None:
        for instance_index in list(self._processes):
            self.stop(instance_index)

    def _cleanup_finished(self) -> None:
        for instance_index, process in list(self._processes.items()):
            if process.poll() is not None:
                self._processes.pop(instance_index, None)

    def _base_command(self) -> tuple[Optional[list[str]], Optional[Path]]:
        configured = os.environ.get("TQK_REDIRECTOR_EXE", "").strip()
        if configured:
            path = Path(configured)
            if path.is_file():
                if path.suffix.lower() == ".dll":
                    return ["dotnet", str(path)], path.parent
                return [str(path)], path.parent

        local_dir = self.project_root / DEFAULT_TOOL_DIR
        local_exe = local_dir / DEFAULT_EXE_NAME
        if local_exe.is_file():
            return [str(local_exe)], local_dir
        local_dll = local_dir / DEFAULT_DLL_NAME
        if local_dll.is_file():
            return ["dotnet", str(local_dll)], local_dir

        discovered = shutil.which(DEFAULT_EXE_NAME)
        if discovered:
            path = Path(discovered)
            return [str(path)], path.parent
        return None, None

    @staticmethod
    def _proxy_url(proxy: ProxyConfig) -> str:
        credentials = ""
        if proxy.username:
            credentials = quote(proxy.username, safe="")
            if proxy.password is not None:
                credentials += f":{quote(proxy.password, safe='')}"
            credentials += "@"
        scheme = "socks5" if proxy.scheme.lower() in {"socks", "socks5"} else proxy.scheme.lower()
        return f"{scheme}://{credentials}{proxy.host}:{proxy.port}"

    @staticmethod
    def _process_output(process: subprocess.Popen[str]) -> str:
        stdout = ""
        stderr = ""
        if process.stdout is not None:
            try:
                stdout = process.stdout.read()
            except OSError:
                stdout = ""
        if process.stderr is not None:
            try:
                stderr = process.stderr.read()
            except OSError:
                stderr = ""
        return "\n".join(part.strip() for part in (stdout, stderr) if part.strip())
