"""Main PySide6 window for Anti-Drift Studio (universal, simplified matte UI)."""

from __future__ import annotations

from dataclasses import dataclass
import os

from PySide6.QtCore import QByteArray, QPointF, QRectF, QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
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
    QStackedWidget,
    QStatusBar,
    QSystemTrayIcon,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .audio.eq_tab import EqTabWidget
from .clips.clips_tab import ClipsTabWidget
from .clips.desktop_notifier import DesktopClipNotifier
from .config import (
    AXIS_MAX,
    DEFAULT_CONFIG_PATH,
    DEFAULT_VIRTUAL_NAME,
    AntiDriftConfig,
    load_config,
    load_device_config,
    sanitize_virtual_name,
    save_config,
    save_device_config,
)
from .deps import (
    DependencyReport,
    OptionalDepStatus,
    check_dependencies,
    check_optional_dependencies,
    install_missing_dependencies,
    install_optional_dependency,
)
from .clips.sound_fx import (
    play_battery_charging_chime,
    play_battery_full_chime,
    play_battery_low_chime,
    play_battery_unplugged_chime,
)
from .mangohud.mh_tab import MangoHudTabWidget
from .evdev_reader import (
    ControllerReaderThread,
    ControllerState,
    list_physical_controllers,
    list_sim_aux_devices,
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
    is_app_autostart_enabled,
    run_service_action,
    set_app_autostart,
)
from .updater import (
    UpdateCheckResult,
    apply_update,
    check_for_updates,
    get_local_commit_sha,
    read_local_app_version,
    resolve_github_target,
    restart_application,
)
from .widgets.circularity_dialog import CircularityTestDialog
from .widgets.controller_widget import ControllerWidget
from .widgets.fit_scroll import B7_MARK_SVG, B7MarkWidget, FitWidthScrollArea, GridRootWidget
from .widgets.macros_tab import MacrosTabWidget
from .widgets.stick_plot_widget import StickPlotWidget

APP_STYLESHEET = """
QMainWindow, QDialog {
    background-color: #090B0E;
    color: #F8FAFC;
    font-family: "Inter", "Noto Sans", "Segoe UI", sans-serif;
    font-size: 13px;
}
QWidget#PageCanvas {
    background-color: transparent;
}
QLabel, QCheckBox {
    background-color: transparent;
    color: #E2E8F0;
}
QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border-radius: 3px;
    border: 1px solid #334155;
    background-color: #07090C;
}
QCheckBox::indicator:checked {
    background-color: #059669;
    border-color: #10B981;
}
QScrollArea, QScrollArea > QWidget > QWidget {
    background-color: transparent;
}
QScrollBar:vertical {
    background: #07090C;
    width: 7px;
    border-radius: 3px;
}
QScrollBar::handle:vertical {
    background: #1E293B;
    border-radius: 3px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover {
    background: #059669;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QFrame#Card {
    background-color: #0F1318;
    border: 1px solid #1E2630;
    border-radius: 8px;
}
QFrame#HeaderBar {
    background-color: #0C1014;
    border: 1px solid #1E2630;
    border-bottom: 2px solid #059669;
    border-radius: 8px;
}
QLabel#Title {
    font-size: 17px;
    font-weight: 800;
    color: #F8FAFC;
    letter-spacing: -0.3px;
}
QLabel#BrandTag {
    font-family: "JetBrains Mono", "Fira Code", monospace;
    font-size: 10px;
    font-weight: 700;
    color: #10B981;
    letter-spacing: 1.4px;
}
QLabel#Subtitle {
    font-size: 12px;
    color: #94A3B8;
}
QLabel#SectionTitle {
    font-size: 13.5px;
    font-weight: 700;
    color: #F8FAFC;
}
QLabel#MutedNote {
    font-size: 12px;
    color: #94A3B8;
}
QLabel#PillOk {
    background-color: #0B261E;
    color: #34D399;
    border: 1px solid #059669;
    border-radius: 6px;
    padding: 5px 11px;
    font-weight: 600;
    font-size: 12px;
}
QLabel#PillOff {
    background-color: #11161C;
    color: #94A3B8;
    border: 1px solid #242E3A;
    border-radius: 6px;
    padding: 5px 11px;
    font-weight: 600;
    font-size: 12px;
}
QPushButton {
    background-color: #141920;
    color: #E2E8F0;
    border: 1px solid #26313E;
    border-radius: 6px;
    padding: 6px 12px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #1B222C;
    border-color: #3B495B;
    color: #F8FAFC;
}
QPushButton:pressed {
    background-color: #0D1116;
}
QPushButton:disabled {
    color: #475569;
    background-color: #0B0E12;
    border-color: #182028;
}
QPushButton#PrimaryBtn {
    background-color: #059669;
    color: #FFFFFF;
    border: 1px solid #10B981;
}
QPushButton#PrimaryBtn:hover {
    background-color: #047857;
}
QPushButton#AccentBtn {
    background-color: #0C261E;
    color: #34D399;
    border: 1px solid #059669;
}
QPushButton#AccentBtn:hover {
    background-color: #059669;
    color: #FFFFFF;
}
QPushButton#DangerBtn {
    background-color: #2A1215;
    color: #FCA5A5;
    border: 1px solid #991B1B;
}
QPushButton#DangerBtn:hover {
    background-color: #3B181C;
}
QPushButton#MiniPill {
    padding: 4px 9px;
    font-size: 11.5px;
    border-radius: 5px;
    background-color: #12171E;
    border: 1px solid #222B36;
}
QPushButton#MiniPill:hover {
    border-color: #059669;
    color: #F8FAFC;
}
QPushButton:checked {
    background-color: #059669;
    border-color: #10B981;
    color: #FFFFFF;
}
QPushButton#ProtectToggleOn {
    background-color: #0B261E;
    color: #34D399;
    border: 1px solid #059669;
    padding: 6px 14px;
}
QPushButton#ProtectToggleOn:hover {
    background-color: #059669;
    color: #FFFFFF;
}
QPushButton#ProtectToggleOff {
    background-color: #261B0D;
    color: #FBBF24;
    border: 1px solid #92400E;
    padding: 6px 14px;
}
QToolButton {
    background: transparent;
    border: none;
    color: #94A3B8;
    font-weight: 600;
    padding: 4px 2px;
}
QToolButton:hover {
    color: #10B981;
}
QTabWidget::pane {
    border: none;
    background: transparent;
}
QTabBar::tab {
    background-color: #0D1116;
    color: #94A3B8;
    border: 1px solid #1E2630;
    border-radius: 6px;
    padding: 7px 11px;
    margin-right: 4px;
    font-weight: 600;
}
QTabBar::tab:hover {
    color: #F8FAFC;
    border-color: #334155;
}
QTabBar::tab:selected {
    background-color: #0B261E;
    color: #34D399;
    border: 1px solid #059669;
}
QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit, QKeySequenceEdit, QKeySequenceEdit QLineEdit {
    background-color: #07090C;
    border: 1px solid #242E3A;
    border-radius: 6px;
    padding: 5px 8px;
    color: #F8FAFC;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QKeySequenceEdit:focus {
    border-color: #059669;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox QAbstractItemView, QListWidget, QMenu {
    background-color: #0B0E12;
    border: 1px solid #1E2630;
    border-radius: 6px;
    color: #E2E8F0;
    selection-background-color: #059669;
    selection-color: #FFFFFF;
    outline: none;
}
QListWidget::item {
    padding: 5px 7px;
    border-radius: 4px;
}
QListWidget::item:selected {
    background-color: #059669;
    color: #FFFFFF;
}
QMenu::item {
    padding: 6px 14px;
}
QMenu::item:selected {
    background-color: #059669;
}
QSlider::groove:horizontal {
    height: 5px;
    background: #1E2630;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #F8FAFC;
    border: 2px solid #059669;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::sub-page:horizontal {
    background: #059669;
    border-radius: 2px;
}
QSlider::groove:vertical {
    width: 5px;
    background: #1E2630;
    border-radius: 2px;
}
QSlider::handle:vertical {
    background: #F8FAFC;
    border: 2px solid #059669;
    width: 14px;
    height: 12px;
    margin: 0 -5px;
    border-radius: 6px;
}
QSlider::add-page:vertical {
    background: #059669;
    border-radius: 2px;
}
QSlider::sub-page:vertical {
    background: #1E2630;
    border-radius: 2px;
}
QProgressBar {
    background-color: #07090C;
    border: 1px solid #1E2630;
    border-radius: 5px;
    text-align: center;
    height: 15px;
    font-size: 11px;
    color: #CBD5E1;
}
QProgressBar::chunk {
    background-color: #059669;
    border-radius: 4px;
}
QPlainTextEdit {
    background-color: #07090C;
    border: 1px solid #1E2630;
    border-radius: 6px;
    color: #94A3B8;
    font-family: "JetBrains Mono", "Fira Code", monospace;
    font-size: 11px;
    padding: 8px;
}
QStatusBar {
    background-color: #080A0D;
    border-top: 1px solid #1E2630;
    color: #94A3B8;
    font-size: 12px;
}
"""


