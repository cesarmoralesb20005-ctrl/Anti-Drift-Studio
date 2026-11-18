"""Systemd user service and environment controls for xbox-antidrift."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import ENV_CONFIG_PATH

SERVICE_NAME = "xbox-antidrift.service"
APP_AUTOSTART_DESKTOP_NAME = "xbox-antidrift-gui.desktop"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ServiceStatus:
    """Snapshot of the xbox-antidrift systemd user unit."""

    active: bool
    state: str
    enabled: bool
    pid: int | None
    summary: str


def _run_cmd(args: list[str], timeout: float = 4.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)


def get_service_status(service_name: str = SERVICE_NAME) -> ServiceStatus:
    """Query systemd --user for active state, enablement, and main PID."""
    rc, out, err = _run_cmd(
        [
            "systemctl",
            "--user",
            "show",
            service_name,
            "--property=ActiveState,SubState,UnitFileState,MainPID,LoadState",
        ]
    )
    if rc != 0:
        return ServiceStatus(
            active=False,
            state="error",
            enabled=False,
            pid=None,
            summary=err or "No se pudo consultar systemd --user",
        )

    props: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            props[k.strip()] = v.strip()

    load_state = props.get("LoadState", "unknown")
    active_state = props.get("ActiveState", "unknown")
    sub_state = props.get("SubState", "")
    unit_file_state = props.get("UnitFileState", "disabled")
    pid_val = int(props.get("MainPID", "0") or 0)
    pid = pid_val if pid_val > 0 else None

    if load_state == "not-found":
        return ServiceStatus(
            active=False,
            state="not-found",
            enabled=False,
            pid=None,
            summary=f"{service_name} no encontrada",
        )

    active = active_state == "active"
    enabled = unit_file_state in {"enabled", "enabled-runtime", "static"}
    summary = f"{active_state} ({sub_state})" if sub_state else active_state
    return ServiceStatus(
        active=active,
        state=active_state,
        enabled=enabled,
        pid=pid,
        summary=summary,
    )


def run_service_action(
    action: str, service_name: str = SERVICE_NAME
) -> tuple[bool, str]:
    """Execute a lifecycle action against xbox-antidrift.service."""
    if action in {"start", "stop", "restart", "enable", "disable"}:
        cmd = ["systemctl", "--user", action, service_name]
    elif action == "reload_hup":
        cmd = ["systemctl", "--user", "kill", "-s", "HUP", service_name]
    else:
        return False, f"Acción no soportada: {action}"

    rc, out, err = _run_cmd(cmd, timeout=8.0)
    if rc == 0:
        return True, out or f"Acción '{action}' ejecutada correctamente."
    return False, err or out or f"Error al ejecutar '{action}' (código {rc})."


def get_service_logs(lines: int = 35, service_name: str = SERVICE_NAME) -> str:
    """Return recent journald output for the anti-drift user unit."""
    rc, out, err = _run_cmd(
        [
            "journalctl",
            "--user",
            "-u",
            service_name,
            "-n",
            str(lines),
            "--no-pager",
            "-o",
            "short-iso",
        ]
    )
    if rc == 0 and out:
        return out
    return err or "Sin registros recientes en journalctl."


def check_sdl_ignore_env(vid_pid: str | None = None) -> tuple[bool, str]:
    """Check whether SDL_GAMECONTROLLER_IGNORE_DEVICES hides the active physical pad."""
    if not ENV_CONFIG_PATH.exists():
        return (
            False,
            f"No configurado ({ENV_CONFIG_PATH}); algunos juegos podrían ver 2 mandos.",
        )
    content = ENV_CONFIG_PATH.read_text(encoding="utf-8", errors="replace").strip()
    if vid_pid and ":" in vid_pid:
        vid, pid = vid_pid.split(":", 1)
        token = f"0X{vid.upper()}/0X{pid.upper()}"
        if token in content.upper():
            return True, content
        return (
            False,
            f"Falta agregar tu mando actual ({token}) al filtro SDL.",
        )
    if "SDL_GAMECONTROLLER_IGNORE_DEVICES=" in content:
        return True, content
    return False, f"Archivo sin regla SDL: {content}"


def ensure_sdl_ignore_device(vid_pid: str | None) -> tuple[bool, str]:
    """Add active controller VID:PID to SDL_GAMECONTROLLER_IGNORE_DEVICES cleanly."""
    if not vid_pid or ":" not in vid_pid:
        return False, "Conecta primero un mando físico para detectar su ID USB/Bluetooth."
    vid, pid = vid_pid.split(":", 1)
    token = f"0x{vid.upper()}/0x{pid.upper()}"

    existing_tokens: list[str] = []
    if ENV_CONFIG_PATH.exists():
        for line in ENV_CONFIG_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("SDL_GAMECONTROLLER_IGNORE_DEVICES="):
                val = line.split("=", 1)[1].strip()
                existing_tokens = [t.strip() for t in val.split(",") if t.strip()]
                break

    if token.upper() not in {t.upper() for t in existing_tokens}:
        existing_tokens.append(token)

    ENV_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_CONFIG_PATH.write_text(
        f"SDL_GAMECONTROLLER_IGNORE_DEVICES={','.join(existing_tokens)}\n",
        encoding="utf-8",
    )
    return (
        True,
        f"Filtro actualizado con {token}. Reinicia el juego o tu sesión si ya estaba abierto.",
    )


def check_uinput_access() -> tuple[bool, str]:
    """Check whether /dev/uinput exists and is writable by current user."""
    if not os.path.exists("/dev/uinput"):
        return False, "No existe /dev/uinput (falta módulo kernel uinput)."
    if os.access("/dev/uinput", os.W_OK):
        return True, "Permiso correcto en /dev/uinput."
    return False, "Sin permiso de escritura en /dev/uinput (ejecuta install.sh para regla udev)."


def get_app_autostart_path() -> Path:
    """Return XDG autostart .desktop path for launching Anti-Drift Studio on login."""
    xdg_cfg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_cfg) if xdg_cfg else (Path.home() / ".config")
    return base / "autostart" / APP_AUTOSTART_DESKTOP_NAME


def is_app_autostart_enabled(autostart_path: Path | None = None) -> bool:
    """Return True if Anti-Drift Studio desktop autostart entry exists and is enabled."""
    path = autostart_path or get_app_autostart_path()
    if not path.exists():
        return False
    try:
        lines = {
            line.strip().lower()
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        }
        if "hidden=true" in lines or "x-gnome-autostart-enabled=false" in lines:
            return False
        return True
    except OSError:
        return False


def set_app_autostart(
    enabled: bool,
    project_root: Path | None = None,
    autostart_path: Path | None = None,
    minimized: bool = False,
) -> tuple[bool, str]:
    """Enable or disable launching Anti-Drift Studio GUI automatically at PC login."""
    path = autostart_path or get_app_autostart_path()
    if not enabled:
        try:
            path.unlink(missing_ok=True)
            return True, "Inicio automático del programa desactivado."
        except OSError as exc:
            return False, f"No se pudo desactivar el inicio automático: {exc}"

    root = project_root or PROJECT_ROOT
    run_sh = root / "run.sh"
    if run_sh.exists():
        exec_cmd = shlex.quote(str(run_sh))
    else:
        exec_cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(root / 'app.py'))}"
    if minimized:
        exec_cmd = f"{exec_cmd} --minimized"

    desktop_content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=Anti-Drift Studio\n"
        "Comment=Calibración universal, macros, baja latencia y clips por voz\n"
        f"Exec={exec_cmd}\n"
        "Icon=input-gaming\n"
        "Terminal=false\n"
        "Categories=Utility;Game;Settings;\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(desktop_content, encoding="utf-8")
        return True, "Anti-Drift Studio se abrirá automáticamente al encender el PC."
    except OSError as exc:
        return False, f"No se pudo guardar el inicio automático: {exc}"
