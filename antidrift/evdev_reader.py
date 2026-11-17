"""Universal pure-Python Linux evdev controller discovery and live input reader."""

from __future__ import annotations

import errno
import fcntl
import os
import select
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .config import (
    AntiDriftConfig,
    compute_corrected_stick,
    scale_hw_to_i16,
    scale_trig_to_1023,
)

# Linux evdev constants (64-bit struct input_event)
INPUT_EVENT_STRUCT = struct.Struct("llHHi")
ABSINFO_STRUCT = struct.Struct("6i")

EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03

ABS_X = 0x00
ABS_Y = 0x01
ABS_Z = 0x02
ABS_RX = 0x03
ABS_RY = 0x04
ABS_RZ = 0x05
ABS_GAS = 0x09
ABS_BRAKE = 0x0A
ABS_HAT0X = 0x10
ABS_HAT0Y = 0x11

BTN_LEFT = 272
BTN_TRIGGER = 288
BTN_SOUTH = 304
BTN_TL2 = 312
BTN_TR2 = 313
BTN_DPAD_UP = 544
BTN_DPAD_DOWN = 545
BTN_DPAD_LEFT = 546
BTN_DPAD_RIGHT = 547

EVIOCGABS_BASE = 0x80184540
KEY_BUF_LEN = 96
EVIOCGKEY_96 = 0x80004518 | (KEY_BUF_LEN << 16)
EVIOCGBIT_KEY_96 = 0x80004521 | (KEY_BUF_LEN << 16)
EVIOCGBIT_ABS_8 = 0x80004523 | (8 << 16)

BUTTON_CODE_MAP: dict[int, str] = {
    304: "A",
    305: "B",
    307: "X",
    308: "Y",
    310: "LB",
    311: "RB",
    314: "VIEW",
    315: "MENU",
    316: "GUIDE",
    317: "LS",
    318: "RS",
}

GENERIC_JOY_BUTTON_MAP: dict[int, str] = {
    288: "A",
    289: "B",
    290: "X",
    291: "Y",
    292: "LB",
    293: "RB",
    296: "VIEW",
    297: "MENU",
    298: "LS",
    299: "RS",
}

ALL_BUTTON_NAMES: tuple[str, ...] = tuple(BUTTON_CODE_MAP.values())


def empty_buttons() -> dict[str, bool]:
    """Return a fresh dictionary with all Xbox buttons unpressed."""
    return {name: False for name in ALL_BUTTON_NAMES}


@dataclass
class ControllerDeviceInfo:
    """Metadata for one physical gamepad candidate."""

    path: str
    name: str
    vid_pid: str | None
    score: int


@dataclass
class HardwareAxisMap:
    """Axis mapping and calibration bounds discovered from physical evdev device."""

    lx_code: int = ABS_X
    ly_code: int = ABS_Y
    rx_code: int = ABS_RX
    ry_code: int = ABS_RY
    lt_code: int = ABS_Z
    rt_code: int = ABS_RZ
    dig_lt_code: int = -1
    dig_rt_code: int = -1
    use_generic_joy_btns: bool = False
    use_dpad_btns: bool = False

    lx_bounds: tuple[int, int] = (-32768, 32767)
    ly_bounds: tuple[int, int] = (-32768, 32767)
    rx_bounds: tuple[int, int] = (-32768, 32767)
    ry_bounds: tuple[int, int] = (-32768, 32767)
    lt_bounds: tuple[int, int] = (0, 1023)
    rt_bounds: tuple[int, int] = (0, 1023)


@dataclass
class DetectedDevices:
    """Discovered physical controller and virtual uinput event device paths."""

    physical_path: str | None = None
    physical_name: str | None = None
    physical_vid_pid: str | None = None
    virtual_path: str | None = None
    virtual_name: str | None = None


@dataclass
class ControllerState:
    """Real-time hardware and anti-drift virtual controller state."""

    mode: str = "disconnected"  # "virtual" | "direct" | "disconnected" | "simulated"
    physical_path: str | None = None
    physical_name: str | None = None
    physical_vid_pid: str | None = None
    virtual_path: str | None = None
    virtual_name: str | None = None
    error_message: str | None = None

    raw_lx: int = 0
    raw_ly: int = 0
    raw_rx: int = 0
    raw_ry: int = 0

    out_lx: int = 0
    out_ly: int = 0
    out_rx: int = 0
    out_ry: int = 0

    lt: int = 0
    rt: int = 0
    hat_x: int = 0
    hat_y: int = 0
    poll_hz: float = 0.0
    buttons: dict[str, bool] = field(default_factory=empty_buttons)
    extra_buttons: dict[int, bool] = field(default_factory=dict)
    supported_extra_keys: list[int] = field(default_factory=list)


