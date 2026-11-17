"""Tactile matte controller visualizer widget using QPainter (non-neon editorial style)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QWidget

from ..config import AXIS_MAX, AntiDriftConfig, TRIGGER_MAX, compute_corrected_stick
from ..evdev_reader import ControllerState
from ..macros import short_extra_badge


class ControllerWidget(QWidget):
    """Matte vector controller displaying real-time inputs cleanly without neon glare."""

    simulated_state_changed = Signal(object)

    LOGICAL_W = 620.0
    LOGICAL_H = 446.0

    L_STICK_CENTER = QPointF(172.0, 198.0)
    R_STICK_CENTER = QPointF(386.0, 272.0)
    DPAD_CENTER = QPointF(234.0, 272.0)
    GUIDE_CENTER = QPointF(310.0, 146.0)
    VIEW_CENTER = QPointF(264.0, 194.0)
    MENU_CENTER = QPointF(356.0, 194.0)

    # Desaturated architectural hues (zero fluorescent neon)
    ABXY_CENTERS: dict[str, tuple[QPointF, QColor]] = {
        "Y": (QPointF(448.0, 160.0), QColor("#C59B57")),
        "B": (QPointF(484.0, 194.0), QColor("#B8625C")),
        "A": (QPointF(448.0, 228.0), QColor("#548C6E")),
        "X": (QPointF(412.0, 194.0), QColor("#5680AD")),
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(430, 305)
        self._state = ControllerState()
        self._config = AntiDriftConfig()
        self._simulator_enabled = False
        self._macro_preview_active = False
        self._drag_target: str | None = None

    def set_controller_state(self, state: ControllerState) -> None:
        """Update live hardware state and repaint."""
        if not self._simulator_enabled and not self._macro_preview_active:
            self._state = state
            self.update()

    def set_macro_preview_state(self, state: ControllerState | None) -> None:
        """Temporarily display animated macro preview state on the controller."""
        if state is None:
            self._macro_preview_active = False
        else:
            self._macro_preview_active = True
            self._state = state
        self.update()

    def set_config_preview(self, config: AntiDriftConfig) -> None:
        """Update config used for previewing deadzone overlays on the sticks."""
        self._config = config.copy()
        self.update()

    def set_simulator_enabled(self, enabled: bool) -> None:
        """Toggle interactive click/drag simulation mode."""
        self._simulator_enabled = enabled
        if enabled:
            self._state = ControllerState(
                mode="simulated",
                extra_buttons={704: False, 705: False, 706: False, 707: False},
                supported_extra_keys=[704, 705, 706, 707],
            )
        self.update()

    def _transform_params(self) -> tuple[float, float, float]:
        w = max(1.0, float(self.width()))
        h = max(1.0, float(self.height()))
        scale = min(w / self.LOGICAL_W, h / self.LOGICAL_H)
        ox = (w - self.LOGICAL_W * scale) * 0.5
        oy = (h - self.LOGICAL_H * scale) * 0.5
        return scale, ox, oy

    def _widget_to_logical(self, pos: QPointF) -> QPointF:
        scale, ox, oy = self._transform_params()
        return QPointF((pos.x() - ox) / scale, (pos.y() - oy) / scale)

    def _emit_sim(self) -> None:
        if not self._simulator_enabled:
            return
        ox_l, oy_l = compute_corrected_stick(
            self._state.raw_lx,
            self._state.raw_ly,
            self._config.center_lx,
            self._config.center_ly,
            self._config.dz_l,
            self._config.response_curve,
            self._config.dz_mode,
            self._config.outer_comp_l,
        )
        ox_r, oy_r = compute_corrected_stick(
            self._state.raw_rx,
            self._state.raw_ry,
            self._config.center_rx,
            self._config.center_ry,
            self._config.dz_r,
            self._config.response_curve,
            self._config.dz_mode,
            self._config.outer_comp_r,
        )
        self._state.out_lx, self._state.out_ly = ox_l, oy_l
        self._state.out_rx, self._state.out_ry = ox_r, oy_r
        self.update()
        self.simulated_state_changed.emit(self._state)

    def _extra_badge_rects(self) -> list[tuple[int, QRectF]]:
        codes = sorted(self._state.extra_buttons.keys())[:8]
        if not codes:
            return []
        badge_w = 50.0
        badge_h = 24.0
        gap = 8.0
        total_w = len(codes) * badge_w + (len(codes) - 1) * gap
        start_x = (self.LOGICAL_W - total_w) * 0.5
        y = 414.0
        return [
            (code, QRectF(start_x + idx * (badge_w + gap), y, badge_w, badge_h))
            for idx, code in enumerate(codes)
        ]

    def mousePressEvent(self, event) -> None:
        if not self._simulator_enabled:
            super().mousePressEvent(event)
            return
        p = self._widget_to_logical(event.position())
        self._drag_target = None

        for tag, center in (("L", self.L_STICK_CENTER), ("R", self.R_STICK_CENTER)):
            if math.hypot(p.x() - center.x(), p.y() - center.y()) <= 44.0:
                if event.button() == Qt.RightButton:
                    key = "LS" if tag == "L" else "RS"
                    self._state.buttons[key] = not self._state.buttons[key]
                    self._emit_sim()
                    return
                self._drag_target = tag
                self._update_sim_stick(tag, p)
                return

        hit_targets = [
            ("GUIDE", self.GUIDE_CENTER, 20.0),
            ("VIEW", self.VIEW_CENTER, 14.0),
            ("MENU", self.MENU_CENTER, 14.0),
        ]
        for btn_name, (center, _) in self.ABXY_CENTERS.items():
            hit_targets.append((btn_name, center, 19.0))

        for name, center, rad in hit_targets:
            if math.hypot(p.x() - center.x(), p.y() - center.y()) <= rad:
                self._state.buttons[name] = not self._state.buttons[name]
                self._emit_sim()
                return

        if QRectF(112, 82, 118, 30).contains(p):
            self._state.buttons["LB"] = not self._state.buttons["LB"]
            self._emit_sim()
            return
        if QRectF(390, 82, 118, 30).contains(p):
            self._state.buttons["RB"] = not self._state.buttons["RB"]
            self._emit_sim()
            return

        if QRectF(126, 20, 92, 50).contains(p):
            self._state.lt = TRIGGER_MAX if self._state.lt < 500 else 0
            self._emit_sim()
            return
        if QRectF(402, 20, 92, 50).contains(p):
            self._state.rt = TRIGGER_MAX if self._state.rt < 500 else 0
            self._emit_sim()
            return

        for code, rect in self._extra_badge_rects():
            if rect.contains(p):
                self._state.extra_buttons[code] = not self._state.extra_buttons.get(
                    code, False
                )
                self._emit_sim()
                return

        dx = p.x() - self.DPAD_CENTER.x()
        dy = p.y() - self.DPAD_CENTER.y()
        if math.hypot(dx, dy) <= 42.0:
            self._state.hat_x = -1 if dx < -13 else (1 if dx > 13 else 0)
            self._state.hat_y = -1 if dy < -13 else (1 if dy > 13 else 0)
            self._emit_sim()

    def mouseMoveEvent(self, event) -> None:
        if self._simulator_enabled and self._drag_target in ("L", "R"):
            p = self._widget_to_logical(event.position())
            self._update_sim_stick(self._drag_target, p)

    def mouseReleaseEvent(self, event) -> None:
        if self._simulator_enabled and self._drag_target in ("L", "R"):
            if self._drag_target == "L":
                self._state.raw_lx = self._config.center_lx
                self._state.raw_ly = self._config.center_ly
            else:
                self._state.raw_rx = self._config.center_rx
                self._state.raw_ry = self._config.center_ry
            self._drag_target = None
            self._emit_sim()

    def _update_sim_stick(self, tag: str, p: QPointF) -> None:
        center = self.L_STICK_CENTER if tag == "L" else self.R_STICK_CENTER
        nx = max(-1.0, min(1.0, (p.x() - center.x()) / 36.0))
        ny = max(-1.0, min(1.0, (p.y() - center.y()) / 36.0))
        rx = round(nx * AXIS_MAX)
        ry = round(ny * AXIS_MAX)
        if tag == "L":
            self._state.raw_lx, self._state.raw_ly = rx, ry
        else:
            self._state.raw_rx, self._state.raw_ry = rx, ry
        self._emit_sim()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        scale, ox, oy = self._transform_params()
        painter.translate(ox, oy)
        painter.scale(scale, scale)

        self._draw_triggers_and_bumpers(painter)
        self._draw_shell(painter)
        self._draw_guide_and_center_buttons(painter)
        self._draw_dpad(painter)
        self._draw_abxy(painter)
        self._draw_stick(
            painter,
            self.L_STICK_CENTER,
            "L",
            self._state.raw_lx,
            self._state.raw_ly,
            self._state.out_lx,
            self._state.out_ly,
            self._config.dz_l,
            self._state.buttons.get("LS", False),
        )
        self._draw_stick(
            painter,
            self.R_STICK_CENTER,
            "R",
            self._state.raw_rx,
            self._state.raw_ry,
            self._state.out_rx,
            self._state.out_ry,
            self._config.dz_r,
            self._state.buttons.get("RS", False),
        )
        self._draw_extra_buttons(painter)

    def _draw_triggers_and_bumpers(self, p: QPainter) -> None:
        for label, val, rect in (
            ("LT", self._state.lt, QRectF(132, 24, 86, 42)),
            ("RT", self._state.rt, QRectF(402, 24, 86, 42)),
        ):
            frac = max(0.0, min(1.0, float(val) / float(TRIGGER_MAX)))
            p.setPen(QPen(QColor("#353841"), 1.4))
            p.setBrush(QColor("#15161A"))
            p.drawRoundedRect(rect, 8, 8)

            if frac > 0.01:
                fill_h = rect.height() * frac
                fill_rect = QRectF(
                    rect.left() + 2.0,
                    rect.bottom() - fill_h + 2.0,
                    rect.width() - 4.0,
                    max(4.0, fill_h - 4.0),
                )
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#5680AD"))
                p.drawRoundedRect(fill_rect, 6, 6)

            p.setPen(QColor("#F4F4F0") if frac > 0.25 else QColor("#8F949E"))
            p.setFont(QFont("Sans Serif", 9, QFont.Bold))
            p.drawText(rect, Qt.AlignCenter, f"{label} {round(frac * 100)}%")

        for label, pressed, rect in (
            ("LB", self._state.buttons.get("LB", False), QRectF(114, 84, 118, 28)),
            ("RB", self._state.buttons.get("RB", False), QRectF(388, 84, 118, 28)),
        ):
            if pressed:
                p.setPen(QPen(QColor("#DFDFD8"), 1.6))
                p.setBrush(QColor("#EFEFE9"))
                p.drawRoundedRect(rect, 11, 11)
                p.setPen(QColor("#16171A"))
            else:
                p.setPen(QPen(QColor("#363942"), 1.3))
                p.setBrush(QColor("#202228"))
                p.drawRoundedRect(rect, 11, 11)
                p.setPen(QColor("#8F949E"))
            p.setFont(QFont("Sans Serif", 9, QFont.Bold))
            p.drawText(rect, Qt.AlignCenter, label)

    def _draw_shell(self, p: QPainter) -> None:
        body = QPainterPath()
        body.moveTo(155, 114)
        body.cubicTo(220, 102, 400, 102, 465, 114)
        body.cubicTo(516, 126, 542, 178, 554, 248)
        body.cubicTo(568, 330, 548, 392, 502, 398)
        body.cubicTo(462, 402, 434, 356, 398, 326)
        body.cubicTo(362, 314, 258, 314, 222, 326)
        body.cubicTo(186, 356, 158, 402, 118, 398)
        body.cubicTo(72, 392, 52, 330, 66, 248)
        body.cubicTo(78, 178, 104, 126, 155, 114)
        body.closeSubpath()

        p.save()
        p.translate(0, 5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 70))
        p.drawPath(body)
        p.restore()

        shell_grad = QLinearGradient(310, 105, 310, 400)
        shell_grad.setColorAt(0.0, QColor("#2B2E35"))
        shell_grad.setColorAt(0.55, QColor("#23252B"))
        shell_grad.setColorAt(1.0, QColor("#1B1D22"))

        p.setPen(QPen(QColor("#3C404A"), 1.6))
        p.setBrush(shell_grad)
        p.drawPath(body)

        p.setPen(QPen(QColor(255, 255, 255, 14), 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(86, 200, 90, 170), 100 * 16, 150 * 16)
        p.drawArc(QRectF(444, 200, 90, 170), -70 * 16, 150 * 16)

    def _draw_guide_and_center_buttons(self, p: QPainter) -> None:
        g_pressed = self._state.buttons.get("GUIDE", False)
        p.setPen(QPen(QColor("#EFEFE9") if g_pressed else QColor("#454852"), 1.8))
        p.setBrush(QColor("#EFEFE9") if g_pressed else QColor("#17191D"))
        p.drawEllipse(self.GUIDE_CENTER, 17.5, 17.5)

        p.setPen(QPen(QColor("#16171A") if g_pressed else QColor("#8E939D"), 2.2))
        cx, cy = self.GUIDE_CENTER.x(), self.GUIDE_CENTER.y()
        p.drawLine(QPointF(cx - 6.5, cy - 6.5), QPointF(cx + 6.5, cy + 6.5))
        p.drawLine(QPointF(cx + 6.5, cy - 6.5), QPointF(cx - 6.5, cy + 6.5))

        for key, center, symbol in (
            ("VIEW", self.VIEW_CENTER, "⧉"),
            ("MENU", self.MENU_CENTER, "☰"),
        ):
            pressed = self._state.buttons.get(key, False)
            p.setPen(QPen(QColor("#EFEFE9") if pressed else QColor("#40434C"), 1.4))
            p.setBrush(QColor("#EFEFE9") if pressed else QColor("#181A1E"))
            p.drawEllipse(center, 12, 12)
            p.setPen(QColor("#16171A") if pressed else QColor("#8E939D"))
            p.setFont(QFont("Sans Serif", 9, QFont.Bold))
            p.drawText(
                QRectF(center.x() - 12, center.y() - 12, 24, 24),
                Qt.AlignCenter,
                symbol,
            )

    def _draw_dpad(self, p: QPainter) -> None:
        c = self.DPAD_CENTER
        p.setPen(QPen(QColor("#363942"), 1.4))
        p.setBrush(QColor("#16171B"))
        p.drawEllipse(c, 38, 38)

        hx, hy = self._state.hat_x, self._state.hat_y
        arms = [
            (QRectF(c.x() - 10, c.y() - 31, 20, 21), hy < 0, "▲"),
            (QRectF(c.x() - 10, c.y() + 10, 20, 21), hy > 0, "▼"),
            (QRectF(c.x() - 31, c.y() - 10, 21, 20), hx < 0, "◀"),
            (QRectF(c.x() + 10, c.y() - 10, 21, 20), hx > 0, "▶"),
        ]
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#23262D"))
        p.drawRect(QRectF(c.x() - 10, c.y() - 10, 20, 20))

        for rect, active, arrow in arms:
            if active:
                p.setPen(QPen(QColor("#EFEFE9"), 1.4))
                p.setBrush(QColor("#EFEFE9"))
            else:
                p.setPen(QPen(QColor("#383B45"), 1.1))
                p.setBrush(QColor("#23262D"))
            p.drawRoundedRect(rect, 4, 4)
            p.setPen(QColor("#16171A") if active else QColor("#747882"))
            p.setFont(QFont("Sans Serif", 8, QFont.Bold))
            p.drawText(rect, Qt.AlignCenter, arrow)

    def _draw_abxy(self, p: QPainter) -> None:
        for name, (center, color) in self.ABXY_CENTERS.items():
            pressed = self._state.buttons.get(name, False)
            radius = 15.0 if pressed else 16.0
            if pressed:
                p.setPen(QPen(color.lighter(115), 1.8))
                p.setBrush(color)
            else:
                idle_border = QColor(color)
                idle_border.setAlpha(155)
                p.setPen(QPen(idle_border, 1.6))
                p.setBrush(QColor("#17191E"))

            p.drawEllipse(center, radius, radius)
            p.setPen(QColor("#121316") if pressed else color.lighter(115))
            p.setFont(QFont("Sans Serif", 10.5, QFont.Bold))
            p.drawText(
                QRectF(center.x() - 16, center.y() - 16, 32, 32),
                Qt.AlignCenter,
                name,
            )

    def _draw_stick(
        self,
        p: QPainter,
        center: QPointF,
        tag: str,
        raw_x: int,
        raw_y: int,
        out_x: int,
        out_y: int,
        dz: float,
        clicked: bool,
    ) -> None:
        well_r = 40.0
        travel = 24.0

        p.setPen(QPen(QColor("#EFEFE9") if clicked else QColor("#383B44"), 1.8))
        p.setBrush(QColor("#141519"))
        p.drawEllipse(center, well_r, well_r)

        dz_px = max(2.0, dz * travel)
        p.setPen(QPen(QColor(197, 155, 87, 110), 1.0, Qt.DashLine))
        p.setBrush(QColor(197, 155, 87, 22))
        if self._config.dz_mode in ("radial", "hybrid"):
            p.drawEllipse(center, dz_px, dz_px)
        else:
            p.drawRect(
                QRectF(
                    center.x() - dz_px,
                    center.y() - dz_px,
                    dz_px * 2.0,
                    dz_px * 2.0,
                )
            )

        p.setPen(QPen(QColor(154, 158, 168, 40), 1.0))
        p.drawLine(
            QPointF(center.x() - well_r + 6, center.y()),
            QPointF(center.x() + well_r - 6, center.y()),
        )
        p.drawLine(
            QPointF(center.x(), center.y() - well_r + 6),
            QPointF(center.x(), center.y() + well_r - 6),
        )

        raw_pos = QPointF(
            center.x() + (float(raw_x) / float(AXIS_MAX)) * travel,
            center.y() + (float(raw_y) / float(AXIS_MAX)) * travel,
        )
        out_pos = QPointF(
            center.x() + (float(out_x) / float(AXIS_MAX)) * travel,
            center.y() + (float(out_y) / float(AXIS_MAX)) * travel,
        )

        if self._state.mode != "disconnected":
            p.setPen(QPen(QColor("#C86A63"), 1.4))
            p.setBrush(QColor(200, 106, 99, 60))
            p.drawEllipse(raw_pos, 4.5, 4.5)

        cap_r = 20.5
        moved = abs(out_x) > 400 or abs(out_y) > 400
        if clicked:
            p.setPen(QPen(QColor("#EFEFE9"), 2.0))
            p.setBrush(QColor("#5680AD"))
        else:
            p.setPen(
                QPen(
                    QColor("#548C6E") if moved else QColor("#545863"),
                    1.8,
                )
            )
            p.setBrush(QColor("#30333B"))
        p.drawEllipse(out_pos, cap_r, cap_r)

        p.setPen(QColor("#F7F7F4") if (clicked or moved) else QColor("#B9BDC7"))
        p.setFont(QFont("Sans Serif", 9, QFont.Bold))
        p.drawText(
            QRectF(out_pos.x() - 16, out_pos.y() - 12, 32, 24),
            Qt.AlignCenter,
            f"{tag}3" if clicked else tag,
        )

    def _draw_extra_buttons(self, p: QPainter) -> None:
        rects = self._extra_badge_rects()
        if not rects:
            return
        for code, rect in rects:
            pressed = self._state.extra_buttons.get(code, False)
            if pressed:
                p.setPen(QPen(QColor("#EFEFE9"), 1.6))
                p.setBrush(QColor("#EFEFE9"))
            else:
                p.setPen(QPen(QColor("#3A3D46"), 1.2))
                p.setBrush(QColor("#1C1E24"))
            p.drawRoundedRect(rect, 6, 6)
            p.setPen(QColor("#16171A") if pressed else QColor("#9A9EA8"))
            p.setFont(QFont("Sans Serif", 8.5, QFont.Bold))
            p.drawText(rect, Qt.AlignCenter, short_extra_badge(code))
