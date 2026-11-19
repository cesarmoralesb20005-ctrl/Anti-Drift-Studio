"""Desktop notifications with clickable actions and per-clip deep linking.

Uses `org.freedesktop.Notifications` over Session D-Bus so both Wayland (KDE Plasma,
GNOME, etc.) and X11 show an action button ("Ver en Studio") and handle clicking the
notification body ("default") to bring Anti-Drift Studio forward with the exact clip
from that notification selected.
"""

from __future__ import annotations

from collections import OrderedDict
import os
import queue
import select
import threading
from typing import Any

from PySide6.QtCore import QObject, Signal


class DesktopClipNotifier(QObject):
    """Persistent freedesktop notification sender + action listener."""

    open_clip_requested = Signal(str, str)  # clip_path, xdg_activation_token

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stop_requested = False
        self._thread: threading.Thread | None = None
        self._pending: queue.Queue[tuple[str, str, str, int, threading.Event, list[int | None]]] = (
            queue.Queue()
        )
        self._notif_clips: OrderedDict[int, str] = OrderedDict()
        self._tokens: dict[int, str] = {}
        self._wake_r: int | None = None
        self._wake_w: int | None = None
        self._ready = threading.Event()
        self._dbus_ok = False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_requested = False
        self._ready.clear()
        try:
            self._wake_r, self._wake_w = os.pipe()
            os.set_blocking(self._wake_r, False)
        except OSError:
            self._wake_r, self._wake_w = None, None
        self._thread = threading.Thread(target=self._run, name="clip-notifier", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_requested = True
        self._wake()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None
        for fd_attr in ("_wake_r", "_wake_w"):
            fd = getattr(self, fd_attr)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, fd_attr, None)

    def _wake(self) -> None:
        if self._wake_w is not None:
            try:
                os.write(self._wake_w, b"\x01")
            except OSError:
                pass

    def _drain_wake(self) -> None:
        if self._wake_r is None:
            return
        try:
            while os.read(self._wake_r, 64):
                pass
        except OSError:
            pass

    def notify(
        self,
        title: str,
        body: str,
        clip_path: str = "",
        timeout_ms: int = 5000,
    ) -> int | None:
        """Send a desktop notification; returns notification ID or None on fallback."""
        if self._thread is None or not self._thread.is_alive():
            self.start()
        self._ready.wait(timeout=0.5)
        if not self._dbus_ok:
            return None
        done = threading.Event()
        result_box: list[int | None] = [None]
        self._pending.put((title, body, clip_path, timeout_ms, done, result_box))
        self._wake()
        done.wait(timeout=0.8)
        return result_box[0]

    def _record_notification(self, notif_id: int, clip_path: str) -> None:
        self._notif_clips[notif_id] = clip_path
        while len(self._notif_clips) > 50:
            old_id, _ = self._notif_clips.popitem(last=False)
            self._tokens.pop(old_id, None)

    def _handle_incoming(self, msg: Any) -> None:
        from jeepney import HeaderFields, MessageType

        if msg.header.message_type != MessageType.signal:
            return
        iface = msg.header.fields.get(HeaderFields.interface)
        if iface != "org.freedesktop.Notifications":
            return
        member = msg.header.fields.get(HeaderFields.member)
        if member == "ActivationToken" and len(msg.body) >= 2:
            nid, token = int(msg.body[0]), str(msg.body[1])
            if nid in self._notif_clips and token:
                self._tokens[nid] = token
        elif member == "ActionInvoked" and len(msg.body) >= 2:
            nid, action_key = int(msg.body[0]), str(msg.body[1])
            if nid in self._notif_clips and action_key in ("default", "open_clip", "open_app"):
                clip_path = self._notif_clips[nid]
                token = self._tokens.pop(nid, "")
                self.open_clip_requested.emit(clip_path, token)

    def _build_notify_call(self, addr: Any, title: str, body: str, clip_path: str, timeout_ms: int):
        from jeepney import new_method_call

        actions = (
            ["default", "Abrir en Studio", "open_clip", "Ver en Studio"]
            if clip_path
            else ["default", "Abrir Studio", "open_app", "Abrir Studio"]
        )
        hints = {
            "desktop-entry": ("s", "xbox-antidrift-gui"),
            "x-kde-display-appname": ("s", "Anti-Drift Studio"),
        }
        return new_method_call(
            addr,
            "Notify",
            "susssasa{sv}i",
            (
                "Anti-Drift Studio",
                0,
                "input-gaming",
                title,
                body,
                actions,
                hints,
                int(timeout_ms),
            ),
        )

    def _run(self) -> None:
        try:
            from jeepney import DBusAddress, MatchRule, new_method_call
            from jeepney.io.blocking import open_dbus_connection
        except ImportError:
            self._dbus_ok = False
            self._ready.set()
            return

        try:
            conn = open_dbus_connection(bus="SESSION")
        except Exception:
            self._dbus_ok = False
            self._ready.set()
            return

        notif_addr = DBusAddress(
            "/org/freedesktop/Notifications",
            bus_name="org.freedesktop.Notifications",
            interface="org.freedesktop.Notifications",
        )
        bus_addr = DBusAddress(
            "/org/freedesktop/DBus",
            bus_name="org.freedesktop.DBus",
            interface="org.freedesktop.DBus",
        )

        try:
            has_rep = conn.send_and_get_reply(
                new_method_call(bus_addr, "NameHasOwner", "s", ("org.freedesktop.Notifications",))
            )
            if not has_rep.body or not has_rep.body[0]:
                self._dbus_ok = False
                self._ready.set()
                return

            for member in ("ActionInvoked", "ActivationToken"):
                rule = MatchRule(
                    type="signal",
                    interface="org.freedesktop.Notifications",
                    member=member,
                    path="/org/freedesktop/Notifications",
                )
                conn.send_and_get_reply(new_method_call(bus_addr, "AddMatch", "s", (rule.serialise(),)))

            self._dbus_ok = True
            self._ready.set()

            while not self._stop_requested:
                self._drain_wake()
                while True:
                    try:
                        title, body, clip_path, timeout_ms, done, res_box = self._pending.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        rep = conn.send_and_get_reply(
                            self._build_notify_call(notif_addr, title, body, clip_path, timeout_ms)
                        )
                        nid = int(rep.body[0]) if rep.body else None
                        if nid is not None:
                            self._record_notification(nid, clip_path)
                        res_box[0] = nid
                    except Exception:
                        res_box[0] = None
                    finally:
                        done.set()

                # Drain any already-buffered D-Bus messages
                while True:
                    try:
                        msg = conn.receive(timeout=0)
                    except TimeoutError:
                        break
                    self._handle_incoming(msg)

                if self._stop_requested:
                    break

                # Wait without busy polling when socket is accessible
                sock = getattr(conn, "sock", None)
                if sock is not None and self._wake_r is not None:
                    try:
                        select.select([sock, self._wake_r], [], [], 0.5)
                        continue
                    except Exception:
                        pass
                try:
                    msg = conn.receive(timeout=0.08)
                    self._handle_incoming(msg)
                except TimeoutError:
                    continue
        except Exception:
            self._dbus_ok = False
            self._ready.set()
        finally:
            while True:
                try:
                    _, _, _, _, done, _ = self._pending.get_nowait()
                    done.set()
                except queue.Empty:
                    break
            try:
                conn.close()
            except Exception:
                pass
