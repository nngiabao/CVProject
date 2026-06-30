from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path


def is_administrator() -> bool:
    if sys.platform != "win32":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def request_administrator() -> bool:
    if is_administrator():
        return True
    if sys.platform != "win32":
        return False

    executable, arguments = _elevated_command()
    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        executable,
        subprocess.list2cmdline(arguments),
        str(Path.cwd()),
        1,
    )
    return result > 32


def show_elevation_error() -> None:
    if sys.platform != "win32":
        return
    ctypes.windll.user32.MessageBoxW(
        None,
        "Administrator access is required to control LDPlayer and configure "
        "WireGuard through ADB.",
        "GrowStone Bot",
        0x10,
    )


def _elevated_command() -> tuple[str, list[str]]:
    if getattr(sys, "frozen", False):
        return sys.executable, sys.argv[1:]

    entry_script = Path(sys.argv[0]).resolve()
    return sys.executable, [str(entry_script), *sys.argv[1:]]
