"""Reusable width-locked QScrollArea and Wayland-safe QVideoSink canvas."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtMultimedia import QVideoFrame
from PySide6.QtWidgets import QFrame, QScrollArea, QSizePolicy, QWidget


class FitWidthScrollArea(QScrollArea):
    """QScrollArea that strictly constrains inner widget width to viewport width."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)

    def setWidget(self, widget: QWidget) -> None:  # type: ignore[override]
        super().setWidget(widget)
        widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        widget.setMinimumWidth(0)
        self._sync_inner_width()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_inner_width()

    def _sync_inner_width(self) -> None:
        w = self.widget()
        if w is not None:
            vw = max(120, self.viewport().width())
            w.setMaximumWidth(vw)


class FrameVideoCanvas(QWidget):
    """Wayland-scroll-safe video viewport painting QVideoSink frames via QPainter."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self.setMinimumSize(180, 130)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_frame(self, frame: QVideoFrame) -> None:
        if frame.isValid():
            img = frame.toImage()
            if not img.isNull():
                self._image = img
                self.update()

    def clear(self) -> None:
        self._image = None
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(240, 150)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        rect = QRectF(0.5, 0.5, max(1.0, self.width() - 1.0), max(1.0, self.height() - 1.0))
        clip = QPainterPath()
        clip.addRoundedRect(rect, 6.0, 6.0)
        p.setClipPath(clip)

        p.fillRect(rect, QColor("#101114"))

        if self._image is not None and not self._image.isNull():
            scaled = self._image.scaled(
                self.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) * 0.5
            y = (self.height() - scaled.height()) * 0.5
            p.drawImage(int(x), int(y), scaled)
        else:
            p.setPen(QColor("#6B6F79"))
            p.drawText(rect, Qt.AlignCenter, "Vista previa del clip")

        p.setClipping(False)
        p.setPen(QPen(QColor("#2D3038"), 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, 6.0, 6.0)
