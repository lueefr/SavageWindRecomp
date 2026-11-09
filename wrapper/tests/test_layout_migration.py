"""Verify the portable root/game split preserves previously installed game data."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from wrapper import core
from wrapper.migration import organize_portable, move_data


def old_files(root):
    folder = root / "extracted"
    (folder / "exefs").mkdir(parents=True)
    (folder / "romfs").mkdir()
    (folder / "exefs/main").write_bytes(b"NSO0")
    (folder / "exefs/main.npdm").write_bytes(b"META")
    (folder / "romfs/marker").write_bytes(b"original")
    (folder / "swgame.so").write_bytes(b"compiled native library")
    (folder / "swgame.so.id").write_bytes(b"valid-id\n")
    (root / "save").mkdir()
    (root / "save/slot.sav").write_bytes(b"precious save")
    (root / "savagewind.ini").write_text("Setting=1\n")


class LayoutTests(unittest.TestCase):
    def test_legacy_portable_organizes_without_losing_saves_or_game_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old_files(root)
            events = []
            count = organize_portable(root, lambda *args: events.append(args), threading.Event())
            self.assertGreater(count, 0)
            self.assertFalse((root / "extracted").exists())
            self.assertFalse((root / "save").exists())
            self.assertEqual((root / "root/romfs/marker").read_bytes(), b"original")
            self.assertEqual((root / "game/save/slot.sav").read_bytes(), b"precious save")
            self.assertEqual((root / "game/swgame.so").read_bytes(), b"compiled native library")
            self.assertEqual((root / "game/swgame.so.id").read_bytes(), b"valid-id\n")
            self.assertEqual((root / "game/savagewind.ini").read_text(), "Setting=1\n")
            self.assertTrue(core.valid_game(core.game_files(root)))
            self.assertTrue(core.compiled_code(root))
            self.assertFalse((root / "root/swgame.so").exists())
            self.assertEqual(organize_portable(root, lambda *_: None, threading.Event()), 0)

    def test_conflicting_root_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old_files(root)
            (root / "root").mkdir()
            (root / "root/keep.txt").write_text("do not touch")
            with self.assertRaisesRegex(core.InstallError, "simultaneamente"):
                organize_portable(root, lambda *_: None, threading.Event())
            self.assertTrue((root / "extracted/exefs/main").exists())
            self.assertEqual((root / "root/keep.txt").read_text(), "do not touch")
            self.assertTrue((root / "save/slot.sav").exists())

    def test_conflicting_compiled_code_does_not_overwrite_saved_library(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old_files(root)
            (root / "game").mkdir()
            (root / "game/swgame.so").write_bytes(b"keep")
            with self.assertRaisesRegex(core.InstallError, "Conflito"):
                organize_portable(root, lambda *_: None, threading.Event())
            self.assertEqual((root / "game/swgame.so").read_bytes(), b"keep")
            self.assertTrue((root / "extracted/swgame.so").exists())
            self.assertTrue((root / "save/slot.sav").exists())

    def test_compiler_publishes_only_native_code_into_game(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "root").mkdir()
            (root / "root/swgame.so").write_bytes(b"new binary")
            (root / "root/swgame.so.id").write_bytes(b"compatible-id")
            (root / "root/exefs").mkdir()
            core.publish_code(root)
            self.assertEqual((root / "game/swgame.so").read_bytes(), b"new binary")
            self.assertFalse((root / "root/swgame.so").exists())
            self.assertTrue((root / "root/exefs").is_dir())
            self.assertTrue(core.compiled_code(root))

    def test_existing_compilation_is_reused_after_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old_files(root)
            (root / ".sw-wrapper").mkdir()
            official = root / "SavageWind-official.AppImage"
            official.write_bytes(b"official stub")
            digest = "a" * 64
            core.write_state(root, {
                "official_digest": digest,
                "official_size": official.stat().st_size,
                "official_mtime_ns": official.stat().st_mtime_ns,
                "built_digest": digest,
                "built_cpu": core.cpu_target(),
            })
            with mock.patch.object(core, "fetch_latest", side_effect=OSError("offline")):
                with mock.patch.object(core, "build_game") as rebuild:
                    with mock.patch.object(core.subprocess, "Popen") as launch:
                        launch.return_value.pid = 17
                        answer = core.Pipeline(root, lambda *_: None, lambda *_: False,
                                               threading.Event()).run()
            self.assertEqual(answer, "launched")
            rebuild.assert_not_called()
            command = launch.call_args.args[0]
            self.assertEqual(command[1], "--play")
            self.assertEqual(command[2], str(root / "root"))
            env = launch.call_args.kwargs["env"]
            self.assertEqual(env["SAVAGEWIND_DATA_ROOT"], str(root / "game"))
            self.assertEqual(env["SAVAGEWIND_GAME_LIB"], str(root / "game/swgame.so"))
            self.assertEqual((root / "game/save/slot.sav").read_bytes(), b"precious save")

    def test_recovers_previously_completed_build_after_old_wrapper_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            official = root / "SavageWind-official.AppImage"
            official.write_bytes(b"official stub")
            old_files(root)
            (root / ".sw-wrapper").mkdir()
            (root / ".sw-wrapper/build.log").write_text(
                "[gamebuild] generator swgen, compiler zig cc, 4 jobs\\n"
                f"[gamebuild] target: -march={core.cpu_target()} -O1\\n"
                "[gamebuild] done in 305s\\n"
                "[wrapper] failed to find READY\\n"
            )
            digest = "f" * 64
            core.write_state(root, {
                "official_digest": digest,
                "official_size": official.stat().st_size,
                "official_mtime_ns": official.stat().st_mtime_ns,
            })
            with mock.patch.object(core, "fetch_latest", side_effect=OSError("offline")):
                with mock.patch.object(core, "build_game") as rebuild:
                    with mock.patch.object(core.subprocess, "Popen") as launch:
                        launch.return_value.pid = 27
                        answer = core.Pipeline(root, lambda *_: None, lambda *_: False,
                                               threading.Event()).run()
            self.assertEqual(answer, "launched")
            rebuild.assert_not_called()
            self.assertEqual(core.read_state(root)["built_digest"], digest)
            self.assertEqual((root / "game/save/slot.sav").read_bytes(), b"precious save")

    def test_unconfirmed_native_code_does_not_skip_compilation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            official = root / "SavageWind-official.AppImage"
            official.write_bytes(b"official stub")
            old_files(root)
            (root / ".sw-wrapper").mkdir()
            core.write_state(root, {
                "official_digest": "d" * 64,
                "official_size": official.stat().st_size,
                "official_mtime_ns": official.stat().st_mtime_ns,
            })
            def rebuild(destination, binary, emit, cancel):
                self.assertEqual(destination, root)
            with mock.patch.object(core, "fetch_latest", side_effect=OSError("offline")):
                with mock.patch.object(core, "build_game", side_effect=rebuild) as called:
                    with mock.patch.object(core.subprocess, "Popen") as launch:
                        launch.return_value.pid = 37
                        answer = core.Pipeline(root, lambda *_: None, lambda *_: False,
                                               threading.Event()).run()
            self.assertEqual(answer, "launched")
            called.assert_called_once()

    def test_upstream_migration_uses_new_destinations(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source, destination = base / "upstream", base / "portable"
            source.mkdir()
            (source / "extracted").mkdir()
            (source / "extracted/romfs").mkdir()
            (source / "extracted/romfs/data").write_bytes(b"game files")
            (source / "save").mkdir()
            (source / "save/slot").write_bytes(b"save files")
            (destination / ".sw-wrapper").mkdir(parents=True)
            move_data(source, destination, lambda *_: None, threading.Event())
            self.assertFalse(source.exists())
            self.assertEqual((destination / "root/romfs/data").read_bytes(), b"game files")
            self.assertEqual((destination / "game/save/slot").read_bytes(), b"save files")


if __name__ == "__main__":
    unittest.main()
