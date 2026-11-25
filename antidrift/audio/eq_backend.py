"""PipeWire & PulseAudio system-wide equalizer, device routing, and mixer backend."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .eq_config import (
    EQ_BAND_FREQS,
    EQ_BAND_Q,
    EQ_BAND_TYPES,
    EqualizerConfig,
    compute_stereo_dsp_params,
    compute_surround_dsp_params,
    effective_preamp_db,
)

EQ_SINK_NAME = "antidrift_eq_sink"
EQ_PLAYBACK_NAME = "antidrift_eq_out"
EQ_SINK_DESC = "Anti-Drift Studio EQ"
EQ_DIR = Path.home() / ".config" / "antidrift-studio"
EQ_CONF_PATH = EQ_DIR / "eq-filter-chain.conf"
EQ_PID_PATH = EQ_DIR / "eq-pipewire.pid"
_SOFA_CANDIDATES = (
    Path("/usr/share/libmysofa/MIT_KEMAR_normal_pinna.sofa"),
    Path("/usr/share/libmysofa/default.sofa"),
)
_SOFA_PLUGIN_CANDIDATES = (
    Path("/usr/lib/x86_64-linux-gnu/spa-0.2/filter-graph/libspa-filter-graph-plugin-sofa.so"),
    Path("/usr/lib/spa-0.2/filter-graph/libspa-filter-graph-plugin-sofa.so"),
    Path("/usr/lib64/spa-0.2/filter-graph/libspa-filter-graph-plugin-sofa.so"),
)


def find_sofa_hrtf_path() -> str | None:
    """Return path to installed SOFA HRTF dataset if PipeWire sofa plugin is available."""
    if not any(p.exists() for p in _SOFA_PLUGIN_CANDIDATES):
        return None
    for cand in _SOFA_CANDIDATES:
        if cand.exists():
            return str(cand)
    return None
EQ_SERVICE_NAME = "antidrift-eq.service"
EQ_SERVICE_PATH = Path.home() / ".config" / "systemd" / "user" / EQ_SERVICE_NAME


@dataclass
class AudioSinkInfo:
    """One physical or virtual audio output device."""

    name: str
    description: str
    volume_pct: int = 100
    balance: float = 0.0
    muted: bool = False
    is_default: bool = False
    is_eq_sink: bool = False


@dataclass
class AudioAppStream:
    """One active application playback stream (sink-input)."""

    index: int
    name: str
    app_binary: str
    sink_id: str
    volume_pct: int = 100
    muted: bool = False


@dataclass
class EqBackendStatus:
    """Live runtime state of the system equalizer."""

    active: bool
    backend: str  # pipewire | pulseaudio | unavailable
    sink_name: str
    target_sink: str
    message: str


def _run(args: list[str], timeout: float = 4.0) -> tuple[int, str, str]:
    env = dict(os.environ)
    env.setdefault("LC_ALL", "C")
    try:
        p = subprocess.run(
            args,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
        )
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)


def _clean_desc(val: object) -> str:
    txt = str(val or "").strip()
    return "" if txt in ("", "(null)", "null", "None") else txt


def _parse_pct(raw: object) -> int:
    s = str(raw or "100%").strip().rstrip("%")
    try:
        return max(0, min(150, int(round(float(s)))))
    except ValueError:
        return 100


def _extract_volume_and_balance(vol_obj: object, balance_val: object = 0.0) -> tuple[int, float]:
    pcts: list[int] = []
    if isinstance(vol_obj, dict):
        for ch_info in vol_obj.values():
            if isinstance(ch_info, dict) and "value_percent" in ch_info:
                pcts.append(_parse_pct(ch_info["value_percent"]))
    max_pct = max(pcts) if pcts else 100
    try:
        bal = round(max(-1.0, min(1.0, float(balance_val or 0.0))), 2)
    except (TypeError, ValueError):
        bal = 0.0
    return max_pct, bal


def is_pipewire_session() -> bool:
    """Return True if PipeWire + pw-cli/pw-dump are available as the active audio server."""
    if not shutil.which("pipewire") or not shutil.which("pw-cli"):
        return False
    rc, out, _ = _run(["pactl", "info"])
    if rc == 0 and "pipewire" in out.lower():
        return True
    rc2, _, _ = _run(["pw-cli", "info", "0"])
    return rc2 == 0


def get_default_sink_name() -> str:
    """Return current default audio sink name via pactl."""
    rc, out, _ = _run(["pactl", "get-default-sink"])
    if rc == 0 and out.strip():
        return out.strip()
    rc2, info_out, _ = _run(["pactl", "info"])
    if rc2 == 0:
        for line in info_out.splitlines():
            if line.lower().startswith("default sink:"):
                return line.split(":", 1)[1].strip()
    return ""


def list_sinks(include_eq: bool = False) -> list[AudioSinkInfo]:
    """List available audio output sinks with clean human-readable names."""
    default_name = get_default_sink_name()
    sinks: list[AudioSinkInfo] = []

    rc, out, _ = _run(["pactl", "-f", "json", "list", "sinks"])
    if rc == 0 and out.strip().startswith("["):
        try:
            items = json.loads(out)
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                is_eq = name == EQ_SINK_NAME
                if is_eq and not include_eq:
                    continue
                props = item.get("properties") or {}
                desc = (
                    _clean_desc(props.get("device.description"))
                    or _clean_desc(props.get("node.description"))
                    or _clean_desc(item.get("description"))
                    or _clean_desc(props.get("node.nick"))
                    or name
                )
                active_port = str(item.get("active_port") or "")
                for port in item.get("ports") or []:
                    if isinstance(port, dict) and port.get("name") == active_port:
                        pdesc = _clean_desc(port.get("description"))
                        if pdesc and pdesc.lower() not in desc.lower():
                            desc = f"{desc} · {pdesc}"
                        break
                vol_pct, bal = _extract_volume_and_balance(
                    item.get("volume"), item.get("balance", 0.0)
                )
                sinks.append(
                    AudioSinkInfo(
                        name=name,
                        description=desc,
                        volume_pct=vol_pct,
                        balance=bal,
                        muted=bool(item.get("mute", False)),
                        is_default=(name == default_name),
                        is_eq_sink=is_eq,
                    )
                )
            return sinks
        except (ValueError, TypeError):
            pass

    # Plain-text fallback for older pactl
    rc_txt, out_txt, _ = _run(["pactl", "list", "short", "sinks"])
    if rc_txt == 0:
        for line in out_txt.splitlines():
            cols = line.split("\t")
            if len(cols) >= 2:
                name = cols[1].strip()
                is_eq = name == EQ_SINK_NAME
                if is_eq and not include_eq:
                    continue
                sinks.append(
                    AudioSinkInfo(
                        name=name,
                        description=name,
                        is_default=(name == default_name),
                        is_eq_sink=is_eq,
                    )
                )
    return sinks


def list_physical_sinks() -> list[AudioSinkInfo]:
    """Return physical output sinks (excluding Anti-Drift Studio EQ sink)."""
    return list_sinks(include_eq=False)


def eq_sink_exists() -> bool:
    """Return True if antidrift_eq_sink is currently active in the audio graph."""
    return any(s.name == EQ_SINK_NAME for s in list_sinks(include_eq=True))


def get_active_sink_info() -> AudioSinkInfo | None:
    """Return volume/balance/mute info for the current default sink."""
    all_sinks = list_sinks(include_eq=True)
    for s in all_sinks:
        if s.is_default:
            return s
    return all_sinks[0] if all_sinks else None


def resolve_target_sink(cfg: EqualizerConfig) -> str:
    """Pick the best physical output sink for EQ playback or fallback restore."""
    phys = list_physical_sinks()
    phys_names = {s.name for s in phys}
    if cfg.output_sink and cfg.output_sink != "auto" and cfg.output_sink in phys_names:
        return cfg.output_sink
    cur_def = get_default_sink_name()
    if cur_def and cur_def != EQ_SINK_NAME and cur_def in phys_names:
        return cur_def
    if cfg.previous_default_sink and cfg.previous_default_sink in phys_names:
        return cfg.previous_default_sink
    return phys[0].name if phys else ""


def set_sink_volume_and_balance(sink_name: str, volume_pct: int, balance: float = 0.0) -> bool:
    """Set master sink volume (0..150%) and stereo L/R balance (-1.0..+1.0)."""
    if not sink_name:
        return False
    vol = max(0, min(150, int(volume_pct)))
    bal = max(-1.0, min(1.0, float(balance)))
    left_scale = 1.0 if bal <= 0.0 else max(0.0, 1.0 - bal)
    right_scale = 1.0 if bal >= 0.0 else max(0.0, 1.0 + bal)
    left_pct = max(0, min(150, int(round(vol * left_scale))))
    right_pct = max(0, min(150, int(round(vol * right_scale))))
    rc, _, _ = _run(["pactl", "set-sink-volume", sink_name, f"{left_pct}%", f"{right_pct}%"])
    return rc == 0


def set_sink_mute(sink_name: str, muted: bool) -> bool:
    if not sink_name:
        return False
    rc, _, _ = _run(["pactl", "set-sink-mute", sink_name, "1" if muted else "0"])
    return rc == 0


def list_sink_inputs() -> list[AudioAppStream]:
    """Return active application playback streams (excluding our internal EQ node)."""
    streams: list[AudioAppStream] = []
    rc, out, _ = _run(["pactl", "-f", "json", "list", "sink-inputs"])
    if rc != 0 or not out.strip().startswith("["):
        return streams
    try:
        items = json.loads(out)
    except (ValueError, TypeError):
        return streams

    for item in items:
        if not isinstance(item, dict):
            continue
        props = item.get("properties") or {}
        node_name = str(props.get("node.name") or "")
        media_name = str(props.get("media.name") or "")
        if node_name == EQ_PLAYBACK_NAME or media_name == EQ_SINK_DESC:
            continue
        idx = int(item.get("index", -1))
        if idx < 0:
            continue
        app_name = (
            _clean_desc(props.get("application.name"))
            or _clean_desc(props.get("node.description"))
            or _clean_desc(media_name)
            or _clean_desc(node_name)
            or f"App #{idx}"
        )
        binary = _clean_desc(props.get("application.process.binary"))
        vol_pct, _ = _extract_volume_and_balance(item.get("volume"), 0.0)
        streams.append(
            AudioAppStream(
                index=idx,
                name=app_name,
                app_binary=binary,
                sink_id=str(item.get("sink", "")),
                volume_pct=vol_pct,
                muted=bool(item.get("mute", False)),
            )
        )
    return streams


def set_sink_input_volume(index: int, volume_pct: int) -> bool:
    pct = max(0, min(150, int(volume_pct)))
    rc, _, _ = _run(["pactl", "set-sink-input-volume", str(int(index)), f"{pct}%"])
    return rc == 0


def set_sink_input_mute(index: int, muted: bool) -> bool:
    rc, _, _ = _run(["pactl", "set-sink-input-mute", str(int(index)), "1" if muted else "0"])
    return rc == 0


def move_all_streams_to_sink(target_sink: str) -> None:
    """Route all active application playback streams cleanly into target_sink."""
    if not target_sink:
        return
    for st in list_sink_inputs():
        _run(["pactl", "move-sink-input", str(st.index), target_sink])


def generate_pipewire_eq_conf(cfg: EqualizerConfig, target_sink: str = "") -> str:
    """Build PipeWire filter-chain config: Mid/Side + Haas Stereo Engine + 10-Band Dual EQ."""
    eff_pre = effective_preamp_db(
        cfg.band_gains, cfg.preamp_db, cfg.auto_preamp, bypass=cfg.bypass
    )
    mult = 1.0 if cfg.bypass else round(10.0 ** (eff_pre / 20.0), 6)
    gains = [0.0] * len(EQ_BAND_FREQS) if cfg.bypass else list(cfg.band_gains)
    st = compute_stereo_dsp_params(cfg.stereo_enabled, cfg.stereo_width, bypass=cfg.bypass)
    sr = compute_surround_dsp_params(cfg.surround_enabled, bypass=cfg.bypass)

    nodes_lines = [
        '                    { type = builtin name = inL label = copy }',
        '                    { type = builtin name = inR label = copy }',
        '                    { type = builtin name = st_mid label = mixer control = { "Gain 1" = 0.5 "Gain 2" = 0.5 } }',
        '                    { type = builtin name = st_side_raw label = mixer control = { "Gain 1" = 0.5 "Gain 2" = -0.5 } }',
        '                    { type = builtin name = st_hp label = bq_highpass control = { "Freq" = 250.0 "Q" = 0.707 } }',
        f'                    {{ type = builtin name = st_exc_4k label = bq_peaking control = {{ "Freq" = 4000.0 "Q" = 1.410 "Gain" = {st["exciter_4k_db"]:.2f} }} }}',
        f'                    {{ type = builtin name = st_exc_8k label = bq_peaking control = {{ "Freq" = 8000.0 "Q" = 1.410 "Gain" = {st["exciter_8k_db"]:.2f} }} }}',
        f'                    {{ type = builtin name = st_exc_16k label = bq_highshelf control = {{ "Freq" = 16000.0 "Q" = 0.707 "Gain" = {st["exciter_16k_db"]:.2f} }} }}',
        f'                    {{ type = builtin name = st_haas label = delay config = {{ "max-delay" = 1 }} control = {{ "Delay (s)" = {st["haas_delay_s"]:.4f} "Feedback" = 0.0 "Feedforward" = 0.0 }} }}',
        f'                    {{ type = builtin name = st_side_blend label = mixer control = {{ "Gain 1" = {st["side_gain"]:.4f} "Gain 2" = {st["haas_gain"]:.4f} "Gain 3" = {st["raw_side_gain"]:.4f} }} }}',
        '                    { type = builtin name = st_outL label = mixer control = { "Gain 1" = 1.0 "Gain 2" = 1.0 } }',
        '                    { type = builtin name = st_outR label = mixer control = { "Gain 1" = 1.0 "Gain 2" = -1.0 } }',
    ]
    sofa_file = find_sofa_hrtf_path()
    if sofa_file:
        nodes_lines.extend(
            [
                f'                    {{ type = sofa name = surround_sofa_l label = spatializer config = {{ "filename" = "{sofa_file}" "normalize" = true }} control = {{ "Azimuth" = 125.0 "Elevation" = 18.0 "Radius" = 1.6 }} }}',
                f'                    {{ type = sofa name = surround_sofa_r label = spatializer config = {{ "filename" = "{sofa_file}" "normalize" = true }} control = {{ "Azimuth" = -125.0 "Elevation" = 18.0 "Radius" = 1.6 }} }}',
                f'                    {{ type = builtin name = surround_blendL label = mixer control = {{ "Gain 1" = {sr["direct_gain"]:.4f} "Gain 2" = {sr["sofa_gain"]:.4f} "Gain 3" = {sr["sofa_gain"]:.4f} }} }}',
                f'                    {{ type = builtin name = surround_blendR label = mixer control = {{ "Gain 1" = {sr["direct_gain"]:.4f} "Gain 2" = {sr["sofa_gain"]:.4f} "Gain 3" = {sr["sofa_gain"]:.4f} }} }}',
                f'                    {{ type = builtin name = preamp label = linear control = {{ "Mult" = {mult:.6f} "Add" = 0.0 }} }}',
                f'                    {{ type = builtin name = preampR label = linear control = {{ "Mult" = {mult:.6f} "Add" = 0.0 }} }}',
            ]
        )
    else:
        nodes_lines.extend(
            [
                '                    { type = builtin name = surround_apL label = bq_allpass control = { "Freq" = 520.0 "Q" = 0.707 } }',
                '                    { type = builtin name = surround_apR label = bq_allpass control = { "Freq" = 1900.0 "Q" = 0.707 } }',
                '                    { type = builtin name = surround_wrapDelL label = delay config = { "max-delay" = 1 } control = { "Delay (s)" = 0.0180 "Feedback" = 0.0 "Feedforward" = 0.0 } }',
                '                    { type = builtin name = surround_wrapDelR label = delay config = { "max-delay" = 1 } control = { "Delay (s)" = 0.0290 "Feedback" = 0.0 "Feedforward" = 0.0 } }',
                '                    { type = builtin name = surround_delL label = delay config = { "max-delay" = 1 } control = { "Delay (s)" = 0.0110 "Feedback" = 0.0 "Feedforward" = 0.0 } }',
                '                    { type = builtin name = surround_delR label = delay config = { "max-delay" = 1 } control = { "Delay (s)" = 0.0080 "Feedback" = 0.0 "Feedforward" = 0.0 } }',
                '                    { type = builtin name = surround_lpL label = bq_lowpass control = { "Freq" = 5500.0 "Q" = 0.707 } }',
                '                    { type = builtin name = surround_lpR label = bq_lowpass control = { "Freq" = 5500.0 "Q" = 0.707 } }',
                f'                    {{ type = builtin name = surround_blendL label = mixer control = {{ "Gain 1" = {sr["direct_gain"]:.4f} "Gain 2" = {sr["wrap_gain"]:.4f} "Gain 3" = {sr["cross_gain"]:.4f} }} }}',
                f'                    {{ type = builtin name = surround_blendR label = mixer control = {{ "Gain 1" = {sr["direct_gain"]:.4f} "Gain 2" = {sr["wrap_gain_r"]:.4f} "Gain 3" = {sr["cross_gain_r"]:.4f} }} }}',
                f'                    {{ type = builtin name = preamp label = linear control = {{ "Mult" = {mult:.6f} "Add" = 0.0 }} }}',
                f'                    {{ type = builtin name = preampR label = linear control = {{ "Mult" = {mult:.6f} "Add" = 0.0 }} }}',
            ]
        )

    links_lines = [
        '                    { output = "inL:Out" input = "st_mid:In 1" }',
        '                    { output = "inR:Out" input = "st_mid:In 2" }',
        '                    { output = "inL:Out" input = "st_side_raw:In 1" }',
        '                    { output = "inR:Out" input = "st_side_raw:In 2" }',
        '                    { output = "st_side_raw:Out" input = "st_hp:In" }',
        '                    { output = "st_hp:Out" input = "st_exc_4k:In" }',
        '                    { output = "st_exc_4k:Out" input = "st_exc_8k:In" }',
        '                    { output = "st_exc_8k:Out" input = "st_exc_16k:In" }',
        '                    { output = "st_exc_16k:Out" input = "st_haas:In" }',
        '                    { output = "st_exc_16k:Out" input = "st_side_blend:In 1" }',
        '                    { output = "st_haas:Out" input = "st_side_blend:In 2" }',
        '                    { output = "st_side_raw:Out" input = "st_side_blend:In 3" }',
        '                    { output = "st_mid:Out" input = "st_outL:In 1" }',
        '                    { output = "st_side_blend:Out" input = "st_outL:In 2" }',
        '                    { output = "st_mid:Out" input = "st_outR:In 1" }',
        '                    { output = "st_side_blend:Out" input = "st_outR:In 2" }',
    ]
    if sofa_file:
        links_lines.extend(
            [
                '                    { output = "st_outL:Out" input = "surround_sofa_l:In" }',
                '                    { output = "st_outR:Out" input = "surround_sofa_r:In" }',
                '                    { output = "st_outL:Out" input = "surround_blendL:In 1" }',
                '                    { output = "surround_sofa_l:Out L" input = "surround_blendL:In 2" }',
                '                    { output = "surround_sofa_r:Out L" input = "surround_blendL:In 3" }',
                '                    { output = "st_outR:Out" input = "surround_blendR:In 1" }',
                '                    { output = "surround_sofa_l:Out R" input = "surround_blendR:In 2" }',
                '                    { output = "surround_sofa_r:Out R" input = "surround_blendR:In 3" }',
                '                    { output = "surround_blendL:Out" input = "preamp:In" }',
                '                    { output = "surround_blendR:Out" input = "preampR:In" }',
                '                    { output = "preamp:Out" input = "b1:In" }',
                '                    { output = "preampR:Out" input = "b1R:In" }',
            ]
        )
    else:
        links_lines.extend(
            [
                '                    { output = "st_side_blend:Out" input = "surround_apL:In" }',
                '                    { output = "st_side_blend:Out" input = "surround_apR:In" }',
                '                    { output = "surround_apL:Out" input = "surround_wrapDelL:In" }',
                '                    { output = "surround_apR:Out" input = "surround_wrapDelR:In" }',
                '                    { output = "surround_wrapDelL:Out" input = "surround_lpL:In" }',
                '                    { output = "surround_wrapDelR:Out" input = "surround_lpR:In" }',
                '                    { output = "surround_lpL:Out" input = "surround_delL:In" }',
                '                    { output = "surround_lpR:Out" input = "surround_delR:In" }',
                '                    { output = "st_outL:Out" input = "surround_blendL:In 1" }',
                '                    { output = "surround_lpL:Out" input = "surround_blendL:In 2" }',
                '                    { output = "surround_delR:Out" input = "surround_blendL:In 3" }',
                '                    { output = "st_outR:Out" input = "surround_blendR:In 1" }',
                '                    { output = "surround_lpR:Out" input = "surround_blendR:In 2" }',
                '                    { output = "surround_delL:Out" input = "surround_blendR:In 3" }',
                '                    { output = "surround_blendL:Out" input = "preamp:In" }',
                '                    { output = "surround_blendR:Out" input = "preampR:In" }',
                '                    { output = "preamp:Out" input = "b1:In" }',
                '                    { output = "preampR:Out" input = "b1R:In" }',
            ]
        )

    for idx, (freq, ftype, q_val, gain) in enumerate(
        zip(EQ_BAND_FREQS, EQ_BAND_TYPES, EQ_BAND_Q, gains), start=1
    ):
        label = f"bq_{ftype}"
        nodes_lines.append(
            f'                    {{ type = builtin name = b{idx} label = {label} control = {{ "Freq" = {float(freq):.1f} "Q" = {q_val:.3f} "Gain" = {float(gain):.2f} }} }}'
        )
        nodes_lines.append(
            f'                    {{ type = builtin name = b{idx}R label = {label} control = {{ "Freq" = {float(freq):.1f} "Q" = {q_val:.3f} "Gain" = {float(gain):.2f} }} }}'
        )
        if idx < len(EQ_BAND_FREQS):
            links_lines.append(
                f'                    {{ output = "b{idx}:Out" input = "b{idx + 1}:In" }}'
            )
            links_lines.append(
                f'                    {{ output = "b{idx}R:Out" input = "b{idx + 1}R:In" }}'
            )

    target_line = f'\n                target.object = "{target_sink}"' if target_sink else ""
    return (
        "# Auto-generated by Anti-Drift Studio\n"
        "context.properties = { log.level = 0 }\n"
        "context.spa-libs = {\n"
        "    audio.convert.* = audioconvert/libspa-audioconvert\n"
        "    support.*       = support/libspa-support\n"
        "}\n"
        "context.modules = [\n"
        "    { name = libpipewire-module-rt flags = [ ifexists nofail ] }\n"
        "    { name = libpipewire-module-protocol-native }\n"
        "    { name = libpipewire-module-client-node }\n"
        "    { name = libpipewire-module-adapter }\n"
        "    { name = libpipewire-module-filter-chain\n"
        "        args = {\n"
        f'            node.description = "{EQ_SINK_DESC}"\n'
        f'            media.name       = "{EQ_SINK_DESC}"\n'
        "            filter.graph = {\n"
        "                nodes = [\n"
        + "\n".join(nodes_lines)
        + "\n                ]\n"
        "                links = [\n"
        + "\n".join(links_lines)
        + "\n                ]\n"
        '                inputs  = [ "inL:In" "inR:In" ]\n'
        '                outputs = [ "b10:Out" "b10R:Out" ]\n'
        "            }\n"
        "            audio.channels = 2\n"
        "            audio.position = [ FL FR ]\n"
        "            capture.props = {\n"
        f'                node.name        = "{EQ_SINK_NAME}"\n'
        f'                node.description = "{EQ_SINK_DESC}"\n'
        '                device.icon-name = "audio-card"\n'
        "                media.class      = Audio/Sink\n"
        "            }\n"
        "            playback.props = {\n"
        f'                node.name    = "{EQ_PLAYBACK_NAME}"\n'
        "                node.passive = true"
        + target_line
        + "\n            }\n"
        "        }\n"
        "    }\n"
        "]\n"
    )


def _find_eq_pipewire_node() -> dict | None:
    rc, out, _ = _run(["pw-dump"])
    if rc != 0 or not out.strip().startswith("["):
        return None
    try:
        objs = json.loads(out)
    except (ValueError, TypeError):
        return None
    for obj in objs:
        if not isinstance(obj, dict) or obj.get("type") != "PipeWire:Interface:Node":
            continue
        props = (obj.get("info") or {}).get("props") or {}
        if props.get("node.name") == EQ_SINK_NAME:
            return obj
    return None


def _find_eq_pipewire_node_id() -> int | None:
    obj = _find_eq_pipewire_node()
    return int(obj["id"]) if obj is not None else None


def _node_has_stereo_graph(obj: dict | None) -> bool:
    if not isinstance(obj, dict):
        return False
    props_list = ((obj.get("info") or {}).get("params") or {}).get("Props") or []
    for entry in props_list:
        if isinstance(entry, dict):
            params = entry.get("params") or []
            if "surround_sofa_l:Azimuth" in params or "surround_wrapDelL:Delay (s)" in params:
                return True
    return False


def _find_eq_target_object() -> str:
    rc, out, _ = _run(["pw-dump"])
    if rc != 0 or not out.strip().startswith("["):
        return ""
    try:
        objs = json.loads(out)
    except (ValueError, TypeError):
        return ""
    for obj in objs:
        if not isinstance(obj, dict) or obj.get("type") != "PipeWire:Interface:Node":
            continue
        props = (obj.get("info") or {}).get("props") or {}
        if props.get("node.name") == EQ_PLAYBACK_NAME:
            return str(props.get("target.object") or "")
    return ""


def apply_eq_live_params(cfg: EqualizerConfig) -> bool:
    """Update stereo width/Haas/exciter, preamp, and all 10 EQ bands live."""
    nid = _find_eq_pipewire_node_id()
    if nid is None:
        return False
    eff_pre = effective_preamp_db(
        cfg.band_gains, cfg.preamp_db, cfg.auto_preamp, bypass=cfg.bypass
    )
    mult = 1.0 if cfg.bypass else round(10.0 ** (eff_pre / 20.0), 6)
    gains = [0.0] * len(EQ_BAND_FREQS) if cfg.bypass else list(cfg.band_gains)
    st = compute_stereo_dsp_params(cfg.stereo_enabled, cfg.stereo_width, bypass=cfg.bypass)
    sr = compute_surround_dsp_params(cfg.surround_enabled, bypass=cfg.bypass)

    sofa_file = find_sofa_hrtf_path()
    g2_l = sr["sofa_gain"] if sofa_file else sr["wrap_gain"]
    g3_l = sr["sofa_gain"] if sofa_file else sr["cross_gain"]
    g2_r = sr["sofa_gain"] if sofa_file else sr["wrap_gain_r"]
    g3_r = sr["sofa_gain"] if sofa_file else sr["cross_gain_r"]

    tokens = [
        f'"st_exc_4k:Gain" {st["exciter_4k_db"]:.2f}',
        f'"st_exc_8k:Gain" {st["exciter_8k_db"]:.2f}',
        f'"st_exc_16k:Gain" {st["exciter_16k_db"]:.2f}',
        f'"st_haas:Delay (s)" {st["haas_delay_s"]:.4f}',
        f'"st_side_blend:Gain 1" {st["side_gain"]:.4f}',
        f'"st_side_blend:Gain 2" {st["haas_gain"]:.4f}',
        f'"st_side_blend:Gain 3" {st["raw_side_gain"]:.4f}',
        f'"surround_blendL:Gain 1" {sr["direct_gain"]:.4f}',
        f'"surround_blendL:Gain 2" {g2_l:.4f}',
        f'"surround_blendL:Gain 3" {g3_l:.4f}',
        f'"surround_blendR:Gain 1" {sr["direct_gain"]:.4f}',
        f'"surround_blendR:Gain 2" {g2_r:.4f}',
        f'"surround_blendR:Gain 3" {g3_r:.4f}',
        f'"preamp:Mult" {mult:.6f}',
        f'"preampR:Mult" {mult:.6f}',
    ]
    for idx, gain in enumerate(gains, start=1):
        g = float(gain)
        tokens.append(f'"b{idx}:Gain" {g:.2f}')
        tokens.append(f'"b{idx}R:Gain" {g:.2f}')
    pod = "{ params = [ " + " ".join(tokens) + " ] }"
    rc, _, _ = _run(["pw-cli", "s", str(nid), "Props", pod])
    return rc == 0


def _ensure_eq_systemd_unit(pipewire_bin: str) -> bool:
    if not shutil.which("systemctl"):
        return False
    unit_text = (
        "[Unit]\n"
        "Description=Anti-Drift Studio System Equalizer (PipeWire)\n"
        "After=pipewire.service\n"
        "PartOf=pipewire.service\n\n"
        "[Service]\n"
        f"ExecStart={pipewire_bin} -c {EQ_CONF_PATH}\n"
        "Restart=on-failure\n"
        "RestartSec=2\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    EQ_SERVICE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not EQ_SERVICE_PATH.exists() or EQ_SERVICE_PATH.read_text(encoding="utf-8") != unit_text:
        EQ_SERVICE_PATH.write_text(unit_text, encoding="utf-8")
        _run(["systemctl", "--user", "daemon-reload"])
    return True


def _stop_fallback_pid() -> None:
    if not EQ_PID_PATH.exists():
        return
    try:
        pid = int(EQ_PID_PATH.read_text(encoding="utf-8").strip())
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        pass
    try:
        EQ_PID_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def _unload_ladspa_eq_modules() -> None:
    rc, out, _ = _run(["pactl", "list", "short", "modules"])
    if rc != 0:
        return
    for line in out.splitlines():
        if "module-ladspa-sink" in line and EQ_SINK_NAME in line:
            mod_id = line.split("\t", 1)[0].strip()
            if mod_id.isdigit():
                _run(["pactl", "unload-module", mod_id])


def _has_ladspa_mbeq() -> bool:
    for base in ("/usr/lib/ladspa", "/usr/lib64/ladspa", "/usr/lib/x86_64-linux-gnu/ladspa"):
        if (Path(base) / "mbeq_1197.so").exists():
            return True
    return False


def _bands_10_to_mbeq15(gains: list[float]) -> list[float]:
    # mbeq_1197 centers: 50,100,156,220,311,440,622,880,1250,1750,2500,3500,5000,10000,20000 Hz
    g = [float(x) for x in gains] + [0.0] * max(0, 10 - len(gains))
    return [
        round((g[0] + g[1]) * 0.5, 2),
        round((g[1] + g[2]) * 0.5, 2),
        round(g[2], 2),
        round(g[3], 2),
        round((g[3] + g[4]) * 0.5, 2),
        round(g[4], 2),
        round((g[4] + g[5]) * 0.5, 2),
        round(g[5], 2),
        round((g[5] + g[6]) * 0.5, 2),
        round(g[6], 2),
        round((g[6] + g[7]) * 0.5, 2),
        round(g[7], 2),
        round((g[7] + g[8]) * 0.5, 2),
        round(g[8], 2),
        round(g[9], 2),
    ]


def start_or_update_eq(cfg: EqualizerConfig) -> EqBackendStatus:
    """Start, update live, or stop the system-wide equalizer cleanly."""
    if not shutil.which("pactl"):
        return EqBackendStatus(
            active=False,
            backend="unavailable",
            sink_name="",
            target_sink="",
            message="Falta pactl/pipewire en el sistema.",
        )

    if not cfg.enabled:
        target = resolve_target_sink(cfg)
        if target:
            _run(["pactl", "set-default-sink", target])
            move_all_streams_to_sink(target)
        if EQ_SERVICE_PATH.exists():
            _run(["systemctl", "--user", "disable", "--now", EQ_SERVICE_NAME], timeout=6.0)
        _stop_fallback_pid()
        _unload_ladspa_eq_modules()
        return EqBackendStatus(
            active=False,
            backend="pipewire" if is_pipewire_session() else "pulseaudio",
            sink_name="",
            target_sink=target,
            message="Ecualizador apagado · Audio directo al dispositivo.",
        )

    cur_default = get_default_sink_name()
    if cur_default and cur_default != EQ_SINK_NAME:
        cfg.previous_default_sink = cur_default

    target_sink = resolve_target_sink(cfg)
    if not target_sink:
        return EqBackendStatus(
            active=False,
            backend="pipewire" if is_pipewire_session() else "pulseaudio",
            sink_name="",
            target_sink="",
            message="No se detectó ningún dispositivo de salida de audio.",
        )

    pipewire_bin = shutil.which("pipewire")
    if not pipewire_bin or not is_pipewire_session():
        if _has_ladspa_mbeq():
            _unload_ladspa_eq_modules()
            ctrls = [0.0] * 15 if cfg.bypass else _bands_10_to_mbeq15(cfg.band_gains)
            ctrl_str = ",".join(f"{v:.2f}" for v in ctrls)
            rc, _, _ = _run(
                [
                    "pactl",
                    "load-module",
                    "module-ladspa-sink",
                    f"sink_name={EQ_SINK_NAME}",
                    f"master={target_sink}",
                    "plugin=mbeq_1197",
                    "label=mbeq",
                    f"control={ctrl_str}",
                ]
            )
            if rc == 0 and eq_sink_exists():
                _run(["pactl", "set-default-sink", EQ_SINK_NAME])
                move_all_streams_to_sink(EQ_SINK_NAME)
                return EqBackendStatus(
                    active=True,
                    backend="pulseaudio",
                    sink_name=EQ_SINK_NAME,
                    target_sink=target_sink,
                    message=f"Ecualizador Activo · PulseAudio LADSPA -> {target_sink}",
                )
        return EqBackendStatus(
            active=False,
            backend="unavailable",
            sink_name="",
            target_sink=target_sink,
            message="Se requiere PipeWire activo (o PulseAudio + swh-plugins) para el ecualizador.",
        )

    EQ_DIR.mkdir(parents=True, exist_ok=True)
    EQ_CONF_PATH.write_text(generate_pipewire_eq_conf(cfg, target_sink), encoding="utf-8")

    eq_node = _find_eq_pipewire_node()
    already_up = eq_node is not None and eq_sink_exists()
    cur_target = _find_eq_target_object() if already_up else ""

    if (
        already_up
        and _node_has_stereo_graph(eq_node)
        and (not target_sink or cur_target == target_sink)
    ):
        apply_eq_live_params(cfg)
    else:
        _stop_fallback_pid()
        systemd_ok = False
        if _ensure_eq_systemd_unit(pipewire_bin):
            rc, _, _ = _run(
                ["systemctl", "--user", "enable", "--now", EQ_SERVICE_NAME], timeout=6.0
            )
            if rc == 0 and already_up:
                rc, _, _ = _run(
                    ["systemctl", "--user", "restart", EQ_SERVICE_NAME], timeout=6.0
                )
            systemd_ok = rc == 0
        if not systemd_ok and not eq_sink_exists():
            proc = subprocess.Popen(
                [pipewire_bin, "-c", str(EQ_CONF_PATH)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            EQ_PID_PATH.write_text(str(proc.pid), encoding="utf-8")
        for _ in range(25):
            if eq_sink_exists():
                break
            time.sleep(0.08)

    if not eq_sink_exists():
        return EqBackendStatus(
            active=False,
            backend="pipewire",
            sink_name="",
            target_sink=target_sink,
            message="No se pudo iniciar el nodo de ecualización PipeWire.",
        )

    _run(["pactl", "set-default-sink", EQ_SINK_NAME])
    move_all_streams_to_sink(EQ_SINK_NAME)
    apply_eq_live_params(cfg)
    mode_tag = (
        "Bypass A/B"
        if cfg.bypass
        else ("Activo + Estéreo" if cfg.stereo_enabled else "Activo")
    )
    return EqBackendStatus(
        active=True,
        backend="pipewire",
        sink_name=EQ_SINK_NAME,
        target_sink=target_sink,
        message=f"Ecualizador {mode_tag} · PipeWire DSP -> {target_sink}",
    )
