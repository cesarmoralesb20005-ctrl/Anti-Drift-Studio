"""Configuration management and exact anti-drift math mirroring xbox-antidrift.c."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path

AXIS_MIN = -32768
AXIS_MAX = 32767
TRIGGER_MAX = 1023

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "xbox-antidrift.conf"
ENV_CONFIG_PATH = Path.home() / ".config" / "environment.d" / "xbox-antidrift.conf"

VALID_DZ_MODES = ("axial", "radial", "hybrid")


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
    device_path: str = "auto"

    def copy(self) -> AntiDriftConfig:
        """Return a shallow copy of the configuration."""
        return replace(self)

    def clamped(self) -> AntiDriftConfig:
        """Return a copy with fields clamped to safe operational bounds."""
        mode = self.dz_mode if self.dz_mode in VALID_DZ_MODES else "axial"
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
            device_path=(self.device_path or "auto").strip(),
        )


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
        key = key.strip()
        val_str = val_str.strip()
        if key == "device_path":
            cfg.device_path = val_str or "auto"
            continue
        if key == "dz_mode":
            if val_str in VALID_DZ_MODES:
                cfg.dz_mode = val_str
            elif val_str in ("0", "1", "2"):
                cfg.dz_mode = VALID_DZ_MODES[int(val_str)]
            continue
        try:
            val = float(val_str)
        except ValueError:
            continue
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
    return cfg.clamped()


def save_config(cfg: AntiDriftConfig, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Persist configuration in the exact format parsed by xbox-antidrift.c."""
    clean = cfg.clamped()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "# Configuracion universal Anti-Drift Studio\n"
        f"center_lx = {clean.center_lx}\n"
        f"center_ly = {clean.center_ly}\n"
        f"dz_l = {clean.dz_l:.2f}\n"
        f"center_rx = {clean.center_rx}\n"
        f"center_ry = {clean.center_ry}\n"
        f"dz_r = {clean.dz_r:.2f}\n"
        f"dz_mode = {clean.dz_mode}\n"
        f"outer_comp_l = {clean.outer_comp_l:.2f}\n"
        f"outer_comp_r = {clean.outer_comp_r:.2f}\n"
        f"low_latency = {1 if clean.low_latency else 0}\n"
        f"response_curve = {clean.response_curve:.2f}\n"
        f"hair_trigger = {1 if clean.hair_trigger else 0}\n"
        f"debounce_ms = {clean.debounce_ms}\n"
        f"rumble_gain = {clean.rumble_gain}\n"
        f"swap_nintendo = {1 if clean.swap_nintendo else 0}\n"
    )
    if clean.device_path and clean.device_path != "auto":
        content += f"device_path = {clean.device_path}\n"
    path.write_text(content, encoding="utf-8")


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
