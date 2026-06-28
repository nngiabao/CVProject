from __future__ import annotations

from typing import Optional
import os
import re
import shutil
import socket
import subprocess
import time
from ctypes import POINTER, WinDLL, get_last_error
from ctypes.wintypes import BOOL, DWORD, HANDLE
from pathlib import Path
from winreg import HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, KEY_READ, KEY_WOW64_32KEY, KEY_WOW64_64KEY, OpenKey, QueryValueEx

from app.emulators.base import EmulatorProvider
from app.models import EmulatorInstance, InstanceState


class LdPlayerProvider(EmulatorProvider):
    ENCODED_INDEX_STEP = 10000
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    COMMON_PATHS = (
        Path(r"C:\LDPlayer\LDPlayer\dnconsole.exe"),
        Path(r"C:\LDPlayer\LDPlayer\ldconsole.exe"),
        Path(r"C:\LDPlayer\LDPlayer9\dnconsole.exe"),
        Path(r"C:\LDPlayer\LDPlayer9\ldconsole.exe"),
        Path(r"C:\LDPlayer\LDPlayer4.0\dnconsole.exe"),
        Path(r"C:\LDPlayer\LDPlayer4.0\ldconsole.exe"),
        Path(r"C:\Program Files\LDPlayer\LDPlayer\dnconsole.exe"),
        Path(r"C:\Program Files\LDPlayer\LDPlayer\ldconsole.exe"),
        Path(r"C:\Program Files\LDPlayer\LDPlayer9\dnconsole.exe"),
        Path(r"C:\Program Files\LDPlayer\LDPlayer9\ldconsole.exe"),
        Path(r"C:\Program Files (x86)\LDPlayer\LDPlayer\dnconsole.exe"),
        Path(r"C:\Program Files (x86)\LDPlayer\LDPlayer\ldconsole.exe"),
        Path(r"C:\Program Files (x86)\LDPlayer\LDPlayer9\dnconsole.exe"),
        Path(r"C:\Program Files (x86)\LDPlayer\LDPlayer9\ldconsole.exe"),
        Path(r"C:\LDPlayer\dnconsole.exe"),
        Path(r"C:\LDPlayer\ldconsole.exe"),
        Path(r"C:\LDPlayer4.0\LDPlayer\dnconsole.exe"),
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
    def detect(cls) -> Optional[EmulatorProvider]:
        configured = os.environ.get("LDPLAYER_CONSOLE")
        candidates: list[Path] = []
        if configured:
            configured_path = Path(configured)
            if configured_path.is_file():
                return cls(configured_path)

        for executable_name in ("dnconsole.exe", "ldconsole.exe"):
            executable = shutil.which(executable_name)
            if executable:
                candidates.append(Path(executable))

        candidates.extend(cls.COMMON_PATHS)
        candidates.extend(cls._registry_candidates())

        existing_by_folder: dict[Path, Path] = {}
        seen: set[str] = set()
        for candidate in candidates:
            normalized = os.path.normcase(os.path.abspath(candidate))
            if normalized in seen:
                continue
            seen.add(normalized)
            if candidate.is_file():
                existing_by_folder.setdefault(candidate.parent.resolve(), candidate)
        if not existing_by_folder:
            return None

        detected = [(cls._provider_sort_key(candidate), cls._instance_count(candidate), candidate) for candidate in existing_by_folder.values()]
        detected.sort(key=lambda item: item[0])
        providers = [cls(candidate) for _, count, candidate in detected if count > 0]
        if not providers:
            return cls(detected[0][2])
        if len(providers) == 1:
            return providers[0]
        return MultiLdPlayerProvider(providers)

    @classmethod
    def _instance_count(cls, console_path: Path) -> int:
        try:
            result = subprocess.run(
                [str(console_path), "list2"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=10,
            )
        except Exception:
            return 0
        if result.returncode != 0:
            return 0
        return sum(1 for line in result.stdout.splitlines() if cls._line_has_instance_index(line))

    @staticmethod
    def _provider_sort_key(console_path: Path) -> str:
        return os.path.normcase(str(console_path.parent.resolve()))

    @staticmethod
    def _line_has_instance_index(line: str) -> bool:
        first_field = line.split(",", 1)[0].strip()
        try:
            int(first_field)
        except ValueError:
            return False
        return True

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
        return self._parse_instances(output, 0)

    def _parse_instances(self, output: str, provider_slot: int) -> list[EmulatorInstance]:
        instances: list[EmulatorInstance] = []
        for line in output.splitlines():
            fields = [field.strip() for field in line.split(",")]
            if len(fields) < 2:
                continue
            try:
                local_index = int(fields[0])
            except ValueError:
                continue

            pid = self._running_pid_from_fields(fields)
            android_started = self._android_started_from_fields(fields, pid)
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
                    index=self._encode_index(provider_slot, local_index),
                    name=fields[1] or f"LDPlayer-{local_index}",
                    state=state,
                    pid=pid if process_alive else None,
                    platform=self._platform_label(provider_slot),
                    identity=self._instance_identity(local_index),
                )
            )
        return instances

    @classmethod
    def _encode_index(cls, provider_slot: int, local_index: int) -> int:
        if provider_slot <= 0:
            return local_index
        return provider_slot * cls.ENCODED_INDEX_STEP + local_index

    @classmethod
    def _decode_index(cls, encoded_index: int) -> tuple[int, int]:
        provider_slot = encoded_index // cls.ENCODED_INDEX_STEP
        local_index = encoded_index % cls.ENCODED_INDEX_STEP
        return provider_slot, local_index

    def _platform_label(self, provider_slot: int) -> str:
        if provider_slot <= 0:
            return "LDPlayer"
        return f"LDPlayer {self.console_path.parent.name}"

    def _instance_identity(self, local_index: int) -> str:
        return f"ldplayer:{os.path.normcase(str(self.console_path.parent))}:{local_index}"

    @classmethod
    def _running_pid_from_fields(cls, fields: list[str]) -> Optional[int]:
        possible_pids: list[int] = []
        for field in fields[4:]:
            try:
                value = int(field)
            except ValueError:
                continue
            if value > 0:
                possible_pids.append(value)

        for pid in reversed(possible_pids):
            if cls._is_process_alive(pid):
                return pid
        return None

    @staticmethod
    def _android_started_from_fields(fields: list[str], pid: Optional[int]) -> bool:
        if pid is not None:
            return True
        return any(field == "1" for field in fields[4:])

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
        self._wait_for_adb(index, timeout=20)
        errors: list[str] = []

        try:
            self._adb(index, f"reverse tcp:{port} tcp:{port}")
            return self._apply_http_proxy(index, "127.0.0.1", port)
        except RuntimeError as exc:
            errors.append(f"adb reverse: {exc}")

        candidates = [candidate for candidate in ("10.0.2.2", _host_lan_ip(), host) if candidate]
        candidates = [candidate for candidate in candidates if candidate != "127.0.0.1"]

        for candidate_host in dict.fromkeys(candidates):
            try:
                return self._apply_http_proxy(index, candidate_host, port)
            except RuntimeError as exc:
                errors.append(f"{candidate_host}: {exc}")

        raise RuntimeError("Could not apply Android proxy. " + " | ".join(errors))

    def _apply_http_proxy(self, index: int, host: str, port: int) -> str:
        expected = f"{host}:{port}"
        self._adb(index, f"shell settings put global http_proxy {expected}")
        self._adb(index, f"shell settings put global global_http_proxy_host {host}")
        self._adb(index, f"shell settings put global global_http_proxy_port {port}")
        applied = self.get_http_proxy(index)
        if applied != expected:
            raise RuntimeError(f"expected {expected}, got {applied or 'empty'}")
        return applied

    def clear_http_proxy(self, index: int) -> None:
        try:
            self._wait_for_adb(index, timeout=10)
        except RuntimeError as exc:
            raise RuntimeError(f"ADB is not ready while clearing Android proxy for instance {index}: {exc}")
        try:
            self._adb(index, "reverse --remove-all")
        except RuntimeError:
            pass

        errors: list[str] = []
        commands = (
            "shell settings put global http_proxy :0",
            "shell settings delete global http_proxy",
            "shell settings delete global global_http_proxy_host",
            "shell settings delete global global_http_proxy_port",
            "shell settings delete global global_http_proxy_exclusion_list",
        )
        for command in commands:
            try:
                self._adb(index, command)
            except RuntimeError as exc:
                errors.append(f"{command}: {exc}")
        if len(errors) == len(commands):
            raise RuntimeError("Could not clear Android proxy. " + " | ".join(errors))

    def get_http_proxy(self, index: int) -> str:
        self._wait_for_adb(index, timeout=30)
        return self._adb(index, "shell settings get global http_proxy").strip()

    def _adb(self, index: int, command: str) -> str:
        last_error: Optional[RuntimeError] = None
        for attempt in range(4):
            try:
                return self._run("adb", "--index", str(index), "--command", command)
            except RuntimeError as exc:
                last_error = exc
                if _is_transient_adb_error(str(exc)):
                    try:
                        return self._adb_direct_serial(index, command)
                    except RuntimeError:
                        pass
                self._repair_missing_adb_device(str(exc), index)
                if not _is_transient_adb_error(str(exc)) or attempt == 3:
                    break
                time.sleep(1)
        if last_error is not None:
            raise last_error
        raise RuntimeError("LDPlayer ADB command failed")

    def _adb_direct_serial(self, index: int, command: str) -> str:
        serial = self._localhost_adb_serial(index)
        self._run("adb", "--command", f"connect {serial}")
        return self._run("adb", "--command", f"-s {serial} {command}")

    @staticmethod
    def _localhost_adb_serial(index: int) -> str:
        return f"127.0.0.1:{5555 + (index * 2)}"

    def _repair_missing_adb_device(self, message: str, index: int) -> None:
        match = re.search(r"device 'emulator-(\d+)' not found", message)
        if match:
            port = int(match.group(1)) + 1
        else:
            port = 5555 + (index * 2)
        if port <= 0:
            return
        try:
            self._run("adb", "--command", f"connect 127.0.0.1:{port}")
        except RuntimeError:
            pass

    def screenshot_png(self, index: int) -> bytes:
        self._wait_for_adb(index, timeout=10)
        return self._run_bytes("adb", "--index", str(index), "--command", "exec-out screencap -p")

    def _wait_for_adb(self, index: int, timeout: int = 20) -> None:
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
            time.sleep(1)
        raise RuntimeError(f"ADB is not ready for instance {index}: {last_error}")