def _read_sys_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _parse_uevent(path: Path) -> dict[str, str]:
    info: dict[str, str] = {}
    for line in _read_sys_text(path).splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip()] = v.strip()
    return info


def _bit_set(buf: bytearray, code: int) -> bool:
    idx = code >> 3
    return idx < len(buf) and bool(buf[idx] & (1 << (code & 7)))


def _open_readonly(path: str) -> int | None:
    try:
        return os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None


def _safe_close(fd: int | None) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


def _is_virtual_sys_device(ev_dir: Path, name: str, phys: str) -> bool:
    uevent = _parse_uevent(ev_dir / "device" / "uevent")
    devpath = uevent.get("DEVPATH", "")
    if "/virtual/" in devpath:
        return True
    if name == "Microsoft X-Box 360 pad" and not phys:
        return True
    return False


def _score_gamepad(ev_sys: Path) -> ControllerDeviceInfo | None:
    dev_dir = ev_sys / "device"
    name = _read_sys_text(dev_dir / "name")
    if not name:
        return None
    uevent = _parse_uevent(dev_dir / "uevent")
    phys = uevent.get("PHYS", "")
    if _is_virtual_sys_device(ev_sys, name, phys):
        return None

    lowered = name.lower()
    if any(bad in lowered for bad in ("motion sensor", "touchpad", "accelerometer", "mouse")):
        return None

    dev_node = f"/dev/input/{ev_sys.name}"
    fd = _open_readonly(dev_node)
    if fd is None:
        return None
    try:
        key_bits = bytearray(KEY_BUF_LEN)
        abs_bits = bytearray(8)
        fcntl.ioctl(fd, EVIOCGBIT_KEY_96, key_bits)
        fcntl.ioctl(fd, EVIOCGBIT_ABS_8, abs_bits)
        has_xy = _bit_set(abs_bits, ABS_X) and _bit_set(abs_bits, ABS_Y)
        has_mouse = _bit_set(key_bits, BTN_LEFT)
        has_south = _bit_set(key_bits, BTN_SOUTH)
        has_joy = _bit_set(key_bits, BTN_TRIGGER)
        if not has_xy or has_mouse or not (has_south or has_joy):
            return None

        score = 10
        if has_south:
            score += 50
        if _bit_set(abs_bits, ABS_RX) and _bit_set(abs_bits, ABS_RY):
            score += 25
        if _bit_set(abs_bits, ABS_HAT0X):
            score += 15
        if any(
            kw in lowered
            for kw in (
                "xbox",
                "x-box",
                "dualsense",
                "wireless controller",
                "switch",
                "pro controller",
                "8bitdo",
                "gamesir",
                "gamepad",
            )
        ):
            score += 20
    except OSError:
        return None
    finally:
        _safe_close(fd)

    vid = _read_sys_text(dev_dir / "id" / "vendor").lower()
    pid = _read_sys_text(dev_dir / "id" / "product").lower()
    vid_pid = f"{vid}:{pid}" if vid and pid else None
    return ControllerDeviceInfo(
        path=dev_node, name=name, vid_pid=vid_pid, score=score
    )


def list_physical_controllers() -> list[ControllerDeviceInfo]:
    """Return all connected physical gamepads sorted by suitability score."""
    items: list[ControllerDeviceInfo] = []
    for ev_sys in sorted(Path("/sys/class/input").glob("event*")):
        info = _score_gamepad(ev_sys)
        if info is not None:
            items.append(info)
    items.sort(key=lambda d: (-d.score, d.path))
    return items


