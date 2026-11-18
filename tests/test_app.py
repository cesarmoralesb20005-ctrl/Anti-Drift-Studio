"""Automated unit and offscreen PySide6 UI tests for Anti-Drift Studio."""

from __future__ import annotations

import math
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from antidrift.clips.clips_config import (
    ClipsConfig,
    load_clips_config,
    save_clips_config,
)
from antidrift.clips.hotkey_listener import (
    parse_combo_evdev,
    qt_seq_to_xdg_trigger,
)
from antidrift.clips.recorder import (
    GsrBackend,
    build_gsr_args,
    detect_gsr_backend,
    resolve_capture_target,
)
from antidrift.clips.voice_trigger import (
    find_vosk_model_dir,
    match_wake_phrase,
    parse_wake_phrases,
    validate_phrase_words,
    vosk_is_available,
)
from antidrift.config import (
    DEFAULT_VIRTUAL_NAME,
    AntiDriftConfig,
    apply_axial_deadzone,
    apply_hair_trigger,
    compute_corrected_stick,
    deadzone_raw_bounds,
    load_config,
    load_device_config,
    normalize_axis,
    sanitize_virtual_name,
    save_config,
    save_device_config,
    scale_hw_to_i16,
    scale_trig_to_1023,
)
from antidrift.evdev_reader import ControllerState
from antidrift.macros import (
    MacroDef,
    MacroStep,
    create_preset_macro,
    load_macros,
    save_macros,
)
from antidrift.main_window import MainWindow
from antidrift.profiles import (
    detect_matching_profile,
    load_profile_store,
    save_profile_store,
)
from antidrift.service_ctrl import (
    check_sdl_ignore_env,
    check_uinput_access,
    get_service_status,
)
from antidrift.widgets.circularity_dialog import CircularityTestDialog