def _host_lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return str(sock.getsockname()[0])
    except OSError:
        return ""
    finally:
        sock.close()


def _is_transient_adb_error(message: str) -> bool:
    normalized = message.lower()
    transient_markers = (
        "device not found",
        "device offline",
        "no devices",
        "cannot connect",
        "failed to connect",
    )
    return any(marker in normalized for marker in transient_markers)


class MultiLdPlayerProvider(EmulatorProvider):
    def __init__(self, providers: list[LdPlayerProvider]) -> None:
        self.providers = providers

    @property
    def display_name(self) -> str:
        folders = ", ".join(provider.console_path.parent.name for provider in self.providers)
        return f"LDPlayer installs ({folders})"

    def list_instances(self) -> list[EmulatorInstance]:
        instances: list[EmulatorInstance] = []
        failures: list[str] = []
        for provider_slot, provider in enumerate(self.providers, start=1):
            try:
                output = provider._run("list2")
            except Exception as exc:
                failures.append(f"{provider.console_path.parent}: {exc}")
                continue
            instances.extend(provider._parse_instances(output, provider_slot))
        if not instances and failures:
            raise RuntimeError("\n".join(failures))
        return instances

    def start(self, index: int) -> None:
        provider, local_index = self._provider_for_index(index)
        provider.start(local_index)

    def stop(self, index: int) -> None:
        provider, local_index = self._provider_for_index(index)
        provider.stop(local_index)

    def restart(self, index: int) -> None:
        provider, local_index = self._provider_for_index(index)
        provider.restart(local_index)

    def set_http_proxy(self, index: int, host: str, port: int) -> str:
        provider, local_index = self._provider_for_index(index)
        return provider.set_http_proxy(local_index, host, port)

    def clear_http_proxy(self, index: int) -> None:
        provider, local_index = self._provider_for_index(index)
        provider.clear_http_proxy(local_index)

    def get_http_proxy(self, index: int) -> str:
        provider, local_index = self._provider_for_index(index)
        return provider.get_http_proxy(local_index)

    def screenshot_png(self, index: int) -> bytes:
        provider, local_index = self._provider_for_index(index)
        return provider.screenshot_png(local_index)

    def _provider_for_index(self, encoded_index: int) -> tuple[LdPlayerProvider, int]:
        provider_slot, local_index = LdPlayerProvider._decode_index(encoded_index)
        if provider_slot <= 0:
            if len(self.providers) != 1:
                raise RuntimeError(f"Instance {encoded_index} does not identify an LDPlayer install")
            return self.providers[0], local_index
        provider_position = provider_slot - 1
        if provider_position < 0 or provider_position >= len(self.providers):
            raise RuntimeError(f"Instance {encoded_index} uses an unknown LDPlayer install")
        return self.providers[provider_position], local_index
