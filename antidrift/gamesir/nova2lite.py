"""GameSir Nova 2 Lite (T4N2, VID 0x3537) vendor HID (0xFF7A) protocol and udev helpers."""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

GAMESIR_VID = "3537"
PID_WIRED = "100f"
PID_DONGLE = "1098"
PID_IDLE_DONGLE = "2106"

VENDOR_USAGE_PAGE_BYTES = b"\x06\x7a\xff"
REPORT_LEN = 64

GAMESIR_UDEV_RULE_PATH = Path("/etc/udev/rules.d/70-antidrift-gamesir.rules")
GAMESIR_UDEV_RULE_CONTENT = (
    '# Anti-Drift Studio: acceso de usuario a mandos GameSir (hidraw)\n'
    'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="3537", MODE="0660", TAG+="uaccess"\n'
)

# Back paddle remap options for L4 / R4 (matches T4N2 0xF1 hardware codes)
PADDLE_TARGETS: dict[str, int | None] = {
    "Sin cambio": None,
    "A": 0xB4,
    "B": 0xB5,
    "X": 0xB7,
    "Y": 0xB8,
    "LB": 0xBA,
    "RB": 0xBB,
    "LT": 0xBE,
    "RT": 0xBF,
    "LS": 0xC0,
    "RS": 0xC1,
    "View": 0xBC,
    "Menu": 0xBD,
    "Arriba (D-Pad)": 0xB0,
    "Abajo (D-Pad)": 0xB1,
    "Izquierda (D-Pad)": 0xB2,
    "Derecha (D-Pad)": 0xB3,
    "Desactivado": 0xD2,
}

_HW_L4 = 0xCB
_HW_R4 = 0xCD


@dataclass
class GameSirDeviceInfo:
    """Detected GameSir controller exposed on hidraw (Usage Page 0xFF7A)."""

    hidraw_path: str
    vid_pid: str
    model_name: str
    transport: str
    has_permission: bool
    firmware: str = ""
    battery: int | None = None
    charging: bool = False


@dataclass
class GameSirHardwareConfig:
    """Persistent GameSir Nova 2 Lite hardware configuration."""

    vib_left: int = 75
    vib_right: int = 75
    hair_trigger_lt: bool = False
    hair_trigger_rt: bool = False
    dpad_4way_lock: bool = False
    dpad_swap_lstick: bool = False
    l4_target: str = "Sin cambio"
    r4_target: str = "Sin cambio"

    def copy(self) -> GameSirHardwareConfig:
        return replace(self)

    def clamped(self) -> GameSirHardwareConfig:
        def _snap_vib(v: int) -> int:
            cl = max(0, min(100, int(v)))
            return min((0, 25, 50, 75, 100), key=lambda step: abs(step - cl))

        l4 = self.l4_target if self.l4_target in PADDLE_TARGETS else "Sin cambio"
        r4 = self.r4_target if self.r4_target in PADDLE_TARGETS else "Sin cambio"
        return GameSirHardwareConfig(
            vib_left=_snap_vib(self.vib_left),
            vib_right=_snap_vib(self.vib_right),
            hair_trigger_lt=bool(self.hair_trigger_lt),
            hair_trigger_rt=bool(self.hair_trigger_rt),
            dpad_4way_lock=bool(self.dpad_4way_lock),
            dpad_swap_lstick=bool(self.dpad_swap_lstick),
            l4_target=l4,
            r4_target=r4,
        )


def default_gamesir_config_path() -> Path:
    cfg_root = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(cfg_root) if cfg_root else (Path.home() / ".config")
    return base / "xbox-antidrift-gamesir.json"


def load_gamesir_config(path: Path | None = None) -> GameSirHardwareConfig:
    p = path or default_gamesir_config_path()
    if not p.exists():
        return GameSirHardwareConfig()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return GameSirHardwareConfig()
        defaults = GameSirHardwareConfig()
        updates: dict[str, object] = {}
        for f in fields(GameSirHardwareConfig):
            if f.name not in raw:
                continue
            cur = getattr(defaults, f.name)
            val = raw[f.name]
            if isinstance(cur, bool):
                updates[f.name] = bool(val)
            elif isinstance(cur, int):
                updates[f.name] = int(val)
            else:
                updates[f.name] = str(val)
        return replace(defaults, **updates).clamped()
    except (OSError, ValueError, TypeError):
        return GameSirHardwareConfig()


def save_gamesir_config(cfg: GameSirHardwareConfig, path: Path | None = None) -> None:
    p = path or default_gamesir_config_path()
    clean = cfg.clamped()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(clean), indent=2, ensure_ascii=False), encoding="utf-8")


