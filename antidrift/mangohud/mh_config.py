"""Configuration schema, presets, and native MangoHud.conf serializer."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

VALID_SESSION_MODES = ("off", "selective", "global")
VALID_POSITIONS = (
    "top-left",
    "top-center",
    "top-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
)

MANGOHUD_PRESETS: dict[str, dict[str, object]] = {
    "FPS Minimal": {
        "horizontal": False,
        "fps": True,
        "frame_timing": False,
        "fps_metrics": False,
        "gpu_stats": True,
        "gpu_temp": True,
        "gpu_core_clock": False,
        "gpu_mem_clock": False,
        "gpu_power": False,
        "vram": False,
        "gpu_name": False,
        "cpu_stats": True,
        "cpu_temp": True,
        "cpu_mhz": False,
        "cpu_power": False,
        "core_load": False,
        "ram": False,
        "swap": False,
        "vulkan_driver": False,
        "wine": False,
        "resolution": False,
        "gamemode": False,
        "time": False,
        "io_stats": False,
        "engine_version": False,
    },
    "Barra Horizontal": {
        "horizontal": True,
        "fps": True,
        "frame_timing": False,
        "fps_metrics": True,
        "gpu_stats": True,
        "gpu_temp": True,
        "gpu_core_clock": False,
        "gpu_mem_clock": False,
        "gpu_power": False,
        "vram": True,
        "gpu_name": False,
        "cpu_stats": True,
        "cpu_temp": True,
        "cpu_mhz": False,
        "cpu_power": False,
        "core_load": False,
        "ram": True,
        "swap": False,
        "vulkan_driver": False,
        "wine": False,
        "resolution": False,
        "gamemode": True,
        "time": False,
        "io_stats": False,
        "engine_version": False,
    },
    "Gaming Completo": {
        "horizontal": False,
        "fps": True,
        "frame_timing": True,
        "fps_metrics": True,
        "gpu_stats": True,
        "gpu_temp": True,
        "gpu_core_clock": True,
        "gpu_mem_clock": False,
        "gpu_power": False,
        "vram": True,
        "gpu_name": False,
        "cpu_stats": True,
        "cpu_temp": True,
        "cpu_mhz": True,
        "cpu_power": False,
        "core_load": False,
        "ram": True,
        "swap": False,
        "vulkan_driver": False,
        "wine": True,
        "resolution": True,
        "gamemode": True,
        "time": False,
        "io_stats": False,
        "engine_version": False,
    },
    "Sim Racing / Debug": {
        "horizontal": False,
        "fps": True,
        "frame_timing": True,
        "fps_metrics": True,
        "gpu_stats": True,
        "gpu_temp": True,
        "gpu_core_clock": True,
        "gpu_mem_clock": True,
        "gpu_power": True,
        "vram": True,
        "gpu_name": True,
        "cpu_stats": True,
        "cpu_temp": True,
        "cpu_mhz": True,
        "cpu_power": True,
        "core_load": True,
        "ram": True,
        "swap": True,
        "vulkan_driver": True,
        "wine": True,
        "resolution": True,
        "gamemode": True,
        "time": True,
        "io_stats": True,
        "engine_version": True,
    },
}


def default_mangohud_app_config_path() -> Path:
    """Return Anti-Drift MangoHud JSON store path honoring XDG_CONFIG_HOME."""
    cfg_root = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(cfg_root) if cfg_root else (Path.home() / ".config")
    return base / "xbox-antidrift-mangohud.json"


def default_mangohud_dir() -> Path:
    """Return MangoHud config directory honoring XDG_CONFIG_HOME."""
    cfg_root = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(cfg_root) if cfg_root else (Path.home() / ".config")
    return base / "MangoHud"


def sanitize_process_name(raw: str | None) -> str:
    """Clean a binary/process name so it maps safely to <name>.conf."""
    txt = (raw or "").strip()
    if not txt:
        return ""
    # Handle Windows paths (S:\Games\fh2\azimuth.exe) or POSIX paths
    txt = txt.replace("\\", "/").rsplit("/", 1)[-1].strip()
    txt = re.sub(r"[\x00-\x1f/\\:*?\"<>|]", "", txt)
    return txt[:96]


@dataclass
class MangoHudWindowRule:
    """Per-window / per-application MangoHud assignment and optional overrides."""

    id: str
    title: str
    process_name: str
    exec_cmd: str = ""
    enabled: bool = True
    override_position: str = ""  # "" = inherit global
    override_fps_limit: int = -1  # -1 = inherit global, >=0 = explicit
    override_preset: str = ""  # "" = inherit global

    def copy(self) -> MangoHudWindowRule:
        return replace(self)

    def clamped(self) -> MangoHudWindowRule:
        pos = self.override_position if self.override_position in VALID_POSITIONS else ""
        preset = self.override_preset if self.override_preset in MANGOHUD_PRESETS else ""
        fps_lim = -1 if int(self.override_fps_limit) < 0 else max(0, min(360, int(self.override_fps_limit)))
        pname = sanitize_process_name(self.process_name)
        return MangoHudWindowRule(
            id=(self.id or pname or "win").strip(),
            title=(self.title or pname or "Ventana").strip(),
            process_name=pname,
            exec_cmd=(self.exec_cmd or "").strip(),
            enabled=bool(self.enabled),
            override_position=pos,
            override_fps_limit=fps_lim,
            override_preset=preset,
        )


@dataclass
class MangoHudConfig:
    """Complete MangoHud overlay preferences + managed window rules."""

    session_mode: str = "selective"  # "off" | "selective" | "global"
    flatpak_enabled: bool = False
    preset_name: str = "Gaming Completo"
    position: str = "top-left"
    horizontal: bool = False
    font_size: int = 22
    background_alpha: float = 0.50
    alpha: float = 1.00
    round_corners: int = 8
    no_margin: bool = False
    fps: bool = True
    frame_timing: bool = True
    fps_metrics: bool = True
    fps_limit: int = 0
    show_fps_limit: bool = True
    vsync: int = -1
    gpu_stats: bool = True
    gpu_temp: bool = True
    gpu_core_clock: bool = True
    gpu_mem_clock: bool = False
    gpu_power: bool = False
    vram: bool = True
    gpu_name: bool = False
    cpu_stats: bool = True
    cpu_temp: bool = True
    cpu_mhz: bool = True
    cpu_power: bool = False
    core_load: bool = False
    ram: bool = True
    swap: bool = False
    vulkan_driver: bool = False
    wine: bool = True
    resolution: bool = True
    gamemode: bool = True
    time: bool = False
    io_stats: bool = False
    engine_version: bool = False
    toggle_hud: str = "Shift_R+F12"
    toggle_hud_position: str = "Shift_R+F11"
    toggle_fps_limit: str = "Shift_L+F1"
    reload_cfg: str = "Shift_L+F4"
    window_rules: list[MangoHudWindowRule] = field(default_factory=list)

    def copy(self) -> MangoHudConfig:
        return replace(
            self,
            window_rules=[r.copy() for r in self.window_rules],
        )

    def clamped(self) -> MangoHudConfig:
        sm = self.session_mode if self.session_mode in VALID_SESSION_MODES else "selective"
        pos = self.position if self.position in VALID_POSITIONS else "top-left"
        preset = (
            self.preset_name
            if (self.preset_name in MANGOHUD_PRESETS or self.preset_name == "Personalizado")
            else "Personalizado"
        )
        clean_rules: list[MangoHudWindowRule] = []
        seen_names: set[str] = set()
        for r in self.window_rules:
            cr = r.clamped()
            if not cr.process_name:
                continue
            key = cr.process_name.lower()
            if key in seen_names:
                continue
            seen_names.add(key)
            clean_rules.append(cr)

        return MangoHudConfig(
            session_mode=sm,
            flatpak_enabled=bool(self.flatpak_enabled),
            preset_name=preset,
            position=pos,
            horizontal=bool(self.horizontal),
            font_size=max(12, min(48, int(self.font_size))),
            background_alpha=round(max(0.0, min(1.0, float(self.background_alpha))), 2),
            alpha=round(max(0.2, min(1.0, float(self.alpha))), 2),
            round_corners=max(0, min(16, int(self.round_corners))),
            no_margin=bool(self.no_margin),
            fps=bool(self.fps),
            frame_timing=bool(self.frame_timing),
            fps_metrics=bool(self.fps_metrics),
            fps_limit=max(0, min(360, int(self.fps_limit))),
            show_fps_limit=bool(self.show_fps_limit),
            vsync=max(-1, min(3, int(self.vsync))),
            gpu_stats=bool(self.gpu_stats),
            gpu_temp=bool(self.gpu_temp),
            gpu_core_clock=bool(self.gpu_core_clock),
            gpu_mem_clock=bool(self.gpu_mem_clock),
            gpu_power=bool(self.gpu_power),
            vram=bool(self.vram),
            gpu_name=bool(self.gpu_name),
            cpu_stats=bool(self.cpu_stats),
            cpu_temp=bool(self.cpu_temp),
            cpu_mhz=bool(self.cpu_mhz),
            cpu_power=bool(self.cpu_power),
            core_load=bool(self.core_load),
            ram=bool(self.ram),
            swap=bool(self.swap),
            vulkan_driver=bool(self.vulkan_driver),
            wine=bool(self.wine),
            resolution=bool(self.resolution),
            gamemode=bool(self.gamemode),
            time=bool(self.time),
            io_stats=bool(self.io_stats),
            engine_version=bool(self.engine_version),
            toggle_hud=(self.toggle_hud or "Shift_R+F12").strip(),
            toggle_hud_position=(self.toggle_hud_position or "Shift_R+F11").strip(),
            toggle_fps_limit=(self.toggle_fps_limit or "Shift_L+F1").strip(),
            reload_cfg=(self.reload_cfg or "Shift_L+F4").strip(),
            window_rules=clean_rules,
        )


def apply_mangohud_preset(cfg: MangoHudConfig, preset_name: str) -> MangoHudConfig:
    """Return a copy of cfg with preset metric toggles applied."""
    preset = MANGOHUD_PRESETS.get(preset_name)
    if not preset:
        return cfg.copy()
    return replace(cfg, preset_name=preset_name, **preset).clamped()


def render_mangohud_conf(
    cfg: MangoHudConfig,
    rule: MangoHudWindowRule | None = None,
    force_no_display: bool = False,
) -> str:
    """Serialize MangoHudConfig (plus optional per-window overrides) to MangoHud.conf."""
    eff = cfg.clamped()
    if rule is not None:
        cr = rule.clamped()
        if cr.override_preset:
            eff = apply_mangohud_preset(eff, cr.override_preset)
        if cr.override_position:
            eff.position = cr.override_position
        if cr.override_fps_limit >= 0:
            eff.fps_limit = cr.override_fps_limit

    lines = [
        "# Generado automáticamente por Anti-Drift Studio",
        f"position={eff.position}",
        f"font_size={eff.font_size}",
        f"background_alpha={eff.background_alpha:.2f}",
        f"alpha={eff.alpha:.2f}",
        f"round_corners={eff.round_corners}",
    ]
    if eff.horizontal:
        lines.append("horizontal")
    if eff.no_margin:
        lines.append("hud_no_margin")

    if force_no_display:
        lines.append("no_display")
    else:
        lines.append("no_display=0")

    lines.append(f"fps={'1' if eff.fps else '0'}")
    if eff.frame_timing:
        lines.append("frame_timing")
    else:
        lines.append("frame_timing=0")
    if eff.fps_metrics:
        lines.append("fps_metrics=avg,0.01")
    lines.append(f"fps_limit={eff.fps_limit}")
    if eff.fps_limit > 0 and eff.show_fps_limit:
        lines.append("show_fps_limit")
    if eff.vsync >= 0:
        lines.append(f"vsync={eff.vsync}")

    bool_flags = (
        ("gpu_stats", eff.gpu_stats),
        ("gpu_temp", eff.gpu_temp),
        ("gpu_core_clock", eff.gpu_core_clock),
        ("gpu_mem_clock", eff.gpu_mem_clock),
        ("gpu_power", eff.gpu_power),
        ("vram", eff.vram),
        ("gpu_name", eff.gpu_name),
        ("cpu_stats", eff.cpu_stats),
        ("cpu_temp", eff.cpu_temp),
        ("cpu_mhz", eff.cpu_mhz),
        ("cpu_power", eff.cpu_power),
        ("core_load", eff.core_load),
        ("ram", eff.ram),
        ("swap", eff.swap),
        ("vulkan_driver", eff.vulkan_driver),
        ("wine", eff.wine),
        ("resolution", eff.resolution),
        ("gamemode", eff.gamemode),
        ("time", eff.time),
        ("engine_version", eff.engine_version),
    )
    for key, active in bool_flags:
        if active:
            lines.append(key)
    if eff.io_stats:
        lines.append("io_read")
        lines.append("io_write")

    lines.extend(
        [
            f"toggle_hud={eff.toggle_hud}",
            f"toggle_hud_position={eff.toggle_hud_position}",
            f"toggle_fps_limit={eff.toggle_fps_limit}",
            f"reload_cfg={eff.reload_cfg}",
        ]
    )
    return "\n".join(lines) + "\n"


def load_mangohud_app_config(path: Path | None = None) -> MangoHudConfig:
    """Load saved Anti-Drift MangoHud configuration JSON."""
    p = path or default_mangohud_app_config_path()
    if not p.exists():
        return MangoHudConfig()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return MangoHudConfig()
        defaults = MangoHudConfig()
        updates: dict[str, object] = {}
        for f in fields(MangoHudConfig):
            if f.name == "window_rules" or f.name not in raw:
                continue
            cur = getattr(defaults, f.name)
            val = raw[f.name]
            if isinstance(cur, bool):
                updates[f.name] = bool(val)
            elif isinstance(cur, int):
                updates[f.name] = int(val)
            elif isinstance(cur, float):
                updates[f.name] = float(val)
            else:
                updates[f.name] = str(val)
        rules: list[MangoHudWindowRule] = []
        for idx, item in enumerate(raw.get("window_rules") or []):
            if not isinstance(item, dict):
                continue
            rules.append(
                MangoHudWindowRule(
                    id=str(item.get("id", f"rule_{idx}")),
                    title=str(item.get("title", "")),
                    process_name=str(item.get("process_name", "")),
                    exec_cmd=str(item.get("exec_cmd", "")),
                    enabled=bool(item.get("enabled", True)),
                    override_position=str(item.get("override_position", "")),
                    override_fps_limit=int(item.get("override_fps_limit", -1)),
                    override_preset=str(item.get("override_preset", "")),
                )
            )
        updates["window_rules"] = rules
        return replace(defaults, **updates).clamped()
    except (OSError, ValueError, TypeError):
        return MangoHudConfig()


def save_mangohud_app_config(cfg: MangoHudConfig, path: Path | None = None) -> None:
    """Persist Anti-Drift MangoHud configuration JSON cleanly."""
    p = path or default_mangohud_app_config_path()
    clean = cfg.clamped()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(clean), indent=2, ensure_ascii=False), encoding="utf-8")
