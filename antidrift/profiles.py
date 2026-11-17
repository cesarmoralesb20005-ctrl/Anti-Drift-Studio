"""Per-game profile persistence and active-game process matching."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .config import AntiDriftConfig

DEFAULT_PROFILES_PATH = Path.home() / ".config" / "xbox-antidrift-profiles.json"


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

    def copy(self) -> GameProfile:
        return replace(self)

    @classmethod
    def from_config(
        cls, profile_id: str, name: str, cfg: AntiDriftConfig, auto_process: str = ""
    ) -> GameProfile:
        return cls(
            id=profile_id,
            name=name,
            auto_process=auto_process,
            dz_l=cfg.dz_l,
            dz_r=cfg.dz_r,
            dz_mode=cfg.dz_mode,
            outer_comp_l=cfg.outer_comp_l,
            outer_comp_r=cfg.outer_comp_r,
            low_latency=cfg.low_latency,
            response_curve=cfg.response_curve,
            hair_trigger=cfg.hair_trigger,
            debounce_ms=cfg.debounce_ms,
            rumble_gain=cfg.rumble_gain,
            swap_nintendo=cfg.swap_nintendo,
        )

    def apply_to_config(self, base: AntiDriftConfig) -> AntiDriftConfig:
        """Apply profile tuning while preserving calibrated stick centers and device."""
        return AntiDriftConfig(
            center_lx=base.center_lx,
            center_ly=base.center_ly,
            dz_l=self.dz_l,
            center_rx=base.center_rx,
            center_ry=base.center_ry,
            dz_r=self.dz_r,
            dz_mode=self.dz_mode,
            outer_comp_l=self.outer_comp_l,
            outer_comp_r=self.outer_comp_r,
            low_latency=self.low_latency,
            response_curve=self.response_curve,
            hair_trigger=self.hair_trigger,
            debounce_ms=self.debounce_ms,
            rumble_gain=self.rumble_gain,
            swap_nintendo=self.swap_nintendo,
            device_path=base.device_path,
        ).clamped()


@dataclass
class ProfileStore:
    """Profiles file payload including active profile id and app preferences."""

    active_id: str
    auto_switch: bool
    minimize_to_tray: bool
    profiles: list[GameProfile]


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
    ]


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
        profiles = [
            GameProfile(
                id=str(item.get("id", f"p_{idx}")),
                name=str(item.get("name", f"Perfil {idx + 1}")),
                auto_process=str(item.get("auto_process", "")),
                dz_l=float(item.get("dz_l", 0.12)),
                dz_r=float(item.get("dz_r", 0.12)),
                dz_mode=str(item.get("dz_mode", "axial")),
                outer_comp_l=float(item.get("outer_comp_l", 0.0)),
                outer_comp_r=float(item.get("outer_comp_r", 0.0)),
                low_latency=bool(item.get("low_latency", False)),
                response_curve=float(item.get("response_curve", 1.00)),
                hair_trigger=bool(item.get("hair_trigger", False)),
                debounce_ms=int(item.get("debounce_ms", 0)),
                rumble_gain=int(item.get("rumble_gain", 100)),
                swap_nintendo=bool(item.get("swap_nintendo", False)),
            )
            for idx, item in enumerate(raw_profiles)
        ]
        if not profiles:
            profiles = default_profiles()
        return ProfileStore(
            active_id=str(data.get("active_id", profiles[0].id)),
            auto_switch=bool(data.get("auto_switch", False)),
            minimize_to_tray=bool(data.get("minimize_to_tray", False)),
            profiles=profiles,
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
