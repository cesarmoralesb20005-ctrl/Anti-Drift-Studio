"""Clips & Moments tab widget (SteelSeries GG-style replay, voice & hotkey triggers)."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..evdev_reader import ControllerState
from ..widgets.fit_scroll import FitWidthScrollArea, FrameVideoCanvas
from .clips_config import (
    ClipsConfig,
    is_tty_hotkey,
    load_clips_config,
    save_clips_config,
)
from .hotkey_listener import GlobalHotkeyThread
from .recorder import (
    ClipRecorderController,
    detect_gsr_backend,
    install_gsr_flatpak_user,
    is_wayland_session,
    list_monitors_and_audio,
)
from .sound_fx import play_clip_saved_chime, play_voice_cue
from .voice_trigger import (
    ModelDownloadThread,
    VoiceTriggerThread,
    find_vosk_model_dir,
    list_voice_microphones,
    validate_phrase_words,
    vosk_is_available,
)

DURATION_OPTIONS: tuple[tuple[int, str], ...] = (
    (10, "10 s"),
    (15, "15 s"),
    (30, "30 s"),
    (60, "1 min"),
    (120, "2 min"),
    (300, "5 min"),
)


def _format_ms(ms: int) -> str:
    sec = max(0, ms // 1000)
    return f"{sec // 60:02d}:{sec % 60:02d}"


class ClipsTabWidget(QWidget):
    """Streamlined Instant Replay clips studio with voice, keyboard & pad triggers."""

    clip_notification = Signal(str, str)  # title, message

    def __init__(self, start_background: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._config: ClipsConfig = load_clips_config()
        self._syncing = False
        self._auto_selecting_saved = False
        self._last_ctrl_combo_fire = 0.0
        self._last_save_fire = 0.0
        self._dl_thread: ModelDownloadThread | None = None
        self._log_tail: list[str] = []

        self._recorder = ClipRecorderController(self._config, self)
        self._recorder.status_changed.connect(self._on_recorder_status)
        self._recorder.clip_saved.connect(self._on_clip_saved)
        self._recorder.save_failed.connect(self._on_clip_save_failed)
        self._recorder.log_line.connect(self._on_recorder_log)

        self._voice_thread: VoiceTriggerThread | None = None
        self._hotkey_thread: GlobalHotkeyThread | None = None
        if start_background:
            self._hotkey_thread = GlobalHotkeyThread(self._config.hotkey_combo, self)
            self._hotkey_thread.hotkey_triggered.connect(self._on_external_trigger)
            self._hotkey_thread.backend_status.connect(self._on_hotkey_status)
            self._hotkey_thread.start()

        self._build_ui()
        self._setup_local_shortcut()
        self._populate_devices()
        self._apply_config_to_ui()
        self._refresh_clips_gallery()
        self._refresh_install_hints()

        if start_background:
            if self._config.buffer_enabled:
                QTimer.singleShot(450, self._recorder.start_buffer)
            if self._config.voice_enabled:
                QTimer.singleShot(650, self._start_voice_listener)

    def shutdown(self) -> None:
        """Stop background recording, voice recognition, and hotkey listener cleanly."""
        if hasattr(self, "_player"):
            self._player.stop()
        if self._voice_thread is not None:
            self._voice_thread.stop()
            self._voice_thread = None
        if self._hotkey_thread is not None:
            self._hotkey_thread.stop()
            self._hotkey_thread = None
        self._recorder.stop_buffer(wait=True)

    def process_controller_state(self, state: ControllerState) -> None:
        """Check controller chord/paddle shortcut to trigger clip save."""
        if not self._recorder.is_running() or state.mode == "disconnected":
            return
        sc = self._config.controller_shortcut
        if sc == "none":
            return
        triggered = False
        if sc == "VIEW+RB":
            triggered = state.buttons.get("VIEW", False) and state.buttons.get(
                "RB", False
            )
        elif sc == "VIEW+LB":
            triggered = state.buttons.get("VIEW", False) and state.buttons.get(
                "LB", False
            )
        elif sc == "P4":
            triggered = bool(state.extra_buttons.get(707, False))

        if triggered:
            now = time.monotonic()
            if now - self._last_ctrl_combo_fire >= 2.0:
                self._last_ctrl_combo_fire = now
                self._trigger_save_clip("Mando")

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 6, 0, 0)
        outer.setSpacing(8)

        self._scroll = FitWidthScrollArea()
        scroll = self._scroll
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(8)

        # ── 1. Compact Replay Control & Quick Save Card ──
        top_card = QFrame()
        top_card.setObjectName("Card")
        tc_lay = QVBoxLayout(top_card)
        tc_lay.setContentsMargins(12, 10, 12, 10)
        tc_lay.setSpacing(8)

        r1 = QHBoxLayout()
        r1.setSpacing(8)
        t_lbl = QLabel("Clips Instantáneos")
        t_lbl.setObjectName("SectionTitle")
        r1.addWidget(t_lbl)

        self._rec_badge = QLabel("● Detenido")
        self._rec_badge.setObjectName("PillOff")
        r1.addWidget(self._rec_badge)
        r1.addStretch(1)

        self._btn_pick_screen = QPushButton("Elegir pantalla...")
        self._btn_pick_screen.setObjectName("MiniPill")
        self._btn_pick_screen.setToolTip(
            "Abre el selector del sistema para elegir monitor, ventana o juego"
        )
        self._btn_pick_screen.clicked.connect(self._on_pick_screen)
        r1.addWidget(self._btn_pick_screen)

        self._btn_toggle_rec = QPushButton("Activar Replay")
        self._btn_toggle_rec.setObjectName("AccentBtn")
        self._btn_toggle_rec.clicked.connect(self._on_toggle_buffer)
        r1.addWidget(self._btn_toggle_rec)
        tc_lay.addLayout(r1)

        r2 = QHBoxLayout()
        r2.setSpacing(6)
        self._btn_save_now = QPushButton("Guardar Clip (30 s)")
        self._btn_save_now.setObjectName("PrimaryBtn")
        self._btn_save_now.clicked.connect(lambda: self._trigger_save_clip("Botón"))
        r2.addWidget(self._btn_save_now)

        for secs, label in ((10, "10 s"), (30, "30 s"), (60, "1 min"), (120, "2 min")):
            b = QPushButton(label)
            b.setObjectName("MiniPill")
            b.clicked.connect(
                lambda _=False, s=secs: self._trigger_save_clip("Rápido", s)
            )
            r2.addWidget(b)

        r2.addStretch(1)
        r2.addWidget(QLabel("Duración:"))
        self._dur_combo = QComboBox()
        for secs, txt in DURATION_OPTIONS:
            self._dur_combo.addItem(txt, secs)
        self._dur_combo.currentIndexChanged.connect(self._on_video_setting_changed)
        r2.addWidget(self._dur_combo)
        tc_lay.addLayout(r2)

        r3 = QHBoxLayout()
        r3.setSpacing(8)
        self._rec_status_lbl = QLabel(
            "Pulsa 'Activar Replay' para empezar a guardar tus mejores jugadas en RAM."
        )
        self._rec_status_lbl.setWordWrap(True)
        self._rec_status_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._rec_status_lbl.setObjectName("MutedNote")
        r3.addWidget(self._rec_status_lbl, 1)

        self._btn_install_gsr = QPushButton("Instalar motor (Flatpak)")
        self._btn_install_gsr.setObjectName("AccentBtn")
        self._btn_install_gsr.setVisible(False)
        self._btn_install_gsr.clicked.connect(self._on_install_gsr)
        r3.addWidget(self._btn_install_gsr)
        tc_lay.addLayout(r3)
        lay.addWidget(top_card)

        # ── 2. Simplified Triggers Card (Voice + Hotkey + Pad) ──
        tr_card = QFrame()
        tr_card.setObjectName("Card")
        tr_lay = QVBoxLayout(tr_card)
        tr_lay.setContentsMargins(12, 9, 12, 9)
        tr_lay.setSpacing(6)

        v_row = QHBoxLayout()
        v_row.setSpacing(6)
        self._voice_chk = QCheckBox("Voz (offline):")
        self._voice_chk.toggled.connect(self._on_voice_toggled)
        v_row.addWidget(self._voice_chk)

        self._phrase_edit = QLineEdit()
        self._phrase_edit.setPlaceholderText("Ej. guarda clip, graba eso, clip")
        self._phrase_edit.returnPressed.connect(self._on_apply_voice_phrase)
        self._phrase_edit.editingFinished.connect(self._on_apply_voice_phrase)
        v_row.addWidget(self._phrase_edit, 1)

        btn_apply_phrase = QPushButton("Aplicar")
        btn_apply_phrase.setObjectName("MiniPill")
        btn_apply_phrase.clicked.connect(self._on_apply_voice_phrase)
        v_row.addWidget(btn_apply_phrase)

        self._btn_dl_model = QPushButton("Descargar voz")
        self._btn_dl_model.setObjectName("MiniPill")
        self._btn_dl_model.setVisible(False)
        self._btn_dl_model.clicked.connect(self._on_download_model)
        v_row.addWidget(self._btn_dl_model)
        tr_lay.addLayout(v_row)

        m_row = QHBoxLayout()
        m_row.setSpacing(6)
        m_row.addWidget(QLabel("Mic:"))
        self._voice_mic_combo = QComboBox()
        self._voice_mic_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self._voice_mic_combo.setMinimumContentsLength(10)
        self._voice_mic_combo.setMaximumWidth(200)
        self._voice_mic_combo.currentIndexChanged.connect(self._on_voice_mic_changed)
        m_row.addWidget(self._voice_mic_combo)

        self._mic_vu = QProgressBar()
        self._mic_vu.setRange(0, 100)
        self._mic_vu.setValue(0)
        self._mic_vu.setFormat("Nivel %p%")
        self._mic_vu.setMaximumWidth(110)
        m_row.addWidget(self._mic_vu)

        self._voice_status_lbl = QLabel("Voz desactivada")
        self._voice_status_lbl.setWordWrap(True)
        self._voice_status_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._voice_status_lbl.setObjectName("MutedNote")
        m_row.addWidget(self._voice_status_lbl, 1)
        tr_lay.addLayout(m_row)

        hk_row = QHBoxLayout()
        hk_row.setSpacing(6)
        hk_row.addWidget(QLabel("Tecla:"))
        self._hotkey_edit = QKeySequenceEdit()
        self._hotkey_edit.setMaximumSequenceLength(1)
        self._hotkey_edit.setMinimumWidth(85)
        self._hotkey_edit.keySequenceChanged.connect(self._on_hotkey_edited)
        hk_row.addWidget(self._hotkey_edit, 1)

        btn_bind_portal = QPushButton("Registrar global")
        btn_bind_portal.setObjectName("MiniPill")
        btn_bind_portal.setToolTip(
            "Autoriza el atajo global en KDE / Wayland incluso con el juego en pantalla completa"
        )
        btn_bind_portal.clicked.connect(self._on_bind_portal_shortcut)
        hk_row.addWidget(btn_bind_portal)

        hk_row.addWidget(QLabel("Mando:"))
        self._pad_sc_combo = QComboBox()
        self._pad_sc_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self._pad_sc_combo.setMinimumContentsLength(8)
        for val, lbl in (
            ("none", "Ninguno"),
            ("VIEW+RB", "View + RB"),
            ("VIEW+LB", "View + LB"),
            ("P4", "Paddle P4"),
        ):
            self._pad_sc_combo.addItem(lbl, val)
        self._pad_sc_combo.currentIndexChanged.connect(self._on_pad_shortcut_changed)
        hk_row.addWidget(self._pad_sc_combo)
        tr_lay.addLayout(hk_row)

        self._hk_status_lbl = QLabel(
            "Atajo activo en ventana · Comando externo: ~/.local/bin/antidrift-save-clip"
        )
        self._hk_status_lbl.setWordWrap(True)
        self._hk_status_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._hk_status_lbl.setObjectName("MutedNote")
        tr_lay.addWidget(self._hk_status_lbl)
        lay.addWidget(tr_card)

        # ── 3. Always-Visible Gallery + Built-in Player ──
        gal_card = QFrame()
        gal_card.setObjectName("Card")
        g_lay = QVBoxLayout(gal_card)
        g_lay.setContentsMargins(12, 9, 12, 9)
        g_lay.setSpacing(6)

        g_hdr = QHBoxLayout()
        self._gal_title = QLabel("Tus Clips Guardados (0)")
        self._gal_title.setObjectName("SectionTitle")
        g_hdr.addWidget(self._gal_title)
        g_hdr.addStretch(1)
        btn_ref = QPushButton("Actualizar")
        btn_ref.setObjectName("MiniPill")
        btn_ref.clicked.connect(self._refresh_clips_gallery)
        btn_open_dir = QPushButton("Abrir carpeta")
        btn_open_dir.setObjectName("MiniPill")
        btn_open_dir.clicked.connect(self._on_open_clips_dir)
        btn_del_clip = QPushButton("Eliminar")
        btn_del_clip.setObjectName("MiniPill")
        btn_del_clip.clicked.connect(self._on_delete_selected_clip)
        for b in (btn_ref, btn_open_dir, btn_del_clip):
            g_hdr.addWidget(b)
        g_lay.addLayout(g_hdr)

        gal_body = QHBoxLayout()
        gal_body.setSpacing(10)

        self._clips_list = QListWidget()
        self._clips_list.setMinimumHeight(145)
        self._clips_list.setMaximumHeight(195)
        self._clips_list.currentItemChanged.connect(self._on_clip_selected)
        gal_body.addWidget(self._clips_list, 1)

        player_col = QVBoxLayout()
        player_col.setSpacing(4)
        self._video_widget = FrameVideoCanvas()
        self._video_widget.setMinimumHeight(140)
        self._video_widget.setMaximumHeight(170)
        player_col.addWidget(self._video_widget, 1)

        self._audio_out = QAudioOutput(self)
        self._video_sink = QVideoSink(self)
        self._video_sink.videoFrameChanged.connect(self._video_widget.set_frame)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_out)
        self._player.setVideoSink(self._video_sink)
        self._player.positionChanged.connect(self._on_player_pos)
        self._player.durationChanged.connect(self._on_player_dur)

        ctrl_row = QHBoxLayout()
        self._btn_play = QPushButton("▶ / ⏸")
        self._btn_play.setObjectName("MiniPill")
        self._btn_play.clicked.connect(self._on_toggle_play)
        self._seek_slider = QSlider(Qt.Horizontal)
        self._seek_slider.sliderMoved.connect(self._player.setPosition)
        self._time_lbl = QLabel("00:00")
        self._time_lbl.setObjectName("MutedNote")
        ctrl_row.addWidget(self._btn_play)
        ctrl_row.addWidget(self._seek_slider, 1)
        ctrl_row.addWidget(self._time_lbl)
        player_col.addLayout(ctrl_row)

        gal_body.addLayout(player_col, 1)
        g_lay.addLayout(gal_body)
        lay.addWidget(gal_card)

        # ── 4. Collapsible Advanced Video / Audio / Folder Settings ──
        self._adv_toggle = QToolButton()
        self._adv_toggle.setText("▸ Opciones de video, audio y carpeta")
        self._adv_toggle.setCheckable(True)
        self._adv_toggle.toggled.connect(self._on_toggle_advanced)
        lay.addWidget(self._adv_toggle)

        self._adv_card = QFrame()
        self._adv_card.setObjectName("Card")
        self._adv_card.setVisible(False)
        va_lay = QGridLayout(self._adv_card)
        va_lay.setContentsMargins(12, 10, 12, 10)
        va_lay.setHorizontalSpacing(8)
        va_lay.setVerticalSpacing(6)

        va_lay.addWidget(QLabel("FPS / Calidad:"), 0, 0)
        fq_row = QHBoxLayout()
        self._fps_combo = QComboBox()
        for fps in (30, 60, 120):
            self._fps_combo.addItem(f"{fps} FPS", fps)
        self._fps_combo.currentIndexChanged.connect(self._on_video_setting_changed)
        self._qual_combo = QComboBox()
        for qk, qt in (
            ("medium", "Media"),
            ("high", "Alta"),
            ("very_high", "Muy alta"),
            ("ultra", "Ultra"),
        ):
            self._qual_combo.addItem(qt, qk)
        self._qual_combo.currentIndexChanged.connect(self._on_video_setting_changed)
        fq_row.addWidget(self._fps_combo)
        fq_row.addWidget(self._qual_combo)
        va_lay.addLayout(fq_row, 0, 1)

        va_lay.addWidget(QLabel("Captura / Códec:"), 1, 0)
        sc_row = QHBoxLayout()
        self._target_combo = QComboBox()
        self._target_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self._target_combo.setMinimumContentsLength(10)
        self._target_combo.currentIndexChanged.connect(self._on_video_setting_changed)
        self._codec_combo = QComboBox()
        for ck, ct in (("h264", "H.264"), ("hevc", "HEVC"), ("av1", "AV1")):
            self._codec_combo.addItem(ct, ck)
        self._codec_combo.currentIndexChanged.connect(self._on_video_setting_changed)
        sc_row.addWidget(self._target_combo, 1)
        sc_row.addWidget(self._codec_combo)
        va_lay.addLayout(sc_row, 1, 1)

        audio_row = QHBoxLayout()
        self._desk_audio_chk = QCheckBox("Audio del juego / PC")
        self._desk_audio_chk.toggled.connect(self._on_video_setting_changed)
        self._mic_audio_chk = QCheckBox("Incluir mi micrófono en el clip")
        self._mic_audio_chk.toggled.connect(self._on_video_setting_changed)
        self._sound_chk = QCheckBox("Sonido amigable al guardar")
        self._sound_chk.toggled.connect(self._on_sound_toggled)
        audio_row.addWidget(self._desk_audio_chk)
        audio_row.addWidget(self._mic_audio_chk)
        audio_row.addWidget(self._sound_chk)
        va_lay.addLayout(audio_row, 2, 0, 1, 2)

        va_lay.addWidget(QLabel("Salida de audio:"), 3, 0)
        self._desk_audio_src_combo = QComboBox()
        self._desk_audio_src_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self._desk_audio_src_combo.setMinimumContentsLength(12)
        self._desk_audio_src_combo.currentIndexChanged.connect(
            self._on_video_setting_changed
        )
        va_lay.addWidget(self._desk_audio_src_combo, 3, 1)

        dir_row = QHBoxLayout()
        self._dir_edit = QLineEdit()
        self._dir_edit.setReadOnly(True)
        self._dir_edit.setMinimumWidth(80)
        btn_dir = QPushButton("Cambiar carpeta...")
        btn_dir.setObjectName("MiniPill")
        btn_dir.clicked.connect(self._on_choose_output_dir)
        dir_row.addWidget(self._dir_edit, 1)
        dir_row.addWidget(btn_dir)
        va_lay.addLayout(dir_row, 4, 0, 1, 2)

        self._gsr_log_lbl = QLabel("Registro del grabador: listo")
        self._gsr_log_lbl.setWordWrap(True)
        self._gsr_log_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._gsr_log_lbl.setObjectName("MutedNote")
        va_lay.addWidget(self._gsr_log_lbl, 5, 0, 1, 2)

        lay.addWidget(self._adv_card)
        lay.addStretch(1)

        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

    def _setup_local_shortcut(self) -> None:
        self._local_shortcut = QShortcut(QKeySequence(self._config.hotkey_combo), self)
        self._local_shortcut.setContext(Qt.ApplicationShortcut)
        self._local_shortcut.activated.connect(
            lambda: self._trigger_save_clip(f"Tecla ({self._config.hotkey_combo})")
        )

    def _populate_devices(self) -> None:
        self._syncing = True
        try:
            backend = detect_gsr_backend()
            targets, audio_sources = list_monitors_and_audio(backend)
            self._target_combo.clear()
            for val, lbl in targets:
                self._target_combo.addItem(lbl, val)

            self._desk_audio_src_combo.clear()
            for val, lbl in audio_sources:
                self._desk_audio_src_combo.addItem(lbl, val)

            self._voice_mic_combo.clear()
            for val, lbl in list_voice_microphones():
                self._voice_mic_combo.addItem(lbl, val)
        finally:
            self._syncing = False

    def _apply_config_to_ui(self) -> None:
        self._syncing = True
        try:
            c = self._config
            self._select_combo_data(self._dur_combo, c.replay_seconds)
            self._select_combo_data(self._fps_combo, c.fps)
            self._select_combo_data(self._qual_combo, c.quality)
            self._select_combo_data(self._codec_combo, c.codec)
            self._select_combo_data(self._target_combo, c.capture_target)
            self._desk_audio_chk.setChecked(c.record_desktop_audio)
            self._select_combo_data(self._desk_audio_src_combo, c.desktop_audio_source)
            self._mic_audio_chk.setChecked(c.record_mic_audio)
            self._sound_chk.setChecked(c.sound_feedback)
            self._dir_edit.setText(c.output_dir)

            self._voice_chk.setChecked(c.voice_enabled)
            self._select_combo_data(self._voice_mic_combo, c.voice_mic_source)
            self._phrase_edit.setText(c.voice_phrase)
            self._hotkey_edit.setKeySequence(QKeySequence(c.hotkey_combo))
            self._select_combo_data(self._pad_sc_combo, c.controller_shortcut)
            self._update_save_button_label()
            self._update_adv_summary()
        finally:
            self._syncing = False
        self._validate_phrase_ui()

    def _select_combo_data(self, combo: QComboBox, value) -> None:
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _update_save_button_label(self) -> None:
        sec = int(self._config.replay_seconds)
        lbl = f"{sec // 60} min" if sec >= 60 and sec % 60 == 0 else f"{sec} s"
        self._btn_save_now.setText(f"Guardar Clip ({lbl})")

    def _update_adv_summary(self) -> None:
        arrow = "▾" if self._adv_toggle.isChecked() else "▸"
        audio_txt = "Audio juego" if self._config.record_desktop_audio else "Sin audio"
        if self._config.record_mic_audio:
            audio_txt += " + Mic"
        self._adv_toggle.setText(
            f"{arrow} Opciones de video, audio y carpeta ({self._config.fps} FPS · {self._config.codec.upper()} · {audio_txt})"
        )

    def _on_toggle_advanced(self, expanded: bool) -> None:
        self._adv_card.setVisible(expanded)
        self._update_adv_summary()
        if expanded and hasattr(self, "_scroll"):
            QTimer.singleShot(
                30, lambda: self._scroll.ensureWidgetVisible(self._adv_card, 0, 12)
            )

    def _refresh_install_hints(self) -> None:
        self._btn_install_gsr.setVisible(detect_gsr_backend() is None)
        missing_model = vosk_is_available() and find_vosk_model_dir() is None
        self._btn_dl_model.setVisible(missing_model)

    def _set_feedback(self, text: str, level: str = "muted") -> None:
        colors = {
            "muted": "#94A3B8",
            "ok": "#34D399",
            "warn": "#FBBF24",
            "err": "#F87171",
        }
        self._rec_status_lbl.setStyleSheet(
            f"color: {colors.get(level, '#94A3B8')}; font-weight: {'600' if level != 'muted' else '400'};"
        )
        self._rec_status_lbl.setText(text)

    def _save_and_sync_recorder(self, restart_buffer: bool = False) -> None:
        save_clips_config(self._config)
        self._recorder.update_config(self._config, restart_if_running=restart_buffer)

    def _on_toggle_buffer(self) -> None:
        if self._recorder.is_running():
            self._config.buffer_enabled = False
            self._save_and_sync_recorder(False)
            self._recorder.stop_buffer(wait=False)
        else:
            self._config.buffer_enabled = True
            self._save_and_sync_recorder(False)
            self._recorder.start_buffer()

    def _on_pick_screen(self) -> None:
        if is_wayland_session():
            self._config.buffer_enabled = True
            if self._config.capture_target not in ("auto", "portal"):
                self._syncing = True
                self._config.capture_target = "portal"
                self._select_combo_data(self._target_combo, "portal")
                self._syncing = False
            save_clips_config(self._config)
            self._recorder.update_config(self._config, restart_if_running=False)
            self._set_feedback(
                "Abriendo selector del sistema para elegir pantalla, ventana o juego...",
                "warn",
            )
            self._recorder.reset_portal_session(start_after=True)
            return

        # X11 (KDE X11, GNOME X11, XFCE, etc.): show direct monitor/window picker
        self._populate_devices()
        options: list[tuple[str, str]] = []
        for i in range(self._target_combo.count()):
            val = str(self._target_combo.itemData(i) or "")
            lbl = self._target_combo.itemText(i)
            if val and val != "portal":
                options.append((val, lbl))

        if not options:
            QMessageBox.warning(
                self,
                "Elegir pantalla (X11)",
                "No se detectaron monitores todavía. Asegúrate de tener GPU Screen Recorder instalado.",
            )
            return

        labels = [lbl for _, lbl in options]
        cur_idx = next(
            (i for i, (v, _) in enumerate(options) if v == self._config.capture_target),
            0,
        )
        chosen_lbl, ok = QInputDialog.getItem(
            self,
            "Elegir pantalla o ventana (X11)",
            "Elige qué pantalla o ventana grabar en esta sesión X11:",
            labels,
            cur_idx,
            False,
        )
        if not ok:
            return

        chosen_val = options[labels.index(chosen_lbl)][0]
        self._syncing = True
        self._config.capture_target = chosen_val
        self._config.buffer_enabled = True
        self._select_combo_data(self._target_combo, chosen_val)
        self._syncing = False
        save_clips_config(self._config)
        self._recorder.update_config(
            self._config, restart_if_running=self._recorder.is_running()
        )
        if not self._recorder.is_running():
            self._recorder.start_buffer()
        self._set_feedback(f"Grabando objetivo X11: {chosen_lbl}", "ok")

    def _trigger_save_clip(self, source: str, override_sec: int | None = None) -> None:
        now = time.monotonic()
        if now - self._last_save_fire < 0.7:
            return
        self._last_save_fire = now

        if not self._recorder.is_running():
            msg = "Primero pulsa 'Activar Replay' arriba para grabar en RAM."
            self._set_feedback(msg, "warn")
            self.clip_notification.emit("Replay apagado", msg)
            return

        sec_txt = f"{override_sec or self._config.replay_seconds} s"
        self._set_feedback(f"Guardando clip ({sec_txt}) por {source}...", "muted")
        self._recorder.save_replay(override_sec)

    def _on_recorder_log(self, line: str) -> None:
        compact = line if len(line) <= 72 else line[:69] + "..."
        self._log_tail.append(compact)
        if len(self._log_tail) > 3:
            self._log_tail = self._log_tail[-3:]
        self._gsr_log_lbl.setText("\n".join(self._log_tail))

    def _on_recorder_status(self, state: str, msg: str) -> None:
        if state == "recording":
            self._rec_badge.setText("● Grabando")
            self._rec_badge.setObjectName("PillOk")
            self._btn_toggle_rec.setText("Detener")
            self._btn_toggle_rec.setObjectName("DangerBtn")
            self._set_feedback(msg, "ok")
        elif state == "starting":
            self._rec_badge.setText("● Iniciando...")
            self._rec_badge.setObjectName("PillOff")
            self._btn_toggle_rec.setText("Detener")
            self._btn_toggle_rec.setObjectName("DangerBtn")
            self._set_feedback(msg, "warn")
        elif state == "error":
            self._rec_badge.setText("● Error")
            self._rec_badge.setObjectName("PillOff")
            self._btn_toggle_rec.setText("Activar Replay")
            self._btn_toggle_rec.setObjectName("AccentBtn")
            self._set_feedback(msg, "err")
        else:
            self._rec_badge.setText("● Detenido")
            self._rec_badge.setObjectName("PillOff")
            self._btn_toggle_rec.setText("Activar Replay")
            self._btn_toggle_rec.setObjectName("AccentBtn")
            self._set_feedback(msg, "muted")

        for w in (self._rec_badge, self._btn_toggle_rec):
            w.style().unpolish(w)
            w.style().polish(w)

    def _on_clip_saved(self, path: str) -> None:
        name = Path(path).name
        self._set_feedback(f"✓ Clip guardado: {name}", "ok")
        if self._config.sound_feedback:
            play_clip_saved_chime()
        self.clip_notification.emit("Clip guardado", name)
        self._refresh_clips_gallery(select_path=path)

    def _on_sound_toggled(self, checked: bool) -> None:
        if self._syncing:
            return
        self._config.sound_feedback = checked
        save_clips_config(self._config)

    def _on_clip_save_failed(self, err: str) -> None:
        self._set_feedback(err, "warn")
        self.clip_notification.emit("No se guardó el clip", err)

    def _on_video_setting_changed(self) -> None:
        if self._syncing:
            return
        self._config.replay_seconds = int(self._dur_combo.currentData() or 30)
        self._config.fps = int(self._fps_combo.currentData() or 60)
        self._config.quality = str(self._qual_combo.currentData() or "very_high")
        self._config.codec = str(self._codec_combo.currentData() or "h264")
        self._config.capture_target = str(self._target_combo.currentData() or "auto")
        self._config.record_desktop_audio = self._desk_audio_chk.isChecked()
        self._config.desktop_audio_source = str(
            self._desk_audio_src_combo.currentData() or "default_output"
        )
        self._config.record_mic_audio = self._mic_audio_chk.isChecked()
        self._update_save_button_label()
        self._update_adv_summary()
        self._save_and_sync_recorder(restart_buffer=True)

    def _on_choose_output_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Carpeta para guardar clips", self._config.output_dir
        )
        if chosen:
            self._config.output_dir = chosen
            self._dir_edit.setText(chosen)
            self._save_and_sync_recorder(restart_buffer=True)
            self._refresh_clips_gallery()

    def _on_install_gsr(self) -> None:
        self._btn_install_gsr.setEnabled(False)
        self._set_feedback("Instalando GPU Screen Recorder (Flatpak)...", "warn")
        QApplication.processEvents()
        ok, msg = install_gsr_flatpak_user()
        self._btn_install_gsr.setEnabled(True)
        self._refresh_install_hints()
        self._populate_devices()
        self._set_feedback(msg, "ok" if ok else "err")
        if not ok:
            QMessageBox.warning(self, "Instalación Flatpak", msg)

    # ── Voice Trigger ──

    def _validate_phrase_ui(self) -> None:
        ok, unknown = validate_phrase_words(self._phrase_edit.text())
        if not self._voice_chk.isChecked():
            if not ok:
                self._voice_status_lbl.setText(
                    f"Nota: '{', '.join(unknown)}' no está en el diccionario; te recomiendo 'guarda clip' o 'graba eso'."
                )
            else:
                self._voice_status_lbl.setText("Voz desactivada")

    def _start_voice_listener(self) -> None:
        self._stop_voice_listener()
        vt = VoiceTriggerThread(
            self._config.voice_phrase,
            self._config.voice_mic_source,
            self._config.voice_cooldown_sec,
            self,
        )
        vt.phrase_detected.connect(self._on_voice_phrase_detected)
        vt.heard_text.connect(
            lambda txt: self._voice_status_lbl.setText(f"Último oído: «{txt}»")
        )
        vt.audio_level.connect(self._mic_vu.setValue)
        vt.status_changed.connect(self._on_voice_status)
        self._voice_thread = vt
        vt.start()

    def _stop_voice_listener(self) -> None:
        if self._voice_thread is not None:
            self._voice_thread.stop()
            self._voice_thread = None
        self._mic_vu.setValue(0)

    def _on_voice_phrase_detected(self, phrase: str) -> None:
        self._voice_status_lbl.setText(f"✓ Detectado: «{phrase}»")
        if self._config.sound_feedback and self._recorder.is_running():
            play_voice_cue()
        self._trigger_save_clip(f"Voz ('{phrase}')")

    def _on_voice_toggled(self, checked: bool) -> None:
        if self._syncing:
            return
        pending_phrase = self._phrase_edit.text().strip()
        if pending_phrase and pending_phrase != self._config.voice_phrase:
            self._config.voice_phrase = pending_phrase
        self._config.voice_enabled = checked
        save_clips_config(self._config)
        if checked:
            self._start_voice_listener()
        else:
            self._stop_voice_listener()
            self._voice_status_lbl.setText("Voz desactivada")

    def _on_voice_mic_changed(self) -> None:
        if self._syncing:
            return
        self._config.voice_mic_source = str(self._voice_mic_combo.currentData() or "")
        save_clips_config(self._config)
        if self._config.voice_enabled:
            self._start_voice_listener()

    def _on_apply_voice_phrase(self) -> None:
        phrase = self._phrase_edit.text().strip() or "guarda clip"
        self._phrase_edit.setText(phrase)
        self._config.voice_phrase = phrase
        save_clips_config(self._config)
        if self._voice_thread is not None:
            self._voice_thread.set_phrase(phrase)
            self._voice_status_lbl.setText(f"Escuchando «{phrase}»")
        else:
            self._validate_phrase_ui()

    def _on_voice_status(self, state: str, msg: str) -> None:
        self._voice_status_lbl.setText(msg)
        if state == "missing_model":
            self._btn_dl_model.setVisible(True)

    def _on_download_model(self) -> None:
        if self._dl_thread is not None:
            return
        self._btn_dl_model.setEnabled(False)
        dl = ModelDownloadThread(self)
        dl.progress.connect(
            lambda p: self._voice_status_lbl.setText(f"Descargando modelo de voz... {p}%")
        )
        dl.finished_ok.connect(self._on_model_downloaded)
        dl.failed.connect(self._on_model_dl_failed)
        self._dl_thread = dl
        dl.start()

    def _on_model_downloaded(self, _path: str) -> None:
        self._dl_thread = None
        self._btn_dl_model.setVisible(False)
        self._btn_dl_model.setEnabled(True)
        self._voice_status_lbl.setText("Modelo descargado correctamente.")
        if self._config.voice_enabled:
            self._start_voice_listener()

    def _on_model_dl_failed(self, err: str) -> None:
        self._dl_thread = None
        self._btn_dl_model.setEnabled(True)
        self._voice_status_lbl.setText(err)

    # ── Keyboard / Controller Shortcuts ──

    def _on_hotkey_edited(self, seq: QKeySequence) -> None:
        if self._syncing:
            return
        txt = seq.toString().strip()
        if not txt:
            return
        if is_tty_hotkey(txt):
            self._syncing = True
            self._hotkey_edit.setKeySequence(QKeySequence(self._config.hotkey_combo))
            self._syncing = False
            self._hk_status_lbl.setText(
                "Evita Ctrl+Alt+F1..F12 en Linux (cambia de TTY). Usa por ejemplo Alt+Shift+C."
            )
            return

        self._config.hotkey_combo = txt
        save_clips_config(self._config)
        self._local_shortcut.setKey(QKeySequence(txt))
        self._hk_status_lbl.setText(
            f"Atajo global actualizado: {txt} (funciona dentro de juegos)"
        )
        if self._hotkey_thread is not None:
            self._hotkey_thread.set_combo(txt)

    def _on_bind_portal_shortcut(self) -> None:
        if not is_wayland_session():
            self._hk_status_lbl.setText(
                f"En X11 el atajo global ({self._config.hotkey_combo}) ya funciona automáticamente dentro de juegos."
            )
            return
        if self._hotkey_thread is not None:
            self._hotkey_thread.request_portal_bind()
            self._hk_status_lbl.setText(
                f"Sincronizando atajo global ({self._config.hotkey_combo}) con el sistema..."
            )

    def _on_pad_shortcut_changed(self) -> None:
        if self._syncing:
            return
        self._config.controller_shortcut = str(
            self._pad_sc_combo.currentData() or "none"
        )
        save_clips_config(self._config)

    def _on_external_trigger(self, source: str, secs: int) -> None:
        self._trigger_save_clip(source, secs if secs > 0 else None)

    def _on_hotkey_status(self, txt: str) -> None:
        self._hk_status_lbl.setText(txt)

    # ── Clips Gallery & Integrated Player ──

    def _refresh_clips_gallery(self, select_path: str | None = None) -> None:
        self._clips_list.clear()
        out_dir = Path(self._config.output_dir)
        if not out_dir.exists():
            self._gal_title.setText("Tus Clips Guardados (0)")
            return
        files = sorted(
            (
                p
                for p in out_dir.iterdir()
                if p.is_file() and p.suffix.lower() in (".mp4", ".mkv", ".mov")
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        self._gal_title.setText(f"Tus Clips Guardados ({len(files)})")
        target_row = -1
        for idx, p in enumerate(files[:40]):
            mb = p.stat().st_size / (1024.0 * 1024.0)
            item = QListWidgetItem(f"{p.name}  ({mb:.1f} MB)")
            item.setData(Qt.UserRole, str(p))
            self._clips_list.addItem(item)
            if select_path and str(p) == str(select_path):
                target_row = idx

        if target_row >= 0:
            self._auto_selecting_saved = True
            try:
                self._clips_list.setCurrentRow(target_row)
            finally:
                self._auto_selecting_saved = False

    def _sync_player_audio_output(self) -> None:
        dev = QMediaDevices.defaultAudioOutput()
        if not dev.isNull():
            self._audio_out.setDevice(dev)
        self._audio_out.setMuted(False)
        self._audio_out.setVolume(1.0)

    def _on_clip_selected(self, current: QListWidgetItem | None, _prev) -> None:
        if current is None:
            return
        path = current.data(Qt.UserRole)
        if path and os.path.exists(path):
            self._sync_player_audio_output()
            self._player.setSource(QUrl.fromLocalFile(path))
            if not self._auto_selecting_saved:
                self._player.play()
            else:
                self._player.pause()

    def _on_toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self._sync_player_audio_output()
            if self._player.source().isEmpty() and self._clips_list.count() > 0:
                self._clips_list.setCurrentRow(0)
            self._player.play()

    def _on_player_pos(self, pos: int) -> None:
        if not self._seek_slider.isSliderDown():
            self._seek_slider.setValue(pos)
        self._time_lbl.setText(_format_ms(pos))

    def _on_player_dur(self, dur: int) -> None:
        self._seek_slider.setRange(0, max(1, dur))

    def _on_open_clips_dir(self) -> None:
        out_dir = Path(self._config.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["xdg-open", str(out_dir)])

    def _on_delete_selected_clip(self) -> None:
        item = self._clips_list.currentItem()
        if item is None:
            return
        path = Path(item.data(Qt.UserRole))
        self._player.stop()
        self._player.setSource(QUrl())
        self._video_widget.clear()
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "No se pudo eliminar", str(exc))
        self._refresh_clips_gallery()
