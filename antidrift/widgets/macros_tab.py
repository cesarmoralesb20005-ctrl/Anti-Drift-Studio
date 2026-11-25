"""Simple, friendly macro & extra-button editor tab (matte editorial UI)."""

from __future__ import annotations

import uuid

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..evdev_reader import ControllerState
from ..macros import (
    DEFAULT_MACROS_PATH,
    STD_BUTTON_NAMES,
    TRIGGER_CATALOG,
    MacroDef,
    MacroStep,
    create_preset_macro,
    format_trigger_name,
    load_macros,
    save_macros,
    short_extra_badge,
)

SIMPLE_MODES: tuple[tuple[str, str], ...] = (
    ("hold_mirror", "Copiar otro botón (Simple)"),
    ("hold_repeat", "Pulsar rápido automático (Turbo)"),
    ("toggle", "Encender / Apagar con un toque"),
    ("sequence", "Combo / Macro paso a paso (Avanzado)"),
)


class MacrosTabWidget(QWidget):
    """Beginner-friendly macro studio supporting extra controller buttons."""

    macros_saved = Signal(bool, str)
    preview_state_changed = Signal(object)  # ControllerState | None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._saved_macros: list[MacroDef] = load_macros()
        self._macros: list[MacroDef] = [m.copy() for m in self._saved_macros]
        self._syncing_editor = False
        self._capturing_trigger = False
        self._detected_extra_keys: list[int] = []

        self._capture_timer = QTimer(self)
        self._capture_timer.setSingleShot(True)
        self._capture_timer.setInterval(6000)
        self._capture_timer.timeout.connect(self._cancel_capture)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._step_preview)
        self._preview_steps: list[MacroStep] = []
        self._preview_idx = 0
        self._preview_state: ControllerState | None = None

        self._build_ui()
        self._reload_list(select_idx=0 if self._macros else -1)
        self._refresh_dirty_label()

    def stop_preview(self) -> None:
        """Stop any running visual macro playback."""
        self._preview_timer.stop()
        self._preview_steps = []
        self._preview_state = None
        self.preview_state_changed.emit(None)

    def update_detected_extra_keys(self, extra_keys: list[int]) -> None:
        """Add hardware-discovered extra key codes to the trigger selector."""
        if extra_keys == self._detected_extra_keys:
            return
        self._detected_extra_keys = list(extra_keys)
        self._populate_trigger_combo(self._trigger_spin.value())

    def on_raw_key_detected(self, code: int) -> None:
        """Capture any pressed physical hardware button while in capture mode."""
        if not self._capturing_trigger:
            return
        self._capturing_trigger = False
        self._capture_timer.stop()
        self._btn_capture.setChecked(False)
        self._btn_capture.setText("Pulsa en tu mando")

        m = self._current_macro()
        if m is not None:
            m.trigger_code = code
        self._trigger_spin.setValue(code)
        self._select_combo_for_code(code)
        self._refresh_current_list_item()
        self._refresh_dirty_label()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 0, 0)
        outer.setSpacing(10)

        # Card 1: Rule List
        list_card = QFrame()
        list_card.setObjectName("Card")
        lc_lay = QVBoxLayout(list_card)
        lc_lay.setContentsMargins(14, 12, 14, 12)
        lc_lay.setSpacing(8)

        hdr = QHBoxLayout()
        title = QLabel("Tus botones extra y macros")
        title.setObjectName("SectionTitle")
        hdr.addWidget(title)
        hdr.addStretch(1)

        btn_new = QPushButton("+ Nuevo")
        btn_new.setObjectName("AccentBtn")
        btn_new.clicked.connect(self._on_add_macro)
        hdr.addWidget(btn_new)

        btn_presets = QPushButton("Ideas listas ▾")
        preset_menu = QMenu(btn_presets)
        for key, text in (
            ("remap_paddle_a", "Copiar botón A en Paddle P1"),
            ("turbo_boost", "Turbo rápido en B (Boost)"),
            ("rl_fast_aerial", "Rocket League: Fast Aerial"),
            ("rl_half_flip", "Rocket League: Half-Flip"),
        ):
            act = preset_menu.addAction(text)
            act.triggered.connect(lambda _=False, k=key: self._on_add_preset(k))
        btn_presets.setMenu(preset_menu)
        hdr.addWidget(btn_presets)

        self._btn_dup = QPushButton("Duplicar")
        self._btn_dup.clicked.connect(self._on_duplicate_macro)
        hdr.addWidget(self._btn_dup)

        self._btn_del = QPushButton("Eliminar")
        self._btn_del.setObjectName("DangerBtn")
        self._btn_del.clicked.connect(self._on_delete_macro)
        hdr.addWidget(self._btn_del)
        lc_lay.addLayout(hdr)

        self._macro_list = QListWidget()
        self._macro_list.setMaximumHeight(124)
        self._macro_list.currentRowChanged.connect(self._on_select_macro)
        self._macro_list.itemChanged.connect(self._on_item_toggled)
        lc_lay.addWidget(self._macro_list)

        outer.addWidget(list_card)

        # Card 2: Plain-language Editor
        self._editor_card = QFrame()
        self._editor_card.setObjectName("Card")
        ed_lay = QVBoxLayout(self._editor_card)
        ed_lay.setContentsMargins(14, 12, 14, 12)
        ed_lay.setSpacing(10)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)

        form.addWidget(QLabel("Nombre:"), 0, 0)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Ej. Paleta trasera -> Saltar")
        self._name_edit.textEdited.connect(self._on_editor_changed)
        form.addWidget(self._name_edit, 0, 1, 1, 3)

        form.addWidget(QLabel("1. Botón físico:"), 1, 0)
        self._trigger_combo = QComboBox()
        self._trigger_combo.currentIndexChanged.connect(self._on_trigger_combo_changed)
        form.addWidget(self._trigger_combo, 1, 1)

        self._trigger_spin = QSpinBox()
        self._trigger_spin.setRange(0, 767)
        self._trigger_spin.setPrefix("#")
        self._trigger_spin.setToolTip("Código interno del botón")
        self._trigger_spin.valueChanged.connect(self._on_trigger_spin_changed)
        form.addWidget(self._trigger_spin, 1, 2)

        self._btn_capture = QPushButton("Pulsa en tu mando")
        self._btn_capture.setCheckable(True)
        self._btn_capture.setObjectName("AccentBtn")
        self._btn_capture.setToolTip(
            "Haz clic aquí y luego presiona cualquier botón o paleta de tu mando físico"
        )
        self._btn_capture.toggled.connect(self._on_toggle_capture)
        form.addWidget(self._btn_capture, 1, 3)

        form.addWidget(QLabel("2. Acción:"), 2, 0)
        self._mode_combo = QComboBox()
        for mode_key, mode_label in SIMPLE_MODES:
            self._mode_combo.addItem(mode_label, mode_key)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        form.addWidget(self._mode_combo, 2, 1, 1, 3)

        self._intercept_chk = QCheckBox(
            "Reemplazar el botón original (evita que el juego haga dos cosas a la vez)"
        )
        self._intercept_chk.toggled.connect(self._on_editor_changed)
        form.addWidget(self._intercept_chk, 3, 0, 1, 4)
        ed_lay.addLayout(form)

        # Action Stack: Simple Remap / Turbo / Advanced Combo
        self._action_stack = QStackedWidget()

        # Stack 0: Simple 1:1 Remap
        remap_page = QWidget()
        rp_lay = QVBoxLayout(remap_page)
        rp_lay.setContentsMargins(0, 4, 0, 0)
        rp_lay.setSpacing(8)
        row_r = QHBoxLayout()
        row_r.addWidget(QLabel("Copiar el botón:"))
        self._simple_btn_combo = QComboBox()
        self._simple_btn_combo.addItems(STD_BUTTON_NAMES)
        self._simple_btn_combo.currentIndexChanged.connect(
            self._sync_simple_target_to_steps
        )
        row_r.addWidget(self._simple_btn_combo, 1)
        self._simple_btn_combo.setToolTip(
            "Mientras mantengas presionado el botón físico, el juego sentirá que presionas el botón elegido."
        )
        rp_lay.addLayout(row_r)
        rp_lay.addStretch(1)
        self._action_stack.addWidget(remap_page)

        # Stack 1: Turbo / Toggle
        turbo_page = QWidget()
        tp_lay = QVBoxLayout(turbo_page)
        tp_lay.setContentsMargins(0, 4, 0, 0)
        tp_lay.setSpacing(10)
        row_t1 = QHBoxLayout()
        row_t1.addWidget(QLabel("Botón a repetir:"))
        self._turbo_btn_combo = QComboBox()
        self._turbo_btn_combo.addItems(STD_BUTTON_NAMES)
        self._turbo_btn_combo.currentIndexChanged.connect(
            self._sync_turbo_target_to_steps
        )
        row_t1.addWidget(self._turbo_btn_combo, 1)
        tp_lay.addLayout(row_t1)

        row_t2 = QHBoxLayout()
        row_t2.addWidget(QLabel("Intervalo:"))
        self._repeat_slider = QSlider(Qt.Horizontal)
        self._repeat_slider.setRange(20, 400)
        self._repeat_slider.setSingleStep(5)
        self._repeat_spin = QSpinBox()
        self._repeat_spin.setRange(10, 5000)
        self._repeat_spin.setSuffix(" ms")
        self._repeat_slider.valueChanged.connect(self._on_repeat_slider_changed)
        self._repeat_spin.valueChanged.connect(self._on_repeat_spin_changed)
        row_t2.addWidget(self._repeat_slider, 1)
        row_t2.addWidget(self._repeat_spin)
        tp_lay.addLayout(row_t2)
        tp_lay.addStretch(1)
        self._action_stack.addWidget(turbo_page)

        # Stack 2: Step-by-step Combo Builder
        combo_page = QWidget()
        cp_lay = QVBoxLayout(combo_page)
        cp_lay.setContentsMargins(0, 2, 0, 0)
        cp_lay.setSpacing(6)

        builder_row = QHBoxLayout()
        builder_row.setSpacing(6)
        self._step_kind_combo = QComboBox()
        for kind_id, label in (
            ("TAP", "Toque rápido"),
            ("KEY_DOWN", "Presionar"),
            ("KEY_UP", "Soltar"),
            ("WAIT", "Esperar (ms)"),
            ("STICK", "Mover palanca"),
            ("STICK_REL", "Soltar palanca"),
            ("TRIG", "Gatillo (LT/RT)"),
            ("HAT", "Cruceta"),
        ):
            self._step_kind_combo.addItem(label, kind_id)
        self._step_kind_combo.currentIndexChanged.connect(self._on_step_kind_changed)
        builder_row.addWidget(self._step_kind_combo)

        self._step_params_stack = self._build_step_param_stack()
        builder_row.addWidget(self._step_params_stack, 1)

        btn_add_step = QPushButton("+ Agregar")
        btn_add_step.setObjectName("AccentBtn")
        btn_add_step.clicked.connect(self._on_add_step)
        builder_row.addWidget(btn_add_step)
        cp_lay.addLayout(builder_row)

        step_body = QHBoxLayout()
        step_body.setSpacing(8)
        self._steps_list = QListWidget()
        step_body.addWidget(self._steps_list, 1)

        step_btns = QVBoxLayout()
        for lbl, slot in (
            ("Subir", lambda: self._move_step(-1)),
            ("Bajar", lambda: self._move_step(1)),
            ("Quitar", self._remove_step),
            ("Limpiar", self._clear_steps),
        ):
            b = QPushButton(lbl)
            b.clicked.connect(slot)
            step_btns.addWidget(b)
        step_btns.addStretch(1)
        step_body.addLayout(step_btns)
        cp_lay.addLayout(step_body, 1)

        self._action_stack.addWidget(combo_page)
        ed_lay.addWidget(self._action_stack, 1)

        outer.addWidget(self._editor_card, 1)

        # Footer
        footer = QFrame()
        footer.setObjectName("Card")
        f_lay = QHBoxLayout(footer)
        f_lay.setContentsMargins(14, 10, 14, 10)
        f_lay.setSpacing(8)

        self._dirty_label = QLabel("Todo guardado")
        f_lay.addWidget(self._dirty_label)
        f_lay.addStretch(1)

        self._btn_test = QPushButton("Ver prueba en mando")
        self._btn_test.clicked.connect(self._on_test_macro_irl)
        f_lay.addWidget(self._btn_test)

        self._btn_revert = QPushButton("Deshacer")
        self._btn_revert.clicked.connect(self._on_revert)
        f_lay.addWidget(self._btn_revert)

        self._btn_apply = QPushButton("Guardar y Aplicar")
        self._btn_apply.setObjectName("PrimaryBtn")
        self._btn_apply.clicked.connect(self._on_save_and_apply)
        f_lay.addWidget(self._btn_apply)

        outer.addWidget(footer)
        self._populate_trigger_combo(704)

    def _build_step_param_stack(self) -> QStackedWidget:
        stack = QStackedWidget()

        p_tap = QWidget()
        l_tap = QHBoxLayout(p_tap)
        l_tap.setContentsMargins(0, 0, 0, 0)
        self._tap_btn_combo = QComboBox()
        self._tap_btn_combo.addItems(STD_BUTTON_NAMES)
        self._tap_ms_spin = QSpinBox()
        self._tap_ms_spin.setRange(10, 2000)
        self._tap_ms_spin.setValue(45)
        self._tap_ms_spin.setSuffix(" ms")
        l_tap.addWidget(self._tap_btn_combo)
        l_tap.addWidget(self._tap_ms_spin)
        stack.addWidget(p_tap)

        p_key = QWidget()
        l_key = QHBoxLayout(p_key)
        l_key.setContentsMargins(0, 0, 0, 0)
        self._key_btn_combo = QComboBox()
        self._key_btn_combo.addItems(STD_BUTTON_NAMES)
        l_key.addWidget(self._key_btn_combo)
        stack.addWidget(p_key)

        p_wait = QWidget()
        l_wait = QHBoxLayout(p_wait)
        l_wait.setContentsMargins(0, 0, 0, 0)
        self._wait_ms_spin = QSpinBox()
        self._wait_ms_spin.setRange(5, 10000)
        self._wait_ms_spin.setValue(50)
        self._wait_ms_spin.setSuffix(" ms")
        l_wait.addWidget(self._wait_ms_spin)
        stack.addWidget(p_wait)

        p_stk = QWidget()
        l_stk = QHBoxLayout(p_stk)
        l_stk.setContentsMargins(0, 0, 0, 0)
        self._stick_which_combo = QComboBox()
        self._stick_which_combo.addItems(["L", "R"])
        self._stick_dir_combo = QComboBox()
        for label, x, y in (
            ("Arriba", 0, -32768),
            ("Abajo", 0, 32767),
            ("Izquierda", -32768, 0),
            ("Derecha", 32767, 0),
            ("Centro", 0, 0),
        ):
            self._stick_dir_combo.addItem(label, (x, y))
        l_stk.addWidget(self._stick_which_combo)
        l_stk.addWidget(self._stick_dir_combo, 1)
        stack.addWidget(p_stk)

        p_srel = QWidget()
        l_srel = QHBoxLayout(p_srel)
        l_srel.setContentsMargins(0, 0, 0, 0)
        self._stick_rel_combo = QComboBox()
        self._stick_rel_combo.addItems(["L", "R"])
        l_srel.addWidget(self._stick_rel_combo)
        stack.addWidget(p_srel)

        p_trig = QWidget()
        l_trig = QHBoxLayout(p_trig)
        l_trig.setContentsMargins(0, 0, 0, 0)
        self._trig_combo = QComboBox()
        self._trig_combo.addItems(["LT", "RT"])
        self._trig_val_spin = QSpinBox()
        self._trig_val_spin.setRange(0, 1023)
        self._trig_val_spin.setValue(1023)
        l_trig.addWidget(self._trig_combo)
        l_trig.addWidget(self._trig_val_spin)
        stack.addWidget(p_trig)

        p_hat = QWidget()
        l_hat = QHBoxLayout(p_hat)
        l_hat.setContentsMargins(0, 0, 0, 0)
        self._hat_dir_combo = QComboBox()
        for label, hx, hy in (
            ("Arriba", 0, -1),
            ("Abajo", 0, 1),
            ("Izquierda", -1, 0),
            ("Derecha", 1, 0),
            ("Neutro", 0, 0),
        ):
            self._hat_dir_combo.addItem(label, (hx, hy))
        l_hat.addWidget(self._hat_dir_combo)
        stack.addWidget(p_hat)

        return stack

    def _on_step_kind_changed(self, _idx: int) -> None:
        kind = self._step_kind_combo.currentData()
        page_map = {
            "TAP": 0,
            "KEY_DOWN": 1,
            "KEY_UP": 1,
            "WAIT": 2,
            "STICK": 3,
            "STICK_REL": 4,
            "TRIG": 5,
            "HAT": 6,
        }
        self._step_params_stack.setCurrentIndex(page_map.get(kind, 0))

    def _populate_trigger_combo(self, current_code: int) -> None:
        self._syncing_editor = True
        try:
            self._trigger_combo.clear()
            seen: set[int] = set()
            for code in self._detected_extra_keys:
                self._trigger_combo.addItem(
                    f"★ {format_trigger_name(code)}", code
                )
                seen.add(code)
            for code, label in TRIGGER_CATALOG:
                if code not in seen:
                    self._trigger_combo.addItem(label, code)
                    seen.add(code)
            if current_code not in seen:
                self._trigger_combo.addItem(
                    format_trigger_name(current_code), current_code
                )
            self._select_combo_for_code(current_code)
        finally:
            self._syncing_editor = False

    def _select_combo_for_code(self, code: int) -> None:
        idx = self._trigger_combo.findData(code)
        if idx < 0:
            self._trigger_combo.addItem(format_trigger_name(code), code)
            idx = self._trigger_combo.findData(code)
        self._trigger_combo.blockSignals(True)
        self._trigger_combo.setCurrentIndex(max(0, idx))
        self._trigger_combo.blockSignals(False)

    def _current_macro(self) -> MacroDef | None:
        row = self._macro_list.currentRow()
        if 0 <= row < len(self._macros):
            return self._macros[row]
        return None

    def _reload_list(self, select_idx: int) -> None:
        self._macro_list.blockSignals(True)
        self._macro_list.clear()
        for m in self._macros:
            item = QListWidgetItem(self._format_macro_row(m))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if m.enabled else Qt.Unchecked)
            self._macro_list.addItem(item)
        self._macro_list.blockSignals(False)

        if 0 <= select_idx < len(self._macros):
            self._macro_list.setCurrentRow(select_idx)
        else:
            self._load_macro_into_editor(None)

    def _format_macro_row(self, m: MacroDef) -> str:
        badge = short_extra_badge(m.trigger_code)
        mode_short = {
            "hold_mirror": "Copiar botón",
            "sequence": "Combo",
            "hold_repeat": "Turbo",
            "toggle": "Alternar",
        }.get(m.mode, m.mode)
        return f"[{badge}]  {m.name}  —  {mode_short}"

    def _refresh_current_list_item(self) -> None:
        row = self._macro_list.currentRow()
        m = self._current_macro()
        if m is not None and 0 <= row < self._macro_list.count():
            item = self._macro_list.item(row)
            self._macro_list.blockSignals(True)
            item.setText(self._format_macro_row(m))
            item.setCheckState(Qt.Checked if m.enabled else Qt.Unchecked)
            self._macro_list.blockSignals(False)

    def _on_select_macro(self, row: int) -> None:
        if 0 <= row < len(self._macros):
            self._load_macro_into_editor(self._macros[row])
        else:
            self._load_macro_into_editor(None)

    def _on_item_toggled(self, item: QListWidgetItem) -> None:
        row = self._macro_list.row(item)
        if 0 <= row < len(self._macros):
            self._macros[row].enabled = item.checkState() == Qt.Checked
            self._refresh_dirty_label()

    def _first_button_in_steps(self, m: MacroDef) -> str:
        for st in m.steps:
            if st.kind == "KEY" and st.arg1 in STD_BUTTON_NAMES:
                return st.arg1
        return "A"

    def _sync_action_stack_page(self, m: MacroDef) -> None:
        if m.mode == "hold_mirror":
            self._action_stack.setCurrentIndex(0)
            btn = self._first_button_in_steps(m)
            self._simple_btn_combo.blockSignals(True)
            self._simple_btn_combo.setCurrentText(btn)
            self._simple_btn_combo.blockSignals(False)
        elif m.mode in ("hold_repeat", "toggle"):
            self._action_stack.setCurrentIndex(1)
            btn = self._first_button_in_steps(m)
            self._turbo_btn_combo.blockSignals(True)
            self._turbo_btn_combo.setCurrentText(btn)
            self._turbo_btn_combo.blockSignals(False)
        else:
            self._action_stack.setCurrentIndex(2)
            self._reload_steps(m)

    def _load_macro_into_editor(self, m: MacroDef | None) -> None:
        has = m is not None
        self._editor_card.setEnabled(has)
        self._btn_dup.setEnabled(has)
        self._btn_del.setEnabled(has)
        self._btn_test.setEnabled(has)
        if m is None:
            self._steps_list.clear()
            return

        self._syncing_editor = True
        try:
            self._name_edit.setText(m.name)
            self._select_combo_for_code(m.trigger_code)
            self._trigger_spin.setValue(m.trigger_code)
            mode_idx = max(0, self._mode_combo.findData(m.mode))
            self._mode_combo.setCurrentIndex(mode_idx)
            self._repeat_spin.setValue(m.repeat_ms)
            self._repeat_slider.setValue(max(20, min(400, m.repeat_ms)))
            self._intercept_chk.setChecked(m.intercept)
            self._sync_action_stack_page(m)
        finally:
            self._syncing_editor = False

    def _reload_steps(self, m: MacroDef) -> None:
        self._steps_list.clear()
        for idx, st in enumerate(m.steps, start=1):
            self._steps_list.addItem(f"{idx}. {st.describe_es()}")

    def _on_mode_changed(self, _idx: int) -> None:
        if self._syncing_editor:
            return
        m = self._current_macro()
        if m is None:
            return
        m.mode = self._mode_combo.currentData() or "sequence"
        if m.mode == "hold_mirror":
            self._sync_simple_target_to_steps()
        elif m.mode in ("hold_repeat", "toggle"):
            self._sync_turbo_target_to_steps()
        self._sync_action_stack_page(m)
        self._refresh_current_list_item()
        self._refresh_dirty_label()

    def _sync_simple_target_to_steps(self) -> None:
        if self._syncing_editor:
            return
        m = self._current_macro()
        if m is None or m.mode != "hold_mirror":
            return
        btn = self._simple_btn_combo.currentText()
        m.steps = [MacroStep("KEY", btn, 1)]
        self._refresh_current_list_item()
        self._refresh_dirty_label()

    def _sync_turbo_target_to_steps(self) -> None:
        if self._syncing_editor:
            return
        m = self._current_macro()
        if m is None or m.mode not in ("hold_repeat", "toggle"):
            return
        btn = self._turbo_btn_combo.currentText()
        m.steps = [
            MacroStep("KEY", btn, 1),
            MacroStep("WAIT", v1=25),
            MacroStep("KEY", btn, 0),
        ]
        self._refresh_current_list_item()
        self._refresh_dirty_label()

    def _on_repeat_slider_changed(self, val: int) -> None:
        if self._syncing_editor:
            return
        self._repeat_spin.blockSignals(True)
        self._repeat_spin.setValue(val)
        self._repeat_spin.blockSignals(False)
        m = self._current_macro()
        if m is not None:
            m.repeat_ms = val
            self._refresh_current_list_item()
            self._refresh_dirty_label()

    def _on_repeat_spin_changed(self, val: int) -> None:
        if self._syncing_editor:
            return
        self._repeat_slider.blockSignals(True)
        self._repeat_slider.setValue(max(20, min(400, val)))
        self._repeat_slider.blockSignals(False)
        m = self._current_macro()
        if m is not None:
            m.repeat_ms = val
            self._refresh_current_list_item()
            self._refresh_dirty_label()

    def _on_editor_changed(self) -> None:
        if self._syncing_editor:
            return
        m = self._current_macro()
        if m is None:
            return
        m.name = self._name_edit.text().strip() or "Sin nombre"
        m.intercept = self._intercept_chk.isChecked()
        self._refresh_current_list_item()
        self._refresh_dirty_label()

    def _on_trigger_combo_changed(self, _idx: int) -> None:
        if self._syncing_editor:
            return
        code = self._trigger_combo.currentData()
        if code is None:
            return
        self._syncing_editor = True
        self._trigger_spin.setValue(int(code))
        self._syncing_editor = False
        m = self._current_macro()
        if m is not None:
            m.trigger_code = int(code)
            self._refresh_current_list_item()
            self._refresh_dirty_label()

    def _on_trigger_spin_changed(self, val: int) -> None:
        if self._syncing_editor:
            return
        self._select_combo_for_code(val)
        m = self._current_macro()
        if m is not None:
            m.trigger_code = int(val)
            self._refresh_current_list_item()
            self._refresh_dirty_label()

    def _on_toggle_capture(self, checked: bool) -> None:
        self._capturing_trigger = checked
        if checked:
            self._btn_capture.setText("Esperando botón (6s)...")
            self._capture_timer.start()
        else:
            self._capture_timer.stop()
            self._btn_capture.setText("Pulsa en tu mando")

    def _cancel_capture(self) -> None:
        if self._capturing_trigger:
            self._capturing_trigger = False
            self._btn_capture.setChecked(False)
            self._btn_capture.setText("Pulsa en tu mando")

    def _on_add_macro(self) -> None:
        next_code = min(719, 704 + len(self._macros))
        m = create_preset_macro("remap_paddle_a", trigger_code=next_code)
        m.id = f"macro_{uuid.uuid4().hex[:8]}"
        m.name = f"Botón extra {len(self._macros) + 1}"
        self._macros.append(m)
        self._reload_list(select_idx=len(self._macros) - 1)
        self._refresh_dirty_label()

    def _on_add_preset(self, preset_key: str) -> None:
        next_code = min(719, 704 + len(self._macros))
        m = create_preset_macro(preset_key, trigger_code=next_code)
        m.id = f"{preset_key}_{uuid.uuid4().hex[:6]}"
        self._macros.append(m)
        self._reload_list(select_idx=len(self._macros) - 1)
        self._refresh_dirty_label()

    def _on_duplicate_macro(self) -> None:
        m = self._current_macro()
        if m is None:
            return
        dup = m.copy()
        dup.id = f"macro_{uuid.uuid4().hex[:8]}"
        dup.name = f"{m.name} (copia)"
        self._macros.append(dup)
        self._reload_list(select_idx=len(self._macros) - 1)
        self._refresh_dirty_label()

    def _on_delete_macro(self) -> None:
        row = self._macro_list.currentRow()
        if not (0 <= row < len(self._macros)):
            return
        self._macros.pop(row)
        self._reload_list(select_idx=min(row, len(self._macros) - 1))
        self._refresh_dirty_label()

    def _on_add_step(self) -> None:
        m = self._current_macro()
        if m is None:
            return
        kind = self._step_kind_combo.currentData()
        new_steps: list[MacroStep] = []
        if kind == "TAP":
            btn = self._tap_btn_combo.currentText()
            ms = self._tap_ms_spin.value()
            new_steps = [
                MacroStep("KEY", btn, 1),
                MacroStep("WAIT", v1=ms),
                MacroStep("KEY", btn, 0),
            ]
        elif kind == "KEY_DOWN":
            new_steps = [MacroStep("KEY", self._key_btn_combo.currentText(), 1)]
        elif kind == "KEY_UP":
            new_steps = [MacroStep("KEY", self._key_btn_combo.currentText(), 0)]
        elif kind == "WAIT":
            new_steps = [MacroStep("WAIT", v1=self._wait_ms_spin.value())]
        elif kind == "STICK":
            sx, sy = self._stick_dir_combo.currentData()
            new_steps = [
                MacroStep("STICK", self._stick_which_combo.currentText(), sx, sy)
            ]
        elif kind == "STICK_REL":
            new_steps = [MacroStep("STICK_REL", self._stick_rel_combo.currentText())]
        elif kind == "TRIG":
            new_steps = [
                MacroStep(
                    "TRIG", self._trig_combo.currentText(), self._trig_val_spin.value()
                )
            ]
        elif kind == "HAT":
            hx, hy = self._hat_dir_combo.currentData()
            new_steps = [MacroStep("HAT", v1=hx, v2=hy)]

        if len(m.steps) + len(new_steps) > 64:
            QMessageBox.warning(
                self, "Límite alcanzado", "Máximo 64 pasos por macro."
            )
            return

        m.steps.extend(new_steps)
        self._reload_steps(m)
        self._refresh_current_list_item()
        self._refresh_dirty_label()

    def _move_step(self, delta: int) -> None:
        m = self._current_macro()
        row = self._steps_list.currentRow()
        if m is None or not (0 <= row < len(m.steps)):
            return
        target = row + delta
        if not (0 <= target < len(m.steps)):
            return
        m.steps[row], m.steps[target] = m.steps[target], m.steps[row]
        self._reload_steps(m)
        self._steps_list.setCurrentRow(target)
        self._refresh_dirty_label()

    def _remove_step(self) -> None:
        m = self._current_macro()
        row = self._steps_list.currentRow()
        if m is None or not (0 <= row < len(m.steps)):
            return
        m.steps.pop(row)
        self._reload_steps(m)
        if m.steps:
            self._steps_list.setCurrentRow(min(row, len(m.steps) - 1))
        self._refresh_current_list_item()
        self._refresh_dirty_label()

    def _clear_steps(self) -> None:
        m = self._current_macro()
        if m is None or not m.steps:
            return
        m.steps.clear()
        self._reload_steps(m)
        self._refresh_current_list_item()
        self._refresh_dirty_label()

    def _refresh_dirty_label(self) -> None:
        dirty = self._macros != self._saved_macros
        if dirty:
            self._dirty_label.setText("Cambios sin aplicar")
            self._dirty_label.setStyleSheet(
                "color: #C59B57; font-weight: 600; font-size: 12px;"
            )
        else:
            self._dirty_label.setText("Todo guardado")
            self._dirty_label.setStyleSheet("color: #639E7E; font-size: 12px;")
        self._btn_revert.setEnabled(dirty)

    def _on_revert(self) -> None:
        self._macros = [m.copy() for m in self._saved_macros]
        self._reload_list(select_idx=0 if self._macros else -1)
        self._refresh_dirty_label()

    def _on_save_and_apply(self) -> None:
        save_macros(self._macros)
        self._saved_macros = [m.copy() for m in self._macros]
        self._refresh_dirty_label()
        self.macros_saved.emit(
            True, f"{len(self._macros)} macro(s) guardadas en {DEFAULT_MACROS_PATH}"
        )

    def _on_test_macro_irl(self) -> None:
        m = self._current_macro()
        if m is None or not m.steps:
            return
        self.stop_preview()
        self._preview_steps = list(m.steps)
        self._preview_idx = 0
        self._preview_state = ControllerState(
            mode="simulated",
            extra_buttons={m.trigger_code: True}
            if m.trigger_code not in range(304, 319)
            else {},
        )
        self.preview_state_changed.emit(self._preview_state)
        self._step_preview()

    def _step_preview(self) -> None:
        if self._preview_state is None:
            return
        while self._preview_idx < len(self._preview_steps):
            st = self._preview_steps[self._preview_idx]
            self._preview_idx += 1
            if st.kind == "KEY":
                self._preview_state.buttons[st.arg1] = bool(st.v1)
            elif st.kind == "HAT":
                self._preview_state.hat_x = st.v1
                self._preview_state.hat_y = st.v2
            elif st.kind == "TRIG":
                if st.arg1 == "LT":
                    self._preview_state.lt = st.v1
                else:
                    self._preview_state.rt = st.v1
            elif st.kind == "STICK":
                if st.arg1 == "L":
                    self._preview_state.out_lx = st.v1
                    self._preview_state.out_ly = st.v2
                else:
                    self._preview_state.out_rx = st.v1
                    self._preview_state.out_ry = st.v2
            elif st.kind == "STICK_REL":
                if st.arg1 == "L":
                    self._preview_state.out_lx = 0
                    self._preview_state.out_ly = 0
                else:
                    self._preview_state.out_rx = 0
                    self._preview_state.out_ry = 0
            elif st.kind == "WAIT":
                self.preview_state_changed.emit(self._preview_state)
                self._preview_timer.start(max(20, min(1500, st.v1)))
                return

        self.preview_state_changed.emit(self._preview_state)
        if self._preview_idx >= len(self._preview_steps):
            self._preview_timer.timeout.disconnect(self._step_preview)
            self._preview_timer.timeout.connect(self._finish_preview_once)
            self._preview_timer.start(450)

    def _finish_preview_once(self) -> None:
        try:
            self._preview_timer.timeout.disconnect(self._finish_preview_once)
        except RuntimeError:
            pass
        self._preview_timer.timeout.connect(self._step_preview)
        self.stop_preview()
