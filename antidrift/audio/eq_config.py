"""Configuration schema, presets, and RBJ biquad DSP math for the System Equalizer."""

from __future__ import annotations

import cmath
import json
import math
import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

DEFAULT_EQ_CONFIG_PATH = Path.home() / ".config" / "xbox-antidrift-eq.json"


def default_eq_config_path() -> Path:
    """Return active EQ config JSON path, honoring XDG_CONFIG_HOME if set."""
    cfg_root = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(cfg_root) if cfg_root else (Path.home() / ".config")
    return base / "xbox-antidrift-eq.json"

EQ_BAND_FREQS: tuple[int, ...] = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
EQ_BAND_LABELS: tuple[str, ...] = (
    "31",
    "62",
    "125",
    "250",
    "500",
    "1k",
    "2k",
    "4k",
    "8k",
    "16k",
)
EQ_BAND_TYPES: tuple[str, ...] = (
    "lowshelf",
    "peaking",
    "peaking",
    "peaking",
    "peaking",
    "peaking",
    "peaking",
    "peaking",
    "peaking",
    "highshelf",
)
EQ_BAND_Q: tuple[float, ...] = (
    0.707,
    1.41,
    1.41,
    1.41,
    1.41,
    1.41,
    1.41,
    1.41,
    1.41,
    0.707,
)

EQ_MIN_DB = -12.0
EQ_MAX_DB = 12.0

EQ_PRESETS: dict[str, dict[str, object]] = {
    "Plano (Neutro)": {
        "preamp_db": 0.0,
        "bands": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "desc": "Respuesta original sin colorear el audio.",
    },
    "Gaming / Pasos FPS": {
        "preamp_db": 0.0,
        "bands": [-2.0, -1.5, -2.5, -2.0, -0.5, 1.5, 4.0, 5.5, 4.5, 2.0],
        "desc": "Limpia graves retumbantes y realza pasos, recargas y detalle espacial.",
    },
    "Motores / Racing": {
        "preamp_db": 0.0,
        "bands": [4.0, 5.5, 4.0, 2.5, 0.5, 2.0, 4.0, 4.5, 2.5, -1.5],
        "desc": "Realza el rugido grave del escape, medios del motor y silbido del turbo.",
    },
    "Graves Potentes": {
        "preamp_db": 0.0,
        "bands": [6.5, 5.5, 3.5, 1.5, 0.0, 0.0, 0.5, 1.0, 1.5, 1.5],
        "desc": "Subgraves profundos con pegada limpia sin apagar voces.",
    },
    "Voces y Discord": {
        "preamp_db": 0.0,
        "bands": [-5.0, -4.0, -2.0, 0.5, 2.5, 4.0, 4.5, 3.0, 1.0, -0.5],
        "desc": "Elimina ruido grave y da máxima nitidez a diálogos y llamadas.",
    },
    "Música (V-Shape)": {
        "preamp_db": 0.0,
        "bands": [4.5, 3.5, 1.5, -1.0, -1.5, -0.5, 1.5, 3.0, 4.5, 4.0],
        "desc": "Realce cálido de graves y agudos brillantes para música.",
    },
    "Rock / Metal": {
        "preamp_db": 0.0,
        "bands": [3.5, 2.5, 1.0, -1.5, -2.0, 0.5, 2.5, 4.0, 3.5, 3.0],
        "desc": "Pega fuerte en bombos, guitarras definidas y platillos vivos.",
    },
    "Electrónica / EDM": {
        "preamp_db": 0.0,
        "bands": [5.5, 4.5, 2.0, -0.5, -1.5, 0.5, 2.0, 3.5, 4.5, 5.0],
        "desc": "Subgraves contundentes y brillo en sintetizadores.",
    },
    "Modo Nocturno": {
        "preamp_db": 0.0,
        "bands": [-5.0, -3.5, -1.5, 1.0, 2.0, 2.0, 1.0, -1.5, -3.5, -5.0],
        "desc": "Suaviza explosiones y agudos estridentes para escuchar de noche.",
    },
}


