"""Circularity & outer-wear diagnostic dialog for analog sticks."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import normalize_axis
from ..evdev_reader import ControllerState


class _CircularityCanvas(QWidget):
    """Polar sweep visualizer recording 36 angular sectors around outer travel."""

    SECTORS = 36

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self._title = title
        self._sectors: list[float] = [0.0] * self.SECTORS
        self._cur_x = 0.0
        self._cur_y = 0.0
        self.setMinimumSize(230, 230)

    def reset(self) -> None:
        self._sectors = [0.0] * self.SECTORS
        self.update()

    def push_normalized(self, nx: float, ny: float) -> None:
        self._cur_x = nx
        self._cur_y = ny
        r = math.hypot(nx, ny)
        if r >= 0.45:
            ang = math.atan2(ny, nx)
            if ang < 0:
                ang += 2.0 * math.pi
            idx = int((ang / (2.0 * math.pi)) * self.SECTORS) % self.SECTORS
            if r > self._sectors[idx]:
                self._sectors[idx] = min(1.35, r)
        self.update()

    def metrics(self) -> tuple[int, float, float, float]:
        """Return (covered_sectors, avg_error_pct, min_reach_pct, suggested_outer_comp)."""
        filled = [r for r in self._sectors if r > 0.0]
        if len(filled) < 8:
            return len(filled), 0.0, 0.0, 0.0
        avg_err = sum(abs(r - 1.0) for r in filled) / len(filled) * 100.0
        min_reach = min(filled)
        # Suggest outer compensation only if real outer reach falls under 97%
        suggested = max(0.0, min(0.25, round(1.0 - min_reach + 0.01, 2))) if min_reach < 0.97 else 0.0
        return len(filled), avg_err, min_reach * 100.0, suggested

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        side = min(float(self.width()), float(self.height())) - 16.0
        rect = QRectF(
            (float(self.width()) - side) * 0.5,
            (float(self.height()) - side) * 0.5,
            side,
            side,
        )
        c = rect.center()
        rad = side * 0.42

        p.setPen(QPen(QColor("#32353E"), 1.3))
        p.setBrush(QColor("#121316"))
        p.drawRoundedRect(rect, 10, 10)

        p.setPen(QPen(QColor(154, 158, 168, 55), 1.0, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, rad, rad)
        p.drawLine(QPointF(c.x() - rad, c.y()), QPointF(c.x() + rad, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - rad), QPointF(c.x(), c.y() + rad))

        for idx, val in enumerate(self._sectors):
            if val <= 0.0:
                continue
            ang = (idx + 0.5) * (2.0 * math.pi / self.SECTORS)
            pt = QPointF(c.x() + math.cos(ang) * val * rad, c.y() + math.sin(ang) * val * rad)
            good = 0.90 <= val <= 1.12
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#548C6E") if good else QColor("#C59B57"))
            p.drawEllipse(pt, 4.0, 4.0)

        cur_pt = QPointF(c.x() + self._cur_x * rad, c.y() + self._cur_y * rad)
        p.setPen(QPen(QColor("#EFEFE9"), 1.4))
        p.setBrush(QColor("#5680AD"))
        p.drawEllipse(cur_pt, 5.0, 5.0)

        p.setPen(QColor("#9A9EA8"))
        p.setFont(QFont("Sans Serif", 9, QFont.Bold))
        p.drawText(
            QRectF(rect.left() + 8, rect.top() + 6, rect.width() - 16, 18),
            Qt.AlignLeft | Qt.AlignTop,
            self._title,
        )


class CircularityTestDialog(QDialog):
    """Interactive circularity & wear diagnostic for Left and Right sticks."""

    apply_outer_comp_requested = Signal(float, float)

    def __init__(
        self, center_lx: int, center_ly: int, center_rx: int, center_ry: int, parent=None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Prueba de circularidad y desgaste")
        self.resize(620, 430)
        self._centers = (center_lx, center_ly, center_rx, center_ry)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        intro = QLabel(
            "Gira ambas palancas lentamente por todo el borde exterior durante 3–4 segundos.\n"
            "Si tu mando gastado no llega al 100% en los bordes, aquí puedes compensar el alcance (Anti-Slowturn)."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #9A9EA8; font-size: 12.5px;")
        lay.addWidget(intro)

        plots_row = QHBoxLayout()
        plots_row.setSpacing(12)

        l_card = QFrame()
        l_card.setObjectName("Card")
        l_lay = QVBoxLayout(l_card)
        self._l_canvas = _CircularityCanvas("Palanca Izquierda (L)")
        self._l_stats = QLabel("Gira la palanca por el borde...")
        self._l_stats.setAlignment(Qt.AlignCenter)
        self._l_stats.setStyleSheet("color: #D8D8D2; font-size: 12px;")
        l_lay.addWidget(self._l_canvas)
        l_lay.addWidget(self._l_stats)

        r_card = QFrame()
        r_card.setObjectName("Card")
        r_lay = QVBoxLayout(r_card)
        self._r_canvas = _CircularityCanvas("Palanca Derecha (R)")
        self._r_stats = QLabel("Gira la palanca por el borde...")
        self._r_stats.setAlignment(Qt.AlignCenter)
        self._r_stats.setStyleSheet("color: #D8D8D2; font-size: 12px;")
        r_lay.addWidget(self._r_canvas)
        r_lay.addWidget(self._r_stats)

        plots_row.addWidget(l_card)
        plots_row.addWidget(r_card)
        lay.addLayout(plots_row, 1)

        btn_row = QHBoxLayout()
        btn_reset = QPushButton("Reiniciar trazo")
        btn_reset.clicked.connect(self._on_reset)
        btn_row.addWidget(btn_reset)
        btn_row.addStretch(1)

        self._btn_apply = QPushButton("Aplicar compensación sugerida")
        self._btn_apply.setObjectName("PrimaryBtn")
        self._btn_apply.clicked.connect(self._on_apply)
        btn_row.addWidget(self._btn_apply)

        btn_close = QPushButton("Cerrar")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        lay.addLayout(btn_row)

    def feed_state(self, state: ControllerState) -> None:
        if state.mode == "disconnected":
            return
        cx_l, cy_l, cx_r, cy_r = self._centers
        self._l_canvas.push_normalized(
            normalize_axis(state.raw_lx, cx_l),
            normalize_axis(state.raw_ly, cy_l),
        )
        self._r_canvas.push_normalized(
            normalize_axis(state.raw_rx, cx_r),
            normalize_axis(state.raw_ry, cy_r),
        )
        self._refresh_stats()

    def _refresh_stats(self) -> None:
        for canvas, lbl in (
            (self._l_canvas, self._l_stats),
            (self._r_canvas, self._r_stats),
        ):
            cov, err, min_r, sug = canvas.metrics()
            if cov < 8:
                lbl.setText(f"Sectores: {cov}/36 (sigue girando el borde)")
            else:
                lbl.setText(
                    f"Error medio: {err:.1f}% · Borde mín: {min_r:.0f}%\n"
                    f"Compensación sugerida: {round(sug * 100)}%"
                )

    def _on_reset(self) -> None:
        self._l_canvas.reset()
        self._r_canvas.reset()
        self._l_stats.setText("Gira la palanca por el borde...")
        self._r_stats.setText("Gira la palanca por el borde...")

    def _on_apply(self) -> None:
        _, _, _, sug_l = self._l_canvas.metrics()
        _, _, _, sug_r = self._r_canvas.metrics()
        self.apply_outer_comp_requested.emit(sug_l, sug_r)
        self.accept()
