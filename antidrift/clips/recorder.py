"""Universal GPU Screen Recorder supervisor and JSON-IPC replay client."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from .clips_config import ClipsConfig

CACHE_DIR = Path.home() / ".cache" / "antidrift-studio"
GSR_SOCKET_PATH = CACHE_DIR / "gsr.sock"
PORTAL_TOKEN_PATH = CACHE_DIR / "portal_token"
FLATPAK_ID = "com.dec05eba.gpu_screen_recorder"


@dataclass
class GsrBackend:
    """Detected GPU Screen Recorder installation variant."""

    kind: str  # "native" | "flatpak_system" | "flatpak_user"
    base_cmd: list[str]
    is_user_flatpak: bool
    label: str


def detect_gsr_backend() -> GsrBackend | None:
    """Locate native or Flatpak (system/user) GPU Screen Recorder."""
    native = shutil.which("gpu-screen-recorder")
    if native:
        return GsrBackend(
            kind="native",
            base_cmd=[native],
            is_user_flatpak=False,
            label=f"Nativo ({native})",
        )

    if shutil.which("flatpak"):
        for scope, flag, is_user in (
            ("flatpak_system", "--system", False),
            ("flatpak_user", "--user", True),
        ):
            proc = subprocess.run(
                ["flatpak", "info", flag, FLATPAK_ID],
                capture_output=True,
                text=True,
                timeout=3.0,
                check=False,
            )
            if proc.returncode == 0:
                return GsrBackend(
                    kind=scope,
                    base_cmd=[
                        "flatpak",
                        "run",
                        flag,
                        "--command=gpu-screen-recorder",
                        FLATPAK_ID,
                    ],
                    is_user_flatpak=is_user,
                    label=f"Flatpak ({'usuario' if is_user else 'sistema'})",
                )
    return None


def install_gsr_flatpak_user() -> tuple[bool, str]:
    """Install GPU Screen Recorder from Flathub into user Flatpak without sudo."""
    if not shutil.which("flatpak"):
        return (
            False,
            "Flatpak no está instalado en este sistema. Instala 'flatpak' o 'gpu-screen-recorder' con tu gestor de paquetes.",
        )
    subprocess.run(
        [
            "flatpak",
            "remote-add",
            "--user",
            "--if-not-exists",
            "flathub",
            "https://dl.flathub.org/repo/flathub.flatpakrepo",
        ],
        capture_output=True,
        text=True,
        timeout=20.0,
        check=False,
    )
    proc = subprocess.run(
        ["flatpak", "install", "--user", "-y", "flathub", FLATPAK_ID],
        capture_output=True,
        text=True,
        timeout=180.0,
        check=False,
    )
    if proc.returncode == 0:
        return True, "GPU Screen Recorder instalado correctamente."
    return False, proc.stderr.strip() or proc.stdout.strip() or "Error al instalar Flatpak."


def resolve_capture_target(cfg: ClipsConfig, backend: GsrBackend) -> str:
    """Choose best capture source universally for Wayland/X11/native/Flatpak."""
    if cfg.capture_target and cfg.capture_target != "auto":
        return cfg.capture_target
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if backend.is_user_flatpak and session == "wayland":
        return "portal"
    return "screen"


def has_saved_portal_session() -> bool:
    """Return True if a reusable XDG Portal screen-share token exists on disk."""
    try:
        return PORTAL_TOKEN_PATH.exists() and PORTAL_TOKEN_PATH.stat().st_size > 0
    except OSError:
        return False


def list_desktop_audio_monitors(backend: GsrBackend | None = None) -> list[str]:
    """Return unique PipeWire/PulseAudio monitor sources for all output devices."""
    monitors: list[str] = []
    if shutil.which("pactl"):
        try:
            res = subprocess.run(
                ["pactl", "list", "short", "sources"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    cols = line.split("\t")
                    if len(cols) >= 2:
                        name = cols[1].strip()
                        if name.endswith(".monitor") and name not in monitors:
                            monitors.append(name)
                if monitors:
                    return monitors
        except (OSError, subprocess.TimeoutExpired):
            pass

    if backend is not None:
        try:
            res = subprocess.run(
                backend.base_cmd + ["--list-audio-devices"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if "|" in line:
                        dev_id, _desc = line.split("|", 1)
                        dev_id = dev_id.strip()
                        if dev_id.endswith(".monitor") and dev_id not in monitors:
                            monitors.append(dev_id)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return monitors


def _format_audio_arg(source: str) -> str:
    if source in ("default_output", "default_input") or source.startswith(
        ("device:", "app:", "app-inverse:")
    ):
        return source
    return f"device:{source}"


def build_gsr_args(
    cfg: ClipsConfig,
    backend: GsrBackend,
    socket_path: Path = GSR_SOCKET_PATH,
    token_path: Path = PORTAL_TOKEN_PATH,
    desktop_monitors: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Build GPU Screen Recorder CLI arguments for RAM instant-replay buffer."""
    clean = cfg.clamped()
    target = resolve_capture_target(clean, backend)

    args: list[str] = list(backend.base_cmd) + ["-w", target]
    if target == "portal":
        args.extend(
            [
                "-restore-portal-session",
                "yes",
                "-portal-session-token-filepath",
                str(token_path),
            ]
        )

    args.extend(
        [
            "-f",
            str(clean.fps),
            "-c",
            "mp4",
            "-k",
            clean.codec,
            "-q",
            clean.quality,
            "-ac",
            "aac",
            "-r",
            str(clean.replay_seconds),
            "-replay-storage",
            "ram",
            "-restart-replay-on-save",
            "no",
            "-o",
            clean.output_dir,
            "-ipc",
            str(socket_path),
        ]
    )

    audio_parts: list[str] = []
    if clean.record_desktop_audio:
        if clean.desktop_audio_source == "all_outputs":
            mons = (
                list_desktop_audio_monitors(backend)
                if desktop_monitors is None
                else desktop_monitors
            )
            if mons:
                for mon in mons:
                    formatted = _format_audio_arg(mon)
                    if formatted not in audio_parts:
                        audio_parts.append(formatted)
            else:
                audio_parts.append("default_output")
        else:
            audio_parts.append(_format_audio_arg(clean.desktop_audio_source))
    if clean.record_mic_audio:
        audio_parts.append(_format_audio_arg(clean.mic_audio_source))
    if audio_parts:
        args.extend(["-a", "|".join(audio_parts)])

    program = args[0]
    return program, args[1:]


