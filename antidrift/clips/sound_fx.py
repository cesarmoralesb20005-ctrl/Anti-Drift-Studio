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

_SAMPLE_RATE = 44100


def _synth_notes_wav(
    path: Path,
    notes: list[tuple[float, float, float, float]],
    volume: float = 0.30,
) -> Path:
    """Synthesize smooth chime WAV from (start_sec, dur_sec, freq_hz, rel_amp) notes."""
    total_sec = max(s + d for s, d, _, _ in notes) + 0.05
    total_frames = int(total_sec * _SAMPLE_RATE)
    buf = [0.0] * total_frames

    for start_sec, dur_sec, freq_hz, rel_amp in notes:
        i0 = int(start_sec * _SAMPLE_RATE)
        n = int(dur_sec * _SAMPLE_RATE)
        attack = max(1, int(0.010 * _SAMPLE_RATE))
        for k in range(n):
            idx = i0 + k
            if idx >= total_frames:
                break
            t = k / float(_SAMPLE_RATE)
            env = min(1.0, k / float(attack)) * math.exp(-5.5 * (t / max(0.05, dur_sec)))
            # Warm fundamental + gentle octave overtone
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


def ensure_battery_sound_assets() -> dict[str, Path]:
    """Generate gentle battery notification WAV chimes on disk if missing."""
    specs: dict[str, tuple[Path, list[tuple[float, float, float, float]], float]] = {
        "charging": (
            BATTERY_CHARGING_WAV,
            [(0.00, 0.12, 659.25, 0.70), (0.08, 0.18, 880.00, 0.85)],
            0.24,
        ),
        "unplugged": (
            BATTERY_UNPLUGGED_WAV,
            [(0.00, 0.12, 783.99, 0.75), (0.08, 0.18, 587.33, 0.65)],
            0.22,
        ),
        "full": (
            BATTERY_FULL_WAV,
            [
                (0.00, 0.14, 523.25, 0.65),
                (0.07, 0.14, 659.25, 0.75),
                (0.14, 0.16, 783.99, 0.85),
                (0.21, 0.22, 1046.50, 0.85),
            ],
            0.26,
        ),
        "low": (
            BATTERY_LOW_WAV,
            [(0.00, 0.14, 440.00, 0.75), (0.11, 0.20, 349.23, 0.80)],
            0.24,
        ),
    }
    out: dict[str, Path] = {}
    for key, (path, notes, vol) in specs.items():
        if not path.exists():
            _synth_notes_wav(path, notes, volume=vol)
        out[key] = path
    return out


def _play_battery_event(kind: str) -> None:
    try:
        assets = ensure_battery_sound_assets()
        wav = assets.get(kind)
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
