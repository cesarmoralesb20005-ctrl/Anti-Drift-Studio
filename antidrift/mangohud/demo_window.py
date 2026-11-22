"""Lightweight OpenGL demo window to preview MangoHud live without vkcube/glxgears."""

from __future__ import annotations

import math
import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QApplication


class _DemoGLWidget(QOpenGLWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Anti-Drift Studio · Ventana Demo MangoHud")
        self.resize(640, 380)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self._timer.start(16)

    def _step(self) -> None:
        self._phase = (self._phase + 0.05) % (2.0 * math.pi)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0B0E14"))
        p.setPen(QColor("#34D399"))
        p.drawText(
            self.rect().adjusted(20, 20, -20, -20),
            Qt.AlignBottom | Qt.AlignHCenter,
            "Ventana OpenGL de prueba · Cierra cuando termines de ajustar MangoHud",
        )
        cx, cy = self.width() * 0.5, self.height() * 0.5
        r = 68.0
        p.setBrush(QColor("#10B981"))
        p.drawEllipse(
            int(cx + math.cos(self._phase) * r - 18),
            int(cy + math.sin(self._phase) * r - 18),
            36,
            36,
        )


def main() -> int:
    app = QApplication(sys.argv)
    w = _DemoGLWidget()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