def _create_tray_icon() -> QIcon:
    """Paint the crisp b7software geometric badge for the system tray & window."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(QPen(QColor("#1E2630"), 2.0))
    p.setBrush(QColor("#090B0E"))
    p.drawRoundedRect(QRectF(4, 4, 56, 56), 11, 11)
    QSvgRenderer(QByteArray(B7_MARK_SVG)).render(p, QRectF(12, 12, 40, 40))
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


class _OptionalDepInstallThread(QThread):
    progress = Signal(str)
    finished_install = Signal(str, bool, str)

    def __init__(self, dep_id: str, parent=None) -> None:
        super().__init__(parent)
        self._dep_id = dep_id

    def run(self) -> None:
        ok, msg = install_optional_dependency(self._dep_id, self.progress.emit)
        self.finished_install.emit(self._dep_id, ok, msg)


class _UpdateCheckThread(QThread):
    checked = Signal(object)

    def run(self) -> None:
        self.checked.emit(check_for_updates())


class _UpdateApplyThread(QThread):
    progress = Signal(str)
    finished_update = Signal(bool, str, bool)

    def __init__(self, remote_sha: str = "", parent=None) -> None:
        super().__init__(parent)
        self._remote_sha = remote_sha

    def run(self) -> None:
        ok, msg, restart_rec = apply_update(
            progress_cb=self.progress.emit,
            remote_sha=self._remote_sha,
        )
        self.finished_update.emit(ok, msg, restart_rec)


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
        self._focused_device_path: str = self._config.device_path or "auto"
        self._focused_stable_id: str = ""
        self._connected_devs_cache = []
        self._profile_store: ProfileStore = load_profile_store()
        self._last_state = ControllerState()
        self._bat_prev_device_id: str | None = None
        self._bat_prev_charging: bool | None = None
        self._bat_prev_level: int | None = None
        self._bat_low_notified = False
        self._bat_full_notified = False
        self._syncing_widgets = False
        self._service_active = False
        self._force_quit = False
        self._start_reader = start_reader
        self._circ_dialog: CircularityTestDialog | None = None
        self._last_notified_clip_path = ""
        self._notifier: DesktopClipNotifier | None = None
        if enable_tray or start_reader:
            self._notifier = DesktopClipNotifier(self)
            self._notifier.open_clip_requested.connect(self._on_open_clip_from_notification)
            self._notifier.start()

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

        self._last_update_result: UpdateCheckResult | None = None
        self._upd_check_manual = False

        if start_reader:
            self._reader.start()
            self._status_timer.start()
            self._profile_watch_timer.start()
            QTimer.singleShot(800, self._start_deps_check)
            QTimer.singleShot(1400, self._start_update_check)

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
        if self._notifier is not None:
            self._notifier.stop()
        if hasattr(self, "_eq_tab"):
            self._eq_tab.shutdown()
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
        tray.messageClicked.connect(
            lambda: self._on_open_clip_from_notification(self._last_notified_clip_path, "")
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
        root = GridRootWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 10, 14, 8)
        outer.setSpacing(10)

        outer.addWidget(self._build_header())

        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("PageCanvas")
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([510, 650])
        outer.addWidget(splitter, 1)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Listo · Conecta cualquier mando USB o Bluetooth.")

    def _build_header(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("HeaderBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(12)

        lay.addWidget(B7MarkWidget(34))

        title = QLabel(
            "Anti-Drift Studio <span style='color:#059669;'>■</span>"
        )
        title.setTextFormat(Qt.RichText)
        title.setObjectName("Title")
        lay.addWidget(title)
        lay.addStretch(1)

        self._update_btn = QPushButton("↑ Actualizar disponible")
        self._update_btn.setObjectName("PrimaryBtn")
        self._update_btn.setVisible(False)
        self._update_btn.setToolTip("Hay una actualización en GitHub. Haz clic para instalarla.")
        self._update_btn.clicked.connect(self._on_apply_update_clicked)
        lay.addWidget(self._update_btn)

        self._protect_btn = QPushButton("Protección: ...")
        self._protect_btn.setObjectName("ProtectToggleOff")
        self._protect_btn.clicked.connect(self._on_toggle_protection)
        lay.addWidget(self._protect_btn)
        return bar

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("PageCanvas")
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
            "background-color: #07090C; border: 1px solid #1E2630; "
            "border-radius: 5px; padding: 3px 8px; color: #10B981; "
            "font-family: 'JetBrains Mono', monospace; font-size: 11px; font-weight: 700;"
        )
        self._hz_badge.setToolTip("Frecuencia real de reporte del mando en Hz")
        top_row.addWidget(self._hz_badge)

        self._family_override: str | None = None
        self._family_btn = QPushButton("Auto: Xbox ▾")
        self._family_btn.setObjectName("MiniPill")
        self._family_btn.setToolTip(
            "Tipo de mando detectado automáticamente (clic para previsualizar otro)"
        )
        fam_menu = QMenu(self._family_btn)
        for fam_key, fam_lbl in (
            (None, "Automático (según mando)"),
            ("xbox", "Xbox One / Series"),
            ("playstation", "PlayStation (DualSense / PS4)"),
            ("nintendo_pro", "Nintendo Switch Pro"),
            ("joycon", "Nintendo Joy-Con"),
            ("wheel", "Volante Racing (T80 / Sim)"),
        ):
            act = fam_menu.addAction(fam_lbl)
            act.triggered.connect(lambda _=False, fk=fam_key: self._set_preview_family(fk))
        self._family_btn.setMenu(fam_menu)
        top_row.addWidget(self._family_btn)

        self._sim_btn = QPushButton("Probar sin mando")
        self._sim_btn.setObjectName("MiniPill")
        self._sim_btn.setCheckable(True)
        self._sim_btn.toggled.connect(self._on_toggle_simulator)
        top_row.addWidget(self._sim_btn)

        self._pad_switch_btn = QPushButton("Cambiar mando ▾")
        self._pad_switch_btn.setObjectName("AccentBtn")
        self._pad_switch_btn.setVisible(False)
        self._pad_switch_btn.setToolTip(
            "Elige qué mando ver y calibrar aquí (el modo multi-mando corrige todos a la vez)"
        )
        self._pad_switch_menu = QMenu(self._pad_switch_btn)
        self._pad_switch_btn.setMenu(self._pad_switch_menu)
        top_row.addWidget(self._pad_switch_btn)
        c_lay.addLayout(top_row)

        self._hw_pill = QLabel("Buscando mando...")
        self._hw_pill.setObjectName("PillOff")
        self._hw_pill.setAlignment(Qt.AlignCenter)
        c_lay.addWidget(self._hw_pill)

        self._controller_widget = ControllerWidget()
        self._controller_widget.simulated_state_changed.connect(
            self._update_simple_readout
        )
        c_lay.addWidget(self._controller_widget, 1)

        legend = QLabel(
            "<span style='color:#F43F5E;'>● Entrada real</span> &nbsp;&nbsp;·&nbsp;&nbsp;"
            "<span style='color:#10B981;'>● Salida limpia</span> &nbsp;&nbsp;·&nbsp;&nbsp;"
            "<span style='color:#34D399;'>▢ Zona muerta</span>"
        )
        legend.setAlignment(Qt.AlignCenter)
        legend.setObjectName("MutedNote")
        c_lay.addWidget(legend)

        self._vname_pill_btn = QPushButton(f"En juegos: {DEFAULT_VIRTUAL_NAME} ✎")
        self._vname_pill_btn.setObjectName("MiniPill")
        self._vname_pill_btn.setToolTip(
            "Haz clic para personalizar cómo aparece este mando en tus juegos"
        )
        self._vname_pill_btn.clicked.connect(self._on_quick_rename_virtual)
        c_lay.addWidget(self._vname_pill_btn)

        self._active_buttons_label = QLabel(
            "Pulsa botones o mueve las palancas para comprobar tu mando."
        )
        self._active_buttons_label.setWordWrap(True)
        self._active_buttons_label.setAlignment(Qt.AlignCenter)
        self._active_buttons_label.setStyleSheet(
            "background-color: #07090C; border: 1px solid #1E2630; "
            "border-radius: 6px; padding: 8px 10px; font-weight: 600; color: #E2E8F0;"
        )
        c_lay.addWidget(self._active_buttons_label)

        lay.addWidget(ctrl_card, 1)
        return panel

    def _build_right_panel(self) -> QWidget:
        wrapper = QWidget()
        wrapper.setObjectName("PageCanvas")
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
        tabs.addTab(self._macros_tab, "Macros")
        self._clips_tab = ClipsTabWidget(start_background=self._start_reader)
        self._clips_tab.clip_notification.connect(self._on_clip_notification)
        tabs.addTab(self._clips_tab, "Clips y Momentos")
        clips_idx = tabs.indexOf(self._clips_tab)
        self._eq_tab = EqTabWidget(start_background=self._start_reader)
        tabs.addTab(self._eq_tab, "Sonido y EQ")
        eq_idx = tabs.indexOf(self._eq_tab)
        self._mangohud_tab = MangoHudTabWidget()
        self._mangohud_tab.dependencies_changed.connect(self._refresh_optional_deps_ui)
        tabs.addTab(self._mangohud_tab, "MangoHud (HUD)")
        mh_idx = tabs.indexOf(self._mangohud_tab)
        tabs.addTab(self._build_settings_tab(), "Ajustes")

        def _on_tab_changed(idx: int) -> None:
            prof_bar.setVisible(idx not in (clips_idx, eq_idx, mh_idx))
            if idx == eq_idx and self._start_reader:
                self._eq_tab.refresh_audio_devices()
                self._eq_tab.refresh_app_streams()
            elif idx == mh_idx:
                self._mangohud_tab.refresh_installation_status()
                self._mangohud_tab.refresh_open_windows()

        tabs.currentChanged.connect(_on_tab_changed)
        self._main_tabs = tabs
        self._clips_tab_idx = clips_idx
        self._eq_tab_idx = eq_idx
        self._mh_tab_idx = mh_idx
        w_lay.addWidget(tabs, 1)
        return wrapper

    def _build_drift_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 6, 0, 0)
        outer.setSpacing(10)

        scroll = FitWidthScrollArea()
        self._mode_stack = QStackedWidget()

        # ======================== PAGE 0: GAMEPAD UI ========================
        self._gamepad_page = QWidget()
        lay = QVBoxLayout(self._gamepad_page)
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

        # Card 3: Configurable Triggers (LT / RT limits, deadzones & response curves)
        trig_hdr_row = QHBoxLayout()
        trig_lbl = QLabel("3. Gatillos configurables (Límite de recorrido y curva)")
        trig_lbl.setObjectName("SectionTitle")
        trig_hdr_row.addWidget(trig_lbl)
        trig_hdr_row.addStretch(1)
        trig_hint = QLabel("Ideal para frenar sin bloquear en carreras")
        trig_hint.setObjectName("MutedNote")
        trig_hdr_row.addWidget(trig_hint)
        lay.addLayout(trig_hdr_row)

        trig_row = QHBoxLayout()
        trig_row.setSpacing(10)
        self._trig_widgets: dict[str, dict[str, object]] = {}
        trig_row.addWidget(self._create_trigger_card("LT", "Gatillo Izquierdo (LT / Freno)"), 1)
        trig_row.addWidget(self._create_trigger_card("RT", "Gatillo Derecho (RT / Gas)"), 1)
        lay.addLayout(trig_row)

        # Card 4: Latency, Response Curve, Hair-Trigger & Nintendo swap
        lat_card = QFrame()
        lat_card.setObjectName("Card")
        lat_lay = QVBoxLayout(lat_card)
        lat_lay.setContentsMargins(14, 10, 14, 10)
        lat_lay.setSpacing(7)

        lat_hdr = QHBoxLayout()
        lat_title = QLabel("4. Baja latencia y extras")
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

        switch_wheel_row = QHBoxLayout()
        switch_wheel_row.addStretch(1)
        btn_force_wheel = QPushButton("¿Usas volante? Cambiar a interfaz de Volante Racing →")
        btn_force_wheel.setObjectName("MiniPill")
        btn_force_wheel.clicked.connect(lambda: self._on_wheel_mode_selected("wheel"))
        switch_wheel_row.addWidget(btn_force_wheel)
        lay.addLayout(switch_wheel_row)
        lay.addStretch(1)

        # ======================== PAGE 1: RACING WHEEL UI ========================
        self._wheel_page = self._build_wheel_page()

        self._mode_stack.addWidget(self._gamepad_page)
        self._mode_stack.addWidget(self._wheel_page)
        scroll.setWidget(self._mode_stack)
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

    def _add_wheel_slider(
        self,
        grid: QGridLayout,
        row: int,
        label: str,
        low: int,
        high: int,
        unit: str,
        on_change,
    ) -> tuple[QSlider, QLabel]:
        grid.addWidget(QLabel(label), row, 0)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(low, high)
        val_lbl = QLabel(f"0{unit}")
        val_lbl.setMinimumWidth(52)
        val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        slider.valueChanged.connect(on_change)
        grid.addWidget(slider, row, 1, 1, 2)
        grid.addWidget(val_lbl, row, 3)
        return slider, val_lbl

    def _create_trigger_card(self, tag: str, title: str) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        tlay = QVBoxLayout(card)
        tlay.setContentsMargins(12, 9, 12, 9)
        tlay.setSpacing(6)

        hdr = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setObjectName("SectionTitle")
        hdr.addWidget(lbl)
        hdr.addStretch(1)
        max_badge = QLabel("Tope: 100%")
        max_badge.setStyleSheet("color: #10B981; font-weight: 700; font-size: 11.5px;")
        hdr.addWidget(max_badge)
        tlay.addLayout(hdr)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(5)
        for pct, plbl in ((100, "100%"), (75, "75%"), (50, "50%"), (35, "35%")):
            b = QPushButton(plbl)
            b.setObjectName("MiniPill")
            b.clicked.connect(
                lambda _=False, t=tag, p=pct: self._on_trigger_max_changed(t, p)
            )
            preset_row.addWidget(b)
        preset_row.addStretch(1)
        tlay.addLayout(preset_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(5)
        max_sl, max_lbl = self._add_wheel_slider(
            grid, 0, "Salida máxima:", 10, 100, "%",
            lambda val, t=tag: self._on_trigger_max_changed(t, val),
        )
        dz_sl, dz_lbl = self._add_wheel_slider(
            grid, 1, "Zona muerta:", 0, 35, "%",
            lambda val, t=tag: self._on_trigger_dz_changed(t, val),
        )
        cur_sl, cur_lbl = self._add_wheel_slider(
            grid, 2, "Curva progreso:", 40, 250, "",
            lambda val, t=tag: self._on_trigger_curve_changed(t, val),
        )
        tlay.addLayout(grid)

        self._trig_widgets[tag] = {
            "badge": max_badge,
            "max_slider": max_sl,
            "max_label": max_lbl,
            "dz_slider": dz_sl,
            "dz_label": dz_lbl,
            "curve_slider": cur_sl,
            "curve_label": cur_lbl,
        }
        return card

    def _build_wheel_page(self) -> QWidget:
        page = QWidget()
        w_root = QVBoxLayout(page)
        w_root.setContentsMargins(0, 0, 6, 0)
        w_root.setSpacing(10)

        # Header Card: Detected Wheel & Quick Actions
        hdr_card = QFrame()
        hdr_card.setObjectName("Card")
        hdr_lay = QHBoxLayout(hdr_card)
        hdr_lay.setContentsMargins(14, 10, 14, 10)
        hdr_lay.setSpacing(8)

        hdr_text = QVBoxLayout()
        hdr_text.setSpacing(2)
        wt = QLabel("Modo Volante de Carreras (Sim Racing)")
        wt.setObjectName("SectionTitle")
        self._wheel_sub_lbl = QLabel(
            "Interfaz adaptada exclusivamente para volante, pedales, palanca y freno de mano."
        )
        self._wheel_sub_lbl.setWordWrap(True)
        self._wheel_sub_lbl.setObjectName("MutedNote")
        hdr_text.addWidget(wt)
        hdr_text.addWidget(self._wheel_sub_lbl)
        hdr_lay.addLayout(hdr_text, 1)

        self._btn_center_wheel = QPushButton("Centrar volante (0°)")
        self._btn_center_wheel.setObjectName("AccentBtn")
        self._btn_center_wheel.setMinimumHeight(34)
        self._btn_center_wheel.clicked.connect(lambda: self._capture_raw_center("L"))
        hdr_lay.addWidget(self._btn_center_wheel)

        btn_back_pad = QPushButton("Usar interfaz Mando")
        btn_back_pad.setObjectName("MiniPill")
        btn_back_pad.clicked.connect(lambda: self._on_wheel_mode_selected("gamepad"))
        hdr_lay.addWidget(btn_back_pad)
        w_root.addWidget(hdr_card)

        # Card 1: Steering Angle, Deadzone, Lock, Linearity & FFB
        steer_card = QFrame()
        steer_card.setObjectName("Card")
        slay = QVBoxLayout(steer_card)
        slay.setContentsMargins(14, 10, 14, 10)
        slay.setSpacing(8)
        st_lbl = QLabel("1. Dirección, giro y respuesta")
        st_lbl.setObjectName("SectionTitle")
        slay.addWidget(st_lbl)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        mode_row.addWidget(QLabel("Detección:"))
        self._wheel_mode_btns: dict[str, QPushButton] = {}
        for mode_id, mlabel in (
            ("auto", "Auto"),
            ("wheel", "Forzar Volante"),
            ("gamepad", "Gamepad"),
        ):
            btn = QPushButton(mlabel)
            btn.setObjectName("MiniPill")
            btn.setCheckable(True)
            btn.clicked.connect(
                lambda _=False, mid=mode_id: self._on_wheel_mode_selected(mid)
            )
            self._wheel_mode_btns[mode_id] = btn
            mode_row.addWidget(btn)
        mode_row.addStretch(1)
        self._wheel_low_lat_chk = QCheckBox("Baja latencia")
        self._wheel_low_lat_chk.toggled.connect(self._on_low_latency_changed)
        mode_row.addWidget(self._wheel_low_lat_chk)
        slay.addLayout(mode_row)

        rot_row = QHBoxLayout()
        rot_row.setSpacing(5)
        rot_row.addWidget(QLabel("Ángulo de giro:"))
        self._wheel_rot_btns: dict[int, QPushButton] = {}
        for deg, rlabel in (
            (180, "180°"),
            (240, "240° (T80)"),
            (270, "270° GT"),
            (540, "540° Rally"),
            (900, "900° Sim"),
            (1080, "1080°"),
        ):
            btn = QPushButton(rlabel)
            btn.setObjectName("MiniPill")
            btn.setCheckable(True)
            btn.clicked.connect(
                lambda _=False, d=deg: self._on_wheel_rot_selected(d)
            )
            self._wheel_rot_btns[deg] = btn
            rot_row.addWidget(btn)
        rot_row.addStretch(1)
        slay.addLayout(rot_row)

        sgrid = QGridLayout()
        sgrid.setHorizontalSpacing(10)
        sgrid.setVerticalSpacing(6)
        self._wheel_steer_dz_slider, self._wheel_steer_dz_lbl = self._add_wheel_slider(
            sgrid, 0, "Zona muerta central:", 0, 20, "%", self._on_wheel_steer_dz_changed
        )
        self._wheel_steer_sat_slider, self._wheel_steer_sat_lbl = self._add_wheel_slider(
            sgrid, 1, "Tope de giro (Saturación):", 35, 100, "%", self._on_wheel_steer_sat_changed
        )
        self._wheel_lin_slider, self._wheel_lin_lbl = self._add_wheel_slider(
            sgrid, 2, "Linealidad dirección:", 50, 150, "", self._on_wheel_lin_changed
        )
        self._wheel_rumble_slider, self._wheel_rumble_lbl = self._add_wheel_slider(
            sgrid, 3, "Fuerza FFB / Vibración:", 0, 100, "%", self._on_rumble_changed
        )
        self._wheel_debounce_slider, self._wheel_debounce_lbl = self._add_wheel_slider(
            sgrid, 4, "Anti-rebote levas/botones:", 0, 45, " ms", self._on_debounce_changed
        )
        slay.addLayout(sgrid)
        w_root.addWidget(steer_card)

        # Card 2: Pedals (Throttle, Brake, Clutch & Combined)
        ped_card = QFrame()
        ped_card.setObjectName("Card")
        play = QVBoxLayout(ped_card)
        play.setContentsMargins(14, 10, 14, 10)
        play.setSpacing(7)
        pt_lbl = QLabel("2. Pedales (Acelerador, Freno y Embrague)")
        pt_lbl.setObjectName("SectionTitle")
        play.addWidget(pt_lbl)

        pgrid = QGridLayout()
        pgrid.setHorizontalSpacing(10)
        pgrid.setVerticalSpacing(6)
        self._wheel_dz_gas_slider, self._wheel_dz_gas_lbl = self._add_wheel_slider(
            pgrid, 0, "Zona muerta acelerador:", 0, 25, "%", self._on_wheel_dz_gas_changed
        )
        self._wheel_max_gas_slider, self._wheel_max_gas_lbl = self._add_wheel_slider(
            pgrid, 1, "Alcance máx acelerador:", 50, 100, "%", self._on_wheel_max_gas_changed
        )
        self._wheel_dz_brake_slider, self._wheel_dz_brake_lbl = self._add_wheel_slider(
            pgrid, 2, "Zona muerta freno:", 0, 25, "%", self._on_wheel_dz_brake_changed
        )
        self._wheel_max_brake_slider, self._wheel_max_brake_lbl = self._add_wheel_slider(
            pgrid, 3, "Alcance máx freno:", 50, 100, "%", self._on_wheel_max_brake_changed
        )
        play.addLayout(pgrid)

        chk_row = QHBoxLayout()
        chk_row.setSpacing(12)
        self._wheel_inv_gas_chk = QCheckBox("Invertir acelerador")
        self._wheel_inv_gas_chk.toggled.connect(self._on_wheel_inv_gas_changed)
        chk_row.addWidget(self._wheel_inv_gas_chk)

        self._wheel_inv_brake_chk = QCheckBox("Invertir freno")
        self._wheel_inv_brake_chk.toggled.connect(self._on_wheel_inv_brake_changed)
        chk_row.addWidget(self._wheel_inv_brake_chk)

        self._wheel_combo_chk = QCheckBox("Pedales combinados (1 eje)")
        self._wheel_combo_chk.toggled.connect(self._on_wheel_combo_changed)
        chk_row.addWidget(self._wheel_combo_chk)

        chk_row.addWidget(QLabel("Embrague:"))
        self._wheel_clutch_combo = QComboBox()
        self._wheel_clutch_combo.addItem("Solo visual", "none")
        self._wheel_clutch_combo.addItem("Embrague → Botón X", "x_btn")
        self._wheel_clutch_combo.addItem("Embrague → Botón LB", "lb_btn")
        self._wheel_clutch_combo.currentIndexChanged.connect(self._on_wheel_aux_combo_changed)
        chk_row.addWidget(self._wheel_clutch_combo)
        chk_row.addStretch(1)
        play.addLayout(chk_row)
        w_root.addWidget(ped_card)

        # Card 3: Shifter (Paddles / Sequential / H-Shifter) & Handbrake
        aux_card = QFrame()
        aux_card.setObjectName("Card")
        aux_lay = QVBoxLayout(aux_card)
        aux_lay.setContentsMargins(14, 10, 14, 10)
        aux_lay.setSpacing(8)

        aux_hdr = QHBoxLayout()
        at_lbl = QLabel("3. Palanca de cambios (Shifter) y Freno de mano")
        at_lbl.setObjectName("SectionTitle")
        aux_hdr.addWidget(at_lbl)
        aux_hdr.addStretch(1)
        btn_scan_aux = QPushButton("↻ Detectar USB Shifter/Freno")
        btn_scan_aux.setObjectName("MiniPill")
        btn_scan_aux.clicked.connect(self._refresh_sim_aux_devices)
        aux_hdr.addWidget(btn_scan_aux)
        aux_lay.addLayout(aux_hdr)

        sh_row = QHBoxLayout()
        sh_row.setSpacing(6)
        sh_row.addWidget(QLabel("Tipo de caja:"))
        self._wheel_shifter_btns: dict[str, QPushButton] = {}
        for sm_id, sm_lbl in (
            ("paddles", "Levas"),
            ("sequential", "Secuencial"),
            ("h_shifter", "H-Shifter → LB/RB"),
            ("none", "Nativo en juego"),
        ):
            b = QPushButton(sm_lbl)
            b.setObjectName("MiniPill")
            b.setCheckable(True)
            b.clicked.connect(
                lambda _=False, sid=sm_id: self._on_shifter_mode_selected(sid)
            )
            self._wheel_shifter_btns[sm_id] = b
            sh_row.addWidget(b)
        sh_row.addStretch(1)
        aux_lay.addLayout(sh_row)

        sh_dev_row = QHBoxLayout()
        sh_dev_row.setSpacing(8)
        sh_dev_row.addWidget(QLabel("Shifter USB:"))
        self._wheel_shifter_combo = QComboBox()
        self._wheel_shifter_combo.currentIndexChanged.connect(self._on_wheel_aux_combo_changed)
        sh_dev_row.addWidget(self._wheel_shifter_combo, 1)
        sh_dev_row.addWidget(QLabel("Bajar:"))
        self._wheel_down_btn_combo = QComboBox()
        self._wheel_up_btn_combo = QComboBox()
        for bname in ("LB", "RB", "X", "B", "A", "Y", "VIEW", "MENU"):
            self._wheel_down_btn_combo.addItem(bname, bname)
            self._wheel_up_btn_combo.addItem(bname, bname)
        self._wheel_down_btn_combo.currentIndexChanged.connect(self._on_wheel_aux_combo_changed)
        self._wheel_up_btn_combo.currentIndexChanged.connect(self._on_wheel_aux_combo_changed)
        sh_dev_row.addWidget(self._wheel_down_btn_combo)
        sh_dev_row.addWidget(QLabel("Subir:"))
        sh_dev_row.addWidget(self._wheel_up_btn_combo)
        aux_lay.addLayout(sh_dev_row)

        hb_row = QHBoxLayout()
        hb_row.setSpacing(8)
        self._wheel_hb_chk = QCheckBox("Habilitar freno de mano")
        self._wheel_hb_chk.toggled.connect(self._on_wheel_hb_toggled)
        hb_row.addWidget(self._wheel_hb_chk)
        self._wheel_hb_combo = QComboBox()
        self._wheel_hb_combo.currentIndexChanged.connect(self._on_wheel_aux_combo_changed)
        hb_row.addWidget(self._wheel_hb_combo, 1)
        hb_row.addWidget(QLabel("Acción:"))
        self._wheel_hb_target_combo = QComboBox()
        for tname in ("A", "B", "X", "Y"):
            self._wheel_hb_target_combo.addItem(f"Botón {tname}", tname)
        self._wheel_hb_target_combo.currentIndexChanged.connect(self._on_wheel_aux_combo_changed)
        hb_row.addWidget(self._wheel_hb_target_combo)
        self._wheel_hb_inv_chk = QCheckBox("Invertir")
        self._wheel_hb_inv_chk.toggled.connect(self._on_wheel_hb_inv_toggled)
        hb_row.addWidget(self._wheel_hb_inv_chk)
        aux_lay.addLayout(hb_row)

        hb_grid = QGridLayout()
        hb_grid.setHorizontalSpacing(10)
        self._wheel_hb_dz_slider, self._wheel_hb_dz_lbl = self._add_wheel_slider(
            hb_grid, 0, "Umbral activación freno de mano:", 5, 85, "%",
            self._on_wheel_hb_dz_changed,
        )
        aux_lay.addLayout(hb_grid)

        w_root.addWidget(aux_card)
        w_root.addStretch(1)
        self._refresh_sim_aux_devices()
        return page

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
        pick_row.addWidget(QLabel("Mando a configurar:"))
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

        vname_row = QHBoxLayout()
        vname_row.addWidget(QLabel("Nombre del mando en juegos:"))
        self._virtual_name_edit = QLineEdit()
        self._virtual_name_edit.setMaxLength(79)
        self._virtual_name_edit.setPlaceholderText(
            f"Ej. Mando de Cesar (por defecto: {DEFAULT_VIRTUAL_NAME})"
        )
        self._virtual_name_edit.textChanged.connect(self._on_virtual_name_changed)
        vname_row.addWidget(self._virtual_name_edit, 1)
        btn_vname_def = QPushButton("Por defecto")
        btn_vname_def.setObjectName("MiniPill")
        btn_vname_def.setToolTip(f"Restaurar '{DEFAULT_VIRTUAL_NAME}'")
        btn_vname_def.clicked.connect(
            lambda: self._virtual_name_edit.setText(DEFAULT_VIRTUAL_NAME)
        )
        vname_row.addWidget(btn_vname_def)
        d_lay.addLayout(vname_row)

        self._multi_pad_chk = QCheckBox(
            "Modo multi-mando: arreglar el drift de todos los mandos conectados a la vez"
        )
        self._multi_pad_chk.setToolTip(
            "Protege simultáneamente varios mandos (hasta 8) con su propia calibración y nombre."
        )
        self._multi_pad_chk.toggled.connect(self._on_multi_pad_toggled)
        d_lay.addWidget(self._multi_pad_chk)

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

        self._app_autostart_chk = QCheckBox(
            "Ejecutar Anti-Drift Studio automáticamente al encender el PC"
        )
        self._app_autostart_chk.setToolTip(
            "Abre el programa automáticamente al iniciar sesión (por defecto desactivado)."
        )
        self._app_autostart_chk.setChecked(is_app_autostart_enabled())
        self._app_autostart_chk.toggled.connect(self._on_toggle_app_autostart)
        d_lay.addWidget(self._app_autostart_chk)

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

        # Card: Optional Dependencies (MangoHud, GameMode, GSR, Vosk)
        opt_card = QFrame()
        opt_card.setObjectName("Card")
        opt_lay = QVBoxLayout(opt_card)
        opt_lay.setContentsMargins(14, 12, 14, 12)
        opt_lay.setSpacing(8)
        opt_hdr = QHBoxLayout()
        opt_title = QLabel("Dependencias opcionales")
        opt_title.setObjectName("SectionTitle")
        self._opt_distro_lbl = QLabel("Distro: detectando...")
        self._opt_distro_lbl.setObjectName("MutedNote")
        btn_opt_refresh = QPushButton("Comprobar")
        btn_opt_refresh.setObjectName("MiniPill")
        btn_opt_refresh.clicked.connect(self._refresh_optional_deps_ui)
        opt_hdr.addWidget(opt_title)
        opt_hdr.addStretch(1)
        opt_hdr.addWidget(self._opt_distro_lbl)
        opt_hdr.addWidget(btn_opt_refresh)
        opt_lay.addLayout(opt_hdr)

        self._opt_deps_rows = QVBoxLayout()
        self._opt_deps_rows.setSpacing(6)
        opt_lay.addLayout(self._opt_deps_rows)
        self._opt_install_msg = QLabel("")
        self._opt_install_msg.setWordWrap(True)
        self._opt_install_msg.setObjectName("MutedNote")
        self._opt_install_msg.setVisible(False)
        opt_lay.addWidget(self._opt_install_msg)
        lay.addWidget(opt_card)
        self._refresh_optional_deps_ui()

        # Card: GitHub Update Manager
        upd_card = QFrame()
        upd_card.setObjectName("Card")
        u_lay = QVBoxLayout(upd_card)
        u_lay.setContentsMargins(14, 12, 14, 12)
        u_lay.setSpacing(8)

        u_title = QLabel("Actualizaciones (GitHub)")
        u_title.setObjectName("SectionTitle")
        u_lay.addWidget(u_title)

        gh_target = resolve_github_target()
        local_ver = read_local_app_version()
        local_sha = get_local_commit_sha()
        short_sha = local_sha[:7] if local_sha else "—"

        self._upd_repo_lbl = QLabel(
            f"Repositorio: {gh_target.owner}/{gh_target.repo} · rama {gh_target.branch} "
            f"· versión instalada v{local_ver} ({short_sha})"
        )
        self._upd_repo_lbl.setWordWrap(True)
        self._upd_repo_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._upd_repo_lbl.setStyleSheet("color: #94A3B8; font-size: 12px;")
        u_lay.addWidget(self._upd_repo_lbl)

        self._upd_status_lbl = QLabel("Estado: Comprobación automática al iniciar o bajo demanda.")
        self._upd_status_lbl.setWordWrap(True)
        self._upd_status_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._upd_status_lbl.setStyleSheet("color: #CFD2DA; font-size: 12.5px;")
        u_lay.addWidget(self._upd_status_lbl)

        self._upd_changes_lbl = QLabel("")
        self._upd_changes_lbl.setWordWrap(True)
        self._upd_changes_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._upd_changes_lbl.setStyleSheet(
            "background-color: #07090C; border: 1px solid #1E2630; border-radius: 6px; "
            "padding: 8px 10px; color: #94A3B8; font-family: 'JetBrains Mono', monospace; font-size: 11px;"
        )
        self._upd_changes_lbl.setVisible(False)
        u_lay.addWidget(self._upd_changes_lbl)

        u_btn_row = QHBoxLayout()
        self._btn_check_updates = QPushButton("Buscar actualizaciones")
        self._btn_check_updates.clicked.connect(lambda: self._start_update_check(manual=True))
        u_btn_row.addWidget(self._btn_check_updates)

        self._btn_apply_update = QPushButton("Actualizar ahora")
        self._btn_apply_update.setObjectName("PrimaryBtn")
        self._btn_apply_update.setVisible(False)
        self._btn_apply_update.clicked.connect(self._on_apply_update_clicked)
        u_btn_row.addWidget(self._btn_apply_update)

        btn_open_gh = QPushButton("Abrir GitHub")
        btn_open_gh.setObjectName("MiniPill")
        btn_open_gh.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(resolve_github_target().repo_url))
        )
        u_btn_row.addWidget(btn_open_gh)
        u_btn_row.addStretch(1)
        u_lay.addLayout(u_btn_row)
        lay.addWidget(upd_card)

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
        devs = list_physical_controllers()
        self._connected_devs_cache = devs
        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        self._device_combo.addItem("Automático (mando principal / todos)", "auto")
        for idx_d, dev in enumerate(devs, start=1):
            vid_txt = f" [{dev.vid_pid}]" if dev.vid_pid else ""
            prefix = f"Mando {idx_d}: " if len(devs) > 1 else ""
            self._device_combo.addItem(f"{prefix}{dev.name}{vid_txt}", dev.path)

        target_path = self._focused_device_path if self._config.multi_controller else self._config.device_path
        idx = self._device_combo.findData(target_path)
        self._device_combo.setCurrentIndex(max(0, idx))
        self._device_combo.blockSignals(False)
        self._rebuild_pad_switch_menu(devs)

    def _rebuild_pad_switch_menu(self, devs: list | None = None) -> None:
        if devs is None:
            devs = self._connected_devs_cache or list_physical_controllers()
        self._pad_switch_menu.clear()
        if len(devs) <= 1:
            self._pad_switch_btn.setVisible(False)
            return

        self._pad_switch_btn.setVisible(True)
        cur_idx = 1
        target = (
            self._focused_device_path
            if self._focused_device_path != "auto"
            else self._last_state.physical_path
        )
        for i, dev in enumerate(devs, start=1):
            if target == dev.path:
                cur_idx = i
            act = self._pad_switch_menu.addAction(f"Mando {i}: {dev.name}")
            act.setCheckable(True)
            act.setChecked(target == dev.path or (i == 1 and not target))
            act.triggered.connect(lambda _=False, p=dev.path: self._select_focused_device(p))
        self._pad_switch_btn.setText(f"Mando {cur_idx}/{len(devs)} ▾")

    def _select_focused_device(self, path: str) -> None:
        clean_path = path or "auto"
        self._focused_device_path = clean_path
        if not self._config.multi_controller:
            self._config.device_path = clean_path
        if self._reader is not None:
            self._reader.set_focused_device(clean_path)

        matched_dev = next((d for d in self._connected_devs_cache if d.path == clean_path), None)
        if matched_dev is None and clean_path == "auto" and self._connected_devs_cache:
            matched_dev = self._connected_devs_cache[0]
        self._focused_stable_id = matched_dev.stable_id if matched_dev else ""

        if self._focused_stable_id:
            dev_cfg = load_device_config(self._focused_stable_id, load_config())
            dev_cfg.multi_controller = self._config.multi_controller
            if not dev_cfg.multi_controller:
                dev_cfg.device_path = clean_path
            self._saved_config = dev_cfg.copy()
            self._config = dev_cfg.copy()
            self._apply_config_to_widgets(self._config)
        elif not self._config.multi_controller:
            self._sync_previews()

        self._refresh_device_selector()

    def _on_device_selected(self, _idx: int) -> None:
        path = self._device_combo.currentData() or "auto"
        if path != self._focused_device_path or (not self._config.multi_controller and path != self._config.device_path):
            self._select_focused_device(path)

    def _on_virtual_name_changed(self, text: str) -> None:
        if self._syncing_widgets:
            return
        self._config.virtual_name = sanitize_virtual_name(text) if text.strip() else DEFAULT_VIRTUAL_NAME
        self._vname_pill_btn.setText(f"En juegos: {self._config.virtual_name} ✎")
        self._sync_previews()

    def _on_quick_rename_virtual(self) -> None:
        new_name, ok = QInputDialog.getText(
            self,
            "Personalizar nombre del mando en juegos",
            "¿Cómo quieres que aparezca este mando en Steam y tus juegos?\n"
            f"(Deja vacío o usa '{DEFAULT_VIRTUAL_NAME}' por defecto)",
            text=self._config.virtual_name,
        )
        if not ok:
            return
        sanitized = sanitize_virtual_name(new_name)
        self._config.virtual_name = sanitized
        self._virtual_name_edit.blockSignals(True)
        self._virtual_name_edit.setText(sanitized)
        self._virtual_name_edit.blockSignals(False)
        self._vname_pill_btn.setText(f"En juegos: {sanitized} ✎")
        self._on_save_and_apply()

    def _on_multi_pad_toggled(self, checked: bool) -> None:
        if self._syncing_widgets:
            return
        self._config.multi_controller = bool(checked)
        if not checked and self._focused_device_path:
            self._config.device_path = self._focused_device_path
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
            self._multi_pad_chk.setChecked(cfg.multi_controller)
            self._virtual_name_edit.setText(cfg.virtual_name)
            self._vname_pill_btn.setText(f"En juegos: {cfg.virtual_name} ✎")
            self._debounce_slider.setValue(max(0, min(45, cfg.debounce_ms)))
            self._debounce_label.setText(f"{cfg.debounce_ms} ms")
            self._rumble_slider.setValue(max(0, min(100, cfg.rumble_gain)))
            self._rumble_label.setText(f"{cfg.rumble_gain}%")

            closest_curve = min(
                self._curve_btns.keys(), key=lambda v: abs(v - cfg.response_curve)
            )
            for v, btn in self._curve_btns.items():
                btn.setChecked(v == closest_curve)

            self._sync_trigger_widgets(cfg)
            self._sync_wheel_widgets(cfg)
        finally:
            self._syncing_widgets = False

        self._sync_active_input_mode()
        self._sync_previews()

    def _sync_trigger_widgets(self, cfg: AntiDriftConfig) -> None:
        for tag, dz, mx, cur in (
            ("LT", cfg.lt_dz, cfg.lt_max, cfg.lt_curve),
            ("RT", cfg.rt_dz, cfg.rt_max, cfg.rt_curve),
        ):
            wmap = self._trig_widgets.get(tag)
            if not wmap:
                continue
            mp = max(10, min(100, round(mx * 100)))
            dp = max(0, min(35, round(dz * 100)))
            cp = max(40, min(250, round(cur * 100)))
            wmap["max_slider"].setValue(mp)
            wmap["max_label"].setText(f"{mp}%")
            wmap["badge"].setText(f"Tope: {mp}%")
            wmap["dz_slider"].setValue(dp)
            wmap["dz_label"].setText(f"{dp}%")
            wmap["curve_slider"].setValue(cp)
            wmap["curve_label"].setText(f"{cp / 100.0:.2f}")

    def _is_wheel_ui_active(self) -> bool:
        if self._config.wheel_mode == "wheel":
            return True
        if self._config.wheel_mode == "gamepad":
            return False
        if self._family_override == "wheel":
            return True
        return bool(self._last_state.is_wheel or self._last_state.controller_family == "wheel")

    def _sync_active_input_mode(self) -> None:
        wheel_active = self._is_wheel_ui_active()
        idx = 1 if wheel_active else 0
        self._gamepad_page.setSizePolicy(
            QSizePolicy.Preferred,
            QSizePolicy.Ignored if wheel_active else QSizePolicy.Preferred,
        )
        self._wheel_page.setSizePolicy(
            QSizePolicy.Preferred,
            QSizePolicy.Preferred if wheel_active else QSizePolicy.Ignored,
        )
        if self._mode_stack.currentIndex() != idx:
            self._mode_stack.setCurrentIndex(idx)
        self._mode_stack.adjustSize()

    @staticmethod
    def _set_combo_by_data(combo: QComboBox, value: str) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return
        if combo.count() > 0:
            combo.setCurrentIndex(0)

    def _refresh_sim_aux_devices(self) -> None:
        if not hasattr(self, "_wheel_shifter_combo"):
            return
        shifters, handbrakes = list_sim_aux_devices(self._last_state.physical_path)
        was_sync = self._syncing_widgets
        self._syncing_widgets = True
        try:
            self._wheel_shifter_combo.clear()
            self._wheel_shifter_combo.addItem("Auto (detectar USB Shifter / Volante)", "auto")
            self._wheel_shifter_combo.addItem("En el mismo volante", "same")
            for dev in shifters:
                self._wheel_shifter_combo.addItem(f"{dev.name} ({dev.path})", dev.path)
            if self._config.wheel_shifter_device not in ("auto", "same", "none") and all(
                d.path != self._config.wheel_shifter_device for d in shifters
            ):
                self._wheel_shifter_combo.addItem(
                    self._config.wheel_shifter_device, self._config.wheel_shifter_device
                )
            self._set_combo_by_data(self._wheel_shifter_combo, self._config.wheel_shifter_device)

            self._wheel_hb_combo.clear()
            self._wheel_hb_combo.addItem("Auto (detectar USB / Volante)", "auto")
            self._wheel_hb_combo.addItem("En el mismo volante", "same")
            self._wheel_hb_combo.addItem("Desconectado", "none")
            for dev in handbrakes:
                self._wheel_hb_combo.addItem(f"{dev.name} ({dev.path})", dev.path)
            if self._config.wheel_handbrake_device not in ("auto", "same", "none") and all(
                d.path != self._config.wheel_handbrake_device for d in handbrakes
            ):
                self._wheel_hb_combo.addItem(
                    self._config.wheel_handbrake_device, self._config.wheel_handbrake_device
                )
            self._set_combo_by_data(self._wheel_hb_combo, self._config.wheel_handbrake_device)
        finally:
            self._syncing_widgets = was_sync

    def _sync_wheel_widgets(self, cfg: AntiDriftConfig) -> None:
        for mode_id, btn in self._wheel_mode_btns.items():
            btn.setChecked(mode_id == cfg.wheel_mode)
        closest_rot = min(
            self._wheel_rot_btns.keys(),
            key=lambda d: abs(d - cfg.wheel_rotation_deg),
        )
        for deg, btn in self._wheel_rot_btns.items():
            btn.setChecked(deg == closest_rot)

        sdz = max(0, min(20, round(cfg.wheel_steer_dz * 100)))
        self._wheel_steer_dz_slider.setValue(sdz)
        self._wheel_steer_dz_lbl.setText(f"{sdz}%")

        ssat = max(35, min(100, round(cfg.wheel_steer_sat * 100)))
        self._wheel_steer_sat_slider.setValue(ssat)
        self._wheel_steer_sat_lbl.setText(f"{ssat}%")

        lin_val = max(50, min(150, round(cfg.wheel_linearity * 100)))
        self._wheel_lin_slider.setValue(lin_val)
        self._wheel_lin_lbl.setText(f"{lin_val / 100.0:.2f}")

        self._wheel_low_lat_chk.setChecked(cfg.low_latency)
        self._wheel_rumble_slider.setValue(max(0, min(100, cfg.rumble_gain)))
        self._wheel_rumble_lbl.setText(f"{cfg.rumble_gain}%")
        self._wheel_debounce_slider.setValue(max(0, min(45, cfg.debounce_ms)))
        self._wheel_debounce_lbl.setText(f"{cfg.debounce_ms} ms")

        dz_g = max(0, min(25, round(cfg.wheel_dz_gas * 100)))
        self._wheel_dz_gas_slider.setValue(dz_g)
        self._wheel_dz_gas_lbl.setText(f"{dz_g}%")

        mg = max(50, min(100, round(cfg.wheel_max_gas * 100)))
        self._wheel_max_gas_slider.setValue(mg)
        self._wheel_max_gas_lbl.setText(f"{mg}%")

        dz_b = max(0, min(25, round(cfg.wheel_dz_brake * 100)))
        self._wheel_dz_brake_slider.setValue(dz_b)
        self._wheel_dz_brake_lbl.setText(f"{dz_b}%")

        mb = max(50, min(100, round(cfg.wheel_max_brake * 100)))
        self._wheel_max_brake_slider.setValue(mb)
        self._wheel_max_brake_lbl.setText(f"{mb}%")

        self._wheel_inv_gas_chk.setChecked(cfg.wheel_invert_gas)
        self._wheel_inv_brake_chk.setChecked(cfg.wheel_invert_brake)
        self._wheel_combo_chk.setChecked(cfg.wheel_combined_pedals)
        self._set_combo_by_data(self._wheel_clutch_combo, cfg.wheel_clutch_mode)

        for sm_id, btn in self._wheel_shifter_btns.items():
            btn.setChecked(sm_id == cfg.wheel_shifter_mode)
        self._set_combo_by_data(self._wheel_shifter_combo, cfg.wheel_shifter_device)
        self._set_combo_by_data(self._wheel_down_btn_combo, cfg.wheel_shifter_down_btn)
        self._set_combo_by_data(self._wheel_up_btn_combo, cfg.wheel_shifter_up_btn)

        self._wheel_hb_chk.setChecked(cfg.wheel_handbrake_enabled)
        self._set_combo_by_data(self._wheel_hb_combo, cfg.wheel_handbrake_device)
        self._set_combo_by_data(self._wheel_hb_target_combo, cfg.wheel_handbrake_target)
        self._wheel_hb_inv_chk.setChecked(cfg.wheel_handbrake_invert)
        hb_dz = max(5, min(85, round(cfg.wheel_handbrake_dz * 100)))
        self._wheel_hb_dz_slider.setValue(hb_dz)
        self._wheel_hb_dz_lbl.setText(f"{hb_dz}%")

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
        self._syncing_widgets = True
        try:
            self._debounce_slider.setValue(ms)
            self._debounce_label.setText(f"{ms} ms")
            self._wheel_debounce_slider.setValue(ms)
            self._wheel_debounce_lbl.setText(f"{ms} ms")
        finally:
            self._syncing_widgets = False
        self._sync_previews()

    def _on_rumble_changed(self, gain: int) -> None:
        if self._syncing_widgets:
            return
        self._config.rumble_gain = gain
        self._syncing_widgets = True
        try:
            self._rumble_slider.setValue(gain)
            self._rumble_label.setText(f"{gain}%")
            self._wheel_rumble_slider.setValue(gain)
            self._wheel_rumble_lbl.setText(f"{gain}%")
        finally:
            self._syncing_widgets = False
        self._sync_previews()

    def _on_low_latency_changed(self, checked: bool) -> None:
        if self._syncing_widgets:
            return
        self._config.low_latency = bool(checked)
        self._syncing_widgets = True
        try:
            self._low_lat_chk.setChecked(bool(checked))
            self._wheel_low_lat_chk.setChecked(bool(checked))
        finally:
            self._syncing_widgets = False
        self._sync_previews()

    def _on_trigger_max_changed(self, tag: str, pct: int) -> None:
        if self._syncing_widgets:
            return
        val = round(max(10, min(100, pct)) / 100.0, 2)
        if tag == "LT":
            self._config.lt_max = val
        else:
            self._config.rt_max = val
        self._syncing_widgets = True
        try:
            self._sync_trigger_widgets(self._config)
        finally:
            self._syncing_widgets = False
        self._sync_previews()

    def _on_trigger_dz_changed(self, tag: str, pct: int) -> None:
        if self._syncing_widgets:
            return
        val = round(max(0, min(35, pct)) / 100.0, 2)
        if tag == "LT":
            self._config.lt_dz = val
        else:
            self._config.rt_dz = val
        self._syncing_widgets = True
        try:
            self._sync_trigger_widgets(self._config)
        finally:
            self._syncing_widgets = False
        self._sync_previews()

    def _on_trigger_curve_changed(self, tag: str, cval: int) -> None:
        if self._syncing_widgets:
            return
        val = round(max(40, min(250, cval)) / 100.0, 2)
        if tag == "LT":
            self._config.lt_curve = val
        else:
            self._config.rt_curve = val
        self._syncing_widgets = True
        try:
            self._sync_trigger_widgets(self._config)
        finally:
            self._syncing_widgets = False
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

    def _on_wheel_mode_selected(self, mode_id: str) -> None:
        if self._syncing_widgets:
            return
        self._config.wheel_mode = mode_id
        for m, btn in self._wheel_mode_btns.items():
            btn.blockSignals(True)
            btn.setChecked(m == mode_id)
            btn.blockSignals(False)
        if mode_id == "wheel":
            self._set_preview_family("wheel")
        elif mode_id in ("auto", "gamepad") and self._family_override == "wheel":
            self._set_preview_family(None)
        self._sync_active_input_mode()
        self._sync_previews()

    def _on_shifter_mode_selected(self, sm_id: str) -> None:
        if self._syncing_widgets:
            return
        self._config.wheel_shifter_mode = sm_id
        for s, btn in self._wheel_shifter_btns.items():
            btn.blockSignals(True)
            btn.setChecked(s == sm_id)
            btn.blockSignals(False)
        self._sync_previews()

    def _on_wheel_aux_combo_changed(self) -> None:
        if self._syncing_widgets:
            return
        self._config.wheel_clutch_mode = str(self._wheel_clutch_combo.currentData() or "none")
        self._config.wheel_shifter_device = str(self._wheel_shifter_combo.currentData() or "auto")
        self._config.wheel_shifter_down_btn = str(self._wheel_down_btn_combo.currentData() or "LB")
        self._config.wheel_shifter_up_btn = str(self._wheel_up_btn_combo.currentData() or "RB")
        self._config.wheel_handbrake_device = str(self._wheel_hb_combo.currentData() or "auto")
        self._config.wheel_handbrake_target = str(self._wheel_hb_target_combo.currentData() or "A")
        self._sync_previews()

    def _on_wheel_hb_toggled(self, checked: bool) -> None:
        if self._syncing_widgets:
            return
        self._config.wheel_handbrake_enabled = bool(checked)
        self._sync_previews()

    def _on_wheel_hb_inv_toggled(self, checked: bool) -> None:
        if self._syncing_widgets:
            return
        self._config.wheel_handbrake_invert = bool(checked)
        self._sync_previews()

    def _on_wheel_hb_dz_changed(self, pct: int) -> None:
        if self._syncing_widgets:
            return
        self._config.wheel_handbrake_dz = round(pct / 100.0, 2)
        self._wheel_hb_dz_lbl.setText(f"{pct}%")
        self._sync_previews()

    def _on_wheel_rot_selected(self, deg: int) -> None:
        if self._syncing_widgets:
            return
        self._config.wheel_rotation_deg = deg
        for d, btn in self._wheel_rot_btns.items():
            btn.blockSignals(True)
            btn.setChecked(d == deg)
            btn.blockSignals(False)
        self._sync_previews()

    def _on_wheel_steer_dz_changed(self, pct: int) -> None:
        if self._syncing_widgets:
            return
        self._config.wheel_steer_dz = round(pct / 100.0, 3)
        self._wheel_steer_dz_lbl.setText(f"{pct}%")
        self._sync_previews()

    def _on_wheel_steer_sat_changed(self, pct: int) -> None:
        if self._syncing_widgets:
            return
        self._config.wheel_steer_sat = round(pct / 100.0, 2)
        self._wheel_steer_sat_lbl.setText(f"{pct}%")
        self._sync_previews()

    def _on_wheel_lin_changed(self, val: int) -> None:
        if self._syncing_widgets:
            return
        self._config.wheel_linearity = round(val / 100.0, 2)
        self._wheel_lin_lbl.setText(f"{val / 100.0:.2f}")
        self._sync_previews()

    def _on_wheel_dz_gas_changed(self, pct: int) -> None:
        if self._syncing_widgets:
            return
        self._config.wheel_dz_gas = round(pct / 100.0, 3)
        self._wheel_dz_gas_lbl.setText(f"{pct}%")
        self._sync_previews()

    def _on_wheel_max_gas_changed(self, pct: int) -> None:
        if self._syncing_widgets:
            return
        self._config.wheel_max_gas = round(pct / 100.0, 2)
        self._wheel_max_gas_lbl.setText(f"{pct}%")
        self._sync_previews()

    def _on_wheel_dz_brake_changed(self, pct: int) -> None:
        if self._syncing_widgets:
            return
        self._config.wheel_dz_brake = round(pct / 100.0, 3)
        self._wheel_dz_brake_lbl.setText(f"{pct}%")
        self._sync_previews()

    def _on_wheel_max_brake_changed(self, pct: int) -> None:
        if self._syncing_widgets:
            return
        self._config.wheel_max_brake = round(pct / 100.0, 2)
        self._wheel_max_brake_lbl.setText(f"{pct}%")
        self._sync_previews()

    def _on_wheel_inv_gas_changed(self, checked: bool) -> None:
        if self._syncing_widgets:
            return
        self._config.wheel_invert_gas = bool(checked)
        self._sync_previews()

    def _on_wheel_inv_brake_changed(self, checked: bool) -> None:
        if self._syncing_widgets:
            return
        self._config.wheel_invert_brake = bool(checked)
        self._sync_previews()

    def _on_wheel_combo_changed(self, checked: bool) -> None:
        if self._syncing_widgets:
            return
        self._config.wheel_combined_pedals = bool(checked)
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
        self._config.virtual_name = sanitize_virtual_name(self._virtual_name_edit.text())
        self._virtual_name_edit.blockSignals(True)
        self._virtual_name_edit.setText(self._config.virtual_name)
        self._virtual_name_edit.blockSignals(False)
        self._vname_pill_btn.setText(f"En juegos: {self._config.virtual_name} ✎")

        stable_id = self._focused_stable_id or (self._last_state.physical_stable_id or "")
        focused_label = self._last_state.physical_name or ""
        is_specific_secondary = (
            self._config.multi_controller
            and self._focused_device_path != "auto"
            and bool(self._connected_devs_cache)
            and self._connected_devs_cache[0].path != self._focused_device_path
        )

        if stable_id:
            save_device_config(stable_id, self._config, label=focused_label)
        if is_specific_secondary:
            # Keep primary global pad overrides intact; only sync multi_controller mode flag
            base_cfg = load_config()
            base_cfg.multi_controller = self._config.multi_controller
            save_config(base_cfg)
        else:
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
        self._focused_device_path = "auto"
        self._focused_stable_id = ""
        if self._reader is not None:
            self._reader.set_focused_device("auto")
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

    def _set_preview_family(self, family: str | None) -> None:
        self._family_override = family
        self._controller_widget.set_family_override(family)
        self._sync_active_input_mode()
        self._refresh_family_badge()

    def _refresh_family_badge(self) -> None:
        labels = {
            "xbox": "Xbox",
            "playstation": "PS",
            "nintendo_pro": "Switch Pro",
            "joycon": "Joy-Con",
            "wheel": "Volante",
        }
        active = self._controller_widget.active_family()
        prefix = "Auto: " if self._family_override is None else ""
        self._family_btn.setText(f"{prefix}{labels.get(active, 'Xbox')} ▾")

    def _on_toggle_simulator(self, enabled: bool) -> None:
        self._controller_widget.set_simulator_enabled(enabled)
        if enabled:
            self._active_buttons_label.setText(
                "Modo prueba activo: haz clic o arrastra sobre el mando."
            )
        else:
            self._on_controller_state(self._last_state)

    def _on_controller_state(self, state: ControllerState) -> None:
        prev_stable = self._last_state.physical_stable_id
        prev_count = self._last_state.connected_count
        self._last_state = state

        if state.physical_stable_id and state.physical_stable_id != prev_stable:
            self._focused_stable_id = state.physical_stable_id
            # Load per-controller saved calibration/virtual name when newly focused on startup
            if self._config == self._saved_config:
                dev_cfg = load_device_config(state.physical_stable_id, self._saved_config)
                if dev_cfg != self._config:
                    dev_cfg.multi_controller = self._config.multi_controller
                    self._saved_config = dev_cfg.copy()
                    self._config = dev_cfg.copy()
                    self._apply_config_to_widgets(self._config)

        if state.connected_count != prev_count or state.physical_stable_id != prev_stable:
            self._connected_devs_cache = list_physical_controllers()
            self._refresh_device_selector()

        if state.mode in ("virtual", "direct"):
            pad_name = state.physical_name or "Mando conectado"
            if len(pad_name) > 24:
                pad_name = pad_name[:21] + "..."
            self._hw_pill.setText(
                f"Conectado ({state.connected_count} mandos): {pad_name}"
                if state.connected_count > 1
                else f"Conectado: {pad_name}"
            )
            vname_shown = state.virtual_name or self._config.virtual_name
            self._hw_pill.setToolTip(f"Físico: {state.physical_name}\nEn juegos: {vname_shown}")
            self._hw_pill.setObjectName("PillOk")
            self._hz_badge.setText(f"{round(state.poll_hz)} Hz")
            if state.virtual_name and self._config == self._saved_config:
                self._vname_pill_btn.setText(f"En juegos: {state.virtual_name} ✎")
        else:
            self._hw_pill.setText("Sin mando conectado")
            self._hw_pill.setToolTip("")
            self._hw_pill.setObjectName("PillOff")
            self._hz_badge.setText("0 Hz")

        self._hw_pill.style().unpolish(self._hw_pill)
        self._hw_pill.style().polish(self._hw_pill)

        if not self._sim_btn.isChecked():
            self._controller_widget.set_controller_state(state)
            self._update_simple_readout(state)
        if state.physical_stable_id != prev_stable or state.connected_count != prev_count:
            if state.is_wheel or state.controller_family == "wheel":
                self._wheel_sub_lbl.setText(
                    f"Conectado: {state.physical_name or 'Volante'} · Listo para volante, pedales, shifter y freno de mano."
                )
            else:
                self._wheel_sub_lbl.setText(
                    "Interfaz adaptada exclusivamente para volante, pedales, palanca y freno de mano."
                )
            self._refresh_sim_aux_devices()
        self._sync_active_input_mode()
        self._refresh_family_badge()
        if self._circ_dialog is not None:
            self._circ_dialog.feed_state(state)
        if state.supported_extra_keys:
            self._macros_tab.update_detected_extra_keys(state.supported_extra_keys)
        if hasattr(self, "_clips_tab"):
            self._clips_tab.process_controller_state(state)
        self._check_battery_sounds(state)

    def _check_battery_sounds(self, state: ControllerState) -> None:
        if state.battery_level is None:
            return
        dev_id = state.physical_stable_id or state.physical_path or "__default__"
        lvl = max(0, min(100, int(state.battery_level)))
        chg = bool(state.battery_charging)

        if dev_id != self._bat_prev_device_id:
            self._bat_prev_device_id = dev_id
            self._bat_prev_charging = chg
            self._bat_prev_level = lvl
            self._bat_full_notified = lvl >= 100
            self._bat_low_notified = lvl <= 20 and not chg
            if self._bat_low_notified:
                play_battery_low_chime()
            return

        prev_chg = self._bat_prev_charging
        if prev_chg is False and chg:
            self._bat_low_notified = False
            play_battery_charging_chime()
        elif prev_chg is True and not chg:
            play_battery_unplugged_chime()

        if chg and lvl >= 100 and not self._bat_full_notified:
            self._bat_full_notified = True
            play_battery_full_chime()
        elif lvl < 98:
            self._bat_full_notified = False

        if not chg and lvl <= 20 and not self._bat_low_notified:
            self._bat_low_notified = True
            play_battery_low_chime()
        elif chg or lvl >= 25:
            self._bat_low_notified = False

        self._bat_prev_charging = chg
        self._bat_prev_level = lvl

    def _on_clip_notification(self, title: str, msg: str, clip_path: str = "") -> None:
        self.statusBar().showMessage(f"{title}: {msg}", 4500)
        if clip_path:
            self._last_notified_clip_path = clip_path
        if self._notifier is not None:
            nid = self._notifier.notify(title, msg, clip_path, timeout_ms=5000)
            if nid is not None:
                return
        if self._tray is not None and self._tray.isVisible():
            self._tray.showMessage(title, msg, QSystemTrayIcon.Information, 3500)

    def _on_open_clip_from_notification(
        self, clip_path: str = "", activation_token: str = ""
    ) -> None:
        if activation_token:
            os.environ["XDG_ACTIVATION_TOKEN"] = activation_token
        self.setWindowState(
            (self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive
        )
        self.show()
        self.raise_()
        self.activateWindow()
        if hasattr(self, "_main_tabs") and hasattr(self, "_clips_tab_idx"):
            self._main_tabs.setCurrentIndex(self._clips_tab_idx)
        if clip_path and hasattr(self, "_clips_tab"):
            self._clips_tab.select_clip_by_path(clip_path)

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
                ctrl.status_badge.setStyleSheet("color: #64748B; font-size: 11.5px;")
            elif abs(ox) < 250 and abs(oy) < 250:
                ctrl.status_badge.setText("✓ Estable en centro")
                ctrl.status_badge.setStyleSheet(
                    "color: #10B981; font-weight: 600; font-size: 11.5px;"
                )
            else:
                ctrl.status_badge.setText("↗ En movimiento")
                ctrl.status_badge.setStyleSheet(
                    "color: #FBBF24; font-weight: 600; font-size: 11.5px;"
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
        is_wheel_view = state.is_wheel or self._controller_widget.active_family() == "wheel"
        if is_wheel_view and connected:
            deg = (float(state.out_lx) / float(AXIS_MAX)) * (float(self._config.wheel_rotation_deg) * 0.5)
            if abs(deg) >= 1.0:
                pressed.insert(0, f"Giro {'+' if deg > 0 else ''}{deg:.0f}°")
        if lt_pct >= 5:
            pressed.append(("Freno " if is_wheel_view else "LT ") + f"{lt_pct}%")
        if rt_pct >= 5:
            pressed.append(("Gas " if is_wheel_view else "RT ") + f"{rt_pct}%")
        if is_wheel_view:
            hb_pct = round((max(0, state.handbrake) / 1023.0) * 100.0)
            cl_pct = round((max(0, state.clutch) / 1023.0) * 100.0)
            if hb_pct >= 5:
                pressed.append(f"F.Mano {hb_pct}%")
            if cl_pct >= 5:
                pressed.append(f"Embrague {cl_pct}%")
            if state.shifter_gear:
                pressed.append(f"Marcha {state.shifter_gear}")

        if pressed:
            self._active_buttons_label.setText("Pulsando: " + "  ·  ".join(pressed))
        elif connected:
            self._active_buttons_label.setText(
                "Listo · Gira el volante o pisa los pedales para probar."
                if is_wheel_view
                else "Listo · Pulsa botones o mueve las palancas para probar."
            )
        else:
            self._active_buttons_label.setText(
                "Conecta tu mando por USB o Bluetooth (o activa 'Probar sin mando')."
            )

    def _on_toggle_protection(self) -> None:
        action = "stop" if self._service_active else "start"
        self._run_svc(action)

    def _on_fix_sdl_filter(self) -> None:
        vid_pids = [
            d.vid_pid for d in (self._connected_devs_cache or list_physical_controllers()) if d.vid_pid
        ]
        if not vid_pids and self._last_state.physical_vid_pid:
            vid_pids = [self._last_state.physical_vid_pid]
        if not vid_pids:
            ok, msg = ensure_sdl_ignore_device(None)
        else:
            msgs: list[str] = []
            ok = True
            for vp in dict.fromkeys(vid_pids):
                cur_ok, cur_msg = ensure_sdl_ignore_device(vp)
                ok = ok and cur_ok
                msgs.append(cur_msg)
            msg = "\n".join(msgs)
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

        self._app_autostart_chk.blockSignals(True)
        self._app_autostart_chk.setChecked(is_app_autostart_enabled())
        self._app_autostart_chk.blockSignals(False)

        cur_devs = list_physical_controllers()
        if [d.path for d in cur_devs] != [d.path for d in self._connected_devs_cache]:
            self._refresh_device_selector()

    def _run_svc(self, action: str) -> None:
        ok, msg = run_service_action(action)
        self._refresh_service_status()
        self.statusBar().showMessage(msg, 3500)
        if not ok:
            QMessageBox.warning(self, "Servicio Anti-Drift", msg)

    def _on_toggle_autostart(self, checked: bool) -> None:
        self._run_svc("enable" if checked else "disable")

    def _on_toggle_app_autostart(self, checked: bool) -> None:
        ok, msg = set_app_autostart(checked)
        self._app_autostart_chk.blockSignals(True)
        self._app_autostart_chk.setChecked(is_app_autostart_enabled())
        self._app_autostart_chk.blockSignals(False)
        self.statusBar().showMessage(msg, 4000)
        if not ok:
            QMessageBox.warning(self, "Inicio automático", msg)

    def _refresh_logs(self) -> None:
        self._log_view.setPlainText(get_service_logs())

    def _refresh_optional_deps_ui(self) -> None:
        if not hasattr(self, "_opt_deps_rows"):
            return
        while self._opt_deps_rows.count():
            item = self._opt_deps_rows.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        items: list[OptionalDepStatus] = check_optional_dependencies()
        if items:
            d = items[0].distro
            self._opt_distro_lbl.setText(f"{d.pretty_name} ({d.pkg_manager})")

        for dep in items:
            row_f = QFrame()
            row_f.setStyleSheet(
                "background-color: #0B0E14; border: 1px solid #1E2630; border-radius: 6px;"
            )
            rlay = QHBoxLayout(row_f)
            rlay.setContentsMargins(10, 7, 10, 7)
            rlay.setSpacing(8)

            txt_col = QVBoxLayout()
            txt_col.setSpacing(1)
            nlbl = QLabel(dep.name)
            nlbl.setStyleSheet("font-weight: 700; color: #F8FAFC; font-size: 12.5px;")
            dlbl = QLabel(dep.description)
            dlbl.setObjectName("MutedNote")
            dlbl.setWordWrap(True)
            txt_col.addWidget(nlbl)
            txt_col.addWidget(dlbl)
            rlay.addLayout(txt_col, 1)

            if dep.installed:
                st_lbl = QLabel("Instalado")
                st_lbl.setStyleSheet("color: #34D399; font-weight: 700; font-size: 12px;")
                rlay.addWidget(st_lbl)
            else:
                pkg_hint = ", ".join(dep.packages) if dep.packages else dep.distro.pkg_manager
                btn = QPushButton(f"Instalar ({pkg_hint})")
                btn.setObjectName("AccentBtn")
                btn.setEnabled(
                    bool(dep.install_cmd) or dep.id in ("vosk", "gsr")
                )
                btn.clicked.connect(
                    lambda _=False, did=dep.id: self._on_install_optional_dep(did)
                )
                rlay.addWidget(btn)
            self._opt_deps_rows.addWidget(row_f)

    def _on_install_optional_dep(self, dep_id: str) -> None:
        if getattr(self, "_opt_inst_thread", None) is not None:
            return
        self._opt_install_msg.setVisible(True)
        self._opt_install_msg.setText(f"Instalando {dep_id} según tu distribución...")
        th = _OptionalDepInstallThread(dep_id, self)
        th.progress.connect(lambda m: self._opt_install_msg.setText(m))
        th.finished_install.connect(self._on_optional_dep_installed)
        self._opt_inst_thread = th
        th.start()

    def _on_optional_dep_installed(self, dep_id: str, ok: bool, msg: str) -> None:
        self._opt_inst_thread = None
        self._opt_install_msg.setVisible(True)
        self._opt_install_msg.setText(msg)
        self._refresh_optional_deps_ui()
        if hasattr(self, "_mangohud_tab"):
            self._mangohud_tab.refresh_installation_status()
        if hasattr(self, "_clips_tab"):
            self._clips_tab._refresh_install_hints()
            self._clips_tab._populate_devices()
        if ok:
            self.statusBar().showMessage(msg, 5000)
        else:
            QMessageBox.warning(self, f"Instalar {dep_id}", msg)

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
        if hasattr(self, "_mangohud_tab"):
            self._mangohud_tab.refresh_installation_status()
        self._refresh_optional_deps_ui()
        if ok:
            self.statusBar().showMessage(msg, 5000)
        else:
            QMessageBox.warning(self, "Dependencias", msg)

    # ── GitHub Update Manager ──

    def _start_update_check(self, manual: bool = False) -> None:
        if getattr(self, "_upd_check_thread", None) is not None:
            return
        self._upd_check_manual = manual
        self._btn_check_updates.setEnabled(False)
        self._upd_status_lbl.setText("Estado: Consultando GitHub en busca de actualizaciones...")
        th = _UpdateCheckThread(self)
        th.checked.connect(self._on_update_checked)
        self._upd_check_thread = th
        th.start()

    def _on_update_checked(self, result: UpdateCheckResult) -> None:
        self._upd_check_thread = None
        self._btn_check_updates.setEnabled(True)
        self._last_update_result = result

        self._upd_repo_lbl.setText(
            f"Repositorio: {result.repo_url.removeprefix('https://github.com/')} · "
            f"rama {result.branch} · instalado v{result.local_version} ({result.local_short_sha})"
        )

        if result.error:
            self._update_btn.setVisible(False)
            self._btn_apply_update.setVisible(False)
            self._upd_changes_lbl.setVisible(False)
            self._upd_status_lbl.setText(f"Estado: ⚠ {result.error}")
            self._upd_status_lbl.setStyleSheet("color: #FBBF24; font-size: 12.5px;")
            if self._upd_check_manual:
                self.statusBar().showMessage(result.error, 5000)
            return

        if result.update_available:
            count_txt = f" ({result.ahead_by} cambios)" if result.ahead_by > 1 else ""
            ver_tag = f" {result.remote_tag} ·" if result.remote_tag else ""
            self._update_btn.setText(f"↑ Actualizar disponible{count_txt}")
            self._update_btn.setVisible(True)
            self._btn_apply_update.setText(f"Actualizar ahora ({result.remote_short_sha})")
            self._btn_apply_update.setVisible(True)

            headline = result.remote_message or "Nueva versión en GitHub"
            self._upd_status_lbl.setText(
                f"Estado: ↑ Actualización disponible ·{ver_tag} {result.remote_short_sha} — {headline}"
            )
            self._upd_status_lbl.setStyleSheet("color: #34D399; font-size: 12.5px; font-weight: 600;")

            if result.commits:
                self._upd_changes_lbl.setText("\n".join(result.commits))
                self._upd_changes_lbl.setVisible(True)
            else:
                self._upd_changes_lbl.setVisible(False)

            self.statusBar().showMessage(
                f"Nueva actualización disponible en GitHub ({result.remote_short_sha}).",
                6000,
            )
        else:
            self._update_btn.setVisible(False)
            self._btn_apply_update.setVisible(False)
            self._upd_changes_lbl.setVisible(False)
            tail = f" ({result.remote_short_sha})" if result.remote_sha else ""
            self._upd_status_lbl.setText(f"Estado: ✓ Estás al día con GitHub{tail}.")
            self._upd_status_lbl.setStyleSheet("color: #34D399; font-size: 12.5px;")
            if self._upd_check_manual:
                self.statusBar().showMessage("Anti-Drift Studio ya está en la última versión.", 4000)

    def _on_apply_update_clicked(self) -> None:
        if getattr(self, "_upd_apply_thread", None) is not None:
            return
        res = self._last_update_result
        if res is None or not res.update_available:
            self._start_update_check(manual=True)
            return

        details = "\n".join(f"  • {c}" for c in res.commits) if res.commits else (
            f"  • {res.remote_short_sha} — {res.remote_message}"
        )
        ans = QMessageBox.question(
            self,
            "Actualizar Anti-Drift Studio",
            f"Se encontró una actualización en GitHub ({res.branch}):\n\n"
            f"Actual: {res.local_short_sha}  →  Nuevo: {res.remote_short_sha}\n\n"
            f"Cambios recientes:\n{details}\n\n"
            "¿Descargar y aplicar la actualización ahora?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if ans != QMessageBox.Yes:
            return

        self._update_btn.setEnabled(False)
        self._btn_apply_update.setEnabled(False)
        self._btn_check_updates.setEnabled(False)
        self._upd_status_lbl.setText("Estado: Descargando e instalando actualización...")
        self._upd_status_lbl.setStyleSheet("color: #CFD2DA; font-size: 12.5px;")

        th = _UpdateApplyThread(remote_sha=res.remote_sha, parent=self)
        th.progress.connect(self._on_update_progress)
        th.finished_update.connect(self._on_update_applied)
        self._upd_apply_thread = th
        th.start()

    def _on_update_progress(self, msg: str) -> None:
        self._upd_status_lbl.setText(f"Estado: {msg}")
        self.statusBar().showMessage(msg, 4000)

    def _on_update_applied(self, ok: bool, msg: str, restart_rec: bool) -> None:
        self._upd_apply_thread = None
        self._update_btn.setEnabled(True)
        self._btn_apply_update.setEnabled(True)
        self._btn_check_updates.setEnabled(True)

        if not ok:
            self._upd_status_lbl.setText(f"Estado: ⚠ {msg}")
            self._upd_status_lbl.setStyleSheet("color: #FCA5A5; font-size: 12.5px;")
            QMessageBox.warning(self, "Actualización", msg)
            return

        self._update_btn.setVisible(False)
        self._btn_apply_update.setVisible(False)
        self._upd_changes_lbl.setVisible(False)
        self._upd_status_lbl.setText(f"Estado: ✓ {msg}")
        self._upd_status_lbl.setStyleSheet("color: #34D399; font-size: 12.5px;")
        self.statusBar().showMessage(msg, 6000)

        if restart_rec:
            ans = QMessageBox.question(
                self,
                "Actualización completada",
                f"{msg}\n\n¿Reiniciar Anti-Drift Studio ahora para cargar la nueva versión?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if ans == QMessageBox.Yes and restart_application():
                self._quit_completely()