def discover_devices(preferred_device: str = "auto") -> DetectedDevices:
    """Locate physical gamepad (any brand) and virtual Xbox 360 uinput device."""
    result = DetectedDevices()
    controllers = list_physical_controllers()

    if preferred_device and preferred_device != "auto":
        resolved = os.path.realpath(preferred_device)
        for c in controllers:
            if c.path == resolved:
                result.physical_path = c.path
                result.physical_name = c.name
                result.physical_vid_pid = c.vid_pid
                break

    if result.physical_path is None and controllers:
        best = controllers[0]
        result.physical_path = best.path
        result.physical_name = best.name
        result.physical_vid_pid = best.vid_pid

    for ev_sys in sorted(Path("/sys/class/input").glob("event*")):
        dev_dir = ev_sys / "device"
        name = _read_sys_text(dev_dir / "name")
        vid = _read_sys_text(dev_dir / "id" / "vendor").lower()
        pid = _read_sys_text(dev_dir / "id" / "product").lower()
        uevent = _parse_uevent(dev_dir / "uevent")
        phys = uevent.get("PHYS", "")
        if (
            name == "Microsoft X-Box 360 pad"
            and vid == "045e"
            and pid == "028e"
            and not phys
        ):
            result.virtual_path = f"/dev/input/{ev_sys.name}"
            result.virtual_name = name
            break

    return result


def read_abs_info(fd: int, axis_code: int) -> tuple[int, int, int] | None:
    """Return (value, min, max) via EVIOCGABS."""
    if axis_code < 0:
        return None
    buf = bytearray(ABSINFO_STRUCT.size)
    try:
        fcntl.ioctl(fd, EVIOCGABS_BASE + axis_code, buf)
    except OSError:
        return None
    val, amin, amax, *_ = ABSINFO_STRUCT.unpack(buf)
    return int(val), int(amin), int(amax)


def read_abs_value(fd: int, axis_code: int) -> int | None:
    """Query current axis value via EVIOCGABS (works even under EVIOCGRAB)."""
    res = read_abs_info(fd, axis_code)
    return res[0] if res is not None else None


def probe_hardware_map(fd: int) -> HardwareAxisMap:
    """Inspect axis codes, bounds, and trigger/button quirks for any gamepad."""
    hm = HardwareAxisMap()
    key_bits = bytearray(KEY_BUF_LEN)
    abs_bits = bytearray(8)
    try:
        fcntl.ioctl(fd, EVIOCGBIT_KEY_96, key_bits)
        fcntl.ioctl(fd, EVIOCGBIT_ABS_8, abs_bits)
    except OSError:
        return hm

    if _bit_set(abs_bits, ABS_RX) and _bit_set(abs_bits, ABS_RY):
        hm.rx_code, hm.ry_code = ABS_RX, ABS_RY
        hm.lt_code = ABS_Z if _bit_set(abs_bits, ABS_Z) else -1
        hm.rt_code = ABS_RZ if _bit_set(abs_bits, ABS_RZ) else -1
    elif _bit_set(abs_bits, ABS_Z) and _bit_set(abs_bits, ABS_RZ):
        hm.rx_code, hm.ry_code = ABS_Z, ABS_RZ
        hm.lt_code, hm.rt_code = -1, -1
    else:
        hm.rx_code, hm.ry_code = -1, -1
        hm.lt_code, hm.rt_code = -1, -1

    if hm.lt_code < 0 and _bit_set(abs_bits, ABS_BRAKE):
        hm.lt_code = ABS_BRAKE
    if hm.rt_code < 0 and _bit_set(abs_bits, ABS_GAS):
        hm.rt_code = ABS_GAS

    if hm.lt_code < 0 and _bit_set(key_bits, BTN_TL2):
        hm.dig_lt_code = BTN_TL2
    if hm.rt_code < 0 and _bit_set(key_bits, BTN_TR2):
        hm.dig_rt_code = BTN_TR2

    if not _bit_set(key_bits, BTN_SOUTH) and _bit_set(key_bits, BTN_TRIGGER):
        hm.use_generic_joy_btns = True
    if not _bit_set(abs_bits, ABS_HAT0X) and _bit_set(key_bits, BTN_DPAD_UP):
        hm.use_dpad_btns = True

    def bounds(code: int, dmin: int, dmax: int) -> tuple[int, int]:
        info = read_abs_info(fd, code)
        if info is not None and info[2] > info[1]:
            return info[1], info[2]
        return dmin, dmax

    hm.lx_bounds = bounds(hm.lx_code, -32768, 32767)
    hm.ly_bounds = bounds(hm.ly_code, -32768, 32767)
    hm.rx_bounds = bounds(hm.rx_code, -32768, 32767)
    hm.ry_bounds = bounds(hm.ry_code, -32768, 32767)
    hm.lt_bounds = bounds(hm.lt_code, 0, 1023)
    hm.rt_bounds = bounds(hm.rt_code, 0, 1023)
    return hm


