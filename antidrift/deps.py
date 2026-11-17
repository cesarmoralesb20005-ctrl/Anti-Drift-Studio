"""Cross-distro dependency detector and automatic installer (pure stdlib)."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PROJECT_DIR = Path(__file__).resolve().parent.parent
DAEMON_SRC = PROJECT_DIR / "daemon" / "xbox-antidrift.c"
DAEMON_BIN = Path.home() / ".local" / "bin" / "xbox-antidrift"
MODELS_DIR = Path.home() / ".local" / "share" / "antidrift-studio" / "models"
VOSK_MODEL_NAME = "vosk-model-small-es-0.42"
VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip"
GSR_FLATPAK_ID = "com.dec05eba.gpu_screen_recorder"


@dataclass
class DistroInfo:
    """Detected Linux distribution family and package manager."""

    distro_id: str
    pretty_name: str
    pkg_manager: str  # apt | pacman | dnf | zypper | xbps | apk | unknown


@dataclass
class DependencyReport:
    """Summary of installed vs missing system/Python/runtime dependencies."""

    distro: DistroInfo
    missing_system_cmds: list[str] = field(default_factory=list)
    missing_system_pkgs: list[str] = field(default_factory=list)
    missing_python_pkgs: list[str] = field(default_factory=list)
    missing_daemon: bool = False
    missing_gsr: bool = False
    missing_vosk_model: bool = False

    @property
    def all_ok(self) -> bool:
        return (
            not self.missing_system_cmds
            and not self.missing_system_pkgs
            and not self.missing_python_pkgs
            and not self.missing_daemon
            and not self.missing_gsr
            and not self.missing_vosk_model
        )

    def install_command(self) -> list[str]:
        """Return native package-manager install argv for missing system packages."""
        return build_distro_install_command(self.missing_system_pkgs, self.distro)

    def summary_lines(self) -> list[str]:
        lines = [f"Distro detectada: {self.distro.pretty_name} ({self.distro.pkg_manager})"]
        if self.all_ok:
            lines.append("Todas las dependencias están instaladas y listas.")
            return lines
        if self.missing_system_pkgs:
            lines.append(
                f"Faltan paquetes del sistema: {', '.join(self.missing_system_pkgs)}"
            )
        if self.missing_python_pkgs:
            lines.append(
                f"Faltan paquetes Python: {', '.join(self.missing_python_pkgs)}"
            )
        if self.missing_daemon:
            lines.append("Servicio C nativo: pendiente de compilar")
        if self.missing_gsr:
            lines.append("Motor de clips (GPU Screen Recorder): faltante")
        if self.missing_vosk_model:
            lines.append("Modelo de voz offline (es-0.42): faltante")
        return lines


def detect_distro() -> DistroInfo:
    """Inspect /etc/os-release and available package managers."""
    os_data: dict[str, str] = {}
    os_release = Path("/etc/os-release")
    if os_release.exists():
        for line in os_release.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                os_data[k.strip()] = v.strip().strip('"').strip("'")

    did = os_data.get("ID", "linux").lower()
    like = os_data.get("ID_LIKE", "").lower()
    pretty = os_data.get("PRETTY_NAME") or did.capitalize()
    family = f"{did} {like}"

    if shutil.which("apt-get") or any(k in family for k in ("debian", "ubuntu", "mint", "pop")):
        mgr = "apt"
    elif shutil.which("pacman") or any(k in family for k in ("arch", "manjaro", "cachyos", "endeavour")):
        mgr = "pacman"
    elif shutil.which("dnf") or any(k in family for k in ("fedora", "rhel", "nobara", "centos", "rocky")):
        mgr = "dnf"
    elif shutil.which("zypper") or "suse" in family:
        mgr = "zypper"
    elif shutil.which("xbps-install") or "void" in family:
        mgr = "xbps"
    elif shutil.which("apk") or "alpine" in family:
        mgr = "apk"
    else:
        mgr = "unknown"

    return DistroInfo(distro_id=did, pretty_name=pretty, pkg_manager=mgr)


# Mapping from logical capability -> distro package names per package manager
_CAPABILITY_PACKAGES: dict[str, dict[str, list[str]]] = {
    "gcc": {
        "apt": ["gcc", "libc6-dev"],
        "pacman": ["gcc"],
        "dnf": ["gcc"],
        "zypper": ["gcc"],
        "xbps": ["gcc"],
        "apk": ["gcc", "musl-dev"],
    },
    "python_venv": {
        "apt": ["python3-venv", "python3-pip"],
        "pacman": ["python"],
        "dnf": ["python3-pip"],
        "zypper": ["python3-pip"],
        "xbps": ["python3-pip"],
        "apk": ["py3-pip"],
    },
    "flatpak": {
        "apt": ["flatpak"],
        "pacman": ["flatpak"],
        "dnf": ["flatpak"],
        "zypper": ["flatpak"],
        "xbps": ["flatpak"],
        "apk": ["flatpak"],
    },
    "audio_tools": {
        "apt": ["pulseaudio-utils", "pipewire-bin"],
        "pacman": ["libpulse", "pipewire"],
        "dnf": ["pulseaudio-utils", "pipewire-utils"],
        "zypper": ["pulseaudio-utils", "pipewire-tools"],
        "xbps": ["pulseaudio-utils", "pipewire"],
        "apk": ["pulseaudio-utils", "pipewire"],
    },
    "qt_runtime": {
        "apt": ["libxcb-cursor0", "libegl1", "libgl1"],
        "pacman": ["libxcb", "xcb-util-cursor", "libglvnd"],
        "dnf": ["xcb-util-cursor", "mesa-libEGL", "mesa-libGL"],
        "zypper": ["libxcb-cursor0", "Mesa-libEGL1", "Mesa-libGL1"],
        "xbps": ["xcb-util-cursor", "MesaLib"],
        "apk": ["xcb-util-cursor", "mesa-egl", "mesa-gl"],
    },
}


def build_distro_install_command(pkgs: list[str], distro: DistroInfo) -> list[str]:
    """Build native non-interactive install argv for the given package manager."""
    if not pkgs:
        return []
    m = distro.pkg_manager
    if m == "apt":
        return ["apt-get", "install", "-y", *pkgs]
    if m == "pacman":
        return ["pacman", "-S", "--needed", "--noconfirm", *pkgs]
    if m == "dnf":
        return ["dnf", "install", "-y", *pkgs]
    if m == "zypper":
        return ["zypper", "--non-interactive", "install", *pkgs]
    if m == "xbps":
        return ["xbps-install", "-Sy", *pkgs]
    if m == "apk":
        return ["apk", "add", *pkgs]
    return []


def _pkg_installed_apt(pkg: str) -> bool:
    res = subprocess.run(
        ["dpkg-query", "-W", "-f=${Status}", pkg],
        capture_output=True,
        text=True,
        check=False,
    )
    return res.returncode == 0 and "install ok installed" in res.stdout


def _check_system_pkgs(distro: DistroInfo, caps: list[str]) -> list[str]:
    missing: list[str] = []
    for cap in caps:
        for pkg in _CAPABILITY_PACKAGES.get(cap, {}).get(distro.pkg_manager, []):
            if pkg in missing:
                continue
            if distro.pkg_manager == "apt" and shutil.which("dpkg-query"):
                if not _pkg_installed_apt(pkg):
                    missing.append(pkg)
            else:
                missing.append(pkg)
    return missing


def _has_gsr() -> bool:
    if shutil.which("gpu-screen-recorder"):
        return True
    if shutil.which("flatpak"):
        for scope in ("--system", "--user"):
            proc = subprocess.run(
                ["flatpak", "info", scope, GSR_FLATPAK_ID],
                capture_output=True,
                timeout=3.0,
                check=False,
            )
            if proc.returncode == 0:
                return True
    return False


def _has_vosk_model() -> bool:
    preferred = MODELS_DIR / VOSK_MODEL_NAME
    if (preferred / "conf" / "mfcc.conf").exists() or (preferred / "am").exists():
        return True
    if MODELS_DIR.exists():
        for child in MODELS_DIR.iterdir():
            if child.is_dir() and child.name.startswith("vosk-model"):
                return True
    return False


def check_dependencies() -> DependencyReport:
    """Audit all system binaries, Python libraries, C daemon, GSR, and Vosk model."""
    distro = detect_distro()
    missing_cmds: list[str] = []
    needed_caps: list[str] = []

    if not shutil.which("gcc"):
        missing_cmds.append("gcc")
        needed_caps.append("gcc")
    elif distro.pkg_manager == "apt" and not Path("/usr/include/linux/uinput.h").exists():
        needed_caps.append("gcc")

    has_venv_mod = importlib.util.find_spec("venv") is not None
    if not has_venv_mod and not (PROJECT_DIR / ".venv" / "bin" / "pip").exists():
        missing_cmds.append("python3-venv")
        needed_caps.append("python_venv")

    if not (shutil.which("parec") or shutil.which("pw-record")) or not shutil.which("pactl"):
        missing_cmds.append("parec/pactl")
        needed_caps.append("audio_tools")

    has_gsr = _has_gsr()
    if not has_gsr and not shutil.which("flatpak"):
        missing_cmds.append("flatpak")
        needed_caps.append("flatpak")

    # Ensure Qt6 XCB/EGL runtime libs exist on apt-based distros if not verified
    if distro.pkg_manager == "apt" and importlib.util.find_spec("PySide6") is None:
        needed_caps.append("qt_runtime")

    missing_sys_pkgs = _check_system_pkgs(distro, needed_caps)

    missing_py: list[str] = []
    for mod, pip_name in (
        ("PySide6", "PySide6"),
        ("vosk", "vosk"),
        ("jeepney", "jeepney"),
    ):
        if importlib.util.find_spec(mod) is None:
            missing_py.append(pip_name)

    missing_daemon = not DAEMON_BIN.exists() or (
        DAEMON_SRC.exists()
        and DAEMON_BIN.stat().st_mtime < DAEMON_SRC.stat().st_mtime
    )

    return DependencyReport(
        distro=distro,
        missing_system_cmds=missing_cmds,
        missing_system_pkgs=missing_sys_pkgs,
        missing_python_pkgs=missing_py,
        missing_daemon=missing_daemon,
        missing_gsr=not has_gsr,
        missing_vosk_model=not _has_vosk_model(),
    )


def _run_elevated(cmd: list[str], log: Callable[[str], None]) -> bool:
    if not cmd:
        return True
    if os.geteuid() == 0:
        full = cmd
    elif shutil.which("pkexec") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        full = ["pkexec", *cmd]
    elif shutil.which("sudo"):
        full = ["sudo", *cmd]
    else:
        log(f"Ejecuta como root: {' '.join(cmd)}")
        return False

    log(f"Ejecutando: {' '.join(full)}")
    proc = subprocess.run(full, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or f"código {proc.returncode}"
        log(f"Error instalando paquetes del sistema: {err}")
        return False
    return True


def install_missing_dependencies(
    log_cb: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """Automatically install missing system, Python, GSR, daemon, and voice dependencies."""
    log = log_cb or (lambda _m: None)
    rep = check_dependencies()
    if rep.all_ok:
        return True, "Todas las dependencias ya están instaladas."

    if rep.missing_system_pkgs:
        cmd = rep.install_command()
        if not cmd:
            return (
                False,
                f"Distro '{rep.distro.pretty_name}' sin gestor compatible automático. Instala manualmente: {', '.join(rep.missing_system_cmds)}",
            )
        if rep.distro.pkg_manager == "apt":
            if not _run_elevated(["apt-get", "update"], log):
                return False, "No se pudo actualizar apt (se canceló o falló permisos)."
        if not _run_elevated(cmd, log):
            return False, "No se pudieron instalar los paquetes del sistema."

    if rep.missing_python_pkgs:
        venv_pip = PROJECT_DIR / ".venv" / "bin" / "pip"
        if not venv_pip.exists():
            venv_dir = PROJECT_DIR / ".venv"
            log("Creando entorno virtual Python...")
            subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=False)
        pip_exe = str(venv_pip) if venv_pip.exists() else sys.executable
        pip_cmd = (
            [pip_exe, "install", *rep.missing_python_pkgs]
            if venv_pip.exists()
            else [pip_exe, "-m", "pip", "install", *rep.missing_python_pkgs]
        )
        log(f"Instalando paquetes Python: {', '.join(rep.missing_python_pkgs)}")
        proc = subprocess.run(pip_cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            return False, f"Error instalando Python deps: {proc.stderr.strip()}"

    if rep.missing_daemon and DAEMON_SRC.exists() and shutil.which("gcc"):
        DAEMON_BIN.parent.mkdir(parents=True, exist_ok=True)
        log("Compilando servicio universal anti-drift...")
        proc = subprocess.run(
            ["gcc", "-O2", "-Wall", "-Wextra", str(DAEMON_SRC), "-o", str(DAEMON_BIN), "-lm"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            shutil.copy2(DAEMON_SRC, DAEMON_BIN.with_suffix(".c"))
            subprocess.run(
                ["systemctl", "--user", "restart", "xbox-antidrift.service"],
                capture_output=True,
                check=False,
            )
        else:
            return False, f"Error compilando servicio C: {proc.stderr.strip()}"

    if rep.missing_gsr and not _has_gsr():
        if shutil.which("flatpak"):
            log("Instalando GPU Screen Recorder (Flatpak usuario)...")
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
                check=False,
            )
            proc = subprocess.run(
                ["flatpak", "install", "--user", "-y", "flathub", GSR_FLATPAK_ID],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                log(f"Aviso Flatpak: {proc.stderr.strip()}")
        else:
            log("Aviso: 'flatpak' no encontrado para GPU Screen Recorder.")

    if rep.missing_vosk_model and not _has_vosk_model():
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        zip_path = MODELS_DIR / f"{VOSK_MODEL_NAME}.zip"
        log("Descargando modelo de voz offline (~38 MB)...")
        try:
            with urllib.request.urlopen(VOSK_MODEL_URL, timeout=60) as resp:
                with zip_path.open("wb") as out:
                    shutil.copyfileobj(resp, out)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(MODELS_DIR)
            zip_path.unlink(missing_ok=True)
        except Exception as exc:
            zip_path.unlink(missing_ok=True)
            log(f"Aviso modelo de voz: {exc}")

    final = check_dependencies()
    if final.all_ok:
        return True, "Todas las dependencias quedaron instaladas correctamente."
    return True, "; ".join(final.summary_lines())


if __name__ == "__main__":
    if "--install" in sys.argv:
        ok, msg = install_missing_dependencies(print)
        print(msg)
        raise SystemExit(0 if ok else 1)
    report = check_dependencies()
    for line in report.summary_lines():
        print(line)
    raise SystemExit(0 if report.all_ok else 2)
