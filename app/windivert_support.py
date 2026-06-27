from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec


@dataclass(frozen=True, slots=True)
class WinDivertStatus:
    available: bool
    message: str


def check_windivert() -> WinDivertStatus:
    if find_spec("pydivert") is None:
        return WinDivertStatus(
            available=False,
            message="pydivert is not installed. Run: python -m pip install -r requirements.txt",
        )

    try:
        import pydivert  # type: ignore[import-not-found]
    except OSError as exc:
        return WinDivertStatus(False, f"WinDivert could not load: {exc}")
    except Exception as exc:
        return WinDivertStatus(False, f"pydivert import failed: {exc}")

    try:
        handle = pydivert.WinDivert("false")
        handle.open()
        handle.close()
    except OSError as exc:
        return WinDivertStatus(False, f"WinDivert driver is not available: {exc}")
    except Exception as exc:
        return WinDivertStatus(False, f"WinDivert check failed: {exc}")

    return WinDivertStatus(True, "WinDivert is available")
