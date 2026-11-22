"""Studio UI tab for configuring MangoHud overlays and assigning them to any window."""

from __future__ import annotations

import math
from collections import deque

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
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
    LiveSystemTelemetry,
    LiveTelemetrySampler,
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
    ("top-left", "Arriba Izq."),
    ("top-center", "Arriba Centro"),
    ("top-right", "Arriba Der."),
    ("bottom-left", "Abajo Izq."),
    ("bottom-center", "Abajo Centro"),
    ("bottom-right", "Abajo Der."),
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


class _NoWheelFilter(QObject):
    """Ignore mouse wheel scrolling on unfocused spinboxes/sliders/combos inside scroll areas."""

    def eventFilter(self, obj, event):  # type: ignore[override]
        if event.type() == QEvent.Wheel and isinstance(obj, QWidget) and not obj.hasFocus():
            event.ignore()
            return True
        return super().eventFilter(obj, event)


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
    """Live hardware-backed preview of the MangoHud overlay inside an animated viewport."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(340, 225)
        self.setMaximumHeight(265)
        self._cfg = MangoHudConfig()
        self._sampler = LiveTelemetrySampler()
        self._telemetry: LiveSystemTelemetry = self._sampler.sample(check_gamemode=False)
        self._tick = 0
        self._frametimes: deque[float] = deque([16.6] * 24, maxlen=24)
        self._last_fps = 60
        self._live_gamemode = False
        self._timer = QTimer(self)
        self._timer.setInterval(450)
        self._timer.timeout.connect(self._on_tick)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._timer.isActive():
            self._on_tick()
            self._timer.start()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._timer.stop()
        super().hideEvent(event)

    def set_preview_config(self, cfg: MangoHudConfig) -> None:
        self._cfg = cfg.copy()
        self.update()

    def _on_tick(self) -> None:
        self._tick += 1
        check_gm = self._cfg.gamemode and (self._tick % 6 == 1)
        snap = self._sampler.sample(check_gamemode=check_gm)
        if check_gm:
            self._live_gamemode = snap.gamemode_active
        self._telemetry = snap

        lim = self._cfg.fps_limit
        base_fps = min(360, lim) if lim > 0 else 60
        if lim > 0:
            jitter = int(round(math.sin(self._tick * 0.9) * (0 if lim <= 30 else 1)))
        else:
            jitter = int(round(math.sin(self._tick * 0.7) * 2 + math.cos(self._tick * 1.3)))
        cur_fps = max(1, min(lim if lim > 0 else 240, base_fps + jitter))
        self._last_fps = cur_fps
        ft = round(1000.0 / float(cur_fps), 1)
        self._frametimes.append(ft)
        self.update()

    def _hud_lines(self) -> list[tuple[str, str, QColor]]:
        c = self._cfg
        t = self._telemetry
        rows: list[tuple[str, str, QColor]] = []

        if c.gpu_name:
            rows.append(("GPU", t.gpu_name, QColor("#10B981")))
        if c.gpu_stats or c.gpu_temp or c.gpu_core_clock or c.gpu_mem_clock or c.gpu_power:
            parts: list[str] = []
            if c.gpu_stats:
                parts.append(f"{t.gpu_percent if t.gpu_percent is not None else '--'}%")
            if c.gpu_temp:
                parts.append(f"{t.gpu_temp_c if t.gpu_temp_c is not None else '--'}°C")
            if c.gpu_core_clock and t.gpu_core_mhz is not None:
                parts.append(f"{t.gpu_core_mhz}MHz")
            if c.gpu_mem_clock and t.gpu_mem_mhz is not None:
                parts.append(f"M{t.gpu_mem_mhz}")
            if c.gpu_power and t.gpu_power_w is not None:
                parts.append(f"{t.gpu_power_w}W")
            if parts:
                rows.append(("GPU", " ".join(parts), QColor("#34D399")))
        if c.vram:
            vram_txt = f"{t.vram_used_gb:.1f} GB" if t.vram_used_gb is not None else "-- GB"
            rows.append(("VRAM", vram_txt, QColor("#38BDF8")))
        if c.cpu_stats or c.cpu_temp or c.cpu_mhz or c.cpu_power or c.core_load:
            parts = []
            if c.cpu_stats or c.core_load:
                parts.append(f"{t.cpu_percent}%")
            if c.cpu_temp and t.cpu_temp_c is not None:
                parts.append(f"{t.cpu_temp_c}°C")
            if c.cpu_mhz and t.cpu_ghz is not None:
                parts.append(f"{t.cpu_ghz:.2f}GHz")
            if c.cpu_power:
                parts.append("CPU")
            if parts:
                rows.append(("CPU", " ".join(parts), QColor("#F59E0B")))
        if c.ram or c.swap:
            ram_str = f"{t.ram_used_gb:.1f} GB" if c.ram else ""
            sw_str = f"SW {t.swap_used_gb:.1f}G" if c.swap else ""
            val = " | ".join(s for s in (ram_str, sw_str) if s)
            rows.append(("RAM" if c.ram else "SWAP", val, QColor("#E2E8F0")))
        if c.io_stats:
            rows.append(("I/O", f"R{t.io_read_mbs:.1f} W{t.io_write_mbs:.1f}MB/s", QColor("#60A5FA")))
        if c.fps:
            cur_ft = self._frametimes[-1] if self._frametimes else 16.6
            low1 = max(1, self._last_fps - max(2, round(self._last_fps * 0.08)))
            fps_val = f"{self._last_fps}"
            if c.show_fps_limit and c.fps_limit > 0:
                fps_val += f"/{c.fps_limit}"
            fps_val += " FPS"
            if c.frame_timing:
                fps_val += f"  {cur_ft:.1f}ms"
            if c.fps_metrics:
                fps_val += f"  1% {low1}"
            rows.append(("FPS", fps_val, QColor("#F8FAFC")))
        if c.vulkan_driver:
            rows.append(("DRIVER", "Mesa RADV", QColor("#38BDF8")))
        if c.wine:
            rows.append(("PROTON", "Wine/Proton", QColor("#A78BFA")))
        if c.resolution:
            rows.append(("RES", "1920x1080", QColor("#94A3B8")))
        if c.gamemode:
            rows.append(("GAMEMODE", "ON" if self._live_gamemode else "LISTO", QColor("#10B981")))
        if c.time:
            rows.append(("HORA", t.clock_str or "--:--:--", QColor("#CBD5E1")))
        if not rows:
            rows.append(("HUD", "Sin métricas", QColor("#94A3B8")))
        return rows[: (5 if c.horizontal else 12)]

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)

        outer = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setPen(QPen(QColor("#232934"), 1.2))
        p.setBrush(QColor("#0A0D12"))
        p.drawRoundedRect(outer, 10, 10)

        footer_h = 20.0
        viewport = outer.adjusted(0, 0, 0, -footer_h)

        # Animated subtle radar / viewport backdrop so preview is clearly live
        cx, cy = viewport.center().x(), viewport.center().y()
        p.setPen(QPen(QColor(255, 255, 255, 14), 1.0))
        p.drawLine(QPointF(viewport.left() + 12, cy), QPointF(viewport.right() - 12, cy))
        p.drawLine(QPointF(cx, viewport.top() + 10), QPointF(cx, viewport.bottom() - 10))
        p.drawEllipse(QPointF(cx, cy), 40.0, 24.0)

        angle = (self._tick * 0.42) % (2.0 * math.pi)
        dot_x = cx + math.cos(angle) * 34.0
        dot_y = cy + math.sin(angle) * 18.0
        p.setPen(QPen(QColor(16, 185, 129, 75), 1.2))
        p.drawLine(QPointF(cx, cy), QPointF(dot_x, dot_y))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(16, 185, 129, 140))
        p.drawEllipse(QPointF(dot_x, dot_y), 3.0, 3.0)

        # Bottom live telemetry status strip (never overlaps HUD box)
        pulse_alpha = 230 if (self._tick % 2 == 0) else 120
        p.setBrush(QColor(16, 185, 129, pulse_alpha))
        p.drawEllipse(QPointF(outer.left() + 14.0, outer.bottom() - 10.0), 3.2, 3.2)
        p.setPen(QColor("#64748B"))
        p.setFont(QFont("JetBrains Mono", 8))
        mode_tag = {
            "off": "MODO MANUAL",
            "selective": "SOLO MIS JUEGOS",
            "global": "GLOBAL (TODO 3D)",
        }.get(self._cfg.session_mode, "")
        p.drawText(
            QRectF(outer.left() + 22.0, outer.bottom() - footer_h, outer.width() - 32.0, footer_h),
            Qt.AlignVCenter | Qt.AlignLeft,
            f"EN VIVO · {mode_tag}",
        )

        rows = self._hud_lines()
        scale = max(0.74, min(1.18, float(self._cfg.font_size) / 22.0))
        hud_font = QFont("JetBrains Mono", max(7, round(8.0 * scale)), QFont.Bold)
        fm = QFontMetrics(hud_font)
        margin = 4.0 if self._cfg.no_margin else 10.0
        max_w = max(140.0, viewport.width() - margin * 2.0)
        max_h = max(32.0, viewport.height() - margin * 2.0)

        if self._cfg.horizontal:
            segs = [f"{k} {v}" for k, v, _ in rows[:5]]
            needed_w = sum(fm.horizontalAdvance(s) + 14.0 for s in segs) + 8.0
            box_w = min(max_w, max(165.0, needed_w))
            box_h = min(max_h, float(fm.height()) + 12.0)
            visible_rows = rows[:5]
        else:
            line_h = float(fm.height()) + 2.5
            graph_h = 24.0 if self._cfg.frame_timing else 0.0
            max_rows = max(1, int((max_h - 12.0 - graph_h) // line_h))
            visible_rows = rows[:max_rows]
            key_w = max(fm.horizontalAdvance(k) for k, _, _ in visible_rows)
            val_w = max(fm.horizontalAdvance(v) for _, v, _ in visible_rows)
            box_w = min(max_w, max(172.0, float(key_w + val_w + 30)))
            box_h = min(max_h, len(visible_rows) * line_h + 12.0 + graph_h)

        pos = self._cfg.position
        if "left" in pos:
            bx = viewport.left() + margin
        elif "right" in pos:
            bx = viewport.right() - margin - box_w
        else:
            bx = viewport.center().x() - box_w * 0.5
        by = viewport.top() + margin if pos.startswith("top") else viewport.bottom() - margin - box_h
        box = QRectF(bx, by, box_w, box_h)

        bg = QColor(12, 15, 20, round(self._cfg.background_alpha * 235))
        p.setPen(QPen(QColor(16, 185, 129, round(self._cfg.alpha * 145)), 1.0))
        p.setBrush(bg)
        p.drawRoundedRect(box, float(self._cfg.round_corners), float(self._cfg.round_corners))

        p.setFont(hud_font)
        if self._cfg.horizontal:
            x_cur = box.left() + 9.0
            for k, v, col in visible_rows:
                col_a = QColor(col)
                col_a.setAlpha(round(self._cfg.alpha * 255))
                p.setPen(col_a)
                seg = f"{k} {v}"
                seg_w = float(fm.horizontalAdvance(seg))
                if x_cur + seg_w > box.right() - 6.0 and x_cur > box.left() + 12.0:
                    break
                p.drawText(
                    QRectF(x_cur, box.top(), box.right() - x_cur - 6.0, box.height()),
                    Qt.AlignVCenter | Qt.AlignLeft,
                    seg,
                )
                x_cur += seg_w + 13.0
            return

        y_cur = box.top() + 6.0
        key_col_w = float(max(fm.horizontalAdvance(k) for k, _, _ in visible_rows)) + 6.0
        val_x = box.left() + 9.0 + key_col_w + 6.0
        val_col_w = max(40.0, box.right() - 9.0 - val_x)

        for k, v, col in visible_rows:
            col_a = QColor(col)
            col_a.setAlpha(round(self._cfg.alpha * 255))
            p.setPen(col_a)
            p.drawText(QRectF(box.left() + 9.0, y_cur, key_col_w, line_h), Qt.AlignVCenter | Qt.AlignLeft, k)
            fg = QColor("#F1F5F9")
            fg.setAlpha(round(self._cfg.alpha * 255))
            p.setPen(fg)
            elided = fm.elidedText(v, Qt.ElideRight, int(val_col_w))
            p.drawText(QRectF(val_x, y_cur, val_col_w, line_h), Qt.AlignVCenter | Qt.AlignRight, elided)
            y_cur += line_h

        if self._cfg.frame_timing and box.bottom() - y_cur >= 16.0:
            g_top = box.bottom() - 20.0
            g_h = 14.0
            samples = list(self._frametimes)
            if len(samples) >= 2:
                mn = min(samples)
                mx = max(samples)
                span = max(2.0, mx - mn)
                p.setPen(QPen(QColor("#10B981"), 1.5))
                pts = []
                w_span = max(20.0, box.width() - 18.0)
                for idx, ft in enumerate(samples):
                    px = box.left() + 9.0 + idx * (w_span / max(1, len(samples) - 1))
                    norm = (ft - mn) / span
                    py = g_top + g_h - (2.0 + norm * (g_h - 4.0))
                    pts.append(QPointF(px, py))
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
        self._wheel_guard = _NoWheelFilter(self)
        self._rule_adv_widgets: list[QWidget] = []
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

        # Card 0: Installation status & quick demo button
        status_card = QFrame()
        status_card.setObjectName("Card")
        st_lay = QHBoxLayout(status_card)
        st_lay.setContentsMargins(14, 10, 14, 10)
        st_lay.setSpacing(10)
        st_txt = QVBoxLayout()
        st_txt.setSpacing(2)
        st_title = QLabel("MangoHud (FPS, GPU y CPU en tus juegos)")
        st_title.setObjectName("SectionTitle")
        self._install_status_lbl = QLabel("Comprobando MangoHud...")
        self._install_status_lbl.setWordWrap(True)
        self._install_status_lbl.setObjectName("MutedNote")
        st_txt.addWidget(st_title)
        st_txt.addWidget(self._install_status_lbl)
        st_lay.addLayout(st_txt, 1)

        btn_demo = QPushButton("Probar ahora")
        btn_demo.setObjectName("MiniPill")
        btn_demo.setToolTip("Abre una ventana de prueba rápida para ver tu HUD real")
        btn_demo.clicked.connect(self._on_launch_demo)
        st_lay.addWidget(btn_demo)

        self._btn_install_mh = QPushButton("Instalar MangoHud")
        self._btn_install_mh.setObjectName("AccentBtn")
        self._btn_install_mh.clicked.connect(self._on_install_mangohud)
        st_lay.addWidget(self._btn_install_mh)
        lay.addWidget(status_card)

        # Row 1: Step 1 (Simple 2-choice Mode) + Live Preview side-by-side
        top_split = QHBoxLayout()
        top_split.setSpacing(10)

        mode_card = QFrame()
        mode_card.setObjectName("Card")
        mlay = QVBoxLayout(mode_card)
        mlay.setContentsMargins(14, 10, 14, 10)
        mlay.setSpacing(8)
        m_title = QLabel("1. ¿Dónde quieres ver el HUD?")
        m_title.setObjectName("SectionTitle")
        mlay.addWidget(m_title)

        self._mode_btns: dict[str, QPushButton] = {}
        for mid, mlabel, mtip in (
            ("selective", "Solo en mis juegos elegidos (Recomendado)", "Solo aparece en las ventanas/juegos de la lista de abajo"),
            ("global", "En todos los juegos automáticamente", "Aparece en cualquier juego Vulkan/OpenGL que abras"),
        ):
            b = QPushButton(mlabel)
            b.setObjectName("MiniPill")
            b.setCheckable(True)
            b.setToolTip(mtip)
            b.clicked.connect(lambda _=False, k=mid: self._on_session_mode_selected(k))
            self._mode_btns[mid] = b
            mlay.addWidget(b)

        tip_lbl = QLabel(
            "Tip: dentro de cualquier juego puedes pulsar Shift Derecho + F12 para ocultar o mostrar el HUD al instante."
        )
        tip_lbl.setObjectName("MutedNote")
        tip_lbl.setWordWrap(True)
        mlay.addWidget(tip_lbl)
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

        # Card 2: Step 2 (Pick your game or window — dead simple)
        win_card = QFrame()
        win_card.setObjectName("Card")
        wlay = QVBoxLayout(win_card)
        wlay.setContentsMargins(14, 10, 14, 10)
        wlay.setSpacing(8)

        win_title = QLabel("2. Elige tu juego o ventana")
        win_title.setObjectName("SectionTitle")
        wlay.addWidget(win_title)

        win_tools = QHBoxLayout()
        win_tools.setSpacing(6)
        self._btn_pick_click = QPushButton("Capturar al enfocar")
        self._btn_pick_click.setObjectName("AccentBtn")
        self._btn_pick_click.setToolTip(
            "Pulsa aquí y haz clic en tu juego dentro de 8 segundos (sin bloquear el ratón)"
        )
        self._btn_pick_click.clicked.connect(self._on_pick_window_click)

        btn_add_desktop = QPushButton("+ App instalada")
        btn_add_desktop.setObjectName("MiniPill")
        btn_add_desktop.clicked.connect(self._on_add_desktop_app)

        btn_add_bin = QPushButton("+ Archivo o nombre (.exe)")
        btn_add_bin.setObjectName("MiniPill")
        btn_add_bin.clicked.connect(self._on_add_custom_process)

        for b in (self._btn_pick_click, btn_add_desktop, btn_add_bin):
            win_tools.addWidget(b)
        win_tools.addStretch(1)
        wlay.addLayout(win_tools)

        det_row = QHBoxLayout()
        det_row.setSpacing(6)
        self._detected_combo = QComboBox()
        self._detected_combo.setMinimumWidth(240)
        self._guard_wheel(self._detected_combo)
        det_row.addWidget(self._detected_combo, 1)
        btn_add_det = QPushButton("+ Añadir")
        btn_add_det.setObjectName("MiniPill")
        btn_add_det.clicked.connect(self._on_add_detected_window)
        btn_scan = QPushButton("Actualizar")
        btn_scan.setObjectName("MiniPill")
        btn_scan.setToolTip("Actualizar lista de ventanas abiertas")
        btn_scan.clicked.connect(self.refresh_open_windows)
        det_row.addWidget(btn_add_det)
        det_row.addWidget(btn_scan)
        wlay.addLayout(det_row)

        self._rules_empty_lbl = QLabel(
            "Todavía no agregas ningún juego o ventana. Elígelo en la lista desplegable de arriba o usa 'Capturar al enfocar'."
        )
        self._rules_empty_lbl.setObjectName("MutedNote")
        self._rules_empty_lbl.setWordWrap(True)
        wlay.addWidget(self._rules_empty_lbl)

        self._rules_container = QVBoxLayout()
        self._rules_container.setSpacing(6)
        wlay.addLayout(self._rules_container)
        lay.addWidget(win_card)

        # Card 3: Step 3 (Simple Preset & Corner)
        des_card = QFrame()
        des_card.setObjectName("Card")
        dlay = QVBoxLayout(des_card)
        dlay.setContentsMargins(14, 10, 14, 10)
        dlay.setSpacing(8)
        d_title = QLabel("3. Estilo y esquina en pantalla")
        d_title.setObjectName("SectionTitle")
        dlay.addWidget(d_title)

        pr_row = QHBoxLayout()
        pr_row.setSpacing(6)
        pr_row.addWidget(QLabel("Estilo:"))
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
        pos_row.addWidget(QLabel("Esquina:"))
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
        lay.addWidget(des_card)

        # Collapsible Advanced Section (tucked away by default so basics stay effortless)
        self._adv_toggle_btn = QPushButton("Opciones avanzadas (métricas, límite FPS, tamaño, Steam, Flatpak)")
        self._adv_toggle_btn.setObjectName("MiniPill")
        self._adv_toggle_btn.setCheckable(True)
        self._adv_toggle_btn.setChecked(False)
        self._adv_toggle_btn.toggled.connect(self._on_advanced_toggled)
        lay.addWidget(self._adv_toggle_btn, 0, Qt.AlignLeft)

        self._advanced_panel = QFrame()
        self._advanced_panel.setObjectName("Card")
        self._advanced_panel.setVisible(False)
        adv_lay = QVBoxLayout(self._advanced_panel)
        adv_lay.setContentsMargins(14, 10, 14, 10)
        adv_lay.setSpacing(10)

        adv_tools = QHBoxLayout()
        adv_tools.setSpacing(8)
        b_off = QPushButton("Modo manual (Solo lanzar aquí / Steam)")
        b_off.setObjectName("MiniPill")
        b_off.setCheckable(True)
        b_off.setToolTip("No activa MANGOHUD=1 en sesión; solo al abrir desde aquí o con mangohud %command%")
        b_off.clicked.connect(lambda _=False: self._on_session_mode_selected("off"))
        self._mode_btns["off"] = b_off
        adv_tools.addWidget(b_off)

        btn_toggle_live = QPushButton("Mostrar/Ocultar en vivo")
        btn_toggle_live.setObjectName("MiniPill")
        btn_toggle_live.clicked.connect(self._on_toggle_live)
        btn_copy_steam = QPushButton("Copiar para Steam")
        btn_copy_steam.setObjectName("MiniPill")
        btn_copy_steam.clicked.connect(self._on_copy_steam_cmd)
        adv_tools.addWidget(btn_toggle_live)
        adv_tools.addWidget(btn_copy_steam)
        adv_tools.addStretch(1)
        adv_lay.addLayout(adv_tools)

        self._flatpak_chk = QCheckBox("Habilitar también para apps Flatpak")
        self._flatpak_chk.toggled.connect(self._on_flatpak_toggled)
        adv_lay.addWidget(self._flatpak_chk)

        style_grid = QGridLayout()
        style_grid.setHorizontalSpacing(10)
        style_grid.setVerticalSpacing(6)

        style_grid.addWidget(QLabel("Tamaño fuente:"), 0, 0)
        self._font_slider = QSlider(Qt.Horizontal)
        self._font_slider.setRange(14, 42)
        self._guard_wheel(self._font_slider)
        self._font_lbl = QLabel("22 px")
        self._font_slider.valueChanged.connect(self._on_style_changed)
        style_grid.addWidget(self._font_slider, 0, 1)
        style_grid.addWidget(self._font_lbl, 0, 2)

        style_grid.addWidget(QLabel("Opacidad fondo:"), 1, 0)
        self._bg_alpha_slider = QSlider(Qt.Horizontal)
        self._bg_alpha_slider.setRange(0, 100)
        self._guard_wheel(self._bg_alpha_slider)
        self._bg_alpha_lbl = QLabel("50%")
        self._bg_alpha_slider.valueChanged.connect(self._on_style_changed)
        style_grid.addWidget(self._bg_alpha_slider, 1, 1)
        style_grid.addWidget(self._bg_alpha_lbl, 1, 2)

        style_grid.addWidget(QLabel("Bordes redondeados:"), 2, 0)
        self._corners_slider = QSlider(Qt.Horizontal)
        self._corners_slider.setRange(0, 16)
        self._guard_wheel(self._corners_slider)
        self._corners_lbl = QLabel("8 px")
        self._corners_slider.valueChanged.connect(self._on_style_changed)
        style_grid.addWidget(self._corners_slider, 2, 1)
        style_grid.addWidget(self._corners_lbl, 2, 2)

        style_grid.addWidget(QLabel("Límite FPS global:"), 3, 0)
        self._fps_limit_spin = QSpinBox()
        self._fps_limit_spin.setRange(0, 360)
        self._fps_limit_spin.setSpecialValueText("Sin límite (0)")
        self._fps_limit_spin.setSuffix(" FPS")
        self._guard_wheel(self._fps_limit_spin)
        self._fps_limit_spin.valueChanged.connect(self._on_style_changed)
        style_grid.addWidget(self._fps_limit_spin, 3, 1)

        fps_q_row = QHBoxLayout()
        for fval, flbl in ((0, "Ilimitado"), (60, "60"), (120, "120"), (144, "144")):
            qb = QPushButton(flbl)
            qb.setObjectName("MiniPill")
            qb.clicked.connect(lambda _=False, fv=fval: self._fps_limit_spin.setValue(fv))
            fps_q_row.addWidget(qb)
        style_grid.addLayout(fps_q_row, 3, 2)
        adv_lay.addLayout(style_grid)

        opt_row = QHBoxLayout()
        opt_row.setSpacing(14)
        self._horiz_chk = QCheckBox("Barra horizontal compacta")
        self._horiz_chk.toggled.connect(self._on_style_changed)
        self._no_margin_chk = QCheckBox("Pegar al borde (sin margen)")
        self._no_margin_chk.toggled.connect(self._on_style_changed)
        opt_row.addWidget(self._horiz_chk)
        opt_row.addWidget(self._no_margin_chk)
        opt_row.addStretch(1)
        adv_lay.addLayout(opt_row)

        met_title = QLabel("Métricas personalizadas y teclas rápidas")
        met_title.setStyleSheet("color: #E2E8F0; font-weight: 700; font-size: 12.5px;")
        adv_lay.addWidget(met_title)

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
        adv_lay.addLayout(cols)

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
        adv_lay.addLayout(hk_row)
        lay.addWidget(self._advanced_panel)

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
        self._save_status_lbl.setWordWrap(True)
        self._save_status_lbl.setObjectName("MutedNote")
        fl.addWidget(self._save_status_lbl, 1)
        btn_apply = QPushButton("Guardar y aplicar MangoHud")
        btn_apply.setObjectName("PrimaryBtn")
        btn_apply.clicked.connect(self.save_and_apply)
        fl.addWidget(btn_apply)
        outer.addWidget(footer)

        self.refresh_open_windows()

    # -- Status & Distro Installation --

    def refresh_installation_status(self) -> None:
        distro = detect_distro()
        installed = has_mangohud()
        if installed:
            ver = mangohud_version()
            self._install_status_lbl.setText(
                f"MangoHud listo ({ver}) · Sistema: {distro.pretty_name}"
            )
            self._install_status_lbl.setStyleSheet("color: #34D399; font-size: 12.5px;")
            self._btn_install_mh.setVisible(False)
            self._btn_install_mh.setEnabled(False)
        else:
            self._install_status_lbl.setText(
                f"Falta instalar MangoHud ({distro.pretty_name} · {distro.pkg_manager}). "
                "Pulsa el botón para instalarlo automáticamente."
            )
            self._install_status_lbl.setStyleSheet("color: #FBBF24; font-size: 12.5px;")
            self._btn_install_mh.setText(f"Instalar MangoHud ({distro.pkg_manager})")
            self._btn_install_mh.setVisible(True)
            self._btn_install_mh.setEnabled(True)

    def _on_advanced_toggled(self, open_adv: bool) -> None:
        self._advanced_panel.setVisible(bool(open_adv))
        for w in self._rule_adv_widgets:
            w.setVisible(bool(open_adv))

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

    # -- Window & Process Selection --

    def _guard_wheel(self, widget: QWidget) -> None:
        widget.setFocusPolicy(Qt.StrongFocus)
        widget.installEventFilter(self._wheel_guard)

    def _running_status_for_process(self, process_name: str) -> tuple[bool, bool]:
        """Return (is_open, hud_loaded) across currently detected open windows."""
        target = (process_name or "").strip().lower()
        if not target:
            return False, False
        is_open = False
        for win in self._detected_windows:
            if win.process_name.lower() == target:
                is_open = True
                if win.hud_loaded:
                    return True, True
        return is_open, False

    def refresh_open_windows(self) -> None:
        self._detected_windows = discover_open_windows()
        self._detected_combo.clear()
        if not self._detected_windows:
            self._detected_combo.addItem("No se detectaron ventanas activas (abre un juego o app)", None)
        else:
            for win in self._detected_windows:
                hud_tag = " · HUD activo" if win.hud_loaded else " · abierta sin HUD (reiniciar)"
                label = f"{win.title}  [{win.process_name}]  ({win.api_hint}{hud_tag})"
                self._detected_combo.addItem(label, win)
        if hasattr(self, "_rules_container"):
            self._rebuild_rules_list()

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
        self._btn_pick_click.setText("Cambia a tu juego ahora (8s)...")
        th = _WindowPickThread(self)
        th.picked.connect(self._on_window_picked)
        self._pick_thread = th
        th.start()

    def _on_window_picked(self, win: DetectedWindow | None, msg: str) -> None:
        self._pick_thread = None
        self._btn_pick_click.setEnabled(True)
        self._btn_pick_click.setText("Capturar al enfocar")
        if win is None:
            self._save_status_lbl.setText(msg)
            self.refresh_open_windows()
            return
        self._add_or_enable_rule(win.title, win.process_name, win.exec_cmd)
        self.refresh_open_windows()
        self.save_and_apply()
        if win.hud_loaded:
            self._save_status_lbl.setText(f"{msg} · HUD cargado en vivo.")

    def _on_add_detected_window(self) -> None:
        win = self._detected_combo.currentData()
        if not isinstance(win, DetectedWindow):
            return
        self._add_or_enable_rule(win.title, win.process_name, win.exec_cmd)
        self.save_and_apply()

    def _on_add_desktop_app(self) -> None:
        apps = list_desktop_apps()
        if not apps:
            QMessageBox.information(self, "Aplicaciones", "No se encontraron accesos .desktop.")
            return
        dlg = _DesktopAppPickerDialog(apps, self)
        if dlg.exec() == QDialog.Accepted and dlg.selected_app is not None:
            app = dlg.selected_app
            self._add_or_enable_rule(app.name, app.process_name, app.exec_cmd)
            self.save_and_apply()

    def _on_add_custom_process(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Elegir ejecutable (o cancela para escribir el nombre del proceso)",
            "",
        )
        if file_path:
            pname = sanitize_process_name(file_path)
            self._add_or_enable_rule(pname, pname, file_path)
            self.save_and_apply()
            return
        txt, ok = QInputDialog.getText(
            self,
            "Añadir proceso / ventana",
            "Nombre exacto del proceso o ejecutable (ej. azimuth.exe, vkcube, firefox):",
        )
        if ok and txt.strip():
            pname = sanitize_process_name(txt)
            self._add_or_enable_rule(pname, pname, "")
            self.save_and_apply()

    def _rebuild_rules_list(self) -> None:
        self._rule_adv_widgets.clear()
        while self._rules_container.count():
            item = self._rules_container.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        adv_open = self._adv_toggle_btn.isChecked() if hasattr(self, "_adv_toggle_btn") else False
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

            is_open, is_hud = self._running_status_for_process(rule.process_name)
            state_lbl = QLabel()
            if is_open and is_hud:
                state_lbl.setText("Activo en vivo")
                state_lbl.setStyleSheet("color: #34D399; font-size: 11px; font-weight: 600;")
            elif is_open:
                state_lbl.setText("Abierta sin HUD: cierra y vuelve a abrir")
                state_lbl.setStyleSheet("color: #FBBF24; font-size: 11px; font-weight: 600;")
            else:
                state_lbl.setText("Listo al abrir")
                state_lbl.setStyleSheet("color: #64748B; font-size: 11px;")
            rlay.addWidget(state_lbl)

            pos_combo = QComboBox()
            self._guard_wheel(pos_combo)
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
            pos_combo.setVisible(adv_open)
            self._rule_adv_widgets.append(pos_combo)
            rlay.addWidget(pos_combo)

            fps_combo = QComboBox()
            self._guard_wheel(fps_combo)
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
            fps_combo.setVisible(adv_open)
            self._rule_adv_widgets.append(fps_combo)

            btn_launch = QPushButton("Abrir")
            btn_launch.setObjectName("MiniPill")
            can_launch = bool(rule.exec_cmd or not rule.process_name.lower().endswith(".exe"))
            btn_launch.setVisible(can_launch)
            btn_launch.clicked.connect(lambda _=False, r=rule: self._on_launch_rule(r))
            rlay.addWidget(btn_launch)

            btn_del = QPushButton("Quitar")
            btn_del.setObjectName("MiniPill")
            btn_del.clicked.connect(lambda _=False, i=idx: self._on_remove_rule(i))
            rlay.addWidget(btn_del)

            self._rules_container.addWidget(row_frame)

    def _on_rule_enabled(self, idx: int, enabled: bool) -> None:
        if 0 <= idx < len(self._config.window_rules):
            self._config.window_rules[idx].enabled = bool(enabled)
            self.save_and_apply()

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
            self.save_and_apply()

    def _on_launch_rule(self, rule: MangoHudWindowRule) -> None:
        cmd = rule.exec_cmd or rule.process_name
        ok, msg = launch_command_with_mangohud(cmd, self._config, rule=rule)
        self._save_status_lbl.setText(msg)
        if not ok:
            QMessageBox.warning(self, "Lanzar con MangoHud", msg)

    # -- Styling & Metrics Slots --

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
        self.save_and_apply()

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
        self.save_and_apply()

    def _on_position_selected(self, pos_key: str) -> None:
        if self._syncing:
            return
        self._config.position = pos_key
        for k, btn in self._pos_btns.items():
            btn.blockSignals(True)
            btn.setChecked(k == pos_key)
            btn.blockSignals(False)
        self._preview.set_preview_config(self._config)
        self.save_and_apply()

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
        self._detected_windows = discover_open_windows()
        self._rebuild_rules_list()
        n_rules = sum(1 for r in self._config.window_rules if r.enabled)

        needs_restart: list[str] = []
        for r in self._config.window_rules:
            if not r.enabled:
                continue
            is_open, is_hud = self._running_status_for_process(r.process_name)
            if is_open and not is_hud:
                needs_restart.append(r.process_name)

        if needs_restart:
            joined = ", ".join(needs_restart)
            self._save_status_lbl.setStyleSheet("color: #FBBF24; font-size: 12px;")
            self._save_status_lbl.setText(
                f"Aviso: guardado, pero {joined} ya estaba abierto sin MangoHud. "
                "Ciérralo (y reinicia Heroic/Steam si estaba abierto) y ábrelo de nuevo para ver el HUD."
            )
        else:
            self._save_status_lbl.setStyleSheet("color: #34D399; font-size: 12px;")
            self._save_status_lbl.setText(
                f"MangoHud guardado ({len(written)} archivos, {n_rules} ventanas activas)."
            )
