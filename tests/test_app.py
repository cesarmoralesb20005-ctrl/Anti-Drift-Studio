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
    AntiDriftConfig,
    apply_axial_deadzone,
    apply_hair_trigger,
    compute_corrected_stick,
    deadzone_raw_bounds,
    load_config,
    normalize_axis,
    save_config,
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
                device_path="/dev/input/event12",
            )
            save_config(cfg, path)
            loaded = load_config(path)
            self.assertEqual(loaded, cfg)

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
        with tempfile.TemporaryDirectory() as tmp:
            tmp_clips_cfg = Path(tmp) / "clips_test.json"
            clips_tab_mod.save_clips_config = lambda cfg, path=tmp_clips_cfg: orig_save(cfg, tmp_clips_cfg)
            clips_tab_mod.load_clips_config = lambda path=tmp_clips_cfg: orig_load(tmp_clips_cfg)
            try:
                self._run_main_window_test_body()
            finally:
                clips_tab_mod.save_clips_config = orig_save
                clips_tab_mod.load_clips_config = orig_load

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
