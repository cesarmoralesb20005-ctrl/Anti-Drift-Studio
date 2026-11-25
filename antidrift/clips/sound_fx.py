"""Friendly non-blocking synthesized notification chimes for Clips & Voice triggers."""

from __future__ import annotations

import array
import math
import os
import shutil
import subprocess
import wave
from pathlib import Path

SOUNDS_DIR = Path.home() / ".cache" / "antidrift-studio" / "sounds"
VOICE_CUE_WAV = SOUNDS_DIR / "voice_detected.wav"
CLIP_SAVED_WAV = SOUNDS_DIR / "clip_saved.wav"
BATTERY_CHARGING_WAV = SOUNDS_DIR / "battery_charging.wav"
BATTERY_UNPLUGGED_WAV = SOUNDS_DIR / "battery_unplugged.wav"
BATTERY_FULL_WAV = SOUNDS_DIR / "battery_full.wav"
BATTERY_LOW_WAV = SOUNDS_DIR / "battery_low.wav"
CONTROLLER_CONNECTED_WAV = SOUNDS_DIR / "controller_connected.wav"
CONTROLLER_DISCONNECTED_WAV = SOUNDS_DIR / "controller_disconnected.wav"

_SAMPLE_RATE = 44100

SOUND_PROFILES: tuple[tuple[str, str], ...] = (
    ("frutiger", "Frutiger Aero (Xbox 360)"),
    ("switch", "Nintendo Switch"),
    ("ps2", "Play Retro (PS2)"),
    ("suave", "Suave (Clásico)"),
)
_VALID_SOUND_PROFILES = {k for k, _ in SOUND_PROFILES}
_ACTIVE_SOUND_PROFILE = "frutiger"


def get_sound_profile() -> str:
    """Return currently active sound effect theme id."""
    return _ACTIVE_SOUND_PROFILE


def set_sound_profile(profile_id: str) -> str:
    """Set active sound effect theme id (falling back to 'frutiger' if unknown)."""
    global _ACTIVE_SOUND_PROFILE
    norm = (profile_id or "").strip().lower()
    _ACTIVE_SOUND_PROFILE = norm if norm in _VALID_SOUND_PROFILES else "frutiger"
    return _ACTIVE_SOUND_PROFILE


def _synth_notes_wav(
    path: Path,
    notes: list[tuple[float, float, float, float]],
    volume: float = 0.30,
    timbre: str = "warm",
) -> Path:
    """Synthesize smooth chime WAV from (start_sec, dur_sec, freq_hz, rel_amp) notes."""
    total_sec = max(s + d for s, d, _, _ in notes) + 0.06
    total_frames = int(total_sec * _SAMPLE_RATE)
    buf = [0.0] * total_frames

    for start_sec, dur_sec, freq_hz, rel_amp in notes:
        i0 = int(start_sec * _SAMPLE_RATE)
        n = int(dur_sec * _SAMPLE_RATE)
        attack_sec = 0.003 if timbre == "switch" else (0.042 if timbre == "ps2" else 0.010)
        decay_k = 7.5 if timbre == "switch" else (2.5 if timbre == "ps2" else 5.2)
        attack = max(1, int(attack_sec * _SAMPLE_RATE))
        for k in range(n):
            idx = i0 + k
            if idx >= total_frames:
                break
            t = k / float(_SAMPLE_RATE)
            env = min(1.0, k / float(attack)) * math.exp(-decay_k * (t / max(0.05, dur_sec)))
            if timbre == "frutiger":
                # Xbox 360 NXE liquid-glass / woody bubble synthesis with subtle pitch glide
                phase = 2.0 * math.pi * (
                    freq_hz * t - (0.14 * freq_hz / 38.0) * (1.0 - math.exp(-38.0 * t))
                )
                sample = (
                    math.sin(phase) * 0.72
                    + math.sin(phase * 2.0) * 0.22 * math.exp(-7.0 * t)
                    + math.sin(phase * 0.5) * 0.12 * math.exp(-5.0 * t)
                )
            elif timbre == "switch":
                # Snappy Joy-Con click transient + clean bright pop
                click = math.sin(2.0 * math.pi * (freq_hz * 2.5) * t) * math.exp(-85.0 * t) * 0.35
                sample = math.sin(2.0 * math.pi * freq_hz * t) * 0.85 + click
            elif timbre == "ps2":
                # Dreamy PS2 BIOS/Browser pad: slow-attack detuned chorus + crystalline glass shimmer
                sample = (
                    math.sin(2.0 * math.pi * (freq_hz * 0.9965) * t) * 0.42
                    + math.sin(2.0 * math.pi * (freq_hz * 1.0035) * t) * 0.42
                    + math.sin(2.0 * math.pi * (freq_hz * 2.0) * t) * 0.10
                    + math.sin(2.0 * math.pi * (freq_hz * 4.0) * t) * 0.06 * math.exp(-3.5 * t)
                )
            else:
                sample = (
                    math.sin(2.0 * math.pi * freq_hz * t) * 0.82
                    + math.sin(2.0 * math.pi * (freq_hz * 2.0) * t) * 0.18
                )
            buf[idx] += sample * env * rel_amp

    pcm = array.array("h")
    scale = 32767.0 * max(0.05, min(0.8, volume))
    for v in buf:
        clamped = max(-1.0, min(1.0, v))
        pcm.append(int(round(clamped * scale)))

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())
    return path


