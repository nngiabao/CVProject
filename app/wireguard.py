from __future__ import annotations

import re
import shutil
import subprocess
import os
from dataclasses import dataclass
from pathlib import Path

PACKAGE_NAME = "com.wireguard.android"


@dataclass(frozen=True)
class WireGuardSetupResult:
    installed: bool
    remote_config: str
    import_started: bool
    message: str


class WireGuardEmulatorManager:
    def __init__(self, app_root: Path) -> None:
        self.app_root = app_root.resolve()

    def ensure_installed_and_imported(self, instance_index: int, config_path: Path) -> WireGuardSetupResult:
        if not config_path.is_file():
            raise RuntimeError(f"WireGuard config does not exist: {config_path}")

        installed_now = False
        if not self.is_installed(instance_index):
            apk_path = self._wireguard_apk_path()
            self._adb(instance_index, "install", "-r", str(apk_path), timeout=90)
            installed_now = True

        remote_config = f"/sdcard/Download/{_safe_remote_name(config_path.name)}"
        self._adb_shell(instance_index, "mkdir", "-p", "/sdcard/Download")
        self._adb(instance_index, "push", str(config_path), remote_config, timeout=60)

        import_started = self._try_start_import(instance_index, remote_config)
        if not import_started:
            self.open_app(instance_index)

        message = "WireGuard is installed and the config was copied to Downloads."
        if import_started:
            message = "WireGuard is installed and Android opened the config import flow."
        if installed_now:
            message = "Installed WireGuard. " + message
        return WireGuardSetupResult(installed_now, remote_config, import_started, message)

    def is_installed(self, instance_index: int) -> bool:
        output = self._adb_shell(instance_index, "pm", "path", PACKAGE_NAME, check=False)
        return f"package:{PACKAGE_NAME}" in output or "base.apk" in output

    def open_app(self, instance_index: int) -> None:
        self._adb_shell(
            instance_index,
            "monkey",
            "-p",
            PACKAGE_NAME,
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        )

    def public_ip(self, instance_index: int) -> str:
        commands = (
            ("curl", "-4", "-s", "--max-time", "8", "https://api.ipify.org"),
            ("wget", "-qO-", "https://api.ipify.org"),
        )
        errors: list[str] = []
        for command in commands:
            output = self._adb_shell(instance_index, *command, check=False, timeout=15).strip()
            if _looks_like_ip(output):
                return output
            if output:
                errors.append(output)
        raise RuntimeError("; ".join(errors[:2]) or "Could not read public IP inside emulator")

    def _try_start_import(self, instance_index: int, remote_config: str) -> bool:
        output = self._adb_shell(
            instance_index,
            "am",
            "start",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            f"file://{remote_config}",
            "-t",
            "application/x-wireguard-profile",
            check=False,
        )
        failed = ("Error:" in output) or ("unable to resolve" in output.lower()) or ("not found" in output.lower())
        return not failed

    def _wireguard_apk_path(self) -> Path:
        candidates = (
            self.app_root / "work" / "com.wireguard.android-1.0.20260315.apk",
            self.app_root.parent / "work" / "com.wireguard.android-1.0.20260315.apk",
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise RuntimeError("WireGuard APK not found in the workspace work folder")

    def _adb_shell(
        self,
        instance_index: int,
        *arguments: str,
        check: bool = True,
        timeout: int = 30,
    ) -> str:
        return self._adb(instance_index, "shell", *arguments, check=check, timeout=timeout)

    def _adb(
        self,
        instance_index: int,
        *arguments: str,
        check: bool = True,
        timeout: int = 30,
    ) -> str:
        adb_path = self._adb_path()
        last_output = ""
        for serial in _candidate_serials(instance_index):
            if serial.startswith("127.0.0.1:"):
                subprocess.run(
                    [str(adb_path), "connect", serial],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=10,
                )
            result = subprocess.run(
                [str(adb_path), "-s", serial, *arguments],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=timeout,
            )
            output = (result.stdout or "").strip()
            error = (result.stderr or "").strip()
            last_output = "\n".join(part for part in (output, error) if part)
            if result.returncode == 0:
                return last_output
        if check:
            raise RuntimeError(last_output or "ADB command failed")
        return last_output

    def _adb_path(self) -> Path:
        candidates = list(_adb_candidates(self.app_root))
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        discovered = shutil.which("adb.exe") or shutil.which("adb")
        if discovered:
            return Path(discovered)
        checked = "\n".join(str(path) for path in candidates)
        raise RuntimeError(
            "adb.exe was not found. Install Android platform-tools, add adb.exe to PATH, "
            "or keep LDPlayer's adb.exe in its install folder.\n\nChecked:\n" + checked
        )


def _adb_candidates(app_root: Path) -> tuple[Path, ...]:
    env_roots = [
        value
        for value in (
            os.environ.get("ANDROID_HOME"),
            os.environ.get("ANDROID_SDK_ROOT"),
            os.environ.get("LOCALAPPDATA") and str(Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk"),
        )
        if value
    ]
    sdk_candidates = [Path(root) / "platform-tools" / "adb.exe" for root in env_roots]
    return (
        app_root.parent / "android-sdk" / "platform-tools" / "adb.exe",
        app_root / "android-sdk" / "platform-tools" / "adb.exe",
        *sdk_candidates,
        Path(r"C:\LDPlayer\LDPlayer9\adb.exe"),
        Path(r"C:\LDPlayer\LDPlayer4\adb.exe"),
        Path(r"C:\Program Files\LDPlayer\LDPlayer9\adb.exe"),
        Path(r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe"),
    )


def _candidate_serials(instance_index: int) -> tuple[str, str]:
    return (
        f"emulator-{5554 + (instance_index * 2)}",
        f"127.0.0.1:{5555 + (instance_index * 2)}",
    )


def _safe_remote_name(name: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
    return safe_name if safe_name.endswith(".conf") else f"{safe_name}.conf"


def _looks_like_ip(value: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", value))