def read_supported_extra_keys(fd: int) -> list[int]:
    """Return sorted EV_KEY codes outside standard Xbox buttons."""
    buf = bytearray(KEY_BUF_LEN)
    try:
        fcntl.ioctl(fd, EVIOCGBIT_KEY_96, buf)
    except OSError:
        return []
    extras: list[int] = []
    for code in range(128, KEY_BUF_LEN * 8):
        if code in BUTTON_CODE_MAP:
            continue
        if _bit_set(buf, code):
            extras.append(code)
    return extras


def read_raw_pressed_codes(fd: int) -> set[int]:
    """Query all currently held physical EV_KEY codes via EVIOCGKEY."""
    buf = bytearray(KEY_BUF_LEN)
    try:
        fcntl.ioctl(fd, EVIOCGKEY_96, buf)
    except OSError:
        return set()
    pressed: set[int] = set()
    for byte_idx, byte_val in enumerate(buf):
        if byte_val == 0:
            continue
        for bit in range(8):
            if byte_val & (1 << bit):
                pressed.add((byte_idx << 3) + bit)
    return pressed


def read_key_states(fd: int) -> dict[str, bool]:
    """Query initial pressed state of mapped controller buttons via EVIOCGKEY."""
    buttons = empty_buttons()
    pressed = read_raw_pressed_codes(fd)
    for code, name in BUTTON_CODE_MAP.items():
        buttons[name] = code in pressed
    return buttons


