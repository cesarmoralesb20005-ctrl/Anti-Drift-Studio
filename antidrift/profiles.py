"""Per-game profile persistence and active-game process matching."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from .config import AntiDriftConfig

DEFAULT_PROFILES_PATH = Path.home() / ".config" / "xbox-antidrift-profiles.json"

_PROFILE_META_FIELDS = {"id", "name", "auto_process"}


@dataclass
class GameProfile:
    """Reusable gameplay configuration profile preserving hardware stick centers."""

    id: str
    name: str
    auto_process: str = ""
    dz_l: float = 0.12
    dz_r: float = 0.12
    dz_mode: str = "axial"
    outer_comp_l: float = 0.0
    outer_comp_r: float = 0.0
    low_latency: bool = False
    response_curve: float = 1.00
    hair_trigger: bool = False
    debounce_ms: int = 0
    rumble_gain: int = 100
    swap_nintendo: bool = False
    lt_dz: float = 0.00
    rt_dz: float = 0.00
    lt_max: float = 1.00
    rt_max: float = 1.00
    lt_curve: float = 1.00
    rt_curve: float = 1.00
    wheel_mode: str = "auto"
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
    wheel_shifter_mode: str = "paddles"
    wheel_shifter_device: str = "auto"
    wheel_shifter_down_btn: str = "LB"
    wheel_shifter_up_btn: str = "RB"
    wheel_handbrake_enabled: bool = True
    wheel_handbrake_device: str = "auto"
    wheel_handbrake_target: str = "A"
    wheel_handbrake_dz: float = 0.20
    wheel_handbrake_invert: bool = False
    wheel_clutch_mode: str = "none"

    def copy(self) -> GameProfile:
        return replace(self)

    @classmethod
    def from_config(
        cls, profile_id: str, name: str, cfg: AntiDriftConfig, auto_process: str = ""
    ) -> GameProfile:
        kwargs = {
            f.name: getattr(cfg, f.name)
            for f in fields(cls)
            if f.name not in _PROFILE_META_FIELDS
        }
        return cls(id=profile_id, name=name, auto_process=auto_process, **kwargs)

    def apply_to_config(self, base: AntiDriftConfig) -> AntiDriftConfig:
        """Apply profile tuning while preserving calibrated stick centers and device."""
        updates = {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if f.name not in _PROFILE_META_FIELDS
        }
        return replace(base, **updates).clamped()


@dataclass
class ProfileStore:
    """Profiles file payload including active profile id and app preferences."""

    active_id: str
    auto_switch: bool
    minimize_to_tray: bool
    profiles: list[GameProfile]
    sound_profile: str = "frutiger"
    ui_theme: str = "solid_dark"


def default_profiles() -> list[GameProfile]:
    """Built-in starter profiles for popular games."""
    return [
        GameProfile(
            id="default",
            name="Estándar (General)",
            auto_process="",
            dz_l=0.14,
            dz_r=0.15,
            dz_mode="axial",
            low_latency=True,
            response_curve=1.00,
        ),
        GameProfile(
            id="rocket_league",
            name="Rocket League",
            auto_process="RocketLeague",
            dz_l=0.15,
            dz_r=0.18,
            dz_mode="axial",
            low_latency=True,
            response_curve=0.78,
            hair_trigger=False,
            debounce_ms=12,
        ),
        GameProfile(
            id="geometry_dash",
            name="Geometry Dash",
            auto_process="GeometryDash",
            dz_l=0.15,
            dz_r=0.15,
            dz_mode="radial",
            low_latency=True,
            response_curve=0.62,
            hair_trigger=True,
            debounce_ms=0,
        ),
        GameProfile(
            id="fps_shooters",
            name="Shooters / FPS",
            auto_process="",
            dz_l=0.12,
            dz_r=0.08,
            dz_mode="radial",
            low_latency=True,
            response_curve=0.78,
            hair_trigger=True,
            debounce_ms=0,
        ),
        GameProfile(
            id="racing_gamepad",
            name="Carreras (Mando · Freno 60%)",
            auto_process="",
            dz_l=0.08,
            dz_r=0.12,
            dz_mode="axial",
            low_latency=True,
            response_curve=1.00,
            hair_trigger=False,
            lt_dz=0.02,
            lt_max=0.60,
            lt_curve=1.20,
            rt_dz=0.02,
            rt_max=1.00,
            rt_curve=1.10,
        ),
        GameProfile(
            id="racing_wheel",
            name="Volante Racing (T80 / Sim)",
            auto_process="",
            dz_l=0.02,
            dz_r=0.02,
            dz_mode="axial",
            low_latency=True,
            response_curve=1.00,
            hair_trigger=False,
            debounce_ms=0,
            wheel_mode="wheel",
            wheel_rotation_deg=270,
            wheel_steer_dz=0.02,
            wheel_steer_sat=1.00,
            wheel_linearity=1.00,
            wheel_dz_gas=0.03,
            wheel_dz_brake=0.04,
            wheel_shifter_mode="paddles",
            wheel_handbrake_enabled=True,
        ),
    ]


def _profile_from_dict(item: dict, idx: int) -> GameProfile:
    defaults = GameProfile(
        id=str(item.get("id", f"p_{idx}")),
        name=str(item.get("name", f"Perfil {idx + 1}")),
        auto_process=str(item.get("auto_process", "")),
    )
    updates: dict[str, object] = {}
    for f in fields(GameProfile):
        if f.name in _PROFILE_META_FIELDS or f.name not in item:
            continue
        cur = getattr(defaults, f.name)
        val = item[f.name]
        if isinstance(cur, bool):
            updates[f.name] = bool(val)
        elif isinstance(cur, int):
            updates[f.name] = int(val)
        elif isinstance(cur, float):
            updates[f.name] = float(val)
        else:
            updates[f.name] = str(val)
    return replace(defaults, **updates)


def load_profile_store(path: Path = DEFAULT_PROFILES_PATH) -> ProfileStore:
    """Load saved profiles and UI preferences, creating defaults if missing."""
    if not path.exists():
        return ProfileStore(
            active_id="default",
            auto_switch=False,
            minimize_to_tray=False,
            profiles=default_profiles(),
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw_profiles = data.get("profiles") or []
        profiles = [_profile_from_dict(item, idx) for idx, item in enumerate(raw_profiles)]
        if not profiles:
            profiles = default_profiles()
        return ProfileStore(
            active_id=str(data.get("active_id", profiles[0].id)),
            auto_switch=bool(data.get("auto_switch", False)),
            minimize_to_tray=bool(data.get("minimize_to_tray", False)),
            profiles=profiles,
            sound_profile=str(data.get("sound_profile", "frutiger") or "frutiger"),
            ui_theme=str(data.get("ui_theme", "solid_dark") or "solid_dark"),
        )
    except (OSError, ValueError, TypeError):
        return ProfileStore(
            active_id="default",
            auto_switch=False,
            minimize_to_tray=False,
            profiles=default_profiles(),
        )


def save_profile_store(
    store: ProfileStore, path: Path = DEFAULT_PROFILES_PATH
) -> None:
    """Persist game profiles and preferences cleanly as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "active_id": store.active_id,
        "auto_switch": store.auto_switch,
        "minimize_to_tray": store.minimize_to_tray,
        "sound_profile": store.sound_profile,
        "ui_theme": store.ui_theme,
        "profiles": [asdict(p) for p in store.profiles],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _running_process_strings() -> list[str]:
    uid = os.getuid()
    results: list[str] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            comm = (entry / "comm").read_text(encoding="utf-8", errors="ignore").strip()
            cmd = (
                (entry / "cmdline")
                .read_bytes()
                .replace(b"\x00", b" ")
                .decode("utf-8", errors="ignore")
                .strip()
            )
            combined = f"{comm} {cmd}".lower()
            if combined.strip():
                results.append(combined)
        except OSError:
            continue
    return results


def detect_matching_profile(
    profiles: list[GameProfile], process_lines: list[str] | None = None
) -> GameProfile | None:
    """Return first profile whose auto_process matches any running user process."""
    candidates = [p for p in profiles if p.auto_process.strip()]
    if not candidates:
        return None
    lines = (
        _running_process_strings()
        if process_lines is None
        else [line.lower() for line in process_lines]
    )
    for prof in candidates:
        tokens = [
            tok.strip().lower()
            for tok in prof.auto_process.split(",")
            if tok.strip()
        ]
        for tok in tokens:
            if any(tok in line for line in lines):
                return prof
    return None