def frame_packet(prefix_bytes: list[int] | tuple[int, ...], total_len: int) -> bytes:
    """Build 65-byte write buffer (0x00 report ID + 64B payload) with T4N2 additive checksum."""
    buf = list(prefix_bytes) + [0x00] * (REPORT_LEN - len(prefix_bytes))
    if 1 <= total_len <= REPORT_LEN:
        buf[total_len - 1] = sum(buf[: total_len - 1]) & 0xFF
    return bytes([0x00] + buf[:REPORT_LEN])


def device_info_packet() -> bytes:
    """Query firmware and battery status (opcode 0x01)."""
    return bytes([0x00, 0x01, 0x01, 0x00] + [0x00] * (REPORT_LEN - 3))


def rumble_packet(left: int, right: int) -> bytes:
    """Direct motor vibration pulse packet (opcode 0x04)."""
    lv = max(0, min(255, int(left)))
    rv = max(0, min(255, int(right)))
    payload = [0x04, lv, 0x08, rv, 0x08, 0x00, 0x08, 0x00, 0x08]
    return bytes([0x00] + payload + [0x00] * (REPORT_LEN - len(payload)))


def build_vibration_packet(left_pct: int, right_pct: int) -> bytes:
    """Set persistent vibration strength (0..100, snapped to hardware steps)."""
    lv = max(0, min(100, int(left_pct)))
    rv = max(0, min(100, int(right_pct)))
    return frame_packet(
        [0x25, 0x01, 0x0D, 0x01, lv, 0x03, 0x00, 0x00, rv, 0x03, 0x00, 0x00],
        13,
    )


def build_hair_trigger_packet(lt_hair: bool, rt_hair: bool) -> bytes:
    """Set hardware hair-trigger mode for LT and RT (1=On, 2=Off)."""
    return frame_packet([0x21, 0x01, 1 if lt_hair else 2, 1 if rt_hair else 2], 5)


def build_dpad_packet(sub_cmd: int, enabled: bool) -> bytes:
    """Set D-pad behavior (sub_cmd 0x01=4-way lock, 0x04=swap with left stick)."""
    return frame_packet([0x23, sub_cmd, 0x06, 0x01, 1 if enabled else 0], 6)


def build_paddle_remap_packet(is_right_paddle: bool, target_label: str) -> bytes:
    """Remap L4 or R4 back paddle directly in controller hardware (opcode 0xF1)."""
    src_hw = _HW_R4 if is_right_paddle else _HW_L4
    target_hw = PADDLE_TARGETS.get(target_label)
    if target_hw is None:
        return frame_packet([0xF1, 0x02, 0x09, src_hw, 0x00, 0x00, 0x00, 0x00], 9)
    return frame_packet([0xF1, 0x01, 0x09, src_hw, 0x01, target_hw, 0x00, 0x00], 9)


def parse_device_info(data: bytes | list[int]) -> dict[str, object]:
    """Decode a 0x01 device-info reply into firmware, battery %, and charging."""
    if not data or len(data) < 19 or data[0] != 0x01 or data[1] == 0x00:
        return {}
    out: dict[str, object] = {}
    fw_lo, fw_hi = int(data[10]), int(data[11])
    if fw_hi or fw_lo:
        out["firmware"] = f"{fw_hi}.{fw_lo:02x}"
    bat_raw = int(data[18])
    if bat_raw == 0xFF:
        out["battery"] = 100
        out["charging"] = True
    else:
        out["charging"] = bool(bat_raw & 0x80)
        out["battery"] = min(100, bat_raw & 0x7F)
    return out


def _parse_hid_vid_pid(uevent_text: str) -> tuple[str, str]:
    for line in uevent_text.splitlines():
        if line.startswith("HID_ID="):
            parts = line.split("=", 1)[1].strip().split(":")
            if len(parts) == 3:
                return parts[1][-4:].lower(), parts[2][-4:].lower()
    return "", ""


def discover_gamesir_devices() -> list[GameSirDeviceInfo]:
    """Scan /sys/class/hidraw for GameSir Nova 2 Lite 0xFF7A vendor HID nodes."""
    found: list[GameSirDeviceInfo] = []
    sys_hid = Path("/sys/class/hidraw")
    if not sys_hid.exists():
        return found
    for hdir in sorted(sys_hid.glob("hidraw*")):
        dev_dir = hdir / "device"
        try:
            uevent = (dev_dir / "uevent").read_text(encoding="utf-8", errors="replace")
            rdesc = (dev_dir / "report_descriptor").read_bytes()
        except OSError:
            continue
        vid, pid = _parse_hid_vid_pid(uevent)
        if vid != GAMESIR_VID:
            continue
        if VENDOR_USAGE_PAGE_BYTES not in rdesc:
            continue
        devnode = f"/dev/{hdir.name}"
        if pid == PID_DONGLE:
            model = "GameSir Nova 2 Lite (Dongle 2.4G)"
            transport = "Dongle 2.4 GHz"
        elif pid == PID_WIRED:
            model = "GameSir Nova 2 Lite (Cable USB)"
            transport = "USB Cable"
        elif pid == PID_IDLE_DONGLE:
            model = "GameSir Dongle (Mando apagado)"
            transport = "Dongle 2.4 GHz"
        else:
            model = f"GameSir HID ({vid}:{pid})"
            transport = "USB / HID"
        has_perm = os.access(devnode, os.R_OK | os.W_OK)
        info = GameSirDeviceInfo(
            hidraw_path=devnode,
            vid_pid=f"{vid}:{pid}",
            model_name=model,
            transport=transport,
            has_permission=has_perm,
        )
        if has_perm and pid != PID_IDLE_DONGLE:
            extra = query_device_info(devnode)
            info.firmware = str(extra.get("firmware", ""))
            bat = extra.get("battery")
            info.battery = int(bat) if isinstance(bat, int) else None
            info.charging = bool(extra.get("charging", False))
        found.append(info)
    return found


