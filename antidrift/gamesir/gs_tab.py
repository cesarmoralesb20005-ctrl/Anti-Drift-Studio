"""GameSir Nova 2 Lite hardware configuration tab (paddles, triggers, D-pad, vibration, battery)."""

from __future__ import annotations

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..widgets.fit_scroll import FitWidthScrollArea
from .nova2lite import (
    PADDLE_TARGETS,
    GameSirDeviceInfo,
    GameSirHardwareConfig,
    apply_nova2lite_settings,
    discover_gamesir_devices,
    install_gamesir_udev_rule,
    load_gamesir_config,
    save_gamesir_config,
    test_nova2lite_rumble,
)

_VIB_LEVELS: tuple[int, ...] = (0, 25, 50, 75, 100)


class _UdevInstallThread(QThread):
    finished_install = Signal(bool, str)

    def run(self) -> None:
        ok, msg = install_gamesir_udev_rule()
        self.finished_install.emit(ok, msg)


class GameSirTabWidget(QWidget):
    """Hardware configuration panel for GameSir Nova 2 Lite (0xFF7A vendor HID)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._config: GameSirHardwareConfig = load_gamesir_config()
        self._devices: list[GameSirDeviceInfo] = []
        self._syncing = False
        self._udev_thread: _UdevInstallThread | None = None
        self._build_ui()
        self._apply_config_to_widgets()
        self.refresh_devices()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 6, 0, 0)
        outer.setSpacing(8)

        scroll = FitWidthScrollArea()
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(10)

        # Card 1: Hardware status, battery, firmware, udev permission
        hw_card = QFrame()
        hw_card.setObjectName("Card")
        hl = QHBoxLayout(hw_card)
        hl.setContentsMargins(14, 10, 14, 10)
        hl.setSpacing(10)

        txt_col = QVBoxLayout()
        txt_col.setSpacing(2)
        title = QLabel("GameSir Nova 2 Lite (Control por hardware HID)")
        title.setObjectName("SectionTitle")
        self._status_lbl = QLabel("Buscando mando GameSir...")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setObjectName("MutedNote")
        txt_col.addWidget(title)
        txt_col.addWidget(self._status_lbl)
        hl.addLayout(txt_col, 1)

        self._btn_udev = QPushButton("Habilitar acceso HID (udev)")
        self._btn_udev.setObjectName("AccentBtn")
        self._btn_udev.setVisible(False)
        self._btn_udev.clicked.connect(self._on_install_udev)
        hl.addWidget(self._btn_udev)

        btn_refresh = QPushButton("Actualizar")
        btn_refresh.setObjectName("MiniPill")
        btn_refresh.clicked.connect(self.refresh_devices)
        hl.addWidget(btn_refresh)
        lay.addWidget(hw_card)

        # Card 2: Back Paddles L4 / R4
        pad_card = QFrame()
        pad_card.setObjectName("Card")
        pl = QVBoxLayout(pad_card)
        pl.setContentsMargins(14, 10, 14, 10)
        pl.setSpacing(8)

        ptitle = QLabel("Botones traseros (L4 y R4)")
        ptitle.setObjectName("SectionTitle")
        pl.addWidget(ptitle)
        pnote = QLabel(
            "Reasigna las paletas traseras L4 y R4 directamente en la memoria interna del mando."
        )
        pnote.setObjectName("MutedNote")
        pnote.setWordWrap(True)
        pl.addWidget(pnote)

        pgrid = QGridLayout()
        pgrid.setHorizontalSpacing(12)
        pgrid.setVerticalSpacing(8)

        pgrid.addWidget(QLabel("Paleta izquierda (L4):"), 0, 0)
        self._l4_combo = QComboBox()
        for label in PADDLE_TARGETS:
            self._l4_combo.addItem(label, label)
        self._l4_combo.currentIndexChanged.connect(self._on_widgets_changed)
        pgrid.addWidget(self._l4_combo, 0, 1)

        pgrid.addWidget(QLabel("Paleta derecha (R4):"), 1, 0)
        self._r4_combo = QComboBox()
        for label in PADDLE_TARGETS:
            self._r4_combo.addItem(label, label)
        self._r4_combo.currentIndexChanged.connect(self._on_widgets_changed)
        pgrid.addWidget(self._r4_combo, 1, 1)
        pl.addLayout(pgrid)
        lay.addWidget(pad_card)

        # Card 3: Triggers, D-pad & Vibration
        tun_card = QFrame()
        tun_card.setObjectName("Card")
        tl = QVBoxLayout(tun_card)
        tl.setContentsMargins(14, 10, 14, 10)
        tl.setSpacing(8)

        ttitle = QLabel("Vibración, gatillos rápidos y cruceta")
        ttitle.setObjectName("SectionTitle")
        tl.addWidget(ttitle)

        vgrid = QGridLayout()
        vgrid.setHorizontalSpacing(10)
        vgrid.setVerticalSpacing(6)

        vgrid.addWidget(QLabel("Vibración izquierda:"), 0, 0)
        self._vib_l_combo = QComboBox()
        for v in _VIB_LEVELS:
            self._vib_l_combo.addItem(f"{v}%", v)
        self._vib_l_combo.currentIndexChanged.connect(self._on_widgets_changed)
        vgrid.addWidget(self._vib_l_combo, 0, 1)

        vgrid.addWidget(QLabel("Vibración derecha:"), 1, 0)
        self._vib_r_combo = QComboBox()
        for v in _VIB_LEVELS:
            self._vib_r_combo.addItem(f"{v}%", v)
        self._vib_r_combo.currentIndexChanged.connect(self._on_widgets_changed)
        vgrid.addWidget(self._vib_r_combo, 1, 1)

        self._btn_rumble = QPushButton("Probar vibración")
        self._btn_rumble.setObjectName("MiniPill")
        self._btn_rumble.clicked.connect(self._on_test_rumble)
        vgrid.addWidget(self._btn_rumble, 0, 2, 2, 1)
        tl.addLayout(vgrid)

        self._hair_lt_chk = QCheckBox("Hair-Trigger físico en LT (activación instantánea)")
        self._hair_lt_chk.toggled.connect(self._on_widgets_changed)
        self._hair_rt_chk = QCheckBox("Hair-Trigger físico en RT (activación instantánea)")
        self._hair_rt_chk.toggled.connect(self._on_widgets_changed)
        self._dpad_lock_chk = QCheckBox("Bloquear cruceta a 4 direcciones (sin diagonales)")
        self._dpad_lock_chk.toggled.connect(self._on_widgets_changed)
        self._dpad_swap_chk = QCheckBox("Intercambiar cruceta con stick izquierdo")
        self._dpad_swap_chk.toggled.connect(self._on_widgets_changed)

        for chk in (
            self._hair_lt_chk,
            self._hair_rt_chk,
            self._dpad_lock_chk,
            self._dpad_swap_chk,
        ):
            tl.addWidget(chk)

        led_note = QLabel(
            "Nota sobre el botón Home: en el Nova 2 Lite (T4N2) el LED central indica el modo de conexión "
            "(verde en dongle 2.4G / XInput, azul en Bluetooth, amarillo en Switch); el firmware no expone "
            "canales RGB programables por software."
        )
        led_note.setObjectName("MutedNote")
        led_note.setWordWrap(True)
        tl.addWidget(led_note)
        lay.addWidget(tun_card)

        lay.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # Footer
        footer = QFrame()
        footer.setObjectName("Card")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(14, 10, 14, 10)
        fl.setSpacing(8)
        self._save_lbl = QLabel("Configura tu mando GameSir y pulsa Aplicar.")
        self._save_lbl.setWordWrap(True)
        self._save_lbl.setObjectName("MutedNote")
        fl.addWidget(self._save_lbl, 1)
        self._btn_apply = QPushButton("Aplicar al mando GameSir")
        self._btn_apply.setObjectName("PrimaryBtn")
        self._btn_apply.clicked.connect(self.save_and_apply)
        fl.addWidget(self._btn_apply)
        outer.addWidget(footer)

    def _active_device(self) -> GameSirDeviceInfo | None:
        return self._devices[0] if self._devices else None

    def refresh_devices(self) -> None:
        self._devices = discover_gamesir_devices()
        dev = self._active_device()
        if dev is None:
            self._status_lbl.setText(
                "No se detectó ningún mando GameSir por HID (conéctalo por dongle 2.4G o cable USB)."
            )
            self._status_lbl.setStyleSheet("color: #94A3B8; font-size: 12.5px;")
            self._btn_udev.setVisible(False)
            self._btn_rumble.setEnabled(False)
            return

        if not dev.has_permission:
            self._status_lbl.setText(
                f"Detectado: {dev.model_name} ({dev.hidraw_path}) · "
                "Falta permiso de usuario en hidraw para enviar ajustes."
            )
            self._status_lbl.setStyleSheet("color: #FBBF24; font-size: 12.5px;")
            self._btn_udev.setVisible(True)
            self._btn_rumble.setEnabled(False)
            return

        parts = [dev.model_name, dev.transport]
        if dev.firmware:
            parts.append(f"FW {dev.firmware}")
        if dev.battery is not None:
            chg = " (cargando)" if dev.charging else ""
            parts.append(f"Batería: {dev.battery}%{chg}")
        self._status_lbl.setText(" · ".join(parts))
        self._status_lbl.setStyleSheet("color: #34D399; font-size: 12.5px;")
        self._btn_udev.setVisible(False)
        self._btn_rumble.setEnabled(True)

    def _on_install_udev(self) -> None:
        if self._udev_thread is not None:
            return
        self._btn_udev.setEnabled(False)
        self._status_lbl.setText("Instalando regla udev para GameSir (0x3537)...")
        th = _UdevInstallThread(self)
        th.finished_install.connect(self._on_udev_finished)
        self._udev_thread = th
        th.start()

    def _on_udev_finished(self, ok: bool, msg: str) -> None:
        self._udev_thread = None
        self._btn_udev.setEnabled(True)
        self.refresh_devices()
        self._save_lbl.setText(msg)
        if not ok:
            QMessageBox.warning(self, "Permiso HID GameSir", msg)

    def _apply_config_to_widgets(self) -> None:
        self._syncing = True
        try:
            c = self._config
            for combo, target in ((self._l4_combo, c.l4_target), (self._r4_combo, c.r4_target)):
                for i in range(combo.count()):
                    if combo.itemData(i) == target:
                        combo.setCurrentIndex(i)
                        break
            for combo, val in ((self._vib_l_combo, c.vib_left), (self._vib_r_combo, c.vib_right)):
                for i in range(combo.count()):
                    if combo.itemData(i) == val:
                        combo.setCurrentIndex(i)
                        break
            self._hair_lt_chk.setChecked(c.hair_trigger_lt)
            self._hair_rt_chk.setChecked(c.hair_trigger_rt)
            self._dpad_lock_chk.setChecked(c.dpad_4way_lock)
            self._dpad_swap_chk.setChecked(c.dpad_swap_lstick)
        finally:
            self._syncing = False

    def _on_widgets_changed(self) -> None:
        if self._syncing:
            return
        self._config.l4_target = str(self._l4_combo.currentData() or "Sin cambio")
        self._config.r4_target = str(self._r4_combo.currentData() or "Sin cambio")
        self._config.vib_left = int(self._vib_l_combo.currentData() or 0)
        self._config.vib_right = int(self._vib_r_combo.currentData() or 0)
        self._config.hair_trigger_lt = self._hair_lt_chk.isChecked()
        self._config.hair_trigger_rt = self._hair_rt_chk.isChecked()
        self._config.dpad_4way_lock = self._dpad_lock_chk.isChecked()
        self._config.dpad_swap_lstick = self._dpad_swap_chk.isChecked()

    def _on_test_rumble(self) -> None:
        dev = self._active_device()
        if dev is None:
            self._save_lbl.setText("Conecta tu mando GameSir para probar la vibración.")
            return
        ok, msg = test_nova2lite_rumble(dev.hidraw_path, self._config.vib_left, self._config.vib_right)
        self._save_lbl.setText(msg)
        if not ok:
            self._save_lbl.setStyleSheet("color: #FBBF24; font-size: 12px;")

    def save_and_apply(self) -> None:
        self._config = self._config.clamped()
        save_gamesir_config(self._config)
        dev = self._active_device()
        if dev is None:
            self._save_lbl.setStyleSheet("color: #94A3B8; font-size: 12px;")
            self._save_lbl.setText("Configuración guardada. Conecta el mando GameSir para aplicarla.")
            return
        ok, msg = apply_nova2lite_settings(dev.hidraw_path, self._config)
        self._save_lbl.setStyleSheet(
            "color: #34D399; font-size: 12px;" if ok else "color: #FBBF24; font-size: 12px;"
        )
        self._save_lbl.setText(msg)