def _clamp_db(val: float) -> float:
    return round(max(EQ_MIN_DB, min(EQ_MAX_DB, float(val))), 1)


def _clamp_bands(raw: object) -> list[float]:
    if not isinstance(raw, (list, tuple)):
        return [0.0] * len(EQ_BAND_FREQS)
    out = [_clamp_db(v) for v in raw[: len(EQ_BAND_FREQS)]]
    while len(out) < len(EQ_BAND_FREQS):
        out.append(0.0)
    return out


@dataclass
class EqualizerConfig:
    """Persistent state for the 10-band system-wide equalizer."""

    enabled: bool = False
    bypass: bool = False
    output_sink: str = "auto"
    preamp_db: float = 0.0
    auto_preamp: bool = True
    balance: float = 0.0  # -1.0 (left) .. 0.0 (center) .. +1.0 (right)
    band_gains: list[float] = field(default_factory=lambda: [0.0] * len(EQ_BAND_FREQS))
    active_preset: str = "Plano (Neutro)"
    custom_presets: dict[str, dict[str, object]] = field(default_factory=dict)
    previous_default_sink: str = ""
    stereo_enabled: bool = False
    stereo_width: float = 0.5
    surround_enabled: bool = False

    def copy(self) -> EqualizerConfig:
        return replace(
            self,
            band_gains=list(self.band_gains),
            custom_presets={
                k: {"preamp_db": float(v.get("preamp_db", 0.0)), "bands": list(v.get("bands", []))}
                for k, v in self.custom_presets.items()
            },
        )

    def clamped(self) -> EqualizerConfig:
        clean_custom: dict[str, dict[str, object]] = {}
        if isinstance(self.custom_presets, dict):
            for name, payload in self.custom_presets.items():
                cname = str(name).strip()
                if not cname or not isinstance(payload, dict):
                    continue
                clean_custom[cname] = {
                    "preamp_db": _clamp_db(float(payload.get("preamp_db", 0.0))),
                    "bands": _clamp_bands(payload.get("bands")),
                }
        return EqualizerConfig(
            enabled=bool(self.enabled),
            bypass=bool(self.bypass),
            output_sink=(str(self.output_sink or "auto").strip() or "auto"),
            preamp_db=_clamp_db(self.preamp_db),
            auto_preamp=bool(self.auto_preamp),
            balance=round(max(-1.0, min(1.0, float(self.balance))), 2),
            band_gains=_clamp_bands(self.band_gains),
            active_preset=(str(self.active_preset or "Personalizado").strip() or "Personalizado"),
            custom_presets=clean_custom,
            previous_default_sink=str(self.previous_default_sink or "").strip(),
            stereo_enabled=bool(self.stereo_enabled),
            stereo_width=round(max(0.0, min(1.0, float(self.stereo_width))), 2),
            surround_enabled=bool(self.surround_enabled),
        )


def compute_stereo_dsp_params(
    enabled: bool, width: float, bypass: bool = False
) -> dict[str, float]:
    """Compute Mid/Side + 250 Hz mono bass + Treble Exciter + Haas delay parameters.

    Mirrors musicb7's Stereo Amplification Engine:
    - Keeps <250 Hz mono via high-pass filter on the Side channel.
    - Scales Side gain (1.0 + width * 1.5), applies 2-25 ms Haas delay,
      and excites upper treble bands (4k/8k/16k) on the Side channel.
    - When disabled or bypassed, switches cleanly to bit-transparent raw Side passthrough.
    """
    w_clamped = max(0.0, min(1.0, float(width)))
    if not enabled or bypass:
        return {
            "side_gain": 0.0,
            "haas_gain": 0.0,
            "raw_side_gain": 1.0,
            "haas_delay_ms": 12.0,
            "haas_delay_s": 0.012,
            "exciter_4k_db": 0.0,
            "exciter_8k_db": 0.0,
            "exciter_16k_db": 0.0,
        }
    side_w = 1.0 + w_clamped * 1.5
    delay_ms = float(int(max(2.0, min(25.0, w_clamped * 20.0))))
    exc_boost = w_clamped * 10.0
    return {
        "side_gain": round(side_w, 4),
        "haas_gain": round(side_w * w_clamped * 0.65, 4),
        "raw_side_gain": 0.0,
        "haas_delay_ms": delay_ms,
        "haas_delay_s": round(delay_ms / 1000.0, 4),
        "exciter_4k_db": round(exc_boost * 0.4, 2),
        "exciter_8k_db": round(exc_boost * 0.7, 2),
        "exciter_16k_db": round(exc_boost, 2),
    }


