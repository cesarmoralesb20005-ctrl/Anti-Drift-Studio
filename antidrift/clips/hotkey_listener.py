"""Universal global hotkey & external trigger listener (Portal / evdev / X11 / IPC)."""

from __future__ import annotations

import ctypes
import fcntl
import glob
import os
import select
import socket
import struct
import threading
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .clips_config import DEFAULT_SAFE_HOTKEY

CLIP_TRIGGER_SOCKET = Path.home() / ".cache" / "antidrift-studio" / "clip_trigger.sock"
INPUT_EVENT_STRUCT = struct.Struct("llHHi")

# Subset of standard Linux input-event-codes for global hotkeys
EVDEV_KEY_MAP: dict[str, int] = {
    "ESC": 1,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "Q": 16, "W": 17, "E": 18, "R": 19, "T": 20, "Y": 21, "U": 22, "I": 23, "O": 24, "P": 25,
    "A": 30, "S": 31, "D": 32, "F": 33, "G": 34, "H": 35, "J": 36, "K": 37, "L": 38,
    "Z": 44, "X": 45, "C": 46, "V": 47, "B": 48, "N": 49, "M": 50,
    "SPACE": 57,
    "F1": 59, "F2": 60, "F3": 61, "F4": 62, "F5": 63, "F6": 64,
    "F7": 65, "F8": 66, "F9": 67, "F10": 68, "F11": 87, "F12": 88,
    "HOME": 102, "UP": 103, "PGUP": 104, "LEFT": 105, "RIGHT": 106,
    "END": 107, "DOWN": 108, "PGDOWN": 109, "INS": 110, "DEL": 111,
    "PAUSE": 119,
}

EVDEV_MOD_MAP: dict[str, tuple[int, ...]] = {
    "CTRL": (29, 97),
    "ALT": (56, 100),
    "SHIFT": (42, 54),
    "META": (125, 126),
}

_X11_SPECIAL_KEYSYMS: dict[str, str] = {
    "ESC": "Escape",
    "SPACE": "space",
    "PGUP": "Prior",
    "PGDOWN": "Next",
    "INS": "Insert",
    "DEL": "Delete",
    "HOME": "Home",
    "END": "End",
    "UP": "Up",
    "DOWN": "Down",
    "LEFT": "Left",
    "RIGHT": "Right",
    "PAUSE": "Pause",
}

_X11_MOD_KEYSYMS: dict[str, tuple[str, ...]] = {
    "CTRL": ("Control_L", "Control_R"),
    "ALT": ("Alt_L", "Alt_R"),
    "SHIFT": ("Shift_L", "Shift_R"),
    "META": ("Super_L", "Super_R"),
}


def parse_combo_x11_names(seq: str) -> tuple[list[tuple[str, ...]], str | None]:
    """Parse Qt shortcut string into X11 keysym names for modifiers and main key."""
    mods: list[tuple[str, ...]] = []
    main_sym: str | None = None
    for part in seq.split("+"):
        u = part.strip().upper()
        if not u:
            continue
        if u in ("CTRL", "CONTROL"):
            mods.append(_X11_MOD_KEYSYMS["CTRL"])
        elif u == "ALT":
            mods.append(_X11_MOD_KEYSYMS["ALT"])
        elif u == "SHIFT":
            mods.append(_X11_MOD_KEYSYMS["SHIFT"])
        elif u in ("META", "SUPER", "WIN"):
            mods.append(_X11_MOD_KEYSYMS["META"])
        elif u in _X11_SPECIAL_KEYSYMS:
            main_sym = _X11_SPECIAL_KEYSYMS[u]
        elif u.startswith("F") and u[1:].isdigit():
            main_sym = u
        elif len(u) == 1 and u.isalnum():
            main_sym = u.lower()
    return mods, main_sym


