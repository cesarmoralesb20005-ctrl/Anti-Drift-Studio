"""Persistence and schema for Instant Replay Clips & Voice Trigger settings."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path

DEFAULT_CLIPS_CONFIG_PATH = Path.home() / ".config" / "xbox-antidrift-clips.json"
DEFAULT_CLIPS_DIR = Path.home() / "Videos" / "AntiDrift-Clips"
MODELS_DIR = Path.home() / ".local" / "share" / "antidrift-studio" / "models"
DEFAULT_VOSK_MODEL_NAME = "vosk-model-small-es-0.42"
DEFAULT_VOSK_MODEL_URL = (
    "https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip"
)
DEFAULT_SAFE_HOTKEY = "Alt+Shift+C"

_TTY_HOTKEY_RE = re.compile(r"ctrl\+alt\+f([1-9]|1[0-2])$", re.IGNORECASE)

PAD_TOKEN_ORDER: tuple[str, ...] = (
    "GUIDE",
    "SHARE",
    "VIEW",
    "MENU",
    "LB",
    "RB",
    "LT",
    "RT",
    "LS",
    "RS",
    "UP",
    "DOWN",
    "LEFT",
    "RIGHT",
    "A",
    "B",
    "X",
    "Y",
    "P1",
    "P2",
    "P3",
    "P4",
    "TOUCHPAD",
    "MUTE",
)

_PAD_ALIASES: dict[str, str] = {
    "NONE": "none",
    "NINGUNO": "none",
    "SELECT": "VIEW",
    "BACK": "VIEW",
    "START": "MENU",
    "OPTIONS": "MENU",
    "HOME": "GUIDE",
    "XBOX": "GUIDE",
    "PS": "GUIDE",
    "CAPTURA": "SHARE",
    "CAPTURE": "SHARE",
    "CREATE": "SHARE",
    "KEY_167": "SHARE",
    "BTN_167": "SHARE",
    "L1": "LB",
    "R1": "RB",
    "L2": "LT",
    "R2": "RT",
    "L3": "LS",
    "R3": "RS",
    "ARRIBA": "UP",
    "ABAJO": "DOWN",
    "IZQUIERDA": "LEFT",
    "DERECHA": "RIGHT",
    "DPAD_UP": "UP",
    "DPAD_DOWN": "DOWN",
    "DPAD_LEFT": "LEFT",
    "DPAD_RIGHT": "RIGHT",
    "KEY_704": "P1",
    "KEY_705": "P2",
    "KEY_706": "P3",
    "KEY_707": "P4",
    "BTN_704": "P1",
    "BTN_705": "P2",
    "BTN_706": "P3",
    "BTN_707": "P4",
    "KEY_272": "TOUCHPAD",
    "KEY_325": "TOUCHPAD",
    "BTN_LEFT": "TOUCHPAD",
    "KEY_248": "MUTE",
    "KEY_712": "MUTE",
}

_EXTRA_CODE_TO_TOKEN: dict[int, str] = {
    167: "SHARE",
    248: "MUTE",
    272: "TOUCHPAD",
    325: "TOUCHPAD",
    704: "P1",
    705: "P2",
    706: "P3",
    707: "P4",
    712: "MUTE",
}

_PAD_DISPLAY_NAMES: dict[str, str] = {
    "VIEW": "View",
    "MENU": "Menu",
    "GUIDE": "Guía",
    "SHARE": "Share / Captura",
    "TOUCHPAD": "Touchpad",
    "MUTE": "Mute",
    "UP": "D-Pad ↑",
    "DOWN": "D-Pad ↓",
    "LEFT": "D-Pad ←",
    "RIGHT": "D-Pad →",
}


def _pad_token_sort_key(tok: str) -> tuple[int, int, str]:
    if tok in PAD_TOKEN_ORDER:
        return (0, PAD_TOKEN_ORDER.index(tok), tok)
    if tok.startswith("KEY_") and tok[4:].isdigit():
        return (1, int(tok[4:]), tok)
    return (2, 0, tok)


def normalize_controller_shortcut(combo: str) -> str:
    """Normalize a controller button chord string (e.g. 'RB + View' -> 'VIEW+RB')."""
    raw = (combo or "").strip()
    if not raw or raw.lower() in ("none", "ninguno"):
        return "none"
    tokens: set[str] = set()
    for part in raw.split("+"):
        u = part.strip().upper().replace(" ", "_")
        if not u:
            continue
        u = _PAD_ALIASES.get(u, u)
        if u == "none":
            continue
        if u.startswith("#") and u[1:].isdigit():
            code = int(u[1:])
            u = _EXTRA_CODE_TO_TOKEN.get(code, f"KEY_{code}")
        elif u.startswith("BTN_") and u[4:].isdigit():
            code = int(u[4:])
            u = _EXTRA_CODE_TO_TOKEN.get(code, f"KEY_{code}")
        elif u.isdigit():
            code = int(u)
            u = _EXTRA_CODE_TO_TOKEN.get(code, f"KEY_{code}")
        tokens.add(u)
    if not tokens:
        return "none"
    return "+".join(sorted(tokens, key=_pad_token_sort_key))


def format_controller_shortcut_label(combo: str) -> str:
    """Return user-friendly Spanish label for a controller shortcut."""
    norm = normalize_controller_shortcut(combo)
    if norm == "none":
        return "Ninguno"
    if norm == "P4":
        return "Paddle P4"
    parts = [_PAD_DISPLAY_NAMES.get(t, t) for t in norm.split("+")]
    return " + ".join(parts)


def extract_pressed_pad_tokens(state) -> list[str]:
    """Extract canonical pressed button/trigger/dpad/extra tokens from ControllerState."""
    pressed: set[str] = set()
    for name, is_down in getattr(state, "buttons", {}).items():
        if is_down:
            u = str(name).strip().upper()
            pressed.add(_PAD_ALIASES.get(u, u))
    if getattr(state, "lt", 0) >= 600:
        pressed.add("LT")
    if getattr(state, "rt", 0) >= 600:
        pressed.add("RT")
    hx = getattr(state, "hat_x", 0)
    hy = getattr(state, "hat_y", 0)
    if hy < 0:
        pressed.add("UP")
    elif hy > 0:
        pressed.add("DOWN")
    if hx < 0:
        pressed.add("LEFT")
    elif hx > 0:
        pressed.add("RIGHT")
    for code, is_down in getattr(state, "extra_buttons", {}).items():
        if is_down:
            pressed.add(_EXTRA_CODE_TO_TOKEN.get(int(code), f"KEY_{int(code)}"))
    return sorted(pressed, key=_pad_token_sort_key)


def controller_shortcut_matches(shortcut: str, state) -> bool:
    """Return True if all buttons in controller shortcut are currently pressed."""
    norm = normalize_controller_shortcut(shortcut)
    if norm == "none":
        return False
    required = set(norm.split("+"))
    pressed = set(extract_pressed_pad_tokens(state))
    return required.issubset(pressed)


def is_tty_hotkey(combo: str) -> bool:
    """Return True if hotkey matches Linux virtual-terminal switch Ctrl+Alt+F1..F12."""
    normalized = "+".join(p.strip().lower() for p in combo.split("+") if p.strip())
    return bool(_TTY_HOTKEY_RE.match(normalized))


def sanitize_hotkey(combo: str) -> str:
    """Ensure hotkey is non-empty and never uses Linux TTY shortcut Ctrl+Alt+F1..12."""
    cleaned = (combo or "").strip()
    if not cleaned or is_tty_hotkey(cleaned):
        return DEFAULT_SAFE_HOTKEY
    return cleaned


@dataclass
class ClipsConfig:
    """Configuration for GPU Screen Recorder replay buffer and triggers."""

    buffer_enabled: bool = False
    replay_seconds: int = 30
    fps: int = 60
    quality: str = "very_high"  # medium | high | very_high | ultra
    codec: str = "h264"  # h264 | hevc | av1
    capture_target: str = "auto"  # auto | portal | screen | monitor_name
    record_desktop_audio: bool = True
    desktop_audio_source: str = "default_output"
    record_mic_audio: bool = False
    mic_audio_source: str = "default_input"
    output_dir: str = str(DEFAULT_CLIPS_DIR)

    voice_enabled: bool = False
    voice_phrase: str = "guarda clip"
    voice_mic_source: str = ""  # empty = Pulse/PipeWire default source
    voice_cooldown_sec: float = 3.0

    hotkey_combo: str = DEFAULT_SAFE_HOTKEY
    controller_shortcut: str = "none"  # none | VIEW+RB | VIEW+LB | P4
    sound_feedback: bool = True

    def copy(self) -> ClipsConfig:
        return replace(self)

    def clamped(self) -> ClipsConfig:
        q = (
            self.quality
            if self.quality in ("medium", "high", "very_high", "ultra")
            else "very_high"
        )
        c = self.codec if self.codec in ("h264", "hevc", "av1") else "h264"
        return ClipsConfig(
            buffer_enabled=bool(self.buffer_enabled),
            replay_seconds=max(5, min(600, int(self.replay_seconds))),
            fps=max(24, min(240, int(self.fps))),
            quality=q,
            codec=c,
            capture_target=(self.capture_target or "auto").strip(),
            record_desktop_audio=bool(self.record_desktop_audio),
            desktop_audio_source=(self.desktop_audio_source or "default_output").strip(),
            record_mic_audio=bool(self.record_mic_audio),
            mic_audio_source=(self.mic_audio_source or "default_input").strip(),
            output_dir=(self.output_dir or str(DEFAULT_CLIPS_DIR)).strip(),
            voice_enabled=bool(self.voice_enabled),
            voice_phrase=(self.voice_phrase or "guarda clip").strip(),
            voice_mic_source=(self.voice_mic_source or "").strip(),
            voice_cooldown_sec=max(1.5, min(30.0, float(self.voice_cooldown_sec))),
            hotkey_combo=sanitize_hotkey(self.hotkey_combo),
            controller_shortcut=normalize_controller_shortcut(self.controller_shortcut),
            sound_feedback=bool(self.sound_feedback),
        )


def load_clips_config(path: Path = DEFAULT_CLIPS_CONFIG_PATH) -> ClipsConfig:
    """Load clips configuration from disk or return clean defaults."""
    if not path.exists():
        return ClipsConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cfg = ClipsConfig(
            buffer_enabled=bool(data.get("buffer_enabled", False)),
            replay_seconds=int(data.get("replay_seconds", 30)),
            fps=int(data.get("fps", 60)),
            quality=str(data.get("quality", "very_high")),
            codec=str(data.get("codec", "h264")),
            capture_target=str(data.get("capture_target", "auto")),
            record_desktop_audio=bool(data.get("record_desktop_audio", True)),
            desktop_audio_source=str(data.get("desktop_audio_source", "default_output")),
            record_mic_audio=bool(data.get("record_mic_audio", False)),
            mic_audio_source=str(data.get("mic_audio_source", "default_input")),
            output_dir=str(data.get("output_dir", str(DEFAULT_CLIPS_DIR))),
            voice_enabled=bool(data.get("voice_enabled", False)),
            voice_phrase=str(data.get("voice_phrase", "guarda clip")),
            voice_mic_source=str(data.get("voice_mic_source", "")),
            voice_cooldown_sec=float(data.get("voice_cooldown_sec", 3.0)),
            hotkey_combo=str(data.get("hotkey_combo", DEFAULT_SAFE_HOTKEY)),
            controller_shortcut=str(data.get("controller_shortcut", "none")),
            sound_feedback=bool(data.get("sound_feedback", True)),
        )
        return cfg.clamped()
    except (OSError, ValueError, TypeError):
        return ClipsConfig()


def save_clips_config(
    cfg: ClipsConfig, path: Path = DEFAULT_CLIPS_CONFIG_PATH
) -> None:
    """Persist clips configuration as UTF-8 JSON."""
    clean = cfg.clamped()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(clean), indent=2, ensure_ascii=False), encoding="utf-8"
    )
