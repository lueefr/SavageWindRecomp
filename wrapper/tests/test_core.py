"""Synthetic portable wrapper tests; no game binaries, keys or network calls."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from wrapper import core


def release(tag, published, binary=b"A" * 1200000, prerelease=True):
    name = "Savage_Wind-" + tag + "-x86_64.AppImage"
    return {"tag_name": tag, "published_at": published, "draft": False,
            "prerelease": prerelease,
            "assets": [{"name": name, "size": len(binary), "digest": "sha256:" + hashlib.sha256(binary).hexdigest(),
                        "browser_download_url": core.ASSETS + tag + "/" + name}]}


def game(root):
    path = Path(root) / "extracted"
    (path / "exefs").mkdir(parents=True)
    (path / "romfs").mkdir()
    (path / "exefs/main").write_bytes(b"NSO0")
    (path / "exefs/main.npdm").write_bytes(b"META")
    return path


class PortableTests(unittest.TestCase):
    def test_folder_follows_physical_appimage(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / "SavageWind-Portable.AppImage"
            app.write_bytes(b"")
            self.assertEqual(core.portable_root({"APPIMAGE": str(app)}), Path(folder).resolve())

    def test_missing_appimage_does_not_fall_back_to_home(self):
        with self.assertRaises(core.InstallError):
            core.portable_root({})

    def test_upstream_is_confined_to_portable_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            variables = core.portable_env(Path(folder), {"HOME": "/tmp/fakehome",
                                                        "APPDIR": "/tmp/runtime",
                                                        "LD_LIBRARY_PATH": "/tmp/runtime/lib"})
            self.assertEqual(variables["SAVAGEWIND_DATA_ROOT"], folder)
            self.assertTrue(variables["MESA_SHADER_CACHE_DIR"].startswith(folder))
            self.assertTrue(variables["XDG_DATA_HOME"].startswith(folder))
            self.assertNotIn(".local/share/savagewind", json.dumps(variables))
            self.assertNotIn("APPDIR", variables)
            self.assertNotIn("LD_LIBRARY_PATH", variables)

    def test_newest_release_includes_alpha(self):
        older = release("v0.4", "2026-11-01T00:00:00Z", prerelease=False)
        newer = release("v0.5-alpha", "2026-11-09T00:00:00Z")
        self.assertEqual(core.latest([older, newer], "x86_64")["tag"], "v0.5-alpha")

    def test_official_url_and_digest_required(self):
        data = release("v0.5", "2026-11-09T00:00:00Z")
        data["assets"][0]["browser_download_url"] = "https://bad.example/fake"
        with self.assertRaises(core.InstallError):
            core.latest([data], "x86_64")
        data["assets"][0]["browser_download_url"] = core.ASSETS + "v0.5/Savage_Wind-v0.5-x86_64.AppImage"
        data["assets"][0]["digest"] = "sha256:NOT_A_HASH"
        with self.assertRaises(core.InstallError):
            core.latest([data], "x86_64")

    def test_game_import_is_atomic(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".sw-wrapper").mkdir()
            original = root / "original"
            (original / "exefs").mkdir(parents=True)
            (original / "romfs").mkdir()
            (original / "exefs/main").write_bytes(b"NSO0")
            (original / "exefs/main.npdm").write_bytes(b"META")
            (original / "romfs/test.bin").write_bytes(b"1234")
            events = []
            core.copy_game(root, original, lambda *args: events.append(args), threading.Event())
            self.assertTrue(core.valid_game(root / "extracted"))
            self.assertEqual((root / "extracted/romfs/test.bin").read_bytes(), b"1234")
            self.assertEqual(len(list((root / ".sw-wrapper").glob(".install-*"))), 0)

    def test_cancelled_copy_does_not_install_partial_game(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".sw-wrapper").mkdir()
            original = root / "original"
            (original / "exefs").mkdir(parents=True)
            (original / "romfs").mkdir()
            (original / "exefs/main").write_bytes(b"NSO0")
            (original / "exefs/main.npdm").write_bytes(b"META")
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(core.Cancelled):
                core.copy_game(root, original, lambda *_: None, cancel)
            self.assertFalse((root / "extracted").exists())

    def test_no_game_shows_installer_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(core, "fetch_latest", side_effect=AssertionError("network called")):
                flow = core.Pipeline(Path(folder), lambda *_: None, lambda *_: False, threading.Event())
                self.assertEqual(flow.run(), "setup")

    def test_existing_game_starts_without_rebuild_if_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = game(root)
            (path / "swgame.so").write_bytes(b"fake")
            (path / "swgame.so.id").write_bytes(b"fake id")
            official = root / "SavageWind-official.AppImage"
            official.write_bytes(b"stub")
            state = {"official_digest": "a" * 64, "official_size": official.stat().st_size,
                     "official_mtime_ns": official.stat().st_mtime_ns,
                     "built_digest": "a" * 64}
            core.write_state(root, state)
            with mock.patch.object(core, "fetch_latest", side_effect=OSError("offline")):
                with mock.patch.object(core, "build_game") as rebuild:
                    with mock.patch.object(core.subprocess, "Popen") as launch:
                        launch.return_value.pid = 42
                        result = core.Pipeline(root, lambda *_: None, lambda *_: False, threading.Event()).run()
            self.assertEqual(result, "launched")
            rebuild.assert_not_called()
            self.assertEqual(launch.call_args.args[0][1], "--play")

    def test_decline_update_preserves_installed_binary(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = game(root)
            (path / "swgame.so").write_bytes(b"fake")
            (path / "swgame.so.id").write_bytes(b"fake")
            official = root / "SavageWind-official.AppImage"
            official.write_bytes(b"old binary")
            state = {"official_digest": "a" * 64, "official_size": official.stat().st_size,
                     "official_mtime_ns": official.stat().st_mtime_ns,
                     "built_digest": "a" * 64}
            core.write_state(root, state)
            latest = core.latest([release("v0.6", "2026-11-10T00:00:00Z")], "x86_64")
            prompts = []
            def ask(message):
                prompts.append(message)
                return False
            with mock.patch.object(core, "fetch_latest", return_value=latest):
                with mock.patch.object(core, "download_release") as download:
                    with mock.patch.object(core.subprocess, "Popen") as launch:
                        launch.return_value.pid = 7
                        result = core.Pipeline(root, lambda *_: None, ask, threading.Event()).run()
            self.assertEqual(result, "launched")
            self.assertEqual(len(prompts), 1)
            self.assertEqual(official.read_bytes(), b"old binary")
            download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