def _x11_keycode_pressed(keymap_bytes: bytes, keycode: int) -> bool:
    """Check whether X11 keycode bit is set in 32-byte XQueryKeymap bitmask."""
    if keycode <= 0 or keycode >= 256 or len(keymap_bytes) < 32:
        return False
    return bool(keymap_bytes[keycode >> 3] & (1 << (keycode & 7)))


def qt_seq_to_xdg_trigger(seq: str) -> str:
    """Convert Qt QKeySequence string (e.g. 'Ctrl+Alt+F10') to XDG GlobalShortcuts."""
    parts = [p.strip() for p in seq.split("+") if p.strip()]
    mapped: list[str] = []
    for p in parts:
        u = p.upper()
        if u in ("CTRL", "CONTROL"):
            mapped.append("CTRL")
        elif u == "ALT":
            mapped.append("ALT")
        elif u == "SHIFT":
            mapped.append("SHIFT")
        elif u in ("META", "SUPER", "WIN"):
            mapped.append("LOGO")
        else:
            mapped.append(u)
    return "+".join(mapped) or "ALT+SHIFT+C"


def parse_combo_evdev(seq: str) -> tuple[list[tuple[int, ...]], int | None]:
    """Parse Qt shortcut string into (required_modifier_code_tuples, main_keycode)."""
    mods: list[tuple[int, ...]] = []
    main_key: int | None = None
    for part in seq.split("+"):
        u = part.strip().upper()
        if not u:
            continue
        if u in ("CTRL", "CONTROL"):
            mods.append(EVDEV_MOD_MAP["CTRL"])
        elif u == "ALT":
            mods.append(EVDEV_MOD_MAP["ALT"])
        elif u == "SHIFT":
            mods.append(EVDEV_MOD_MAP["SHIFT"])
        elif u in ("META", "SUPER", "WIN"):
            mods.append(EVDEV_MOD_MAP["META"])
        elif u in EVDEV_KEY_MAP:
            main_key = EVDEV_KEY_MAP[u]
    return mods, main_key


