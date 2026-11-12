"""Portable AppImage identification, version history and rollback regression tests."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from wrapper import core, runtime_tracking
from wrapper.rollback import Rollback


def image(root, name="SavageWind-official.AppImage"):
    path = root / name
    path.write_bytes(b"\x7fELF" + b"x" * 1_100_000)
    return path


def state(path, tag="v1"):
    stat = path.stat()
    return {
        "runtime_source": "official",
        "official_digest": core.sha256(path),
        "official_tag": tag,
        "official_size": stat.st_size,
        "official_mtime_ns": stat.st_mtime_ns,
    }


def release(tag, digest, size):
    name = f"Savage_Wind-{tag}-x86_64.AppImage"
    return {
        "tag_name": tag, "published_at": "2026-11-01T00:00:00Z",
        "assets": [{"name": name, "digest": "sha256:" + digest,
                    "size": size, "browser_download_url":
                    core.ASSETS + tag + "/" + name}],
    }


class RuntimeTrackingTests(unittest.TestCase):
    def test_initial_record_and_idempotent_state_updates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = image(root)
            saved = state(path)
            core.write_state(root, saved)
            core.write_state(root, dict(saved))
            document = runtime_tracking.read(root)
            self.assertEqual(len(document["history"]), 1)
            self.assertEqual(document["current"]["version"], "v1")
            self.assertEqual(document["current"]["sha256"], core.sha256(path))
            self.assertTrue(document["current"]["updated_at"].endswith("+00:00"))

    def test_update_records_linked_changelog_without_duplicate_event(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = image(root)
            core.write_state(root, state(path))
            path.write_bytes(b"\x7fELF" + b"y" * 1_100_000)
            changed = state(path, "v2")
            core.write_state(root, changed)
            core.write_state(root, changed, changes={
                "title": "Official update", "html": "<p><a href='https://github.com/"
                "Revivedx/SavageWindRecomp/releases/tag/v2'>v2</a></p>"})
            document = runtime_tracking.read(root)
            self.assertEqual([item["version"] for item in document["history"]], ["v1", "v2"])
            self.assertIn("releases/tag/v2", document["current"]["changes"][0]["html"])
            self.assertIn("updated_at", document["current"])

    def test_reconstruct_missing_state_only_from_matching_sha(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = image(root)
            expected = state(path)
            core.write_state(root, expected)
            (root / ".sw-wrapper/state.json").unlink()
            self.assertEqual(core.read_state(root), expected)
            original = path.stat()
            path.write_bytes(b"\x7fELF" + b"z" * 1_100_000)
            import os
            os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
            self.assertEqual(core.read_state(root), {})

    def test_native_digest_survives_recovery_without_extra_recompile(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = image(root)
            saved = state(path)
            saved["built_digest"] = saved["official_digest"]
            saved["built_cpu"] = core.cpu_target()
            core.write_state(root, saved)
            (root / ".sw-wrapper/state.json").unlink()
            self.assertEqual(core.read_state(root), saved)
            self.assertEqual(runtime_tracking.read(root)["current"]["native"]["built_digest"],
                             saved["built_digest"])

    def test_rollback_restores_history_and_original_executable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = image(root)
            original = path.read_bytes()
            saved = state(path)
            core.write_state(root, saved)
            Rollback(root).begin(saved)
            path.write_bytes(b"\x7fELF" + b"new")
            core.write_state(root, state(path, "v2"))
            self.assertTrue(Rollback(root).restore())
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(core.read_state(root), saved)
            document = runtime_tracking.read(root)
            self.assertEqual(len(document["history"]), 1)
            self.assertEqual(document["current"]["version"], "v1")

    def test_reset_keeps_historical_versions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = image(root)
            core.write_state(root, state(path))
            core.reset_install(root, "DELETE")
            self.assertFalse(path.exists())
            history = runtime_tracking.read(root)
            self.assertIsNone(history["current"])
            self.assertEqual(history["history"][0]["version"], "v1")
            self.assertEqual(history["history"][-1]["event"], "installation_reset")
            self.assertEqual(core.read_state(root), {})


class ExistingAppImageTests(unittest.TestCase):
    def test_identifies_previous_official_release_by_exact_upstream_digest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".sw-wrapper").mkdir()
            existing = image(root, "Savage_Wind-v1-x86_64.AppImage")
            old = release("v1", core.sha256(existing), existing.stat().st_size)
            newer = release("v2", "f" * 64, 9_999_999)
            catalogue = json.dumps([newer, old]).encode("utf-8")
            with mock.patch.object(core.platform, "machine", return_value="x86_64"):
                with mock.patch.object(core.urllib.request, "urlopen",
                                       return_value=io.BytesIO(catalogue)):
                    adopted = core.identify_local_appimage(
                        root, lambda *_: None, threading.Event())
            self.assertEqual(adopted, root / "SavageWind-official.AppImage")
            self.assertEqual(adopted.read_bytes(), existing.read_bytes())
            self.assertEqual(core.read_state(root)["official_tag"], "v1")
            self.assertEqual(runtime_tracking.read(root)["current"]["version"], "v1")

    def test_untracked_development_build_is_not_assigned_invented_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image(root, "SavageWind-test.AppImage")
            self.assertTrue(core.untracked_appimage(root))
            with mock.patch.object(core.urllib.request, "urlopen",
                                   side_effect=AssertionError("not an official release")):
                self.assertIsNone(core.identify_local_appimage(
                    root, lambda *_: None, threading.Event()))
            self.assertEqual(core.read_state(root), {})

    def test_invalid_official_file_never_adopted_without_checksum_match(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".sw-wrapper").mkdir()
            path = image(root)
            catalogue = json.dumps([release("v1", "f" * 64, path.stat().st_size)]).encode()
            with mock.patch.object(core.platform, "machine", return_value="x86_64"):
                with mock.patch.object(core.urllib.request, "urlopen",
                                       return_value=io.BytesIO(catalogue)):
                    self.assertIsNone(core.identify_local_appimage(
                        root, lambda *_: None, threading.Event()))
            self.assertEqual(core.read_state(root), {})


if __name__ == "__main__":
    unittest.main()
