"""Entry point for Anti-Drift Studio (PySide6)."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from antidrift.main_window import MainWindow


def main() -> int:
    """Launch the Anti-Drift Studio desktop application."""
    app = QApplication(sys.argv)
    app.setApplicationName("Anti-Drift Studio")
    app.setOrganizationName("antidrift-studio")
    app.setDesktopFileName("xbox-antidrift-gui")
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(start_reader=True, enable_tray=True)
    if "--minimized" not in sys.argv:
        window.show()
    elif window._tray is None:
        window.show()

    def _on_last_closed() -> None:
        if not window._profile_store.minimize_to_tray or window._force_quit or window._tray is None:
            app.quit()

    app.lastWindowClosed.connect(_on_last_closed)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
