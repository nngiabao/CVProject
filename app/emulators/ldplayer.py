from __future__ import annotations

from typing import Optional
import os
import shutil
import subprocess
import time
from ctypes import POINTER, WinDLL, get_last_error
from ctypes.wintypes import BOOL, DWORD, HANDLE
from pathlib import Path
from winreg import HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, KEY_READ, KEY_WOW64_32KEY, KEY_WOW64_64KEY, OpenKey, QueryValueEx

from app.emulators.base import EmulatorProvider
from app.models import EmulatorInstance, InstanceState


class LdPlayerProvider(EmulatorProvider):
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    COMMON_PATHS = (
        Path(r"C:\LDPlayer\LDPlayer9\dnconsole.exe"),
        Path(r"C:\LDPlayer\LDPlayer9\ldconsole.exe"),
        Path(r"C:\LDPlayer\LDPlayer4.0\dnconsole.exe"),
        Path(r"C:\LDPlayer\LDPlayer4.0\ldconsole.exe"),
        Path(r"C:\Program Files\LDPlayer\LDPlayer9\dnconsole.exe"),
        Path(r"C:\Program Files\LDPlayer\LDPlayer9\ldconsole.exe"),
        Path(r"C:\Program Files (x86)\LDPlayer\LDPlayer9\dnconsole.exe"),
        Path(r"C:\Program Files (x86)\LDPlayer\LDPlayer9\ldconsole.exe"),
        Path(r"C:\Program Files\dnplayerext2\dnconsole.exe"),
    )
    UNINSTALL_REGISTRY_PATHS = (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    )

    def __init__(self, console_path: Path) -> None:
        self.console_path = console_path.resolve()

    @property
    def display_name(self) -> str:
        return f"LDPlayer ({self.console_path.parent})"

    @classmethod
    def detect(cls) -> Optional[LdPlayerProvider]:
        configured = os.environ.get("LDPLAYER_CONSOLE")
        candidates: list[Path] = []
        if configured:
            candidates.append(Path(configured))

        for executable_name in ("dnconsole.exe", "ldconsole.exe"):
            executable = shutil.which(executable_name)
            if executable:
                candidates.append(Path(executable))

        candidates.extend(cls.COMMON_PATHS)
        candidates.extend(cls._registry_candidates())

        seen: set[str] = set()
        for candidate in candidates:
            normalized = os.path.normcase(os.path.abspath(candidate))
            if normalized in seen:
                continue
            seen.add(normalized)
            if candidate.is_file():
                return cls(candidate)
        return None

    @classmethod
    def _registry_candidates(cls) -> list[Path]:
        candidates: list[Path] = []
        registry_views = (KEY_READ | KEY_WOW64_64KEY, KEY_READ | KEY_WOW64_32KEY)
        for root in (HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER):
            for registry_path in cls.UNINSTALL_REGISTRY_PATHS:
                for access in registry_views:
                    try:
                        with OpenKey(root, registry_path, 0, access) as uninstall_key:
                            candidates.extend(cls._read_uninstall_entries(uninstall_key))
                    except OSError:
                        continue
        return candidates

    @classmethod
    def _read_uninstall_entries(cls, uninstall_key: object) -> list[Path]:
        import winreg

        candidates: list[Path] = []
        entry_index = 0
        while True:
            try:
                entry_name = winreg.EnumKey(uninstall_key, entry_index)
            except OSError:
                break
            entry_index += 1
            try:
                with OpenKey(uninstall_key, entry_name) as entry:
                    display_name = str(QueryValueEx(entry, "DisplayName")[0])
                    if "ldplayer" not in display_name.lower():
                        continue
                    install_location = str(QueryValueEx(entry, "InstallLocation")[0]).strip()
            except OSError:
                continue
            if not install_location:
                continue
            install_path = Path(install_location)
            candidates.extend((install_path / "dnconsole.exe", install_path / "ldconsole.exe"))
        return candidates

    def _run(self, *arguments: str) -> str:
        result = subprocess.run(
            [str(self.console_path), *arguments],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=20,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "LDPlayer command failed"
            raise RuntimeError(message)
        return result.stdout.strip()

    def _run_bytes(self, *arguments: str) -> bytes:
        result = subprocess.run(
            [str(self.console_path), *arguments],
            check=False,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=20,
        )
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", errors="replace").strip()
            message = message or result.stdout.decode("utf-8", errors="replace").strip()
            raise RuntimeError(message or "LDPlayer command failed")
        return result.stdout

    def list_instances(self) -> list[EmulatorInstance]:
        output = self._run("list2")
        instances: list[EmulatorInstance] = []
        for line in output.splitlines():
            fields = [field.strip() for field in line.split(",")]
            if len(fields) < 6:
                continue
            try:
                index = int(fields[0])
                android_started = fields[4] == "1"
                pid = int(fields[5]) or None
            except ValueError:
                continue
            process_alive = self._is_process_alive(pid)
            if android_started and process_alive:
                state = InstanceState.RUNNING
            elif process_alive:
                state = InstanceState.STARTING
            elif android_started:
                state = InstanceState.UNKNOWN
            else:
                state = InstanceState.STOPPED
            instances.append(
                EmulatorInstance(
                    index=index,
                    name=fields[1] or f"LDPlayer-{index}",
                    state=state,
                    pid=pid if process_alive else None,
                )
            )
        return instances

    @classmethod
    def _is_process_alive(cls, pid: Optional[int]) -> bool:
        if not pid:
            return False

        kernel32 = WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (DWORD, BOOL, DWORD)
        open_process.restype = HANDLE
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = (HANDLE, POINTER(DWORD))
        get_exit_code.restype = BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (HANDLE,)
        close_handle.restype = BOOL

        handle = open_process(cls.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return get_last_error() == 5
        try:
            exit_code = DWORD()
            if not get_exit_code(handle, exit_code):
                return False
            return exit_code.value == cls.STILL_ACTIVE
        finally:
            close_handle(handle)

    def start(self, index: int) -> None:
        self._run("launch", "--index", str(index))

    def stop(self, index: int) -> None:
        self._run("quit", "--index", str(index))

    def restart(self, index: int) -> None:
        self._run("reboot", "--index", str(index))

    def set_http_proxy(self, index: int, host: str, port: int) -> str:
        expected = f"{host}:{port}"
        self._wait_for_adb(index)
        self._adb(index, f"shell settings put global http_proxy {expected}")
        self._adb(index, f"shell settings put global global_http_proxy_host {host}")
        self._adb(index, f"shell settings put global global_http_proxy_port {port}")
        applied = self.get_http_proxy(index)
        if applied != expected:
            raise RuntimeError(f"Android proxy was not applied. Expected {expected}, got {applied or 'empty'}")
        return applied

    def clear_http_proxy(self, index: int) -> None:
        try:
            self._wait_for_adb(index, timeout=10)
        except RuntimeError:
            return
        self._adb(index, "shell settings put global http_proxy :0")
        self._adb(index, "shell settings delete global global_http_proxy_host")
        self._adb(index, "shell settings delete global global_http_proxy_port")

    def get_http_proxy(self, index: int) -> str:
        return self._adb(index, "shell settings get global http_proxy").strip()

    def _adb(self, index: int, command: str) -> str:
        return self._run("adb", "--index", str(index), "--command", command)

    def screenshot_png(self, index: int) -> bytes:
        self._wait_for_adb(index, timeout=10)
        return self._run_bytes("adb", "--index", str(index), "--command", "exec-out screencap -p")

    def _wait_for_adb(self, index: int, timeout: int = 45) -> None:
        deadline = time.monotonic() + timeout
        last_error = "device not ready"
        while time.monotonic() < deadline:
            try:
                output = self._adb(index, "shell getprop sys.boot_completed").strip()
                if output == "1":
                    return
                last_error = output or "Android is still booting"
            except RuntimeError as exc:
                last_error = str(exc)
            time.sleep(2)
        raise RuntimeError(f"ADB is not ready for instance {index}: {last_error}")
