"""Multi-family b7software vector controller visualizer (Xbox, PlayStation, Switch Pro, Joy-Con)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QWidget

from ..config import AXIS_MAX, AntiDriftConfig, TRIGGER_MAX, compute_corrected_stick
from ..evdev_reader import ControllerState
from ..macros import short_extra_badge


@dataclass(frozen=True)
class _ControllerLayoutSpec:
    family: str
    l_stick: QPointF
    r_stick: QPointF
    dpad: QPointF
    face_center: QPointF
    guide: QPointF
    view: QPointF
    menu: QPointF
    lb_rect: QRectF
    rb_rect: QRectF
    lt_rect: QRectF
    rt_rect: QRectF
    trigger_labels: tuple[str, str]
    bumper_labels: tuple[str, str]
    share: QPointF | None = None
    touchpad_rect: QRectF | None = None


_SPECS: dict[str, _ControllerLayoutSpec] = {
    "xbox": _ControllerLayoutSpec(
        family="xbox",
        l_stick=QPointF(166.0, 194.0),
        r_stick=QPointF(374.0, 278.0),
        dpad=QPointF(226.0, 278.0),
        face_center=QPointF(434.0, 194.0),
        guide=QPointF(300.0, 148.0),
        view=QPointF(256.0, 194.0),
        menu=QPointF(344.0, 194.0),
        lb_rect=QRectF(112, 62, 114, 26),
        rb_rect=QRectF(374, 62, 114, 26),
        lt_rect=QRectF(124, 14, 86, 38),
        rt_rect=QRectF(390, 14, 86, 38),
        trigger_labels=("LT", "RT"),
        bumper_labels=("LB", "RB"),
        share=QPointF(300.0, 218.0),
    ),
    "playstation": _ControllerLayoutSpec(
        family="playstation",
        l_stick=QPointF(228.0, 276.0),
        r_stick=QPointF(372.0, 276.0),
        dpad=QPointF(160.0, 192.0),
        face_center=QPointF(440.0, 192.0),
        guide=QPointF(300.0, 212.0),
        view=QPointF(214.0, 128.0),
        menu=QPointF(386.0, 128.0),
        lb_rect=QRectF(110, 62, 114, 26),
        rb_rect=QRectF(376, 62, 114, 26),
        lt_rect=QRectF(122, 14, 86, 38),
        rt_rect=QRectF(392, 14, 86, 38),
        trigger_labels=("L2", "R2"),
        bumper_labels=("L1", "R1"),
        touchpad_rect=QRectF(236, 106, 128, 70),
    ),
    "nintendo_pro": _ControllerLayoutSpec(
        family="nintendo_pro",
        l_stick=QPointF(170.0, 190.0),
        r_stick=QPointF(372.0, 276.0),
        dpad=QPointF(228.0, 276.0),
        face_center=QPointF(430.0, 190.0),
        guide=QPointF(324.0, 198.0),
        view=QPointF(248.0, 150.0),
        menu=QPointF(352.0, 150.0),
        lb_rect=QRectF(118, 62, 112, 26),
        rb_rect=QRectF(370, 62, 112, 26),
        lt_rect=QRectF(128, 14, 86, 38),
        rt_rect=QRectF(386, 14, 86, 38),
        trigger_labels=("ZL", "ZR"),
        bumper_labels=("L", "R"),
    ),
    "joycon": _ControllerLayoutSpec(
        family="joycon",
        l_stick=QPointF(164.0, 188.0),
        r_stick=QPointF(436.0, 284.0),
        dpad=QPointF(164.0, 284.0),
        face_center=QPointF(436.0, 188.0),
        guide=QPointF(412.0, 352.0),
        view=QPointF(194.0, 134.0),
        menu=QPointF(406.0, 134.0),
        lb_rect=QRectF(110, 62, 108, 26),
        rb_rect=QRectF(382, 62, 108, 26),
        lt_rect=QRectF(120, 14, 86, 38),
        rt_rect=QRectF(394, 14, 86, 38),
        trigger_labels=("ZL", "ZR"),
        bumper_labels=("L", "R"),
    ),
}


class ControllerWidget(QWidget):
    """Dynamic b7software controller visualizer adapting to Xbox, PlayStation, Switch Pro & Joy-Con."""

    simulated_state_changed = Signal(object)

    LOGICAL_W = 600.0
    LOGICAL_H = 492.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(400, 325)
        self._state = ControllerState()
        self._config = AntiDriftConfig()
        self._simulator_enabled = False
        self._macro_preview_active = False
        self._family_override: str | None = None
        self._drag_target: str | None = None

    def set_family_override(self, family: str | None) -> None:
        """Force preview family ('xbox'|'playstation'|'nintendo_pro'|'joycon') or None for Auto."""
        self._family_override = family if family in _SPECS else None
        self.update()

    def active_family(self) -> str:
        """Return currently displayed controller silhouette family."""
        if self._family_override in _SPECS:
            return self._family_override
        fam = getattr(self._state, "controller_family", "xbox")
        return fam if fam in _SPECS else "xbox"

    def _layout_spec(self) -> _ControllerLayoutSpec:
        return _SPECS[self.active_family()]

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
                controller_family=self.active_family(),
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

    def _face_buttons(self, spec: _ControllerLayoutSpec) -> dict[str, tuple[QPointF, str, QColor]]:
        cx, cy = spec.face_center.x(), spec.face_center.y()
        d = 31.0 if spec.family == "joycon" else 34.0
        pos = {
            "Y": QPointF(cx, cy - d),
            "B": QPointF(cx + d, cy),
            "A": QPointF(cx, cy + d),
            "X": QPointF(cx - d, cy),
        }
        if spec.family == "playstation":
            meta = {
                "Y": ("△", QColor("#34D399")),
                "B": ("○", QColor("#FB7185")),
                "A": ("✕", QColor("#38BDF8")),
                "X": ("□", QColor("#C084FC")),
            }
        elif spec.family in ("nintendo_pro", "joycon"):
            meta = {
                "Y": ("X", QColor("#94A3B8")),
                "B": ("A", QColor("#F43F5E")),
                "A": ("B", QColor("#F59E0B")),
                "X": ("Y", QColor("#0EA5E9")),
            }
        else:
            meta = {
                "Y": ("Y", QColor("#F59E0B")),
                "B": ("B", QColor("#EF4444")),
                "A": ("A", QColor("#059669")),
                "X": ("X", QColor("#0EA5E9")),
            }
        return {k: (pos[k], meta[k][0], meta[k][1]) for k in ("Y", "B", "A", "X")}

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
        fam = self.active_family()
        codes = [
            c
            for c in sorted(self._state.extra_buttons.keys())
            if not (fam == "xbox" and c == 167)
        ][:8]
        if not codes:
            return []
        badge_w = 50.0
        badge_h = 24.0
        gap = 8.0
        total_w = len(codes) * badge_w + (len(codes) - 1) * gap
        start_x = (self.LOGICAL_W - total_w) * 0.5
        y = 454.0
        return [
            (code, QRectF(start_x + idx * (badge_w + gap), y, badge_w, badge_h))
            for idx, code in enumerate(codes)
        ]

    def mousePressEvent(self, event) -> None:
        if not self._simulator_enabled:
            super().mousePressEvent(event)
            return
        spec = self._layout_spec()
        p = self._widget_to_logical(event.position())
        self._drag_target = None

        for tag, center in (("L", spec.l_stick), ("R", spec.r_stick)):
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
            ("GUIDE", spec.guide, 19.0),
            ("VIEW", spec.view, 14.0),
            ("MENU", spec.menu, 14.0),
        ]
        for btn_name, (center, _, _) in self._face_buttons(spec).items():
            hit_targets.append((btn_name, center, 18.0))

        for name, center, rad in hit_targets:
            if math.hypot(p.x() - center.x(), p.y() - center.y()) <= rad:
                self._state.buttons[name] = not self._state.buttons[name]
                self._emit_sim()
                return

        if spec.lb_rect.adjusted(-4, -4, 4, 4).contains(p):
            self._state.buttons["LB"] = not self._state.buttons["LB"]
            self._emit_sim()
            return
        if spec.rb_rect.adjusted(-4, -4, 4, 4).contains(p):
            self._state.buttons["RB"] = not self._state.buttons["RB"]
            self._emit_sim()
            return

        if spec.lt_rect.adjusted(-4, -4, 4, 4).contains(p):
            self._state.lt = TRIGGER_MAX if self._state.lt < 500 else 0
            self._emit_sim()
            return
        if spec.rt_rect.adjusted(-4, -4, 4, 4).contains(p):
            self._state.rt = TRIGGER_MAX if self._state.rt < 500 else 0
            self._emit_sim()
            return

        if spec.share is not None:
            s_rect = QRectF(spec.share.x() - 13.0, spec.share.y() - 11.0, 26.0, 22.0)
            if s_rect.contains(p):
                self._state.extra_buttons[167] = not self._state.extra_buttons.get(
                    167, False
                )
                self._emit_sim()
                return

        for code, rect in self._extra_badge_rects():
            if rect.contains(p):
                self._state.extra_buttons[code] = not self._state.extra_buttons.get(
                    code, False
                )
                self._emit_sim()
                return

        dx = p.x() - spec.dpad.x()
        dy = p.y() - spec.dpad.y()
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
        spec = self._layout_spec()
        center = spec.l_stick if tag == "L" else spec.r_stick
        nx = max(-1.0, min(1.0, (p.x() - center.x()) / 36.0))
        ny = max(-1.0, min(1.0, (p.y() - center.y()) / 36.0))
        rx = round(nx * AXIS_MAX)
        ry = round(ny * AXIS_MAX)
        if tag == "L":
            self._state.raw_lx, self._state.raw_ly = rx, ry
        else:
            self._state.raw_rx, self._state.raw_ry = rx, ry
        self._emit_sim()

    def paintEvent(self, _event) -> None:
        spec = self._layout_spec()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        scale, ox, oy = self._transform_params()
        painter.translate(ox, oy)
        painter.scale(scale, scale)

        self._draw_triggers_and_bumpers(painter, spec)
        self._draw_shell(painter, spec)
        self._draw_center_cluster(painter, spec)
        self._draw_dpad(painter, spec)
        self._draw_face_buttons(painter, spec)
        self._draw_stick(
            painter,
            spec.l_stick,
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
            spec.r_stick,
            "R",
            self._state.raw_rx,
            self._state.raw_ry,
            self._state.out_rx,
            self._state.out_ry,
            self._config.dz_r,
            self._state.buttons.get("RS", False),
        )
        self._draw_extra_buttons(painter)

    def _draw_triggers_and_bumpers(
        self, p: QPainter, spec: _ControllerLayoutSpec
    ) -> None:
        for label, val, rect in (
            (spec.trigger_labels[0], self._state.lt, spec.lt_rect),
            (spec.trigger_labels[1], self._state.rt, spec.rt_rect),
        ):
            frac = max(0.0, min(1.0, float(val) / float(TRIGGER_MAX)))
            p.setPen(QPen(QColor("#1E2630"), 1.3))
            p.setBrush(QColor("#07090C"))
            p.drawRoundedRect(rect, 7, 7)

            if frac > 0.01:
                fill_h = rect.height() * frac
                fill_rect = QRectF(
                    rect.left() + 2.0,
                    rect.bottom() - fill_h + 2.0,
                    rect.width() - 4.0,
                    max(4.0, fill_h - 4.0),
                )
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#059669"))
                p.drawRoundedRect(fill_rect, 5, 5)

            p.setPen(QColor("#F8FAFC") if frac > 0.25 else QColor("#94A3B8"))
            p.setFont(QFont("JetBrains Mono", 9, QFont.Bold))
            p.drawText(rect, Qt.AlignCenter, f"{label} {round(frac * 100)}%")

        for label, pressed, rect in (
            (spec.bumper_labels[0], self._state.buttons.get("LB", False), spec.lb_rect),
            (spec.bumper_labels[1], self._state.buttons.get("RB", False), spec.rb_rect),
        ):
            if pressed:
                p.setPen(QPen(QColor("#10B981"), 1.6))
                p.setBrush(QColor("#059669"))
                p.drawRoundedRect(rect, 8, 8)
                p.setPen(QColor("#FFFFFF"))
            else:
                p.setPen(QPen(QColor("#242E3A"), 1.3))
                p.setBrush(QColor("#11161C"))
                p.drawRoundedRect(rect, 8, 8)
                p.setPen(QColor("#94A3B8"))
            p.setFont(QFont("JetBrains Mono", 9, QFont.Bold))
            p.drawText(rect, Qt.AlignCenter, label)

    def _xbox_shell_path(self) -> QPainterPath:
        # Crisp Xbox One / Series X|S silhouette (tall flared grips, angled shoulders, arched belly)
        b = QPainterPath()
        b.moveTo(226, 108)
        b.lineTo(374, 108)
        b.quadTo(404, 96, 434, 98)
        b.cubicTo(486, 108, 514, 178, 534, 314)
        b.cubicTo(542, 376, 536, 424, 506, 434)
        b.quadTo(480, 440, 456, 412)
        b.cubicTo(420, 366, 392, 336, 368, 332)
        b.lineTo(232, 332)
        b.cubicTo(208, 336, 180, 366, 144, 412)
        b.quadTo(120, 440, 94, 434)
        b.cubicTo(64, 424, 58, 376, 66, 314)
        b.cubicTo(86, 178, 114, 108, 166, 98)
        b.quadTo(196, 96, 226, 108)
        b.closeSubpath()
        return b

    def _playstation_shell_path(self) -> QPainterPath:
        # DualSense sculpted tall grips & centered touchpad bridge
        b = QPainterPath()
        b.moveTo(214, 104)
        b.lineTo(386, 104)
        b.quadTo(418, 98, 452, 112)
        b.cubicTo(494, 130, 518, 206, 534, 326)
        b.cubicTo(542, 386, 528, 430, 496, 434)
        b.quadTo(468, 434, 446, 384)
        b.cubicTo(424, 340, 398, 328, 300, 328)
        b.cubicTo(202, 328, 176, 340, 154, 384)
        b.quadTo(132, 434, 104, 434)
        b.cubicTo(72, 430, 58, 386, 66, 326)
        b.cubicTo(82, 206, 106, 130, 148, 112)
        b.quadTo(182, 98, 214, 104)
        b.closeSubpath()
        return b

    def _nintendo_pro_shell_path(self) -> QPainterPath:
        # Switch Pro sculpted shoulders & vertical grips
        b = QPainterPath()
        b.moveTo(208, 108)
        b.lineTo(392, 108)
        b.quadTo(430, 102, 460, 118)
        b.cubicTo(496, 140, 512, 220, 516, 328)
        b.cubicTo(518, 382, 502, 420, 468, 422)
        b.quadTo(440, 422, 418, 372)
        b.cubicTo(396, 330, 368, 320, 300, 320)
        b.cubicTo(232, 320, 204, 330, 182, 372)
        b.quadTo(160, 422, 132, 422)
        b.cubicTo(98, 420, 82, 382, 84, 328)
        b.cubicTo(88, 220, 104, 140, 140, 118)
        b.quadTo(170, 102, 208, 108)
        b.closeSubpath()
        return b

    def _draw_shell(self, p: QPainter, spec: _ControllerLayoutSpec) -> None:
        if spec.family == "joycon":
            self._draw_joycon_shell(p)
            return

        if spec.family == "playstation":
            body = self._playstation_shell_path()
        elif spec.family == "nintendo_pro":
            body = self._nintendo_pro_shell_path()
        else:
            body = self._xbox_shell_path()

        p.save()
        p.translate(0, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 85))
        p.drawPath(body)
        p.restore()

        shell_grad = QLinearGradient(300, 96, 300, 434)
        shell_grad.setColorAt(0.0, QColor("#171E27"))
        shell_grad.setColorAt(0.55, QColor("#11161C"))
        shell_grad.setColorAt(1.0, QColor("#0B0E12"))
        p.setPen(QPen(QColor("#2A3544"), 1.6))
        p.setBrush(shell_grad)
        p.drawPath(body)

        if spec.family == "playstation" and spec.touchpad_rect is not None:
            tr = spec.touchpad_rect
            tp_path = QPainterPath()
            tp_path.moveTo(tr.left(), tr.top() + 2)
            tp_path.lineTo(tr.right(), tr.top() + 2)
            tp_path.lineTo(tr.right() - 6, tr.bottom() - 8)
            tp_path.quadTo(tr.right() - 8, tr.bottom(), tr.right() - 16, tr.bottom())
            tp_path.lineTo(tr.left() + 16, tr.bottom())
            tp_path.quadTo(tr.left() + 8, tr.bottom(), tr.left() + 6, tr.bottom() - 8)
            tp_path.closeSubpath()
            p.setPen(QPen(QColor("#059669"), 1.2))
            p.setBrush(QColor("#0B0F14"))
            p.drawPath(tp_path)
            # Subtle DualSense top lightbar accent
            p.setPen(QPen(QColor("#10B981"), 2.0))
            p.drawLine(
                QPointF(tr.left() + 12, tr.top() + 6),
                QPointF(tr.right() - 12, tr.top() + 6),
            )
        elif spec.family == "nintendo_pro":
            # Translucent Switch Pro center grip seam framing
            p.setPen(QPen(QColor(255, 255, 255, 18), 1.2))
            p.drawLine(QPointF(230, 110), QPointF(230, 322))
            p.drawLine(QPointF(370, 110), QPointF(370, 322))

    def _draw_joycon_shell(self, p: QPainter) -> None:
        # Clean center Joy-Con Comfort Grip bridge (stays framed cleanly between the two Joy-Con rails)
        grip = QPainterPath()
        grip.addRoundedRect(QRectF(204, 128, 192, 206), 16, 16)

        p.setPen(QPen(QColor("#242E3A"), 1.4))
        p.setBrush(QColor("#0D1116"))
        p.drawPath(grip)

        # Left & Right Joy-Con vertical rail shells
        left_jc = QRectF(108, 102, 112, 290)
        right_jc = QRectF(380, 102, 112, 290)

        for jc_rect, accent in (
            (left_jc, QColor("#0EA5E9")),
            (right_jc, QColor("#F43F5E")),
        ):
            path = QPainterPath()
            if jc_rect is left_jc:
                path.moveTo(jc_rect.right(), jc_rect.top())
                path.lineTo(jc_rect.left() + 42, jc_rect.top())
                path.quadTo(jc_rect.left(), jc_rect.top(), jc_rect.left(), jc_rect.top() + 44)
                path.lineTo(jc_rect.left(), jc_rect.bottom() - 44)
                path.quadTo(jc_rect.left(), jc_rect.bottom(), jc_rect.left() + 42, jc_rect.bottom())
                path.lineTo(jc_rect.right(), jc_rect.bottom())
                path.closeSubpath()
            else:
                path.moveTo(jc_rect.left(), jc_rect.top())
                path.lineTo(jc_rect.right() - 42, jc_rect.top())
                path.quadTo(jc_rect.right(), jc_rect.top(), jc_rect.right(), jc_rect.top() + 44)
                path.lineTo(jc_rect.right(), jc_rect.bottom() - 44)
                path.quadTo(jc_rect.right(), jc_rect.bottom(), jc_rect.right() - 42, jc_rect.bottom())
                path.lineTo(jc_rect.left(), jc_rect.bottom())
                path.closeSubpath()

            grad = QLinearGradient(jc_rect.left(), jc_rect.top(), jc_rect.left(), jc_rect.bottom())
            grad.setColorAt(0.0, QColor("#18202A"))
            grad.setColorAt(1.0, QColor("#10151C"))
            p.setPen(QPen(QColor("#2A3544"), 1.5))
            p.setBrush(grad)
            p.drawPath(path)

            # Iconic subtle outer rail strip
            strip_x = jc_rect.left() + 4 if jc_rect is left_jc else jc_rect.right() - 4
            p.setPen(QPen(accent, 2.2))
            p.drawLine(
                QPointF(strip_x, jc_rect.top() + 48),
                QPointF(strip_x, jc_rect.bottom() - 48),
            )

        # Center b7. status LEDs on Joy-Con grip
        p.setPen(Qt.NoPen)
        for i in range(4):
            p.setBrush(QColor("#10B981") if i == 0 else QColor("#26303D"))
            p.drawRect(QRectF(276 + i * 14, 224, 6, 6))

    def _draw_center_cluster(
        self, p: QPainter, spec: _ControllerLayoutSpec
    ) -> None:
        g_pressed = self._state.buttons.get("GUIDE", False)
        gc = spec.guide
        g_rad = 13.0 if spec.family in ("nintendo_pro", "joycon") else 17.0
        p.setPen(QPen(QColor("#10B981") if g_pressed else QColor("#E2E8F0") if spec.family == "xbox" else QColor("#2D3748"), 1.9))
        p.setBrush(QColor("#059669") if g_pressed else QColor("#080B0F"))
        p.drawEllipse(gc, g_rad, g_rad)

        if spec.family == "xbox":
            # Crisp curved Xbox sphere X clipped inside the Guide button
            p.save()
            clip = QPainterPath()
            clip.addEllipse(gc, g_rad - 1.8, g_rad - 1.8)
            p.setClipPath(clip)
            p.setPen(
                QPen(
                    QColor("#FFFFFF") if g_pressed else QColor("#E2E8F0"),
                    2.45,
                    Qt.SolidLine,
                    Qt.RoundCap,
                    Qt.RoundJoin,
                )
            )
            p.setBrush(Qt.NoBrush)
            r = g_rad * 0.88
            cx, cy = gc.x(), gc.y()
            for sign in (-1.0, 1.0):
                arm = QPainterPath()
                arm.moveTo(cx + sign * r * 0.72, cy - r * 0.68)
                arm.cubicTo(
                    cx + sign * r * 0.16,
                    cy - r * 0.34,
                    cx - sign * r * 0.28,
                    cy + r * 0.08,
                    cx - sign * r * 0.76,
                    cy + r * 0.78,
                )
                p.drawPath(arm)
            p.restore()
        elif spec.family == "playstation":
            p.setPen(QColor("#FFFFFF") if g_pressed else QColor("#94A3B8"))
            p.setFont(QFont("JetBrains Mono", 8, QFont.Bold))
            p.drawText(QRectF(gc.x() - 14, gc.y() - 10, 28, 20), Qt.AlignCenter, "PS")
        else:
            # Nintendo Home icon
            p.setPen(QColor("#FFFFFF") if g_pressed else QColor("#94A3B8"))
            p.setFont(QFont("Sans Serif", 8, QFont.Bold))
            p.drawText(QRectF(gc.x() - 12, gc.y() - 11, 24, 22), Qt.AlignCenter, "⌂")

        # View/Menu (or Create/Options, -/+)
        labels = (
            ("⧉", "☰")
            if spec.family in ("xbox", "playstation")
            else ("−", "+")
        )
        for key, center, symbol in (
            ("VIEW", spec.view, labels[0]),
            ("MENU", spec.menu, labels[1]),
        ):
            pressed = self._state.buttons.get(key, False)
            p.setPen(QPen(QColor("#10B981") if pressed else QColor("#CBD5E1") if spec.family == "xbox" else QColor("#2D3748"), 1.5))
            p.setBrush(QColor("#059669") if pressed else QColor("#080B0F"))
            if spec.family == "playstation":
                pill = QRectF(center.x() - 6, center.y() - 12, 12, 24)
                p.drawRoundedRect(pill, 5, 5)
            else:
                p.drawEllipse(center, 11.0, 11.0)
                p.setPen(QColor("#FFFFFF") if pressed else QColor("#94A3B8"))
                p.setFont(QFont("JetBrains Mono", 9, QFont.Bold))
                p.drawText(
                    QRectF(center.x() - 11, center.y() - 11, 22, 22),
                    Qt.AlignCenter,
                    symbol,
                )

        if spec.share is not None:
            sh_pressed = bool(
                self._state.extra_buttons.get(167, False)
                or self._state.buttons.get("SHARE", False)
            )
            sc = spec.share
            sh_rect = QRectF(sc.x() - 11.0, sc.y() - 8.5, 22.0, 17.0)
            p.setPen(
                QPen(
                    QColor("#10B981")
                    if sh_pressed
                    else QColor("#CBD5E1")
                    if spec.family == "xbox"
                    else QColor("#2D3748"),
                    1.5,
                )
            )
            p.setBrush(QColor("#059669") if sh_pressed else QColor("#080B0F"))
            p.drawRoundedRect(sh_rect, 4.5, 4.5)

            # Crisp Share/Capture upload tray + upward arrow icon
            p.setPen(
                QPen(
                    QColor("#FFFFFF") if sh_pressed else QColor("#94A3B8"),
                    1.45,
                    Qt.SolidLine,
                    Qt.RoundCap,
                    Qt.RoundJoin,
                )
            )
            p.setBrush(Qt.NoBrush)
            tray = QPainterPath()
            tray.moveTo(sc.x() - 4.4, sc.y() + 1.2)
            tray.lineTo(sc.x() - 4.4, sc.y() + 4.0)
            tray.lineTo(sc.x() + 4.4, sc.y() + 4.0)
            tray.lineTo(sc.x() + 4.4, sc.y() + 1.2)
            p.drawPath(tray)
            arrow = QPainterPath()
            arrow.moveTo(sc.x(), sc.y() + 1.8)
            arrow.lineTo(sc.x(), sc.y() - 4.0)
            arrow.moveTo(sc.x() - 2.6, sc.y() - 1.5)
            arrow.lineTo(sc.x(), sc.y() - 4.2)
            arrow.lineTo(sc.x() + 2.6, sc.y() - 1.5)
            p.drawPath(arrow)

        if spec.family == "nintendo_pro":
            # Extra decorative Capture button to match Switch Pro
            cap = QRectF(266, 189, 18, 18)
            p.setPen(QPen(QColor("#2D3748"), 1.3))
            p.setBrush(QColor("#080B0F"))
            p.drawRoundedRect(cap, 3, 3)

    def _draw_dpad(self, p: QPainter, spec: _ControllerLayoutSpec) -> None:
        c = spec.dpad
        hx, hy = self._state.hat_x, self._state.hat_y

        if spec.family == "joycon":
            # 4 separate Joy-Con directional button circles
            btns = [
                (QPointF(c.x(), c.y() - 23), hy < 0, "▲"),
                (QPointF(c.x(), c.y() + 23), hy > 0, "▼"),
                (QPointF(c.x() - 23, c.y()), hx < 0, "◀"),
                (QPointF(c.x() + 23, c.y()), hx > 0, "▶"),
            ]
            for pt, active, arrow in btns:
                p.setPen(QPen(QColor("#10B981") if active else QColor("#2A3544"), 1.4))
                p.setBrush(QColor("#059669") if active else QColor("#080B0F"))
                p.drawEllipse(pt, 11.0, 11.0)
                p.setPen(QColor("#FFFFFF") if active else QColor("#64748B"))
                p.setFont(QFont("Sans Serif", 7.5, QFont.Bold))
                p.drawText(QRectF(pt.x() - 10, pt.y() - 10, 20, 20), Qt.AlignCenter, arrow)
            return

        if spec.family == "playstation":
            # 4 segmented PlayStation directional wedges
            wedges = [
                (QPolygonF([QPointF(c.x() - 9, c.y() - 28), QPointF(c.x() + 9, c.y() - 28), QPointF(c.x() + 9, c.y() - 12), QPointF(c.x(), c.y() - 5), QPointF(c.x() - 9, c.y() - 12)]), hy < 0),
                (QPolygonF([QPointF(c.x() - 9, c.y() + 28), QPointF(c.x() + 9, c.y() + 28), QPointF(c.x() + 9, c.y() + 12), QPointF(c.x(), c.y() + 5), QPointF(c.x() - 9, c.y() + 12)]), hy > 0),
                (QPolygonF([QPointF(c.x() - 28, c.y() - 9), QPointF(c.x() - 28, c.y() + 9), QPointF(c.x() - 12, c.y() + 9), QPointF(c.x() - 5, c.y()), QPointF(c.x() - 12, c.y() - 9)]), hx < 0),
                (QPolygonF([QPointF(c.x() + 28, c.y() - 9), QPointF(c.x() + 28, c.y() + 9), QPointF(c.x() + 12, c.y() + 9), QPointF(c.x() + 5, c.y()), QPointF(c.x() + 12, c.y() - 9)]), hx > 0),
            ]
            for poly, active in wedges:
                p.setPen(QPen(QColor("#10B981") if active else QColor("#2D3748"), 1.4))
                p.setBrush(QColor("#059669") if active else QColor("#0B0F14"))
                p.drawPolygon(poly)
            return

        # Clean sharp Cross '+' D-Pad (Xbox & Switch Pro, matching uploaded silhouette!)
        arm_w = 10.5
        arm_len = 28.0
        cross = QPolygonF(
            [
                QPointF(c.x() - arm_w, c.y() - arm_len),
                QPointF(c.x() + arm_w, c.y() - arm_len),
                QPointF(c.x() + arm_w, c.y() - arm_w),
                QPointF(c.x() + arm_len, c.y() - arm_w),
                QPointF(c.x() + arm_len, c.y() + arm_w),
                QPointF(c.x() + arm_w, c.y() + arm_w),
                QPointF(c.x() + arm_w, c.y() + arm_len),
                QPointF(c.x() - arm_w, c.y() + arm_len),
                QPointF(c.x() - arm_w, c.y() + arm_w),
                QPointF(c.x() - arm_len, c.y() + arm_w),
                QPointF(c.x() - arm_len, c.y() - arm_w),
                QPointF(c.x() - arm_w, c.y() - arm_w),
            ]
        )
        p.setPen(QPen(QColor("#E2E8F0") if spec.family == "xbox" else QColor("#2D3748"), 1.8))
        p.setBrush(QColor("#07090C"))
        p.drawPolygon(cross)

        # Highlight pressed directional arm cleanly inside the cross
        arms = [
            (QRectF(c.x() - 9, c.y() - 26, 18, 16), hy < 0, "▲"),
            (QRectF(c.x() - 9, c.y() + 10, 18, 16), hy > 0, "▼"),
            (QRectF(c.x() - 26, c.y() - 9, 16, 18), hx < 0, "◀"),
            (QRectF(c.x() + 10, c.y() - 9, 16, 18), hx > 0, "▶"),
        ]
        for rect, active, arrow in arms:
            if active:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#059669"))
                p.drawRoundedRect(rect, 3, 3)
            p.setPen(QColor("#FFFFFF") if active else QColor("#64748B"))
            p.setFont(QFont("Sans Serif", 7.5, QFont.Bold))
            p.drawText(rect, Qt.AlignCenter, arrow)

    def _draw_face_buttons(
        self, p: QPainter, spec: _ControllerLayoutSpec
    ) -> None:
        rad = 13.0 if spec.family == "joycon" else 15.5
        for key, (center, symbol, color) in self._face_buttons(spec).items():
            pressed = self._state.buttons.get(key, False)
            r = rad - 1.0 if pressed else rad
            if pressed:
                p.setPen(QPen(color.lighter(120), 1.8))
                p.setBrush(color)
            else:
                border = QColor("#E2E8F0") if spec.family == "xbox" else QColor(color)
                if spec.family != "xbox":
                    border.setAlpha(170)
                p.setPen(QPen(border, 1.7))
                p.setBrush(QColor("#07090C"))
            p.drawEllipse(center, r, r)

            fg = QColor("#FFFFFF") if pressed else color.lighter(115)
            if spec.family == "playstation":
                self._draw_ps_symbol(p, center, symbol, fg, r * 0.45)
            else:
                p.setPen(fg)
                p.setFont(QFont("JetBrains Mono", 9.5 if spec.family == "joycon" else 10.5, QFont.Bold))
                p.drawText(
                    QRectF(center.x() - 16, center.y() - 16, 32, 32),
                    Qt.AlignCenter,
                    symbol,
                )

    def _draw_ps_symbol(
        self, p: QPainter, c: QPointF, symbol: str, color: QColor, sz: float
    ) -> None:
        p.setPen(QPen(color, 1.9))
        p.setBrush(Qt.NoBrush)
        if symbol == "△":
            tri = QPolygonF(
                [
                    QPointF(c.x(), c.y() - sz),
                    QPointF(c.x() + sz * 0.92, c.y() + sz * 0.72),
                    QPointF(c.x() - sz * 0.92, c.y() + sz * 0.72),
                ]
            )
            p.drawPolygon(tri)
        elif symbol == "○":
            p.drawEllipse(c, sz * 0.82, sz * 0.82)
        elif symbol == "✕":
            d = sz * 0.74
            p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
            p.drawLine(QPointF(c.x() + d, c.y() - d), QPointF(c.x() - d, c.y() + d))
        else:
            d = sz * 0.75
            p.drawRect(QRectF(c.x() - d, c.y() - d, d * 2.0, d * 2.0))

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
        well_r = 35.0 if self.active_family() == "joycon" else 39.0
        travel = 21.0 if self.active_family() == "joycon" else 24.0

        # Double concentric outer rings matching uploaded Xbox outline cleanly
        p.setPen(QPen(QColor("#10B981") if clicked else QColor("#E2E8F0") if self.active_family() == "xbox" else QColor("#2A3544"), 1.8))
        p.setBrush(QColor("#07090C"))
        p.drawEllipse(center, well_r, well_r)

        p.setPen(QPen(QColor("#242E3A"), 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(center, well_r - 6.0, well_r - 6.0)

        dz_px = max(2.0, dz * travel)
        p.setPen(QPen(QColor(16, 185, 129, 120), 1.0, Qt.DashLine))
        p.setBrush(QColor(5, 150, 105, 22))
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

        raw_pos = QPointF(
            center.x() + (float(raw_x) / float(AXIS_MAX)) * travel,
            center.y() + (float(raw_y) / float(AXIS_MAX)) * travel,
        )
        out_pos = QPointF(
            center.x() + (float(out_x) / float(AXIS_MAX)) * travel,
            center.y() + (float(out_y) / float(AXIS_MAX)) * travel,
        )

        if self._state.mode != "disconnected":
            p.setPen(QPen(QColor("#F43F5E"), 1.5))
            p.setBrush(QColor(244, 63, 94, 75))
            p.drawEllipse(raw_pos, 4.5, 4.5)

        cap_r = 18.0 if self.active_family() == "joycon" else 20.0
        moved = abs(out_x) > 400 or abs(out_y) > 400
        if clicked:
            p.setPen(QPen(QColor("#34D399"), 2.0))
            p.setBrush(QColor("#059669"))
        else:
            p.setPen(
                QPen(
                    QColor("#10B981") if moved else QColor("#E2E8F0") if self.active_family() == "xbox" else QColor("#3B4756"),
                    1.8,
                )
            )
            p.setBrush(QColor("#141920"))
        p.drawEllipse(out_pos, cap_r, cap_r)

        p.setPen(QColor("#F8FAFC") if (clicked or moved) else QColor("#94A3B8"))
        p.setFont(QFont("JetBrains Mono", 9, QFont.Bold))
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
                p.setPen(QPen(QColor("#10B981"), 1.6))
                p.setBrush(QColor("#059669"))
            else:
                p.setPen(QPen(QColor("#242E3A"), 1.2))
                p.setBrush(QColor("#10141A"))
            p.drawRoundedRect(rect, 5, 5)
            p.setPen(QColor("#FFFFFF") if pressed else QColor("#94A3B8"))
            p.setFont(QFont("JetBrains Mono", 8.5, QFont.Bold))
            p.drawText(rect, Qt.AlignCenter, short_extra_badge(code))