def list_monitors_and_audio(
    backend: GsrBackend | None,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (capture_targets, audio_sources) pairs of (value, label)."""
    targets: list[tuple[str, str]] = [
        ("auto", "Automático (recomendado)"),
        ("portal", "Selector de pantalla Wayland (Portal)"),
        ("screen", "Pantalla principal directa (KMS/X11)"),
    ]
    audio_sources: list[tuple[str, str]] = [
        ("all_outputs", "Todas las salidas del PC (recomendado)"),
        ("default_output", "Salida predeterminada del sistema"),
    ]

    if backend is not None:
        try:
            res = subprocess.run(
                backend.base_cmd + ["--list-monitors"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if "|" in line:
                        name, dim = line.split("|", 1)
                        targets.append((name.strip(), f"Monitor {name.strip()} ({dim.strip()})"))
        except (OSError, subprocess.TimeoutExpired):
            pass

        try:
            res = subprocess.run(
                backend.base_cmd + ["--list-audio-devices"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if "|" not in line:
                        continue
                    dev_id, desc = line.split("|", 1)
                    dev_id = dev_id.strip()
                    if dev_id.endswith(".monitor"):
                        clean_desc = desc.strip().removeprefix("Monitor of ")
                        audio_sources.append((dev_id, clean_desc))
                return targets, audio_sources
        except (OSError, subprocess.TimeoutExpired):
            pass

    for mon in list_desktop_audio_monitors(None):
        audio_sources.append((mon, mon))

    return targets, audio_sources


def _cleanup_stale_gsr(backend: GsrBackend | None) -> None:
    """Terminate any orphaned GPU Screen Recorder instances bound to our socket/sandbox."""
    if GSR_SOCKET_PATH.exists():
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.6)
                sock.connect(str(GSR_SOCKET_PATH))
                sock.sendall(b'{"id":9998,"name":"stop"}\n')
                sock.recv(1024)
        except OSError:
            pass

    if backend is not None and backend.kind.startswith("flatpak"):
        subprocess.run(
            ["flatpak", "kill", FLATPAK_ID],
            capture_output=True,
            timeout=2.0,
            check=False,
        )

    if shutil.which("pkill"):
        subprocess.run(
            ["pkill", "-f", f"gpu-screen-recorder.*{GSR_SOCKET_PATH}"],
            capture_output=True,
            timeout=1.5,
            check=False,
        )

    if GSR_SOCKET_PATH.exists():
        try:
            GSR_SOCKET_PATH.unlink()
        except OSError:
            pass


class ClipRecorderController(QObject):
    """Manages GPU Screen Recorder background process and non-blocking replay saves."""

    status_changed = Signal(str, str)  # state ("stopped"|"starting"|"recording"|"error"), message
    clip_saved = Signal(str)
    save_failed = Signal(str)
    log_line = Signal(str)

    def __init__(self, config: ClipsConfig, parent=None) -> None:
        super().__init__(parent)
        self._config = config.copy()
        self._backend: GsrBackend | None = None
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._on_output)
        self._process.started.connect(self._on_started)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_proc_error)

        self._active_buffer_seconds = config.replay_seconds
        self._active_target = "screen"
        self._stream_confirmed = False
        self._stop_requested = False
        self._pending_restart = False
        self._last_error_line = ""
        self._ipc_seq = 1
        self._started_at = 0.0

        self._socket_poll_timer = QTimer(self)
        self._socket_poll_timer.setInterval(200)
        self._socket_poll_timer.timeout.connect(self._check_socket_ready)

        self._restart_debounce = QTimer(self)
        self._restart_debounce.setSingleShot(True)
        self._restart_debounce.setInterval(450)
        self._restart_debounce.timeout.connect(self._execute_debounced_restart)

        self._force_kill_timer = QTimer(self)
        self._force_kill_timer.setSingleShot(True)
        self._force_kill_timer.setInterval(1500)
        self._force_kill_timer.timeout.connect(self._force_kill_process)

    def is_running(self) -> bool:
        return self._process.state() != QProcess.NotRunning

    def is_recording_ready(self) -> bool:
        return self.is_running() and GSR_SOCKET_PATH.exists() and self._stream_confirmed

    def update_config(self, config: ClipsConfig, restart_if_running: bool = False) -> None:
        """Store updated config and debounce non-blocking restart if needed."""
        self._config = config.copy()
        if restart_if_running and (self.is_running() or self._pending_restart):
            self._restart_debounce.start()

    def _execute_debounced_restart(self) -> None:
        if self.is_running():
            self._pending_restart = True
            self.stop_buffer(wait=False)
        else:
            self.start_buffer()

    def reset_portal_session(self, start_after: bool = True) -> None:
        """Clear saved portal token so KDE/Wayland re-opens the screen/window selector."""
        try:
            PORTAL_TOKEN_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        self._restart_debounce.stop()
        if self.is_running():
            self._pending_restart = start_after
            self.stop_buffer(wait=False)
        elif start_after:
            self.start_buffer()

    def start_buffer(self) -> None:
        """Start background instant-replay capture using GPU Screen Recorder."""
        self._restart_debounce.stop()
        self._pending_restart = False
        if self.is_running():
            return
        backend = detect_gsr_backend()
        if backend is None:
            self.status_changed.emit(
                "error",
                "Falta GPU Screen Recorder. Instálalo con el botón de abajo o ./install.sh.",
            )
            return

        self._backend = backend
        _cleanup_stale_gsr(backend)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        Path(self._config.output_dir).mkdir(parents=True, exist_ok=True)

        program, args = build_gsr_args(self._config, backend)
        self._active_buffer_seconds = self._config.replay_seconds
        self._active_target = resolve_capture_target(self._config, backend)
        self._stream_confirmed = False
        self._stop_requested = False
        self._last_error_line = ""
        self._started_at = time.monotonic()

        self.log_line.emit(f"$ {program} {' '.join(args)}")
        if self._active_target == "portal" and not has_saved_portal_session():
            self.status_changed.emit(
                "starting",
                "Elige la pantalla o ventana en el diálogo del sistema...",
            )
        else:
            self.status_changed.emit("starting", "Iniciando búfer de clips...")
        self._process.start(program, args)

    def stop_buffer(self, wait: bool = False) -> None:
        """Gracefully stop GPU Screen Recorder replay buffer (non-blocking by default)."""
        self._restart_debounce.stop()
        self._socket_poll_timer.stop()
        if not self.is_running():
            self._force_kill_timer.stop()
            if self._backend is not None:
                _cleanup_stale_gsr(self._backend)
            self.status_changed.emit("stopped", "Grabación detenida")
            return

        self._stop_requested = True
        sent_ipc = False
        if GSR_SOCKET_PATH.exists():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    sock.settimeout(0.8)
                    sock.connect(str(GSR_SOCKET_PATH))
                    sock.sendall(b'{"id":9999,"name":"stop"}\n')
                    sent_ipc = True
            except OSError:
                pass

        if not sent_ipc:
            if self._backend is not None and self._backend.kind.startswith("flatpak"):
                subprocess.Popen(
                    ["flatpak", "kill", FLATPAK_ID],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                self._process.terminate()

        if wait:
            if not self._process.waitForFinished(1800):
                self._force_kill_process()
                self._process.waitForFinished(800)
        else:
            self._force_kill_timer.start()

    def _force_kill_process(self) -> None:
        if not self.is_running():
            return
        if self._backend is not None and self._backend.kind.startswith("flatpak"):
            subprocess.Popen(
                ["flatpak", "kill", FLATPAK_ID],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        self._process.kill()

    def save_replay(self, seconds: int | None = None) -> None:
        """Request instant replay save over Unix IPC without blocking UI."""
        if not self.is_running():
            self.save_failed.emit(
                "El Replay está apagado. Pulsa 'Activar Replay' arriba para empezar a grabar."
            )
            return
        if not GSR_SOCKET_PATH.exists():
            self.save_failed.emit(
                "El grabador sigue iniciando (o esperando que elijas pantalla)."
            )
            return
        if (
            self._backend is not None
            and self._backend.is_user_flatpak
            and str(self._config.output_dir).startswith("/tmp")
        ):
            self.save_failed.emit(
                "Flatpak no comparte /tmp con el sistema. Elige una carpeta dentro de tu /home (ej. Videos/AntiDrift-Clips)."
            )
            return

        req_sec = (
            min(self._active_buffer_seconds, max(2, int(seconds)))
            if seconds is not None
            else self._active_buffer_seconds
        )
        self._ipc_seq += 1
        req_id = self._ipc_seq
        payload = json.dumps(
            {"id": req_id, "name": "save-replay", "data": {"seconds": req_sec}}
        ).encode("utf-8") + b"\n"

        def _worker() -> None:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    sock.settimeout(25.0)
                    sock.connect(str(GSR_SOCKET_PATH))
                    sock.sendall(payload)
                    chunks: list[bytes] = []
                    while True:
                        data = sock.recv(4096)
                        if not data:
                            break
                        chunks.append(data)
                        if b"\n" in data:
                            break
                    raw_reply = b"".join(chunks).decode("utf-8", errors="replace").strip()
                    if not raw_reply:
                        self.save_failed.emit(
                            "GPU Screen Recorder no devolvió respuesta al guardar."
                        )
                        return
                    reply = json.loads(raw_reply.splitlines()[0])
                    if reply.get("result") == "ok" and reply.get("data"):
                        saved_path = str(reply["data"])
                        if Path(saved_path).exists():
                            self.clip_saved.emit(saved_path)
                        else:
                            self.save_failed.emit(
                                f"El grabador respondió ok, pero no aparece el archivo en disco: {saved_path}"
                            )
                    else:
                        self.save_failed.emit(
                            str(reply.get("data") or "No se pudo guardar el clip.")
                        )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self.save_failed.emit(f"Error al comunicar con el grabador: {exc}")

        threading.Thread(target=_worker, daemon=True).start()

    def _on_started(self) -> None:
        self._socket_poll_timer.start()

    def _check_socket_ready(self) -> None:
        if not self.is_running():
            self._socket_poll_timer.stop()
            return
        if not GSR_SOCKET_PATH.exists():
            return
        elapsed = time.monotonic() - self._started_at
        if self._stream_confirmed or (self._active_target != "portal" and elapsed >= 1.2):
            self._stream_confirmed = True
            self._socket_poll_timer.stop()
            self.status_changed.emit(
                "recording",
                f"Grabando últimos {self._active_buffer_seconds} s en RAM",
            )

    def _on_output(self) -> None:
        raw = bytes(self._process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        for line in raw.splitlines():
            clean = line.strip()
            if not clean:
                continue
            self.log_line.emit(clean)
            low = clean.lower()
            if "pipewire negotiation finished" in low or "update fps:" in low:
                if not self._stream_confirmed:
                    self._stream_confirmed = True
                    self._socket_poll_timer.stop()
                    self.status_changed.emit(
                        "recording",
                        f"Grabando últimos {self._active_buffer_seconds} s en RAM",
                    )
            elif "selectsources" in low and not self._stream_confirmed:
                self.status_changed.emit(
                    "starting",
                    "Esperando selección de pantalla o ventana en el sistema...",
                )
            elif "error" in low or "failed" in low or "canceled" in low or "cancelled" in low:
                self._last_error_line = clean

    def _on_finished(self, exit_code: int, _exit_status) -> None:
        self._socket_poll_timer.stop()
        self._force_kill_timer.stop()
        self._stream_confirmed = False
        if GSR_SOCKET_PATH.exists():
            try:
                GSR_SOCKET_PATH.unlink()
            except OSError:
                pass

        if self._pending_restart:
            self._pending_restart = False
            self.start_buffer()
            return

        if exit_code == 0 or self._stop_requested:
            self._stop_requested = False
            self.status_changed.emit("stopped", "Grabación detenida")
            return

        err_low = self._last_error_line.lower()
        if "cancel" in err_low or "portal" in err_low and not has_saved_portal_session():
            msg = "Selección de pantalla cancelada. Pulsa 'Elegir pantalla...' o 'Activar Replay'."
        elif self._last_error_line:
            msg = f"Grabador se detuvo: {self._last_error_line}"
        else:
            msg = f"El grabador terminó (código {exit_code}). Prueba 'Elegir pantalla...' o revisa Opciones."
        self.status_changed.emit("error", msg)

    def _on_proc_error(self, _err) -> None:
        self._socket_poll_timer.stop()
        self._force_kill_timer.stop()
        self._stream_confirmed = False
        if not self._stop_requested:
            self.status_changed.emit("error", self._process.errorString())
