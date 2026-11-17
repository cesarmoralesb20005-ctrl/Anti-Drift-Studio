"""Main PySide6 window for Anti-Drift Studio (universal, simplified matte UI)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QSystemTrayIcon,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .clips.clips_tab import ClipsTabWidget
from .config import (
    DEFAULT_CONFIG_PATH,
    AntiDriftConfig,
    load_config,
    save_config,
)
from .deps import DependencyReport, check_dependencies, install_missing_dependencies
from .evdev_reader import (
    ControllerReaderThread,
    ControllerState,
    list_physical_controllers,
)
from .macros import short_extra_badge
from .profiles import (
    GameProfile,
    ProfileStore,
    detect_matching_profile,
    load_profile_store,
    save_profile_store,
)
from .service_ctrl import (
    check_sdl_ignore_env,
    check_uinput_access,
    ensure_sdl_ignore_device,
    get_service_logs,
    get_service_status,
    run_service_action,
)
from .widgets.circularity_dialog import CircularityTestDialog
from .widgets.controller_widget import ControllerWidget
from .widgets.fit_scroll import FitWidthScrollArea
from .widgets.macros_tab import MacrosTabWidget
from .widgets.stick_plot_widget import StickPlotWidget

APP_STYLESHEET = """
QMainWindow, QWidget, QDialog {
    background-color: #16171A;
    color: #EFEFE9;
    font-family: "Inter", "Noto Sans", "Segoe UI", sans-serif;
    font-size: 13px;
}
QLabel, QCheckBox {
    background-color: transparent;
}
QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1px solid #4A4F5C;
    background-color: #141519;
}
QCheckBox::indicator:checked {
    background-color: #4D8065;
    border-color: #639E7E;
}
QScrollArea {
    background-color: transparent;
}
QScrollBar:vertical {
    background: #141519;
    width: 8px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #363943;
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QFrame#Card {
    background-color: #1E2025;
    border: 1px solid #2D3038;
    border-radius: 10px;
}
QFrame#HeaderBar {
    background-color: #1A1C21;
    border: 1px solid #2D3038;
    border-radius: 10px;
}
QLabel#Title {
    font-size: 17px;
    font-weight: 700;
    color: #F4F4F0;
}
QLabel#Subtitle {
    font-size: 12px;
    color: #9A9EA8;
}
QLabel#SectionTitle {
    font-size: 14px;
    font-weight: 700;
    color: #F0F0EC;
}
QLabel#MutedNote {
    font-size: 12px;
    color: #9A9EA8;
}
QLabel#PillOk {
    background-color: #1D3327;
    color: #8FC7A7;
    border: 1px solid #3C6E52;
    border-radius: 8px;
    padding: 5px 11px;
    font-weight: 600;
    font-size: 12px;
}
QLabel#PillOff {
    background-color: #25272E;
    color: #9A9EA8;
    border: 1px solid #383B44;
    border-radius: 8px;
    padding: 5px 11px;
    font-weight: 600;
    font-size: 12px;
}
QPushButton {
    background-color: #272A32;
    color: #EFEFE9;
    border: 1px solid #383B45;
    border-radius: 7px;
    padding: 6px 12px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #30343E;
    border-color: #494E5C;
}
QPushButton:pressed {
    background-color: #202229;
}
QPushButton:disabled {
    color: #6B6F79;
    background-color: #1B1D22;
    border-color: #272A31;
}
QPushButton#PrimaryBtn {
    background-color: #4D8065;
    color: #FFFFFF;
    border: 1px solid #5D9879;
}
QPushButton#PrimaryBtn:hover {
    background-color: #5A9375;
}
QPushButton#AccentBtn {
    background-color: #4B729E;
    color: #FFFFFF;
    border: 1px solid #5F8AB9;
}
QPushButton#AccentBtn:hover {
    background-color: #5882B2;
}
QPushButton#DangerBtn {
    background-color: #5A2E2B;
    color: #F2D7D5;
    border: 1px solid #8A4A46;
}
QPushButton#DangerBtn:hover {
    background-color: #6E3935;
}
QPushButton#MiniPill {
    padding: 4px 9px;
    font-size: 11.5px;
    border-radius: 6px;
    background-color: #23252C;
}
QPushButton:checked {
    background-color: #4B729E;
    border-color: #6E98C7;
    color: #FFFFFF;
}
QPushButton#ProtectToggleOn {
    background-color: #233E30;
    color: #9ED4B6;
    border: 1px solid #4D8065;
    padding: 6px 14px;
}
QPushButton#ProtectToggleOn:hover {
    background-color: #2A4B3A;
}
QPushButton#ProtectToggleOff {
    background-color: #3A2C1D;
    color: #DFBE82;
    border: 1px solid #7D6234;
    padding: 6px 14px;
}
QToolButton {
    background: transparent;
    border: none;
    color: #9A9EA8;
    font-weight: 600;
    padding: 4px 2px;
}
QToolButton:hover {
    color: #EFEFE9;
}
QTabWidget::pane {
    border: none;
    background: transparent;
}
QTabBar::tab {
    background-color: #1B1D22;
    color: #9A9EA8;
    border: 1px solid #2D3038;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 8px 16px;
    margin-right: 4px;
    font-weight: 600;
}
QTabBar::tab:selected {
    background-color: #25282F;
    color: #F4F4F0;
    border-color: #4B729E;
}
QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit, QKeySequenceEdit, QKeySequenceEdit QLineEdit {
    background-color: #141519;
    border: 1px solid #343741;
    border-radius: 6px;
    padding: 5px 8px;
    color: #F0F0EC;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox QAbstractItemView, QListWidget, QMenu {
    background-color: #16171B;
    border: 1px solid #343741;
    border-radius: 6px;
    color: #EFEFE9;
    selection-background-color: #4B729E;
    selection-color: #FFFFFF;
    outline: none;
}
QListWidget::item {
    padding: 5px 7px;
    border-radius: 4px;
}
QListWidget::item:selected {
    background-color: #4B729E;
    color: #FFFFFF;
}
QMenu::item {
    padding: 6px 14px;
}
QMenu::item:selected {
    background-color: #4B729E;
}
QSlider::groove:horizontal {
    height: 5px;
    background: #2C2F38;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #EFEFE9;
    border: 1px solid #5F8AB9;
    width: 15px;
    height: 15px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::sub-page:horizontal {
    background: #5680AD;
    border-radius: 2px;
}
QProgressBar {
    background-color: #141519;
    border: 1px solid #2D3038;
    border-radius: 6px;
    text-align: center;
    height: 15px;
    font-size: 11px;
}
QProgressBar::chunk {
    background-color: #4D8065;
    border-radius: 5px;
}
QPlainTextEdit {
    background-color: #121316;
    border: 1px solid #2D3038;
    border-radius: 8px;
    color: #B9BDC7;
    font-family: "JetBrains Mono", "Fira Code", monospace;
    font-size: 11px;
    padding: 8px;
}
QStatusBar {
    background-color: #141519;
    border-top: 1px solid #262930;
    color: #9A9EA8;
}
"""


def _create_tray_icon() -> QIcon:
    """Paint a crisp monochrome matte gamepad badge for the system tray."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(QPen(QColor("#EFEFE9"), 3.0))
    p.setBrush(QColor("#23262E"))
    p.drawRoundedRect(QRectF(6, 16, 52, 32), 14, 14)
    p.setPen(QPen(QColor("#548C6E"), 3.5))
    p.drawPoint(QPointF(21, 32))
    p.drawEllipse(QPointF(21, 32), 5, 5)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#EFEFE9"))
    p.drawEllipse(QPointF(44, 27), 2.8, 2.8)
    p.drawEllipse(QPointF(44, 37), 2.8, 2.8)
    p.drawEllipse(QPointF(39, 32), 2.8, 2.8)
    p.drawEllipse(QPointF(49, 32), 2.8, 2.8)
    p.end()
    return QIcon(pm)


class _DepsCheckThread(QThread):
    checked = Signal(object)

    def run(self) -> None:
        self.checked.emit(check_dependencies())


class _DepsInstallThread(QThread):
    progress = Signal(str)
    finished_install = Signal(bool, str)

    def run(self) -> None:
        ok, msg = install_missing_dependencies(self.progress.emit)
        self.finished_install.emit(ok, msg)


@dataclass
class _StickSimpleControls:
    plot: StickPlotWidget
    status_badge: QLabel
    dz_pct_label: QLabel
    dz_slider: QSlider
    outer_slider: QSlider
    outer_label: QLabel
    cx_spin: QSpinBox
    cy_spin: QSpinBox


class MainWindow(QMainWindow):
    """Streamlined universal anti-drift, profile, and macro desktop studio."""

    def __init__(self, start_reader: bool = True, enable_tray: bool = True) -> None:
        super().__init__()
        self.setWindowTitle("Anti-Drift Studio")
        self.setWindowIcon(_create_tray_icon())
        self.resize(1180, 770)
        self.setMinimumSize(980, 660)
        self.setStyleSheet(APP_STYLESHEET)

        self._saved_config = load_config()
        self._config = self._saved_config.copy()
        self._profile_store: ProfileStore = load_profile_store()
        self._last_state = ControllerState()
        self._syncing_widgets = False
        self._service_active = False
        self._force_quit = False
        self._start_reader = start_reader
        self._circ_dialog: CircularityTestDialog | None = None

        self._reader: ControllerReaderThread | None = None
        if start_reader:
            self._reader = ControllerReaderThread(self._config, self)
            self._reader.state_updated.connect(self._on_controller_state)
            self._reader.calibration_progress.connect(self._on_cal_progress)
            self._reader.calibration_finished.connect(self._on_cal_finished)
            self._reader.calibration_failed.connect(self._on_cal_failed)

        self._build_ui()
        self._setup_tray(enable_tray)
        self._refresh_profile_selector()
        self._apply_config_to_widgets(self._config)
        self._refresh_dirty_state()
        self._refresh_service_status()
        self._refresh_device_selector()

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(2200)
        self._status_timer.timeout.connect(self._refresh_service_status)

        self._profile_watch_timer = QTimer(self)
        self._profile_watch_timer.setInterval(2500)
        self._profile_watch_timer.timeout.connect(self._check_auto_game_profile)

        if start_reader:
            self._reader.start()
            self._status_timer.start()
            self._profile_watch_timer.start()
            QTimer.singleShot(800, self._start_deps_check)

    def closeEvent(self, event) -> None:
        if (
            not self._force_quit
            and self._profile_store.minimize_to_tray
            and hasattr(self, "_tray")
            and self._tray is not None
            and self._tray.isVisible()
        ):
            event.ignore()
            self.hide()
            self._tray.showMessage(
                "Anti-Drift Studio",
                "Sigue protegiendo tu mando en segundo plano.",
                QSystemTrayIcon.Information,
                2000,
            )
            return

        self._status_timer.stop()
        self._profile_watch_timer.stop()
        if hasattr(self, "_clips_tab"):
            self._clips_tab.shutdown()
        if hasattr(self, "_macros_tab"):
            self._macros_tab.stop_preview()
        if self._reader is not None:
            self._reader.stop()
        if hasattr(self, "_tray") and self._tray is not None:
            self._tray.hide()
        super().closeEvent(event)

    def _quit_completely(self) -> None:
        self._force_quit = True
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _setup_tray(self, enable_tray: bool) -> None:
        self._tray: QSystemTrayIcon | None = None
        if not enable_tray or not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(_create_tray_icon(), self)
        tray.setToolTip("Anti-Drift Studio")

        menu = QMenu(self)
        act_show = menu.addAction("Mostrar / Ocultar ventana")
        act_show.triggered.connect(self._toggle_window_visibility)

        self._tray_protect_action = menu.addAction("Protección: ACTIVA")
        self._tray_protect_action.triggered.connect(self._on_toggle_protection)

        self._tray_profiles_menu = menu.addMenu("Cambiar perfil")
        menu.addSeparator()
        act_quit = menu.addAction("Salir por completo")
        act_quit.triggered.connect(self._quit_completely)

        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: self._toggle_window_visibility()
            if reason == QSystemTrayIcon.Trigger
            else None
        )
        tray.show()
        self._tray = tray

    def _toggle_window_visibility(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.showNormal()
            self.activateWindow()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 14, 14, 10)
        outer.setSpacing(12)

        outer.addWidget(self._build_header())

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([510, 650])
        outer.addWidget(splitter, 1)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Listo. Conecta cualquier mando USB o Bluetooth.")

    def _build_header(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("HeaderBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 11, 16, 11)
        lay.setSpacing(10)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("Anti-Drift Studio")
        title.setObjectName("Title")
        sub = QLabel("Calibración universal, baja latencia y perfiles por juego")
        sub.setObjectName("Subtitle")
        title_col.addWidget(title)
        title_col.addWidget(sub)
        lay.addLayout(title_col)
        lay.addStretch(1)

        self._hw_pill = QLabel("Buscando mando...")
        self._hw_pill.setObjectName("PillOff")
        lay.addWidget(self._hw_pill)

        self._protect_btn = QPushButton("Protección: ...")
        self._protect_btn.setObjectName("ProtectToggleOff")
        self._protect_btn.clicked.connect(self._on_toggle_protection)
        lay.addWidget(self._protect_btn)
        return bar

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(10)

        ctrl_card = QFrame()
        ctrl_card.setObjectName("Card")
        c_lay = QVBoxLayout(ctrl_card)
        c_lay.setContentsMargins(14, 12, 14, 12)
        c_lay.setSpacing(8)

        top_row = QHBoxLayout()
        hdr = QLabel("Tu mando en vivo")
        hdr.setObjectName("SectionTitle")
        top_row.addWidget(hdr)
        top_row.addStretch(1)

        self._hz_badge = QLabel("0 Hz")
        self._hz_badge.setStyleSheet(
            "background-color: #141519; border: 1px solid #2D3038; "
            "border-radius: 6px; padding: 3px 8px; color: #9A9EA8; font-size: 11.5px; font-weight: 600;"
        )
        self._hz_badge.setToolTip("Frecuencia real de reporte del mando en Hz")
        top_row.addWidget(self._hz_badge)

        self._sim_btn = QPushButton("Probar sin mando")
        self._sim_btn.setObjectName("MiniPill")
        self._sim_btn.setCheckable(True)
        self._sim_btn.toggled.connect(self._on_toggle_simulator)
        top_row.addWidget(self._sim_btn)
        c_lay.addLayout(top_row)

        self._controller_widget = ControllerWidget()
        self._controller_widget.simulated_state_changed.connect(
            self._update_simple_readout
        )
        c_lay.addWidget(self._controller_widget, 1)

        legend = QLabel(
            "● <span style='color:#C86A63;'>Coral</span>: entrada real &nbsp;&nbsp;·&nbsp;&nbsp;"
            "● <span style='color:#639E7E;'>Verde</span>: salida limpia al juego &nbsp;&nbsp;·&nbsp;&nbsp;"
            "▢ <span style='color:#C59B57;'>Dorado</span>: zona muerta"
        )
        legend.setAlignment(Qt.AlignCenter)
        legend.setObjectName("MutedNote")
        c_lay.addWidget(legend)

        self._active_buttons_label = QLabel(
            "Pulsa botones o mueve las palancas para comprobar tu mando."
        )
        self._active_buttons_label.setWordWrap(True)
        self._active_buttons_label.setAlignment(Qt.AlignCenter)
        self._active_buttons_label.setStyleSheet(
            "background-color: #141519; border: 1px solid #2A2D35; "
            "border-radius: 7px; padding: 8px 10px; font-weight: 600; color: #D8D8D2;"
        )
        c_lay.addWidget(self._active_buttons_label)

        lay.addWidget(ctrl_card, 1)
        return panel

    def _build_right_panel(self) -> QWidget:
        wrapper = QWidget()
        w_lay = QVBoxLayout(wrapper)
        w_lay.setContentsMargins(6, 0, 0, 0)
        w_lay.setSpacing(8)

        # Profile Switcher Bar above tabs
        prof_bar = QFrame()
        prof_bar.setObjectName("Card")
        pb_lay = QHBoxLayout(prof_bar)
        pb_lay.setContentsMargins(12, 8, 12, 8)
        pb_lay.setSpacing(8)

        pb_lay.addWidget(QLabel("Perfil:"))
        self._profile_combo = QComboBox()
        self._profile_combo.currentIndexChanged.connect(self._on_profile_selected)
        pb_lay.addWidget(self._profile_combo, 1)

        btn_save_prof = QPushButton("Guardar en perfil")
        btn_save_prof.setObjectName("MiniPill")
        btn_save_prof.clicked.connect(self._on_update_current_profile)
        pb_lay.addWidget(btn_save_prof)

        btn_new_prof = QPushButton("+ Nuevo")
        btn_new_prof.setObjectName("MiniPill")
        btn_new_prof.clicked.connect(self._on_create_profile)
        pb_lay.addWidget(btn_new_prof)

        self._btn_del_prof = QPushButton("Borrar")
        self._btn_del_prof.setObjectName("MiniPill")
        self._btn_del_prof.clicked.connect(self._on_delete_profile)
        pb_lay.addWidget(self._btn_del_prof)

        self._auto_prof_chk = QCheckBox("Auto por juego")
        self._auto_prof_chk.setToolTip(
            "Cambia automáticamente de perfil cuando abres Rocket League, Geometry Dash, etc."
        )
        self._auto_prof_chk.toggled.connect(self._on_toggle_auto_profile)
        pb_lay.addWidget(self._auto_prof_chk)

        w_lay.addWidget(prof_bar)

        tabs = QTabWidget()
        tabs.addTab(self._build_drift_tab(), "Arreglar Drift")
        self._macros_tab = MacrosTabWidget()
        self._macros_tab.macros_saved.connect(self._on_macros_saved)
        self._macros_tab.preview_state_changed.connect(self._on_macro_preview_state)
        if self._reader is not None:
            self._reader.raw_key_detected.connect(self._macros_tab.on_raw_key_detected)
        tabs.addTab(self._macros_tab, "Botones Extra y Macros")
        self._clips_tab = ClipsTabWidget(start_background=self._start_reader)
        self._clips_tab.clip_notification.connect(self._on_clip_notification)
        clips_idx = tabs.addTab(self._clips_tab, "Clips y Momentos")
        tabs.addTab(self._build_settings_tab(), "Ajustes")
        tabs.currentChanged.connect(lambda idx: prof_bar.setVisible(idx != clips_idx))
        w_lay.addWidget(tabs, 1)
        return wrapper

    def _build_drift_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 6, 0, 0)
        outer.setSpacing(10)

        scroll = FitWidthScrollArea()
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(10)

        # Card 1: Auto-Center + Circularity Diagnostic
        cal_card = QFrame()
        cal_card.setObjectName("Card")
        cal_lay = QVBoxLayout(cal_card)
        cal_lay.setContentsMargins(12, 9, 12, 9)
        cal_lay.setSpacing(6)

        cal_row = QHBoxLayout()
        cal_text_col = QVBoxLayout()
        cal_text_col.setSpacing(2)
        cal_title = QLabel("1. Centrar palancas automáticamente")
        cal_title.setObjectName("SectionTitle")
        self._cal_info = QLabel(
            "Deja el mando quieto en la mesa sin tocar las palancas y pulsa Calibrar."
        )
        self._cal_info.setWordWrap(True)
        self._cal_info.setObjectName("MutedNote")
        cal_text_col.addWidget(cal_title)
        cal_text_col.addWidget(self._cal_info)
        cal_row.addLayout(cal_text_col, 1)

        btn_circ = QPushButton("Test de bordes")
        btn_circ.setToolTip(
            "Comprueba la circularidad del stick y corrige el desgaste en bordes (Anti-Slowturn)"
        )
        btn_circ.clicked.connect(self._on_open_circularity_test)
        cal_row.addWidget(btn_circ)

        self._btn_auto_cal = QPushButton("Calibrar ahora")
        self._btn_auto_cal.setObjectName("AccentBtn")
        self._btn_auto_cal.setMinimumHeight(36)
        self._btn_auto_cal.clicked.connect(self._on_start_auto_calibration)
        cal_row.addWidget(self._btn_auto_cal)
        cal_lay.addLayout(cal_row)

        self._cal_progress = QProgressBar()
        self._cal_progress.setRange(0, 100)
        self._cal_progress.setValue(0)
        self._cal_progress.setVisible(False)
        cal_lay.addWidget(self._cal_progress)
        lay.addWidget(cal_card)

        # Card 2 header + Deadzone Shape switcher
        step2_row = QHBoxLayout()
        step2_lbl = QLabel("2. Zona muerta y forma")
        step2_lbl.setObjectName("SectionTitle")
        step2_row.addWidget(step2_lbl)
        step2_row.addStretch(1)
        step2_row.addWidget(QLabel("Forma:"))
        self._dz_mode_btns: dict[str, QPushButton] = {}
        for mode_id, mode_text in (
            ("axial", "Cruz (RL)"),
            ("radial", "Círculo"),
            ("hybrid", "Mixta"),
        ):
            b = QPushButton(mode_text)
            b.setObjectName("MiniPill")
            b.setCheckable(True)
            b.clicked.connect(
                lambda _=False, m=mode_id: self._on_dz_mode_selected(m)
            )
            self._dz_mode_btns[mode_id] = b
            step2_row.addWidget(b)
        lay.addLayout(step2_row)

        sticks_row = QHBoxLayout()
        sticks_row.setSpacing(10)
        left_card, self._ls_controls = self._create_simple_stick_card(
            "Palanca Izquierda (L)", "L"
        )
        right_card, self._rs_controls = self._create_simple_stick_card(
            "Palanca Derecha (R)", "R"
        )
        sticks_row.addWidget(left_card, 1)
        sticks_row.addWidget(right_card, 1)
        lay.addLayout(sticks_row)

        # Card 3: Latency, Response Curve, Hair-Trigger & Nintendo swap
        lat_card = QFrame()
        lat_card.setObjectName("Card")
        lat_lay = QVBoxLayout(lat_card)
        lat_lay.setContentsMargins(14, 10, 14, 10)
        lat_lay.setSpacing(7)

        lat_hdr = QHBoxLayout()
        lat_title = QLabel("3. Baja latencia y extras")
        lat_title.setObjectName("SectionTitle")
        lat_hdr.addWidget(lat_title)
        lat_hdr.addStretch(1)
        safe_badge = QLabel("100% seguro en Rocket League")
        safe_badge.setStyleSheet("color: #639E7E; font-size: 11.5px; font-weight: 600;")
        lat_hdr.addWidget(safe_badge)
        lat_lay.addLayout(lat_hdr)

        self._low_lat_chk = QCheckBox(
            "Modo Baja Latencia (sin filtrado del kernel fuzz=0, lotes y RAM fija)"
        )
        self._low_lat_chk.toggled.connect(self._on_low_latency_changed)
        lat_lay.addWidget(self._low_lat_chk)

        resp_row = QHBoxLayout()
        resp_row.setSpacing(6)
        resp_row.addWidget(QLabel("Respuesta:"))
        self._curve_btns: dict[float, QPushButton] = {}
        for label, curve_val in (
            ("Normal", 1.00),
            ("Rápida", 0.78),
            ("Instantánea", 0.62),
        ):
            b = QPushButton(label)
            b.setObjectName("MiniPill")
            b.setCheckable(True)
            b.clicked.connect(
                lambda _=False, v=curve_val: self._on_curve_selected(v)
            )
            self._curve_btns[curve_val] = b
            resp_row.addWidget(b)
        resp_row.addStretch(1)

        self._hair_trig_chk = QCheckBox("Gatillos rápidos")
        self._hair_trig_chk.toggled.connect(self._on_hair_trigger_changed)
        resp_row.addWidget(self._hair_trig_chk)

        self._swap_nintendo_chk = QCheckBox("Layout Nintendo (A↔B)")
        self._swap_nintendo_chk.setToolTip(
            "Intercambia A/B y X/Y para mandos Switch Pro / 8BitDo"
        )
        self._swap_nintendo_chk.toggled.connect(self._on_swap_nintendo_changed)
        resp_row.addWidget(self._swap_nintendo_chk)
        lat_lay.addLayout(resp_row)
        lay.addWidget(lat_card)

        # Collapsible Advanced Controls (Outer Edge Anti-Slowturn, Debounce, Rumble, Manual Centers)
        self._adv_toggle = QToolButton()
        self._adv_toggle.setText(
            "▸ Ajuste avanzado (Anti-Slowturn en bordes, anti-rebote, vibración y centros)"
        )
        self._adv_toggle.setCheckable(True)
        self._adv_toggle.toggled.connect(self._on_toggle_advanced_centers)
        lay.addWidget(self._adv_toggle)

        self._adv_card = QFrame()
        self._adv_card.setObjectName("Card")
        self._adv_card.setVisible(False)
        adv_lay = QGridLayout(self._adv_card)
        adv_lay.setContentsMargins(14, 10, 14, 10)
        adv_lay.setHorizontalSpacing(10)
        adv_lay.setVerticalSpacing(7)

        adv_lay.addWidget(QLabel("Compensar bordes L (Anti-Slowturn):"), 0, 0)
        adv_lay.addWidget(self._ls_controls.outer_slider, 0, 1, 1, 2)
        adv_lay.addWidget(self._ls_controls.outer_label, 0, 3)

        adv_lay.addWidget(QLabel("Compensar bordes R (Anti-Slowturn):"), 1, 0)
        adv_lay.addWidget(self._rs_controls.outer_slider, 1, 1, 1, 2)
        adv_lay.addWidget(self._rs_controls.outer_label, 1, 3)

        adv_lay.addWidget(QLabel("Anti-rebote botones (Debounce):"), 2, 0)
        self._debounce_slider = QSlider(Qt.Horizontal)
        self._debounce_slider.setRange(0, 45)
        self._debounce_slider.valueChanged.connect(self._on_debounce_changed)
        self._debounce_label = QLabel("0 ms")
        adv_lay.addWidget(self._debounce_slider, 2, 1, 1, 2)
        adv_lay.addWidget(self._debounce_label, 2, 3)

        adv_lay.addWidget(QLabel("Intensidad vibración (Rumble):"), 3, 0)
        self._rumble_slider = QSlider(Qt.Horizontal)
        self._rumble_slider.setRange(0, 100)
        self._rumble_slider.setSingleStep(5)
        self._rumble_slider.valueChanged.connect(self._on_rumble_changed)
        self._rumble_label = QLabel("100%")
        adv_lay.addWidget(self._rumble_slider, 3, 1, 1, 2)
        adv_lay.addWidget(self._rumble_label, 3, 3)

        adv_lay.addWidget(QLabel("Izquierda Centro X / Y:"), 4, 0)
        adv_lay.addWidget(self._ls_controls.cx_spin, 4, 1)
        adv_lay.addWidget(self._ls_controls.cy_spin, 4, 2)
        btn_cap_l = QPushButton("Capturar L")
        btn_cap_l.setObjectName("MiniPill")
        btn_cap_l.clicked.connect(lambda: self._capture_raw_center("L"))
        adv_lay.addWidget(btn_cap_l, 4, 3)

        adv_lay.addWidget(QLabel("Derecha Centro X / Y:"), 5, 0)
        adv_lay.addWidget(self._rs_controls.cx_spin, 5, 1)
        adv_lay.addWidget(self._rs_controls.cy_spin, 5, 2)
        btn_cap_r = QPushButton("Capturar R")
        btn_cap_r.setObjectName("MiniPill")
        btn_cap_r.clicked.connect(lambda: self._capture_raw_center("R"))
        adv_lay.addWidget(btn_cap_r, 5, 3)

        lay.addWidget(self._adv_card)
        lay.addStretch(1)

        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # Footer Save / Apply
        footer = QFrame()
        footer.setObjectName("Card")
        f_lay = QHBoxLayout(footer)
        f_lay.setContentsMargins(14, 10, 14, 10)
        f_lay.setSpacing(8)

        self._dirty_label = QLabel("Todo guardado")
        f_lay.addWidget(self._dirty_label)
        f_lay.addStretch(1)

        btn_reset = QPushButton("Restablecer")
        btn_reset.clicked.connect(self._on_reset_defaults)
        f_lay.addWidget(btn_reset)

        self._btn_revert = QPushButton("Deshacer")
        self._btn_revert.clicked.connect(self._on_revert)
        f_lay.addWidget(self._btn_revert)

        self._btn_apply = QPushButton("Aplicar cambios")
        self._btn_apply.setObjectName("PrimaryBtn")
        self._btn_apply.clicked.connect(self._on_save_and_apply)
        f_lay.addWidget(self._btn_apply)

        outer.addWidget(footer)
        return tab

    def _create_simple_stick_card(
        self, title: str, tag: str
    ) -> tuple[QFrame, _StickSimpleControls]:
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(11, 10, 11, 10)
        lay.setSpacing(6)

        hdr = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setObjectName("SectionTitle")
        status_badge = QLabel("Sin señal")
        status_badge.setStyleSheet("color: #9A9EA8; font-size: 11.5px;")
        hdr.addWidget(lbl)
        hdr.addStretch(1)
        hdr.addWidget(status_badge)
        lay.addLayout(hdr)

        plot_wrap = QHBoxLayout()
        plot_wrap.addStretch(1)
        plot = StickPlotWidget(tag)
        plot.setFixedSize(130, 130)
        plot.center_changed.connect(
            lambda cx, cy, t=tag: self._on_plot_center_clicked(t, cx, cy)
        )
        plot_wrap.addWidget(plot)
        plot_wrap.addStretch(1)
        lay.addLayout(plot_wrap)

        dz_pct_label = QLabel("Zona muerta: 12%")
        dz_pct_label.setStyleSheet("font-weight: 600;")
        lay.addWidget(dz_pct_label)

        dz_slider = QSlider(Qt.Horizontal)
        dz_slider.setRange(0, 40)
        dz_slider.setSingleStep(1)
        dz_slider.valueChanged.connect(
            lambda val, t=tag: self._on_dz_slider_changed(t, val)
        )
        lay.addWidget(dz_slider)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        for text, pct in (("Fino 8%", 8), ("Normal 15%", 15), ("Fuerte 24%", 24)):
            b = QPushButton(text)
            b.setObjectName("MiniPill")
            b.clicked.connect(
                lambda _=False, s=dz_slider, p=pct: s.setValue(p)
            )
            preset_row.addWidget(b)
        lay.addLayout(preset_row)

        outer_slider = QSlider(Qt.Horizontal)
        outer_slider.setRange(0, 25)
        outer_label = QLabel("0%")
        outer_slider.valueChanged.connect(
            lambda val, t=tag: self._on_outer_comp_changed(t, val)
        )

        cx_spin = QSpinBox()
        cx_spin.setRange(-28000, 28000)
        cx_spin.setSingleStep(50)
        cx_spin.valueChanged.connect(
            lambda val, t=tag: self._on_center_spin_changed(t, "x", val)
        )
        cy_spin = QSpinBox()
        cy_spin.setRange(-28000, 28000)
        cy_spin.setSingleStep(50)
        cy_spin.valueChanged.connect(
            lambda val, t=tag: self._on_center_spin_changed(t, "y", val)
        )

        return card, _StickSimpleControls(
            plot=plot,
            status_badge=status_badge,
            dz_pct_label=dz_pct_label,
            dz_slider=dz_slider,
            outer_slider=outer_slider,
            outer_label=outer_label,
            cx_spin=cx_spin,
            cy_spin=cy_spin,
        )

    def _build_settings_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 6, 0, 0)
        outer.setSpacing(8)

        scroll = FitWidthScrollArea()
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(10)

        dev_card = QFrame()
        dev_card.setObjectName("Card")
        d_lay = QVBoxLayout(dev_card)
        d_lay.setContentsMargins(14, 12, 14, 12)
        d_lay.setSpacing(9)

        d_title = QLabel("Mando, bandeja del sistema y arranque")
        d_title.setObjectName("SectionTitle")
        d_lay.addWidget(d_title)

        pick_row = QHBoxLayout()
        pick_row.addWidget(QLabel("Mando activo:"))
        self._device_combo = QComboBox()
        self._device_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self._device_combo.setMinimumContentsLength(10)
        self._device_combo.currentIndexChanged.connect(self._on_device_selected)
        pick_row.addWidget(self._device_combo, 1)
        btn_rescan = QPushButton("Buscar")
        btn_rescan.clicked.connect(self._refresh_device_selector)
        pick_row.addWidget(btn_rescan)
        d_lay.addLayout(pick_row)

        proc_row = QHBoxLayout()
        proc_row.addWidget(QLabel("Proceso del juego (perfil actual):"))
        self._prof_proc_edit = QLineEdit()
        self._prof_proc_edit.setPlaceholderText("Ej. RocketLeague o GeometryDash (opcional)")
        self._prof_proc_edit.editingFinished.connect(self._on_profile_proc_edited)
        proc_row.addWidget(self._prof_proc_edit, 1)
        d_lay.addLayout(proc_row)

        self._autostart_chk = QCheckBox(
            "Encender protección Anti-Drift automáticamente al iniciar sesión"
        )
        self._autostart_chk.toggled.connect(self._on_toggle_autostart)
        d_lay.addWidget(self._autostart_chk)

        self._tray_chk = QCheckBox(
            "Minimizar a la bandeja del sistema al cerrar la ventana"
        )
        self._tray_chk.setChecked(self._profile_store.minimize_to_tray)
        self._tray_chk.toggled.connect(self._on_toggle_minimize_to_tray)
        d_lay.addWidget(self._tray_chk)
        lay.addWidget(dev_card)

        health_card = QFrame()
        health_card.setObjectName("Card")
        h_lay = QVBoxLayout(health_card)
        h_lay.setContentsMargins(14, 12, 14, 12)
        h_lay.setSpacing(8)

        h_title = QLabel("Estado del sistema (para cualquier juego)")
        h_title.setObjectName("SectionTitle")
        h_lay.addWidget(h_title)

        self._diag_svc_label = QLabel("Servicio: ...")
        self._diag_uinput_label = QLabel("Mando virtual (/dev/uinput): ...")
        self._diag_sdl_label = QLabel("Filtro anti doble-mando en juegos: ...")
        self._diag_deps_label = QLabel("Dependencias del sistema: comprobando...")
        for lbl in (
            self._diag_svc_label,
            self._diag_uinput_label,
            self._diag_sdl_label,
            self._diag_deps_label,
        ):
            lbl.setWordWrap(True)
            lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            lbl.setStyleSheet("color: #CFD2DA; font-size: 12.5px;")
            h_lay.addWidget(lbl)

        btn_row = QHBoxLayout()
        self._btn_fix_sdl = QPushButton("Configurar filtro anti doble-mando")
        self._btn_fix_sdl.setObjectName("AccentBtn")
        self._btn_fix_sdl.clicked.connect(self._on_fix_sdl_filter)
        btn_restart = QPushButton("Reiniciar servicio")
        btn_restart.clicked.connect(lambda: self._run_svc("restart"))
        self._btn_fix_deps = QPushButton("Verificar / Instalar dependencias")
        self._btn_fix_deps.clicked.connect(self._on_install_missing_deps)
        btn_row.addWidget(self._btn_fix_sdl)
        btn_row.addWidget(btn_restart)
        btn_row.addWidget(self._btn_fix_deps)
        btn_row.addStretch(1)
        h_lay.addLayout(btn_row)
        lay.addWidget(health_card)

        self._logs_toggle = QToolButton()
        self._logs_toggle.setText("▸ Ver registro técnico (solo soporte)")
        self._logs_toggle.setCheckable(True)
        self._logs_toggle.toggled.connect(self._on_toggle_logs)
        lay.addWidget(self._logs_toggle)

        self._log_card = QFrame()
        self._log_card.setObjectName("Card")
        self._log_card.setVisible(False)
        l_lay = QVBoxLayout(self._log_card)
        l_lay.setContentsMargins(12, 10, 12, 10)
        l_lay.setSpacing(6)
        btn_logs = QPushButton("Actualizar registro")
        btn_logs.clicked.connect(self._refresh_logs)
        l_lay.addWidget(btn_logs, 0, Qt.AlignRight)
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        l_lay.addWidget(self._log_view, 1)
        lay.addWidget(self._log_card, 1)

        lay.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        return tab

    # ── Profiles & Auto-Game Switching ──

    def _current_profile(self) -> GameProfile | None:
        for p in self._profile_store.profiles:
            if p.id == self._profile_store.active_id:
                return p
        return self._profile_store.profiles[0] if self._profile_store.profiles else None

    def _refresh_profile_selector(self) -> None:
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        for p in self._profile_store.profiles:
            tag = f" [{p.auto_process}]" if p.auto_process else ""
            self._profile_combo.addItem(f"{p.name}{tag}", p.id)

        idx = self._profile_combo.findData(self._profile_store.active_id)
        if idx < 0 and self._profile_combo.count() > 0:
            idx = 0
            self._profile_store.active_id = self._profile_combo.itemData(0)
        self._profile_combo.setCurrentIndex(max(0, idx))
        self._profile_combo.blockSignals(False)

        self._auto_prof_chk.blockSignals(True)
        self._auto_prof_chk.setChecked(self._profile_store.auto_switch)
        self._auto_prof_chk.blockSignals(False)

        cur = self._current_profile()
        self._prof_proc_edit.setText(cur.auto_process if cur else "")
        self._btn_del_prof.setEnabled(len(self._profile_store.profiles) > 1)
        self._rebuild_tray_profiles_menu()

    def _rebuild_tray_profiles_menu(self) -> None:
        if self._tray is None or not hasattr(self, "_tray_profiles_menu"):
            return
        self._tray_profiles_menu.clear()
        for p in self._profile_store.profiles:
            act = QAction(p.name, self._tray_profiles_menu)
            act.setCheckable(True)
            act.setChecked(p.id == self._profile_store.active_id)
            act.triggered.connect(lambda _=False, pid=p.id: self._activate_profile_by_id(pid, apply_live=True))
            self._tray_profiles_menu.addAction(act)

    def _activate_profile_by_id(self, profile_id: str, apply_live: bool) -> None:
        target = next(
            (p for p in self._profile_store.profiles if p.id == profile_id), None
        )
        if target is None:
            return
        self._profile_store.active_id = target.id
        save_profile_store(self._profile_store)
        self._config = target.apply_to_config(self._config)
        self._refresh_profile_selector()
        self._apply_config_to_widgets(self._config)
        if apply_live:
            self._on_save_and_apply()
            self.statusBar().showMessage(f"Perfil activo: {target.name}", 3500)

    def _on_profile_selected(self, _idx: int) -> None:
        pid = self._profile_combo.currentData()
        if pid and pid != self._profile_store.active_id:
            self._activate_profile_by_id(pid, apply_live=True)

    def _on_update_current_profile(self) -> None:
        cur = self._current_profile()
        if cur is None:
            return
        updated = GameProfile.from_config(
            cur.id, cur.name, self._config, cur.auto_process
        )
        for i, p in enumerate(self._profile_store.profiles):
            if p.id == cur.id:
                self._profile_store.profiles[i] = updated
                break
        save_profile_store(self._profile_store)
        self.statusBar().showMessage(f"Perfil '{cur.name}' actualizado.", 3000)

    def _on_create_profile(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Nuevo perfil", "Nombre del perfil (ej. Rocket League Pro):"
        )
        if not ok or not name.strip():
            return
        clean_name = name.strip()
        pid = f"prof_{len(self._profile_store.profiles) + 1}_{abs(hash(clean_name)) % 9999}"
        prof = GameProfile.from_config(pid, clean_name, self._config)
        self._profile_store.profiles.append(prof)
        self._profile_store.active_id = pid
        save_profile_store(self._profile_store)
        self._refresh_profile_selector()
        self.statusBar().showMessage(f"Perfil '{clean_name}' creado.", 3000)

    def _on_delete_profile(self) -> None:
        if len(self._profile_store.profiles) <= 1:
            return
        cur = self._current_profile()
        if cur is None:
            return
        self._profile_store.profiles = [
            p for p in self._profile_store.profiles if p.id != cur.id
        ]
        self._profile_store.active_id = self._profile_store.profiles[0].id
        save_profile_store(self._profile_store)
        self._refresh_profile_selector()

    def _on_toggle_auto_profile(self, checked: bool) -> None:
        self._profile_store.auto_switch = checked
        save_profile_store(self._profile_store)

    def _on_profile_proc_edited(self) -> None:
        cur = self._current_profile()
        if cur is None:
            return
        cur.auto_process = self._prof_proc_edit.text().strip()
        save_profile_store(self._profile_store)
        self._refresh_profile_selector()

    def _check_auto_game_profile(self) -> None:
        if not self._profile_store.auto_switch:
            return
        matched = detect_matching_profile(self._profile_store.profiles)
        if matched is not None and matched.id != self._profile_store.active_id:
            self._activate_profile_by_id(matched.id, apply_live=True)

    def _on_toggle_minimize_to_tray(self, checked: bool) -> None:
        self._profile_store.minimize_to_tray = checked
        save_profile_store(self._profile_store)

    # ── Circularity & Wear Test ──

    def _on_open_circularity_test(self) -> None:
        dlg = CircularityTestDialog(
            self._config.center_lx,
            self._config.center_ly,
            self._config.center_rx,
            self._config.center_ry,
            self,
        )
        dlg.apply_outer_comp_requested.connect(self._on_apply_circularity_comp)
        self._circ_dialog = dlg
        dlg.exec()
        self._circ_dialog = None

    def _on_apply_circularity_comp(self, comp_l: float, comp_r: float) -> None:
        self._config.outer_comp_l = comp_l
        self._config.outer_comp_r = comp_r
        self._apply_config_to_widgets(self._config)
        self.statusBar().showMessage(
            f"Compensación Anti-Slowturn ajustada (L: {round(comp_l * 100)}%, R: {round(comp_r * 100)}%).",
            4000,
        )

    # ── Standard Settings & Config Sync ──

    def _on_toggle_advanced_centers(self, checked: bool) -> None:
        self._adv_toggle.setText(
            ("▾ " if checked else "▸ ")
            + "Ajuste avanzado (Anti-Slowturn en bordes, anti-rebote, vibración y centros)"
        )
        self._adv_card.setVisible(checked)

    def _on_toggle_logs(self, checked: bool) -> None:
        self._logs_toggle.setText(
            ("▾ " if checked else "▸ ") + "Ver registro técnico (solo soporte)"
        )
        self._log_card.setVisible(checked)
        if checked:
            self._refresh_logs()

    def _refresh_device_selector(self) -> None:
        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        self._device_combo.addItem("Automático (cualquier mando conectado)", "auto")
        for dev in list_physical_controllers():
            vid_txt = f" [{dev.vid_pid}]" if dev.vid_pid else ""
            self._device_combo.addItem(f"{dev.name}{vid_txt}", dev.path)

        idx = self._device_combo.findData(self._config.device_path)
        self._device_combo.setCurrentIndex(max(0, idx))
        self._device_combo.blockSignals(False)

    def _on_device_selected(self, _idx: int) -> None:
        path = self._device_combo.currentData() or "auto"
        if path != self._config.device_path:
            self._config.device_path = path
            self._sync_previews()

    def _apply_config_to_widgets(self, cfg: AntiDriftConfig) -> None:
        self._syncing_widgets = True
        try:
            self._ls_controls.cx_spin.setValue(cfg.center_lx)
            self._ls_controls.cy_spin.setValue(cfg.center_ly)
            l_pct = max(0, min(40, round(cfg.dz_l * 100)))
            self._ls_controls.dz_slider.setValue(l_pct)
            self._ls_controls.dz_pct_label.setText(f"Zona muerta: {l_pct}%")
            ol_pct = max(0, min(25, round(cfg.outer_comp_l * 100)))
            self._ls_controls.outer_slider.setValue(ol_pct)
            self._ls_controls.outer_label.setText(f"{ol_pct}%")

            self._rs_controls.cx_spin.setValue(cfg.center_rx)
            self._rs_controls.cy_spin.setValue(cfg.center_ry)
            r_pct = max(0, min(40, round(cfg.dz_r * 100)))
            self._rs_controls.dz_slider.setValue(r_pct)
            self._rs_controls.dz_pct_label.setText(f"Zona muerta: {r_pct}%")
            or_pct = max(0, min(25, round(cfg.outer_comp_r * 100)))
            self._rs_controls.outer_slider.setValue(or_pct)
            self._rs_controls.outer_label.setText(f"{or_pct}%")

            for mode_id, btn in self._dz_mode_btns.items():
                btn.setChecked(mode_id == cfg.dz_mode)

            self._low_lat_chk.setChecked(cfg.low_latency)
            self._hair_trig_chk.setChecked(cfg.hair_trigger)
            self._swap_nintendo_chk.setChecked(cfg.swap_nintendo)
            self._debounce_slider.setValue(max(0, min(45, cfg.debounce_ms)))
            self._debounce_label.setText(f"{cfg.debounce_ms} ms")
            self._rumble_slider.setValue(max(0, min(100, cfg.rumble_gain)))
            self._rumble_label.setText(f"{cfg.rumble_gain}%")

            closest_curve = min(
                self._curve_btns.keys(), key=lambda v: abs(v - cfg.response_curve)
            )
            for v, btn in self._curve_btns.items():
                btn.setChecked(v == closest_curve)
        finally:
            self._syncing_widgets = False

        self._sync_previews()

    def _sync_previews(self) -> None:
        self._ls_controls.plot.set_params(
            self._config.center_lx,
            self._config.center_ly,
            self._config.dz_l,
            self._config.dz_mode,
            self._config.outer_comp_l,
        )
        self._rs_controls.plot.set_params(
            self._config.center_rx,
            self._config.center_ry,
            self._config.dz_r,
            self._config.dz_mode,
            self._config.outer_comp_r,
        )
        self._controller_widget.set_config_preview(self._config)
        if self._reader is not None:
            self._reader.update_config(self._config)
        self._refresh_dirty_state()

    def _on_dz_mode_selected(self, mode_id: str) -> None:
        if self._syncing_widgets:
            return
        self._config.dz_mode = mode_id
        for m, btn in self._dz_mode_btns.items():
            btn.blockSignals(True)
            btn.setChecked(m == mode_id)
            btn.blockSignals(False)
        self._sync_previews()

    def _on_dz_slider_changed(self, tag: str, pct: int) -> None:
        if self._syncing_widgets:
            return
        dz = round(pct / 100.0, 2)
        if tag == "L":
            self._config.dz_l = dz
            self._ls_controls.dz_pct_label.setText(f"Zona muerta: {pct}%")
        else:
            self._config.dz_r = dz
            self._rs_controls.dz_pct_label.setText(f"Zona muerta: {pct}%")
        self._sync_previews()

    def _on_outer_comp_changed(self, tag: str, pct: int) -> None:
        if self._syncing_widgets:
            return
        val = round(pct / 100.0, 2)
        if tag == "L":
            self._config.outer_comp_l = val
            self._ls_controls.outer_label.setText(f"{pct}%")
        else:
            self._config.outer_comp_r = val
            self._rs_controls.outer_label.setText(f"{pct}%")
        self._sync_previews()

    def _on_debounce_changed(self, ms: int) -> None:
        if self._syncing_widgets:
            return
        self._config.debounce_ms = ms
        self._debounce_label.setText(f"{ms} ms")
        self._sync_previews()

    def _on_rumble_changed(self, gain: int) -> None:
        if self._syncing_widgets:
            return
        self._config.rumble_gain = gain
        self._rumble_label.setText(f"{gain}%")
        self._sync_previews()

    def _on_low_latency_changed(self, checked: bool) -> None:
        if self._syncing_widgets:
            return
        self._config.low_latency = checked
        self._sync_previews()

    def _on_hair_trigger_changed(self, checked: bool) -> None:
        if self._syncing_widgets:
            return
        self._config.hair_trigger = checked
        self._sync_previews()

    def _on_swap_nintendo_changed(self, checked: bool) -> None:
        if self._syncing_widgets:
            return
        self._config.swap_nintendo = checked
        self._sync_previews()

    def _on_curve_selected(self, curve_val: float) -> None:
        if self._syncing_widgets:
            return
        self._config.response_curve = curve_val
        for v, btn in self._curve_btns.items():
            btn.blockSignals(True)
            btn.setChecked(v == curve_val)
            btn.blockSignals(False)
        self._sync_previews()

    def _on_center_spin_changed(self, tag: str, axis: str, val: int) -> None:
        if self._syncing_widgets:
            return
        if tag == "L":
            if axis == "x":
                self._config.center_lx = val
            else:
                self._config.center_ly = val
        else:
            if axis == "x":
                self._config.center_rx = val
            else:
                self._config.center_ry = val
        self._sync_previews()

    def _on_plot_center_clicked(self, tag: str, cx: int, cy: int) -> None:
        if tag == "L":
            self._config.center_lx = cx
            self._config.center_ly = cy
        else:
            self._config.center_rx = cx
            self._config.center_ry = cy
        self._apply_config_to_widgets(self._config)

    def _capture_raw_center(self, target: str) -> None:
        if self._last_state.mode == "disconnected":
            QMessageBox.information(
                self,
                "Sin mando",
                "Conecta tu mando primero para poder capturar su centro actual.",
            )
            return
        if target in ("L", "BOTH"):
            self._config.center_lx = max(-28000, min(28000, self._last_state.raw_lx))
            self._config.center_ly = max(-28000, min(28000, self._last_state.raw_ly))
        if target in ("R", "BOTH"):
            self._config.center_rx = max(-28000, min(28000, self._last_state.raw_rx))
            self._config.center_ry = max(-28000, min(28000, self._last_state.raw_ry))
        self._apply_config_to_widgets(self._config)

    def _refresh_dirty_state(self) -> None:
        dirty = self._config != self._saved_config
        if dirty:
            self._dirty_label.setText("Cambios sin aplicar")
            self._dirty_label.setStyleSheet(
                "color: #C59B57; font-weight: 600; font-size: 12px;"
            )
        else:
            self._dirty_label.setText("Todo guardado")
            self._dirty_label.setStyleSheet("color: #639E7E; font-size: 12px;")
        self._btn_revert.setEnabled(dirty)

    def _on_save_and_apply(self) -> None:
        save_config(self._config)
        self._saved_config = self._config.copy()
        self._refresh_dirty_state()

        status = get_service_status()
        if status.active:
            ok, msg = run_service_action("reload_hup")
            if not ok:
                ok, msg = run_service_action("restart")
        else:
            ok, msg = run_service_action("start")

        self._refresh_service_status()
        if ok:
            self.statusBar().showMessage("Cambios guardados y aplicados en vivo.", 4000)
        else:
            QMessageBox.warning(
                self,
                "Configuración guardada",
                f"Se guardó {DEFAULT_CONFIG_PATH}, pero el servicio reportó:\n{msg}",
            )

    def _on_revert(self) -> None:
        self._config = self._saved_config.copy()
        self._apply_config_to_widgets(self._config)
        self.statusBar().showMessage("Cambios deshechos.", 2500)

    def _on_reset_defaults(self) -> None:
        self._config = AntiDriftConfig()
        self._apply_config_to_widgets(self._config)
        self._refresh_device_selector()

    def _on_start_auto_calibration(self) -> None:
        if self._reader is None:
            return
        self._btn_auto_cal.setEnabled(False)
        self._cal_progress.setValue(0)
        self._cal_progress.setVisible(True)
        self._cal_info.setText(
            "Midiendo reposo (1.5 s)... No toques las palancas."
        )
        self._reader.start_calibration(1.5)

    def _on_cal_progress(self, pct: int) -> None:
        self._cal_progress.setValue(pct)

    def _on_cal_finished(
        self,
        avg_lx: int,
        avg_ly: int,
        avg_rx: int,
        avg_ry: int,
        jitter_l: int,
        jitter_r: int,
    ) -> None:
        self._btn_auto_cal.setEnabled(True)
        self._cal_progress.setVisible(False)
        self._config.center_lx = max(-28000, min(28000, avg_lx))
        self._config.center_ly = max(-28000, min(28000, avg_ly))
        self._config.center_rx = max(-28000, min(28000, avg_rx))
        self._config.center_ry = max(-28000, min(28000, avg_ry))
        self._apply_config_to_widgets(self._config)
        self._cal_info.setText(
            "Listo, palancas centradas. Ahora pulsa 'Aplicar cambios' abajo."
        )
        self.statusBar().showMessage(
            f"Calibrado (ruido L={jitter_l}, R={jitter_r}). Pulsa Aplicar cambios.",
            4500,
        )

    def _on_cal_failed(self, reason: str) -> None:
        self._btn_auto_cal.setEnabled(True)
        self._cal_progress.setVisible(False)
        self._cal_info.setText(reason)
        self.statusBar().showMessage(reason, 4000)

    def _on_toggle_simulator(self, enabled: bool) -> None:
        self._controller_widget.set_simulator_enabled(enabled)
        if enabled:
            self._active_buttons_label.setText(
                "Modo prueba activo: haz clic o arrastra sobre el mando."
            )
        else:
            self._on_controller_state(self._last_state)

    def _on_controller_state(self, state: ControllerState) -> None:
        self._last_state = state

        if state.mode in ("virtual", "direct"):
            pad_name = state.physical_name or "Mando conectado"
            if len(pad_name) > 26:
                pad_name = pad_name[:23] + "..."
            self._hw_pill.setText(f"Conectado: {pad_name}")
            self._hw_pill.setObjectName("PillOk")
            self._hz_badge.setText(f"{round(state.poll_hz)} Hz")
        else:
            self._hw_pill.setText("Sin mando conectado")
            self._hw_pill.setObjectName("PillOff")
            self._hz_badge.setText("0 Hz")

        self._hw_pill.style().unpolish(self._hw_pill)
        self._hw_pill.style().polish(self._hw_pill)

        if not self._sim_btn.isChecked():
            self._controller_widget.set_controller_state(state)
            self._update_simple_readout(state)
        if self._circ_dialog is not None:
            self._circ_dialog.feed_state(state)
        if state.supported_extra_keys:
            self._macros_tab.update_detected_extra_keys(state.supported_extra_keys)
        if hasattr(self, "_clips_tab"):
            self._clips_tab.process_controller_state(state)

    def _on_clip_notification(self, title: str, msg: str) -> None:
        self.statusBar().showMessage(f"{title}: {msg}", 4500)
        if self._tray is not None and self._tray.isVisible():
            self._tray.showMessage(title, msg, QSystemTrayIcon.Information, 2500)

    def _on_macro_preview_state(self, state: ControllerState | None) -> None:
        self._controller_widget.set_macro_preview_state(state)
        if state is not None:
            self._update_simple_readout(state)
        elif not self._sim_btn.isChecked():
            self._update_simple_readout(self._last_state)

    def _on_macros_saved(self, _ok: bool, summary: str) -> None:
        status = get_service_status()
        if status.active:
            ok, msg = run_service_action("reload_hup")
            if not ok:
                ok, msg = run_service_action("restart")
        else:
            ok, msg = run_service_action("start")

        self._refresh_service_status()
        if ok:
            self.statusBar().showMessage("Macros guardadas y aplicadas en vivo.", 4000)
        else:
            QMessageBox.warning(
                self,
                "Macros guardadas",
                f"{summary}\nPero el servicio reportó:\n{msg}",
            )

    def _update_simple_readout(self, state: ControllerState) -> None:
        connected = state.mode != "disconnected"
        self._ls_controls.plot.set_live_values(
            state.raw_lx, state.raw_ly, state.out_lx, state.out_ly, connected
        )
        self._rs_controls.plot.set_live_values(
            state.raw_rx, state.raw_ry, state.out_rx, state.out_ry, connected
        )

        for ctrl, ox, oy in (
            (self._ls_controls, state.out_lx, state.out_ly),
            (self._rs_controls, state.out_rx, state.out_ry),
        ):
            if not connected:
                ctrl.status_badge.setText("Sin señal")
                ctrl.status_badge.setStyleSheet("color: #9A9EA8; font-size: 11.5px;")
            elif abs(ox) < 250 and abs(oy) < 250:
                ctrl.status_badge.setText("✓ Estable en centro")
                ctrl.status_badge.setStyleSheet(
                    "color: #639E7E; font-weight: 600; font-size: 11.5px;"
                )
            else:
                ctrl.status_badge.setText("↗ En movimiento")
                ctrl.status_badge.setStyleSheet(
                    "color: #C59B57; font-weight: 600; font-size: 11.5px;"
                )

        lt_pct = round((max(0, state.lt) / 1023.0) * 100.0)
        rt_pct = round((max(0, state.rt) / 1023.0) * 100.0)

        pressed = [name for name, is_down in state.buttons.items() if is_down]
        for code, is_down in sorted(state.extra_buttons.items()):
            if is_down:
                pressed.append(short_extra_badge(code))
        if state.hat_y < 0:
            pressed.append("Cruceta ▲")
        elif state.hat_y > 0:
            pressed.append("Cruceta ▼")
        if state.hat_x < 0:
            pressed.append("Cruceta ◀")
        elif state.hat_x > 0:
            pressed.append("Cruceta ▶")
        if lt_pct >= 5:
            pressed.append(f"LT {lt_pct}%")
        if rt_pct >= 5:
            pressed.append(f"RT {rt_pct}%")

        if pressed:
            self._active_buttons_label.setText("Pulsando: " + "  ·  ".join(pressed))
        elif connected:
            self._active_buttons_label.setText(
                "Listo · Pulsa botones o mueve las palancas para probar."
            )
        else:
            self._active_buttons_label.setText(
                "Conecta tu mando por USB o Bluetooth (o activa 'Probar sin mando')."
            )

    def _on_toggle_protection(self) -> None:
        action = "stop" if self._service_active else "start"
        self._run_svc(action)

    def _on_fix_sdl_filter(self) -> None:
        vid_pid = self._last_state.physical_vid_pid
        ok, msg = ensure_sdl_ignore_device(vid_pid)
        self._refresh_service_status()
        if ok:
            QMessageBox.information(self, "Filtro actualizado", msg)
        else:
            QMessageBox.warning(self, "No se pudo configurar", msg)

    def _refresh_service_status(self) -> None:
        st = get_service_status()
        self._service_active = st.active
        if st.active:
            self._protect_btn.setText("Protección: ACTIVA")
            self._protect_btn.setObjectName("ProtectToggleOn")
        else:
            self._protect_btn.setText("Protección: PAUSADA")
            self._protect_btn.setObjectName("ProtectToggleOff")
        self._protect_btn.style().unpolish(self._protect_btn)
        self._protect_btn.style().polish(self._protect_btn)

        if self._tray is not None and hasattr(self, "_tray_protect_action"):
            self._tray_protect_action.setText(
                "Protección: ACTIVA (clic para pausar)"
                if st.active
                else "Protección: PAUSADA (clic para activar)"
            )

        self._diag_svc_label.setText(
            f"Servicio Anti-Drift: {'Activo' if st.active else 'Pausado'} ({st.summary})"
        )

        u_ok, u_msg = check_uinput_access()
        self._diag_uinput_label.setText(
            f"Permisos del sistema: {'✓ ' if u_ok else '⚠ '}{u_msg}"
        )

        sdl_ok, sdl_msg = check_sdl_ignore_env(self._last_state.physical_vid_pid)
        self._diag_sdl_label.setText(
            f"Evitar doble mando en juegos: {'✓ Activo' if sdl_ok else '⚠ ' + sdl_msg}"
        )
        self._btn_fix_sdl.setEnabled(self._last_state.physical_vid_pid is not None)

        self._autostart_chk.blockSignals(True)
        self._autostart_chk.setChecked(st.enabled)
        self._autostart_chk.blockSignals(False)

    def _run_svc(self, action: str) -> None:
        ok, msg = run_service_action(action)
        self._refresh_service_status()
        self.statusBar().showMessage(msg, 3500)
        if not ok:
            QMessageBox.warning(self, "Servicio Anti-Drift", msg)

    def _on_toggle_autostart(self, checked: bool) -> None:
        self._run_svc("enable" if checked else "disable")

    def _refresh_logs(self) -> None:
        self._log_view.setPlainText(get_service_logs())

    def _start_deps_check(self) -> None:
        if getattr(self, "_deps_chk_thread", None) is not None:
            return
        th = _DepsCheckThread(self)
        th.checked.connect(self._on_deps_checked)
        self._deps_chk_thread = th
        th.start()

    def _on_deps_checked(self, report: DependencyReport) -> None:
        self._deps_chk_thread = None
        if report.all_ok:
            self._diag_deps_label.setText(
                f"Dependencias ({report.distro.pretty_name}): ✓ Todo instalado"
            )
            self._btn_fix_deps.setText("Verificar dependencias")
        else:
            summary = " · ".join(report.summary_lines()[1:])
            self._diag_deps_label.setText(
                f"Dependencias ({report.distro.pretty_name}): ⚠ {summary}"
            )
            self._btn_fix_deps.setText("Instalar dependencias faltantes")
            self._btn_fix_deps.setObjectName("AccentBtn")
            self._btn_fix_deps.style().unpolish(self._btn_fix_deps)
            self._btn_fix_deps.style().polish(self._btn_fix_deps)
            self.statusBar().showMessage(
                f"Faltan dependencias ({report.distro.pkg_manager}). Ve a Ajustes o pulsa Instalar.",
                6000,
            )

    def _on_install_missing_deps(self) -> None:
        if getattr(self, "_deps_inst_thread", None) is not None:
            return
        self._btn_fix_deps.setEnabled(False)
        self._diag_deps_label.setText("Verificando / instalando dependencias...")
        th = _DepsInstallThread(self)
        th.progress.connect(lambda m: self._diag_deps_label.setText(m))
        th.finished_install.connect(self._on_deps_installed)
        self._deps_inst_thread = th
        th.start()

    def _on_deps_installed(self, ok: bool, msg: str) -> None:
        self._deps_inst_thread = None
        self._btn_fix_deps.setEnabled(True)
        self._refresh_service_status()
        self._start_deps_check()
        if hasattr(self, "_clips_tab"):
            self._clips_tab._refresh_install_hints()
            self._clips_tab._populate_devices()
        if ok:
            self.statusBar().showMessage(msg, 5000)
        else:
            QMessageBox.warning(self, "Dependencias", msg)
