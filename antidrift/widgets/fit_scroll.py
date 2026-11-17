"""Reusable b7software visual primitives, width-locked QScrollArea, and QVideoSink canvas."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtMultimedia import QVideoFrame
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QFrame, QScrollArea, QSizePolicy, QWidget

# Official b7. vector mark (based on b7software/b7/logos/b7.svg)
B7_MARK_SVG = b"""<svg version="1.2" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800">
  <path fill="#F8FAFC" d="m220.8 620.3q-34.8 0-59.1-14-24.4-14.6-38.5-40.9l-1.8 47.6h-115.3v-433h118.4v151.3q14-25 38.4-39.7 24.4-15.2 57.9-15.2 40.9 0 70.7 21.3 30.5 21.4 47 60.4 16.5 38.4 16.5 90.2 0 51.9-16.5 90.9-16.5 38.4-47 59.8-29.8 21.3-70.7 21.3zm-40.2-84.7q23.1 0 37.8-22.6 15.2-22.6 15.2-64.7 0-42-14.6-64.6-14-22.6-38.4-22.6-17.7 0-30.5 10.4-12.8 9.8-19.5 29.3-6.1 19.5-6.1 47.5 0 27.5 6.1 47 6.7 19.5 19.5 29.9 12.8 10.4 30.5 10.4zm366.7 77.4h-120.8q0-67.1 15.9-126.8 16.4-60.4 45.1-112.9 28.7-53 65.9-97.5h-209.2v-95.8h328.1v89.7q-39.7 43.9-67.7 93.3-28.1 48.8-42.7 109.7-14.6 61-14.6 140.3z"/>
  <path fill="#059669" d="m794.17 492.34v121.96h-121.96v-121.96z"/>
</svg>"""


class GridRootWidget(QWidget):
    """Architectural carbon backdrop with subtle 36px engineering grid (b7software signature)."""

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#090B0E"))
        step = 36
        p.setPen(QPen(QColor(255, 255, 255, 8), 1.0))
        w = self.width()
        h = self.height()
        for x in range(0, w, step):
            p.drawLine(x, 0, x, h)
        for y in range(0, h, step):
            p.drawLine(0, y, w, y)


class B7MarkWidget(QWidget):
    """Crisp DPI-independent b7software geometric logo badge."""

    def __init__(self, size: int = 38, parent=None) -> None:
        super().__init__(parent)
        self._renderer = QSvgRenderer(QByteArray(B7_MARK_SVG), self)
        self.setFixedSize(size, size)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        p.setPen(QPen(QColor("#1E2630"), 1.0))
        p.setBrush(QColor("#0D1116"))
        p.drawRoundedRect(r, 7.0, 7.0)
        inner = r.adjusted(5.0, 5.0, -5.0, -5.0)
        self._renderer.render(p, inner)


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

        p.fillRect(rect, QColor("#07090C"))

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
            p.setPen(QColor("#64748B"))
            p.setFont(QFont("JetBrains Mono", 9, QFont.Medium))
            p.drawText(rect, Qt.AlignCenter, "VISTA PREVIA // CLIP")

        p.setClipping(False)
        p.setPen(QPen(QColor("#1E2630"), 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, 6.0, 6.0)