def ensure_sound_assets() -> tuple[Path, Path]:
    """Generate friendly WAV chimes on disk if missing."""
    if not VOICE_CUE_WAV.exists():
        # Subtle warm D5 pluck (90 ms) when wake word is heard
        _synth_notes_wav(
            VOICE_CUE_WAV,
            [(0.0, 0.095, 587.33, 0.85)],
            volume=0.22,
        )
    if not CLIP_SAVED_WAV.exists():
        # Friendly ascending major triad chime C5 -> E5 -> G5 (320 ms)
        _synth_notes_wav(
            CLIP_SAVED_WAV,
            [
                (0.00, 0.16, 523.25, 0.70),
                (0.075, 0.18, 659.25, 0.80),
                (0.15, 0.24, 783.99, 0.90),
            ],
            volume=0.34,
        )
    return VOICE_CUE_WAV, CLIP_SAVED_WAV


def _play_wav_async(wav_path: Path) -> None:
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return
    cmd: list[str] | None = None
    if shutil.which("pw-play"):
        cmd = ["pw-play", str(wav_path)]
    elif shutil.which("paplay"):
        cmd = ["paplay", str(wav_path)]
    elif shutil.which("aplay"):
        cmd = ["aplay", "-q", str(wav_path)]
    if cmd is None:
        return
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def play_voice_cue() -> None:
    """Play soft cue indicating wake phrase was recognized."""
    try:
        cue, _ = ensure_sound_assets()
        _play_wav_async(cue)
    except OSError:
        pass


def play_clip_saved_chime() -> None:
    """Play friendly chime confirming the clip was saved to disk."""
    try:
        _, saved = ensure_sound_assets()
        _play_wav_async(saved)
    except OSError:
        pass


