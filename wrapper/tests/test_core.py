"""Synthetic portable wrapper tests; no game binaries, keys or network calls."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import signal
import subprocess
import sys
import time
from unittest import mock

from wrapper import core


def release(tag, published, binary=b"A" * 1200000, prerelease=True):
    name = "Savage_Wind-" + tag + "-x86_64.AppImage"
    return {"tag_name": tag, "published_at": published, "draft": False,
            "prerelease": prerelease,
            "assets": [{"name": name, "size": len(binary), "digest": "sha256:" + hashlib.sha256(binary).hexdigest(),
                        "browser_download_url": core.ASSETS + tag + "/" + name}]}


def game(root):
    path = Path(root) / "root"
    (Path(root) / "game").mkdir(exist_ok=True)
    (path / "exefs").mkdir(parents=True)
    (path / "romfs").mkdir()
    (path / "exefs/main").write_bytes(b"NSO0")
    (path / "exefs/main.npdm").write_bytes(b"META")
    return path


class ImportSnapshotTests(unittest.TestCase):
    def test_live_snapshot_has_four_lines_and_measured_count(self):
        with mock.patch.object(core.time, "monotonic", return_value=70):
            snapshot = core.import_detail("Copiando NCA base", 2 * core.CHUNK,
                                          10 * core.CHUNK, 0, 1, "arquivo NCA", 10)
        self.assertEqual(snapshot.splitlines(), [
            "Copiando NCA base: 20.0%",
            "NCA base: 20.0% (2 / 10 MiB)",
            "0 de 1 arquivo NCA",
            "ETA: 04:00",
        ])


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
            self.assertEqual(variables["SAVAGEWIND_DATA_ROOT"], str(Path(folder) / "game"))
            self.assertEqual(variables["HOME"], str(Path(folder) / "game"))
            launch = core.portable_env(Path(folder), play=True)
            self.assertEqual(launch["SAVAGEWIND_GAME_LIB"],
                             str(Path(folder) / "game/swgame.so"))
            self.assertTrue(variables["TMPDIR"].startswith(folder))
            self.assertTrue(variables["MESA_SHADER_CACHE_DIR"].startswith(folder))
            self.assertTrue(variables["XDG_DATA_HOME"].startswith(folder))
            self.assertNotIn(".local/share/savagewind", json.dumps(variables))
            self.assertNotIn("APPDIR", variables)
            self.assertNotIn("LD_LIBRARY_PATH", variables)

    def test_bundled_ca_extends_default_tls_trust(self):
        with tempfile.NamedTemporaryFile() as bundle:
            with mock.patch.dict(os.environ, {"SAVAGEWIND_CA_BUNDLE": bundle.name}):
                with mock.patch.object(core.ssl, "create_default_context") as create:
                    context = core.tls_context()
            self.assertIs(context, create.return_value)
            context.load_verify_locations.assert_called_once_with(cafile=bundle.name)

    def test_bundled_ca_missing_fails_closed(self):
        with mock.patch.dict(os.environ, {"SAVAGEWIND_CA_BUNDLE": "/nonexistent/savagewind-ca.pem"}):
            with self.assertRaisesRegex(core.InstallError, "certificados HTTPS"):
                core.tls_context()

    def test_release_lookup_passes_verified_tls_context(self):
        class Reply:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, *args):
                return b"[]"
        with mock.patch.object(core, "tls_context", return_value="trusted-context"):
            with mock.patch.object(core.urllib.request, "urlopen", return_value=Reply()) as open_url:
                with self.assertRaises(core.InstallError):
                    core.fetch_latest()
        self.assertEqual(open_url.call_args.kwargs["context"], "trusted-context")

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
            self.assertTrue(core.valid_game(root / "root"))
            self.assertEqual((root / "root/romfs/test.bin").read_bytes(), b"1234")
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
            self.assertFalse((root / "root").exists())

    def test_migration_moves_game_and_saves_without_creating_home_data(self):
        from wrapper.migration import move_data
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            old = base / "old"
            old.mkdir()
            (old / "save").mkdir()
            (old / "save/test.sav").write_bytes(b"player save")
            (old / "extracted").mkdir()
            (old / "extracted/marker").write_bytes(b"game")
            dest = base / "portable"
            (dest / ".sw-wrapper").mkdir(parents=True)
            move_data(old, dest, lambda *_: None, threading.Event())
            self.assertFalse(old.exists())
            self.assertEqual((dest / "game/save/test.sav").read_bytes(), b"player save")
            self.assertEqual((dest / "root/marker").read_bytes(), b"game")

    def test_migration_refuses_existing_destination_before_moving_anything(self):
        from wrapper.migration import move_data
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            old, dest = base / "old", base / "portable"
            old.mkdir()
            (dest / ".sw-wrapper").mkdir(parents=True)
            (old / "save").mkdir()
            (old / "save/data").write_bytes(b"save")
            (old / "cache").mkdir()
            (dest / "game/cache").mkdir(parents=True)
            with self.assertRaises(core.InstallError):
                move_data(old, dest, lambda *_: None, threading.Event())
            self.assertTrue((old / "save/data").exists())
            self.assertFalse((dest / "game/save").exists())

    def test_cancelled_migration_leaves_old_files(self):
        from wrapper.migration import move_data
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            old, dest = base / "old", base / "portable"
            old.mkdir()
            (dest / ".sw-wrapper").mkdir(parents=True)
            (old / "save").mkdir()
            (old / "save/data").write_bytes(b"save")
            stopped = threading.Event()
            stopped.set()
            with self.assertRaises(core.Cancelled):
                move_data(old, dest, lambda *_: None, stopped)
            self.assertTrue((old / "save/data").exists())

    def test_installer_lock_refuses_simultaneous_operations(self):
        import fcntl
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            control = root / ".sw-wrapper"
            control.mkdir()
            with (control / "operation.lock").open("a+b") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = core.Pipeline(root, lambda *_: None, lambda *_: False, threading.Event())
                with self.assertRaisesRegex(core.InstallError, "operação"):
                    result.run()

    def test_existing_official_image_is_verified_before_adoption(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".sw-wrapper").mkdir()
            binary = b"\x7fELF" + b"\0" * 4 + b"AI\x02" + b"A" * 1_100_000
            existing = root / "Savage_Wind-v0.9.0-x86_64.AppImage"
            existing.write_bytes(binary)
            info = core.latest([release("v0.9.0", "2026-11-09T00:00:00Z", binary)], "x86_64")
            chosen = core.adopt_release(info, root, lambda *_: None, threading.Event())
            self.assertEqual(chosen, root / "SavageWind-official.AppImage")
            self.assertEqual(chosen.read_bytes(), binary)
            self.assertEqual(existing.read_bytes(), binary)
            self.assertEqual(core.sha256(chosen), info["digest"])

    def test_wrong_hash_is_not_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".sw-wrapper").mkdir()
            existing = root / "Savage_Wind-v0.9.0-x86_64.AppImage"
            existing.write_bytes(b"B" * 1_200_000)
            info = core.latest([release("v0.9.0", "2026-11-09T00:00:00Z")], "x86_64")
            self.assertIsNone(core.adopt_release(info, root, lambda *_: None, threading.Event()))
            self.assertFalse((root / "SavageWind-official.AppImage").exists())

    def test_nsp_mode_uses_extractor_without_touching_saves(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "game/save").mkdir(parents=True)
            (root / "game/save/slot.sav").write_bytes(b"save")
            source = {"base": "/fake/base.xci", "update": "/fake/update.nsp", "keys": "/fake/prod.keys"}
            official = root / "SavageWind-official.AppImage"
            official.write_bytes(b"local")
            stat = official.stat()
            core.write_state(root, {"official_digest": "b" * 64,
                                    "official_size": stat.st_size,
                                    "official_mtime_ns": stat.st_mtime_ns})
            def extract(root_, files, emit, cancel):
                self.assertEqual(files, source)
                game(root_)
            def rebuild(root_, binary, emit, cancel):
                (root_ / "game/swgame.so").write_bytes(b"compiled")
                (root_ / "game/swgame.so.id").write_bytes(b"built")
            with mock.patch.object(core, "fetch_latest", side_effect=OSError("offline")):
                with mock.patch.object(core, "import_packages", side_effect=extract) as importer:
                    with mock.patch.object(core, "build_game", side_effect=rebuild):
                        with mock.patch.object(core.subprocess, "Popen") as start:
                            start.return_value.pid = 123
                            result = core.Pipeline(root, lambda *_: None,
                                                   lambda *_: False, threading.Event()).run(source)
            self.assertEqual(result, "launched")
            importer.assert_called_once()
            self.assertEqual((root / "game/save/slot.sav").read_bytes(), b"save")

    def test_download_checked_before_copying_large_game(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            (source / "exefs").mkdir(parents=True)
            (source / "romfs").mkdir()
            (source / "exefs/main").write_bytes(b"NSO0")
            (source / "exefs/main.npdm").write_bytes(b"META")
            with mock.patch.object(core, "fetch_latest", side_effect=OSError("offline")):
                with mock.patch.object(core, "copy_game") as copier:
                    with self.assertRaises(core.InstallError):
                        core.Pipeline(root, lambda *_: None, lambda *_: False,
                                      threading.Event()).run(str(source))
            copier.assert_not_called()
            self.assertFalse((root / "root").exists())

    def test_cancel_stops_detached_compiler_groups(self):
        # The upstream creates subprocesses in distinct process groups on Linux.
        command = (
            "import subprocess, sys, time; "
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
            "start_new_session=True); "
            "print(p.pid, flush=True); time.sleep(60)"
        )
        parent = subprocess.Popen([sys.executable, "-c", command], stdout=subprocess.PIPE,
                                  text=True, start_new_session=True)
        child = None
        try:
            child = int(parent.stdout.readline().strip())
            self.assertNotEqual(os.getpgid(child), parent.pid)
            core.stop_process(parent)
            self.assertIsNotNone(parent.poll())
            # A stopped child may remain briefly as a zombie awaiting reaping.
            status = Path(f"/proc/{child}/stat")
            if status.exists():
                fields = status.read_text().split()
                self.assertEqual(fields[2], "Z", "Detached compiler should not remain running")
        finally:
            if child is not None:
                try:
                    os.killpg(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if parent.poll() is None:
                core.stop_process(parent)
            parent.stdout.close()

    def test_no_game_shows_installer_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(core, "fetch_latest", side_effect=AssertionError("network called")):
                flow = core.Pipeline(Path(folder), lambda *_: None, lambda *_: False, threading.Event())
                self.assertEqual(flow.run(), "setup")

    def test_existing_game_starts_without_rebuild_if_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = game(root)
            (root / "game/swgame.so").write_bytes(b"fake")
            (root / "game/swgame.so.id").write_bytes(b"fake id")
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

    def test_no_update_stays_silent_and_opens_game(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = game(root)
            (root / "game/swgame.so").write_bytes(b"compiled")
            (root / "game/swgame.so.id").write_bytes(b"known")
            binary = root / "SavageWind-official.AppImage"
            binary.write_bytes(b"stub")
            digest = "a" * 64
            core.write_state(root, {
                "official_digest": digest, "official_size": binary.stat().st_size,
                "official_mtime_ns": binary.stat().st_mtime_ns,
                "built_digest": digest, "built_cpu": core.cpu_target(),
            })
            events = []
            with mock.patch.object(core, "fetch_latest", side_effect=OSError("offline")):
                with mock.patch.object(core.subprocess, "Popen") as launch:
                    launch.return_value.pid = 44
                    result = core.Pipeline(root, lambda *args: events.append(args),
                                           lambda *_: self.fail("unexpected question"),
                                           threading.Event()).run()
            self.assertEqual(result, "launched")
            self.assertNotIn("reveal", [event[0] for event in events])
            self.assertEqual(launch.call_args.args[0][1], "--play")

    def test_declined_update_never_reveals_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = game(root)
            (root / "game/swgame.so").write_bytes(b"compiled")
            (root / "game/swgame.so.id").write_bytes(b"known")
            binary = root / "SavageWind-official.AppImage"
            binary.write_bytes(b"stub")
            digest = "a" * 64
            core.write_state(root, {
                "official_digest": digest, "official_size": binary.stat().st_size,
                "official_mtime_ns": binary.stat().st_mtime_ns,
                "built_digest": digest, "built_cpu": core.cpu_target(),
            })
            events = []
            update = core.latest([release("v0.9", "2026-11-10T00:00:00Z")], "x86_64")
            with mock.patch.object(core, "fetch_latest", return_value=update):
                with mock.patch.object(core.subprocess, "Popen") as launch:
                    launch.return_value.pid = 45
                    result = core.Pipeline(root, lambda *args: events.append(args),
                                           lambda *_: False, threading.Event()).run()
            self.assertEqual(result, "launched")
            self.assertNotIn("reveal", [event[0] for event in events])

    def test_update_accepted_reveals_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = game(root)
            (root / "game/swgame.so").write_bytes(b"compiled")
            (root / "game/swgame.so.id").write_bytes(b"known")
            binary = root / "SavageWind-official.AppImage"
            binary.write_bytes(b"stub")
            digest = "a" * 64
            core.write_state(root, {
                "official_digest": digest, "official_size": binary.stat().st_size,
                "official_mtime_ns": binary.stat().st_mtime_ns,
                "built_digest": digest, "built_cpu": core.cpu_target(),
            })
            events = []
            update = core.latest([release("v0.9", "2026-11-10T00:00:00Z")], "x86_64")
            def downloaded(info, destination, emit, cancel):
                new = destination / "SavageWind-official.AppImage"
                new.write_bytes(b"updated image")
                return new
            def rebuilt(destination, executable, emit, cancel):
                (destination / "game/swgame.so").write_bytes(b"new code")
                (destination / "game/swgame.so.id").write_bytes(b"new id")
            with mock.patch.object(core, "fetch_latest", return_value=update):
                with mock.patch.object(core, "adopt_release", return_value=None):
                    with mock.patch.object(core, "download_release", side_effect=downloaded):
                        with mock.patch.object(core, "build_game", side_effect=rebuilt):
                            with mock.patch.object(core.subprocess, "Popen") as launch:
                                launch.return_value.pid = 45
                                result = core.Pipeline(root, lambda *args: events.append(args),
                                                       lambda *_: True, threading.Event()).run()
            self.assertEqual(result, "launched")
            self.assertIn("reveal", [event[0] for event in events])

    def test_cpu_target_respects_compat_override(self):
        with mock.patch.dict(os.environ, {"SAVAGEWIND_COMPAT_CPU": "1"}):
            self.assertIn("compatible", core.cpu_target())

    def test_new_cpu_target_rebuilds_portable_game_code(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = game(root)
            (root / "game/swgame.so").write_bytes(b"compiled")
            (root / "game/swgame.so.id").write_bytes(b"known")
            binary = root / "SavageWind-official.AppImage"
            binary.write_bytes(b"stub")
            digest = "a" * 64
            core.write_state(root, {
                "official_digest": digest, "official_size": binary.stat().st_size,
                "official_mtime_ns": binary.stat().st_mtime_ns,
                "built_digest": digest, "built_cpu": "x86-64-v2",
            })
            with mock.patch.object(core, "cpu_target", return_value="x86-64-v3"):
                with mock.patch.object(core, "fetch_latest", side_effect=OSError("offline")):
                    with mock.patch.object(core, "build_game") as rebuild:
                        with mock.patch.object(core.subprocess, "Popen") as launch:
                            launch.return_value.pid = 44
                            result = core.Pipeline(root, lambda *_: None,
                                                   lambda *_: False, threading.Event()).run()
            self.assertEqual(result, "launched")
            rebuild.assert_called_once()
            self.assertEqual(core.read_state(root)["built_cpu"], "x86-64-v3")

    def test_decline_update_preserves_installed_binary(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = game(root)
            (root / "game/swgame.so").write_bytes(b"fake")
            (root / "game/swgame.so.id").write_bytes(b"fake")
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
