"""Offline custom wake-phrase recognition using Vosk + PipeWire/PulseAudio."""

from __future__ import annotations

import array
from collections import deque
from difflib import SequenceMatcher
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
    "clip": ("clip", "clips", "clic", "clik", "clics", "cli", "clif", "klip"),
    "graba": ("graba", "grabar", "grava", "grabo", "grabe", "gravar"),
    "grabacion": ("grabacion", "grabaciones", "gravacion"),
    "guarda": ("guarda", "guardar", "guardo", "guarde", "guardia"),
    "guardalo": ("guardalo", "guardala", "guardame", "guardar"),
    "saca": ("saca", "sacar", "saco", "saque", "sacale"),
    "toma": ("toma", "tomar", "tomo", "tome"),
    "captura": ("captura", "capturar", "capturo", "capture"),
    "momento": ("momento", "momentos", "momentazo"),
    "replay": ("replay", "riplay", "ripley"),
    "eso": ("eso", "esto", "esa", "esta"),
    "video": ("video", "videos"),
}

_FILLER_TOKENS = frozenset({
    "el", "la", "los", "las", "un", "una", "unos", "unas",
    "este", "esta", "esto", "ese", "esa", "eso",
    "mi", "tu", "su", "de", "del", "al", "a",
    "por", "para", "ya", "me", "te", "lo", "le", "se",
})

_GRAMMAR_DISTRACTORS: tuple[str, ...] = (
    "el", "la", "los", "las", "un", "una", "de", "del", "que", "y", "a", "en",
    "por", "con", "no", "si", "es", "lo", "como", "pero", "para", "mi", "te",
    "me", "se", "yo", "tu", "ya", "hola", "bueno", "oye", "pues", "esta",
    "eso", "todo", "bien", "mal", "vamos", "vale", "claro", "ahora", "aqui",
)

_ACCENT_CHAR_MAP: dict[str, str] = {
    "a": "á",
    "e": "é",
    "i": "í",
    "o": "ó",
    "u": "ú",
    "n": "ñ",
}


