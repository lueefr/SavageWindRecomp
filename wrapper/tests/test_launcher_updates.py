"""Portable launcher self-update: version provenance, consent and safe replacement."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from wrapper import core, launcher_updates, updates


OLD = b"\x7fELF" + b"old" * 380000
NEW = b"\x7fELF" + b"new" * 380000
OLDER_SHA = "a" * 40
NEW_SHA = "b" * 40


def release(tag, payload=NEW, commit=NEW_SHA):
    return {
        "tag_name": tag, "published_at": "2026-11-12T00:00:00Z",
        "draft": False, "target_commitish": commit,
        "body": "# Changelog\n\n- Improve launcher dialogs ([source](https://github.com/lueefr/SavageWindRecomp))",
        "assets": [{
            "name": launcher_updates.ASSET, "size": len(payload),
            "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "browser_download_url": (launcher_updates.SITE + "/releases/download/"
                                     + tag + "/" + launcher_updates.ASSET),
        }],
    }


def existing(root):
    image = root / launcher_updates.ASSET
    image.write_bytes(OLD)
    image.chmod(0o755)
    return image


def build_info(root, tag=None, commit=None):
    path = root / "launcher-build.json"
    path.write_text(json.dumps({"tag": tag, "commit": commit}))
    return path


def environment(root, image, tag=None, commit=None):
    return {"APPIMAGE": str(image),
            "SAVAGEWIND_LAUNCHER_METADATA": str(build_info(root, tag, commit))}


class DiscoveryTests(unittest.TestCase):
    def test_only_versioned_fork_releases_with_valid_asset_digest(self):
        old = release("portable-v0.1.0", OLD, OLDER_SHA)
        current = release("portable-v0.1.1")
        wrong = release("portable-preview")
        forged = release("portable-v99.0.0")
        forged["assets"][0]["browser_download_url"] = "https://evil.example/AppImage"
        found = launcher_updates._versions([wrong, current, forged, old])
        self.assertEqual([entry["tag"] for entry in found],
                         ["portable-v0.1.0", "portable-v0.1.1"])
        self.assertEqual(found[-1]["sha256"], hashlib.sha256(NEW).hexdigest())

    def test_detect_version_from_embedded_release_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = existing(root)
            env = environment(root, image, "portable-v0.1.0", OLDER_SHA)
            with mock.patch.object(launcher_updates, "_json",
                                   return_value=[release("portable-v0.1.1")]):
                info = launcher_updates.check(root, env)
            self.assertEqual(info["source"], "launcher")
            self.assertEqual(info["release"]["tag"], "portable-v0.1.1")
            self.assertIn("/releases/tag/portable-v0.1.1", info["html"])
            self.assertIn("Improve launcher dialogs", info["html"])
            self.assertIn("launcher itself", info["html"])
            self.assertEqual(image.read_bytes(), OLD)

    def test_preview_ahead_of_release_must_not_be_downgraded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = existing(root)
            env = environment(root, image, commit=NEW_SHA)
            def api(url):
                if url == launcher_updates.RELEASES:
                    return [release("portable-v0.1.0", OLD, OLDER_SHA)]
                self.assertIn("/compare/" + OLDER_SHA + "..." + NEW_SHA, url)
                return {"status": "ahead"}
            with mock.patch.object(launcher_updates, "_json", side_effect=api):
                self.assertIsNone(launcher_updates.check(root, env))

    def test_preview_behind_release_receives_update(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = existing(root)
            env = environment(root, image, commit=OLDER_SHA)
            def api(url):
                if url == launcher_updates.RELEASES:
                    return [release("portable-v0.1.1")]
                return {"status": "behind"}
            with mock.patch.object(launcher_updates, "_json", side_effect=api):
                self.assertIsNotNone(launcher_updates.check(root, env))

    def test_existing_old_release_recognized_by_digest_without_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = existing(root)
            env = {"APPIMAGE": str(image)}
            with mock.patch.object(launcher_updates, "_json",
                                   return_value=[release("portable-v0.1.0", OLD),
                                                 release("portable-v0.1.1")]):
                self.assertEqual(launcher_updates.check(root, env)["release"]["tag"],
                                 "portable-v0.1.1")

    def test_unknown_build_without_provenance_does_not_downgrade(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = existing(root)
            with mock.patch.object(launcher_updates, "_json",
                                   return_value=[release("portable-v0.1.1")]):
                self.assertIsNone(launcher_updates.check(root, {"APPIMAGE": str(image)}))

    def test_reject_unrelated_or_symlinked_launcher(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as outer:
            root = Path(folder)
            original = existing(root)
            alias = root / "SavageWind-Portable-alias.AppImage"
            alias.symlink_to(original)
            self.assertIsNone(launcher_updates._image(root, {"APPIMAGE": str(alias)}))
            self.assertIsNone(launcher_updates._image(root, {"APPIMAGE": str(Path(outer) / original.name)}))


class InstallTests(unittest.TestCase):
    def test_verified_download_replaces_only_launcher_preserving_game_and_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = existing(root)
            (root / "game/save").mkdir(parents=True)
            (root / "game/save/slot.sav").write_bytes(b"save")
            (root / "root").mkdir()
            (root / "root/exefs").mkdir()
            current = release("portable-v0.1.1")
            data = launcher_updates._versions([current])[-1]
            notes = {"release": data, "html": "link to changelog"}
            events = []
            with mock.patch.object(launcher_updates.urllib.request, "urlopen",
                                   return_value=io.BytesIO(NEW)):
                result = launcher_updates.install(root, notes, lambda *a: events.append(a),
                                                  threading.Event(), {"APPIMAGE": str(image)})
            self.assertEqual(result, image)
            self.assertEqual(image.read_bytes(), NEW)
            self.assertEqual((root / ".sw-wrapper/launcher-previous.AppImage").read_bytes(), OLD)
            self.assertEqual((root / "game/save/slot.sav").read_bytes(), b"save")
            self.assertTrue((root / "root/exefs").is_dir())
            history = json.loads((root / ".sw-wrapper/launcher.json").read_text())
            self.assertEqual(history["tag"], "portable-v0.1.1")
            self.assertEqual(history["sha256"], data["sha256"])
            self.assertIn("updated_at", history)
            self.assertTrue(any(item[0] == "progress" for item in events))

    def test_corrupted_download_does_not_touch_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = existing(root)
            info = launcher_updates._versions([release("portable-v0.1.1")])[-1]
            with mock.patch.object(launcher_updates.urllib.request, "urlopen",
                                   return_value=io.BytesIO(b"\x7fELF" + b"corrupt")):
                with self.assertRaises(core.InstallError):
                    launcher_updates.install(root, {"release": info, "html": ""},
                                             lambda *_: None, threading.Event(),
                                             {"APPIMAGE": str(image)})
            self.assertEqual(image.read_bytes(), OLD)
            self.assertFalse((root / ".sw-wrapper/launcher.json").exists())

    def test_metadata_failure_restores_old_launcher(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = existing(root)
            info = launcher_updates._versions([release("portable-v0.1.1")])[-1]
            with mock.patch.object(launcher_updates.urllib.request, "urlopen",
                                   return_value=io.BytesIO(NEW)):
                with mock.patch.object(launcher_updates, "_write_state",
                                       side_effect=OSError("disk error")):
                    with self.assertRaises(OSError):
                        launcher_updates.install(root, {"release": info, "html": ""},
                                                 lambda *_: None, threading.Event(),
                                                 {"APPIMAGE": str(image)})
            self.assertEqual(image.read_bytes(), OLD)

    def test_cancel_does_not_replace_appimage(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = existing(root)
            info = launcher_updates._versions([release("portable-v0.1.1")])[-1]
            cancel = threading.Event()
            cancel.set()
            with mock.patch.object(launcher_updates.urllib.request, "urlopen",
                                   return_value=io.BytesIO(NEW)):
                with self.assertRaises(core.Cancelled):
                    launcher_updates.install(root, {"release": info, "html": ""},
                                             lambda *_: None, cancel,
                                             {"APPIMAGE": str(image)})
            self.assertEqual(image.read_bytes(), OLD)


class PipelineTests(unittest.TestCase):
    def test_declining_reoffers_next_launch_and_does_not_block_setup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            offered = {"source": "launcher", "title": "portable", "html": "", "release": {}}
            asked = []
            with mock.patch.object(launcher_updates, "check", return_value=offered):
                with mock.patch.object(launcher_updates, "install") as installer:
                    for _ in range(2):
                        result = core.Pipeline(root, lambda *_: None, lambda _: True,
                            threading.Event(), mode="launcher_check",
                            ask_update=lambda item: (asked.append(item) or False, False)).run()
                        self.assertEqual(result, "setup")
            self.assertEqual(len(asked), 2)
            installer.assert_not_called()

    def test_dont_ask_again_is_independent_of_game_updates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            offered = {"source": "launcher", "title": "portable", "html": "", "release": {}}
            with mock.patch.object(launcher_updates, "check", return_value=offered) as checker:
                worker = lambda: core.Pipeline(
                    root, lambda *_: None, lambda _: True, threading.Event(),
                    mode="launcher_check", ask_update=lambda _: (False, True)).run()
                self.assertEqual(worker(), "setup")
                self.assertEqual(worker(), "setup")
                checker.assert_called_once()
            self.assertTrue(updates.suppressed(root, "launcher"))
            self.assertFalse(updates.suppressed(root, "official"))

    def test_accept_runs_self_update_before_game_setup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            offered = {"source": "launcher", "title": "portable", "html": "", "release": {}}
            events = []
            with mock.patch.object(launcher_updates, "check", return_value=offered):
                with mock.patch.object(launcher_updates, "install") as installer:
                    result = core.Pipeline(root, lambda *e: events.append(e),
                        lambda _: True, threading.Event(), mode="launcher_check",
                        ask_update=lambda _: (True, False)).run()
            self.assertEqual(result, "setup")
            installer.assert_called_once()
            self.assertTrue(any(event[0] == "reveal" for event in events))


if __name__ == "__main__":
    unittest.main()
