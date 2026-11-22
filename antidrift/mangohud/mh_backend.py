"""MangoHud config sync, live window/process discovery, interactive picker, and launcher."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .mh_config import (
    MangoHudConfig,
    MangoHudWindowRule,
    default_mangohud_dir,
    render_mangohud_conf,
    sanitize_process_name,
)

ENV_MANGOHUD_PATH = Path.home() / ".config" / "environment.d" / "xbox-antidrift-mangohud.conf"
_MANAGED_INDEX_NAME = ".antidrift-managed.json"

_IGNORED_PROCESSES = {
    "wineserver",
    "winedevice.exe",
    "services.exe",
    "explorer.exe",
    "plugplay.exe",
    "svchost.exe",
    "rpcss.exe",
    "tabbed.exe",
    "conhost.exe",
    "start.exe",
    "xalia.exe",
    "systemd",
    "dbus-daemon",
    "kwin_wayland",
    "kwin_x11",
    "plasmashell",
    "xwayland",
    "pipewire",
    "pipewire-pulse",
    "wireplumber",
    "bash",
    "sh",
    "zsh",
    "pv-adverb",
    "srt-bwrap",
    "umu-shim",
}


@dataclass
class DetectedWindow:
    """Live open window or graphical process eligible for MangoHud."""

    pid: int
    title: str
    process_name: str
    exec_cmd: str
    api_hint: str = "Ventana"
    hud_loaded: bool = False
    wm_class: str = ""


@dataclass
class DesktopAppInfo:
    """Installed GUI application parsed from .desktop entries."""

    name: str
    process_name: str
    exec_cmd: str


def _env_mangohud_path() -> Path:
    cfg_root = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if cfg_root:
        return Path(cfg_root) / "environment.d" / "xbox-antidrift-mangohud.conf"
    return ENV_MANGOHUD_PATH


def mangohud_version() -> str:
    """Return installed MangoHud version string or empty string if absent."""
    exe = shutil.which("mangohud") or shutil.which("mangohud.x86_64")
    if not exe:
        return ""
    try:
        proc = subprocess.run(
            ["mangohudctl", "--version"] if shutil.which("mangohudctl") else [exe, "--version"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        out = (proc.stdout or proc.stderr or "").strip().splitlines()
        return out[-1].strip() if out else "instalado"
    except OSError:
        return "instalado"


def extract_process_name_from_cmdline(argv: list[str], exe_path: str = "") -> str:
    """Resolve real binary / Windows .exe name for MangoHud config matching."""
    for arg in argv:
        low = arg.lower()
        if low.endswith(".exe") and not low.endswith(("umu.exe", "proton.exe")):
            name = sanitize_process_name(arg)
            if name:
                return name
    if exe_path:
        base = sanitize_process_name(exe_path)
        if base and not base.startswith(("wine64", "wine-", "python")):
            return base
    if argv:
        first = sanitize_process_name(argv[0])
        if first.startswith("python") and len(argv) >= 2:
            for a in argv[1:]:
                if not a.startswith("-"):
                    return sanitize_process_name(a)
        return first
    return ""


def _read_proc_cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return [p.decode("utf-8", errors="replace") for p in raw.split(b"\x00") if p]
    except OSError:
        return []


def _read_proc_env(pid: int) -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return env
    for entry in raw.split(b"\x00"):
        if b"=" not in entry:
            continue
        k, v = entry.split(b"=", 1)
        env[k.decode("utf-8", errors="replace")] = v.decode("utf-8", errors="replace")
    return env


def _inspect_proc_graphics(pid: int) -> tuple[str, bool]:
    """Return (api_hint, hud_loaded) by inspecting /proc/<pid>/maps and env."""
    env = _read_proc_env(pid)
    hud_loaded = env.get("MANGOHUD") == "1"
    has_vk = False
    has_gl = False
    try:
        maps_txt = Path(f"/proc/{pid}/maps").read_text(encoding="utf-8", errors="replace")
        if "libMangoHud" in maps_txt:
            hud_loaded = True
        if "libvulkan" in maps_txt or "vulkan" in maps_txt.lower():
            has_vk = True
        if "libGL" in maps_txt or "libEGL" in maps_txt:
            has_gl = True
    except OSError:
        pass
    api = "Vulkan + GL" if (has_vk and has_gl) else ("Vulkan" if has_vk else ("OpenGL" if has_gl else "Ventana"))
    return api, hud_loaded


def _xprop_open_windows() -> dict[int, tuple[str, str]]:
    """Query X11/XWayland open windows returning {pid: (title, wm_class)}."""
    if not shutil.which("xprop") or not os.environ.get("DISPLAY"):
        return {}
    res: dict[int, tuple[str, str]] = {}
    try:
        root_out = subprocess.run(
            ["xprop", "-root", "_NET_CLIENT_LIST"],
            capture_output=True,
            text=True,
            timeout=1.8,
            check=False,
        )
        if root_out.returncode != 0 or "#" not in root_out.stdout:
            return res
        wids = re.findall(r"0x[0-9a-fA-F]+", root_out.stdout.split("#", 1)[1])
        for wid in wids[:40]:
            info = subprocess.run(
                ["xprop", "-id", wid, "_NET_WM_PID", "_NET_WM_NAME", "WM_NAME", "WM_CLASS"],
                capture_output=True,
                text=True,
                timeout=0.7,
                check=False,
            )
            if info.returncode != 0:
                continue
            pid_m = re.search(r"_NET_WM_PID\(CARDINAL\)\s*=\s*(\d+)", info.stdout)
            if not pid_m:
                continue
            pid = int(pid_m.group(1))
            title_m = re.search(r'_NET_WM_NAME\([^)]+\)\s*=\s*"([^"]*)"', info.stdout)
            if not title_m:
                title_m = re.search(r'WM_NAME\([^)]+\)\s*=\s*"([^"]*)"', info.stdout)
            cls_m = re.search(r'WM_CLASS\([^)]+\)\s*=\s*"([^"]*)"', info.stdout)
            title = title_m.group(1).strip() if title_m else ""
            wm_class = cls_m.group(1).strip() if cls_m else ""
            if pid > 0 and title and pid not in res:
                res[pid] = (title, wm_class)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return res


def _clean_exec_for_launch(argv: list[str], exe_path: str) -> str:
    if not argv:
        return exe_path
    if argv[0].lower().endswith(".exe") or "wine" in exe_path.lower():
        return ""
    return " ".join(shlex.quote(a) for a in argv[:6])


def discover_open_windows() -> list[DetectedWindow]:
    """List open graphical windows, Vulkan/GL apps, and active Wine/Proton games."""
    x11_wins = _xprop_open_windows()
    uid = os.getuid()
    my_pid = os.getpid()
    found: list[DetectedWindow] = []
    seen_procs: set[tuple[str, int]] = set()

    for pdir in Path("/proc").iterdir():
        if not pdir.name.isdigit():
            continue
        pid = int(pdir.name)
        if pid == my_pid:
            continue
        try:
            if pdir.stat().st_uid != uid:
                continue
        except OSError:
            continue

        argv = _read_proc_cmdline(pid)
        if not argv:
            continue
        try:
            exe_path = os.readlink(pdir / "exe")
        except OSError:
            exe_path = ""

        proc_name = extract_process_name_from_cmdline(argv, exe_path)
        if not proc_name or proc_name.lower() in _IGNORED_PROCESSES:
            continue
        if "antidrift" in proc_name.lower() or "xbox-antidrift" in proc_name.lower():
            continue

        in_x11 = pid in x11_wins
        is_wine_exe = proc_name.lower().endswith(".exe")
        api_hint, hud_loaded = _inspect_proc_graphics(pid)
        if not in_x11 and not is_wine_exe and api_hint not in ("Vulkan", "Vulkan + GL", "OpenGL"):
            continue
        if is_wine_exe and api_hint == "Ventana":
            api_hint = "Wine/Proton"

        title, wm_class = x11_wins.get(pid, (proc_name, ""))
        if not title:
            title = proc_name
        key = (proc_name.lower(), pid)
        if key in seen_procs:
            continue
        seen_procs.add(key)

        found.append(
            DetectedWindow(
                pid=pid,
                title=title,
                process_name=proc_name,
                exec_cmd=_clean_exec_for_launch(argv, exe_path),
                api_hint=api_hint,
                hud_loaded=hud_loaded,
                wm_class=wm_class,
            )
        )

    found.sort(key=lambda w: (0 if w.process_name.lower().endswith(".exe") else 1, w.title.lower()))
    return found


def pick_window_with_click() -> tuple[DetectedWindow | None, str]:
    """Let the user click any visible window (via xprop) and resolve its process info."""
    if not shutil.which("xprop") or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return None, "Selector por clic no disponible (usa 'Detectar ventanas abiertas')."
    try:
        proc = subprocess.run(
            ["xprop", "_NET_WM_PID", "_NET_WM_NAME", "WM_NAME", "WM_CLASS"],
            capture_output=True,
            text=True,
            timeout=20.0,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, "Se agotó el tiempo esperando clic en una ventana."
    except OSError as exc:
        return None, f"No se pudo lanzar xprop: {exc}"

    if proc.returncode != 0:
        return None, "Selección cancelada (si es app Wayland nativa, selecciónala de la lista)."

    pid_m = re.search(r"_NET_WM_PID\(CARDINAL\)\s*=\s*(\d+)", proc.stdout)
    title_m = re.search(r'_NET_WM_NAME\([^)]+\)\s*=\s*"([^"]*)"', proc.stdout) or re.search(
        r'WM_NAME\([^)]+\)\s*=\s*"([^"]*)"', proc.stdout
    )
    cls_m = re.search(r'WM_CLASS\([^)]+\)\s*=\s*"([^"]*)"', proc.stdout)

    title = title_m.group(1).strip() if title_m else ""
    wm_class = cls_m.group(1).strip() if cls_m else ""
    pid = int(pid_m.group(1)) if pid_m else 0

    proc_name = ""
    exec_cmd = ""
    api_hint = "Ventana"
    hud_loaded = False
    if pid > 0:
        argv = _read_proc_cmdline(pid)
        try:
            exe_path = os.readlink(f"/proc/{pid}/exe")
        except OSError:
            exe_path = ""
        proc_name = extract_process_name_from_cmdline(argv, exe_path)
        exec_cmd = _clean_exec_for_launch(argv, exe_path)
        api_hint, hud_loaded = _inspect_proc_graphics(pid)

    if not proc_name:
        proc_name = sanitize_process_name(wm_class or title)
    if not proc_name:
        return None, "No se pudo identificar el ejecutable de esa ventana."

    return (
        DetectedWindow(
            pid=pid,
            title=title or proc_name,
            process_name=proc_name,
            exec_cmd=exec_cmd,
            api_hint=api_hint,
            hud_loaded=hud_loaded,
            wm_class=wm_class,
        ),
        f"Ventana capturada: {title or proc_name} ({proc_name})",
    )


def list_desktop_apps() -> list[DesktopAppInfo]:
    """List installed GUI .desktop applications for easy one-click MangoHud launching."""
    dirs = [
        Path.home() / ".local" / "share" / "applications",
        Path("/usr/local/share/applications"),
        Path("/usr/share/applications"),
    ]
    apps: list[DesktopAppInfo] = []
    seen: set[str] = set()
    for d in dirs:
        if not d.exists():
            continue
        for f in sorted(d.glob("*.desktop")):
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            name = ""
            exec_line = ""
            no_disp = False
            in_entry = False
            for line in txt.splitlines():
                s = line.strip()
                if s.startswith("["):
                    in_entry = (s == "[Desktop Entry]")
                    continue
                if not in_entry or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                if k == "Name" and not name:
                    name = v.strip()
                elif k == "Exec" and not exec_line:
                    exec_line = v.strip()
                elif k in ("NoDisplay", "Hidden") and v.strip().lower() == "true":
                    no_disp = True
            if no_disp or not name or not exec_line:
                continue
            clean_exec = re.sub(r"\s+%[fFuUdDnNickvm]", "", exec_line).strip()
            try:
                parts = shlex.split(clean_exec)
            except ValueError:
                parts = clean_exec.split()
            if not parts:
                continue
            pname = sanitize_process_name(parts[0])
            if parts[0] in ("env", "/usr/bin/env") and len(parts) >= 2:
                for p in parts[1:]:
                    if "=" not in p:
                        pname = sanitize_process_name(p)
                        break
            if not pname or pname.lower() in seen:
                continue
            seen.add(pname.lower())
            apps.append(DesktopAppInfo(name=name, process_name=pname, exec_cmd=clean_exec))

    apps.sort(key=lambda a: a.name.lower())
    return apps


def sync_mangohud_system_files(
    cfg: MangoHudConfig,
    mh_dir: Path | None = None,
    env_file: Path | None = None,
) -> list[Path]:
    """Write MangoHud.conf, per-window <app>.conf files, and session MANGOHUD env."""
    clean = cfg.clamped()
    target_dir = mh_dir or default_mangohud_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    main_conf = target_dir / "MangoHud.conf"
    main_conf.write_text(
        render_mangohud_conf(clean, rule=None, force_no_display=(clean.session_mode == "selective")),
        encoding="utf-8",
    )
    written.append(main_conf)

    # Clean up previously managed per-app files that were removed or disabled
    idx_file = target_dir / _MANAGED_INDEX_NAME
    prev_managed: list[str] = []
    if idx_file.exists():
        try:
            prev_managed = list(json.loads(idx_file.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            prev_managed = []

    new_managed: list[str] = []
    for rule in clean.window_rules:
        if not rule.enabled:
            continue
        fname = f"{rule.process_name}.conf"
        rpath = target_dir / fname
        rpath.write_text(
            render_mangohud_conf(clean, rule=rule, force_no_display=False),
            encoding="utf-8",
        )
        new_managed.append(fname)
        written.append(rpath)

    for old_name in prev_managed:
        if old_name not in new_managed:
            old_path = target_dir / old_name
            if old_path.exists():
                old_path.unlink(missing_ok=True)
    idx_file.write_text(json.dumps(new_managed, indent=2), encoding="utf-8")

    # Session environment (~/.config/environment.d/)
    epath = env_file or _env_mangohud_path()
    want_global_env = clean.session_mode in ("selective", "global")
    if want_global_env:
        epath.parent.mkdir(parents=True, exist_ok=True)
        epath.write_text("MANGOHUD=1\n", encoding="utf-8")
    elif epath.exists():
        epath.unlink(missing_ok=True)

    # Live session systemd --user environment & running MangoHud hot-reload
    if mh_dir is None and shutil.which("systemctl"):
        try:
            if want_global_env:
                subprocess.run(
                    ["systemctl", "--user", "set-environment", "MANGOHUD=1"],
                    capture_output=True,
                    timeout=2.0,
                    check=False,
                )
            else:
                subprocess.run(
                    ["systemctl", "--user", "unset-environment", "MANGOHUD"],
                    capture_output=True,
                    timeout=2.0,
                    check=False,
                )
        except OSError:
            pass

    if mh_dir is None and shutil.which("mangohudctl"):
        try:
            subprocess.run(["mangohudctl", "reload-cfg"], capture_output=True, timeout=1.5, check=False)
        except OSError:
            pass

    return written


def sync_flatpak_mangohud(enabled: bool) -> tuple[bool, str]:
    """Configure Flatpak user override so Flatpak apps see MangoHud and config dir."""
    if not shutil.which("flatpak"):
        return False, "Flatpak no está instalado."
    try:
        if enabled:
            proc = subprocess.run(
                [
                    "flatpak",
                    "override",
                    "--user",
                    "--env=MANGOHUD=1",
                    "--filesystem=xdg-config/MangoHud:ro",
                ],
                capture_output=True,
                text=True,
                timeout=4.0,
                check=False,
            )
            ok = proc.returncode == 0
            return ok, "MangoHud activado en apps Flatpak." if ok else (proc.stderr.strip() or "Error Flatpak.")
        proc = subprocess.run(
            ["flatpak", "override", "--user", "--unset-env=MANGOHUD"],
            capture_output=True,
            text=True,
            timeout=4.0,
            check=False,
        )
        ok = proc.returncode == 0
        return ok, "MangoHud desactivado en apps Flatpak." if ok else (proc.stderr.strip() or "Error Flatpak.")
    except OSError as exc:
        return False, f"Error ejecutando flatpak: {exc}"


def toggle_live_mangohud(show: bool | None = None) -> tuple[bool, str]:
    """Toggle or explicitly show/hide currently running MangoHud instances via mangohudctl."""
    if not shutil.which("mangohudctl"):
        return False, "mangohudctl no está disponible."
    try:
        if show is None:
            cmd = ["mangohudctl", "toggle", "no_display"]
        else:
            cmd = ["mangohudctl", "set", "no_display", "false" if show else "true"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0, check=False)
        if proc.returncode == 0:
            return True, "Señal enviada a ventanas MangoHud activas."
        return False, proc.stderr.strip() or "No hay ventanas escuchando a mangohudctl."
    except OSError as exc:
        return False, f"Error en mangohudctl: {exc}"


def launch_command_with_mangohud(
    exec_cmd: str,
    cfg: MangoHudConfig,
    rule: MangoHudWindowRule | None = None,
) -> tuple[bool, str]:
    """Launch an application/executable directly wrapped with MangoHud."""
    if not (shutil.which("mangohud") or shutil.which("mangohud.x86_64")):
        return False, "MangoHud no está instalado todavía."
    cmd_str = (exec_cmd or "").strip()
    if not cmd_str:
        return False, "No hay comando ejecutable configurado para esta ventana."

    try:
        parts = shlex.split(cmd_str)
    except ValueError:
        parts = cmd_str.split()
    if not parts:
        return False, "Comando vacío."

    cache_dir = Path.home() / ".cache" / "antidrift-studio"
    cache_dir.mkdir(parents=True, exist_ok=True)
    conf_path = cache_dir / "mangohud-launch.conf"
    conf_path.write_text(
        render_mangohud_conf(cfg, rule=rule, force_no_display=False),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["MANGOHUD"] = "1"
    env["MANGOHUD_CONFIGFILE"] = str(conf_path)
    try:
        subprocess.Popen(
            ["mangohud", "--dlsym", *parts],
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True, f"Lanzado con MangoHud: {parts[0]}"
    except OSError:
        try:
            subprocess.Popen(
                ["mangohud", *parts],
                env=env,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True, f"Lanzado con MangoHud: {parts[0]}"
        except OSError as exc:
            return False, f"No se pudo lanzar '{cmd_str}': {exc}"


def launch_demo_window_with_mangohud(cfg: MangoHudConfig) -> tuple[bool, str]:
    """Open a lightweight OpenGL/Vulkan test window running MangoHud live."""
    if shutil.which("vkcube"):
        return launch_command_with_mangohud("vkcube", cfg)
    if shutil.which("glxgears"):
        return launch_command_with_mangohud("glxgears", cfg)
    demo_cmd = f"{shlex.quote(sys.executable)} -m antidrift.mangohud.demo_window"
    return launch_command_with_mangohud(demo_cmd, cfg)