def normalize_spoken_text(text: str) -> str:
    """Lowercase and strip accents/punctuation for reliable phrase matching."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^a-z0-9\s]", " ", stripped)
    return " ".join(cleaned.split())


def spanish_phonetic_key(token: str) -> str:
    """Fold Spanish orthographic variations (h, b/v, c/z/s, qu/k, ll/y, j/g)."""
    t = normalize_spoken_text(token).replace(" ", "")
    if not t:
        return ""
    t = re.sub(r"^h+", "", t)
    t = re.sub(r"(?<![c])h", "", t)
    t = t.replace("qu", "k").replace("gu", "g")
    t = re.sub(r"c([ei])", r"s\1", t)
    t = t.replace("c", "k").replace("q", "k")
    t = t.replace("z", "s").replace("v", "b").replace("w", "b")
    t = t.replace("ll", "y")
    t = re.sub(r"g([ei])", r"j\1", t)
    t = re.sub(r"(.)\1+", r"\1", t)
    return t


def resolve_model_word(model, word: str) -> str | None:
    """Find matching lowercase or accented Spanish word in Vosk model vocabulary."""
    low = word.strip().lower()
    norm = normalize_spoken_text(low)
    candidates: list[str] = []
    for base in (low, norm):
        if base and base not in candidates:
            candidates.append(base)
    if norm:
        for idx, ch in enumerate(norm):
            rep = _ACCENT_CHAR_MAP.get(ch)
            if rep:
                cand = norm[:idx] + rep + norm[idx + 1 :]
                if cand not in candidates:
                    candidates.append(cand)
    for cand in candidates:
        if model.vosk_model_find_word(cand) >= 0:
            return cand
    return None


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
    """Check whether words in user phrase exist in installed Vosk model (case/accent tolerant)."""
    model_dir = find_vosk_model_dir()
    if model_dir is None or not vosk_is_available():
        return True, []
    try:
        import vosk

        vosk.SetLogLevel(-1)
        model = vosk.Model(str(model_dir))
        unknown: list[str] = []
        for phrase in parse_wake_phrases(phrase_raw):
            for word in normalize_spoken_text(phrase).split():
                if (
                    resolve_model_word(model, word) is None
                    and word not in _WORD_EQUIVALENTS
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


def _single_token_matches(heard_tok: str, target_tok: str, strict_single: bool = False) -> bool:
    """Check if two normalized tokens match (equivalents, Spanish phonetics, or tight fuzzy)."""
    if heard_tok == target_tok:
        return True
    allowed = _WORD_EQUIVALENTS.get(target_tok, ())
    if heard_tok in allowed:
        return True

    h_phon = spanish_phonetic_key(heard_tok)
    t_phon = spanish_phonetic_key(target_tok)
    if h_phon and h_phon == t_phon:
        return True

    min_len = 5 if strict_single else 4
    if len(target_tok) >= min_len and len(heard_tok) >= min_len - 1:
        # Must share opening phoneme or first 2 chars so unrelated words don't cross-match
        if h_phon[:1] == t_phon[:1] or heard_tok[:2] == target_tok[:2]:
            ratio = SequenceMatcher(None, h_phon, t_phon).ratio()
            threshold = 0.84 if strict_single else 0.78
            if ratio >= threshold:
                return True
    return False


def _tokens_match(heard_tokens: list[str], target_tokens: list[str]) -> bool:
    """Accent-insensitive, phonetic, filler-tolerant match for 1+ word Spanish wake phrases."""
    if not target_tokens or not heard_tokens:
        return False

    if len(target_tokens) == 1:
        t0 = target_tokens[0]
        if any(_single_token_matches(h, t0, strict_single=True) for h in heard_tokens):
            return True
        # Match joined/merged two-word ASR output against 1 compound wake word (e.g. "guarda clip" -> "guardaclip")
        for i in range(len(heard_tokens) - 1):
            joined = heard_tokens[i] + heard_tokens[i + 1]
            if _single_token_matches(joined, t0, strict_single=True):
                return True
        return False

    # Multi-word phrase: allow split/merged compounds OR up to 1 Spanish filler token between words
    joined_target = "".join(target_tokens)
    if len(joined_target) >= 6 and any(
        _single_token_matches(h, joined_target, strict_single=True) for h in heard_tokens
    ):
        return True

    if len(heard_tokens) < len(target_tokens):
        return False

    for start in range(len(heard_tokens)):
        if not _single_token_matches(heard_tokens[start], target_tokens[0], strict_single=False):
            continue
        hi = start + 1
        ti = 1
        skipped_filler = 0
        while ti < len(target_tokens) and hi < len(heard_tokens):
            h_cur = heard_tokens[hi]
            t_cur = target_tokens[ti]
            if _single_token_matches(h_cur, t_cur, strict_single=False):
                ti += 1
                hi += 1
            elif skipped_filler < 2 and h_cur in _FILLER_TOKENS:
                skipped_filler += 1
                hi += 1
            else:
                break
        if ti == len(target_tokens):
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

    def _create_recognizers(self, vosk_mod, model, phrase_raw: str):
        # Hybrid dual-recognizer:
        # 1) Full Spanish decoder for general speech + fuzzy/phonetic matching + "Último oído"
        # 2) Optional grammar-boosted decoder (only for known model words) to boost multi-word accuracy
        phrases = parse_wake_phrases(phrase_raw)
        rec_full = vosk_mod.KaldiRecognizer(model, 16000)

        grammar_entries: list[str] = []
        seen: set[str] = set()
        for phrase in phrases:
            resolved_tokens: list[str] = []
            for tok in normalize_spoken_text(phrase).split():
                res_w = resolve_model_word(model, tok)
                if res_w is None:
                    resolved_tokens = []
                    break
                resolved_tokens.append(res_w)
            if resolved_tokens:
                resolved_phrase = " ".join(resolved_tokens)
                if resolved_phrase not in seen:
                    seen.add(resolved_phrase)
                    grammar_entries.append(resolved_phrase)
                for rt in resolved_tokens:
                    if rt not in seen:
                        seen.add(rt)
                        grammar_entries.append(rt)

        rec_grammar = None
        if grammar_entries:
            for d in _GRAMMAR_DISTRACTORS:
                if d not in seen and model.vosk_model_find_word(d) >= 0:
                    seen.add(d)
                    grammar_entries.append(d)
            grammar_entries.append("[unk]")
            try:
                rec_grammar = vosk_mod.KaldiRecognizer(
                    model, 16000, json.dumps(grammar_entries, ensure_ascii=False)
                )
            except Exception:
                rec_grammar = None

        return rec_full, rec_grammar, phrases

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
            rec, rec_grammar, phrases = self._create_recognizers(vosk, model, phrase_raw)
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
        last_speech_ts = 0.0
        last_unconstrained_ts = 0.0
        token_window: deque[tuple[float, str]] = deque(maxlen=16)

        def _fire_match(matched: str, now_ts: float) -> None:
            nonlocal last_trigger, suppress_heard_until
            last_trigger = now_ts
            suppress_heard_until = now_ts + 1.6
            token_window.clear()
            self.heard_text.emit(matched)
            self.phrase_detected.emit(matched)
            rec.Reset()
            if rec_grammar is not None:
                rec_grammar.Reset()

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
                    rec, rec_grammar, phrases = self._create_recognizers(vosk, model, phrase_raw)
                    token_window.clear()
                    self.status_changed.emit(
                        "listening", f"Escuchando «{', '.join(phrases)}»"
                    )

                chunk = self._proc.stdout.read(3200)  # ~100ms at 16kHz s16le mono
                if not chunk:
                    if self._proc.poll() is not None:
                        break
                    continue

                now = time.monotonic()
                rms = _pcm_rms_percent(chunk)
                if rms >= 3:
                    last_speech_ts = now
                if now - last_lvl_emit >= 0.08:
                    last_lvl_emit = now
                    self.audio_level.emit(rms)

                triggered = False
                if rec.AcceptWaveform(chunk):
                    res = json.loads(rec.Result())
                    text = res.get("text", "").strip()
                    if text and text != "[unk]":
                        last_speech_ts = now
                        last_unconstrained_ts = now
                        for tok in normalize_spoken_text(text).split():
                            token_window.append((now, tok))
                        recent_window = " ".join(
                            t for ts, t in token_window if now - ts <= 4.0
                        )
                        matched = match_wake_phrase(text, phrases) or match_wake_phrase(
                            recent_window, phrases
                        )
                        if matched and (now - last_trigger >= cooldown):
                            _fire_match(matched, now)
                            triggered = True
                        elif now >= suppress_heard_until:
                            self.heard_text.emit(text)
                else:
                    partial = json.loads(rec.PartialResult()).get("partial", "").strip()
                    if partial and partial != "[unk]":
                        last_speech_ts = now
                        last_unconstrained_ts = now
                        recent_window = " ".join(
                            t for ts, t in token_window if now - ts <= 3.5
                        )
                        combined = f"{recent_window} {partial}".strip()
                        matched = match_wake_phrase(partial, phrases) or match_wake_phrase(
                            combined, phrases
                        )
                        if matched and (now - last_trigger >= cooldown):
                            _fire_match(matched, now)
                            triggered = True

                # Grammar-boosted verifier catches multi-word & 5+ char accented/custom words cleanly
                if rec_grammar is not None and not triggered:
                    if rec_grammar.AcceptWaveform(chunk):
                        gres = json.loads(rec_grammar.Result())
                        gtext = gres.get("text", "").strip()
                        if (
                            gtext
                            and gtext != "[unk]"
                            and (now - last_speech_ts <= 1.8)
                            and (now - last_unconstrained_ts <= 1.8)
                        ):
                            gmatched = match_wake_phrase(gtext, phrases)
                            if gmatched and (now - last_trigger >= cooldown):
                                norm_g = normalize_spoken_text(gmatched)
                                g_toks = norm_g.split()
                                if len(g_toks) >= 2 or len(norm_g) >= 5:
                                    _fire_match(gmatched, now)
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
