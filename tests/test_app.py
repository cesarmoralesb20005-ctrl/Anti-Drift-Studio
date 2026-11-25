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
        for fam in ("xbox", "playstation", "nintendo_pro", "joycon", "wheel"):
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

        # Verify gamepad triggers panel (LT/RT max limiters) on gamepad page
        self.assertEqual(win._mode_stack.currentIndex(), 0)
        win._trig_widgets["LT"]["max_slider"].setValue(50)
        self._app.processEvents()
        self.assertAlmostEqual(win._config.lt_max, 0.50, places=2)
        self.assertEqual(win._trig_widgets["LT"]["badge"].text(), "Tope: 50%")

        # Verify steering wheel full mode switch + shifter + handbrake + clutch UI
        win._on_controller_state(
            ControllerState(
                mode="virtual",
                controller_family="wheel",
                is_wheel=True,
                physical_name="Thrustmaster T80 Ferrari",
                physical_vid_pid="044f:b668",
                physical_stable_id="044f:b668|wheel-test",
                out_lx=16384,
                lt=0,
                rt=820,
                handbrake=700,
                shifter_gear="3",
                poll_hz=250.0,
            )
        )
        self._app.processEvents()
        self.assertEqual(win._mode_stack.currentIndex(), 1)
        self.assertEqual(win._family_btn.text(), "Auto: Volante ▾")
        self.assertIn("Giro", win._active_buttons_label.text())
        self.assertIn("Gas", win._active_buttons_label.text())
        self.assertIn("F.Mano", win._active_buttons_label.text())
        self.assertIn("Marcha 3", win._active_buttons_label.text())
        win._wheel_rot_btns[240].click()
        self.assertEqual(win._config.wheel_rotation_deg, 240)
        win._wheel_shifter_btns["h_shifter"].click()
        self.assertEqual(win._config.wheel_shifter_mode, "h_shifter")
        win._wheel_hb_dz_slider.setValue(30)
        self.assertAlmostEqual(win._config.wheel_handbrake_dz, 0.30, places=2)
        win._wheel_inv_gas_chk.setChecked(True)
        self.assertTrue(win._config.wheel_invert_gas)
        win._wheel_combo_chk.setChecked(True)
        self.assertTrue(win._config.wheel_combined_pedals)
        win._wheel_inv_gas_chk.setChecked(False)
        win._wheel_combo_chk.setChecked(False)
        win._trig_widgets["LT"]["max_slider"].setValue(100)
        win._on_controller_state(
            ControllerState(
                mode="virtual",
                controller_family="xbox",
                is_wheel=False,
                physical_name="Xbox Wireless Controller",
                physical_stable_id="045e:0b13|back-pad",
                poll_hz=250.0,
            )
        )
        self._app.processEvents()
        self.assertEqual(win._mode_stack.currentIndex(), 0)

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
            prog, args = build_gsr_args(cfg, user_fp)
            self.assertEqual(prog, "flatpak")
            self.assertIn("-restore-portal-session", args)
            # Clean default avoids multi-monitor DSP echo by using default_output
            self.assertIn("default_output|default_input", args)

            # Explicit all_outputs mode combines physical hardware monitors cleanly
            cfg_all = ClipsConfig(
                replay_seconds=30,
                record_desktop_audio=True,
                desktop_audio_source="all_outputs",
                record_mic_audio=True,
            )
            _, args_all = build_gsr_args(
                cfg_all,
                user_fp,
                desktop_monitors=["sink1.monitor", "sink2.monitor"],
            )
            self.assertIn(
                "device:sink1.monitor|device:sink2.monitor|default_input",
                args_all,
            )
            _, fallback_args = build_gsr_args(cfg_all, user_fp, desktop_monitors=[])
            self.assertIn("default_output|default_input", fallback_args)

            from antidrift.clips.recorder import is_virtual_audio_monitor

            self.assertTrue(is_virtual_audio_monitor("easyeffects_sink.monitor"))
            self.assertTrue(is_virtual_audio_monitor("combine_sink.monitor"))
            self.assertFalse(is_virtual_audio_monitor("alsa_output.pci-0000_07_00.6.analog-stereo.monitor"))

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

        from PySide6.QtGui import QKeySequence
        from antidrift.clips.hotkey_listener import (
            _x11_keycode_pressed,
            parse_combo_x11_names,
            qt_seq_to_combined_int,
        )

        x_mods, x_main = parse_combo_x11_names("Alt+Shift+C")
        self.assertEqual(len(x_mods), 2)
        self.assertEqual(x_main, "c")
        kbuf = bytearray(32)
        kbuf[54 >> 3] |= 1 << (54 & 7)
        self.assertTrue(_x11_keycode_pressed(bytes(kbuf), 54))
        self.assertFalse(_x11_keycode_pressed(bytes(kbuf), 55))

        for combo_str in ("Alt+Shift+C", "Ctrl+Shift+F9", "Meta+Alt+Space", "Esc", "Pause"):
            self.assertEqual(
                qt_seq_to_combined_int(combo_str),
                QKeySequence(combo_str)[0].toCombined(),
            )

    def test_kde_globalaccel_sets_present_flag_and_x11_companion(self) -> None:
        from contextlib import contextmanager
        from unittest import mock

        from antidrift.clips.hotkey_listener import GlobalHotkeyThread

        calls: list[tuple[str, object]] = []

        class _FakeReply:
            def __init__(self, body):
                self.body = body

        class _FakeMsg:
            def __init__(self, body):
                self.body = body

        class _FakeConn:
            def send_and_get_reply(self, msg):
                member = msg.header.fields.get(3)  # HeaderFields.member
                calls.append((member, msg.body))
                if member == "NameHasOwner":
                    return _FakeReply([True])
                if member == "getComponent":
                    return _FakeReply(["/component/xbox_antidrift_gui"])
                return _FakeReply([])

            @contextmanager
            def filter(self, _rule):
                yield object()

            def recv_until_filtered(self, _q, timeout=0.25):
                # Emit one globalShortcutPressed then request stop
                th._stop_requested = True
                return _FakeMsg(("xbox-antidrift-gui", "save_clip", 1))

            def close(self):
                calls.append(("close", None))

        th = GlobalHotkeyThread("Alt+Shift+C")
        triggered: list[tuple[str, int]] = []
        th.hotkey_triggered.connect(lambda src, sec: triggered.append((src, sec)))

        with mock.patch("jeepney.io.blocking.open_dbus_connection", return_value=_FakeConn()):
            ok = th._run_kde_globalaccel_loop()

        self.assertTrue(ok)
        set_sc = [body for name, body in calls if name == "setShortcut"]
        self.assertTrue(set_sc)
        self.assertEqual(set_sc[0][2], 6)  # SetPresent (2) | NoAutoloading (4)
        self.assertTrue(any(name == "setInactive" for name, _ in calls))
        self.assertEqual(triggered, [("Atajo global (Alt+Shift+C)", 0)])

        # Verify run() runs XQueryKeymap companion with emit_status=False when DISPLAY is set
        x11_kwargs: list[dict] = []
        th2 = GlobalHotkeyThread("Alt+Shift+C")
        th2._stop_requested = True
        with (
            mock.patch.dict(os.environ, {"DISPLAY": ":1", "XDG_SESSION_TYPE": "wayland"}, clear=False),
            mock.patch.object(th2, "_run_ipc_server", return_value=None),
            mock.patch.object(th2, "_readable_keyboards", return_value=[]),
            mock.patch.object(th2, "_kde_kglobalaccel_available", return_value=True),
            mock.patch.object(th2, "_run_kde_globalaccel_loop", return_value=True),
            mock.patch.object(
                th2,
                "_run_x11_loop",
                side_effect=lambda emit_status=True: x11_kwargs.append({"emit_status": emit_status}) or True,
            ),
        ):
            th2.run()
        self.assertEqual(x11_kwargs, [{"emit_status": False}])

    def test_clickable_clip_notification_and_selection(self) -> None:
        from types import SimpleNamespace
        from jeepney import HeaderFields, MessageType
        from antidrift.clips.desktop_notifier import DesktopClipNotifier
        from antidrift.main_window import MainWindow

        notifier = DesktopClipNotifier()
        addr_dummy = SimpleNamespace(
            bus_name="org.freedesktop.Notifications",
            object_path="/org/freedesktop/Notifications",
            interface="org.freedesktop.Notifications",
        )
        msg = notifier._build_notify_call(
            addr_dummy, "Clip guardado", "Replay_1.mp4", "/tmp/Replay_1.mp4", 5000
        )
        actions = msg.body[5]
        self.assertIn("default", actions)
        self.assertIn("open_clip", actions)
        self.assertIn("Ver en Studio", actions)

        notifier._record_notification(41, "/tmp/Replay_1.mp4")
        notifier._record_notification(42, "/tmp/Replay_2.mp4")

        opened: list[tuple[str, str]] = []
        notifier.open_clip_requested.connect(lambda p, tok: opened.append((p, tok)))

        def _sig(member: str, body: tuple):
            return SimpleNamespace(
                header=SimpleNamespace(
                    message_type=MessageType.signal,
                    fields={
                        HeaderFields.interface: "org.freedesktop.Notifications",
                        HeaderFields.member: member,
                    },
                ),
                body=body,
            )

        notifier._handle_incoming(_sig("ActivationToken", (41, "tok-41")))
        notifier._handle_incoming(_sig("ActionInvoked", (41, "open_clip")))
        notifier._handle_incoming(_sig("ActionInvoked", (42, "default")))
        self.assertEqual(
            opened,
            [("/tmp/Replay_1.mp4", "tok-41"), ("/tmp/Replay_2.mp4", "")],
        )

        # Verify MainWindow selects the exact notification clip and switches to Clips tab
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        _app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            clip1 = Path(tmp) / "Replay_older.mp4"
            clip2 = Path(tmp) / "Replay_newer.mp4"
            clip1.write_bytes(b"0" * 1024)
            clip2.write_bytes(b"0" * 2048)
            os.utime(clip1, (1000, 1000))
            os.utime(clip2, (2000, 2000))

            old_xdg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmp
            try:
                win = MainWindow(start_reader=False, enable_tray=False)
                win._clips_tab._config.output_dir = tmp
                win._main_tabs.setCurrentIndex(0)
                win._on_open_clip_from_notification(str(clip1), "xdg-tok-1")

                self.assertEqual(win._main_tabs.currentIndex(), win._clips_tab_idx)
                cur = win._clips_tab._clips_list.currentItem()
                self.assertIsNotNone(cur)
                self.assertEqual(cur.data(Qt.UserRole), str(clip1))
                self.assertEqual(os.environ.get("XDG_ACTIVATION_TOKEN"), "xdg-tok-1")
                win.close()
            finally:
                if old_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old_xdg

    def test_xbox_share_preview_and_custom_pad_combo(self) -> None:
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QApplication
        from unittest import mock

        from antidrift.clips.clips_config import (
            controller_shortcut_matches,
            extract_pressed_pad_tokens,
            format_controller_shortcut_label,
            normalize_controller_shortcut,
        )
        from antidrift.clips.clips_tab import ClipsTabWidget
        from antidrift.widgets.controller_widget import ControllerWidget

        # 1. Token normalization, label formatting & multi-button matching
        self.assertEqual(normalize_controller_shortcut("rb + view"), "VIEW+RB")
        self.assertEqual(normalize_controller_shortcut("Captura"), "SHARE")
        self.assertEqual(normalize_controller_shortcut("A+X+LB+RB"), "LB+RB+A+X")
        self.assertEqual(
            format_controller_shortcut_label("LT+RT+Y"),
            "LT + RT + Y",
        )

        st_share = ControllerState(mode="live", extra_buttons={167: True})
        self.assertEqual(extract_pressed_pad_tokens(st_share), ["SHARE"])
        self.assertTrue(controller_shortcut_matches("SHARE", st_share))

        st_chord = ControllerState(
            mode="live",
            buttons={**ControllerState().buttons, "LB": True, "RB": True, "A": True},
            lt=800,
            hat_y=-1,
            extra_buttons={710: True},
        )
        self.assertTrue(controller_shortcut_matches("LB+RB+A", st_chord))
        self.assertTrue(controller_shortcut_matches("LT+UP+KEY_710", st_chord))
        self.assertFalse(controller_shortcut_matches("LB+RB+B", st_chord))

        # 2. Xbox Share button exists on ControllerWidget preview and toggles 167 in simulator
        _app = QApplication.instance() or QApplication([])
        cw = ControllerWidget()
        cw.resize(int(cw.LOGICAL_W), int(cw.LOGICAL_H))
        cw.set_simulator_enabled(True)
        share_pt = cw._layout_spec().share
        self.assertIsNotNone(share_pt)
        ev = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(share_pt.x(), share_pt.y()),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        cw.mousePressEvent(ev)
        self.assertTrue(cw._state.extra_buttons.get(167, False))

        # 3. Interactive peak-hold capture & edge-triggered save on ClipsTabWidget
        with tempfile.TemporaryDirectory() as tmp:
            old_xdg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmp
            try:
                tab = ClipsTabWidget(start_background=False)
                tab._btn_capture_pad.setChecked(True)
                self.assertTrue(tab._capturing_pad)

                tab.process_controller_state(
                    ControllerState(
                        mode="live",
                        buttons={**ControllerState().buttons, "LB": True},
                    )
                )
                tab.process_controller_state(
                    ControllerState(
                        mode="live",
                        buttons={**ControllerState().buttons, "LB": True, "RB": True, "A": True},
                    )
                )
                # Still capturing until released
                self.assertTrue(tab._capturing_pad)
                # Release all -> finalizes combo even with Replay off
                tab.process_controller_state(ControllerState(mode="live"))
                self.assertFalse(tab._capturing_pad)
                self.assertEqual(tab._config.controller_shortcut, "LB+RB+A")
                self.assertEqual(tab._pad_sc_combo.currentData(), "LB+RB+A")

                # Edge-triggered clip trigger fires once per hold while Replay is active
                saved_calls: list[str] = []
                with (
                    mock.patch.object(tab._recorder, "is_running", return_value=True),
                    mock.patch.object(
                        tab, "_trigger_save_clip", side_effect=lambda src: saved_calls.append(src)
                    ),
                ):
                    chord_state = ControllerState(
                        mode="live",
                        buttons={**ControllerState().buttons, "LB": True, "RB": True, "A": True},
                    )
                    tab.process_controller_state(chord_state)
                    tab.process_controller_state(chord_state)  # held -> no repeat
                    tab.process_controller_state(ControllerState(mode="live"))  # release
                    tab._last_ctrl_combo_fire = 0.0
                    tab.process_controller_state(chord_state)  # second press -> fires again
                self.assertEqual(len(saved_calls), 2)
                tab.shutdown()
            finally:
                if old_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old_xdg

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
            detect_controller_family("Xbox Wireless Controller", "045e:0b13"),
            "xbox",
        )
        self.assertEqual(
            detect_controller_family("Xbox Wireless Controller", None),
            "xbox",
        )
        self.assertEqual(
            detect_controller_family("PowerA Wireless Controller", "20d6:2002"),
            "xbox",
        )
        self.assertEqual(
            detect_controller_family("Wireless Controller", "054c:0ce6"),
            "playstation",
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
        self.assertEqual(
            detect_controller_family("Thrustmaster T80 Ferrari Racing Wheel", "044f:b668"),
            "wheel",
        )
        self.assertEqual(
            detect_controller_family("Logitech G29 Driving Force Racing Wheel", "046d:c24f"),
            "wheel",
        )
        self.assertEqual(
            detect_controller_family("Fanatec CSL Elite Wheel Base", "0eb7:0e03"),
            "wheel",
        )


class SteeringWheelTests(unittest.TestCase):
    def test_wheel_config_roundtrip_math_and_profile(self) -> None:
        from antidrift.config import (
            AntiDriftConfig,
            compute_wheel_pedal,
            compute_wheel_steering,
            load_config,
            save_config,
            split_combined_pedals,
        )
        from antidrift.profiles import default_profiles

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "wheel.conf"
            cfg = AntiDriftConfig(
                center_lx=420,
                wheel_mode="wheel",
                wheel_rotation_deg=240,
                wheel_steer_dz=0.05,
                wheel_steer_sat=0.80,
                wheel_linearity=1.25,
                wheel_dz_gas=0.04,
                wheel_dz_brake=0.06,
                wheel_max_gas=0.90,
                wheel_max_brake=0.85,
                wheel_invert_gas=True,
                wheel_invert_brake=False,
                wheel_combined_pedals=True,
            )
            save_config(cfg, path=cfg_path)
            loaded = load_config(path=cfg_path)
            self.assertEqual(loaded.wheel_mode, "wheel")
            self.assertEqual(loaded.wheel_rotation_deg, 240)
            self.assertAlmostEqual(loaded.wheel_steer_dz, 0.05, places=3)
            self.assertAlmostEqual(loaded.wheel_steer_sat, 0.80, places=2)
            self.assertAlmostEqual(loaded.wheel_linearity, 1.25, places=2)
            self.assertAlmostEqual(loaded.wheel_dz_gas, 0.04, places=3)
            self.assertAlmostEqual(loaded.wheel_dz_brake, 0.06, places=3)
            self.assertAlmostEqual(loaded.wheel_max_gas, 0.90, places=2)
            self.assertAlmostEqual(loaded.wheel_max_brake, 0.85, places=2)
            self.assertTrue(loaded.wheel_invert_gas)
            self.assertFalse(loaded.wheel_invert_brake)
            self.assertTrue(loaded.wheel_combined_pedals)

        self.assertEqual(compute_wheel_steering(420, 420, 0.05, 1.0, 1.0), 0)
        self.assertEqual(compute_wheel_steering(26500, 0, 0.05, 0.80, 1.0), 32767)
        self.assertEqual(compute_wheel_steering(-26500, 0, 0.05, 0.80, 1.0), -32767)
        lin_mid = compute_wheel_steering(16384, 0, 0.0, 1.0, 1.0)
        soft_mid = compute_wheel_steering(16384, 0, 0.0, 1.0, 1.5)
        self.assertLess(soft_mid, lin_mid)

        self.assertEqual(compute_wheel_pedal(20, dz=0.05), 0)
        self.assertEqual(compute_wheel_pedal(900, dz=0.0, max_reach=0.80), 1023)
        self.assertEqual(compute_wheel_pedal(1023, dz=0.03, invert=True), 0)
        self.assertEqual(compute_wheel_pedal(0, dz=0.03, invert=True), 1023)
        self.assertEqual(compute_wheel_pedal(200, dz=0.0, hair_trigger=True), 1023)

        self.assertEqual(split_combined_pedals(0), (0, 0))
        self.assertEqual(split_combined_pedals(-32768), (0, 1023))
        self.assertEqual(split_combined_pedals(32767), (1023, 0))
        self.assertEqual(split_combined_pedals(-32768, swap_polarity=True), (1023, 0))

        profs = {p.id: p for p in default_profiles()}
        self.assertIn("racing_wheel", profs)
        wprof = profs["racing_wheel"]
        self.assertEqual(wprof.wheel_mode, "wheel")
        self.assertEqual(wprof.wheel_rotation_deg, 270)
        applied = wprof.apply_to_config(AntiDriftConfig())
        self.assertEqual(applied.wheel_mode, "wheel")
        self.assertEqual(applied.wheel_rotation_deg, 270)

    def test_wheel_hardware_probe_separate_and_combined(self) -> None:
        from unittest.mock import patch
        from antidrift import evdev_reader as er

        self.assertEqual(er.GENERIC_JOY_BUTTON_MAP[300], "GUIDE")
        self.assertEqual(er.WHEEL_SHIFTER_MAP[er.BTN_GEAR_DOWN], "LB")
        self.assertEqual(er.WHEEL_SHIFTER_MAP[er.BTN_GEAR_UP], "RB")

        def _make_fake_ioctl(abs_codes: set[int], key_codes: set[int]):
            def fake_ioctl(_fd, req, buf):
                if req == er.EVIOCGBIT_ABS_8:
                    for c in abs_codes:
                        buf[c >> 3] |= 1 << (c & 7)
                    return len(buf)
                if req == er.EVIOCGBIT_KEY_96:
                    for c in key_codes:
                        buf[c >> 3] |= 1 << (c & 7)
                    return len(buf)
                return 0

            return fake_ioctl

        with (
            patch.object(
                er.fcntl,
                "ioctl",
                side_effect=_make_fake_ioctl(
                    {er.ABS_X, er.ABS_GAS, er.ABS_BRAKE, er.ABS_HAT0X},
                    {er.BTN_TRIGGER, er.BTN_GEAR_DOWN, er.BTN_GEAR_UP},
                ),
            ),
            patch.object(er, "read_abs_info", return_value=None),
        ):
            hm = er.probe_hardware_map(99, "Thrustmaster T80 Ferrari")
        self.assertTrue(hm.is_wheel)
        self.assertEqual(hm.lx_code, er.ABS_X)
        self.assertEqual(hm.ly_code, -1)
        self.assertEqual(hm.rt_code, er.ABS_GAS)
        self.assertEqual(hm.lt_code, er.ABS_BRAKE)
        self.assertEqual(hm.combo_pedal_code, -1)
        self.assertTrue(hm.use_generic_joy_btns)
        self.assertTrue(hm.wheel_use_gear_shifters)

        with (
            patch.object(
                er.fcntl,
                "ioctl",
                side_effect=_make_fake_ioctl(
                    {er.ABS_X, er.ABS_Y, er.ABS_HAT0X},
                    {er.BTN_SOUTH},
                ),
            ),
            patch.object(er, "read_abs_info", return_value=None),
        ):
            hm_combo = er.probe_hardware_map(99, "Thrustmaster T80 Combined")
        self.assertTrue(hm_combo.is_wheel)
        self.assertEqual(hm_combo.lx_code, er.ABS_X)
        self.assertEqual(hm_combo.ly_code, -1)
        self.assertEqual(hm_combo.combo_pedal_code, er.ABS_Y)

        # Xbox Series / One Bluetooth controller reports triggers as ABS_GAS/ABS_BRAKE
        # alongside dual XY + RXRY thumbsticks: must stay a gamepad, NEVER a wheel.
        with (
            patch.object(
                er.fcntl,
                "ioctl",
                side_effect=_make_fake_ioctl(
                    {er.ABS_X, er.ABS_Y, er.ABS_RX, er.ABS_RY, er.ABS_GAS, er.ABS_BRAKE, er.ABS_HAT0X},
                    {er.BTN_SOUTH},
                ),
            ),
            patch.object(er, "read_abs_info", return_value=None),
        ):
            hm_xbox = er.probe_hardware_map(99, "Xbox Wireless Controller")
        self.assertFalse(hm_xbox.is_wheel)
        self.assertEqual(hm_xbox.lx_code, er.ABS_X)
        self.assertEqual(hm_xbox.ly_code, er.ABS_Y)
        self.assertEqual(hm_xbox.rx_code, er.ABS_RX)
        self.assertEqual(hm_xbox.ry_code, er.ABS_RY)
        self.assertEqual(hm_xbox.lt_code, er.ABS_BRAKE)
        self.assertEqual(hm_xbox.rt_code, er.ABS_GAS)
        self.assertEqual(er.detect_controller_family("Xbox Wireless Controller", "045e:0b13"), "xbox")

        # Thrustmaster T80 PS4 (044f:b668): ABS_X (128), idle ABS_Z/ABS_RZ (128),
        # real pedals on ABS_RX/ABS_RY (0..255 resting at 0), and 14 DS4 hid-generic keys (304..317).
        def _fake_t80_abs(_fd, code):
            if code in (er.ABS_RX, er.ABS_RY):
                return (0, 0, 255)
            return (128, 0, 255)

        with (
            patch.object(
                er.fcntl,
                "ioctl",
                side_effect=_make_fake_ioctl(
                    {er.ABS_X, er.ABS_Y, er.ABS_Z, er.ABS_RX, er.ABS_RY, er.ABS_RZ, er.ABS_HAT0X},
                    set(range(304, 318)),
                ),
            ),
            patch.object(er, "read_abs_info", side_effect=_fake_t80_abs),
        ):
            hm_t80 = er.probe_hardware_map(99, "Thrustmaster Thrustmaster T80")
        self.assertTrue(hm_t80.is_wheel)
        self.assertEqual(hm_t80.lx_code, er.ABS_X)
        self.assertEqual(hm_t80.lt_code, er.ABS_RX)
        self.assertEqual(hm_t80.rt_code, er.ABS_RY)
        self.assertTrue(hm_t80.use_ds4_hid_btns)
        self.assertNotIn(310, er.DS4_HID_BUTTON_MAP)
        self.assertNotIn(311, er.DS4_HID_BUTTON_MAP)

    def test_wheel_visualizer_simulator_and_main_window_controls(self) -> None:
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QApplication
        from antidrift.config import AntiDriftConfig
        from antidrift.evdev_reader import ControllerState
        from antidrift.widgets.controller_widget import ControllerWidget, _SPECS

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])

        w = ControllerWidget()
        w.resize(600, 492)
        w.set_family_override("wheel")
        w.set_config_preview(AntiDriftConfig(wheel_mode="wheel", wheel_rotation_deg=240))
        w.set_simulator_enabled(True)

        scale, ox, oy = w._transform_params()
        spec = _SPECS["wheel"]

        def _logical_to_widget(pt: QPointF) -> QPointF:
            return QPointF(ox + pt.x() * scale, oy + pt.y() * scale)

        wheel_right = _logical_to_widget(QPointF(438.0, 212.0))
        press_ev = QMouseEvent(
            QMouseEvent.MouseButtonPress,
            wheel_right,
            wheel_right,
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        w.mousePressEvent(press_ev)
        self.assertGreater(w._state.raw_lx, 15000)
        self.assertGreater(w._state.out_lx, 15000)

        rel_ev = QMouseEvent(
            QMouseEvent.MouseButtonRelease,
            wheel_right,
            wheel_right,
            Qt.LeftButton,
            Qt.NoButton,
            Qt.NoModifier,
        )
        w.mouseReleaseEvent(rel_ev)
        self.assertEqual(w._state.raw_lx, 0)
        self.assertEqual(w._state.out_lx, 0)

        gas_pt = _logical_to_widget(spec.rt_rect.center())
        w.mousePressEvent(
            QMouseEvent(
                QMouseEvent.MouseButtonPress,
                gas_pt,
                gas_pt,
                Qt.LeftButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
        )
        self.assertEqual(w._state.raw_rt, 1023)
        self.assertEqual(w._state.rt, 1023)
        w.grab()

        st = ControllerState(
            mode="virtual",
            controller_family="wheel",
            is_wheel=True,
            physical_name="Thrustmaster T80 Ferrari",
            out_lx=16384,
            lt=0,
            rt=800,
            clutch=450,
            handbrake=720,
            shifter_gear="4",
        )
        w.set_simulator_enabled(False)
        w.set_controller_state(st)
        self.assertEqual(w.active_family(), "wheel")
        w.grab()
        app.processEvents()

    def test_gamepad_triggers_and_shifter_handbrake_roundtrip(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QApplication
        from antidrift.config import (
            AntiDriftConfig,
            compute_trigger_output,
            load_config,
            save_config,
        )
        from antidrift.profiles import default_profiles
        from antidrift.widgets.controller_widget import ControllerWidget, _SPECS

        self.assertEqual(compute_trigger_output(1023, dz=0.0, max_out=0.50, curve=1.0), 512)
        self.assertEqual(compute_trigger_output(512, dz=0.0, max_out=0.50, curve=1.0), 256)
        self.assertEqual(compute_trigger_output(80, dz=0.10, max_out=0.50, curve=1.0), 0)
        self.assertEqual(compute_trigger_output(500, dz=0.0, max_out=0.50, hair_trigger=True), 512)

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "trig_sim.conf"
            cfg = AntiDriftConfig(
                lt_dz=0.05,
                rt_dz=0.02,
                lt_max=0.50,
                rt_max=0.85,
                lt_curve=1.35,
                rt_curve=0.90,
                wheel_shifter_mode="h_shifter",
                wheel_shifter_device="/dev/input/event22",
                wheel_shifter_down_btn="X",
                wheel_shifter_up_btn="Y",
                wheel_handbrake_enabled=True,
                wheel_handbrake_device="/dev/input/event23",
                wheel_handbrake_target="B",
                wheel_handbrake_dz=0.30,
                wheel_handbrake_invert=True,
                wheel_clutch_mode="x_btn",
            )
            save_config(cfg, path=p)
            loaded = load_config(path=p)
            self.assertAlmostEqual(loaded.lt_max, 0.50, places=2)
            self.assertAlmostEqual(loaded.rt_max, 0.85, places=2)
            self.assertAlmostEqual(loaded.lt_curve, 1.35, places=2)
            self.assertEqual(loaded.wheel_shifter_mode, "h_shifter")
            self.assertEqual(loaded.wheel_shifter_device, "/dev/input/event22")
            self.assertEqual(loaded.wheel_shifter_down_btn, "X")
            self.assertEqual(loaded.wheel_shifter_up_btn, "Y")
            self.assertEqual(loaded.wheel_handbrake_device, "/dev/input/event23")
            self.assertEqual(loaded.wheel_handbrake_target, "B")
            self.assertAlmostEqual(loaded.wheel_handbrake_dz, 0.30, places=2)
            self.assertTrue(loaded.wheel_handbrake_invert)
            self.assertEqual(loaded.wheel_clutch_mode, "x_btn")

        profs = {p.id: p for p in default_profiles()}
        self.assertIn("racing_gamepad", profs)
        self.assertAlmostEqual(profs["racing_gamepad"].lt_max, 0.60, places=2)

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        cw = ControllerWidget()
        cw.resize(600, 492)
        cw.set_family_override("xbox")
        cw.set_config_preview(AntiDriftConfig(lt_max=0.50))
        cw.set_simulator_enabled(True)
        scale, ox, oy = cw._transform_params()
        from PySide6.QtCore import QPointF
        lt_center = _SPECS["xbox"].lt_rect.center()
        pt = QPointF(ox + lt_center.x() * scale, oy + lt_center.y() * scale)
        cw.mousePressEvent(
            QMouseEvent(
                QMouseEvent.MouseButtonPress,
                pt,
                pt,
                Qt.LeftButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
        )
        self.assertEqual(cw._state.raw_lt, 1023)
        self.assertEqual(cw._state.lt, 512)
        cw.grab()
        app.processEvents()


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


class EqualizerSubsystemTests(unittest.TestCase):
    def test_eq_config_math_presets_and_roundtrip(self) -> None:
        from antidrift.audio.eq_config import (
            EQ_BAND_FREQS,
            EQ_PRESETS,
            EqualizerConfig,
            biquad_coefficients,
            compute_eq_curve_db,
            effective_preamp_db,
            load_eq_config,
            save_eq_config,
        )

        flat_curve = compute_eq_curve_db([0.0] * 10, 0.0, False, freqs_hz=[31.0, 1000.0, 16000.0])
        self.assertEqual(flat_curve, [0.0, 0.0, 0.0])

        fps_bands = list(EQ_PRESETS["Gaming / Pasos FPS"]["bands"])
        self.assertEqual(effective_preamp_db(fps_bands, 0.0, True), -5.5)
        self.assertEqual(effective_preamp_db(fps_bands, 2.0, False), 2.0)
        self.assertEqual(effective_preamp_db(fps_bands, 2.0, True, bypass=True), 0.0)

        self.assertIn("Motores / Racing", EQ_PRESETS)
        engine_bands = list(EQ_PRESETS["Motores / Racing"]["bands"])
        self.assertGreater(engine_bands[1], 3.0)
        self.assertGreater(engine_bands[7], 3.0)

        bass_bands = list(EQ_PRESETS["Graves Potentes"]["bands"])
        bass_curve = compute_eq_curve_db(bass_bands, 0.0, False, freqs_hz=[31.0, 1000.0, 16000.0])
        self.assertGreater(bass_curve[0], 4.0)
        self.assertGreater(bass_curve[0], bass_curve[1])
        self.assertEqual(
            compute_eq_curve_db(bass_bands, 0.0, False, freqs_hz=[31.0, 1000.0], bypass=True),
            [0.0, 0.0],
        )

        # Unity coefficients when gain is zero
        self.assertEqual(biquad_coefficients("peaking", 1000.0, 1.4, 0.0), (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "eq.json"
            cfg = EqualizerConfig(
                enabled=True,
                bypass=False,
                output_sink="alsa_output.pci-0000_07_00.6.analog-stereo",
                preamp_db=1.5,
                auto_preamp=True,
                balance=-0.35,
                band_gains=[3.5, 2.0, 0.0, -1.5, 0.0, 1.0, 2.5, 4.0, 2.0, 1.5],
                active_preset="Mi EQ",
                custom_presets={"Mi EQ": {"preamp_db": 1.5, "bands": [3.5] + [0.0] * 9}},
                previous_default_sink="alsa_output.pci-0000_07_00.6.analog-stereo",
            )
            save_eq_config(cfg, p)
            loaded = load_eq_config(p)
            self.assertTrue(loaded.enabled)
            self.assertEqual(len(loaded.band_gains), len(EQ_BAND_FREQS))
            self.assertEqual(loaded.balance, -0.35)
            self.assertIn("Mi EQ", loaded.custom_presets)

    def test_eq_pipewire_backend_and_mixer(self) -> None:
        import json
        from unittest import mock
        from antidrift.audio.eq_backend import (
            EQ_PLAYBACK_NAME,
            EQ_SINK_NAME,
            apply_eq_live_params,
            generate_pipewire_eq_conf,
            list_physical_sinks,
            list_sink_inputs,
            set_sink_volume_and_balance,
        )
        from antidrift.audio.eq_config import EqualizerConfig

        cfg = EqualizerConfig(
            enabled=True,
            preamp_db=0.0,
            auto_preamp=True,
            band_gains=[4.0, 2.0, 0.0, 0.0, 0.0, 0.0, 1.0, 3.0, 2.0, 0.0],
        )
        conf_txt = generate_pipewire_eq_conf(cfg, "alsa_output.test")
        self.assertIn("libpipewire-module-filter-chain", conf_txt)
        self.assertIn('label = bq_lowshelf', conf_txt)
        self.assertIn('label = bq_peaking', conf_txt)
        self.assertIn('label = bq_highshelf', conf_txt)
        self.assertIn(f'node.name        = "{EQ_SINK_NAME}"', conf_txt)
        self.assertIn('target.object = "alsa_output.test"', conf_txt)
        self.assertIn('label = bq_lowshelf control = { "Freq" = 31.0 "Q" = 0.707 "Gain" = 4.00 }', conf_txt)
        # Auto preamp = -4.0 dB => Mult ~ 0.630957
        self.assertIn('"Mult" = 0.630957', conf_txt)
        # Stereo Mid/Side + Haas nodes present (off by default -> raw passthrough)
        self.assertIn('name = st_haas label = delay config = { "max-delay" = 1 }', conf_txt)
        self.assertIn('"Gain 1" = 0.0000 "Gain 2" = 0.0000 "Gain 3" = 1.0000', conf_txt)

        from antidrift.audio.eq_config import compute_stereo_dsp_params

        st_on = compute_stereo_dsp_params(True, 0.5, bypass=False)
        self.assertEqual(st_on["side_gain"], 1.75)
        self.assertEqual(st_on["haas_delay_ms"], 10.0)
        self.assertEqual(st_on["raw_side_gain"], 0.0)
        self.assertEqual(st_on["exciter_16k_db"], 5.0)
        cfg_st = cfg.copy()
        cfg_st.stereo_enabled = True
        cfg_st.stereo_width = 0.5
        st_txt = generate_pipewire_eq_conf(cfg_st, "alsa_output.test")
        self.assertIn('"Gain 1" = 1.7500', st_txt)
        self.assertIn('"Delay (s)" = 0.0100', st_txt)

        # Bypass zeroes gains & preamp multiplier cleanly
        cfg_bp = cfg.copy()
        cfg_bp.bypass = True
        bp_txt = generate_pipewire_eq_conf(cfg_bp, "alsa_output.test")
        self.assertIn('"Mult" = 1.000000', bp_txt)
        self.assertIn('"Gain" = 0.00', bp_txt)

        fake_sinks_json = json.dumps([
            {
                "name": EQ_SINK_NAME,
                "description": "Anti-Drift Studio EQ",
                "mute": False,
                "volume": {"front-left": {"value_percent": "100%"}},
                "balance": 0.0,
                "properties": {"device.description": "Anti-Drift Studio EQ"},
                "ports": [],
            },
            {
                "name": "alsa_output.test_hw",
                "description": "(null)",
                "mute": False,
                "volume": {"front-left": {"value_percent": "80%"}, "front-right": {"value_percent": "80%"}},
                "balance": 0.0,
                "properties": {"device.description": "Ryzen HD Audio"},
                "active_port": "analog-output-headphones",
                "ports": [{"name": "analog-output-headphones", "description": "Auriculares"}],
            },
        ])
        fake_inputs_json = json.dumps([
            {
                "index": 10,
                "sink": 1,
                "mute": False,
                "volume": {"front-left": {"value_percent": "100%"}},
                "properties": {"node.name": EQ_PLAYBACK_NAME, "media.name": "Anti-Drift Studio EQ"},
            },
            {
                "index": 22,
                "sink": 1,
                "mute": False,
                "volume": {"front-left": {"value_percent": "72%"}},
                "properties": {"application.name": "Spotify", "application.process.binary": "spotify"},
            },
        ])

        def _fake_run(args, timeout=4.0):
            if args == ["pactl", "get-default-sink"]:
                return 0, "alsa_output.test_hw", ""
            if args == ["pactl", "-f", "json", "list", "sinks"]:
                return 0, fake_sinks_json, ""
            if args == ["pactl", "-f", "json", "list", "sink-inputs"]:
                return 0, fake_inputs_json, ""
            return 0, "", ""

        with mock.patch("antidrift.audio.eq_backend._run", side_effect=_fake_run):
            phys = list_physical_sinks()
            self.assertEqual(len(phys), 1)
            self.assertEqual(phys[0].name, "alsa_output.test_hw")
            self.assertEqual(phys[0].description, "Ryzen HD Audio · Auriculares")
            self.assertEqual(phys[0].volume_pct, 80)

            streams = list_sink_inputs()
            self.assertEqual(len(streams), 1)
            self.assertEqual(streams[0].index, 22)
            self.assertEqual(streams[0].name, "Spotify")

        calls: list[list[str]] = []
        with mock.patch(
            "antidrift.audio.eq_backend._run",
            side_effect=lambda a, timeout=4.0: (calls.append(a) or (0, "", "")),
        ):
            set_sink_volume_and_balance("sink_x", 100, -0.5)
            set_sink_volume_and_balance("sink_x", 80, 0.5)
        self.assertEqual(calls[0], ["pactl", "set-sink-volume", "sink_x", "100%", "50%"])
        self.assertEqual(calls[1], ["pactl", "set-sink-volume", "sink_x", "40%", "80%"])

        pw_calls: list[list[str]] = []
        pw_dump_out = json.dumps([
            {"id": 99, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": EQ_SINK_NAME}}}
        ])

        def _fake_pw(args, timeout=4.0):
            pw_calls.append(args)
            if args == ["pw-dump"]:
                return 0, pw_dump_out, ""
            return 0, "", ""

        with mock.patch("antidrift.audio.eq_backend._run", side_effect=_fake_pw):
            ok_live = apply_eq_live_params(cfg)
        self.assertTrue(ok_live)
        self.assertEqual(pw_calls[-1][:4], ["pw-cli", "s", "99", "Props"])
        self.assertIn('"b1:Gain" 4.00', pw_calls[-1][4])

        # Verify EQ monitor is treated as virtual (prevents clip echo on Wayland & X11)
        from antidrift.clips.recorder import is_virtual_audio_monitor
        from antidrift.clips.hotkey_listener import GlobalHotkeyThread
        from antidrift.audio.eq_backend import _bands_10_to_mbeq15, start_or_update_eq

        self.assertTrue(is_virtual_audio_monitor(f"{EQ_SINK_NAME}.monitor"))
        self.assertEqual(len(_bands_10_to_mbeq15(cfg.band_gains)), 15)

        # Verify PulseAudio LADSPA fallback on legacy X11 sessions without PipeWire
        pa_cmds: list[list[str]] = []

        def _fake_pa_run(args, timeout=4.0):
            pa_cmds.append(args)
            if args == ["pactl", "list", "short", "sinks"]:
                return 0, f"1\t{EQ_SINK_NAME}\tmodule-ladspa-sink\tfloat32le\tRUNNING\n", ""
            return 0, "", ""

        with (
            mock.patch("antidrift.audio.eq_backend._run", side_effect=_fake_pa_run),
            mock.patch("antidrift.audio.eq_backend.shutil.which", side_effect=lambda b: "/usr/bin/pactl" if b == "pactl" else None),
            mock.patch("antidrift.audio.eq_backend.is_pipewire_session", return_value=False),
            mock.patch("antidrift.audio.eq_backend._has_ladspa_mbeq", return_value=True),
            mock.patch("antidrift.audio.eq_backend.resolve_target_sink", return_value="alsa_output.test_hw"),
            mock.patch("antidrift.audio.eq_backend.eq_sink_exists", return_value=True),
        ):
            pa_st = start_or_update_eq(cfg)
        self.assertTrue(pa_st.active)
        self.assertEqual(pa_st.backend, "pulseaudio")
        self.assertTrue(any("module-ladspa-sink" in c for c in pa_cmds))

        # Verify GlobalHotkeyThread pure-X11 (non-KDE X11) triggers XQueryKeymap primary
        x11_direct: list[bool] = []
        th_x11 = GlobalHotkeyThread("Alt+Shift+C")
        with (
            mock.patch.dict(os.environ, {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11", "WAYLAND_DISPLAY": ""}, clear=False),
            mock.patch.object(th_x11, "_run_ipc_server", return_value=None),
            mock.patch.object(th_x11, "_readable_keyboards", return_value=[]),
            mock.patch.object(th_x11, "_kde_kglobalaccel_available", return_value=False),
            mock.patch.object(th_x11, "_run_kde_globalaccel_loop", return_value=False),
            mock.patch.object(th_x11, "_run_x11_loop", side_effect=lambda emit_status=True: x11_direct.append(emit_status) or True),
        ):
            th_x11.run()
        self.assertEqual(x11_direct, [True])

    def test_eq_tab_ui_and_main_window_integration(self) -> None:
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QApplication
        from antidrift.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            old_xdg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmp
            try:
                win = MainWindow(start_reader=False, enable_tray=False)
                win.resize(1180, 770)
                win._main_tabs.setCurrentIndex(win._eq_tab_idx)
                app.processEvents()

                eq_tab = win._eq_tab
                eq_tab._apply_preset_by_name("Graves Potentes")
                self.assertEqual(eq_tab._config.active_preset, "Graves Potentes")
                self.assertEqual(eq_tab._band_sliders[0].value(), 65)
                self.assertEqual(eq_tab._preamp_lbl.text(), "-6.5")

                # Manual band change updates preset to Personalizado and reset restores 0.0 dB
                eq_tab._band_sliders[2].setValue(55)
                self.assertEqual(eq_tab._config.band_gains[2], 5.5)
                self.assertEqual(eq_tab._config.active_preset, "Personalizado")
                eq_tab._band_sliders[2].reset_requested.emit()
                self.assertEqual(eq_tab._config.band_gains[2], 0.0)

                # Balance readout
                eq_tab._bal_slider.setValue(-40)
                self.assertEqual(eq_tab._bal_lbl.text(), "I 40%")
                eq_tab._bal_slider.reset_requested.emit()
                self.assertEqual(eq_tab._bal_lbl.text(), "Centro")

                # Stereo Amplification quick pill auto-enables both stereo and DSP
                eq_tab._config.enabled = False
                eq_tab._config.stereo_enabled = False
                eq_tab._apply_stereo_quick_pct(80)
                self.assertTrue(eq_tab._config.stereo_enabled)
                self.assertTrue(eq_tab._config.enabled)
                self.assertEqual(eq_tab._config.stereo_width, 0.8)
                self.assertIn("80%", eq_tab._stereo_val_lbl.text())

                # Non-disruptive in-place app mixer auto-refresh
                from antidrift.audio.eq_backend import AudioAppStream

                eq_tab.refresh_app_streams([
                    AudioAppStream(index=22, name="Spotify", app_binary="spotify", sink_id="68", volume_pct=16, muted=False)
                ])
                self.assertIn(22, eq_tab._app_rows)
                sl_spot = eq_tab._app_rows[22][3]
                self.assertEqual(sl_spot.value(), 16)

                # External update updates slider in-place without recreating widget
                eq_tab.refresh_app_streams([
                    AudioAppStream(index=22, name="Spotify", app_binary="spotify", sink_id="68", volume_pct=48, muted=False),
                    AudioAppStream(index=30, name="Discord", app_binary="Discord", sink_id="68", volume_pct=90, muted=False),
                ])
                self.assertIs(eq_tab._app_rows[22][3], sl_spot)
                self.assertEqual(sl_spot.value(), 48)
                self.assertIn(30, eq_tab._app_rows)

                # While user is actively dragging slider, external refresh does not yank it
                sl_spot.setSliderDown(True)
                sl_spot.setValue(60)
                eq_tab.refresh_app_streams([
                    AudioAppStream(index=22, name="Spotify", app_binary="spotify", sink_id="68", volume_pct=20, muted=False),
                ])
                self.assertEqual(sl_spot.value(), 60)
                self.assertNotIn(30, eq_tab._app_rows)
                sl_spot.setSliderDown(False)

                # Render tab cleanly offscreen
                img = QImage(QSize(1180, 770), QImage.Format_ARGB32)
                win.render(img)
                self.assertFalse(img.isNull())
                win.close()
            finally:
                if old_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old_xdg


class MangoHudAndOptionalDepsTests(unittest.TestCase):
    def test_optional_deps_distro_commands_and_installer(self) -> None:
        from unittest import mock
        from antidrift.deps import (
            DistroInfo,
            check_optional_dependencies,
            install_optional_dependency,
        )

        for mgr, expected_pkg in (
            ("apt", "mangohud"),
            ("pacman", "mangohud"),
            ("dnf", "mangohud"),
            ("zypper", "mangohud"),
            ("xbps", "MangoHud"),
            ("apk", "mangohud"),
        ):
            items = {
                d.id: d
                for d in check_optional_dependencies(DistroInfo(mgr, f"Test {mgr}", mgr))
            }
            self.assertIn("mangohud", items)
            self.assertIn(expected_pkg, items["mangohud"].packages)
            self.assertIn(expected_pkg, items["mangohud"].install_cmd)

        ran_cmds: list[list[str]] = []

        def _fake_elev(cmd, _log):
            ran_cmds.append(cmd)
            return True

        with (
            mock.patch(
                "antidrift.deps.detect_distro",
                return_value=DistroInfo("arch", "Arch Linux", "pacman"),
            ),
            mock.patch("antidrift.deps._run_elevated", side_effect=_fake_elev),
        ):
            ok, _msg = install_optional_dependency("mangohud")
        self.assertTrue(ok)
        self.assertEqual(ran_cmds, [["pacman", "-S", "--needed", "--noconfirm", "mangohud"]])

    def test_mangohud_config_sync_and_process_resolution(self) -> None:
        from antidrift.mangohud.mh_backend import (
            extract_process_name_from_cmdline,
            sync_mangohud_system_files,
        )
        from antidrift.mangohud.mh_config import (
            MangoHudConfig,
            MangoHudWindowRule,
            apply_mangohud_preset,
            load_mangohud_app_config,
            render_mangohud_conf,
            save_mangohud_app_config,
        )

        self.assertEqual(
            extract_process_name_from_cmdline(["S:\\Games\\fh2\\azimuth.exe", "--game=fh2"], "/usr/bin/wine64-preloader"),
            "azimuth.exe",
        )
        self.assertEqual(
            extract_process_name_from_cmdline(["/usr/bin/vkcube"], "/usr/bin/vkcube"),
            "vkcube",
        )

        cfg = apply_mangohud_preset(MangoHudConfig(), "Barra Horizontal")
        self.assertTrue(cfg.horizontal)
        self.assertTrue(cfg.fps)
        cfg.session_mode = "selective"
        cfg.fps_limit = 60
        cfg.window_rules = [
            MangoHudWindowRule(
                id="r1",
                title="Forza Horizon 2",
                process_name="azimuth.exe",
                enabled=True,
                override_position="top-right",
                override_fps_limit=60,
            ),
            MangoHudWindowRule(
                id="r2",
                title="Desactivado",
                process_name="glxgears",
                enabled=False,
            ),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_json = root / "mh.json"
            mh_dir = root / "MangoHud"
            env_file = root / "env" / "xbox-antidrift-mangohud.conf"

            save_mangohud_app_config(cfg, app_json)
            loaded = load_mangohud_app_config(app_json)
            self.assertEqual(loaded.session_mode, "selective")
            self.assertEqual(len(loaded.window_rules), 2)
            self.assertEqual(loaded.window_rules[0].override_position, "top-right")

            sync_mangohud_system_files(loaded, mh_dir=mh_dir, env_file=env_file)
            main_txt = (mh_dir / "MangoHud.conf").read_text(encoding="utf-8")
            az_txt = (mh_dir / "azimuth.exe.conf").read_text(encoding="utf-8")
            wine_az_txt = (mh_dir / "wine-azimuth.conf").read_text(encoding="utf-8")
            self.assertIn("no_display\n", main_txt)
            self.assertIn("no_display=0\n", az_txt)
            self.assertIn("no_display=0\n", wine_az_txt)
            self.assertIn("position=top-right\n", az_txt)
            self.assertIn("fps_limit=60\n", az_txt)
            self.assertFalse((mh_dir / "glxgears.conf").exists())
            self.assertEqual(env_file.read_text(encoding="utf-8"), "MANGOHUD=1\n")

            # Removing/disabling a managed rule cleans up all managed .conf aliases
            loaded.window_rules[0].enabled = False
            loaded.session_mode = "off"
            sync_mangohud_system_files(loaded, mh_dir=mh_dir, env_file=env_file)
            self.assertFalse((mh_dir / "azimuth.exe.conf").exists())
            self.assertFalse((mh_dir / "wine-azimuth.conf").exists())
            self.assertFalse((mh_dir / "wine-azimuth.exe.conf").exists())
            self.assertFalse(env_file.exists())

            rendered = render_mangohud_conf(apply_mangohud_preset(MangoHudConfig(), "Sim Racing / Debug"))
            self.assertIn("gpu_power", rendered)
            self.assertIn("cpu_power", rendered)

    def test_mangohud_tab_ui_and_main_window_integration(self) -> None:
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QApplication
        from antidrift.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            old_xdg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmp
            try:
                win = MainWindow(start_reader=False, enable_tray=False)
                win.resize(1180, 770)
                win._main_tabs.setCurrentIndex(win._mh_tab_idx)
                app.processEvents()

                mh_tab = win._mangohud_tab
                self.assertTrue(mh_tab._advanced_panel.isHidden())
                mh_tab._adv_toggle_btn.setChecked(True)
                self.assertFalse(mh_tab._advanced_panel.isHidden())
                mh_tab._adv_toggle_btn.setChecked(False)
                self.assertTrue(mh_tab._advanced_panel.isHidden())
                mh_tab._on_preset_clicked("Sim Racing / Debug")
                self.assertTrue(mh_tab._config.gpu_power)
                self.assertTrue(mh_tab._config.cpu_power)

                mh_tab._on_position_selected("bottom-right")
                self.assertEqual(mh_tab._config.position, "bottom-right")
                mh_tab._preview._on_tick()
                lines_preview = mh_tab._preview._hud_lines()
                self.assertTrue(any(k == "GPU" for k, _, _ in lines_preview))
                self.assertTrue(any(k == "GAMEMODE" for k, _, _ in lines_preview))
                self.assertTrue(any(k == "HORA" for k, _, _ in lines_preview))
                mh_tab._preview.hide()

                mh_tab._add_or_enable_rule("Forza Horizon 2", "azimuth.exe", "")
                self.assertEqual(len(mh_tab._config.window_rules), 1)
                self.assertEqual(mh_tab._rules_container.count(), 1)
                from unittest import mock
                from antidrift.mangohud.mh_backend import DetectedWindow

                fake_open = [
                    DetectedWindow(
                        pid=99999,
                        title="Azimuth",
                        process_name="azimuth.exe",
                        exec_cmd="",
                        api_hint="Vulkan",
                        hud_loaded=False,
                    )
                ]
                with mock.patch("antidrift.mangohud.mh_tab.discover_open_windows", return_value=fake_open):
                    mh_tab.save_and_apply()
                self.assertTrue((Path(tmp) / "MangoHud" / "azimuth.exe.conf").exists())
                self.assertTrue((Path(tmp) / "MangoHud" / "wine-azimuth.conf").exists())
                self.assertIn("azimuth.exe", mh_tab._save_status_lbl.text())
                self.assertIn("sin MangoHud", mh_tab._save_status_lbl.text())

                img = QImage(QSize(1180, 770), QImage.Format_ARGB32)
                win.render(img)
                self.assertFalse(img.isNull())
                self.assertGreater(win._opt_deps_rows.count(), 0)
                win.close()
            finally:
                if old_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old_xdg


class GameSirExtraButtonsTests(unittest.TestCase):
    def test_gamesir_uses_xbox_family_and_two_extra_buttons(self) -> None:
        from antidrift.evdev_reader import (
            ControllerState,
            default_extra_keys_for_device,
            detect_controller_family,
        )
        from antidrift.widgets.controller_widget import ControllerWidget
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QApplication

        self.assertEqual(detect_controller_family("Generic X-Box pad", "3537:1098"), "xbox")
        self.assertEqual(detect_controller_family("GameSir-Nova 2 Lite", "3537:100f"), "xbox")
        self.assertEqual(default_extra_keys_for_device("Generic X-Box pad", "3537:1098"), [704, 705])
        self.assertEqual(default_extra_keys_for_device("GameSir-Nova 2 Lite", "3537:100f"), [704, 705])
        self.assertEqual(default_extra_keys_for_device("Xbox Wireless Controller", "045e:0b12"), [])

        app = QApplication.instance() or QApplication([])
        preview = ControllerWidget()
        preview.resize(620, 480)
        preview.set_controller_state(
            ControllerState(
                mode="direct",
                controller_family="xbox",
                physical_name="Generic X-Box pad",
                physical_vid_pid="3537:1098",
                supported_extra_keys=[704, 705],
                extra_buttons={704: False, 705: True},
                battery_level=52,
                battery_charging=False,
            )
        )
        rects = preview._extra_badge_rects()
        self.assertEqual([code for code, _ in rects], [704, 705])
        img = QImage(QSize(620, 480), QImage.Format_ARGB32)
        preview.render(img)
        self.assertFalse(img.isNull())
        preview.set_controller_state(
            ControllerState(
                mode="direct",
                controller_family="xbox",
                battery_level=100,
                battery_charging=True,
            )
        )
        preview.render(img)
        app.processEvents()

    def test_main_window_has_no_dedicated_gamesir_tab(self) -> None:
        from PySide6.QtWidgets import QApplication
        from antidrift.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            old_xdg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmp
            try:
                win = MainWindow(start_reader=False, enable_tray=False)
                tab_titles = [
                    win._main_tabs.tabText(i) for i in range(win._main_tabs.count())
                ]
                self.assertNotIn("GameSir", tab_titles)
                self.assertFalse(hasattr(win, "_gamesir_tab"))
                app.processEvents()
                win.close()
            finally:
                if old_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old_xdg


    def test_battery_charging_indicator_and_sound_transitions(self) -> None:
        from unittest.mock import patch
        from PySide6.QtWidgets import QApplication
        from antidrift.clips.sound_fx import (
            ensure_battery_sound_assets,
            ensure_connection_sound_assets,
        )
        from antidrift.evdev_reader import ControllerState, read_controller_battery
        from antidrift.main_window import MainWindow

        conn_assets = ensure_connection_sound_assets()
        for key in ("connected", "disconnected"):
            self.assertIn(key, conn_assets)
            self.assertTrue(conn_assets[key].exists())
            self.assertGreater(conn_assets[key].stat().st_size, 100)

        with tempfile.TemporaryDirectory() as ps_tmp:
            ps_root = Path(ps_tmp)
            sys_bat = ps_root / "BAT0"
            sys_bat.mkdir()
            (sys_bat / "type").write_text("Battery\n", encoding="utf-8")
            (sys_bat / "scope").write_text("System\n", encoding="utf-8")
            (sys_bat / "capacity").write_text("90\n", encoding="utf-8")

            xb_bat = ps_root / "hid-00:11:22:33:44:55-battery"
            xb_bat.mkdir()
            (xb_bat / "type").write_text("Battery\n", encoding="utf-8")
            (xb_bat / "scope").write_text("Device\n", encoding="utf-8")
            (xb_bat / "model_name").write_text("Xbox Wireless Controller\n", encoding="utf-8")
            (xb_bat / "status").write_text("Discharging\n", encoding="utf-8")
            (xb_bat / "capacity_level").write_text("Low\n", encoding="utf-8")

            self.assertEqual(
                read_controller_battery(
                    "Xbox Wireless Controller", "045e:0b13", power_supply_root=ps_root
                ),
                (15, False),
            )
            (xb_bat / "capacity_level").write_text("Normal\n", encoding="utf-8")
            self.assertEqual(
                read_controller_battery(
                    "Xbox Wireless Controller", "045e:0b13", power_supply_root=ps_root
                ),
                (50, False),
            )

        assets = ensure_battery_sound_assets()
        for key in ("charging", "unplugged", "full", "low"):
            self.assertIn(key, assets)
            self.assertTrue(assets[key].exists())
            self.assertGreater(assets[key].stat().st_size, 100)

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            old_xdg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmp
            try:
                win = MainWindow(start_reader=False, enable_tray=False)
                events: list[str] = []
                with (
                    patch("antidrift.main_window.play_battery_charging_chime", side_effect=lambda: events.append("charging")),
                    patch("antidrift.main_window.play_battery_unplugged_chime", side_effect=lambda: events.append("unplugged")),
                    patch("antidrift.main_window.play_battery_full_chime", side_effect=lambda: events.append("full")),
                    patch("antidrift.main_window.play_battery_low_chime", side_effect=lambda: events.append("low")),
                ):
                    def feed(lvl: int, chg: bool) -> None:
                        win._check_battery_sounds(
                            ControllerState(
                                physical_stable_id="pad1",
                                battery_level=lvl,
                                battery_charging=chg,
                            )
                        )

                    feed(60, False)
                    self.assertEqual(events, [])
                    feed(18, False)
                    feed(15, False)  # should not spam low alert twice
                    feed(15, True)   # plugged in
                    feed(100, True)  # reached 100%
                    feed(100, True)  # should not spam full alert twice
                    feed(100, False) # unplugged
                    self.assertEqual(events, ["low", "charging", "full", "unplugged"])

                conn_events: list[str] = []
                with (
                    patch("antidrift.main_window.play_controller_connected_chime", side_effect=lambda: conn_events.append("connected")),
                    patch("antidrift.main_window.play_controller_disconnected_chime", side_effect=lambda: conn_events.append("disconnected")),
                ):
                    win._check_connection_sounds(ControllerState(mode="direct"))
                    win._check_connection_sounds(ControllerState(mode="disconnected"))
                    win._check_connection_sounds(ControllerState(mode="disconnected"))
                    win._check_connection_sounds(ControllerState(mode="virtual"))
                    self.assertEqual(conn_events, ["disconnected", "connected"])
                app.processEvents()
                win.close()
            finally:
                if old_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old_xdg


if __name__ == "__main__":
    unittest.main(verbosity=2)
