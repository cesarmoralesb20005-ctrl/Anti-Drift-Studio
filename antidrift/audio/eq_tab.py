"""Friendly System Sound & 10-Band Equalizer studio tab."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..widgets.fit_scroll import FitWidthScrollArea
from .eq_backend import (
    AudioAppStream,
    AudioSinkInfo,
    get_active_sink_info,
    list_physical_sinks,
    list_sink_inputs,
    set_sink_input_mute,
    set_sink_input_volume,
    set_sink_mute,
    set_sink_volume_and_balance,
    start_or_update_eq,
)
from .eq_config import (
    EQ_BAND_FREQS,
    EQ_BAND_LABELS,
    EQ_MAX_DB,
    EQ_MIN_DB,
    EQ_PRESETS,
    EqualizerConfig,
    compute_eq_curve_db,
    effective_preamp_db,
    load_eq_config,
    save_eq_config,
)

_QUICK_PRESETS: tuple[tuple[str, str], ...] = (
    ("Plano (Neutro)", "Plano"),
    ("Gaming / Pasos FPS", "Pasos FPS"),
    ("Graves Potentes", "Graves+"),
    ("Voces y Discord", "Voces"),
    ("Música (V-Shape)", "Música"),
    ("Modo Nocturno", "Nocturno"),
)


def _format_db(val: float) -> str:
    if abs(val) < 0.05:
        return "0.0"
    return f"{val:+.1f}"


class _DoubleClickResetSlider(QSlider):
    """QSlider that emits reset_requested on double-click for instant 0 dB / center reset."""

    reset_requested = Signal()

    def mouseDoubleClickEvent(self, event) -> None:
        self.reset_requested.emit()
        event.accept()


class EqCurveWidget(QWidget):
    """Interactive logarithmic 20 Hz - 20 kHz equalizer response curve canvas."""

    band_changed = Signal(int, float)
    band_reset = Signal(int)

    _F_MIN = 20.0
    _F_MAX = 20000.0
    _DB_RANGE = 15.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(152)
        self.setMaximumHeight(185)
        self.setMouseTracking(True)
        self._bands: list[float] = [0.0] * len(EQ_BAND_FREQS)
        self._preamp_db: float = 0.0
        self._auto_preamp: bool = True
        self._active: bool = False
        self._bypass: bool = False
        self._drag_idx: int = -1
        self._hover_idx: int = -1

    def set_response_state(
        self,
        band_gains: list[float],
        preamp_db: float,
        auto_preamp: bool,
        active: bool,
        bypass: bool,
    ) -> None:
        self._bands = list(band_gains[: len(EQ_BAND_FREQS)])
        while len(self._bands) < len(EQ_BAND_FREQS):
            self._bands.append(0.0)
        self._preamp_db = float(preamp_db)
        self._auto_preamp = bool(auto_preamp)
        self._active = bool(active)
        self._bypass = bool(bypass)
        self.update()

    def _plot_rect(self) -> QRectF:
        return QRectF(36.0, 14.0, max(60.0, self.width() - 50.0), max(40.0, self.height() - 36.0))

    def _freq_to_x(self, freq_hz: float, pr: QRectF) -> float:
        lo = math.log10(self._F_MIN)
        hi = math.log10(self._F_MAX)
        t = (math.log10(max(self._F_MIN, min(self._F_MAX, freq_hz))) - lo) / (hi - lo)
        return pr.left() + t * pr.width()

    def _db_to_y(self, db: float, pr: QRectF) -> float:
        clamped = max(-self._DB_RANGE, min(self._DB_RANGE, db))
        t = (self._DB_RANGE - clamped) / (2.0 * self._DB_RANGE)
        return pr.top() + t * pr.height()

    def _y_to_db(self, y: float, pr: QRectF) -> float:
        t = (y - pr.top()) / max(1.0, pr.height())
        db = self._DB_RANGE - t * (2.0 * self._DB_RANGE)
        snapped = round(max(EQ_MIN_DB, min(EQ_MAX_DB, db)) * 2.0) / 2.0
        return snapped

    def _handle_points(self, pr: QRectF) -> list[QPointF]:
        return [
            QPointF(self._freq_to_x(float(f), pr), self._db_to_y(g, pr))
            for f, g in zip(EQ_BAND_FREQS, self._bands)
        ]

    def _closest_handle(self, pos: QPointF, max_dist: float = 18.0) -> int:
        pr = self._plot_rect()
        pts = self._handle_points(pr)
        best_i = -1
        best_d = max_dist
        for i, pt in enumerate(pts):
            d = math.hypot(pos.x() - pt.x(), pos.y() - pt.y())
            if d <= best_d:
                best_d = d
                best_i = i
        return best_i

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            idx = self._closest_handle(event.position(), max_dist=22.0)
            if idx < 0:
                # Pick nearest frequency column when clicking directly on plot
                pr = self._plot_rect()
                if pr.contains(event.position()):
                    pts = self._handle_points(pr)
                    idx = min(range(len(pts)), key=lambda i: abs(pts[i].x() - event.position().x()))
            if idx >= 0:
                self._drag_idx = idx
                self._hover_idx = idx
                db = self._y_to_db(event.position().y(), self._plot_rect())
                self.band_changed.emit(idx, db)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pr = self._plot_rect()
        if self._drag_idx >= 0:
            db = self._y_to_db(event.position().y(), pr)
            self.band_changed.emit(self._drag_idx, db)
            event.accept()
            return
        hov = self._closest_handle(event.position(), max_dist=18.0)
        if hov != self._hover_idx:
            self._hover_idx = hov
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_idx >= 0:
            self._drag_idx = -1
            self.update()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        idx = self._closest_handle(event.position(), max_dist=20.0)
        if idx >= 0:
            self.band_reset.emit(idx)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover_idx != -1:
            self._hover_idx = -1
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        outer = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        p.setPen(QPen(QColor("#1E2630"), 1.0))
        p.setBrush(QColor("#07090C"))
        p.drawRoundedRect(outer, 8, 8)

        pr = self._plot_rect()
        # Horizontal dB grid
        p.setFont(QFont("JetBrains Mono", 8))
        for db in (12, 6, 0, -6, -12):
            y = self._db_to_y(float(db), pr)
            if db == 0:
                p.setPen(QPen(QColor("#2E3A48"), 1.2))
            else:
                p.setPen(QPen(QColor("#151C24"), 1.0, Qt.DashLine))
            p.drawLine(QPointF(pr.left(), y), QPointF(pr.right(), y))
            p.setPen(QColor("#64748B"))
            lbl = f"{db:+d}" if db != 0 else "0"
            p.drawText(QRectF(4.0, y - 8.0, 28.0, 16.0), Qt.AlignRight | Qt.AlignVCenter, lbl)

        # Vertical frequency grid
        for freq, lbl in zip(EQ_BAND_FREQS, EQ_BAND_LABELS):
            x = self._freq_to_x(float(freq), pr)
            p.setPen(QPen(QColor("#151C24"), 1.0))
            p.drawLine(QPointF(x, pr.top()), QPointF(x, pr.bottom()))
            p.setPen(QColor("#64748B"))
            p.drawText(QRectF(x - 16.0, pr.bottom() + 4.0, 32.0, 14.0), Qt.AlignCenter, lbl)

        # Effective preamp headroom dashed indicator
        eff_pre = effective_preamp_db(
            self._bands, self._preamp_db, self._auto_preamp, bypass=self._bypass
        )
        if abs(eff_pre) >= 0.1:
            py = self._db_to_y(eff_pre, pr)
            p.setPen(QPen(QColor(56, 189, 248, 120), 1.0, Qt.DotLine))
            p.drawLine(QPointF(pr.left(), py), QPointF(pr.right(), py))

        # Plot tonal shaping curve directly through the 10 band handles, while the
        # dashed cyan line indicates effective anti-clipping/preamp headroom.
        n_pts = 110
        freqs = [self._F_MIN * ((self._F_MAX / self._F_MIN) ** (i / (n_pts - 1))) for i in range(n_pts)]
        dbs = compute_eq_curve_db(
            self._bands,
            0.0,
            False,
            freqs_hz=freqs,
            bypass=self._bypass,
        )
        # Draw band-shaping curve around 0 dB when previewing, or with preamp shift
        curve_pts = [QPointF(self._freq_to_x(f, pr), self._db_to_y(db, pr)) for f, db in zip(freqs, dbs)]
        curve_path = QPainterPath()
        curve_path.moveTo(curve_pts[0])
        for pt in curve_pts[1:]:
            curve_path.lineTo(pt)

        y0 = self._db_to_y(0.0, pr)
        fill_path = QPainterPath(curve_path)
        fill_path.lineTo(curve_pts[-1].x(), y0)
        fill_path.lineTo(curve_pts[0].x(), y0)
        fill_path.closeSubpath()

        live_color = QColor("#10B981") if (self._active and not self._bypass) else QColor("#64748B")
        grad = QLinearGradient(0, pr.top(), 0, pr.bottom())
        grad.setColorAt(0.0, QColor(live_color.red(), live_color.green(), live_color.blue(), 85))
        grad.setColorAt(0.5, QColor(live_color.red(), live_color.green(), live_color.blue(), 18))
        grad.setColorAt(1.0, QColor(live_color.red(), live_color.green(), live_color.blue(), 65))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawPath(fill_path)

        p.setPen(QPen(live_color, 2.2, Qt.SolidLine if not self._bypass else Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawPath(curve_path)

        # Interactive control points
        pts = self._handle_points(pr)
        for i, pt in enumerate(pts):
            is_hot = i in (self._hover_idx, self._drag_idx)
            r = 5.5 if is_hot else 4.0
            if is_hot:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(16, 185, 129, 65))
                p.drawEllipse(pt, 9.5, 9.5)
            p.setPen(QPen(QColor("#E2E8F0") if is_hot else live_color, 1.5))
            p.setBrush(QColor("#059669") if (is_hot or abs(self._bands[i]) >= 0.1) else QColor("#0E1319"))
            p.drawEllipse(pt, r, r)

        hot = self._drag_idx if self._drag_idx >= 0 else self._hover_idx
        if 0 <= hot < len(EQ_BAND_FREQS):
            pt = pts[hot]
            tag = f"{EQ_BAND_LABELS[hot]} Hz: {_format_db(self._bands[hot])} dB"
            p.setFont(QFont("JetBrains Mono", 8.5, QFont.Bold))
            bw = 112.0
            bx = max(pr.left(), min(pr.right() - bw, pt.x() - bw * 0.5))
            by = max(pr.top(), pt.y() - 28.0) if pt.y() > pr.top() + 30.0 else pt.y() + 12.0
            pill = QRectF(bx, by, bw, 19.0)
            p.setPen(QPen(QColor("#10B981"), 1.0))
            p.setBrush(QColor("#0D131A"))
            p.drawRoundedRect(pill, 4, 4)
            p.setPen(QColor("#F1F5F9"))
            p.drawText(pill, Qt.AlignCenter, tag)


class EqTabWidget(QWidget):
    """Friendly system sound controller and 10-band PipeWire Equalizer studio."""

    def __init__(self, start_background: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._config: EqualizerConfig = load_eq_config()
        self._syncing = False
        self._start_background = start_background
        self._band_sliders: list[_DoubleClickResetSlider] = []
        self._band_val_labels: list[QLabel] = []
        self._active_sink_name: str = ""
        self._master_muted: bool = False

        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.setInterval(45)
        self._live_timer.timeout.connect(self._flush_live_eq_params)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(350)
        self._save_timer.timeout.connect(self._save_only)

        self._build_ui()
        self._apply_config_to_ui()

        if start_background:
            QTimer.singleShot(250, self._initial_sync)

    def shutdown(self) -> None:
        """Flush pending equalizer timers cleanly when closing the window."""
        if self._live_timer.isActive():
            self._live_timer.stop()
            self._flush_live_eq_params()
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_only()

    def _initial_sync(self) -> None:
        self.refresh_audio_devices()
        self.refresh_app_streams()
        if self._config.enabled:
            self._apply_backend_full()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 6, 0, 0)
        outer.setSpacing(8)

        scroll = FitWidthScrollArea()
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(10)

        # ── Card 1: Master Sound, Device & EQ Switch ──
        top_card = QFrame()
        top_card.setObjectName("Card")
        t_lay = QVBoxLayout(top_card)
        t_lay.setContentsMargins(12, 10, 12, 10)
        t_lay.setSpacing(8)

        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(8)
        title = QLabel("Ecualizador del Sistema")
        title.setObjectName("SectionTitle")
        hdr_row.addWidget(title)

        self._btn_enable = QPushButton("Ecualizador: APAGADO")
        self._btn_enable.setCheckable(True)
        self._btn_enable.setObjectName("ProtectToggleOff")
        self._btn_enable.toggled.connect(self._on_enable_toggled)
        hdr_row.addWidget(self._btn_enable)

        self._btn_bypass = QPushButton("Comparar (Bypass)")
        self._btn_bypass.setCheckable(True)
        self._btn_bypass.setObjectName("MiniPill")
        self._btn_bypass.setToolTip(
            "Silencia temporalmente las bandas (A/B) sin desconectar el audio"
        )
        self._btn_bypass.toggled.connect(self._on_bypass_toggled)
        hdr_row.addWidget(self._btn_bypass)
        hdr_row.addStretch(1)

        t_lay.addLayout(hdr_row)

        dev_row = QHBoxLayout()
        dev_row.setSpacing(8)
        dev_row.addWidget(QLabel("Salida:"))
        self._sink_combo = QComboBox()
        self._sink_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self._sink_combo.setMinimumContentsLength(18)
        self._sink_combo.currentIndexChanged.connect(self._on_output_sink_changed)
        dev_row.addWidget(self._sink_combo, 1)

        btn_ref_dev = QPushButton("↻")
        btn_ref_dev.setObjectName("MiniPill")
        btn_ref_dev.setFixedWidth(32)
        btn_ref_dev.setToolTip("Actualizar dispositivos de audio")
        btn_ref_dev.clicked.connect(self.refresh_audio_devices)
        dev_row.addWidget(btn_ref_dev)
        t_lay.addLayout(dev_row)

        vol_row = QHBoxLayout()
        vol_row.setSpacing(8)
        self._btn_mute = QPushButton("🔊")
        self._btn_mute.setObjectName("MiniPill")
        self._btn_mute.setCheckable(True)
        self._btn_mute.setFixedWidth(36)
        self._btn_mute.setToolTip("Silenciar / activar audio del sistema")
        self._btn_mute.toggled.connect(self._on_mute_toggled)
        vol_row.addWidget(self._btn_mute)

        vol_row.addWidget(QLabel("Volumen:"))
        self._vol_slider = _DoubleClickResetSlider(Qt.Horizontal)
        self._vol_slider.setRange(0, 130)
        self._vol_slider.setValue(100)
        self._vol_slider.valueChanged.connect(self._on_master_volume_changed)
        self._vol_slider.reset_requested.connect(lambda: self._vol_slider.setValue(100))
        vol_row.addWidget(self._vol_slider, 2)

        self._vol_lbl = QLabel("100%")
        self._vol_lbl.setMinimumWidth(42)
        self._vol_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono', monospace; font-weight: 700; color: #10B981;"
        )
        vol_row.addWidget(self._vol_lbl)

        vol_row.addWidget(QLabel("Balance:"))
        self._bal_slider = _DoubleClickResetSlider(Qt.Horizontal)
        self._bal_slider.setRange(-100, 100)
        self._bal_slider.setValue(0)
        self._bal_slider.setToolTip("Balance Izquierda / Derecha (doble clic para centrar)")
        self._bal_slider.valueChanged.connect(self._on_balance_changed)
        self._bal_slider.reset_requested.connect(lambda: self._bal_slider.setValue(0))
        vol_row.addWidget(self._bal_slider, 1)

        self._bal_lbl = QLabel("Centro")
        self._bal_lbl.setMinimumWidth(48)
        self._bal_lbl.setObjectName("MutedNote")
        vol_row.addWidget(self._bal_lbl)
        t_lay.addLayout(vol_row)

        self._status_lbl = QLabel("Ecualizador apagado · Pulsa el interruptor para activar.")
        self._status_lbl.setObjectName("MutedNote")
        self._status_lbl.setWordWrap(True)
        t_lay.addWidget(self._status_lbl)
        lay.addWidget(top_card)

        # ── Card 2: Presets + Curve + 10-Band Equalizer ──
        eq_card = QFrame()
        eq_card.setObjectName("Card")
        e_lay = QVBoxLayout(eq_card)
        e_lay.setContentsMargins(12, 10, 12, 10)
        e_lay.setSpacing(8)

        pre_row = QHBoxLayout()
        pre_row.setSpacing(6)
        pre_row.addWidget(QLabel("Preset:"))
        self._preset_combo = QComboBox()
        self._preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        pre_row.addWidget(self._preset_combo, 1)

        btn_save_pre = QPushButton("+ Guardar")
        btn_save_pre.setObjectName("MiniPill")
        btn_save_pre.clicked.connect(self._on_save_custom_preset)
        pre_row.addWidget(btn_save_pre)

        self._btn_del_pre = QPushButton("Borrar")
        self._btn_del_pre.setObjectName("MiniPill")
        self._btn_del_pre.clicked.connect(self._on_delete_custom_preset)
        pre_row.addWidget(self._btn_del_pre)

        btn_flat = QPushButton("Reset 0 dB")
        btn_flat.setObjectName("MiniPill")
        btn_flat.clicked.connect(lambda: self._apply_preset_by_name("Plano (Neutro)"))
        pre_row.addWidget(btn_flat)
        e_lay.addLayout(pre_row)

        # One-click friendly quick preset pills
        pills_row = QHBoxLayout()
        pills_row.setSpacing(6)
        for full_name, short_lbl in _QUICK_PRESETS:
            b = QPushButton(short_lbl)
            b.setObjectName("MiniPill")
            b.setToolTip(str(EQ_PRESETS[full_name]["desc"]))
            b.clicked.connect(lambda _=False, n=full_name: self._apply_preset_by_name(n))
            pills_row.addWidget(b)
        e_lay.addLayout(pills_row)

        self._preset_desc_lbl = QLabel("")
        self._preset_desc_lbl.setObjectName("MutedNote")
        e_lay.addWidget(self._preset_desc_lbl)

        self._curve = EqCurveWidget()
        self._curve.band_changed.connect(self._on_curve_band_dragged)
        self._curve.band_reset.connect(lambda idx: self._set_single_band(idx, 0.0))
        e_lay.addWidget(self._curve)

        # Preamp + 10 ISO Bands
        faders_row = QHBoxLayout()
        faders_row.setSpacing(4)

        pre_col = QVBoxLayout()
        pre_col.setSpacing(3)
        self._preamp_lbl = QLabel("0.0")
        self._preamp_lbl.setAlignment(Qt.AlignCenter)
        self._preamp_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono', monospace; font-size: 10px; font-weight: 700; color: #38BDF8;"
        )
        pre_col.addWidget(self._preamp_lbl)

        self._preamp_slider = _DoubleClickResetSlider(Qt.Vertical)
        self._preamp_slider.setRange(-120, 120)
        self._preamp_slider.setSingleStep(5)
        self._preamp_slider.setPageStep(10)
        self._preamp_slider.setMinimumHeight(130)
        self._preamp_slider.setToolTip("Ganancia previa (Preamp). Doble clic para 0 dB")
        self._preamp_slider.valueChanged.connect(self._on_preamp_changed)
        self._preamp_slider.reset_requested.connect(lambda: self._preamp_slider.setValue(0))
        pre_col.addWidget(self._preamp_slider, 1, Qt.AlignHCenter)

        pre_tag = QLabel("Pre")
        pre_tag.setAlignment(Qt.AlignCenter)
        pre_tag.setStyleSheet("font-size: 10px; font-weight: 700; color: #94A3B8;")
        pre_col.addWidget(pre_tag)

        self._auto_preamp_chk = QCheckBox("Auto")
        self._auto_preamp_chk.setToolTip(
            "Reduce automáticamente el preamp según el pico máximo para evitar saturación/clipping"
        )
        self._auto_preamp_chk.toggled.connect(self._on_auto_preamp_toggled)
        pre_col.addWidget(self._auto_preamp_chk, 0, Qt.AlignHCenter)
        faders_row.addLayout(pre_col)

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: #1E2630;")
        faders_row.addWidget(sep)

        for idx, lbl_txt in enumerate(EQ_BAND_LABELS):
            col = QVBoxLayout()
            col.setSpacing(3)

            val_lbl = QLabel("0.0")
            val_lbl.setAlignment(Qt.AlignCenter)
            val_lbl.setStyleSheet(
                "font-family: 'JetBrains Mono', monospace; font-size: 10px; font-weight: 700; color: #E2E8F0;"
            )
            col.addWidget(val_lbl)
            self._band_val_labels.append(val_lbl)

            sl = _DoubleClickResetSlider(Qt.Vertical)
            sl.setRange(-120, 120)
            sl.setSingleStep(5)
            sl.setPageStep(10)
            sl.setMinimumHeight(130)
            sl.setToolTip(f"Banda {lbl_txt} Hz (doble clic para 0.0 dB)")
            sl.valueChanged.connect(lambda val, i=idx: self._on_band_slider_changed(i, val))
            sl.reset_requested.connect(lambda i=idx: self._set_single_band(i, 0.0))
            col.addWidget(sl, 1, Qt.AlignHCenter)
            self._band_sliders.append(sl)

            hz_lbl = QLabel(lbl_txt)
            hz_lbl.setAlignment(Qt.AlignCenter)
            hz_lbl.setStyleSheet(
                "font-family: 'JetBrains Mono', monospace; font-size: 10px; color: #94A3B8;"
            )
            col.addWidget(hz_lbl)
            faders_row.addLayout(col, 1)

        e_lay.addLayout(faders_row)
        lay.addWidget(eq_card)

        # ── Card 3: Per-Application Mixer ──
        app_card = QFrame()
        app_card.setObjectName("Card")
        a_lay = QVBoxLayout(app_card)
        a_lay.setContentsMargins(12, 10, 12, 10)
        a_lay.setSpacing(6)

        a_hdr = QHBoxLayout()
        a_title = QLabel("Mezclador por aplicación")
        a_title.setObjectName("SectionTitle")
        a_hdr.addWidget(a_title)
        a_hdr.addStretch(1)
        btn_ref_apps = QPushButton("↻ Actualizar")
        btn_ref_apps.setObjectName("MiniPill")
        btn_ref_apps.clicked.connect(self.refresh_app_streams)
        a_hdr.addWidget(btn_ref_apps)
        a_lay.addLayout(a_hdr)

        self._apps_container = QVBoxLayout()
        self._apps_container.setSpacing(6)
        a_lay.addLayout(self._apps_container)
        lay.addWidget(app_card)

        lay.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

    def _rebuild_preset_combo(self) -> None:
        self._preset_combo.blockSignals(True)
        try:
            self._preset_combo.clear()
            for name in EQ_PRESETS:
                self._preset_combo.addItem(name, name)
            if self._config.custom_presets:
                for cname in sorted(self._config.custom_presets):
                    self._preset_combo.addItem(f"★ {cname}", cname)
            idx = self._preset_combo.findData(self._config.active_preset)
            if idx < 0:
                self._preset_combo.addItem(
                    f"Personalizado ({self._config.active_preset})",
                    self._config.active_preset,
                )
                idx = self._preset_combo.count() - 1
            self._preset_combo.setCurrentIndex(idx)
            self._btn_del_pre.setEnabled(
                self._config.active_preset in self._config.custom_presets
            )
        finally:
            self._preset_combo.blockSignals(False)

    def _apply_config_to_ui(self) -> None:
        self._syncing = True
        try:
            c = self._config
            self._btn_enable.setChecked(c.enabled)
            self._update_enable_button_style()
            self._btn_bypass.setChecked(c.bypass)
            self._btn_bypass.setEnabled(c.enabled)
            self._auto_preamp_chk.setChecked(c.auto_preamp)
            self._preamp_slider.setValue(int(round(c.preamp_db * 10.0)))
            self._bal_slider.setValue(int(round(c.balance * 100.0)))
            self._update_balance_label(c.balance)

            for i, g in enumerate(c.band_gains):
                self._band_sliders[i].setValue(int(round(g * 10.0)))
                self._band_val_labels[i].setText(_format_db(g))

            self._rebuild_preset_combo()
            self._update_preamp_label()
            self._update_curve()
            desc = EQ_PRESETS.get(c.active_preset, {}).get("desc", "")
            self._preset_desc_lbl.setText(str(desc or "Ajuste manual · Doble clic en cualquier control para centrar."))
        finally:
            self._syncing = False

    def _update_enable_button_style(self) -> None:
        on = self._config.enabled
        self._btn_enable.setText(
            "Ecualizador: ACTIVO" if on else "Ecualizador: APAGADO"
        )
        self._btn_enable.setObjectName("ProtectToggleOn" if on else "ProtectToggleOff")
        self._btn_enable.style().unpolish(self._btn_enable)
        self._btn_enable.style().polish(self._btn_enable)

    def _update_preamp_label(self) -> None:
        eff = effective_preamp_db(
            self._config.band_gains,
            self._config.preamp_db,
            self._config.auto_preamp,
            bypass=self._config.bypass,
        )
        self._preamp_lbl.setText(_format_db(eff))

    def _update_balance_label(self, bal: float) -> None:
        if abs(bal) < 0.03:
            self._bal_lbl.setText("Centro")
        elif bal < 0:
            self._bal_lbl.setText(f"I {int(round(abs(bal) * 100))}%")
        else:
            self._bal_lbl.setText(f"D {int(round(bal * 100))}%")

    def _update_curve(self) -> None:
        self._curve.set_response_state(
            self._config.band_gains,
            self._config.preamp_db,
            self._config.auto_preamp,
            self._config.enabled,
            self._config.bypass,
        )

    def refresh_audio_devices(self) -> None:
        sinks = list_physical_sinks()
        self._syncing = True
        try:
            self._sink_combo.clear()
            self._sink_combo.addItem("Automático (salida predeterminada)", "auto")
            for s in sinks:
                self._sink_combo.addItem(s.description, s.name)
            idx = self._sink_combo.findData(self._config.output_sink)
            self._sink_combo.setCurrentIndex(idx if idx >= 0 else 0)

            active: AudioSinkInfo | None = get_active_sink_info()
            if active is not None:
                self._active_sink_name = active.name
                self._master_muted = active.muted
                self._vol_slider.setValue(max(0, min(130, active.volume_pct)))
                self._vol_lbl.setText(f"{active.volume_pct}%")
                self._btn_mute.setChecked(active.muted)
                self._btn_mute.setText("🔇" if active.muted else "🔊")
        finally:
            self._syncing = False

    def refresh_app_streams(self) -> None:
        while self._apps_container.count():
            item = self._apps_container.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        streams: list[AudioAppStream] = list_sink_inputs()
        if not streams:
            empty = QLabel("No hay aplicaciones reproduciendo audio en este momento.")
            empty.setObjectName("MutedNote")
            self._apps_container.addWidget(empty)
            return

        for st in streams:
            row_w = QWidget()
            r_lay = QHBoxLayout(row_w)
            r_lay.setContentsMargins(0, 2, 0, 2)
            r_lay.setSpacing(8)

            btn_m = QPushButton("🔇" if st.muted else "🔊")
            btn_m.setObjectName("MiniPill")
            btn_m.setCheckable(True)
            btn_m.setChecked(st.muted)
            btn_m.setFixedWidth(34)

            name_lbl = QLabel(st.name)
            name_lbl.setMinimumWidth(120)
            name_lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            if st.app_binary:
                name_lbl.setToolTip(f"Proceso: {st.app_binary}")

            sl = QSlider(Qt.Horizontal)
            sl.setRange(0, 130)
            sl.setValue(max(0, min(130, st.volume_pct)))

            pct_lbl = QLabel(f"{st.volume_pct}%")
            pct_lbl.setMinimumWidth(40)
            pct_lbl.setStyleSheet("font-family: 'JetBrains Mono', monospace; color: #94A3B8;")

            def _on_app_vol(val: int, idx: int = st.index, lbl: QLabel = pct_lbl) -> None:
                lbl.setText(f"{val}%")
                if self._start_background:
                    set_sink_input_volume(idx, val)

            def _on_app_mute(chk: bool, idx: int = st.index, b: QPushButton = btn_m) -> None:
                b.setText("🔇" if chk else "🔊")
                if self._start_background:
                    set_sink_input_mute(idx, chk)

            sl.valueChanged.connect(_on_app_vol)
            btn_m.toggled.connect(_on_app_mute)

            r_lay.addWidget(btn_m)
            r_lay.addWidget(name_lbl)
            r_lay.addWidget(sl, 1)
            r_lay.addWidget(pct_lbl)
            self._apps_container.addWidget(row_w)

    def _schedule_live_update(self) -> None:
        if self._start_background and self._config.enabled:
            self._live_timer.start()
        self._save_timer.start()

    def _flush_live_eq_params(self) -> None:
        if self._start_background and self._config.enabled:
            status = start_or_update_eq(self._config)
            self._status_lbl.setText(status.message)

    def _save_only(self) -> None:
        save_eq_config(self._config)

    def _apply_backend_full(self) -> None:
        save_eq_config(self._config)
        if not self._start_background:
            return
        status = start_or_update_eq(self._config)
        self._status_lbl.setText(status.message)
        if not status.active and self._config.enabled:
            self._syncing = True
            try:
                self._config.enabled = False
                self._btn_enable.setChecked(False)
                self._update_enable_button_style()
                self._btn_bypass.setEnabled(False)
            finally:
                self._syncing = False
        save_eq_config(self._config)
        self.refresh_audio_devices()
        self.refresh_app_streams()

    def _on_enable_toggled(self, checked: bool) -> None:
        if self._syncing:
            return
        self._config.enabled = bool(checked)
        self._update_enable_button_style()
        self._btn_bypass.setEnabled(self._config.enabled)
        self._update_curve()
        if not self._config.enabled:
            self._status_lbl.setText("Ecualizador apagado · Audio directo al dispositivo.")
        self._apply_backend_full()

    def _on_bypass_toggled(self, checked: bool) -> None:
        if self._syncing:
            return
        self._config.bypass = bool(checked)
        self._update_preamp_label()
        self._update_curve()
        self._schedule_live_update()

    def _on_output_sink_changed(self) -> None:
        if self._syncing:
            return
        self._config.output_sink = str(self._sink_combo.currentData() or "auto")
        self._apply_backend_full()

    def _on_mute_toggled(self, checked: bool) -> None:
        if self._syncing:
            return
        self._btn_mute.setText("🔇" if checked else "🔊")
        target = self._active_sink_name or (self._config.output_sink if self._config.output_sink != "auto" else "")
        if self._start_background and target:
            set_sink_mute(target, checked)

    def _on_master_volume_changed(self, val: int) -> None:
        self._vol_lbl.setText(f"{val}%")
        if self._syncing:
            return
        target = self._active_sink_name
        if self._start_background and target:
            set_sink_volume_and_balance(target, val, self._config.balance)

    def _on_balance_changed(self, val: int) -> None:
        bal = round(max(-1.0, min(1.0, val / 100.0)), 2)
        self._update_balance_label(bal)
        if self._syncing:
            return
        self._config.balance = bal
        target = self._active_sink_name
        if self._start_background and target:
            set_sink_volume_and_balance(target, self._vol_slider.value(), bal)
        self._save_timer.start()

    def _on_preamp_changed(self, val: int) -> None:
        if self._syncing:
            return
        self._config.preamp_db = round(val / 10.0, 1)
        self._update_preamp_label()
        self._update_curve()
        self._schedule_live_update()

    def _on_auto_preamp_toggled(self, checked: bool) -> None:
        if self._syncing:
            return
        self._config.auto_preamp = bool(checked)
        self._update_preamp_label()
        self._update_curve()
        self._schedule_live_update()

    def _set_single_band(self, idx: int, db_val: float) -> None:
        self._band_sliders[idx].setValue(int(round(db_val * 10.0)))

    def _on_curve_band_dragged(self, idx: int, db_val: float) -> None:
        self._set_single_band(idx, db_val)

    def _on_band_slider_changed(self, idx: int, slider_val: int) -> None:
        db = round(slider_val / 10.0, 1)
        self._band_val_labels[idx].setText(_format_db(db))
        if self._syncing:
            return
        self._config.band_gains[idx] = db
        self._config.active_preset = "Personalizado"
        self._syncing = True
        try:
            self._rebuild_preset_combo()
        finally:
            self._syncing = False
        self._preset_desc_lbl.setText("Ajuste manual · Doble clic en cualquier control para centrar.")
        self._update_preamp_label()
        self._update_curve()
        self._schedule_live_update()

    def _apply_preset_by_name(self, name: str) -> None:
        payload = EQ_PRESETS.get(name) or self._config.custom_presets.get(name)
        if not payload:
            return
        bands = [float(v) for v in payload.get("bands", [0.0] * 10)]
        pre = float(payload.get("preamp_db", 0.0))
        self._config.active_preset = name
        self._config.band_gains = bands[: len(EQ_BAND_FREQS)]
        self._config.preamp_db = pre
        self._apply_config_to_ui()
        save_eq_config(self._config)
        self._flush_live_eq_params()

    def _on_preset_selected(self) -> None:
        if self._syncing:
            return
        name = str(self._preset_combo.currentData() or "")
        if name:
            self._apply_preset_by_name(name)

    def _on_save_custom_preset(self) -> None:
        name, ok = QInputDialog.getText(
            self,
            "Guardar preset de ecualizador",
            "Nombre del preset:",
        )
        clean = name.strip()
        if not ok or not clean:
            return
        if clean in EQ_PRESETS:
            QMessageBox.information(
                self,
                "Nombre reservado",
                "Ese nombre pertenece a un preset integrado. Elige otro nombre.",
            )
            return
        self._config.custom_presets[clean] = {
            "preamp_db": self._config.preamp_db,
            "bands": list(self._config.band_gains),
        }
        self._config.active_preset = clean
        self._rebuild_preset_combo()
        save_eq_config(self._config)
        self._status_lbl.setText(f"Preset '{clean}' guardado.")

    def _on_delete_custom_preset(self) -> None:
        cur = self._config.active_preset
        if cur not in self._config.custom_presets:
            return
        del self._config.custom_presets[cur]
        self._config.active_preset = "Personalizado"
        self._rebuild_preset_combo()
        save_eq_config(self._config)
