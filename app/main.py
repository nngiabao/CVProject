from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.admin import request_administrator, show_elevation_error
from app.emulators.ldplayer import LdPlayerProvider
from app.emulators.mock import MockEmulatorProvider
from app.ui.main_window import MainWindow
from app.ui.styles import APP_STYLE


def main() -> int:
    if not request_administrator():
        show_elevation_error()
        return 1
    if sys.platform == "win32":
        from app.admin import is_administrator

        if not is_administrator():
            return 0

    application = QApplication(sys.argv)
    application.setApplicationName("GrowStone Bot")
    application.setStyle("Fusion")
    application.setStyleSheet(APP_STYLE)

    provider = LdPlayerProvider.detect() or MockEmulatorProvider()
    window = MainWindow(provider)
    window.show()
    return application.exec()