def compute_surround_dsp_params(
    enabled: bool, bypass: bool = False
) -> dict[str, float]:
    """Compute head-wrap 3D surround parameters keeping core music anchored."""
    if not enabled or bypass:
        return {
            "direct_gain": 1.0,
            "wrap_gain": 0.0,
            "wrap_gain_r": 0.0,
            "cross_gain": 0.0,
            "cross_gain_r": 0.0,
        }
    return {
        "direct_gain": 1.0,
        "wrap_gain": 0.44,
        "wrap_gain_r": -0.44,
        "cross_gain": -0.28,
        "cross_gain_r": 0.28,
    }


def effective_preamp_db(
    band_gains: list[float], preamp_db: float, auto_preamp: bool, bypass: bool = False
) -> float:
    """Compute effective preamp gain in dB (compensating positive peaks when auto_preamp is on)."""
    if bypass:
        return 0.0
    base = _clamp_db(preamp_db)
    if auto_preamp and band_gains:
        peak_pos = max(0.0, *(float(g) for g in band_gains))
        # Ensure preamp + peak_pos does not exceed 0 dB headroom
        base = min(base, -peak_pos)
    return round(max(-24.0, min(12.0, base)), 2)


def biquad_coefficients(
    filter_type: str,
    freq_hz: float,
    q: float,
    gain_db: float,
    sample_rate: float = 48000.0,
) -> tuple[float, float, float, float, float, float]:
    """Return normalized RBJ biquad coefficients (b0, b1, b2, a0, a1, a2)."""
    if abs(gain_db) < 1e-6:
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    w0 = 2.0 * math.pi * max(5.0, min(freq_hz, sample_rate * 0.49)) / sample_rate
    cos_w = math.cos(w0)
    sin_w = math.sin(w0)
    a_amp = 10.0 ** (gain_db / 40.0)
    alpha = sin_w / (2.0 * max(0.1, q))

    if filter_type == "lowshelf":
        beta = 2.0 * math.sqrt(a_amp) * alpha
        b0 = a_amp * ((a_amp + 1.0) - (a_amp - 1.0) * cos_w + beta)
        b1 = 2.0 * a_amp * ((a_amp - 1.0) - (a_amp + 1.0) * cos_w)
        b2 = a_amp * ((a_amp + 1.0) - (a_amp - 1.0) * cos_w - beta)
        a0 = (a_amp + 1.0) + (a_amp - 1.0) * cos_w + beta
        a1 = -2.0 * ((a_amp - 1.0) + (a_amp + 1.0) * cos_w)
        a2 = (a_amp + 1.0) + (a_amp - 1.0) * cos_w - beta
    elif filter_type == "highshelf":
        beta = 2.0 * math.sqrt(a_amp) * alpha
        b0 = a_amp * ((a_amp + 1.0) + (a_amp - 1.0) * cos_w + beta)
        b1 = -2.0 * a_amp * ((a_amp - 1.0) + (a_amp + 1.0) * cos_w)
        b2 = a_amp * ((a_amp + 1.0) + (a_amp - 1.0) * cos_w - beta)
        a0 = (a_amp + 1.0) - (a_amp - 1.0) * cos_w + beta
        a1 = 2.0 * ((a_amp - 1.0) - (a_amp + 1.0) * cos_w)
        a2 = (a_amp + 1.0) - (a_amp - 1.0) * cos_w - beta
    else:
        b0 = 1.0 + alpha * a_amp
        b1 = -2.0 * cos_w
        b2 = 1.0 - alpha * a_amp
        a0 = 1.0 + alpha / a_amp
        a1 = -2.0 * cos_w
        a2 = 1.0 - alpha / a_amp
    return (b0, b1, b2, a0, a1, a2)


