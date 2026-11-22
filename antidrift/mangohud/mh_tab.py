"""Studio UI tab for configuring MangoHud overlays and assigning them to any window."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QThread, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..deps import detect_distro, has_mangohud, install_optional_dependency
from ..widgets.fit_scroll import FitWidthScrollArea
from .mh_backend import (
    DesktopAppInfo,
    DetectedWindow,
    discover_open_windows,
    launch_command_with_mangohud,
    launch_demo_window_with_mangohud,
    list_desktop_apps,
    mangohud_version,
    pick_window_with_click,
    sync_flatpak_mangohud,
    sync_mangohud_system_files,
    toggle_live_mangohud,
)
from .mh_config import (
    MANGOHUD_PRESETS,
    MangoHudConfig,
    MangoHudWindowRule,
    apply_mangohud_preset,
    load_mangohud_app_config,
    sanitize_process_name,
    save_mangohud_app_config,
)

_POS_LABELS: tuple[tuple[str, str], ...] = (
    ("top-left", "Sup. Izq"),
    ("top-center", "Sup. Centro"),
    ("top-right", "Sup. Der"),
    ("bottom-left", "Inf. Izq"),
    ("bottom-center", "Inf. Centro"),
    ("bottom-right", "Inf. Der"),
)

_METRIC_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Rendimiento",
        (
            ("fps", "FPS actuales"),
            ("frame_timing", "Gráfica Frametime"),
            ("fps_metrics", "1% Low y Promedio"),
            ("show_fps_limit", "Mostrar tope FPS"),
            ("resolution", "Resolución ventana"),
            ("gamemode", "Estado GameMode"),
        ),
    ),
    (
        "Tarjeta Gráfica (GPU)",
        (
            ("gpu_stats", "Uso GPU (%)"),
            ("gpu_temp", "Temp GPU (°C)"),
            ("gpu_core_clock", "Reloj núcleo GPU"),
            ("gpu_mem_clock", "Reloj memoria GPU"),
            ("gpu_power", "Consumo GPU (W)"),
            ("vram", "Uso VRAM"),
            ("gpu_name", "Modelo GPU"),
        ),
    ),
    (
        "Procesador y Sistema",
        (
            ("cpu_stats", "Uso CPU (%)"),
            ("cpu_temp", "Temp CPU (°C)"),
            ("cpu_mhz", "Frecuencia CPU (MHz)"),
            ("cpu_power", "Consumo CPU (W)"),
            ("core_load", "Carga por núcleo"),
            ("ram", "Memoria RAM"),
            ("swap", "Memoria Swap"),
            ("wine", "Versión Wine / Proton"),
            ("vulkan_driver", "Driver Vulkan / Mesa"),
            ("io_stats", "Lectura / Escritura disco"),
            ("time", "Reloj actual"),
        ),
    ),
)


class _MhInstallThread(QThread):
    progress = Signal(str)
    finished_install = Signal(bool, str)

    def run(self) -> None:
        ok, msg = install_optional_dependency("mangohud", log_cb=self.progress.emit)
        self.finished_install.emit(ok, msg)


class _WindowPickThread(QThread):
    picked = Signal(object, str)

    def run(self) -> None:
        win, msg = pick_window_with_click()
        self.picked.emit(win, msg)


class MangoHudPreviewWidget(QWidget):
    """Compact live visual mockup of the MangoHud overlay inside a game viewport."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(300, 185)
        self.setMaximumHeight(210)
        self._cfg = MangoHudConfig()

    def set_preview_config(self, cfg: MangoHudConfig) -> None:
        self._cfg = cfg.copy()
        self.update()

    def _hud_lines(self) -> list[tuple[str, str, QColor]]:
        c = self._cfg
        rows: list[tuple[str, str, QColor]] = []
        if c.gpu_name:
            rows.append(("GPU", "RX 580 2048SP", QColor("#10B981")))
        if c.gpu_stats or c.gpu_temp or c.gpu_core_clock or c.gpu_power:
            parts = []
            if c.gpu_stats:
                parts.append("78%")
            if c.gpu_temp:
                parts.append("63°C")
            if c.gpu_core_clock:
                parts.append("1150MHz")
            if c.gpu_power:
                parts.append("118W")
            rows.append(("GPU", "  ".join(parts), QColor("#34D399")))
        if c.vram:
            rows.append(("VRAM", "2.4 GB", QColor("#38BDF8")))
        if c.cpu_stats or c.cpu_temp or c.cpu_mhz or c.cpu_power:
            parts = []
            if c.cpu_stats:
                parts.append("46%")
            if c.cpu_temp:
                parts.append("54°C")
            if c.cpu_mhz:
                parts.append("4.34GHz")
            if c.cpu_power:
                parts.append("42W")
            rows.append(("CPU", "  ".join(parts), QColor("#F59E0B")))
        if c.ram:
            rows.append(("RAM", "8.6 GB" + (" | SW 0.9" if c.swap else ""), QColor("#E2E8F0")))
        if c.fps:
            fps_val = f"{min(60, c.fps_limit) if c.fps_limit > 0 else 60} FPS"
            extra = "  16.6ms" if c.frame_timing else ""
            if c.fps_metrics:
                extra += "  1% 56"
            rows.append(("FPS", f"{fps_val}{extra}", QColor("#F8FAFC")))
        if c.wine:
            rows.append(("PROTON", "DW-11.0", QColor("#A78BFA")))
        if c.resolution:
            rows.append(("RES", "1920x1080", QColor("#94A3B8")))
        if c.gamemode:
            rows.append(("GAMEMODE", "ON", QColor("#10B981")))
        if not rows:
            rows.append(("HUD", "Sin métricas", QColor("#94A3B8")))
        return rows[: (5 if c.horizontal else 8)]

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)

        outer = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setPen(QPen(QColor("#232934"), 1.2))
        p.setBrush(QColor("#0A0D12"))
        p.drawRoundedRect(outer, 10, 10)

        # Subtle viewport horizon grid decoration
        p.setPen(QPen(QColor(255, 255, 255, 16), 1.0))
        p.drawLine(QPointF(outer.left() + 12, outer.center().y()), QPointF(outer.right() - 12, outer.center().y()))
        p.drawEllipse(outer.center(), 36.0, 22.0)

        p.setPen(QColor("#475569"))
        p.setFont(QFont("JetBrains Mono", 8))
        mode_tag = {
            "off": "MODO MANUAL",
            "selective": "SELECTIVO (MIS VENTANAS)",
            "global": "GLOBAL (TODO 3D)",
        }.get(self._cfg.session_mode, "")
        p.drawText(outer.adjusted(10, 6, -10, -6), Qt.AlignBottom | Qt.AlignHCenter, f"Vista previa · {mode_tag}")

        rows = self._hud_lines()
        scale = max(0.72, min(1.25, float(self._cfg.font_size) / 22.0))
        margin = 4.0 if self._cfg.no_margin else 11.0

        if self._cfg.horizontal:
            text = "   ".join(f"{k}:{v}" for k, v, _ in rows[:5])
            box_w = min(outer.width() - margin * 2.0, max(165.0, len(text) * 5.2 * scale))
            box_h = 26.0 * scale
        else:
            box_w = min(outer.width() * 0.62, 168.0 * scale)
            line_h = 15.0 * scale
            graph_h = 22.0 if self._cfg.frame_timing else 0.0
            box_h = min(outer.height() - margin * 2.0, len(rows) * line_h + 14.0 + graph_h)

        pos = self._cfg.position
        if "left" in pos:
            bx = outer.left() + margin
        elif "right" in pos:
            bx = outer.right() - margin - box_w
        else:
            bx = outer.center().x() - box_w * 0.5
        by = outer.top() + margin if pos.startswith("top") else outer.bottom() - margin - box_h - 14.0
        box = QRectF(bx, by, box_w, box_h)

        bg = QColor(12, 15, 20, round(self._cfg.background_alpha * 235))
        p.setPen(QPen(QColor(16, 185, 129, round(self._cfg.alpha * 130)), 1.0))
        p.setBrush(bg)
        p.drawRoundedRect(box, float(self._cfg.round_corners), float(self._cfg.round_corners))

        p.setFont(QFont("JetBrains Mono", max(7, round(8.2 * scale)), QFont.Bold))
        if self._cfg.horizontal:
            x_cur = box.left() + 8.0
            for k, v, col in rows[:5]:
                col_a = QColor(col)
                col_a.setAlpha(round(self._cfg.alpha * 255))
                p.setPen(col_a)
                seg = f"{k} {v}"
                p.drawText(QRectF(x_cur, box.top(), box.right() - x_cur - 6.0, box.height()), Qt.AlignVCenter | Qt.AlignLeft, seg)
                x_cur += (len(seg) + 2) * 5.5 * scale
                if x_cur > box.right() - 20.0:
                    break
            return

        y_cur = box.top() + 7.0
        line_h = 15.0 * scale
        for k, v, col in rows:
            if y_cur + line_h > box.bottom() - (22.0 if self._cfg.frame_timing else 4.0):
                break
            col_a = QColor(col)
            col_a.setAlpha(round(self._cfg.alpha * 255))
            p.setPen(col_a)
            p.drawText(QRectF(box.left() + 8, y_cur, 58 * scale, line_h), Qt.AlignVCenter | Qt.AlignLeft, k)
            fg = QColor("#F1F5F9")
            fg.setAlpha(round(self._cfg.alpha * 255))
            p.setPen(fg)
            p.drawText(QRectF(box.left() + 56 * scale, y_cur, box.width() - 64 * scale, line_h), Qt.AlignVCenter | Qt.AlignRight, v)
            y_cur += line_h

        if self._cfg.frame_timing and y_cur + 16.0 <= box.bottom():
            g_top = box.bottom() - 18.0
            p.setPen(QPen(QColor("#10B981"), 1.4))
            pts = [
                QPointF(box.left() + 8 + i * ((box.width() - 16) / 14.0), g_top + (5.0 if i % 3 == 0 else (2.0 if i % 2 else 8.0)))
                for i in range(15)
            ]
            for idx in range(len(pts) - 1):
                p.drawLine(pts[idx], pts[idx + 1])