_THEME_SPECS: dict[str, dict[str, tuple[list[tuple[float, float, float, float]], float]]] = {
    "frutiger": {
        "charging": ([(0.00, 0.18, 392.00, 0.72), (0.09, 0.28, 523.25, 0.85)], 0.28),
        "unplugged": ([(0.00, 0.16, 523.25, 0.72), (0.08, 0.24, 392.00, 0.66)], 0.25),
        "full": ([(0.00, 0.20, 392.00, 0.70), (0.08, 0.22, 523.25, 0.78), (0.17, 0.36, 783.99, 0.88)], 0.29),
        "low": ([(0.00, 0.20, 329.63, 0.76), (0.14, 0.26, 261.63, 0.82)], 0.26),
        "connected": ([(0.00, 0.18, 392.00, 0.72), (0.08, 0.20, 523.25, 0.80), (0.16, 0.34, 783.99, 0.88)], 0.28),
        "disconnected": ([(0.00, 0.18, 659.25, 0.72), (0.08, 0.20, 523.25, 0.68), (0.16, 0.28, 392.00, 0.60)], 0.25),
    },
    "switch": {
        "charging": ([(0.00, 0.08, 659.25, 0.75), (0.06, 0.14, 987.77, 0.88)], 0.24),
        "unplugged": ([(0.00, 0.08, 987.77, 0.72), (0.06, 0.13, 659.25, 0.65)], 0.22),
        "full": ([(0.00, 0.09, 523.25, 0.70), (0.06, 0.09, 659.25, 0.76), (0.12, 0.10, 783.99, 0.82), (0.18, 0.18, 1046.50, 0.90)], 0.26),
        "low": ([(0.00, 0.10, 659.25, 0.75), (0.10, 0.14, 523.25, 0.80)], 0.24),
        "connected": ([(0.00, 0.06, 783.99, 0.78), (0.055, 0.13, 1046.50, 0.90)], 0.25),
        "disconnected": ([(0.00, 0.07, 1046.50, 0.72), (0.06, 0.13, 783.99, 0.66)], 0.22),
    },
    "ps2": {
        "charging": ([(0.00, 0.34, 293.66, 0.65), (0.12, 0.44, 440.00, 0.78), (0.24, 0.56, 659.25, 0.82)], 0.27),
        "unplugged": ([(0.00, 0.32, 659.25, 0.68), (0.14, 0.46, 440.00, 0.60)], 0.24),
        "full": ([(0.00, 0.38, 220.00, 0.65), (0.12, 0.42, 329.63, 0.72), (0.24, 0.48, 493.88, 0.78), (0.36, 0.65, 659.25, 0.84)], 0.28),
        "low": ([(0.00, 0.36, 207.65, 0.72), (0.20, 0.48, 155.56, 0.78)], 0.25),
        "connected": ([(0.00, 0.38, 220.00, 0.65), (0.11, 0.42, 329.63, 0.74), (0.22, 0.50, 493.88, 0.80), (0.33, 0.66, 659.25, 0.85)], 0.27),
        "disconnected": ([(0.00, 0.34, 659.25, 0.68), (0.12, 0.38, 493.88, 0.62), (0.24, 0.52, 329.63, 0.56)], 0.24),
    },
    "suave": {
        "charging": ([(0.00, 0.12, 659.25, 0.70), (0.08, 0.18, 880.00, 0.85)], 0.24),
        "unplugged": ([(0.00, 0.12, 783.99, 0.75), (0.08, 0.18, 587.33, 0.65)], 0.22),
        "full": ([(0.00, 0.14, 523.25, 0.65), (0.07, 0.14, 659.25, 0.75), (0.14, 0.16, 783.99, 0.85), (0.21, 0.22, 1046.50, 0.85)], 0.26),
        "low": ([(0.00, 0.14, 440.00, 0.75), (0.11, 0.20, 349.23, 0.80)], 0.24),
        "connected": ([(0.00, 0.11, 523.25, 0.72), (0.075, 0.17, 783.99, 0.85)], 0.24),
        "disconnected": ([(0.00, 0.11, 783.99, 0.72), (0.075, 0.17, 523.25, 0.65)], 0.22),
    },
}


def ensure_profile_sound_assets(profile_id: str | None = None) -> dict[str, Path]:
    """Generate WAV chimes for the requested sound profile on disk if missing."""
    pid = (profile_id or _ACTIVE_SOUND_PROFILE).strip().lower()
    if pid not in _THEME_SPECS:
        pid = "frutiger"
    specs = _THEME_SPECS[pid]
    out: dict[str, Path] = {}
    for key, (notes, vol) in specs.items():
        wav = SOUNDS_DIR / pid / f"{key}.wav"
        if not wav.exists():
            _synth_notes_wav(wav, notes, volume=vol, timbre=pid)
        out[key] = wav
    return out


def ensure_battery_sound_assets(profile_id: str | None = None) -> dict[str, Path]:
    """Generate battery notification WAV chimes for the active sound profile."""
    all_assets = ensure_profile_sound_assets(profile_id)
    return {k: all_assets[k] for k in ("charging", "unplugged", "full", "low")}


def _play_battery_event(kind: str) -> None:
    try:
        wav = ensure_profile_sound_assets().get(kind)
        if wav is not None:
            _play_wav_async(wav)
    except OSError:
        pass


def play_battery_charging_chime() -> None:
    """Play gentle ascending chime when controller starts charging."""
    _play_battery_event("charging")


def play_battery_unplugged_chime() -> None:
    """Play gentle descending chime when charger is disconnected."""
    _play_battery_event("unplugged")


def play_battery_full_chime() -> None:
    """Play pleasant chime when controller battery reaches 100%."""
    _play_battery_event("full")


def play_battery_low_chime() -> None:
    """Play soft double-tone alert when controller battery is low."""
    _play_battery_event("low")


def ensure_connection_sound_assets(profile_id: str | None = None) -> dict[str, Path]:
    """Generate controller connect/disconnect WAV chimes for the active sound profile."""
    all_assets = ensure_profile_sound_assets(profile_id)
    return {k: all_assets[k] for k in ("connected", "disconnected")}


def play_controller_connected_chime() -> None:
    """Play friendly chime when a controller connects."""
    _play_battery_event("connected")


def play_controller_disconnected_chime() -> None:
    """Play gentle chime when a controller disconnects."""
    _play_battery_event("disconnected")
