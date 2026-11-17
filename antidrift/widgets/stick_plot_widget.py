"""Clean 2D calibration and deadzone shape plot (axial / radial / hybrid + outer reach)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..config import AXIS_MAX, AXIS_MIN, deadzone_raw_bounds


class StickPlotWidget(QWidget):
    """Square 2D graph showing deadzone geometry, outer reach, raw & clean output."""

    center_changed = Signal(int, int)

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self._title = title
        self._center_x = 0
        self._center_y = 0
        self._dz = 0.12
        self._dz_mode = "axial"
        self._outer_comp = 0.0
        self._raw_x = 0
        self._raw_y = 0
        self._out_x = 0
        self._out_y = 0
        self._connected = False
        self._allow_drag = True
        self._dragging = False
        self.setMinimumSize(128, 128)

    def set_params(
        self,
        center_x: int,
        center_y: int,
        dz: float,
        dz_mode: str = "axial",
        outer_comp: float = 0.0,
    ) -> None:
        """Update configured recentering point, deadzone shape, and outer compensation."""
        self._center_x = center_x
        self._center_y = center_y
        self._dz = dz
        self._dz_mode = dz_mode
        self._outer_comp = outer_comp
        self.update()

    def set_live_values(
        self, raw_x: int, raw_y: int, out_x: int, out_y: int, connected: bool
    ) -> None:
        """Update live raw and corrected stick coordinates."""
        self._raw_x = raw_x
        self._raw_y = raw_y
        self._out_x = out_x
        self._out_y = out_y
        self._connected = connected
        self.update()

    def _plot_rect(self) -> QRectF:
        pad = 8.0
        avail_w = max(40.0, float(self.width()) - 2.0 * pad)
        avail_h = max(40.0, float(self.height()) - 2.0 * pad)
        side = min(avail_w, avail_h)
        left = (float(self.width()) - side) * 0.5
        top = (float(self.height()) - side) * 0.5
        return QRectF(left, top, side, side)

    def _axis_to_px(self, x: float, y: float, rect: QRectF) -> QPointF:
        fx = (x - float(AXIS_MIN)) / float(AXIS_MAX - AXIS_MIN)
        fy = (y - float(AXIS_MIN)) / float(AXIS_MAX - AXIS_MIN)
        return QPointF(rect.left() + fx * rect.width(), rect.top() + fy * rect.height())

    def _px_to_axis(self, pos: QPointF, rect: QRectF) -> tuple[int, int]:
        fx = max(0.0, min(1.0, (pos.x() - rect.left()) / max(1.0, rect.width())))
        fy = max(0.0, min(1.0, (pos.y() - rect.top()) / max(1.0, rect.height())))
        ax = round(AXIS_MIN + fx * (AXIS_MAX - AXIS_MIN))
        ay = round(AXIS_MIN + fy * (AXIS_MAX - AXIS_MIN))
        return max(-28000, min(28000, ax)), max(-28000, min(28000, ay))

    def mousePressEvent(self, event) -> None:
        if self._allow_drag and event.button() == Qt.LeftButton:
            self._dragging = True
            rect = self._plot_rect()
            cx, cy = self._px_to_axis(event.position(), rect)
            self.center_changed.emit(cx, cy)

    def mouseMoveEvent(self, event) -> None:
        if self._allow_drag and self._dragging:
            rect = self._plot_rect()
            cx, cy = self._px_to_axis(event.position(), rect)
            self.center_changed.emit(cx, cy)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._dragging = False

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = self._plot_rect()

        p.setPen(QPen(QColor("#32353E"), 1.3))
        p.setBrush(QColor("#121316"))
        p.drawRoundedRect(rect, 9, 9)

        zero = self._axis_to_px(0, 0, rect)
        p.setPen(QPen(QColor(154, 158, 168, 45), 1.0, Qt.DotLine))
        p.drawLine(QPointF(rect.left() + 4, zero.y()), QPointF(rect.right() - 4, zero.y()))
        p.drawLine(QPointF(zero.x(), rect.top() + 4), QPointF(zero.x(), rect.bottom() - 4))

        # Circular boundary (shrinks visually when outer_comp > 0)
        outer_r = rect.width() * 0.46
        p.setPen(QPen(QColor(107, 111, 121, 70), 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(rect.center(), outer_r, outer_r)

        cpx = self._axis_to_px(self._center_x, self._center_y, rect)
        if self._outer_comp > 0.005:
            reach_r = outer_r * (1.0 - self._outer_comp)
            p.setPen(QPen(QColor(86, 128, 173, 150), 1.0, Qt.DashLine))
            p.drawEllipse(cpx, reach_r, reach_r)

        min_x, max_x = deadzone_raw_bounds(self._center_x, self._dz)
        min_y, max_y = deadzone_raw_bounds(self._center_y, self._dz)
        tl = self._axis_to_px(min_x, min_y, rect)
        br = self._axis_to_px(max_x, max_y, rect)

        if self._dz_mode in ("radial", "hybrid"):
            rx = max(3.0, (br.x() - tl.x()) * 0.5)
            ry = max(3.0, (br.y() - tl.y()) * 0.5)
            if self._dz_mode == "hybrid":
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(197, 155, 87, 16))
                band_rx = rx * 0.40
                band_ry = ry * 0.40
                p.drawRect(QRectF(cpx.x() - band_rx, rect.top() + 1, band_rx * 2.0, rect.height() - 2))
                p.drawRect(QRectF(rect.left() + 1, cpx.y() - band_ry, rect.width() - 2, band_ry * 2.0))
            p.setPen(QPen(QColor(197, 155, 87, 185), 1.1, Qt.DashLine))
            p.setBrush(QColor(197, 155, 87, 36))
            p.drawEllipse(cpx, rx, ry)
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(197, 155, 87, 18))
            p.drawRect(QRectF(tl.x(), rect.top() + 1, br.x() - tl.x(), rect.height() - 2))
            p.drawRect(QRectF(rect.left() + 1, tl.y(), rect.width() - 2, br.y() - tl.y()))
            p.setPen(QPen(QColor(197, 155, 87, 175), 1.1, Qt.DashLine))
            p.setBrush(QColor(197, 155, 87, 36))
            p.drawRect(QRectF(tl, br))

        p.setPen(QPen(QColor("#C59B57"), 1.4))
        p.drawLine(QPointF(cpx.x() - 5, cpx.y()), QPointF(cpx.x() + 5, cpx.y()))
        p.drawLine(QPointF(cpx.x(), cpx.y() - 5), QPointF(cpx.x(), cpx.y() + 5))

        opx = self._axis_to_px(self._out_x, self._out_y, rect)
        p.setPen(QPen(QColor("#548C6E"), 1.6))
        p.setBrush(QColor(84, 140, 110, 110))
        p.drawEllipse(opx, 6.0, 6.0)

        if self._connected:
            rpx = self._axis_to_px(self._raw_x, self._raw_y, rect)
            p.setPen(QPen(QColor("#E3918B"), 1.2))
            p.setBrush(QColor("#C86A63"))
            p.drawEllipse(rpx, 4.0, 4.0)

        p.setPen(QColor("#9A9EA8"))
        p.setFont(QFont("Sans Serif", 8, QFont.Bold))
        p.drawText(
            QRectF(rect.left() + 7, rect.top() + 5, rect.width() - 14, 16),
            Qt.AlignLeft | Qt.AlignTop,
            self._title,
        )