class GlobalHotkeyThread(QThread):
    """Unified background listener: Unix IPC trigger + Portal/evdev/X11 hotkeys."""

    hotkey_triggered = Signal(str, int)  # source_label, optional_seconds (0 = default)
    backend_status = Signal(str)

    def __init__(self, combo: str = DEFAULT_SAFE_HOTKEY, parent=None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._combo = combo
        self._stop_requested = False
        self._rebind_requested = False
        self._portal_bind_requested = False

    def set_combo(self, combo: str, bind_portal: bool = False) -> None:
        with self._lock:
            self._combo = combo
            self._rebind_requested = True
            if bind_portal:
                self._portal_bind_requested = True

    def request_portal_bind(self) -> None:
        with self._lock:
            self._portal_bind_requested = True

    def stop(self) -> None:
        self._stop_requested = True
        self.wait(1500)

    def _readable_keyboards(self) -> list[str]:
        nodes: set[str] = set()
        for link in sorted(glob.glob("/dev/input/by-id/*-kbd")):
            real = os.path.realpath(link)
            if os.access(real, os.R_OK):
                nodes.add(real)

        # Also check all /sys/class/input/event* for BT/2.4GHz/PS2 keyboards
        eviocgbit_key = 0x80604521  # EVIOCGBIT(EV_KEY, 96)
        for ev_sys in sorted(Path("/sys/class/input").glob("event*")):
            node = f"/dev/input/{ev_sys.name}"
            if node in nodes or not os.access(node, os.R_OK):
                continue
            try:
                fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            try:
                bits = bytearray(96)
                fcntl.ioctl(fd, eviocgbit_key, bits)
                has_wasd = all(
                    bool(bits[c >> 3] & (1 << (c & 7)))
                    for c in (17, 30, 31, 32, 57)  # W, A, S, D, SPACE
                )
                has_south = bool(bits[304 >> 3] & (1 << (304 & 7)))
                if has_wasd and not has_south:
                    nodes.add(node)
            except OSError:
                pass
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass
        return sorted(nodes)

    def _run_ipc_server(self) -> None:
        CLIP_TRIGGER_SOCKET.parent.mkdir(parents=True, exist_ok=True)
        if CLIP_TRIGGER_SOCKET.exists():
            try:
                CLIP_TRIGGER_SOCKET.unlink()
            except OSError:
                pass
        try:
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(str(CLIP_TRIGGER_SOCKET))
            srv.listen(4)
            srv.settimeout(0.3)
        except OSError:
            return

        try:
            while not self._stop_requested:
                try:
                    conn, _ = srv.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                with conn:
                    conn.settimeout(0.5)
                    try:
                        msg = conn.recv(256).decode("utf-8", errors="replace").strip()
                    except OSError:
                        msg = ""
                    secs = 0
                    for tok in msg.split():
                        if tok.isdigit():
                            secs = int(tok)
                    self.hotkey_triggered.emit("Atajo externo / CLI", secs)
                    try:
                        conn.sendall(b"ok\n")
                    except OSError:
                        pass
        finally:
            srv.close()
            try:
                CLIP_TRIGGER_SOCKET.unlink()
            except OSError:
                pass

    def _run_evdev_loop(self, kbd_paths: list[str]) -> None:
        fds: list[int] = []
        for p in kbd_paths:
            try:
                fds.append(os.open(p, os.O_RDONLY | os.O_NONBLOCK))
            except OSError:
                pass
        if not fds:
            return

        pressed: set[int] = set()
        last_fire = 0.0
        self.backend_status.emit(f"Atajo global activo (evdev · {len(fds)} teclado/s)")
        try:
            while not self._stop_requested:
                with self._lock:
                    combo = self._combo
                mods, main_key = parse_combo_evdev(combo)

                rlist, _, _ = select.select(fds, [], [], 0.2)
                for fd in rlist:
                    try:
                        chunk = os.read(fd, INPUT_EVENT_STRUCT.size * 32)
                    except OSError:
                        continue
                    for off in range(
                        0,
                        len(chunk) - (len(chunk) % INPUT_EVENT_STRUCT.size),
                        INPUT_EVENT_STRUCT.size,
                    ):
                        _, _, ev_type, ev_code, ev_val = INPUT_EVENT_STRUCT.unpack_from(
                            chunk, off
                        )
                        if ev_type != 1:
                            continue
                        if ev_val in (1, 2):
                            pressed.add(ev_code)
                        elif ev_val == 0:
                            pressed.discard(ev_code)

                        if (
                            ev_val == 1
                            and main_key is not None
                            and ev_code == main_key
                            and all(any(m in pressed for m in grp) for grp in mods)
                        ):
                            now = time.monotonic()
                            if now - last_fire >= 1.2:
                                last_fire = now
                                self.hotkey_triggered.emit(f"Teclado ({combo})", 0)
        finally:
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def _run_portal_loop(self) -> None:
        try:
            from jeepney import DBusAddress, MatchRule, new_method_call
            from jeepney.io.blocking import open_dbus_connection
        except ImportError:
            self.backend_status.emit(
                "Atajo CLI/Portal listo (~/.local/bin/antidrift-save-clip)"
            )
            return

        try:
            conn = open_dbus_connection(bus="SESSION")
        except Exception:
            return

        try:
            reg = DBusAddress(
                "/org/freedesktop/portal/desktop",
                bus_name="org.freedesktop.portal.Desktop",
                interface="org.freedesktop.host.portal.Registry",
            )
            try:
                conn.send_and_get_reply(
                    new_method_call(reg, "Register", "sa{sv}", ("xbox-antidrift-gui", {}))
                )
            except Exception:
                pass

            gs = DBusAddress(
                "/org/freedesktop/portal/desktop",
                bus_name="org.freedesktop.portal.Desktop",
                interface="org.freedesktop.portal.GlobalShortcuts",
            )
            bus_iface = DBusAddress(
                "/org/freedesktop/DBus",
                bus_name="org.freedesktop.DBus",
                interface="org.freedesktop.DBus",
            )
            unique = conn.unique_name.lstrip(":").replace(".", "_")
            tok = f"ad_s_{os.getpid()}"
            req_path = f"/org/freedesktop/portal/desktop/request/{unique}/{tok}"
            rule_resp = MatchRule(
                type="signal",
                interface="org.freedesktop.portal.Request",
                member="Response",
                path=req_path,
            )
            conn.send_and_get_reply(
                new_method_call(bus_iface, "AddMatch", "s", (rule_resp.serialise(),))
            )
            conn.send_and_get_reply(
                new_method_call(
                    gs,
                    "CreateSession",
                    "a{sv}",
                    (
                        {
                            "handle_token": ("s", tok),
                            "session_handle_token": ("s", f"ads_{os.getpid()}"),
                        },
                    ),
                )
            )
            with conn.filter(rule_resp) as q:
                msg = conn.recv_until_filtered(q, timeout=3.0)
            code, res = msg.body
            if code != 0 or "session_handle" not in res:
                self.backend_status.emit(
                    "Atajo externo listo (~/.local/bin/antidrift-save-clip)"
                )
                return
            session_handle = res["session_handle"][1]

            act_rule = MatchRule(
                type="signal",
                interface="org.freedesktop.portal.GlobalShortcuts",
                member="Activated",
                path="/org/freedesktop/portal/desktop",
            )
            conn.send_and_get_reply(
                new_method_call(bus_iface, "AddMatch", "s", (act_rule.serialise(),))
            )
            self.backend_status.emit(
                "Atajo listo en la app · En Wayland pulsa 'Registrar global' o usa ~/.local/bin/antidrift-save-clip"
            )

            def _bind(combo_str: str) -> None:
                btok = f"ad_b_{int(time.monotonic() * 1000) % 100000}"
                breq = f"/org/freedesktop/portal/desktop/request/{unique}/{btok}"
                brule = MatchRule(
                    type="signal",
                    interface="org.freedesktop.portal.Request",
                    member="Response",
                    path=breq,
                )
                conn.send_and_get_reply(
                    new_method_call(bus_iface, "AddMatch", "s", (brule.serialise(),))
                )
                xdg_trig = qt_seq_to_xdg_trigger(combo_str)
                with conn.filter(brule) as q_bind:
                    conn.send_and_get_reply(
                        new_method_call(
                            gs,
                            "BindShortcuts",
                            "oa(sa{sv})sa{sv}",
                            (
                                session_handle,
                                [
                                    (
                                        "save_clip",
                                        {
                                            "description": ("s", "Guardar clip instantáneo"),
                                            "preferred_trigger": ("s", xdg_trig),
                                        },
                                    )
                                ],
                                "",
                                {"handle_token": ("s", btok)},
                            ),
                        )
                    )
                    try:
                        resp = conn.recv_until_filtered(q_bind, timeout=20.0)
                        b_code, b_res = resp.body
                        if b_code == 0:
                            self.backend_status.emit(
                                f"Atajo global activo en Wayland ({combo_str})"
                            )
                        else:
                            self.backend_status.emit(
                                "Registro cancelado en KDE/Wayland. Vuelve a pulsar 'Registrar global' o usa ~/.local/bin/antidrift-save-clip"
                            )
                    except TimeoutError:
                        self.backend_status.emit(
                            "Tiempo de espera agotado al registrar atajo en Wayland."
                        )

            with conn.filter(act_rule) as q_act:
                while not self._stop_requested:
                    with self._lock:
                        do_bind = self._portal_bind_requested
                        combo_str = self._combo
                        if do_bind:
                            self._portal_bind_requested = False
                    if do_bind:
                        try:
                            _bind(combo_str)
                        except Exception:
                            self.backend_status.emit(
                                "No se pudo vincular atajo en Portal (usa ~/.local/bin/antidrift-save-clip)"
                            )
                    try:
                        amsg = conn.recv_until_filtered(q_act, timeout=0.25)
                        _, shortcut_id, _, _ = amsg.body
                        if shortcut_id == "save_clip":
                            self.hotkey_triggered.emit(f"Atajo global ({combo_str})", 0)
                    except TimeoutError:
                        continue
        except Exception:
            self.backend_status.emit(
                "Atajo externo listo (~/.local/bin/antidrift-save-clip)"
            )
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def run(self) -> None:
        ipc_t = threading.Thread(target=self._run_ipc_server, daemon=True)
        ipc_t.start()

        kbd_paths = self._readable_keyboards()
        if kbd_paths:
            self._run_evdev_loop(kbd_paths)
            return

        session = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
        is_x11 = session == "x11" or (not session and not os.environ.get("WAYLAND_DISPLAY"))
        if is_x11 and self._run_x11_loop():
            return

        self._run_portal_loop()
        while not self._stop_requested:
            time.sleep(0.25)

    def _run_x11_loop(self) -> bool:
        """Poll X11 global keymap via libX11 XQueryKeymap (works inside fullscreen X11 games)."""
        try:
            x11 = ctypes.CDLL("libX11.so.6")
        except OSError:
            return False
        display_name = os.environ.get("DISPLAY")
        if not display_name:
            return False

        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
        x11.XStringToKeysym.restype = ctypes.c_ulong
        x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        x11.XQueryKeymap.argtypes = [ctypes.c_void_p, ctypes.c_char * 32]
        x11.XQueryKeymap.restype = ctypes.c_int

        dpy = x11.XOpenDisplay(display_name.encode("utf-8", errors="ignore"))
        if not dpy:
            return False

        def _resolve(combo_str: str) -> tuple[list[tuple[int, ...]], int]:
            mod_names, main_name = parse_combo_x11_names(combo_str)
            mod_kcs: list[tuple[int, ...]] = []
            for grp in mod_names:
                kcs = tuple(
                    int(x11.XKeysymToKeycode(dpy, x11.XStringToKeysym(sym.encode())))
                    for sym in grp
                )
                kcs = tuple(kc for kc in kcs if kc > 0)
                if kcs:
                    mod_kcs.append(kcs)
            main_kc = (
                int(x11.XKeysymToKeycode(dpy, x11.XStringToKeysym(main_name.encode())))
                if main_name
                else 0
            )
            return mod_kcs, main_kc

        keymap_buf = (ctypes.c_char * 32)()
        last_combo = ""
        mod_codes: list[tuple[int, ...]] = []
        main_code = 0
        was_main_down = False
        last_fire = 0.0

        with self._lock:
            init_combo = self._combo
        self.backend_status.emit(
            f"Atajo global X11 activo ({init_combo} · funciona dentro de juegos)"
        )

        try:
            while not self._stop_requested:
                with self._lock:
                    combo = self._combo
                if combo != last_combo:
                    last_combo = combo
                    mod_codes, main_code = _resolve(combo)
                    was_main_down = False

                if main_code > 0:
                    x11.XQueryKeymap(dpy, keymap_buf)
                    raw_bytes = bytes(keymap_buf)
                    is_down = _x11_keycode_pressed(raw_bytes, main_code)
                    if is_down and not was_main_down:
                        mods_ok = all(
                            any(_x11_keycode_pressed(raw_bytes, mkc) for mkc in grp)
                            for grp in mod_codes
                        )
                        now = time.monotonic()
                        if mods_ok and (now - last_fire >= 1.2):
                            last_fire = now
                            self.hotkey_triggered.emit(f"Teclado X11 ({combo})", 0)
                    was_main_down = is_down

                time.sleep(0.025)
        finally:
            x11.XCloseDisplay(ctypes.c_void_p(dpy))
        return True