def compute_eq_curve_db(
    band_gains: list[float],
    preamp_db: float = 0.0,
    auto_preamp: bool = False,
    freqs_hz: list[float] | None = None,
    bypass: bool = False,
    sample_rate: float = 48000.0,
) -> list[float]:
    """Evaluate total EQ frequency response (dB) across the requested frequencies."""
    if freqs_hz is None:
        freqs_hz = [20.0 * (1000.0 ** (i / 119.0)) for i in range(120)]
    if bypass:
        return [0.0 for _ in freqs_hz]

    gains = _clamp_bands(band_gains)
    eff_pre = effective_preamp_db(gains, preamp_db, auto_preamp, bypass=False)
    filters = [
        biquad_coefficients(ftype, float(f0), q, g, sample_rate)
        for ftype, f0, q, g in zip(EQ_BAND_TYPES, EQ_BAND_FREQS, EQ_BAND_Q, gains)
        if abs(g) >= 1e-6
    ]

    curve: list[float] = []
    for f in freqs_hz:
        w = 2.0 * math.pi * f / sample_rate
        z1 = cmath.exp(-1j * w)
        z2 = z1 * z1
        mag_sq = 1.0
        for b0, b1, b2, a0, a1, a2 in filters:
            num = b0 + b1 * z1 + b2 * z2
            den = a0 + a1 * z1 + a2 * z2
            ratio = num / den
            mag_sq *= ratio.real * ratio.real + ratio.imag * ratio.imag
        db = eff_pre + (10.0 * math.log10(max(1e-12, mag_sq)))
        curve.append(round(db, 2))
    return curve


def load_eq_config(path: Path | None = None) -> EqualizerConfig:
    """Load equalizer configuration from JSON or return flat defaults."""
    target = path if path is not None else default_eq_config_path()
    if not target.exists():
        return EqualizerConfig()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        cfg = EqualizerConfig(
            enabled=bool(data.get("enabled", False)),
            bypass=bool(data.get("bypass", False)),
            output_sink=str(data.get("output_sink", "auto")),
            preamp_db=float(data.get("preamp_db", 0.0)),
            auto_preamp=bool(data.get("auto_preamp", True)),
            balance=float(data.get("balance", 0.0)),
            band_gains=_clamp_bands(data.get("band_gains")),
            active_preset=str(data.get("active_preset", "Plano (Neutro)")),
            custom_presets=data.get("custom_presets", {}) if isinstance(data.get("custom_presets"), dict) else {},
            previous_default_sink=str(data.get("previous_default_sink", "")),
            stereo_enabled=bool(data.get("stereo_enabled", False)),
            stereo_width=float(data.get("stereo_width", 0.5)),
            surround_enabled=bool(data.get("surround_enabled", False)),
        )
        return cfg.clamped()
    except (OSError, ValueError, TypeError):
        return EqualizerConfig()


def save_eq_config(cfg: EqualizerConfig, path: Path | None = None) -> None:
    """Persist equalizer configuration to UTF-8 JSON."""
    target = path if path is not None else default_eq_config_path()
    clean = cfg.clamped()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(asdict(clean), indent=2, ensure_ascii=False), encoding="utf-8")
