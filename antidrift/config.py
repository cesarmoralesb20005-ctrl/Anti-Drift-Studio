"""Configuration management and exact anti-drift math mirroring xbox-antidrift.c."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path

AXIS_MIN = -32768
AXIS_MAX = 32767
TRIGGER_MAX = 1023

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "xbox-antidrift.conf"
DEFAULT_DEVICES_CONFIG_PATH = Path.home() / ".config" / "xbox-antidrift-devices.conf"
ENV_CONFIG_PATH = Path.home() / ".config" / "environment.d" / "xbox-antidrift.conf"
DEFAULT_VIRTUAL_NAME = "Microsoft X-Box 360 pad"

VALID_DZ_MODES = ("axial", "radial", "hybrid")
VALID_WHEEL_MODES = ("auto", "wheel", "gamepad")
VALID_SHIFTER_MODES = ("none", "paddles", "sequential", "h_shifter")
VALID_WHEEL_BTNS = ("A", "B", "X", "Y", "LB", "RB", "VIEW", "MENU")
VALID_CLUTCH_MODES = ("none", "x_btn", "lb_btn")


def sanitize_virtual_name(name: str | None) -> str:
    """Sanitize virtual uinput device name and fit within Linux 79-byte limit."""
    cleaned = " ".join((name or "").replace("\n", " ").replace("\r", " ").split()).strip()
    if not cleaned:
        return DEFAULT_VIRTUAL_NAME
    encoded = cleaned.encode("utf-8")
    if len(encoded) <= 79:
        return cleaned
    return encoded[:79].decode("utf-8", errors="ignore").strip() or DEFAULT_VIRTUAL_NAME


@dataclass
class AntiDriftConfig:
    """Universal controller anti-drift, curve, debounce, and rumble settings."""

    center_lx: int = 0
    center_ly: int = 0
    dz_l: float = 0.12
    center_rx: int = 0
    center_ry: int = 0
    dz_r: float = 0.12
    dz_mode: str = "axial"  # "axial" | "radial" | "hybrid"
    outer_comp_l: float = 0.0
    outer_comp_r: float = 0.0
    low_latency: bool = False
    response_curve: float = 1.00
    hair_trigger: bool = False
    debounce_ms: int = 0
    rumble_gain: int = 100
    swap_nintendo: bool = False
    multi_controller: bool = True
    device_path: str = "auto"
    virtual_name: str = DEFAULT_VIRTUAL_NAME
    lt_dz: float = 0.00
    rt_dz: float = 0.00
    lt_max: float = 1.00
    rt_max: float = 1.00
    lt_curve: float = 1.00
    rt_curve: float = 1.00
    wheel_mode: str = "auto"  # "auto" | "wheel" | "gamepad"
    wheel_rotation_deg: int = 270
    wheel_steer_dz: float = 0.02
    wheel_steer_sat: float = 1.00
    wheel_linearity: float = 1.00
    wheel_dz_gas: float = 0.03
    wheel_dz_brake: float = 0.04
    wheel_max_gas: float = 1.00
    wheel_max_brake: float = 1.00
    wheel_invert_gas: bool = False
    wheel_invert_brake: bool = False
    wheel_combined_pedals: bool = False
    wheel_shifter_mode: str = "paddles"  # "none" | "paddles" | "sequential" | "h_shifter"
    wheel_shifter_device: str = "auto"
    wheel_shifter_down_btn: str = "LB"
    wheel_shifter_up_btn: str = "RB"
    wheel_handbrake_enabled: bool = True
    wheel_handbrake_device: str = "auto"
    wheel_handbrake_target: str = "A"
    wheel_handbrake_dz: float = 0.20
    wheel_handbrake_invert: bool = False
    wheel_clutch_mode: str = "none"  # "none" | "x_btn" | "lb_btn"

    def copy(self) -> AntiDriftConfig:
        """Return a shallow copy of the configuration."""
        return replace(self)

    def clamped(self) -> AntiDriftConfig:
        """Return a copy with fields clamped to safe operational bounds."""
        mode = self.dz_mode if self.dz_mode in VALID_DZ_MODES else "axial"
        wmode = self.wheel_mode if self.wheel_mode in VALID_WHEEL_MODES else "auto"
        smode = (
            self.wheel_shifter_mode
            if self.wheel_shifter_mode in VALID_SHIFTER_MODES
            else "paddles"
        )
        s_down = (
            self.wheel_shifter_down_btn
            if self.wheel_shifter_down_btn in VALID_WHEEL_BTNS
            else "LB"
        )
        s_up = (
            self.wheel_shifter_up_btn
            if self.wheel_shifter_up_btn in VALID_WHEEL_BTNS
            else "RB"
        )
        hb_tgt = (
            self.wheel_handbrake_target
            if self.wheel_handbrake_target in ("A", "B", "X", "Y")
            else "A"
        )
        cmode = (
            self.wheel_clutch_mode
            if self.wheel_clutch_mode in VALID_CLUTCH_MODES
            else "none"
        )
        return AntiDriftConfig(
            center_lx=max(-28000, min(28000, int(self.center_lx))),
            center_ly=max(-28000, min(28000, int(self.center_ly))),
            dz_l=round(max(0.0, min(0.60, float(self.dz_l))), 3),
            center_rx=max(-28000, min(28000, int(self.center_rx))),
            center_ry=max(-28000, min(28000, int(self.center_ry))),
            dz_r=round(max(0.0, min(0.60, float(self.dz_r))), 3),
            dz_mode=mode,
            outer_comp_l=round(max(0.0, min(0.30, float(self.outer_comp_l))), 3),
            outer_comp_r=round(max(0.0, min(0.30, float(self.outer_comp_r))), 3),
            low_latency=bool(self.low_latency),
            response_curve=round(max(0.45, min(1.30, float(self.response_curve))), 2),
            hair_trigger=bool(self.hair_trigger),
            debounce_ms=max(0, min(80, int(self.debounce_ms))),
            rumble_gain=max(0, min(100, int(self.rumble_gain))),
            swap_nintendo=bool(self.swap_nintendo),
            multi_controller=bool(self.multi_controller),
            device_path=(self.device_path or "auto").strip(),
            virtual_name=sanitize_virtual_name(self.virtual_name),
            lt_dz=round(max(0.0, min(0.40, float(self.lt_dz))), 2),
            rt_dz=round(max(0.0, min(0.40, float(self.rt_dz))), 2),
            lt_max=round(max(0.10, min(1.00, float(self.lt_max))), 2),
            rt_max=round(max(0.10, min(1.00, float(self.rt_max))), 2),
            lt_curve=round(max(0.40, min(2.50, float(self.lt_curve))), 2),
            rt_curve=round(max(0.40, min(2.50, float(self.rt_curve))), 2),
            wheel_mode=wmode,
            wheel_rotation_deg=max(180, min(1080, int(self.wheel_rotation_deg))),
            wheel_steer_dz=round(max(0.0, min(0.30, float(self.wheel_steer_dz))), 3),
            wheel_steer_sat=round(max(0.25, min(1.00, float(self.wheel_steer_sat))), 2),
            wheel_linearity=round(max(0.45, min(1.60, float(self.wheel_linearity))), 2),
            wheel_dz_gas=round(max(0.0, min(0.35, float(self.wheel_dz_gas))), 3),
            wheel_dz_brake=round(max(0.0, min(0.35, float(self.wheel_dz_brake))), 3),
            wheel_max_gas=round(max(0.50, min(1.00, float(self.wheel_max_gas))), 2),
            wheel_max_brake=round(max(0.50, min(1.00, float(self.wheel_max_brake))), 2),
            wheel_invert_gas=bool(self.wheel_invert_gas),
            wheel_invert_brake=bool(self.wheel_invert_brake),
            wheel_combined_pedals=bool(self.wheel_combined_pedals),
            wheel_shifter_mode=smode,
            wheel_shifter_device=(self.wheel_shifter_device or "auto").strip(),
            wheel_shifter_down_btn=s_down,
            wheel_shifter_up_btn=s_up,
            wheel_handbrake_enabled=bool(self.wheel_handbrake_enabled),
            wheel_handbrake_device=(self.wheel_handbrake_device or "auto").strip(),
            wheel_handbrake_target=hb_tgt,
            wheel_handbrake_dz=round(max(0.05, min(0.85, float(self.wheel_handbrake_dz))), 2),
            wheel_handbrake_invert=bool(self.wheel_handbrake_invert),
            wheel_clutch_mode=cmode,
        )


def _apply_kv_to_config(cfg: AntiDriftConfig, key: str, val_str: str) -> None:
    if key == "device_path":
        cfg.device_path = val_str or "auto"
        return
    if key == "virtual_name":
        cfg.virtual_name = sanitize_virtual_name(val_str)
        return
    if key == "wheel_mode":
        if val_str in VALID_WHEEL_MODES:
            cfg.wheel_mode = val_str
        elif val_str in ("0", "1", "2"):
            cfg.wheel_mode = VALID_WHEEL_MODES[int(val_str)]
        return
    if key == "wheel_shifter_mode":
        if val_str in VALID_SHIFTER_MODES:
            cfg.wheel_shifter_mode = val_str
        return
    if key == "wheel_shifter_device":
        cfg.wheel_shifter_device = val_str or "auto"
        return
    if key == "wheel_shifter_down_btn":
        if val_str in VALID_WHEEL_BTNS:
            cfg.wheel_shifter_down_btn = val_str
        return
    if key == "wheel_shifter_up_btn":
        if val_str in VALID_WHEEL_BTNS:
            cfg.wheel_shifter_up_btn = val_str
        return
    if key == "wheel_handbrake_device":
        cfg.wheel_handbrake_device = val_str or "auto"
        return
    if key == "wheel_handbrake_target":
        if val_str in ("A", "B", "X", "Y"):
            cfg.wheel_handbrake_target = val_str
        return
    if key == "wheel_clutch_mode":
        if val_str in VALID_CLUTCH_MODES:
            cfg.wheel_clutch_mode = val_str
        return
    if key == "dz_mode":
        if val_str in VALID_DZ_MODES:
            cfg.dz_mode = val_str
        elif val_str in ("0", "1", "2"):
            cfg.dz_mode = VALID_DZ_MODES[int(val_str)]
        return
    try:
        val = float(val_str)
    except ValueError:
        return
    if key == "center_lx":
        cfg.center_lx = int(val)
    elif key == "center_ly":
        cfg.center_ly = int(val)
    elif key == "dz_l":
        cfg.dz_l = val
    elif key == "center_rx":
        cfg.center_rx = int(val)
    elif key == "center_ry":
        cfg.center_ry = int(val)
    elif key == "dz_r":
        cfg.dz_r = val
    elif key == "outer_comp_l":
        cfg.outer_comp_l = val
    elif key == "outer_comp_r":
        cfg.outer_comp_r = val
    elif key == "low_latency":
        cfg.low_latency = bool(int(val))
    elif key == "response_curve":
        cfg.response_curve = val
    elif key == "hair_trigger":
        cfg.hair_trigger = bool(int(val))
    elif key == "debounce_ms":
        cfg.debounce_ms = int(val)
    elif key == "rumble_gain":
        cfg.rumble_gain = int(val)
    elif key == "swap_nintendo":
        cfg.swap_nintendo = bool(int(val))
    elif key == "multi_controller":
        cfg.multi_controller = bool(int(val))
    elif key == "wheel_rotation_deg":
        cfg.wheel_rotation_deg = int(val)
    elif key == "wheel_steer_dz":
        cfg.wheel_steer_dz = val
    elif key == "wheel_steer_sat":
        cfg.wheel_steer_sat = val
    elif key == "wheel_linearity":
        cfg.wheel_linearity = val
    elif key == "wheel_dz_gas":
        cfg.wheel_dz_gas = val
    elif key == "wheel_dz_brake":
        cfg.wheel_dz_brake = val
    elif key == "wheel_max_gas":
        cfg.wheel_max_gas = val
    elif key == "wheel_max_brake":
        cfg.wheel_max_brake = val
    elif key == "wheel_invert_gas":
        cfg.wheel_invert_gas = bool(int(val))
    elif key == "wheel_invert_brake":
        cfg.wheel_invert_brake = bool(int(val))
    elif key == "wheel_combined_pedals":
        cfg.wheel_combined_pedals = bool(int(val))
    elif key == "lt_dz":
        cfg.lt_dz = val
    elif key == "rt_dz":
        cfg.rt_dz = val
    elif key == "lt_max":
        cfg.lt_max = val
    elif key == "rt_max":
        cfg.rt_max = val
    elif key == "lt_curve":
        cfg.lt_curve = val
    elif key == "rt_curve":
        cfg.rt_curve = val
    elif key == "wheel_handbrake_enabled":
        cfg.wheel_handbrake_enabled = bool(int(val))
    elif key == "wheel_handbrake_dz":
        cfg.wheel_handbrake_dz = val
    elif key == "wheel_handbrake_invert":
        cfg.wheel_handbrake_invert = bool(int(val))


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AntiDriftConfig:
    """Load configuration from ~/.config/xbox-antidrift.conf with safe defaults."""
    cfg = AntiDriftConfig()
    if not path.exists():
        return cfg

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val_str = line.split("=", 1)
        _apply_kv_to_config(cfg, key.strip(), val_str.strip())
    return cfg.clamped()


def _format_config_lines(cfg: AntiDriftConfig, include_global_routing: bool = True) -> list[str]:
    clean = cfg.clamped()
    lines = [
        f"center_lx = {clean.center_lx}",
        f"center_ly = {clean.center_ly}",
        f"dz_l = {clean.dz_l:.2f}",
        f"center_rx = {clean.center_rx}",
        f"center_ry = {clean.center_ry}",
        f"dz_r = {clean.dz_r:.2f}",
        f"dz_mode = {clean.dz_mode}",
        f"outer_comp_l = {clean.outer_comp_l:.2f}",
        f"outer_comp_r = {clean.outer_comp_r:.2f}",
        f"low_latency = {1 if clean.low_latency else 0}",
        f"response_curve = {clean.response_curve:.2f}",
        f"hair_trigger = {1 if clean.hair_trigger else 0}",
        f"debounce_ms = {clean.debounce_ms}",
        f"rumble_gain = {clean.rumble_gain}",
        f"swap_nintendo = {1 if clean.swap_nintendo else 0}",
        f"virtual_name = {clean.virtual_name}",
        f"wheel_mode = {clean.wheel_mode}",
        f"wheel_rotation_deg = {clean.wheel_rotation_deg}",
        f"wheel_steer_dz = {clean.wheel_steer_dz:.3f}",
        f"wheel_steer_sat = {clean.wheel_steer_sat:.2f}",
        f"wheel_linearity = {clean.wheel_linearity:.2f}",
        f"wheel_dz_gas = {clean.wheel_dz_gas:.3f}",
        f"wheel_dz_brake = {clean.wheel_dz_brake:.3f}",
        f"wheel_max_gas = {clean.wheel_max_gas:.2f}",
        f"wheel_max_brake = {clean.wheel_max_brake:.2f}",
        f"wheel_invert_gas = {1 if clean.wheel_invert_gas else 0}",
        f"wheel_invert_brake = {1 if clean.wheel_invert_brake else 0}",
        f"wheel_combined_pedals = {1 if clean.wheel_combined_pedals else 0}",
        f"lt_dz = {clean.lt_dz:.2f}",
        f"rt_dz = {clean.rt_dz:.2f}",
        f"lt_max = {clean.lt_max:.2f}",
        f"rt_max = {clean.rt_max:.2f}",
        f"lt_curve = {clean.lt_curve:.2f}",
        f"rt_curve = {clean.rt_curve:.2f}",
        f"wheel_shifter_mode = {clean.wheel_shifter_mode}",
        f"wheel_shifter_device = {clean.wheel_shifter_device}",
        f"wheel_shifter_down_btn = {clean.wheel_shifter_down_btn}",
        f"wheel_shifter_up_btn = {clean.wheel_shifter_up_btn}",
        f"wheel_handbrake_enabled = {1 if clean.wheel_handbrake_enabled else 0}",
        f"wheel_handbrake_device = {clean.wheel_handbrake_device}",
        f"wheel_handbrake_target = {clean.wheel_handbrake_target}",
        f"wheel_handbrake_dz = {clean.wheel_handbrake_dz:.2f}",
        f"wheel_handbrake_invert = {1 if clean.wheel_handbrake_invert else 0}",
        f"wheel_clutch_mode = {clean.wheel_clutch_mode}",
    ]
    if include_global_routing:
        lines.append(f"multi_controller = {1 if clean.multi_controller else 0}")
        if clean.device_path and clean.device_path != "auto":
            lines.append(f"device_path = {clean.device_path}")
    return lines


def save_config(cfg: AntiDriftConfig, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Persist configuration in the exact format parsed by xbox-antidrift.c."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "# Configuracion universal Anti-Drift Studio\n"
    content = header + "\n".join(_format_config_lines(cfg, include_global_routing=True)) + "\n"
    path.write_text(content, encoding="utf-8")


def _parse_device_sections(
    path: Path = DEFAULT_DEVICES_CONFIG_PATH,
) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    if not path.exists():
        return sections
    cur_id: str | None = None
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[device ") and line.endswith("]"):
            cur_id = line[8:-1].strip()
            if cur_id:
                sections.setdefault(cur_id, {})
            else:
                cur_id = None
            continue
        if cur_id and "=" in line:
            k, v = line.split("=", 1)
            sections[cur_id][k.strip()] = v.strip()
    return sections


def load_device_config(
    stable_id: str | None,
    base_cfg: AntiDriftConfig | None = None,
    path: Path = DEFAULT_DEVICES_CONFIG_PATH,
) -> AntiDriftConfig:
    """Load effective AntiDriftConfig for a specific controller ID (base + override)."""
    base = (base_cfg or load_config()).copy()
    if not stable_id:
        return base.clamped()
    sections = _parse_device_sections(path)
    sec = sections.get(stable_id)
    if not sec:
        return base.clamped()
    for k, v in sec.items():
        if k == "label":
            continue
        _apply_kv_to_config(base, k, v)
    return base.clamped()


def save_device_config(
    stable_id: str,
    cfg: AntiDriftConfig,
    label: str = "",
    path: Path = DEFAULT_DEVICES_CONFIG_PATH,
) -> None:
    """Save per-controller drift calibration & custom virtual name override."""
    if not stable_id:
        return
    sections = _parse_device_sections(path)
    new_sec: dict[str, str] = {}
    if label.strip():
        new_sec["label"] = label.strip()
    for line in _format_config_lines(cfg, include_global_routing=False):
        k, v = line.split("=", 1)
        new_sec[k.strip()] = v.strip()
    sections[stable_id] = new_sec

    path.parent.mkdir(parents=True, exist_ok=True)
    out_lines = ["# Configuracion por mando Anti-Drift Studio", ""]
    for dev_id, kv in sections.items():
        out_lines.append(f"[device {dev_id}]")
        for k, v in kv.items():
            out_lines.append(f"{k} = {v}")
        out_lines.append("")
    path.write_text("\n".join(out_lines), encoding="utf-8")


def normalize_axis(raw: int, center: int) -> float:
    """Mirror normalize_axis() from xbox-antidrift.c."""
    d = float(raw - center)
    span = (32767.0 - float(center)) if d >= 0.0 else (float(center) - (-32768.0))
    if span < 1000.0:
        span = 1000.0
    norm = d / span
    return max(-1.0, min(1.0, norm))


def _shape_scalar(mag: float, dz: float, outer_comp: float, curve: float) -> float:
    if mag <= dz or dz >= 0.999:
        return 0.0
    reach = max(dz + 0.10, 1.0 - outer_comp)
    scaled = min(1.0, (mag - dz) / (reach - dz))
    if curve != 1.0 and scaled > 0.0:
        scaled = min(1.0, pow(scaled, curve))
    return scaled


def _scalar_to_i16(signed_val: float) -> int:
    iv = round(signed_val * 32767.0)
    return max(AXIS_MIN, min(AXIS_MAX, iv))


def apply_axial_deadzone(
    norm: float, dz: float, curve: float = 1.0, outer_comp: float = 0.0
) -> int:
    """Apply axial deadzone + outer-edge compensation + response curve."""
    s = _shape_scalar(abs(norm), dz, outer_comp, curve)
    return _scalar_to_i16(-s if norm < 0.0 else s)


def compute_corrected_stick(
    raw_x: int,
    raw_y: int,
    center_x: int,
    center_y: int,
    dz: float,
    curve: float = 1.0,
    dz_mode: str = "axial",
    outer_comp: float = 0.0,
) -> tuple[int, int]:
    """Compute corrected (ox, oy) stick values matching xbox-antidrift.c."""
    nx = normalize_axis(raw_x, center_x)
    ny = normalize_axis(raw_y, center_y)

    if dz_mode in ("radial", "hybrid"):
        if dz_mode == "hybrid":
            ax_dz = dz * 0.40
            if abs(nx) <= ax_dz:
                nx = 0.0
            if abs(ny) <= ax_dz:
                ny = 0.0
        r = math.hypot(nx, ny)
        if r <= dz or r < 1e-9:
            return 0, 0
        sr = _shape_scalar(r, dz, outer_comp, curve)
        return _scalar_to_i16((nx / r) * sr), _scalar_to_i16((ny / r) * sr)

    return (
        apply_axial_deadzone(nx, dz, curve, outer_comp),
        apply_axial_deadzone(ny, dz, curve, outer_comp),
    )


def apply_hair_trigger(val: int, hair_trigger: bool) -> int:
    """Mirror apply_hair_trigger() from xbox-antidrift.c."""
    if not hair_trigger:
        return val
    return TRIGGER_MAX if val >= 100 else 0


def compute_trigger_output(
    raw_val: int,
    dz: float = 0.0,
    max_out: float = 1.0,
    curve: float = 1.0,
    hair_trigger: bool = False,
) -> int:
    """Apply trigger inner deadzone, custom response curve, and max output cap [0..1023]."""
    clamped_raw = max(0, min(TRIGGER_MAX, int(raw_val)))
    cap = max(0.10, min(1.0, float(max_out)))
    if hair_trigger:
        return max(0, min(TRIGGER_MAX, round(cap * float(TRIGGER_MAX)))) if clamped_raw >= 100 else 0
    dz_c = max(0.0, min(0.95, float(dz)))
    frac = float(clamped_raw) / float(TRIGGER_MAX)
    if frac <= dz_c:
        return 0
    scaled = min(1.0, (frac - dz_c) / (1.0 - dz_c))
    if curve != 1.0 and scaled > 0.0:
        scaled = min(1.0, pow(scaled, float(curve)))
    return max(0, min(TRIGGER_MAX, round(scaled * cap * float(TRIGGER_MAX))))


def deadzone_raw_bounds(center: int, dz: float) -> tuple[float, float]:
    """Return raw [min_raw, max_raw] boundaries corresponding to [-dz, +dz]."""
    neg_span = max(1000.0, float(center) - float(AXIS_MIN))
    pos_span = max(1000.0, float(AXIS_MAX) - float(center))
    return float(center) - dz * neg_span, float(center) + dz * pos_span


def scale_hw_to_i16(raw: int, hw_min: int, hw_max: int) -> int:
    """Scale arbitrary hardware axis bounds [hw_min..hw_max] to [-32768..32767]."""
    if hw_min == AXIS_MIN and hw_max == AXIS_MAX:
        return int(raw)
    if hw_max <= hw_min:
        return 0
    frac = max(0.0, min(1.0, float(raw - hw_min) / float(hw_max - hw_min)))
    return max(AXIS_MIN, min(AXIS_MAX, round(-32768.0 + frac * 65535.0)))


def scale_trig_to_1023(raw: int, hw_min: int, hw_max: int) -> int:
    """Scale arbitrary trigger bounds [hw_min..hw_max] to [0..1023]."""
    if hw_min == 0 and hw_max == TRIGGER_MAX:
        return max(0, min(TRIGGER_MAX, int(raw)))
    if hw_max <= hw_min:
        return 0
    frac = max(0.0, min(1.0, float(raw - hw_min) / float(hw_max - hw_min)))
    return round(frac * float(TRIGGER_MAX))


def compute_wheel_steering(
    raw_x: int,
    center_x: int,
    dz: float,
    saturation: float = 1.0,
    linearity: float = 1.0,
) -> int:
    """Compute centered, deadzoned, lock-scaled, and curve-shaped steering output."""
    nx = normalize_axis(raw_x, center_x)
    mag = abs(nx)
    reach = max(dz + 0.05, min(1.0, float(saturation)))
    if mag <= dz or dz >= 0.999:
        return 0
    scaled = min(1.0, (mag - dz) / (reach - dz))
    if linearity != 1.0 and scaled > 0.0:
        scaled = min(1.0, pow(scaled, linearity))
    return _scalar_to_i16(-scaled if nx < 0.0 else scaled)


def compute_wheel_pedal(
    raw_val: int,
    dz: float,
    max_reach: float = 1.0,
    invert: bool = False,
    hair_trigger: bool = False,
) -> int:
    """Apply inversion, deadzone, saturation, and optional hair-trigger to a [0..1023] pedal."""
    clamped_raw = max(0, min(TRIGGER_MAX, int(raw_val)))
    v = (TRIGGER_MAX - clamped_raw) if invert else clamped_raw
    frac = float(v) / float(TRIGGER_MAX)
    reach = max(dz + 0.05, min(1.0, float(max_reach)))
    if frac <= dz:
        out = 0
    else:
        out = round(min(1.0, (frac - dz) / (reach - dz)) * float(TRIGGER_MAX))
    return apply_hair_trigger(out, hair_trigger)


def split_combined_pedals(raw_i16: int, swap_polarity: bool = False) -> tuple[int, int]:
    """Split a single signed [-32768..32767] combined pedal axis into (brake_lt, gas_rt)."""
    val = -int(raw_i16) if swap_polarity else int(raw_i16)
    if val < 0:
        gas = round(min(1.0, float(-val) / 32768.0) * float(TRIGGER_MAX))
        return 0, gas
    brake = round(min(1.0, float(val) / 32767.0) * float(TRIGGER_MAX))
    return brake, 0