def _get_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class ConfigAndUniversalMathTests(unittest.TestCase):
    def test_default_neutral_config_and_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nonexistent.conf"
            defaults = load_config(missing)
            self.assertEqual((defaults.center_lx, defaults.center_ly), (0, 0))
            self.assertEqual((defaults.center_rx, defaults.center_ry), (0, 0))

            path = Path(tmp) / "xbox-antidrift.conf"
            cfg = AntiDriftConfig(
                center_lx=1700,
                center_ly=250,
                dz_l=0.15,
                center_rx=3000,
                center_ry=-10350,
                dz_r=0.18,
                dz_mode="hybrid",
                outer_comp_l=0.12,
                outer_comp_r=0.08,
                low_latency=True,
                response_curve=0.78,
                hair_trigger=True,
                debounce_ms=15,
                rumble_gain=65,
                swap_nintendo=True,
                multi_controller=False,
                device_path="/dev/input/event12",
                virtual_name="Mando de Cesar",
            )
            save_config(cfg, path)
            loaded = load_config(path)
            self.assertEqual(loaded, cfg)

    def test_virtual_name_and_multipad_device_configs(self) -> None:
        self.assertEqual(sanitize_virtual_name("   "), DEFAULT_VIRTUAL_NAME)
        self.assertEqual(sanitize_virtual_name("Mando   de\nCesar"), "Mando de Cesar")
        self.assertLessEqual(len(sanitize_virtual_name("á" * 60).encode("utf-8")), 79)

        with tempfile.TemporaryDirectory() as tmp:
            dev_conf = Path(tmp) / "devices.conf"
            base = AntiDriftConfig(virtual_name="Global Pad", dz_l=0.14, multi_controller=True)

            pad1_cfg = AntiDriftConfig(
                center_lx=-1200,
                dz_l=0.16,
                virtual_name="Mando de Cesar",
                swap_nintendo=False,
            )
            pad2_cfg = AntiDriftConfig(
                center_rx=2400,
                dz_r=0.22,
                virtual_name="Mando Switch Ana",
                swap_nintendo=True,
            )
            save_device_config("045e:02d1|usb-1", pad1_cfg, label="Xbox Pad", path=dev_conf)
            save_device_config("057e:2009|uniq:aa:bb", pad2_cfg, label="Pro Pad", path=dev_conf)

            r1 = load_device_config("045e:02d1|usb-1", base, path=dev_conf)
            r2 = load_device_config("057e:2009|uniq:aa:bb", base, path=dev_conf)
            r_unknown = load_device_config("1234:5678|usb-9", base, path=dev_conf)

            self.assertEqual(r1.virtual_name, "Mando de Cesar")
            self.assertEqual(r1.center_lx, -1200)
            self.assertEqual(r2.virtual_name, "Mando Switch Ana")
            self.assertEqual(r2.center_rx, 2400)
            self.assertTrue(r2.swap_nintendo)
            self.assertEqual(r_unknown.virtual_name, "Global Pad")

    def test_daemon_c_compiles_cleanly(self) -> None:
        import shutil
        import subprocess

        gcc = shutil.which("gcc")
        if gcc is None:
            self.skipTest("gcc not installed")
        daemon_c = Path(__file__).resolve().parent.parent / "daemon" / "xbox-antidrift.c"
        with tempfile.TemporaryDirectory() as tmp:
            out_bin = Path(tmp) / "xbox-antidrift"
            res = subprocess.run(
                [gcc, "-O2", "-Wall", "-Wextra", "-Werror", str(daemon_c), "-o", str(out_bin), "-lm"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertTrue(out_bin.exists())

    def test_bluetooth_uhid_not_virtual(self) -> None:
        from antidrift.evdev_reader import _is_virtual_sys_device

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Simulated BlueZ uhid Bluetooth Joy-Con: /virtual/misc/uhid/... must NOT be treated as virtual uinput
            bt_real = root / "devices" / "virtual" / "misc" / "uhid" / "0005:057E:2006.0007" / "input" / "input50"
            bt_real.mkdir(parents=True)
            (bt_real / "uevent").write_text('PHYS="00:1a:7d:da:71:11"\n', encoding="utf-8")
            (bt_real / "phys").write_text("00:1a:7d:da:71:11\n", encoding="utf-8")
            ev_bt = root / "class_input" / "event257"
            ev_bt.mkdir(parents=True)
            (ev_bt / "device").symlink_to(bt_real)
            self.assertFalse(_is_virtual_sys_device(ev_bt, "Joy-Con (L)", "00:1a:7d:da:71:11"))

            # Simulated uinput virtual pad: /virtual/input/... MUST be treated as virtual
            virt_real = root / "devices" / "virtual" / "input" / "input60"
            virt_real.mkdir(parents=True)
            (virt_real / "uevent").write_text('PHYS="antidrift:event257"\n', encoding="utf-8")
            ev_virt = root / "class_input" / "event258"
            ev_virt.mkdir(parents=True)
            (ev_virt / "device").symlink_to(virt_real)
            self.assertTrue(_is_virtual_sys_device(ev_virt, "Custom Joy-Con", "antidrift:event257"))

    def test_universal_axis_scaling(self) -> None:
        self.assertEqual(scale_hw_to_i16(0, 0, 255), -32768)
        self.assertEqual(scale_hw_to_i16(255, 0, 255), 32767)
        self.assertLess(abs(scale_hw_to_i16(128, 0, 255)), 260)
        self.assertEqual(scale_hw_to_i16(-12345, -32768, 32767), -12345)
        self.assertEqual(scale_trig_to_1023(0, 0, 255), 0)
        self.assertEqual(scale_trig_to_1023(255, 0, 255), 1023)

    def test_deadzone_shapes_and_outer_compensation(self) -> None:
        # Axial deadzone
        ox, oy = compute_corrected_stick(3000, -10350, 3000, -10350, 0.18, dz_mode="axial")
        self.assertEqual((ox, oy), (0, 0))

        # Radial deadzone zeros diagonal inside radius 0.20
        rx, ry = compute_corrected_stick(4000, 4000, 0, 0, 0.20, dz_mode="radial")
        self.assertEqual((rx, ry), (0, 0))

        # Hybrid deadzone snaps small off-axis tilt onto axis
        hx, hy = compute_corrected_stick(16000, 1200, 0, 0, 0.20, dz_mode="hybrid")
        self.assertGreater(hx, 8000)
        self.assertEqual(hy, 0)

        # Anti-Slowturn outer compensation: 85% physical throw reaches full 32767 when outer_comp=0.15
        ox_comp, _ = compute_corrected_stick(
            round(0.85 * 32767), 0, 0, 0, 0.12, dz_mode="axial", outer_comp=0.15
        )
        self.assertEqual(ox_comp, 32767)

        # Response curve & hair-trigger
        self.assertGreater(
            apply_axial_deadzone(0.45, 0.12, curve=0.78),
            apply_axial_deadzone(0.45, 0.12, curve=1.0),
        )
        self.assertEqual(apply_hair_trigger(50, True), 0)
        self.assertEqual(apply_hair_trigger(150, True), 1023)
        self.assertEqual(normalize_axis(32767, 3000), 1.0)

    def test_deadzone_raw_bounds(self) -> None:
        lo, hi = deadzone_raw_bounds(0, 0.20)
        self.assertLess(lo, -6000)
        self.assertGreater(hi, 6000)

    def test_macros_and_profiles_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mpath = Path(tmp) / "xbox-antidrift-macros.conf"
            m1 = create_preset_macro("rl_half_flip", trigger_code=704)
            m2 = MacroDef(
                id="m2",
                name="Extra Share Turbo",
                enabled=False,
                trigger_code=167,
                mode="hold_repeat",
                intercept=True,
                repeat_ms=40,
                steps=[
                    MacroStep("KEY", "B", 1),
                    MacroStep("WAIT", v1=20),
                    MacroStep("KEY", "B", 0),
                ],
            )
            save_macros([m1, m2], mpath)
            self.assertEqual(load_macros(mpath), [m1, m2])

            ppath = Path(tmp) / "xbox-antidrift-profiles.json"
            store = load_profile_store(ppath)
            store.auto_switch = True
            store.minimize_to_tray = True
            save_profile_store(store, ppath)
            reloaded = load_profile_store(ppath)
            self.assertTrue(reloaded.auto_switch)
            self.assertTrue(reloaded.minimize_to_tray)

            matched = detect_matching_profile(
                reloaded.profiles, process_lines=["wine64 RocketLeague.exe"]
            )
            self.assertIsNotNone(matched)
            self.assertEqual(matched.id, "rocket_league")

            base = AntiDriftConfig(center_lx=1700, center_ry=-10350)
            applied = matched.apply_to_config(base)
            self.assertEqual(applied.center_lx, 1700)
            self.assertEqual(applied.center_ry, -10350)
            self.assertTrue(applied.low_latency)


class ServiceAndUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = _get_app()

    def test_service_uinput_and_sdl_queries(self) -> None:
        st = get_service_status()
        self.assertIsInstance(st.state, str)
        u_ok, _ = check_uinput_access()
        self.assertIsInstance(u_ok, bool)
        sdl_ok, sdl_msg = check_sdl_ignore_env()
        self.assertIsInstance(sdl_ok, bool)
        self.assertTrue(len(sdl_msg) > 0)

    def test_app_autostart_desktop_toggle(self) -> None:
        from antidrift.service_ctrl import is_app_autostart_enabled, set_app_autostart

        with tempfile.TemporaryDirectory() as tmp:
            desktop_path = Path(tmp) / "autostart" / "xbox-antidrift-gui.desktop"
            # Disabled by default when no .desktop entry exists
            self.assertFalse(is_app_autostart_enabled(desktop_path))

            ok, _msg = set_app_autostart(True, autostart_path=desktop_path)
            self.assertTrue(ok)
            self.assertTrue(desktop_path.exists())
            self.assertTrue(is_app_autostart_enabled(desktop_path))
            txt = desktop_path.read_text(encoding="utf-8")
            self.assertIn("[Desktop Entry]", txt)
            self.assertIn("X-GNOME-Autostart-enabled=true", txt)

            ok2, _msg2 = set_app_autostart(False, autostart_path=desktop_path)
            self.assertTrue(ok2)
            self.assertFalse(desktop_path.exists())
            self.assertFalse(is_app_autostart_enabled(desktop_path))

    def test_circularity_dialog_metrics(self) -> None:
        dlg = CircularityTestDialog(0, 0, 0, 0)
        # Sweep 36 sectors around 85% radius -> suggests ~15% outer comp
        for deg in range(5, 360, 10):
            rad = math.radians(deg)
            rx = round(math.cos(rad) * 0.85 * 32767)
            ry = round(math.sin(rad) * 0.85 * 32767)
            dlg.feed_state(ControllerState(mode="virtual", raw_lx=rx, raw_ly=ry))
        cov, _err, min_r, sug = dlg._l_canvas.metrics()
        self.assertEqual(cov, 36)
        self.assertLess(min_r, 88.0)
        self.assertGreater(sug, 0.10)
        dlg.close()

    def test_main_window_full_features_and_rendering(self) -> None:
        from antidrift.clips import clips_tab as clips_tab_mod

        orig_save = clips_tab_mod.save_clips_config
        orig_load = clips_tab_mod.load_clips_config
        orig_xdg = os.environ.get("XDG_CONFIG_HOME")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_clips_cfg = Path(tmp) / "clips_test.json"
            os.environ["XDG_CONFIG_HOME"] = str(Path(tmp) / "xdg")
            clips_tab_mod.save_clips_config = lambda cfg, path=tmp_clips_cfg: orig_save(cfg, tmp_clips_cfg)
            clips_tab_mod.load_clips_config = lambda path=tmp_clips_cfg: orig_load(tmp_clips_cfg)
            try:
                self._run_main_window_test_body()
            finally:
                clips_tab_mod.save_clips_config = orig_save
                clips_tab_mod.load_clips_config = orig_load
                if orig_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = orig_xdg

    def _run_main_window_test_body(self) -> None:
        win = MainWindow(start_reader=False, enable_tray=False)
        win.resize(1200, 780)
        win.show()
        self._app.processEvents()

        img = QImage(QSize(1200, 780), QImage.Format_ARGB32)
        win.render(img)
        self.assertFalse(img.isNull())

        state = ControllerState(
            mode="virtual",
            controller_family="playstation",
            physical_path="/dev/input/event30",
            physical_name="DualSense Wireless Controller",
            physical_vid_pid="054c:0ce6",
            virtual_path="/dev/input/event31",
            virtual_name="Microsoft X-Box 360 pad",
            raw_lx=1750,
            raw_ly=300,
            raw_rx=14000,
            raw_ry=-10300,
            out_lx=0,
            out_ly=0,
            out_rx=11200,
            out_ry=0,
            lt=1023,
            rt=512,
            hat_x=-1,
            hat_y=0,
            poll_hz=250.0,
            extra_buttons={704: True, 705: False},
            supported_extra_keys=[704, 705],
        )
        state.buttons["A"] = True
        win._on_controller_state(state)
        self._app.processEvents()

        self.assertIn("250 Hz", win._hz_badge.text())
        self.assertIn("P1", win._active_buttons_label.text())
        self.assertEqual(win._controller_widget.active_family(), "playstation")
        for fam in ("xbox", "playstation", "nintendo_pro", "joycon"):
            win._set_preview_family(fam)
            self._app.processEvents()
            win._controller_widget.grab()
        win._set_preview_family(None)

        win._dz_mode_btns["radial"].click()
        self.assertEqual(win._config.dz_mode, "radial")
        win._adv_toggle.setChecked(True)
        win._ls_controls.outer_slider.setValue(14)
        win._debounce_slider.setValue(18)
        win._rumble_slider.setValue(60)
        win._swap_nintendo_chk.setChecked(True)
        self._app.processEvents()

        self.assertAlmostEqual(win._config.outer_comp_l, 0.14, places=2)
        self.assertEqual(win._config.debounce_ms, 18)
        self.assertEqual(win._config.rumble_gain, 60)
        self.assertTrue(win._config.swap_nintendo)

        # Verify Clips & Moments tab integration (simplified layout & safe hotkeys)
        ctab = win._clips_tab
        ctab._phrase_edit.setText("graba eso")
        ctab._on_apply_voice_phrase()
        self.assertEqual(ctab._config.voice_phrase, "graba eso")
        ctab._dur_combo.setCurrentIndex(3)  # 60s
        self.assertEqual(ctab._config.replay_seconds, 60)
        # Saving when buffer is off must show visible warning text instead of staying silent
        ctab._trigger_save_clip("Rápido", 10)
        self.assertIn("Activar Replay", ctab._rec_status_lbl.text())
        # Ctrl+Alt+F1..F12 must be rejected so TTY doesn't open on Linux
        from PySide6.QtGui import QKeySequence
        ctab._hotkey_edit.setKeySequence(QKeySequence("Ctrl+Alt+F10"))
        self.assertNotEqual(ctab._config.hotkey_combo, "Ctrl+Alt+F10")

        # Verify FitWidthScrollArea prevents horizontal blowout with expanded options & logs
        ctab._adv_toggle.setChecked(True)
        for idx in range(5):
            ctab._on_recorder_log(
                f"gsr info: update fps: 60, damage fps: 60 very long diagnostic log line #{idx} xxxxxxxxxxxxxxxxxxxxx"
            )
        self._app.processEvents()
        self.assertLessEqual(
            ctab._scroll.widget().width(),
            ctab._scroll.viewport().width() + 2,
        )

        # Verify GitHub update manager UI button visibility toggles properly
        from antidrift.updater import UpdateCheckResult

        self.assertTrue(win._update_btn.isHidden())
        self.assertTrue(win._btn_apply_update.isHidden())
        win._on_update_checked(
            UpdateCheckResult(
                update_available=True,
                local_version="1.0.0",
                local_sha="1111111abcdef",
                remote_sha="2222222abcdef",
                remote_message="feat: nueva mejora",
                ahead_by=2,
                commits=["2222222 · feat: nueva mejora", "3333333 · fix: detalle"],
            )
        )
        self._app.processEvents()
        self.assertTrue(win._update_btn.isVisible())
        self.assertFalse(win._btn_apply_update.isHidden())
        self.assertFalse(win._upd_changes_lbl.isHidden())
        self.assertIn("Actualizar disponible", win._update_btn.text())

        win._on_update_checked(
            UpdateCheckResult(
                update_available=False,
                local_version="1.0.0",
                local_sha="2222222abcdef",
                remote_sha="2222222abcdef",
            )
        )
        self._app.processEvents()
        self.assertTrue(win._update_btn.isHidden())
        self.assertTrue(win._btn_apply_update.isHidden())

        # Verify PC startup desktop autostart toggle defaults to False and writes XDG entry
        from antidrift.service_ctrl import is_app_autostart_enabled

        self.assertFalse(win._app_autostart_chk.isChecked())
        win._app_autostart_chk.setChecked(True)
        self._app.processEvents()
        self.assertTrue(is_app_autostart_enabled())
        win._app_autostart_chk.setChecked(False)
        self._app.processEvents()
        self.assertFalse(is_app_autostart_enabled())

        # Verify custom virtual controller name + multi-controller settings & pill updates
        win._virtual_name_edit.setText("Mando de Cesar")
        self._app.processEvents()
        self.assertEqual(win._config.virtual_name, "Mando de Cesar")
        self.assertIn("Mando de Cesar", win._vname_pill_btn.text())
        self.assertTrue(win._multi_pad_chk.isChecked())
        win._multi_pad_chk.setChecked(False)
        self._app.processEvents()
        self.assertFalse(win._config.multi_controller)

        multi_state = ControllerState(
            mode="virtual",
            controller_family="xbox",
            physical_path="/dev/input/event20",
            physical_name="Xbox Pad P1",
            physical_vid_pid="045e:02d1",
            virtual_path="/dev/input/event21",
            virtual_name="Mando de Cesar",
            connected_count=2,
        )
        win._on_controller_state(multi_state)
        self._app.processEvents()
        self.assertIn("2 mandos", win._hw_pill.text())

        win.close()


class ClipsSubsystemTests(unittest.TestCase):
    def test_clips_config_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clips.json"
            cfg = ClipsConfig(
                buffer_enabled=True,
                replay_seconds=60,
                fps=120,
                quality="ultra",
                codec="hevc",
                record_desktop_audio=True,
                record_mic_audio=True,
                voice_enabled=True,
                voice_phrase="guarda clip, momentazo",
                hotkey_combo="Alt+Shift+C",
                controller_shortcut="VIEW+RB",
            )
            save_clips_config(cfg, path)
            loaded = load_clips_config(path)
            self.assertEqual(loaded, cfg)

            # Verify unsafe Linux TTY hotkeys sanitize cleanly to Alt+Shift+C
            bad_cfg = ClipsConfig(hotkey_combo="Ctrl+Alt+F10")
            save_clips_config(bad_cfg, path)
            sanitized = load_clips_config(path)
            self.assertEqual(sanitized.hotkey_combo, "Alt+Shift+C")

    def test_universal_gsr_command_and_backend(self) -> None:
        old_session = os.environ.get("XDG_SESSION_TYPE")
        try:
            os.environ["XDG_SESSION_TYPE"] = "wayland"
            user_fp = GsrBackend(
                kind="flatpak_user",
                base_cmd=["flatpak", "run", "--user", "--command=gpu-screen-recorder", "com.dec05eba.gpu_screen_recorder"],
                is_user_flatpak=True,
                label="Flatpak (usuario)",
            )
            cfg = ClipsConfig(replay_seconds=30, record_desktop_audio=True, record_mic_audio=True)
            self.assertEqual(resolve_capture_target(cfg, user_fp), "portal")
            prog, args = build_gsr_args(
                cfg,
                user_fp,
                desktop_monitors=["sink1.monitor", "sink2.monitor"],
            )
            self.assertEqual(prog, "flatpak")
            self.assertIn("-restore-portal-session", args)
            self.assertIn(
                "device:sink1.monitor|device:sink2.monitor|default_input",
                args,
            )
            _, fallback_args = build_gsr_args(cfg, user_fp, desktop_monitors=[])
            self.assertIn("default_output|default_input", fallback_args)

            native = GsrBackend(
                kind="native",
                base_cmd=["/usr/bin/gpu-screen-recorder"],
                is_user_flatpak=False,
                label="Nativo",
            )
            self.assertEqual(resolve_capture_target(cfg, native), "screen")

            # On X11, multi-monitor 'auto' or stale 'portal' resolves to primary monitor cleanly
            from antidrift.clips.recorder import match_primary_monitor

            os.environ["XDG_SESSION_TYPE"] = "x11"
            mons = [("HDMI-A-1", "1920x1080"), ("DP-2", "2560x1440")]
            self.assertEqual(match_primary_monitor(mons, "DisplayPort-2", "2560x1440"), "DP-2")
            self.assertEqual(match_primary_monitor(mons, "HDMI-1", "1920x1080"), "HDMI-A-1")
            self.assertEqual(
                resolve_capture_target(
                    ClipsConfig(capture_target="auto"),
                    user_fp,
                    gsr_monitors=mons,
                    primary_hint=("DP-2", "2560x1440"),
                ),
                "DP-2",
            )
            self.assertEqual(
                resolve_capture_target(
                    ClipsConfig(capture_target="portal"),
                    user_fp,
                    gsr_monitors=mons,
                    primary_hint=("DP-2", "2560x1440"),
                ),
                "DP-2",
            )
            _, focused_args = build_gsr_args(
                ClipsConfig(capture_target="focused"),
                native,
                desktop_monitors=["sink1.monitor"],
            )
            self.assertIn("-s", focused_args)

            self.assertIsNotNone(detect_gsr_backend())
        finally:
            if old_session is None:
                os.environ.pop("XDG_SESSION_TYPE", None)
            else:
                os.environ["XDG_SESSION_TYPE"] = old_session

    def test_voice_and_hotkey_parsers(self) -> None:
        self.assertTrue(vosk_is_available())
        self.assertIsNotNone(find_vosk_model_dir())
        phrases = parse_wake_phrases("Guarda Clip, Graba Eso")
        self.assertEqual(phrases, ["guarda clip", "graba eso"])
        self.assertEqual(match_wake_phrase("por favor guarda clip ahora", phrases), "guarda clip")
        self.assertEqual(match_wake_phrase("haz clic ahora", ["clip"]), "clip")
        self.assertEqual(match_wake_phrase("oye cli", ["clip"]), "clip")
        self.assertIsNone(match_wake_phrase("el clima hoy", ["clip"]))
        self.assertIsNone(match_wake_phrase("hola mundo", phrases))
        ok, unk = validate_phrase_words("guarda clip")
        self.assertTrue(ok)
        self.assertEqual(unk, [])

        # Accents, uppercase, phonetics, filler connectors, and compound forms
        self.assertEqual(match_wake_phrase("haz una GRABACIÓN rápida", ["grabacion rapida"]), "grabacion rapida")
        self.assertEqual(match_wake_phrase("Guarda el Clip ahora", ["guarda clip"]), "guarda clip")
        self.assertEqual(match_wake_phrase("saca un momento increíble", ["saca momento"]), "saca momento")
        self.assertEqual(match_wake_phrase("por favor grava eso ya", ["graba eso"]), "graba eso")
        self.assertEqual(match_wake_phrase("guardaclip", ["guarda clip"]), "guarda clip")
        self.assertEqual(match_wake_phrase("Cámara", ["camara"]), "camara")
        self.assertIsNone(match_wake_phrase("buenos días amigo", ["grabacion rapida", "saca momento"]))

        ok_acc, unk_acc = validate_phrase_words("Grabacion Rapida Guardalo")
        self.assertTrue(ok_acc, f"Unexpected unknown words: {unk_acc}")

        self.assertEqual(qt_seq_to_xdg_trigger("Alt+Shift+C"), "ALT+SHIFT+C")
        mods, main_k = parse_combo_evdev("Alt+Shift+C")
        self.assertEqual(len(mods), 2)
        self.assertEqual(main_k, 46)

    def test_distro_deps_detection(self) -> None:
        from antidrift.deps import DistroInfo, build_distro_install_command, check_dependencies, detect_distro

        d = detect_distro()
        self.assertNotEqual(d.pkg_manager, "")
        self.assertEqual(
            build_distro_install_command(["gcc", "flatpak"], DistroInfo("arch", "Arch", "pacman")),
            ["pacman", "-S", "--needed", "--noconfirm", "gcc", "flatpak"],
        )
        self.assertEqual(
            build_distro_install_command(["gcc", "flatpak"], DistroInfo("fedora", "Fedora", "dnf")),
            ["dnf", "install", "-y", "gcc", "flatpak"],
        )
        self.assertEqual(
            build_distro_install_command(["gcc"], DistroInfo("opensuse", "openSUSE", "zypper")),
            ["zypper", "--non-interactive", "install", "gcc"],
        )
        rep = check_dependencies()
        self.assertTrue(rep.all_ok)

    def test_sound_chimes_and_dnd_guard(self) -> None:
        import wave
        from antidrift.clips.sound_fx import ensure_sound_assets
        from antidrift.clips.recorder import ensure_notifications_not_inhibited

        cue, saved = ensure_sound_assets()
        self.assertTrue(cue.exists() and saved.exists())
        for wav_path in (cue, saved):
            with wave.open(str(wav_path), "rb") as wf:
                self.assertEqual(wf.getnchannels(), 1)
                self.assertEqual(wf.getframerate(), 44100)
                self.assertGreater(wf.getnframes(), 3000)
        ensure_notifications_not_inhibited()

    def test_controller_family_detection(self) -> None:
        from antidrift.evdev_reader import detect_controller_family

        self.assertEqual(
            detect_controller_family("Microsoft X-Box One pad", "045e:02d1"),
            "xbox",
        )
        self.assertEqual(
            detect_controller_family("DualSense Wireless Controller", "054c:0ce6"),
            "playstation",
        )
        self.assertEqual(
            detect_controller_family("Sony Interactive Entertainment DualShock 4", "054c:09cc"),
            "playstation",
        )
        self.assertEqual(
            detect_controller_family("Nintendo Switch Pro Controller", "057e:2009"),
            "nintendo_pro",
        )
        self.assertEqual(
            detect_controller_family("Nintendo Switch Joy-Con (L)", "057e:2006"),
            "joycon",
        )
        self.assertEqual(
            detect_controller_family("Combined Joy-Cons", "057e:200e"),
            "joycon",
        )
        self.assertEqual(
            detect_controller_family("Generic USB Gamepad", "1234:5678"),
            "xbox",
        )


class UpdaterTests(unittest.TestCase):
    def test_github_url_version_and_api_checks(self) -> None:
        from antidrift.updater import (
            check_for_updates,
            is_newer_version,
            parse_github_remote_url,
        )

        self.assertEqual(
            parse_github_remote_url("https://github.com/user1/Repo-Test.git"),
            ("user1", "Repo-Test"),
        )
        self.assertEqual(
            parse_github_remote_url("git@github.com:user1/Repo-Test.git"),
            ("user1", "Repo-Test"),
        )
        self.assertTrue(is_newer_version("v1.2.0", "1.0.9"))
        self.assertFalse(is_newer_version("v1.0.0", "1.0.0"))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
            stamp = root / "stamp.txt"
            stamp.write_text("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n", encoding="utf-8")

            def _mock_fetch_ahead(url: str, _timeout: float):
                if url.endswith("/releases/latest"):
                    raise RuntimeError("404")
                if "/commits/" in url:
                    return {
                        "sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                        "commit": {
                            "message": "feat: add update check\nextended body",
                            "committer": {"date": "2026-11-17T06:00:00Z"},
                        },
                    }
                if "/compare/" in url:
                    return {
                        "status": "ahead",
                        "ahead_by": 2,
                        "commits": [
                            {"sha": "cccccccc", "commit": {"message": "fix: first"}},
                            {"sha": "bbbbbbbb", "commit": {"message": "feat: add update check"}},
                        ],
                    }
                return {}

            res = check_for_updates(
                project_root=root,
                stamp_path=stamp,
                fetcher=_mock_fetch_ahead,
            )
            self.assertTrue(res.update_available)
            self.assertEqual(res.ahead_by, 2)
            self.assertEqual(res.remote_short_sha, "bbbbbbb")
            self.assertEqual(res.commits[0], "bbbbbbb · feat: add update check")

            # Local ahead of GitHub ('behind' in compare/{local}...{remote}) must NOT flag update
            def _mock_fetch_local_ahead(url: str, _timeout: float):
                if url.endswith("/releases/latest"):
                    raise RuntimeError("404")
                if "/commits/" in url:
                    return {
                        "sha": "1111111111111111111111111111111111111111",
                        "commit": {"message": "old remote commit"},
                    }
                if "/compare/" in url:
                    return {"status": "behind", "ahead_by": 0}
                return {}

            res2 = check_for_updates(
                project_root=root,
                stamp_path=stamp,
                fetcher=_mock_fetch_local_ahead,
            )
            self.assertFalse(res2.update_available)

    def test_git_clone_check_and_apply_update(self) -> None:
        import subprocess
        from antidrift.updater import apply_update, check_for_updates

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            remote = base / "remote.git"
            work = base / "work"
            client = base / "client"

            git_env = {
                **os.environ,
                "GIT_AUTHOR_NAME": "Test",
                "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_COMMITTER_EMAIL": "test@example.com",
            }
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "clone", str(remote), str(work)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(work), "checkout", "-b", "main"], check=True, capture_output=True)
            (work / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
            subprocess.run(["git", "-C", str(work), "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(work), "commit", "-m", "initial"], check=True, env=git_env, capture_output=True)
            subprocess.run(["git", "-C", str(work), "push", "-u", "origin", "main"], check=True, capture_output=True)

            subprocess.run(["git", "clone", "-b", "main", str(remote), str(client)], check=True, capture_output=True)

            # Push a new commit to remote from work
            (work / "pyproject.toml").write_text('version = "1.1.0"\n', encoding="utf-8")
            subprocess.run(["git", "-C", str(work), "commit", "-am", "feat: version 1.1.0"], check=True, env=git_env, capture_output=True)
            subprocess.run(["git", "-C", str(work), "push", "origin", "main"], check=True, capture_output=True)

            # Check via git fallback (offline HTTP fetcher)
            res = check_for_updates(
                project_root=client,
                fetcher=lambda _u, _t: (_ for _ in ()).throw(RuntimeError("offline")),
            )
            self.assertTrue(res.update_available)
            self.assertEqual(res.ahead_by, 1)
            self.assertIn("feat: version 1.1.0", res.remote_message)

            # Apply update
            ok, msg, restart_rec = apply_update(project_root=client)
            self.assertTrue(ok, msg)
            self.assertTrue(restart_rec)
            self.assertIn('version = "1.1.0"', (client / "pyproject.toml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