class ControllerReaderThread(QThread):
    """Background evdev poller producing 60Hz live ControllerState updates."""

    state_updated = Signal(object)
    raw_key_detected = Signal(int)
    calibration_progress = Signal(int)
    calibration_finished = Signal(int, int, int, int, int, int)
    calibration_failed = Signal(str)

    def __init__(self, config: AntiDriftConfig, parent=None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._config = config.copy()
        self._stop_requested = False

        self._calibrating = False
        self._cal_deadline = 0.0
        self._cal_duration = 1.5
        self._cal_samples: list[tuple[int, int, int, int]] = []

    def update_config(self, config: AntiDriftConfig) -> None:
        """Update config used for live direct-mode preview calculations."""
        with self._lock:
            self._config = config.copy()

    def start_calibration(self, duration_sec: float = 1.5) -> None:
        """Begin collecting raw rest samples for automatic center calibration."""
        with self._lock:
            self._calibrating = True
            self._cal_duration = max(0.4, duration_sec)
            self._cal_deadline = time.monotonic() + self._cal_duration
            self._cal_samples.clear()

    def stop(self) -> None:
        """Request clean shutdown of the reader thread."""
        self._stop_requested = True
        self.wait(1500)

    def _sync_abs_from_fd(
        self,
        fd: int,
        hm: HardwareAxisMap,
        target_raw: bool,
        state: ControllerState,
    ) -> None:
        if target_raw:
            lx = read_abs_value(fd, hm.lx_code)
            ly = read_abs_value(fd, hm.ly_code)
            rx = read_abs_value(fd, hm.rx_code)
            ry = read_abs_value(fd, hm.ry_code)
            lt = read_abs_value(fd, hm.lt_code)
            rt = read_abs_value(fd, hm.rt_code)
            if lx is not None:
                state.raw_lx = scale_hw_to_i16(lx, *hm.lx_bounds)
            if ly is not None:
                state.raw_ly = scale_hw_to_i16(ly, *hm.ly_bounds)
            if rx is not None:
                state.raw_rx = scale_hw_to_i16(rx, *hm.rx_bounds)
            if ry is not None:
                state.raw_ry = scale_hw_to_i16(ry, *hm.ry_bounds)
            if lt is not None:
                state.lt = scale_trig_to_1023(lt, *hm.lt_bounds)
            if rt is not None:
                state.rt = scale_trig_to_1023(rt, *hm.rt_bounds)
        else:
            for ax, attr in (
                (ABS_X, "out_lx"),
                (ABS_Y, "out_ly"),
                (ABS_RX, "out_rx"),
                (ABS_RY, "out_ry"),
                (ABS_Z, "lt"),
                (ABS_RZ, "rt"),
            ):
                val = read_abs_value(fd, ax)
                if val is not None:
                    setattr(state, attr, val)
        hx = read_abs_value(fd, ABS_HAT0X)
        hy = read_abs_value(fd, ABS_HAT0Y)
        if hx is not None:
            state.hat_x = hx
        if hy is not None:
            state.hat_y = hy

    def _apply_event(
        self,
        ev_type: int,
        ev_code: int,
        ev_val: int,
        is_virtual: bool,
        hm: HardwareAxisMap,
        dpad_bits: list[int],
        state: ControllerState,
    ) -> None:
        if ev_type == EV_KEY:
            if not is_virtual:
                if ev_code == hm.dig_lt_code:
                    state.lt = 1023 if ev_val else 0
                    return
                if ev_code == hm.dig_rt_code:
                    state.rt = 1023 if ev_val else 0
                    return
                if hm.use_dpad_btns and ev_code in (
                    BTN_DPAD_UP,
                    BTN_DPAD_DOWN,
                    BTN_DPAD_LEFT,
                    BTN_DPAD_RIGHT,
                ):
                    idx = ev_code - BTN_DPAD_UP
                    dpad_bits[idx] = 1 if ev_val else 0
                    state.hat_x = dpad_bits[3] - dpad_bits[2]
                    state.hat_y = dpad_bits[1] - dpad_bits[0]
                    return
            name = (
                GENERIC_JOY_BUTTON_MAP.get(ev_code)
                if (not is_virtual and hm.use_generic_joy_btns)
                else BUTTON_CODE_MAP.get(ev_code)
            )
            if name is not None:
                state.buttons[name] = bool(ev_val)
            return

        if ev_type == EV_ABS:
            if ev_code == ABS_HAT0X:
                state.hat_x = -1 if ev_val < 0 else (1 if ev_val > 0 else 0)
            elif ev_code == ABS_HAT0Y:
                state.hat_y = -1 if ev_val < 0 else (1 if ev_val > 0 else 0)
            elif is_virtual:
                if ev_code == ABS_X:
                    state.out_lx = ev_val
                elif ev_code == ABS_Y:
                    state.out_ly = ev_val
                elif ev_code == ABS_RX:
                    state.out_rx = ev_val
                elif ev_code == ABS_RY:
                    state.out_ry = ev_val
                elif ev_code == ABS_Z:
                    state.lt = ev_val
                elif ev_code == ABS_RZ:
                    state.rt = ev_val
            else:
                if ev_code == hm.lx_code:
                    state.raw_lx = scale_hw_to_i16(ev_val, *hm.lx_bounds)
                elif ev_code == hm.ly_code:
                    state.raw_ly = scale_hw_to_i16(ev_val, *hm.ly_bounds)
                elif ev_code == hm.rx_code:
                    state.raw_rx = scale_hw_to_i16(ev_val, *hm.rx_bounds)
                elif ev_code == hm.ry_code:
                    state.raw_ry = scale_hw_to_i16(ev_val, *hm.ry_bounds)
                elif ev_code == hm.lt_code:
                    state.lt = scale_trig_to_1023(ev_val, *hm.lt_bounds)
                elif ev_code == hm.rt_code:
                    state.rt = scale_trig_to_1023(ev_val, *hm.rt_bounds)

    def _step_calibration(self, state: ControllerState, has_raw_source: bool) -> None:
        with self._lock:
            if not self._calibrating:
                return
            if not has_raw_source:
                self._calibrating = False
                self._cal_samples.clear()
                self.calibration_failed.emit(
                    "Conecta primero tu mando para poder calibrarlo."
                )
                return

            self._cal_samples.append(
                (state.raw_lx, state.raw_ly, state.raw_rx, state.raw_ry)
            )
            remaining = self._cal_deadline - time.monotonic()
            pct = int(
                max(
                    0.0,
                    min(100.0, (1.0 - remaining / self._cal_duration) * 100.0),
                )
            )
            self.calibration_progress.emit(pct)

            if remaining <= 0.0 and self._cal_samples:
                n = len(self._cal_samples)
                lxs = [s[0] for s in self._cal_samples]
                lys = [s[1] for s in self._cal_samples]
                rxs = [s[2] for s in self._cal_samples]
                rys = [s[3] for s in self._cal_samples]
                avg_lx = round(sum(lxs) / n)
                avg_ly = round(sum(lys) / n)
                avg_rx = round(sum(rxs) / n)
                avg_ry = round(sum(rys) / n)
                jitter_l = max(max(lxs) - min(lxs), max(lys) - min(lys))
                jitter_r = max(max(rxs) - min(rxs), max(rys) - min(rys))
                self._calibrating = False
                self._cal_samples.clear()
                self.calibration_finished.emit(
                    avg_lx, avg_ly, avg_rx, avg_ry, jitter_l, jitter_r
                )

    def run(self) -> None:
        stream_fd: int | None = None
        raw_fd: int | None = None
        stream_path: str | None = None
        raw_path: str | None = None
        stream_is_virtual = False
        hm = HardwareAxisMap()
        dpad_bits = [0, 0, 0, 0]
        last_discover = 0.0
        last_emit = 0.0
        last_virtual_event_at = 0.0
        devices = DetectedDevices()
        state = ControllerState()
        prev_raw_pressed: set[int] = set()
        syn_times: deque[float] = deque()

        try:
            while not self._stop_requested:
                now = time.monotonic()
                if now - last_discover >= 0.6 or (
                    stream_fd is None and raw_fd is None and now - last_discover >= 0.35
                ):
                    last_discover = now
                    with self._lock:
                        pref_dev = self._config.device_path
                    new_devices = discover_devices(pref_dev)
                    target_stream = new_devices.virtual_path or new_devices.physical_path
                    target_is_virtual = new_devices.virtual_path is not None
                    target_raw = new_devices.physical_path

                    if target_stream != stream_path or target_raw != raw_path:
                        if raw_fd is not None and raw_fd != stream_fd:
                            _safe_close(raw_fd)
                        _safe_close(stream_fd)
                        stream_fd = None
                        raw_fd = None
                        stream_path = target_stream
                        raw_path = target_raw
                        stream_is_virtual = target_is_virtual
                        devices = new_devices
                        hm = HardwareAxisMap()
                        dpad_bits = [0, 0, 0, 0]

                        state = ControllerState(
                            physical_path=devices.physical_path,
                            physical_name=devices.physical_name,
                            physical_vid_pid=devices.physical_vid_pid,
                            virtual_path=devices.virtual_path,
                            virtual_name=devices.virtual_name,
                        )

                        if raw_path is not None:
                            raw_fd = _open_readonly(raw_path)
                            if raw_fd is not None:
                                hm = probe_hardware_map(raw_fd)
                                state.supported_extra_keys = read_supported_extra_keys(
                                    raw_fd
                                )
                                prev_raw_pressed = read_raw_pressed_codes(raw_fd)
                                self._sync_abs_from_fd(
                                    raw_fd, hm, target_raw=True, state=state
                                )

                        if stream_path is not None:
                            if stream_path == raw_path and raw_fd is not None:
                                stream_fd = raw_fd
                            else:
                                stream_fd = _open_readonly(stream_path)
                            if stream_fd is None:
                                state.error_message = (
                                    f"Sin permiso para leer {stream_path}"
                                )
                            else:
                                state.buttons = read_key_states(stream_fd)
                                self._sync_abs_from_fd(
                                    stream_fd,
                                    hm,
                                    target_raw=not stream_is_virtual,
                                    state=state,
                                )

                        state.mode = (
                            ("virtual" if stream_is_virtual else "direct")
                            if stream_fd is not None
                            else "disconnected"
                        )
                        last_virtual_event_at = 0.0
                        last_emit = 0.0

                dirty = False
                if stream_fd is not None:
                    try:
                        readable, _, _ = select.select([stream_fd], [], [], 0.014)
                    except (OSError, ValueError):
                        readable = []
                        if raw_fd == stream_fd:
                            raw_fd = None
                            raw_path = None
                        _safe_close(stream_fd)
                        stream_fd = None
                        stream_path = None
                        state.mode = "disconnected"
                        dirty = True

                    if readable:
                        try:
                            chunk = os.read(stream_fd, INPUT_EVENT_STRUCT.size * 64)
                            for offset in range(
                                0,
                                len(chunk) - (len(chunk) % INPUT_EVENT_STRUCT.size),
                                INPUT_EVENT_STRUCT.size,
                            ):
                                _, _, ev_type, ev_code, ev_val = (
                                    INPUT_EVENT_STRUCT.unpack_from(chunk, offset)
                                )
                                if ev_type == EV_SYN and ev_code == 0:
                                    syn_times.append(time.monotonic())
                                    if stream_is_virtual:
                                        last_virtual_event_at = syn_times[-1]
                                elif ev_type != EV_SYN:
                                    if stream_is_virtual:
                                        last_virtual_event_at = time.monotonic()
                                    self._apply_event(
                                        ev_type,
                                        ev_code,
                                        ev_val,
                                        stream_is_virtual,
                                        hm,
                                        dpad_bits,
                                        state,
                                    )
                                    dirty = True
                        except BlockingIOError:
                            pass
                        except OSError as exc:
                            if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                                if raw_fd == stream_fd:
                                    raw_fd = None
                                    raw_path = None
                                _safe_close(stream_fd)
                                stream_fd = None
                                stream_path = None
                                state.mode = "disconnected"
                                dirty = True
                else:
                    time.sleep(0.025)

                # Poll raw physical axes & key bits even while daemon holds EVIOCGRAB
                if raw_fd is not None:
                    lx_v = read_abs_value(raw_fd, hm.lx_code)
                    ly_v = read_abs_value(raw_fd, hm.ly_code)
                    rx_v = read_abs_value(raw_fd, hm.rx_code)
                    ry_v = read_abs_value(raw_fd, hm.ry_code)
                    if lx_v is None and ly_v is None and rx_v is None and ry_v is None:
                        if raw_fd != stream_fd:
                            _safe_close(raw_fd)
                        raw_fd = None
                        raw_path = None
                    else:
                        if lx_v is not None:
                            s_lx = scale_hw_to_i16(lx_v, *hm.lx_bounds)
                            if s_lx != state.raw_lx:
                                state.raw_lx = s_lx
                                dirty = True
                        if ly_v is not None:
                            s_ly = scale_hw_to_i16(ly_v, *hm.ly_bounds)
                            if s_ly != state.raw_ly:
                                state.raw_ly = s_ly
                                dirty = True
                        if rx_v is not None:
                            s_rx = scale_hw_to_i16(rx_v, *hm.rx_bounds)
                            if s_rx != state.raw_rx:
                                state.raw_rx = s_rx
                                dirty = True
                        if ry_v is not None:
                            s_ry = scale_hw_to_i16(ry_v, *hm.ry_bounds)
                            if s_ry != state.raw_ry:
                                state.raw_ry = s_ry
                                dirty = True

                        cur_pressed = read_raw_pressed_codes(raw_fd)
                        newly_pressed = cur_pressed - prev_raw_pressed
                        for code in sorted(newly_pressed):
                            if code >= 128:
                                self.raw_key_detected.emit(code)
                        prev_raw_pressed = cur_pressed

                        extra_codes = set(state.supported_extra_keys) | {
                            c
                            for c in cur_pressed
                            if c >= 128 and c not in BUTTON_CODE_MAP
                        }
                        new_extras = {c: (c in cur_pressed) for c in sorted(extra_codes)}
                        if new_extras != state.extra_buttons:
                            state.extra_buttons = new_extras
                            dirty = True

                        virtual_stalled = (
                            stream_is_virtual
                            and (now - last_virtual_event_at > 0.25)
                        )
                        if state.mode == "direct" or virtual_stalled:
                            btn_map = (
                                GENERIC_JOY_BUTTON_MAP
                                if hm.use_generic_joy_btns
                                else BUTTON_CODE_MAP
                            )
                            raw_btns = empty_buttons()
                            for code, bname in btn_map.items():
                                raw_btns[bname] = code in cur_pressed
                            if raw_btns != state.buttons:
                                state.buttons = raw_btns
                                dirty = True

                            lt_v = read_abs_value(raw_fd, hm.lt_code)
                            rt_v = read_abs_value(raw_fd, hm.rt_code)
                            new_lt = (
                                (1023 if hm.dig_lt_code in cur_pressed else 0)
                                if hm.dig_lt_code >= 0
                                else (
                                    scale_trig_to_1023(lt_v, *hm.lt_bounds)
                                    if lt_v is not None
                                    else state.lt
                                )
                            )
                            new_rt = (
                                (1023 if hm.dig_rt_code in cur_pressed else 0)
                                if hm.dig_rt_code >= 0
                                else (
                                    scale_trig_to_1023(rt_v, *hm.rt_bounds)
                                    if rt_v is not None
                                    else state.rt
                                )
                            )
                            if new_lt != state.lt or new_rt != state.rt:
                                state.lt, state.rt = new_lt, new_rt
                                dirty = True

                            if hm.use_dpad_btns:
                                new_hx = (1 if BTN_DPAD_RIGHT in cur_pressed else 0) - (
                                    1 if BTN_DPAD_LEFT in cur_pressed else 0
                                )
                                new_hy = (1 if BTN_DPAD_DOWN in cur_pressed else 0) - (
                                    1 if BTN_DPAD_UP in cur_pressed else 0
                                )
                            else:
                                hx_v = read_abs_value(raw_fd, ABS_HAT0X)
                                hy_v = read_abs_value(raw_fd, ABS_HAT0Y)
                                new_hx = (
                                    (-1 if hx_v < 0 else (1 if hx_v > 0 else 0))
                                    if hx_v is not None
                                    else state.hat_x
                                )
                                new_hy = (
                                    (-1 if hy_v < 0 else (1 if hy_v > 0 else 0))
                                    if hy_v is not None
                                    else state.hat_y
                                )
                            if new_hx != state.hat_x or new_hy != state.hat_y:
                                state.hat_x, state.hat_y = new_hx, new_hy
                                dirty = True

                # In direct mode (or if virtual device hasn't emitted yet), compute output preview
                if state.mode == "direct" or (
                    stream_is_virtual
                    and raw_fd is not None
                    and (now - last_virtual_event_at > 0.25)
                ):
                    with self._lock:
                        cfg = self._config
                    ox_l, oy_l = compute_corrected_stick(
                        state.raw_lx,
                        state.raw_ly,
                        cfg.center_lx,
                        cfg.center_ly,
                        cfg.dz_l,
                        cfg.response_curve,
                        cfg.dz_mode,
                        cfg.outer_comp_l,
                    )
                    ox_r, oy_r = compute_corrected_stick(
                        state.raw_rx,
                        state.raw_ry,
                        cfg.center_rx,
                        cfg.center_ry,
                        cfg.dz_r,
                        cfg.response_curve,
                        cfg.dz_mode,
                        cfg.outer_comp_r,
                    )
                    if (
                        ox_l != state.out_lx
                        or oy_l != state.out_ly
                        or ox_r != state.out_rx
                        or oy_r != state.out_ry
                    ):
                        state.out_lx, state.out_ly = ox_l, oy_l
                        state.out_rx, state.out_ry = ox_r, oy_r
                        dirty = True

                self._step_calibration(state, has_raw_source=(raw_fd is not None))

                now_emit = time.monotonic()
                while syn_times and syn_times[0] < now_emit - 1.0:
                    syn_times.popleft()
                state.poll_hz = float(len(syn_times))
                if dirty or (now_emit - last_emit >= 0.05):
                    last_emit = now_emit
                    snapshot = ControllerState(
                        mode=state.mode,
                        physical_path=state.physical_path,
                        physical_name=state.physical_name,
                        physical_vid_pid=state.physical_vid_pid,
                        virtual_path=state.virtual_path,
                        virtual_name=state.virtual_name,
                        error_message=state.error_message,
                        raw_lx=state.raw_lx,
                        raw_ly=state.raw_ly,
                        raw_rx=state.raw_rx,
                        raw_ry=state.raw_ry,
                        out_lx=state.out_lx,
                        out_ly=state.out_ly,
                        out_rx=state.out_rx,
                        out_ry=state.out_ry,
                        lt=state.lt,
                        rt=state.rt,
                        hat_x=state.hat_x,
                        hat_y=state.hat_y,
                        poll_hz=state.poll_hz,
                        buttons=dict(state.buttons),
                        extra_buttons=dict(state.extra_buttons),
                        supported_extra_keys=list(state.supported_extra_keys),
                    )
                    self.state_updated.emit(snapshot)
        finally:
            if raw_fd is not None and raw_fd != stream_fd:
                _safe_close(raw_fd)
            _safe_close(stream_fd)
