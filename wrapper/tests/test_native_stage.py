"""Native code staging must preserve the portable layout, saves, and recovery."""
from pathlib import Path
import os
import tempfile
import threading
import unittest
from unittest import mock

from wrapper import core
from wrapper.build_progress import BuildMonitor


def game(root):
    source = root / "root"
    (source / "exefs").mkdir(parents=True)
    (source / "romfs").mkdir()
    (source / "exefs/main").write_bytes(b"NSO0" + b"x" * 40)
    (source / "exefs/main.npdm").write_bytes(b"META")
    (source / "romfs/System").mkdir()
    (source / "romfs/System/Version.txt").write_text("1.9.0")
    (root / "game/save").mkdir(parents=True)
    (root / "game/save/progress.sav").write_bytes(b"saved-data")
    (root / ".sw-wrapper").mkdir()
    return source


class NativeStageTests(unittest.TestCase):
    def test_staging_renames_without_copying_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = game(root)
            inode = (source / "exefs/main").stat().st_ino
            with core.stage_native_code(root) as staged:
                self.assertEqual(staged, root / "game/extracted")
                self.assertFalse(source.exists())
                self.assertEqual((staged / "exefs/main").stat().st_ino, inode)
                self.assertEqual(core.portable_env(root)["SAVAGEWIND_DATA_ROOT"],
                                 str(root / "game"))
                self.assertTrue((root / ".sw-wrapper/code-stage").exists())
            self.assertEqual((source / "exefs/main").stat().st_ino, inode)
            self.assertFalse((root / "game/extracted").exists())
            self.assertFalse((root / ".sw-wrapper/code-stage").exists())
            self.assertEqual((root / "game/save/progress.sav").read_bytes(),
                             b"saved-data")

    def test_staging_always_restores_on_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = game(root)
            with self.assertRaisesRegex(RuntimeError, "compile failed"):
                with core.stage_native_code(root) as staged:
                    (staged / ".gamebuild").mkdir()
                    raise RuntimeError("compile failed")
            self.assertTrue(core.valid_game(source))
            self.assertFalse((root / "game/extracted").exists())
            self.assertTrue((source / ".gamebuild").is_dir())
            self.assertEqual((root / "game/save/progress.sav").read_bytes(),
                             b"saved-data")

    def test_recover_after_interrupted_rename(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = game(root)
            marker = root / ".sw-wrapper/code-stage"
            marker.write_text("game/extracted\n")
            os.replace(source, root / "game/extracted")
            messages = []
            self.assertTrue(core.recover_code_stage(
                root, lambda *event: messages.append(event)))
            self.assertTrue(core.valid_game(source))
            self.assertFalse(marker.exists())
            self.assertFalse((root / "game/extracted").exists())
            self.assertTrue(any("Recovered" in row[1] for row in messages))

    def test_interrupted_before_move_clears_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = game(root)
            marker = root / ".sw-wrapper/code-stage"
            marker.write_text("game/extracted\n")
            self.assertTrue(core.recover_code_stage(root))
            self.assertTrue(core.valid_game(source))
            self.assertFalse(marker.exists())

    def test_existing_staging_directory_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = game(root)
            target = root / "game/extracted"
            target.mkdir()
            (target / "important.txt").write_text("keep")
            with self.assertRaises(core.InstallError):
                with core.stage_native_code(root):
                    pass
            self.assertTrue(core.valid_game(source))
            self.assertEqual((target / "important.txt").read_text(), "keep")

    def test_invalid_recovery_state_refuses_to_delete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game(root)
            (root / ".sw-wrapper/code-stage").write_text("unknown")
            with self.assertRaises(core.InstallError):
                core.recover_code_stage(root)
            self.assertTrue(core.valid_game(root / "root"))

    def test_build_game_restores_and_publishes_native_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = game(root)
            core.write_state(root, {"runtime_source": "test"})
            observed = []
            def simulated(*args):
                self.assertFalse(source.exists())
                staged = args[-1]
                self.assertEqual(staged, root / "game/extracted")
                (staged / "swgame.so").write_bytes(b"native library")
                (staged / "swgame.so.id").write_text("digest-id")
                observed.append(staged)
            with mock.patch.object(core, "_run_game_build", side_effect=simulated):
                core.build_game(root, root / "SavageWind-test.AppImage",
                                lambda *_: None, threading.Event())
            self.assertEqual(len(observed), 1)
            self.assertTrue(core.valid_game(source))
            self.assertEqual((root / "game/swgame.so").read_bytes(), b"native library")
            self.assertEqual((root / "game/swgame.so.id").read_text(), "digest-id")
            self.assertEqual((root / "game/save/progress.sav").read_bytes(), b"saved-data")
            self.assertFalse((source / "swgame.so").exists())

    def test_monitor_supports_upstream_extracted_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "game/extracted"
            mon = BuildMonitor(root, started=10, game_dir=folder)
            self.assertEqual(mon.work, folder / ".gamebuild")


if __name__ == "__main__":
    unittest.main()