class _DesktopAppPickerDialog(QDialog):
    """Searchable picker for installed .desktop GUI apps."""

    def __init__(self, apps: list[DesktopAppInfo], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Elegir aplicación instalada")
        self.resize(460, 420)
        self._apps = apps
        self.selected_app: DesktopAppInfo | None = None

        lay = QVBoxLayout(self)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Buscar juego o programa...")
        self._search.textChanged.connect(self._populate)
        lay.addWidget(self._search)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda _it: self._accept_current())
        lay.addWidget(self._list, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("Añadir ventana / app")
        btn_ok.setObjectName("PrimaryBtn")
        btn_ok.clicked.connect(self._accept_current)
        row.addWidget(btn_cancel)
        row.addWidget(btn_ok)
        lay.addLayout(row)
        self._populate("")

    def _populate(self, query: str) -> None:
        q = query.strip().lower()
        self._list.clear()
        for app in self._apps:
            if q and q not in app.name.lower() and q not in app.process_name.lower():
                continue
            item = QListWidgetItem(f"{app.name}   ({app.process_name})")
            item.setData(Qt.UserRole, app)
            self._list.addItem(item)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _accept_current(self) -> None:
        cur = self._list.currentItem()
        if cur is not None:
            self.selected_app = cur.data(Qt.UserRole)
            self.accept()


class MangoHudTabWidget(QWidget):
    """Complete MangoHud overlay designer, window rule manager, and launcher."""

    dependencies_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._config = load_mangohud_app_config()
        self._syncing = False
        self._detected_windows: list[DetectedWindow] = []
        self._install_thread: _MhInstallThread | None = None
        self._pick_thread: _WindowPickThread | None = None
        self._build_ui()
        self._apply_config_to_widgets()
        self.refresh_installation_status()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 6, 0, 0)
        outer.setSpacing(8)

        scroll = FitWidthScrollArea()
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(10)

        # Card 0: Installation status & distro installer
        status_card = QFrame()
        status_card.setObjectName("Card")
        st_lay = QHBoxLayout(status_card)
        st_lay.setContentsMargins(14, 10, 14, 10)
        st_lay.setSpacing(10)
        st_txt = QVBoxLayout()
        st_txt.setSpacing(2)
        st_title = QLabel("MangoHud (Overlay de rendimiento en vivo)")
        st_title.setObjectName("SectionTitle")
        self._install_status_lbl = QLabel("Comprobando MangoHud...")
        self._install_status_lbl.setWordWrap(True)
        self._install_status_lbl.setObjectName("MutedNote")
        st_txt.addWidget(st_title)
        st_txt.addWidget(self._install_status_lbl)
        st_lay.addLayout(st_txt, 1)

        self._btn_install_mh = QPushButton("Instalar MangoHud")
        self._btn_install_mh.setObjectName("AccentBtn")
        self._btn_install_mh.clicked.connect(self._on_install_mangohud)
        st_lay.addWidget(self._btn_install_mh)
        lay.addWidget(status_card)

        # Row 1: Session Mode + Live Preview side-by-side
        top_split = QHBoxLayout()
        top_split.setSpacing(10)

        mode_card = QFrame()
        mode_card.setObjectName("Card")
        mlay = QVBoxLayout(mode_card)
        mlay.setContentsMargins(14, 10, 14, 10)
        mlay.setSpacing(8)
        m_title = QLabel("1. Cómo ponerlo en tus ventanas")
        m_title.setObjectName("SectionTitle")
        mlay.addWidget(m_title)

        self._mode_btns: dict[str, QPushButton] = {}
        for mid, mlabel, mtip in (
            ("selective", "Solo mis ventanas", "Activa MANGOHUD=1 en sesión pero lo oculta fuera de tu lista"),
            ("global", "Global (Todo 3D)", "Muestra MangoHud en cualquier ventana Vulkan/OpenGL que abras"),
            ("off", "Solo al lanzar aquí / Steam", "No activa MANGOHUD=1 global; úsalo con Lanzar o mangohud %command%"),
        ):
            b = QPushButton(mlabel)
            b.setObjectName("MiniPill")
            b.setCheckable(True)
            b.setToolTip(mtip)
            b.clicked.connect(lambda _=False, k=mid: self._on_session_mode_selected(k))
            self._mode_btns[mid] = b
            mlay.addWidget(b)

        self._flatpak_chk = QCheckBox("Habilitar también para apps Flatpak")
        self._flatpak_chk.toggled.connect(self._on_flatpak_toggled)
        mlay.addWidget(self._flatpak_chk)

        act_row = QHBoxLayout()
        act_row.setSpacing(6)
        btn_demo = QPushButton("▶ Probar en ventana demo")
        btn_demo.setObjectName("AccentBtn")
        btn_demo.clicked.connect(self._on_launch_demo)
        btn_toggle_live = QPushButton("Mostrar/Ocultar en vivo")
        btn_toggle_live.setObjectName("MiniPill")
        btn_toggle_live.clicked.connect(self._on_toggle_live)
        btn_copy_steam = QPushButton("Copiar para Steam")
        btn_copy_steam.setObjectName("MiniPill")
        btn_copy_steam.clicked.connect(self._on_copy_steam_cmd)
        act_row.addWidget(btn_demo)
        act_row.addWidget(btn_toggle_live)
        act_row.addWidget(btn_copy_steam)
        mlay.addLayout(act_row)
        mlay.addStretch(1)

        prev_card = QFrame()
        prev_card.setObjectName("Card")
        pv_lay = QVBoxLayout(prev_card)
        pv_lay.setContentsMargins(12, 10, 12, 10)
        pv_lay.setSpacing(6)
        pv_title = QLabel("Vista previa en vivo")
        pv_title.setObjectName("SectionTitle")
        pv_lay.addWidget(pv_title)
        self._preview = MangoHudPreviewWidget()
        pv_lay.addWidget(self._preview, 1)

        top_split.addWidget(mode_card, 1)
        top_split.addWidget(prev_card, 1)
        lay.addLayout(top_split)

        # Card 2: Target Any Window / Process Rules
        win_card = QFrame()
        win_card.setObjectName("Card")
        wlay = QVBoxLayout(win_card)
        wlay.setContentsMargins(14, 10, 14, 10)
        wlay.setSpacing(8)

        win_hdr = QHBoxLayout()
        win_title = QLabel("2. Ventanas y juegos elegidos (pon MangoHud en cualquier ventana)")
        win_title.setObjectName("SectionTitle")
        win_hdr.addWidget(win_title)
        win_hdr.addStretch(1)
        wlay.addLayout(win_hdr)

        win_tools = QHBoxLayout()
        win_tools.setSpacing(6)
        self._btn_pick_click = QPushButton("🎯 Elegir ventana con clic")
        self._btn_pick_click.setObjectName("AccentBtn")
        self._btn_pick_click.setToolTip("Haz clic en cualquier ventana abierta en pantalla para agregarla")
        self._btn_pick_click.clicked.connect(self._on_pick_window_click)

        btn_scan = QPushButton("↻ Detectar ventanas abiertas")
        btn_scan.setObjectName("MiniPill")
        btn_scan.clicked.connect(self.refresh_open_windows)

        btn_add_desktop = QPushButton("+ App instalada")
        btn_add_desktop.setObjectName("MiniPill")
        btn_add_desktop.clicked.connect(self._on_add_desktop_app)

        btn_add_bin = QPushButton("+ Ejecutable / Proceso")
        btn_add_bin.setObjectName("MiniPill")
        btn_add_bin.clicked.connect(self._on_add_custom_process)

        for b in (self._btn_pick_click, btn_scan, btn_add_desktop, btn_add_bin):
            win_tools.addWidget(b)
        win_tools.addStretch(1)
        wlay.addLayout(win_tools)

        self._detected_label = QLabel("Ventanas abiertas detectadas (haz clic para agregar a tu lista o lanzar):")
        self._detected_label.setObjectName("MutedNote")
        wlay.addWidget(self._detected_label)

        det_row = QHBoxLayout()
        det_row.setSpacing(6)
        self._detected_combo = QComboBox()
        self._detected_combo.setMinimumWidth(260)
        det_row.addWidget(self._detected_combo, 1)
        btn_add_det = QPushButton("+ Añadir ventana seleccionada")
        btn_add_det.setObjectName("MiniPill")
        btn_add_det.clicked.connect(self._on_add_detected_window)
        det_row.addWidget(btn_add_det)
        wlay.addLayout(det_row)

        self._rules_empty_lbl = QLabel(
            "Aún no agregas ventanas específicas. Captura una ventana arriba o añade tu juego (.exe / app)."
        )
        self._rules_empty_lbl.setObjectName("MutedNote")
        self._rules_empty_lbl.setWordWrap(True)
        wlay.addWidget(self._rules_empty_lbl)

        self._rules_container = QVBoxLayout()
        self._rules_container.setSpacing(6)
        wlay.addLayout(self._rules_container)
        lay.addWidget(win_card)

        # Card 3: HUD Design, Presets & Position
        des_card = QFrame()
        des_card.setObjectName("Card")
        dlay = QVBoxLayout(des_card)
        dlay.setContentsMargins(14, 10, 14, 10)
        dlay.setSpacing(8)
        d_title = QLabel("3. Diseño, presets y posición")
        d_title.setObjectName("SectionTitle")
        dlay.addWidget(d_title)

        pr_row = QHBoxLayout()
        pr_row.setSpacing(6)
        pr_row.addWidget(QLabel("Preset:"))
        self._preset_btns: dict[str, QPushButton] = {}
        for pname in MANGOHUD_PRESETS:
            b = QPushButton(pname)
            b.setObjectName("MiniPill")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, n=pname: self._on_preset_clicked(n))
            self._preset_btns[pname] = b
            pr_row.addWidget(b)
        pr_row.addStretch(1)
        dlay.addLayout(pr_row)

        pos_row = QHBoxLayout()
        pos_row.setSpacing(6)
        pos_row.addWidget(QLabel("Posición:"))
        self._pos_btns: dict[str, QPushButton] = {}
        for pk, plbl in _POS_LABELS:
            b = QPushButton(plbl)
            b.setObjectName("MiniPill")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, p=pk: self._on_position_selected(p))
            self._pos_btns[pk] = b
            pos_row.addWidget(b)
        pos_row.addStretch(1)
        dlay.addLayout(pos_row)

        style_grid = QGridLayout()
        style_grid.setHorizontalSpacing(10)
        style_grid.setVerticalSpacing(6)

        style_grid.addWidget(QLabel("Tamaño fuente:"), 0, 0)
        self._font_slider = QSlider(Qt.Horizontal)
        self._font_slider.setRange(14, 42)
        self._font_lbl = QLabel("22 px")
        self._font_slider.valueChanged.connect(self._on_style_changed)
        style_grid.addWidget(self._font_slider, 0, 1)
        style_grid.addWidget(self._font_lbl, 0, 2)

        style_grid.addWidget(QLabel("Opacidad fondo:"), 1, 0)
        self._bg_alpha_slider = QSlider(Qt.Horizontal)
        self._bg_alpha_slider.setRange(0, 100)
        self._bg_alpha_lbl = QLabel("50%")
        self._bg_alpha_slider.valueChanged.connect(self._on_style_changed)
        style_grid.addWidget(self._bg_alpha_slider, 1, 1)
        style_grid.addWidget(self._bg_alpha_lbl, 1, 2)

        style_grid.addWidget(QLabel("Bordes redondeados:"), 2, 0)
        self._corners_slider = QSlider(Qt.Horizontal)
        self._corners_slider.setRange(0, 16)
        self._corners_lbl = QLabel("8 px")
        self._corners_slider.valueChanged.connect(self._on_style_changed)
        style_grid.addWidget(self._corners_slider, 2, 1)
        style_grid.addWidget(self._corners_lbl, 2, 2)

        style_grid.addWidget(QLabel("Límite FPS global:"), 3, 0)
        self._fps_limit_spin = QSpinBox()
        self._fps_limit_spin.setRange(0, 360)
        self._fps_limit_spin.setSpecialValueText("Sin límite (0)")
        self._fps_limit_spin.setSuffix(" FPS")
        self._fps_limit_spin.valueChanged.connect(self._on_style_changed)
        style_grid.addWidget(self._fps_limit_spin, 3, 1)

        fps_q_row = QHBoxLayout()
        for fval, flbl in ((0, "Ilimitado"), (60, "60"), (120, "120"), (144, "144")):
            qb = QPushButton(flbl)
            qb.setObjectName("MiniPill")
            qb.clicked.connect(lambda _=False, fv=fval: self._fps_limit_spin.setValue(fv))
            fps_q_row.addWidget(qb)
        style_grid.addLayout(fps_q_row, 3, 2)
        dlay.addLayout(style_grid)

        opt_row = QHBoxLayout()
        opt_row.setSpacing(14)
        self._horiz_chk = QCheckBox("Barra horizontal compacta")
        self._horiz_chk.toggled.connect(self._on_style_changed)
        self._no_margin_chk = QCheckBox("Pegar al borde (sin margen)")
        self._no_margin_chk.toggled.connect(self._on_style_changed)
        opt_row.addWidget(self._horiz_chk)
        opt_row.addWidget(self._no_margin_chk)
        opt_row.addStretch(1)
        dlay.addLayout(opt_row)
        lay.addWidget(des_card)

        # Card 4: Metrics Grid
        met_card = QFrame()
        met_card.setObjectName("Card")
        met_lay = QVBoxLayout(met_card)
        met_lay.setContentsMargins(14, 10, 14, 10)
        met_lay.setSpacing(8)
        met_title = QLabel("4. Métricas visibles y teclas rápidas")
        met_title.setObjectName("SectionTitle")
        met_lay.addWidget(met_title)

        cols = QHBoxLayout()
        cols.setSpacing(12)
        self._metric_chks: dict[str, QCheckBox] = {}
        for group_title, items in _METRIC_GROUPS:
            col = QVBoxLayout()
            col.setSpacing(4)
            gl = QLabel(group_title)
            gl.setStyleSheet("color: #E2E8F0; font-weight: 700; font-size: 12px;")
            col.addWidget(gl)
            for key, label in items:
                chk = QCheckBox(label)
                chk.toggled.connect(lambda checked, k=key: self._on_metric_toggled(k, checked))
                self._metric_chks[key] = chk
                col.addWidget(chk)
            col.addStretch(1)
            cols.addLayout(col, 1)
        met_lay.addLayout(cols)

        hk_row = QHBoxLayout()
        hk_row.setSpacing(8)
        hk_row.addWidget(QLabel("Mostrar/Ocultar:"))
        self._hk_toggle_edit = QLineEdit()
        self._hk_toggle_edit.textChanged.connect(self._on_hotkeys_changed)
        hk_row.addWidget(self._hk_toggle_edit)
        hk_row.addWidget(QLabel("Recargar:"))
        self._hk_reload_edit = QLineEdit()
        self._hk_reload_edit.textChanged.connect(self._on_hotkeys_changed)
        hk_row.addWidget(self._hk_reload_edit)
        hk_row.addWidget(QLabel("Tope FPS:"))
        self._hk_fps_edit = QLineEdit()
        self._hk_fps_edit.textChanged.connect(self._on_hotkeys_changed)
        hk_row.addWidget(self._hk_fps_edit)
        met_lay.addLayout(hk_row)
        lay.addWidget(met_card)

        lay.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # Footer Save / Apply
        footer = QFrame()
        footer.setObjectName("Card")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(14, 10, 14, 10)
        fl.setSpacing(8)
        self._save_status_lbl = QLabel("Configura tu HUD y pulsa Guardar y aplicar.")
        self._save_status_lbl.setObjectName("MutedNote")
        fl.addWidget(self._save_status_lbl, 1)
        btn_apply = QPushButton("Guardar y aplicar MangoHud")
        btn_apply.setObjectName("PrimaryBtn")
        btn_apply.clicked.connect(self.save_and_apply)
        fl.addWidget(btn_apply)
        outer.addWidget(footer)

        self.refresh_open_windows()

    # ── Status & Distro Installation ──

    def refresh_installation_status(self) -> None:
        distro = detect_distro()
        installed = has_mangohud()
        if installed:
            ver = mangohud_version()
            self._install_status_lbl.setText(
                f"✓ MangoHud listo ({ver}) · Distro detectada: {distro.pretty_name} ({distro.pkg_manager})"
            )
            self._install_status_lbl.setStyleSheet("color: #34D399; font-size: 12.5px;")
            self._btn_install_mh.setText("✓ MangoHud instalado")
            self._btn_install_mh.setEnabled(False)
        else:
            self._install_status_lbl.setText(
                f"⚠ MangoHud no está instalado · Distro: {distro.pretty_name} ({distro.pkg_manager}). "
                "Puedes instalarlo en 1 clic aquí o desde Ajustes → Dependencias opcionales."
            )
            self._install_status_lbl.setStyleSheet("color: #FBBF24; font-size: 12.5px;")
            self._btn_install_mh.setText(f"Instalar MangoHud ({distro.pkg_manager})")
            self._btn_install_mh.setEnabled(True)

    def _on_install_mangohud(self) -> None:
        if self._install_thread is not None:
            return
        self._btn_install_mh.setEnabled(False)
        self._install_status_lbl.setText("Instalando MangoHud con el gestor de tu distro...")
        th = _MhInstallThread(self)
        th.progress.connect(lambda m: self._install_status_lbl.setText(m))
        th.finished_install.connect(self._on_mangohud_installed)
        self._install_thread = th
        th.start()

    def _on_mangohud_installed(self, ok: bool, msg: str) -> None:
        self._install_thread = None
        self.refresh_installation_status()
        self.dependencies_changed.emit()
        if ok:
            self._save_status_lbl.setText(msg)
        else:
            QMessageBox.warning(self, "Instalar MangoHud", msg)

    # ── Window & Process Selection ──

    def refresh_open_windows(self) -> None:
        self._detected_windows = discover_open_windows()
        self._detected_combo.clear()
        if not self._detected_windows:
            self._detected_combo.addItem("No se detectaron ventanas activas (abre un juego o app)", None)
            return
        for win in self._detected_windows:
            hud_tag = " · HUD activo" if win.hud_loaded else ""
            label = f"{win.title}  [{win.process_name}]  ({win.api_hint}{hud_tag})"
            self._detected_combo.addItem(label, win)

    def _add_or_enable_rule(self, title: str, process_name: str, exec_cmd: str = "") -> None:
        pname = sanitize_process_name(process_name)
        if not pname:
            return
        for existing in self._config.window_rules:
            if existing.process_name.lower() == pname.lower():
                existing.enabled = True
                if title:
                    existing.title = title
                if exec_cmd and not existing.exec_cmd:
                    existing.exec_cmd = exec_cmd
                self._rebuild_rules_list()
                return
        self._config.window_rules.append(
            MangoHudWindowRule(
                id=f"win_{len(self._config.window_rules) + 1}_{pname}",
                title=title or pname,
                process_name=pname,
                exec_cmd=exec_cmd,
                enabled=True,
            )
        )
        self._rebuild_rules_list()

    def _on_pick_window_click(self) -> None:
        if self._pick_thread is not None:
            return
        self._btn_pick_click.setEnabled(False)
        self._btn_pick_click.setText("Haz clic en la ventana objetivo...")
        th = _WindowPickThread(self)
        th.picked.connect(self._on_window_picked)
        self._pick_thread = th
        th.start()

    def _on_window_picked(self, win: DetectedWindow | None, msg: str) -> None:
        self._pick_thread = None
        self._btn_pick_click.setEnabled(True)
        self._btn_pick_click.setText("🎯 Elegir ventana con clic")
        if win is None:
            self._save_status_lbl.setText(msg)
            self.refresh_open_windows()
            return
        self._add_or_enable_rule(win.title, win.process_name, win.exec_cmd)
        self.save_and_apply()
        self._save_status_lbl.setText(msg)

    def _on_add_detected_window(self) -> None:
        win = self._detected_combo.currentData()
        if not isinstance(win, DetectedWindow):
            return
        self._add_or_enable_rule(win.title, win.process_name, win.exec_cmd)
        self._save_status_lbl.setText(
            f"Ventana '{win.process_name}' añadida. Pulsa 'Guardar y aplicar MangoHud'."
        )

    def _on_add_desktop_app(self) -> None:
        apps = list_desktop_apps()
        if not apps:
            QMessageBox.information(self, "Aplicaciones", "No se encontraron accesos .desktop.")
            return
        dlg = _DesktopAppPickerDialog(apps, self)
        if dlg.exec() == QDialog.Accepted and dlg.selected_app is not None:
            app = dlg.selected_app
            self._add_or_enable_rule(app.name, app.process_name, app.exec_cmd)
            self._save_status_lbl.setText(f"App '{app.name}' añadida.")

    def _on_add_custom_process(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Elegir ejecutable (o cancela para escribir el nombre del proceso)",
            "",
        )
        if file_path:
            pname = sanitize_process_name(file_path)
            self._add_or_enable_rule(pname, pname, file_path)
            return
        txt, ok = QInputDialog.getText(
            self,
            "Añadir proceso / ventana",
            "Nombre exacto del proceso o ejecutable (ej. azimuth.exe, vkcube, firefox):",
        )
        if ok and txt.strip():
            pname = sanitize_process_name(txt)
            self._add_or_enable_rule(pname, pname, "")

    def _rebuild_rules_list(self) -> None:
        while self._rules_container.count():
            item = self._rules_container.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        has_rules = bool(self._config.window_rules)
        self._rules_empty_lbl.setVisible(not has_rules)
        for idx, rule in enumerate(self._config.window_rules):
            row_frame = QFrame()
            row_frame.setStyleSheet(
                "background-color: #0B0E14; border: 1px solid #1E2630; border-radius: 7px;"
            )
            rlay = QHBoxLayout(row_frame)
            rlay.setContentsMargins(10, 6, 10, 6)
            rlay.setSpacing(8)

            chk = QCheckBox(f"{rule.title}  ({rule.process_name})")
            chk.setChecked(rule.enabled)
            chk.toggled.connect(lambda val, i=idx: self._on_rule_enabled(i, val))
            rlay.addWidget(chk, 1)

            pos_combo = QComboBox()
            pos_combo.addItem("Posición: Global", "")
            for pk, plbl in _POS_LABELS:
                pos_combo.addItem(plbl, pk)
            for ci in range(pos_combo.count()):
                if pos_combo.itemData(ci) == rule.override_position:
                    pos_combo.setCurrentIndex(ci)
                    break
            pos_combo.currentIndexChanged.connect(
                lambda _ci, i=idx, c=pos_combo: self._on_rule_pos_changed(i, str(c.currentData() or ""))
            )
            rlay.addWidget(pos_combo)

            fps_combo = QComboBox()
            for fv, fl in ((-1, "FPS: Global"), (0, "Ilimitado"), (60, "60 FPS"), (120, "120 FPS"), (144, "144 FPS")):
                fps_combo.addItem(fl, fv)
            if rule.override_fps_limit not in (-1, 0, 60, 120, 144):
                fps_combo.addItem(f"{rule.override_fps_limit} FPS", rule.override_fps_limit)
            for ci in range(fps_combo.count()):
                if fps_combo.itemData(ci) == rule.override_fps_limit:
                    fps_combo.setCurrentIndex(ci)
                    break
            fps_combo.currentIndexChanged.connect(
                lambda _ci, i=idx, c=fps_combo: self._on_rule_fps_changed(i, int(c.currentData()))
            )
            rlay.addWidget(fps_combo)

            btn_launch = QPushButton("▶ Lanzar")
            btn_launch.setObjectName("MiniPill")
            btn_launch.setEnabled(bool(rule.exec_cmd or not rule.process_name.lower().endswith(".exe")))
            btn_launch.clicked.connect(lambda _=False, r=rule: self._on_launch_rule(r))
            rlay.addWidget(btn_launch)

            btn_del = QPushButton("✕")
            btn_del.setObjectName("MiniPill")
            btn_del.clicked.connect(lambda _=False, i=idx: self._on_remove_rule(i))
            rlay.addWidget(btn_del)

            self._rules_container.addWidget(row_frame)

    def _on_rule_enabled(self, idx: int, enabled: bool) -> None:
        if 0 <= idx < len(self._config.window_rules):
            self._config.window_rules[idx].enabled = bool(enabled)

    def _on_rule_pos_changed(self, idx: int, pos: str) -> None:
        if 0 <= idx < len(self._config.window_rules):
            self._config.window_rules[idx].override_position = pos

    def _on_rule_fps_changed(self, idx: int, fps: int) -> None:
        if 0 <= idx < len(self._config.window_rules):
            self._config.window_rules[idx].override_fps_limit = fps

    def _on_remove_rule(self, idx: int) -> None:
        if 0 <= idx < len(self._config.window_rules):
            self._config.window_rules.pop(idx)
            self._rebuild_rules_list()

    def _on_launch_rule(self, rule: MangoHudWindowRule) -> None:
        cmd = rule.exec_cmd or rule.process_name
        ok, msg = launch_command_with_mangohud(cmd, self._config, rule=rule)
        self._save_status_lbl.setText(msg)
        if not ok:
            QMessageBox.warning(self, "Lanzar con MangoHud", msg)

    # ── Styling & Metrics Slots ──

    def _apply_config_to_widgets(self) -> None:
        self._syncing = True
        try:
            c = self._config
            for k, btn in self._mode_btns.items():
                btn.setChecked(k == c.session_mode)
            self._flatpak_chk.setChecked(c.flatpak_enabled)
            for pname, btn in self._preset_btns.items():
                btn.setChecked(pname == c.preset_name)
            for pk, btn in self._pos_btns.items():
                btn.setChecked(pk == c.position)
            self._font_slider.setValue(c.font_size)
            self._font_lbl.setText(f"{c.font_size} px")
            op = round(c.background_alpha * 100)
            self._bg_alpha_slider.setValue(op)
            self._bg_alpha_lbl.setText(f"{op}%")
            self._corners_slider.setValue(c.round_corners)
            self._corners_lbl.setText(f"{c.round_corners} px")
            self._fps_limit_spin.setValue(c.fps_limit)
            self._horiz_chk.setChecked(c.horizontal)
            self._no_margin_chk.setChecked(c.no_margin)
            for k, chk in self._metric_chks.items():
                chk.setChecked(bool(getattr(c, k, False)))
            self._hk_toggle_edit.setText(c.toggle_hud)
            self._hk_reload_edit.setText(c.reload_cfg)
            self._hk_fps_edit.setText(c.toggle_fps_limit)
            self._rebuild_rules_list()
        finally:
            self._syncing = False
        self._preview.set_preview_config(self._config)

    def _on_session_mode_selected(self, mode_id: str) -> None:
        if self._syncing:
            return
        self._config.session_mode = mode_id
        for k, btn in self._mode_btns.items():
            btn.blockSignals(True)
            btn.setChecked(k == mode_id)
            btn.blockSignals(False)
        self._preview.set_preview_config(self._config)

    def _on_flatpak_toggled(self, checked: bool) -> None:
        if self._syncing:
            return
        self._config.flatpak_enabled = bool(checked)
        ok, msg = sync_flatpak_mangohud(bool(checked))
        self._save_status_lbl.setText(msg)
        if not ok and checked:
            self._flatpak_chk.blockSignals(True)
            self._flatpak_chk.setChecked(False)
            self._flatpak_chk.blockSignals(False)
            self._config.flatpak_enabled = False

    def _on_preset_clicked(self, name: str) -> None:
        if self._syncing:
            return
        self._config = apply_mangohud_preset(self._config, name)
        self._apply_config_to_widgets()

    def _on_position_selected(self, pos_key: str) -> None:
        if self._syncing:
            return
        self._config.position = pos_key
        for k, btn in self._pos_btns.items():
            btn.blockSignals(True)
            btn.setChecked(k == pos_key)
            btn.blockSignals(False)
        self._preview.set_preview_config(self._config)

    def _on_style_changed(self) -> None:
        if self._syncing:
            return
        self._config.font_size = self._font_slider.value()
        self._font_lbl.setText(f"{self._config.font_size} px")
        op = self._bg_alpha_slider.value()
        self._config.background_alpha = round(op / 100.0, 2)
        self._bg_alpha_lbl.setText(f"{op}%")
        self._config.round_corners = self._corners_slider.value()
        self._corners_lbl.setText(f"{self._config.round_corners} px")
        self._config.fps_limit = self._fps_limit_spin.value()
        self._config.horizontal = self._horiz_chk.isChecked()
        self._config.no_margin = self._no_margin_chk.isChecked()
        self._preview.set_preview_config(self._config)

    def _on_metric_toggled(self, key: str, checked: bool) -> None:
        if self._syncing:
            return
        setattr(self._config, key, bool(checked))
        self._config.preset_name = "Personalizado"
        for btn in self._preset_btns.values():
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.blockSignals(False)
        self._preview.set_preview_config(self._config)

    def _on_hotkeys_changed(self) -> None:
        if self._syncing:
            return
        self._config.toggle_hud = self._hk_toggle_edit.text().strip() or "Shift_R+F12"
        self._config.reload_cfg = self._hk_reload_edit.text().strip() or "Shift_L+F4"
        self._config.toggle_fps_limit = self._hk_fps_edit.text().strip() or "Shift_L+F1"

    def _on_launch_demo(self) -> None:
        self.save_and_apply()
        ok, msg = launch_demo_window_with_mangohud(self._config)
        self._save_status_lbl.setText(msg)
        if not ok:
            QMessageBox.warning(self, "Ventana Demo", msg)

    def _on_toggle_live(self) -> None:
        ok, msg = toggle_live_mangohud(None)
        self._save_status_lbl.setText(msg)
        if not ok:
            self._save_status_lbl.setText(msg)

    def _on_copy_steam_cmd(self) -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText("mangohud %command%")
        self._save_status_lbl.setText("Copiado al portapapeles: mangohud %command%")

    def save_and_apply(self) -> None:
        """Persist JSON config, write MangoHud.conf + per-window .conf files, and reload live."""
        self._config = self._config.clamped()
        save_mangohud_app_config(self._config)
        written = sync_mangohud_system_files(self._config)
        n_rules = sum(1 for r in self._config.window_rules if r.enabled)
        self._save_status_lbl.setText(
            f"✓ MangoHud guardado ({len(written)} archivos, {n_rules} ventanas activas)."
        )
