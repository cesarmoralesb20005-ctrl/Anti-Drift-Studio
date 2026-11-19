"""System-wide PipeWire/PulseAudio equalizer and audio mixer package."""

from .eq_config import (
    DEFAULT_EQ_CONFIG_PATH,
    EQ_BAND_FREQS,
    EQ_BAND_LABELS,
    EQ_BAND_Q,
    EQ_BAND_TYPES,
    EQ_PRESETS,
    EqualizerConfig,
    compute_eq_curve_db,
    compute_stereo_dsp_params,
    effective_preamp_db,
    load_eq_config,
    save_eq_config,
)

__all__ = [
    "DEFAULT_EQ_CONFIG_PATH",
    "EQ_BAND_FREQS",
    "EQ_BAND_LABELS",
    "EQ_BAND_Q",
    "EQ_BAND_TYPES",
    "EQ_PRESETS",
    "EqualizerConfig",
    "compute_eq_curve_db",
    "compute_stereo_dsp_params",
    "effective_preamp_db",
    "load_eq_config",
    "save_eq_config",
]