def query_device_info(hidraw_path: str, timeout_s: float = 0.4) -> dict[str, object]:
    """Send 0x01 device-info query and return firmware/battery dict if available."""
    try:
        fd = os.open(hidraw_path, os.O_RDWR | os.O_NONBLOCK)
    except OSError:
        return {}
    try:
        os.write(fd, device_info_packet())
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            r, _, _ = select.select([fd], [], [], 0.05)
            if not r:
                continue
            data = os.read(fd, 64)
            parsed = parse_device_info(data)
            if parsed:
                return parsed
    except OSError:
        return {}
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    return {}


def apply_nova2lite_settings(hidraw_path: str, cfg: GameSirHardwareConfig) -> tuple[bool, str]:
    """Send hardware vibration, hair-trigger, D-pad, and L4/R4 remap packets to Nova 2 Lite."""
    clean = cfg.clamped()
    packets = [
        build_vibration_packet(clean.vib_left, clean.vib_right),
        build_hair_trigger_packet(clean.hair_trigger_lt, clean.hair_trigger_rt),
        build_dpad_packet(0x01, clean.dpad_4way_lock),
        build_dpad_packet(0x04, clean.dpad_swap_lstick),
        build_paddle_remap_packet(False, clean.l4_target),
        build_paddle_remap_packet(True, clean.r4_target),
    ]
    try:
        fd = os.open(hidraw_path, os.O_RDWR | os.O_NONBLOCK)
    except PermissionError:
        return False, "Sin permiso en hidraw. Pulsa 'Habilitar acceso HID (udev)' primero."
    except OSError as exc:
        return False, f"No se pudo abrir {hidraw_path}: {exc}"
    try:
        for pkt in packets:
            os.write(fd, pkt)
            time.sleep(0.012)
        return True, "Ajustes aplicados al hardware del GameSir Nova 2 Lite."
    except OSError as exc:
        return False, f"Error escribiendo al mando GameSir: {exc}"
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def test_nova2lite_rumble(hidraw_path: str, left_pct: int = 75, right_pct: int = 75) -> tuple[bool, str]:
    """Send a brief 350ms vibration pulse to verify GameSir HID communication."""
    lv = round((max(0, min(100, left_pct)) / 100.0) * 255)
    rv = round((max(0, min(100, right_pct)) / 100.0) * 255)
    try:
        fd = os.open(hidraw_path, os.O_RDWR | os.O_NONBLOCK)
    except PermissionError:
        return False, "Sin permiso en hidraw. Pulsa 'Habilitar acceso HID (udev)' primero."
    except OSError as exc:
        return False, f"No se pudo abrir {hidraw_path}: {exc}"
    try:
        os.write(fd, rumble_packet(lv, rv))
        time.sleep(0.35)
        os.write(fd, rumble_packet(0, 0))
        return True, "Prueba de vibración enviada al mando GameSir."
    except OSError as exc:
        return False, f"Error probando vibración: {exc}"
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def install_gamesir_udev_rule() -> tuple[bool, str]:
    """Install udev rule granting user-space hidraw access to VID 0x3537 (GameSir)."""
    script = (
        f"cat > {GAMESIR_UDEV_RULE_PATH} <<'EOF'\n"
        f"{GAMESIR_UDEV_RULE_CONTENT}"
        "EOF\n"
        "chmod 644 " + str(GAMESIR_UDEV_RULE_PATH) + "\n"
        "udevadm control --reload-rules\n"
        "udevadm trigger --subsystem-match=hidraw\n"
    )
    if os.geteuid() == 0:
        cmd = ["bash", "-c", script]
    elif shutil.which("pkexec") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        cmd = ["pkexec", "bash", "-c", script]
    elif shutil.which("sudo"):
        cmd = ["sudo", "bash", "-c", script]
    else:
        return False, "Se requieren permisos de administrador (pkexec/sudo) para instalar la regla udev."
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or f"código {proc.returncode}"
        return False, f"No se pudo instalar la regla udev: {err}"
    return True, "Acceso HID (udev) habilitado para mandos GameSir."
