"""Independent AppImage, play and native compilation operations."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from wrapper import core, pr_build


def installed_game(root):
    source = root / "root"
    (source / "exefs").mkdir(parents=True)
    (source / "romfs").mkdir()
    (source / "exefs/main").write_bytes(b"NSO0")
    (source / "exefs/main.npdm").write_bytes(b"META")
    (root / "game/save").mkdir(parents=True)
    (root / "game/save/slot.sav").write_bytes(b"do-not-touch")
    (root / "game/swgame.so").write_bytes(b"existing-native")
    (root / "game/swgame.so.id").write_bytes(b"existing-id")


def result_image(root, name, digest, commit=None, selection=None):
    path = root / name
    path.write_bytes(b"\x7fELF" + b"runtime" * 100)
    st = path.stat()
    return {"path": path, "digest": digest, "size": st.st_size,
            "mtime_ns": st.st_mtime_ns, "commit": commit,
            "selection": selection}


class SeparateOperationsTests(unittest.TestCase):
    def test_appimage_only_does_not_touch_existing_native_code_or_save(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            installed_game(root)
            core.write_state(root, {"built_digest": "old-id", "built_cpu": core.cpu_target()})
            binary = result_image(root, "SavageWind-test.AppImage", "a" * 64, "b" * 40)
            events = []
            questions = []
            def consent(message):
                questions.append(message)
                return "Recompile the game's native library" not in message
            with mock.patch.object(pr_build, "build_test_appimage", return_value=binary) as build:
                with mock.patch.object(core, "build_game", side_effect=AssertionError("native build")):
                    with mock.patch.object(core, "portable_env", side_effect=AssertionError("game env")):
                        with mock.patch.object(core.subprocess, "Popen",
                                               side_effect=AssertionError("game launched")):
                            response = core.Pipeline(
                                root, lambda *x: events.append(x), consent,
                                threading.Event(), prs="test", mode="build_only").run()
            self.assertEqual(response, "built")
            self.assertEqual(len(questions), 2)
            self.assertIn("Choose No", questions[-1])
            self.assertFalse(any(x[0] == "native-start" for x in events))
            build.assert_called_once()
            self.assertEqual((root / "game/swgame.so").read_bytes(), b"existing-native")
            self.assertEqual((root / "game/save/slot.sav").read_bytes(), b"do-not-touch")
            self.assertTrue(core.valid_game(root / "root"))
            self.assertEqual(core.read_state(root)["built_digest"], "old-id")
            self.assertEqual(core.read_state(root)["runtime_source"], "test")
            self.assertTrue(any(e[0] == "source-finish" for e in events))

    def test_appimage_only_does_not_require_imported_game(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            built = result_image(root, "SavageWind-test.AppImage", "c" * 64, "d" * 40)
            with mock.patch.object(pr_build, "build_test_appimage", return_value=built):
                response = core.Pipeline(root, lambda *_: None, lambda _: True,
                                         threading.Event(), prs="test", mode="build_only").run()
            self.assertEqual(response, "built")
            self.assertFalse((root / "root").exists())
            self.assertFalse((root / "game").exists())

    def test_accept_post_build_recompile_uses_new_appimage_and_keeps_saves(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            installed_game(root)
            core.write_state(root, {"built_digest": "old-id", "built_cpu": core.cpu_target()})
            binary = result_image(root, "SavageWind-test.AppImage", "a" * 64, "b" * 40)
            events = []
            questions = []
            def accept(message):
                questions.append(message)
                return True
            def native(root_, executable, emit, cancel):
                self.assertEqual(executable, binary["path"])
                self.assertEqual((root_ / "game/save/slot.sav").read_bytes(), b"do-not-touch")
                (root_ / "game/swgame.so").write_bytes(b"new-native")
                (root_ / "game/swgame.so.id").write_bytes(b"new-id")
            with mock.patch.object(pr_build, "build_test_appimage", return_value=binary):
                with mock.patch.object(core, "build_game", side_effect=native) as rebuild:
                    with mock.patch.object(core.subprocess, "Popen",
                                           side_effect=AssertionError("game must not auto-launch")):
                        response = core.Pipeline(
                            root, lambda *x: events.append(x), accept,
                            threading.Event(), prs="test", mode="build_only").run()
            self.assertEqual(response, "native-built")
            self.assertEqual(len(questions), 2)
            self.assertIn("Recompile the game's native library", questions[-1])
            self.assertIn("Saves, settings and extracted game files", questions[-1])
            rebuild.assert_called_once()
            self.assertEqual((root / "game/swgame.so").read_bytes(), b"new-native")
            self.assertEqual((root / "game/swgame.so.id").read_bytes(), b"new-id")
            self.assertEqual((root / "game/save/slot.sav").read_bytes(), b"do-not-touch")
            self.assertEqual(core.read_state(root)["built_digest"], "a" * 64)
            stages = [event[0] for event in events]
            self.assertLess(stages.index("source-finish"), stages.index("native-start"))

    def test_accept_post_pr_build_uses_custom_appimage(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            installed_game(root)
            selection = [{"number": 636, "sha": "c" * 40}]
            binary = result_image(root, "SavageWind-custom.AppImage", "f" * 64,
                                  selection=pr_build.selection_key(selection))
            questions = []
            def accept(message):
                questions.append(message)
                return True
            with mock.patch.object(pr_build, "build_pr_appimage", return_value=binary):
                with mock.patch.object(core, "build_game") as native:
                    response = core.Pipeline(
                        root, lambda *_: None, accept, threading.Event(),
                        prs=selection, mode="build_only").run()
            self.assertEqual(response, "native-built")
            native.assert_called_once()
            self.assertEqual(native.call_args.args[:2], (root, binary["path"]))
            self.assertEqual(len(questions), 2)
            self.assertIn("Recompile", questions[-1])
            self.assertEqual(core.read_state(root)["runtime_source"], "custom")
            self.assertEqual(core.read_state(root)["built_digest"], "f" * 64)
            self.assertEqual((root / "game/save/slot.sav").read_bytes(), b"do-not-touch")

    def test_no_game_files_never_asks_for_native_recompile(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            built = result_image(root, "SavageWind-test.AppImage", "c" * 64, "d" * 40)
            questions = []
            messages = []
            def accept(message):
                questions.append(message)
                return True
            with mock.patch.object(pr_build, "build_test_appimage", return_value=built):
                response = core.Pipeline(root, lambda *m: messages.append(m), accept,
                                         threading.Event(), prs="test",
                                         mode="build_only").run()
            self.assertEqual(response, "built")
            self.assertEqual(len(questions), 1)
            self.assertFalse((root / "game").exists())
            self.assertTrue(any("was not offered" in e[1] for e in messages if e[0] == "log"))

    def test_failed_optional_native_build_retains_successful_appimage(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            installed_game(root)
            core.write_state(root, {"built_digest": "original"})
            built = result_image(root, "SavageWind-test.AppImage", "d" * 64, "e" * 40)
            with mock.patch.object(pr_build, "build_test_appimage", return_value=built):
                with mock.patch.object(core, "build_game",
                                       side_effect=core.InstallError("native failed")):
                    with self.assertRaisesRegex(core.InstallError, "native failed"):
                        core.Pipeline(root, lambda *_: None, lambda _: True,
                                      threading.Event(), prs="test",
                                      mode="build_only").run()
            self.assertTrue(built["path"].is_file())
            self.assertEqual(core.read_state(root)["test_digest"], "d" * 64)
            self.assertEqual(core.read_state(root)["built_digest"], "original")
            self.assertEqual((root / "game/swgame.so").read_bytes(), b"existing-native")
            self.assertEqual((root / "game/save/slot.sav").read_bytes(), b"do-not-touch")

    def test_manual_official_appimage_reuses_release_and_asks_before_native(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            installed_game(root)
            binary = result_image(root, "SavageWind-official.AppImage", "e" * 64)
            core.write_state(root, {
                "runtime_source": "official", "official_digest": "e" * 64,
                "official_size": binary["size"],
                "official_mtime_ns": binary["mtime_ns"],
                "built_digest": "old-native", "built_cpu": core.cpu_target(),
            })
            latest = {"tag": "v2", "digest": "e" * 64}
            asked = []
            def decline(message):
                asked.append(message)
                return False
            with mock.patch.object(core, "fetch_latest", return_value=latest):
                with mock.patch.object(core, "build_game",
                                       side_effect=AssertionError("unexpected native rebuild")):
                    with mock.patch.object(core.subprocess, "Popen",
                                           side_effect=AssertionError("unexpected launch")):
                        response = core.Pipeline(
                            root, lambda *_: None, decline, threading.Event(),
                            prs=[], mode="build_only").run()
            self.assertEqual(response, "built")
            self.assertEqual(len(asked), 1)
            self.assertIn("Recompile the game's native library", asked[0])
            self.assertEqual((root / "game/swgame.so").read_bytes(), b"existing-native")
            self.assertEqual((root / "game/save/slot.sav").read_bytes(), b"do-not-touch")

    def test_explicit_native_build_may_compile_test_first(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            installed_game(root)
            built = result_image(root, "SavageWind-test.AppImage", "b" * 64, "c" * 40)
            events = []
            def fake_native(root_, executable, emit, cancel):
                self.assertEqual(executable, built["path"])
                (root_ / "game/swgame.so").write_bytes(b"new-native")
            with mock.patch("wrapper.migration.legacy_folder", return_value=None):
                with mock.patch.object(pr_build, "build_test_appimage", return_value=built):
                    with mock.patch.object(core, "build_game", side_effect=fake_native) as native:
                        with mock.patch.object(core.subprocess, "Popen",
                                               side_effect=AssertionError("game started")):
                            response = core.Pipeline(
                                root, lambda *x: events.append(x), lambda _: True,
                                threading.Event(), prs="test", mode="native").run("__reuse__")
            self.assertEqual(response, "native-built")
            native.assert_called_once()
            self.assertEqual((root / "game/swgame.so").read_bytes(), b"new-native")
            self.assertEqual((root / "game/save/slot.sav").read_bytes(), b"do-not-touch")
            self.assertEqual(core.read_state(root)["built_digest"], "b" * 64)
            self.assertTrue(any(x[0] == "native-start" for x in events))

    def test_explicit_native_build_may_apply_selected_prs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            installed_game(root)
            requested = [{"number": 636, "sha": "c" * 40}]
            result = result_image(root, "SavageWind-custom.AppImage", "f" * 64,
                                  selection=pr_build.selection_key(requested))
            with mock.patch("wrapper.migration.legacy_folder", return_value=None):
                with mock.patch.object(pr_build, "build_pr_appimage",
                                       return_value=result) as build:
                    with mock.patch.object(core, "build_game") as native:
                        response = core.Pipeline(
                            root, lambda *_: None, lambda _: True,
                            threading.Event(), prs=requested, mode="native").run("__reuse__")
            self.assertEqual(response, "native-built")
            build.assert_called_once()
            native.assert_called_once()
            self.assertEqual(core.read_state(root)["runtime_source"], "custom")
            self.assertEqual(core.read_state(root)["built_digest"], "f" * 64)

    def test_native_without_test_or_pr_selects_official(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            installed_game(root)
            binary = result_image(root, "SavageWind-official.AppImage", "e" * 64)
            core.write_state(root, {
                "official_digest": "e" * 64,
                "official_size": binary["size"],
                "official_mtime_ns": binary["mtime_ns"],
                "runtime_source": "official",
            })
            with mock.patch("wrapper.migration.legacy_folder", return_value=None):
                with mock.patch.object(core, "fetch_latest",
                                       side_effect=AssertionError("unneeded download")):
                    with mock.patch.object(core, "build_game") as native:
                        result = core.Pipeline(
                            root, lambda *_: None, lambda _: True,
                            threading.Event(), prs=[], mode="native").run("__reuse__")
            self.assertEqual(result, "native-built")
            native.assert_called_once()
            self.assertEqual(core.read_state(root)["built_digest"], "e" * 64)

    def test_play_never_recompiles_even_when_runtime_changed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            installed_game(root)
            binary = result_image(root, "SavageWind-official.AppImage", "e" * 64)
            core.write_state(root, {
                "official_digest": "e" * 64, "official_size": binary["size"],
                "official_mtime_ns": binary["mtime_ns"],
                "built_digest": "old-version", "built_cpu": core.cpu_target(),
            })
            with mock.patch("wrapper.migration.legacy_folder", return_value=None):
                with mock.patch.object(core, "fetch_latest",
                                       side_effect=AssertionError("update")):
                    with mock.patch.object(core, "build_game",
                                           side_effect=AssertionError("unexpected compilation")):
                        with mock.patch.object(core.subprocess, "Popen") as launch:
                            launch.return_value.pid = 4000
                            result = core.Pipeline(
                                root, lambda *_: None, lambda _: True,
                                threading.Event(), mode="play").run("__reuse__")
            self.assertEqual(result, "launched")
            self.assertEqual(launch.call_args.args[0][0], str(binary["path"]))
            self.assertEqual((root / "game/swgame.so").read_bytes(), b"existing-native")
            self.assertEqual((root / "game/save/slot.sav").read_bytes(), b"do-not-touch")

    def test_reset_preserves_xdg_configs_and_user_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            installed_game(root)
            (root / "game/.xdg/config/input").mkdir(parents=True)
            (root / "game/.xdg/config/input/remap.cfg").write_text("user")
            (root / "game/.xdg/cache").mkdir()
            (root / "game/.xdg/cache/tmp").write_text("delete")
            (root / "game/profiles").mkdir()
            (root / "game/profiles/player.ini").write_text("player")
            (root / "game/savagewind.ini").write_text("settings")
            (root / ".sw-wrapper").mkdir()
            (root / ".sw-wrapper/gui.json").write_text('{"language":"pt-BR"}')
            (root / ".sw-wrapper/state.json").write_text("{}")
            core.reset_install(root, "DELETE")
            self.assertFalse((root / "root").exists())
            self.assertFalse((root / "game/swgame.so").exists())
            self.assertFalse((root / "game/.xdg/cache").exists())
            self.assertFalse((root / ".sw-wrapper/state.json").exists())
            self.assertEqual((root / "game/save/slot.sav").read_bytes(), b"do-not-touch")
            self.assertEqual((root / "game/.xdg/config/input/remap.cfg").read_text(), "user")
            self.assertEqual((root / "game/profiles/player.ini").read_text(), "player")
            self.assertEqual((root / "game/savagewind.ini").read_text(), "settings")
            self.assertTrue((root / ".sw-wrapper/gui.json").is_file())


if __name__ == "__main__":
    unittest.main()
