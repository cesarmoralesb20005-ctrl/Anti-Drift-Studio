"""Friendly System Sound & 10-Band Equalizer studio tab."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import time

from PySide6.QtCore import QPointF, QRectF, QThread, QTimer, Qt, Signal
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
    _find_eq_pipewire_node_id,
    get_active_sink_info,
    list_physical_sinks,
    list_sink_inputs,
    set_sink_input_mute,
    set_sink_input_volume,
    set_sink_mute,
    set_sink_volume_and_balance,
    set_surround_orbit_azimuth,
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
    compute_stereo_dsp_params,
    effective_preamp_db,
    load_eq_config,
    save_eq_config,
)

_QUICK_PRESETS: tuple[tuple[str, str], ...] = (
    ("Plano (Neutro)", "Plano"),
    ("Gaming / Pasos FPS", "Pasos FPS"),
    ("Motores / Racing", "Motores"),
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


class _AudioEventThread(QThread):
    """Watch `pactl subscribe` for instant audio sink / app stream changes."""

    audio_changed = Signal(bool)  # include_sinks

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stop_requested = False
        self._proc: subprocess.Popen[str] | None = None

    def stop(self) -> None:
        self._stop_requested = True
        if self._proc is not None:
            try:
                self._proc.terminate()
            except OSError:
                pass
        self.wait(1200)

    def run(self) -> None:
        if not shutil.which("pactl"):
            return
        env = dict(os.environ)
        env["LC_ALL"] = "C"
        try:
            self._proc = subprocess.Popen(
                ["pactl", "subscribe"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                errors="replace",
                env=env,
            )
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                if self._stop_requested:
                    break
                low = line.lower()
                if "on sink-input" in low:
                    self.audio_changed.emit(False)
                elif "on sink" in low or "on server" in low:
                    self.audio_changed.emit(True)
        except OSError:
            pass
        finally:
            if self._proc is not None and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                except OSError:
                    pass
            self._proc = None


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
        self._app_rows: dict[int, tuple[QWidget, QPushButton, QLabel, QSlider, QLabel]] = {}
        self._app_last_drag: dict[int, float] = {}
        self._empty_apps_lbl: QLabel | None = None
        self._pending_sink_refresh = False
        self._event_thread: _AudioEventThread | None = None

        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.setInterval(45)
        self._live_timer.timeout.connect(self._flush_live_eq_params)

        self._orbit_angle_deg: float = 0.0
        self._orbit_nid: int | None = None
        self._orbit_timer = QTimer(self)
        self._orbit_timer.setInterval(120)
        self._orbit_timer.timeout.connect(self._step_surround_orbit)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(350)
        self._save_timer.timeout.connect(self._save_only)

        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.setSingleShot(True)
        self._auto_refresh_timer.setInterval(150)
        self._auto_refresh_timer.timeout.connect(self._on_auto_audio_refresh)

        self._mixer_poll_timer = QTimer(self)
        self._mixer_poll_timer.setInterval(3500)
        self._mixer_poll_timer.timeout.connect(self._on_periodic_mixer_poll)

        self._build_ui()
        self._apply_config_to_ui()

        if start_background:
            self._event_thread = _AudioEventThread(self)
            self._event_thread.audio_changed.connect(self._schedule_auto_audio_refresh)
            self._event_thread.start()
            self._mixer_poll_timer.start()
            QTimer.singleShot(250, self._initial_sync)

    def shutdown(self) -> None:
        """Flush pending equalizer timers and stop audio event watcher cleanly."""
        self._orbit_timer.stop()
        self._mixer_poll_timer.stop()
        self._auto_refresh_timer.stop()
        if self._event_thread is not None:
            self._event_thread.stop()
            self._event_thread = None
        if self._live_timer.isActive():
            self._live_timer.stop()
            self._flush_live_eq_params()
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_only()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._start_background:
            self.refresh_audio_devices()
            self.refresh_app_streams()

    def _schedule_auto_audio_refresh(self, include_sinks: bool) -> None:
        if include_sinks:
            self._pending_sink_refresh = True
        self._auto_refresh_timer.start()

    def _on_auto_audio_refresh(self) -> None:
        if not self._start_background:
            return
        if self._pending_sink_refresh:
            self._pending_sink_refresh = False
            self.refresh_audio_devices()
        else:
            self._sync_master_volume_only()
        self.refresh_app_streams()

    def _on_periodic_mixer_poll(self) -> None:
        if self._start_background and self.isVisible():
            self._sync_master_volume_only()
            self.refresh_app_streams()

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

        self._status_lbl = QLabel("Apagado")
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

        # ── Card 3: Stereo Amplification Engine (Mid/Side + 250Hz Mono + Exciter + Haas) ──
        st_card = QFrame()
        st_card.setObjectName("Card")
        s_lay = QVBoxLayout(st_card)
        s_lay.setContentsMargins(12, 10, 12, 10)
        s_lay.setSpacing(8)

        s_hdr = QHBoxLayout()
        s_hdr.setSpacing(8)
        s_title = QLabel("Amplificación Estéreo (Mid/Side + Haas)")
        s_title.setObjectName("SectionTitle")
        s_hdr.addWidget(s_title)
        s_hdr.addStretch(1)

        self._btn_surround = QPushButton("Alrededor 3D: APAGADO")
        self._btn_surround.setCheckable(True)
        self._btn_surround.setObjectName("ProtectToggleOff")
        self._btn_surround.toggled.connect(self._on_surround_enabled_toggled)
        self._btn_surround.setToolTip(
            "Hace que los instrumentos rodeen tu cabeza manteniendo la música firme en el mismo lugar."
        )
        s_hdr.addWidget(self._btn_surround)

        self._btn_stereo = QPushButton("Estéreo: APAGADO")
        self._btn_stereo.setCheckable(True)
        self._btn_stereo.setObjectName("ProtectToggleOff")
        self._btn_stereo.toggled.connect(self._on_stereo_enabled_toggled)
        self._btn_stereo.setToolTip(
            "Amplía el escenario estéreo (>250 Hz para mantener graves limpios), excitador de agudos y micro-retardo Haas."
        )
        s_hdr.addWidget(self._btn_stereo)
        s_lay.addLayout(s_hdr)

        st_sl_row = QHBoxLayout()
        st_sl_row.setSpacing(8)
        lbl_subtle = QLabel("Sutil")
        lbl_subtle.setObjectName("MutedNote")
        st_sl_row.addWidget(lbl_subtle)

        self._stereo_slider = _DoubleClickResetSlider(Qt.Horizontal)
        self._stereo_slider.setRange(0, 100)
        self._stereo_slider.setSingleStep(5)
        self._stereo_slider.setValue(50)
        self._stereo_slider.setToolTip(
            "Intensidad de ampliación estéreo (doble clic para volver a 50%)"
        )
        self._stereo_slider.valueChanged.connect(self._on_stereo_width_changed)
        self._stereo_slider.reset_requested.connect(lambda: self._stereo_slider.setValue(50))
        st_sl_row.addWidget(self._stereo_slider, 1)

        lbl_wide = QLabel("Amplio")
        lbl_wide.setObjectName("MutedNote")
        st_sl_row.addWidget(lbl_wide)

        self._stereo_val_lbl = QLabel("50% · 10 ms")
        self._stereo_val_lbl.setMinimumWidth(145)
        self._stereo_val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._stereo_val_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono', monospace; font-weight: 700; color: #10B981;"
        )
        st_sl_row.addWidget(self._stereo_val_lbl)
        s_lay.addLayout(st_sl_row)

        st_pills = QHBoxLayout()
        st_pills.setSpacing(6)
        for pct, lbl in ((25, "Sutil 25%"), (50, "Equilibrado 50%"), (80, "Amplio 80%"), (100, "Máximo 100%")):
            pb = QPushButton(lbl)
            pb.setObjectName("MiniPill")
            pb.clicked.connect(lambda _=False, v=pct: self._apply_stereo_quick_pct(v))
            st_pills.addWidget(pb)
        s_lay.addLayout(st_pills)

        sr_sl_row = QHBoxLayout()
        sr_sl_row.setSpacing(8)
        sr_lbl = QLabel("3D Alrededor:")
        sr_lbl.setObjectName("MutedNote")
        sr_sl_row.addWidget(sr_lbl)
        self._surround_slider = _DoubleClickResetSlider(Qt.Horizontal)
        self._surround_slider.setRange(0, 100)
        self._surround_slider.setSingleStep(5)
        self._surround_slider.setValue(50)
        self._surround_slider.setToolTip(
            "Intensidad del efecto 3D alrededor de la cabeza (doble clic para volver a 50%)"
        )
        self._surround_slider.valueChanged.connect(self._on_surround_intensity_changed)
        self._surround_slider.reset_requested.connect(lambda: self._surround_slider.setValue(50))
        sr_sl_row.addWidget(self._surround_slider, 1)

        self._surround_val_lbl = QLabel("50% · 3D")
        self._surround_val_lbl.setMinimumWidth(145)
        self._surround_val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._surround_val_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono', monospace; font-weight: 700; color: #38BDF8;"
        )
        sr_sl_row.addWidget(self._surround_val_lbl)
        s_lay.addLayout(sr_sl_row)
        lay.addWidget(st_card)

        # ── Card 4: Per-Application Mixer ──
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
                lbl = (
                    "Personalizado"
                    if self._config.active_preset == "Personalizado"
                    else f"Personalizado ({self._config.active_preset})"
                )
                self._preset_combo.addItem(lbl, self._config.active_preset)
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
            self._btn_stereo.setChecked(c.stereo_enabled)
            self._btn_surround.setChecked(c.surround_enabled)
            self._stereo_slider.setValue(int(round(c.stereo_width * 100.0)))
            self._surround_slider.setValue(int(round(c.surround_intensity * 100.0)))
            self._update_stereo_ui_labels()

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

    def _sync_master_volume_only(self) -> None:
        active: AudioSinkInfo | None = get_active_sink_info()
        if active is None:
            return
        self._syncing = True
        try:
            self._active_sink_name = active.name
            self._master_muted = active.muted
            if not self._vol_slider.isSliderDown():
                self._vol_slider.setValue(max(0, min(130, active.volume_pct)))
                self._vol_lbl.setText(f"{active.volume_pct}%")
            self._btn_mute.setChecked(active.muted)
            self._btn_mute.setText("🔇" if active.muted else "🔊")
        finally:
            self._syncing = False

    def refresh_audio_devices(self) -> None:
        sinks = list_physical_sinks()
        self._syncing = True
        try:
            desired = [(s.description, s.name) for s in sinks]
            current = [
                (self._sink_combo.itemText(i), self._sink_combo.itemData(i))
                for i in range(1, self._sink_combo.count())
            ]
            if self._sink_combo.count() == 0 or current != desired:
                self._sink_combo.clear()
                self._sink_combo.addItem("Automático (salida predeterminada)", "auto")
                for desc, name in desired:
                    self._sink_combo.addItem(desc, name)
            idx = self._sink_combo.findData(self._config.output_sink)
            self._sink_combo.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self._syncing = False
        self._sync_master_volume_only()

    def refresh_app_streams(self, streams_override: list[AudioAppStream] | None = None) -> None:
        streams: list[AudioAppStream] = (
            list_sink_inputs() if streams_override is None else list(streams_override)
        )
        if not streams:
            for row_w, *_ in self._app_rows.values():
                self._apps_container.removeWidget(row_w)
                row_w.deleteLater()
            self._app_rows.clear()
            self._app_last_drag.clear()
            if self._empty_apps_lbl is None:
                self._empty_apps_lbl = QLabel(
                    "No hay aplicaciones reproduciendo audio en este momento."
                )
                self._empty_apps_lbl.setObjectName("MutedNote")
                self._apps_container.addWidget(self._empty_apps_lbl)
            return

        if self._empty_apps_lbl is not None:
            self._apps_container.removeWidget(self._empty_apps_lbl)
            self._empty_apps_lbl.deleteLater()
            self._empty_apps_lbl = None

        live_ids = {st.index for st in streams}
        for stale_id in [k for k in self._app_rows if k not in live_ids]:
            row_w, *_ = self._app_rows.pop(stale_id)
            self._app_last_drag.pop(stale_id, None)
            self._apps_container.removeWidget(row_w)
            row_w.deleteLater()

        now = time.monotonic()
        for st in streams:
            if st.index in self._app_rows:
                _row_w, btn_m, name_lbl, sl, pct_lbl = self._app_rows[st.index]
                if name_lbl.text() != st.name:
                    name_lbl.setText(st.name)
                btn_m.blockSignals(True)
                try:
                    btn_m.setChecked(st.muted)
                    btn_m.setText("🔇" if st.muted else "🔊")
                finally:
                    btn_m.blockSignals(False)
                if not sl.isSliderDown() and (
                    now - self._app_last_drag.get(st.index, 0.0) >= 0.45
                ):
                    sl.blockSignals(True)
                    try:
                        sl.setValue(max(0, min(130, st.volume_pct)))
                        pct_lbl.setText(f"{st.volume_pct}%")
                    finally:
                        sl.blockSignals(False)
                continue

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
                self._app_last_drag[idx] = time.monotonic()
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
            self._app_rows[st.index] = (row_w, btn_m, name_lbl, sl, pct_lbl)

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

    def _update_stereo_ui_labels(self) -> None:
        on = self._config.stereo_enabled
        self._btn_stereo.setText("Estéreo: ACTIVO" if on else "Estéreo: APAGADO")
        self._btn_stereo.setObjectName("ProtectToggleOn" if on else "ProtectToggleOff")
        self._btn_stereo.style().unpolish(self._btn_stereo)
        self._btn_stereo.style().polish(self._btn_stereo)

        sr_on = bool(self._config.surround_enabled)
        self._btn_surround.setText("Alrededor 3D: ACTIVO" if sr_on else "Alrededor 3D: APAGADO")
        self._btn_surround.setObjectName("ProtectToggleOn" if sr_on else "ProtectToggleOff")
        self._btn_surround.style().unpolish(self._btn_surround)
        self._btn_surround.style().polish(self._btn_surround)

        sr_pct = int(round(self._config.surround_intensity * 100.0))
        self._surround_val_lbl.setText(f"{sr_pct}% · 3D")

        pct = int(round(self._config.stereo_width * 100.0))
        st = compute_stereo_dsp_params(True, self._config.stereo_width, bypass=False)
        self._stereo_val_lbl.setText(
            f"{pct}% · {int(st['haas_delay_ms'])} ms · +{st['exciter_16k_db']:.1f} dB"
        )
        self._sync_orbit_timer()

    def _sync_orbit_timer(self) -> None:
        if not self._start_background:
            return
        want = bool(
            self._config.enabled
            and self._config.surround_enabled
            and not self._config.bypass
        )
        if want and not self._orbit_timer.isActive():
            self._orbit_nid = _find_eq_pipewire_node_id()
            self._orbit_timer.start()
        elif not want and self._orbit_timer.isActive():
            self._orbit_timer.stop()
            if self._orbit_nid is not None:
                set_surround_orbit_azimuth(self._orbit_nid, 125.0, -125.0, 18.0)

    def _step_surround_orbit(self) -> None:
        if not self._start_background:
            self._orbit_timer.stop()
            return
        if self._orbit_nid is None:
            self._orbit_nid = _find_eq_pipewire_node_id()
            if self._orbit_nid is None:
                return
        self._orbit_angle_deg = (self._orbit_angle_deg + 7.5) % 360.0
        az_l = ((self._orbit_angle_deg + 180.0) % 360.0) - 180.0
        az_r = ((self._orbit_angle_deg + 360.0) % 360.0) - 180.0
        # Gentle elevation breath: +6 deg in front (0 deg), +22 deg behind (±180 deg)
        elev = 14.0 - 8.0 * math.cos(math.radians(self._orbit_angle_deg))
        if not set_surround_orbit_azimuth(self._orbit_nid, az_l, az_r, elev):
            self._orbit_nid = None

    def _on_stereo_enabled_toggled(self, checked: bool) -> None:
        if self._syncing:
            return
        self._config.stereo_enabled = bool(checked)
        self._update_stereo_ui_labels()
        if checked and not self._config.enabled:
            self._config.enabled = True
            self._syncing = True
            try:
                self._btn_enable.setChecked(True)
                self._update_enable_button_style()
                self._btn_bypass.setEnabled(True)
                self._update_curve()
            finally:
                self._syncing = False
            self._apply_backend_full()
            return
        self._schedule_live_update()

    def _on_surround_enabled_toggled(self, checked: bool) -> None:
        if self._syncing:
            return
        self._config.surround_enabled = bool(checked)
        self._update_stereo_ui_labels()
        if checked and not self._config.enabled:
            self._config.enabled = True
            self._syncing = True
            try:
                self._btn_enable.setChecked(True)
                self._update_enable_button_style()
            finally:
                self._syncing = False
            self._apply_backend_full()
            return
        self._schedule_live_update()

    def _on_surround_intensity_changed(self, val: int) -> None:
        self._config.surround_intensity = round(max(0.0, min(1.0, val / 100.0)), 2)
        self._update_stereo_ui_labels()
        if self._syncing:
            return
        self._schedule_live_update()

    def _on_stereo_width_changed(self, val: int) -> None:
        width = round(max(0.0, min(1.0, val / 100.0)), 2)
        self._config.stereo_width = width
        self._update_stereo_ui_labels()
        if self._syncing:
            return
        self._schedule_live_update()

    def _apply_stereo_quick_pct(self, pct: int) -> None:
        self._stereo_slider.setValue(int(pct))
        if not self._config.stereo_enabled:
            self._btn_stereo.setChecked(True)

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
