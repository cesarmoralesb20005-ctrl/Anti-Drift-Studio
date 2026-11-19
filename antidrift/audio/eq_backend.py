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
    effective_preamp_db,
)

EQ_SINK_NAME = "antidrift_eq_sink"
EQ_PLAYBACK_NAME = "antidrift_eq_out"
EQ_SINK_DESC = "Anti-Drift Studio EQ"
EQ_DIR = Path.home() / ".config" / "antidrift-studio"
EQ_CONF_PATH = EQ_DIR / "eq-filter-chain.conf"
EQ_PID_PATH = EQ_DIR / "eq-pipewire.pid"
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
    """Build standalone PipeWire filter-chain config for the 10-band equalizer."""
    eff_pre = effective_preamp_db(
        cfg.band_gains, cfg.preamp_db, cfg.auto_preamp, bypass=cfg.bypass
    )
    mult = 1.0 if cfg.bypass else round(10.0 ** (eff_pre / 20.0), 6)
    gains = [0.0] * len(EQ_BAND_FREQS) if cfg.bypass else list(cfg.band_gains)

    nodes_lines = [
        f'                    {{ type = builtin name = preamp label = linear control = {{ "Mult" = {mult:.6f} "Add" = 0.0 }} }}'
    ]
    links_lines = ['                    { output = "preamp:Out" input = "b1:In" }']

    for idx, (freq, ftype, q_val, gain) in enumerate(
        zip(EQ_BAND_FREQS, EQ_BAND_TYPES, EQ_BAND_Q, gains), start=1
    ):
        label = f"bq_{ftype}"
        nodes_lines.append(
            f'                    {{ type = builtin name = b{idx} label = {label} control = {{ "Freq" = {float(freq):.1f} "Q" = {q_val:.3f} "Gain" = {float(gain):.2f} }} }}'
        )
        if idx < len(EQ_BAND_FREQS):
            links_lines.append(
                f'                    {{ output = "b{idx}:Out" input = "b{idx + 1}:In" }}'
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


def _find_eq_pipewire_node_id() -> int | None:
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
            return int(obj["id"])
    return None


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
    """Update preamp & all 10 EQ bands in real time with zero audio interruption."""
    nid = _find_eq_pipewire_node_id()
    if nid is None:
        return False
    eff_pre = effective_preamp_db(
        cfg.band_gains, cfg.preamp_db, cfg.auto_preamp, bypass=cfg.bypass
    )
    mult = 1.0 if cfg.bypass else round(10.0 ** (eff_pre / 20.0), 6)
    gains = [0.0] * len(EQ_BAND_FREQS) if cfg.bypass else list(cfg.band_gains)
    tokens = [f'"preamp:Mult" {mult:.6f}']
    for idx, gain in enumerate(gains, start=1):
        tokens.append(f'"b{idx}:Gain" {float(gain):.2f}')
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
        return EqBackendStatus(
            active=False,
            backend="unavailable",
            sink_name="",
            target_sink=target_sink,
            message="Se requiere PipeWire activo para el ecualizador del sistema.",
        )

    EQ_DIR.mkdir(parents=True, exist_ok=True)
    EQ_CONF_PATH.write_text(generate_pipewire_eq_conf(cfg, target_sink), encoding="utf-8")

    already_up = eq_sink_exists()
    cur_target = _find_eq_target_object() if already_up else ""

    if already_up and (not target_sink or cur_target == target_sink):
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
    mode_tag = "Bypass A/B" if cfg.bypass else "Activo"
    return EqBackendStatus(
        active=True,
        backend="pipewire",
        sink_name=EQ_SINK_NAME,
        target_sink=target_sink,
        message=f"Ecualizador {mode_tag} · PipeWire DSP -> {target_sink}",
    )
