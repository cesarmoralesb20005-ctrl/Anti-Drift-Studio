"""Offline custom wake-phrase recognition using Vosk + PipeWire/PulseAudio."""

from __future__ import annotations

import array
import json
import math
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .clips_config import (
    DEFAULT_VOSK_MODEL_NAME,
    DEFAULT_VOSK_MODEL_URL,
    MODELS_DIR,
)

# Common Spanish ASR phonetic near-homophones per token (space-bounded match only).
_WORD_EQUIVALENTS: dict[str, tuple[str, ...]] = {
    "clip": ("clip", "clips", "clic", "clik", "clics", "cli"),
    "graba": ("graba", "grabar", "grava", "grabo"),
    "guarda": ("guarda", "guardar", "guardo"),
    "replay": ("replay", "riplay", "ripley"),
}


def normalize_spoken_text(text: str) -> str:
    """Lowercase and strip accents/punctuation for reliable phrase matching."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^a-z0-9\s]", " ", stripped)
    return " ".join(cleaned.split())


def parse_wake_phrases(raw: str) -> list[str]:
    """Split comma-separated user wake phrases cleanly."""
    phrases: list[str] = []
    for part in raw.split(","):
        cleaned = " ".join(part.strip().lower().split())
        if cleaned:
            phrases.append(cleaned)
    return phrases or ["guarda clip"]


def find_vosk_model_dir() -> Path | None:
    """Return path to installed Vosk model if present."""
    preferred = MODELS_DIR / DEFAULT_VOSK_MODEL_NAME
    if (preferred / "conf" / "mfcc.conf").exists() or (preferred / "am").exists():
        return preferred
    if MODELS_DIR.exists():
        for child in sorted(MODELS_DIR.iterdir()):
            if child.is_dir() and child.name.startswith("vosk-model"):
                return child
    return None


def vosk_is_available() -> bool:
    """Return True if the vosk Python package imports cleanly."""
    try:
        import vosk  # noqa: F401
        return True
    except ImportError:
        return False


def validate_phrase_words(phrase_raw: str) -> tuple[bool, list[str]]:
    """Check whether words in user phrase exist in installed Vosk model."""
    model_dir = find_vosk_model_dir()
    if model_dir is None or not vosk_is_available():
        return True, []
    try:
        import vosk

        vosk.SetLogLevel(-1)
        model = vosk.Model(str(model_dir))
        unknown: list[str] = []
        for phrase in parse_wake_phrases(phrase_raw):
            for word in phrase.split():
                if (
                    model.vosk_model_find_word(word) < 0
                    and normalize_spoken_text(word) not in _WORD_EQUIVALENTS
                ):
                    unknown.append(word)
        return len(unknown) == 0, unknown
    except Exception:
        return True, []


def list_voice_microphones() -> list[tuple[str, str]]:
    """Return list of (pulse_source_id, friendly_label) for voice activation."""
    mics: list[tuple[str, str]] = [("", "Micrófono predeterminado del sistema")]
    if not shutil.which("pactl"):
        return mics
    try:
        proc = subprocess.run(
            ["pactl", "list", "sources"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        cur_name: str | None = None
        cur_desc: str | None = None
        for line in proc.stdout.splitlines():
            s = line.strip()
            if s.startswith("Name:"):
                cur_name = s.split(":", 1)[1].strip()
                cur_desc = cur_name
            elif s.startswith("Description:") and cur_name:
                cur_desc = s.split(":", 1)[1].strip()
            elif s.startswith("Properties:") and cur_name:
                if ".monitor" not in cur_name:
                    mics.append((cur_name, cur_desc or cur_name))
                cur_name = None
                cur_desc = None
    except (OSError, subprocess.TimeoutExpired):
        pass
    return mics


def _pcm_rms_percent(pcm_bytes: bytes) -> int:
    """Compute 0..100 RMS loudness from s16le mono bytes (Python 3.14 safe)."""
    n = len(pcm_bytes) // 2
    if n <= 0:
        return 0
    samples = array.array("h")
    samples.frombytes(pcm_bytes[: n * 2])
    mean_sq = sum(s * s for s in samples) / float(n)
    rms = math.sqrt(mean_sq)
    return max(0, min(100, round((rms / 8500.0) * 100.0)))


def _tokens_match(heard_tokens: list[str], target_tokens: list[str]) -> bool:
    """Whole-token contiguous match supporting clean Spanish ASR homophones."""
    if not target_tokens or len(heard_tokens) < len(target_tokens):
        return False
    span = len(target_tokens)
    for start in range(len(heard_tokens) - span + 1):
        ok = True
        for offset, target_tok in enumerate(target_tokens):
            heard_tok = heard_tokens[start + offset]
            allowed = _WORD_EQUIVALENTS.get(target_tok, (target_tok,))
            if heard_tok != target_tok and heard_tok not in allowed:
                ok = False
                break
        if ok:
            return True
    return False


def match_wake_phrase(recognized_text: str, wake_phrases: list[str]) -> str | None:
    """Return matching wake phrase if present in recognized text."""
    norm_heard = normalize_spoken_text(recognized_text)
    if not norm_heard:
        return None
    heard_tokens = norm_heard.split()
    for phrase in wake_phrases:
        norm_target = normalize_spoken_text(phrase)
        if not norm_target:
            continue
        target_tokens = norm_target.split()
        if _tokens_match(heard_tokens, target_tokens):
            return phrase
    return None


class ModelDownloadThread(QThread):
    """Downloads and extracts the compact offline Spanish Vosk model."""

    progress = Signal(int)
    finished_ok = Signal(str)
    failed = Signal(str)

    def run(self) -> None:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        zip_path = MODELS_DIR / f"{DEFAULT_VOSK_MODEL_NAME}.zip"
        try:
            with urllib.request.urlopen(DEFAULT_VOSK_MODEL_URL, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                with zip_path.open("wb") as out:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        out.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            self.progress.emit(min(95, round((downloaded / total) * 95)))

            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(MODELS_DIR)
            zip_path.unlink(missing_ok=True)
            self.progress.emit(100)
            self.finished_ok.emit(str(MODELS_DIR / DEFAULT_VOSK_MODEL_NAME))
        except Exception as exc:
            zip_path.unlink(missing_ok=True)
            self.failed.emit(f"Error descargando modelo de voz: {exc}")


class VoiceTriggerThread(QThread):
    """Continuously listens offline for custom wake phrase via PipeWire/PulseAudio."""

    phrase_detected = Signal(str)
    heard_text = Signal(str)
    audio_level = Signal(int)
    status_changed = Signal(str, str)  # state, detail

    def __init__(
        self,
        phrase: str,
        mic_source: str = "",
        cooldown_sec: float = 3.0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._phrase_raw = phrase
        self._mic_source = mic_source
        self._cooldown_sec = max(1.5, cooldown_sec)
        self._reload_recognizer = False
        self._stop_requested = False
        self._proc: subprocess.Popen | None = None

    def set_phrase(self, phrase: str) -> None:
        with self._lock:
            self._phrase_raw = phrase
            self._reload_recognizer = True

    def set_cooldown(self, cooldown_sec: float) -> None:
        with self._lock:
            self._cooldown_sec = max(1.5, cooldown_sec)

    def stop(self) -> None:
        self._stop_requested = True
        proc = self._proc
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass
        self.wait(2000)

    def _build_capture_cmd(self) -> list[str] | None:
        if shutil.which("parec"):
            cmd = [
                "parec",
                "--format=s16le",
                "--rate=16000",
                "--channels=1",
                "--latency-msec=80",
            ]
            if self._mic_source:
                cmd.extend(["-d", self._mic_source])
            return cmd
        if shutil.which("pw-record"):
            cmd = [
                "pw-record",
                "--rate",
                "16000",
                "--channels",
                "1",
                "--format",
                "s16",
                "--raw",
            ]
            if self._mic_source:
                cmd.extend(["--target", self._mic_source])
            cmd.append("-")
            return cmd
        return None

    def _create_recognizer(self, vosk_mod, model, phrase_raw: str):
        # Full unconstrained Spanish decoder prevents short/foreign triggers like
        # "clip" from being silently discarded by strict "[unk]" grammars.
        phrases = parse_wake_phrases(phrase_raw)
        return vosk_mod.KaldiRecognizer(model, 16000), phrases

    def run(self) -> None:
        if not vosk_is_available():
            self.status_changed.emit(
                "error", "Falta paquete Python 'vosk'. Ejecuta ./install.sh."
            )
            return

        model_dir = find_vosk_model_dir()
        if model_dir is None:
            self.status_changed.emit(
                "missing_model",
                "Falta modelo de voz offline (~38 MB). Pulsa 'Descargar modelo'.",
            )
            return

        cmd = self._build_capture_cmd()
        if cmd is None:
            self.status_changed.emit(
                "error", "No se encontró 'parec' ni 'pw-record' para capturar micrófono."
            )
            return

        try:
            import vosk

            vosk.SetLogLevel(-1)
            self.status_changed.emit("loading", "Cargando modelo de voz offline...")
            model = vosk.Model(str(model_dir))
            with self._lock:
                phrase_raw = self._phrase_raw
                self._reload_recognizer = False
            rec, phrases = self._create_recognizer(vosk, model, phrase_raw)
        except Exception as exc:
            self.status_changed.emit("error", f"Error cargando Vosk: {exc}")
            return

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            self.status_changed.emit("error", f"Error abriendo micrófono: {exc}")
            return

        self.status_changed.emit(
            "listening", f"Escuchando «{', '.join(phrases)}»"
        )
        last_trigger = 0.0
        suppress_heard_until = 0.0
        last_lvl_emit = 0.0

        try:
            assert self._proc.stdout is not None
            while not self._stop_requested:
                with self._lock:
                    need_reload = self._reload_recognizer
                    phrase_raw = self._phrase_raw
                    cooldown = self._cooldown_sec
                    if need_reload:
                        self._reload_recognizer = False

                if need_reload:
                    phrases = parse_wake_phrases(phrase_raw)
                    rec.Reset()
                    self.status_changed.emit(
                        "listening", f"Escuchando «{', '.join(phrases)}»"
                    )

                chunk = self._proc.stdout.read(3200)  # ~100ms at 16kHz s16le mono
                if not chunk:
                    if self._proc.poll() is not None:
                        break
                    continue

                now = time.monotonic()
                if now - last_lvl_emit >= 0.08:
                    last_lvl_emit = now
                    self.audio_level.emit(_pcm_rms_percent(chunk))

                if rec.AcceptWaveform(chunk):
                    res = json.loads(rec.Result())
                    text = res.get("text", "").strip()
                    if text and text != "[unk]":
                        matched = match_wake_phrase(text, phrases)
                        if matched and (now - last_trigger >= cooldown):
                            last_trigger = now
                            suppress_heard_until = now + 1.6
                            self.heard_text.emit(matched)
                            self.phrase_detected.emit(matched)
                            rec.Reset()
                        elif now >= suppress_heard_until:
                            self.heard_text.emit(text)
                else:
                    partial = json.loads(rec.PartialResult()).get("partial", "").strip()
                    if partial and partial != "[unk]":
                        matched = match_wake_phrase(partial, phrases)
                        if matched and (now - last_trigger >= cooldown):
                            last_trigger = now
                            suppress_heard_until = now + 1.6
                            self.heard_text.emit(matched)
                            self.phrase_detected.emit(matched)
                            rec.Reset()
        finally:
            self.audio_level.emit(0)
            if self._proc is not None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=1.5)
                except Exception:
                    try:
                        self._proc.kill()
                    except OSError:
                        pass
                self._proc = None
            if not self._stop_requested:
                self.status_changed.emit("stopped", "Escucha detenida")
