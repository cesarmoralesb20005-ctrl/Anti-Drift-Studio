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
            controller_shortcut=(self.controller_shortcut or "none").strip(),
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
