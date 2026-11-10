"""Tests of optional PR selection, portable state and destructive reset safety."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from wrapper import core, pr_build
from wrapper.i18n import get_language, set_language, tr


def create_game(root):
    source = Path(root) / "root"
    (source / "exefs").mkdir(parents=True)
    (source / "romfs").mkdir()
    (source / "exefs/main").write_bytes(b"NSO0")
    (source / "exefs/main.npdm").write_bytes(b"META")
    return source


class InstallerFeatures(unittest.TestCase):
    def test_missing_appimage_requires_setup_even_with_valid_native_lib(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            create_game(root)
            (root / "game").mkdir()
            (root / "game/swgame.so").write_bytes(b"ELF")
            (root / "game/swgame.so.id").write_text("test-id")
            self.assertTrue(core.compiled_code(root))
            with mock.patch.object(core, "fetch_latest", side_effect=AssertionError("not automatic")):
                with mock.patch("wrapper.migration.legacy_folder", return_value=None):
                    result = core.Pipeline(root, lambda *_: None, lambda *_: True,
                                           threading.Event()).run()
            self.assertEqual(result, "setup")

    def test_safe_reset_removes_only_known_install_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            create_game(root)
            (root / "game/save").mkdir(parents=True)
            (root / "game/save/test.sav").write_bytes(b"save")
            (root / "SavageWind-official.AppImage").write_bytes(b"\x7fELF")
            (root / "SavageWind-Portable-x86_64.AppImage").write_bytes(b"\x7fELF")
            (root / "my-notes.txt").write_text("keep me")
            with self.assertRaises(core.InstallError):
                core.reset_install(root, "yes")
            self.assertTrue((root / "game/save/test.sav").exists())
            core.reset_install(root, "DELETE")
            self.assertFalse((root / "root").exists())
            self.assertFalse((root / "game").exists())
            self.assertFalse((root / "SavageWind-official.AppImage").exists())
            self.assertTrue((root / "SavageWind-Portable-x86_64.AppImage").exists())
            self.assertEqual((root / "my-notes.txt").read_text(), "keep me")

    def test_reset_does_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as external:
            root = Path(folder)
            outside = Path(external) / "important.txt"
            outside.write_text("safe")
            (root / "game").symlink_to(external, target_is_directory=True)
            with self.assertRaises(core.InstallError):
                core.reset_install(root, "DELETE")
            self.assertEqual(outside.read_text(), "safe")

    def test_selection_rejects_duplicates_and_invalid_sha(self):
        sha = "a" * 40
        valid = [{"number": 636, "sha": sha}, {"number": 637, "sha": "b" * 40}]
        self.assertEqual(pr_build.validated(valid), valid)
        self.assertEqual(pr_build.selection_key(valid), pr_build.selection_key(valid))
        with self.assertRaises(core.InstallError):
            pr_build.validated([valid[0], valid[0]])
        with self.assertRaises(core.InstallError):
            pr_build.validated([{"number": 636, "sha": "not-a-sha"}])
        with self.assertRaises(core.InstallError):
            pr_build.validated([{"number": True, "sha": sha}])

    def test_custom_appimage_requires_recorded_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "SavageWind-custom.AppImage"
            image.write_bytes(b"\x7fELF" + b"x" * 1024)
            stat = image.stat()
            state = {"runtime_source": "custom", "custom_digest": "a" * 64,
                     "custom_selection": "b" * 64, "custom_size": stat.st_size,
                     "custom_mtime_ns": stat.st_mtime_ns}
            self.assertEqual(pr_build.custom_runtime(root, state), image)
            state["custom_size"] += 1
            self.assertIsNone(pr_build.custom_runtime(root, state))
            self.assertIsNone(core.installed_runtime(root, state))

    def test_test_appimage_requires_recorded_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "SavageWind-test.AppImage"
            image.write_bytes(b"\x7fELF" + b"x" * 1024)
            stat = image.stat()
            state = {"runtime_source": "test", "test_digest": "b" * 64,
                     "test_commit": "a" * 40, "test_size": stat.st_size,
                     "test_mtime_ns": stat.st_mtime_ns}
            self.assertEqual(pr_build.test_runtime(root, state), image)
            self.assertEqual(core.installed_runtime(root, state), image)
            state["test_size"] += 1
            self.assertIsNone(pr_build.test_runtime(root, state))
            self.assertIsNone(core.installed_runtime(root, state))

    def test_test_branch_build_does_not_apply_pull_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            revision = "b" * 40
            calls = []
            def fake_run(argv, cwd, emit, cancel, logfile):
                calls.append(argv)
                if "tools/linux/package.sh" in argv:
                    output = Path(cwd) / "source/build/dist"
                    output.mkdir(parents=True, exist_ok=True)
                    (output / "Savage_Wind-test-x86_64.AppImage").write_bytes(
                        b"\x7fELF" + b"x" * 1_100_000)
            with mock.patch.object(pr_build, "bundled_builder",
                                   return_value=(root / "rootfs", root / "seed", root / "bwrap")):
                with mock.patch.object(pr_build, "_run", side_effect=fake_run):
                    with mock.patch.object(pr_build, "_capture", return_value=revision):
                        with mock.patch.object(pr_build, "seed_sources") as seed:
                            result = pr_build.build_test_appimage(
                                root, lambda *_: None, threading.Event())
            self.assertEqual(result["path"], root / "SavageWind-test.AppImage")
            self.assertEqual(result["commit"], revision)
            self.assertEqual(result["selection"], "test:" + revision)
            self.assertTrue(result["path"].is_file())
            self.assertTrue(any("git" in x and "clone" in x and "test" in x for x in calls))
            self.assertFalse(any("refs/pull/" in " ".join(x) for x in calls))
            self.assertFalse(any("podman" in " ".join(x) for x in calls))
            self.assertFalse(any(x[:2] == ["make", "appimage"] for x in calls))
            seed.assert_called_once()

    def test_source_builder_never_uses_host_git_or_podman(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            command = pr_build.sandbox_command(work / "rootfs", work / "bwrap",
                                                work / "source", ["clang", "--version"])
            self.assertEqual(command[0], str(work / "bwrap"))
            self.assertIn("--unshare-net", command)
            self.assertNotIn("podman", command)
            self.assertNotIn("--ro-bind-try", command)
            self.assertNotIn(str(work / "game"), command)
            self.assertEqual(command[-2:], ["clang", "--version"])

    def test_untrusted_checkout_cannot_redirect_compiler_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / "source"
            seed = base / "seed"
            outside = base / "outside"
            source.mkdir()
            seed.mkdir()
            outside.mkdir()
            (outside / "important").write_text("untouched")
            (source / "build").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(core.InstallError, "Refusing to overwrite"):
                pr_build.seed_sources(seed, source, threading.Event())
            self.assertEqual((outside / "important").read_text(), "untouched")

    def test_builder_reports_missing_bundled_toolchain(self):
        with mock.patch.dict("os.environ", {"SAVAGEWIND_BUILDER_ROOT": "/nonexistent",
                                           "SAVAGEWIND_BWRAP": "/nonexistent"}):
            with self.assertRaisesRegex(core.InstallError, "embedded source-build toolchain"):
                pr_build.bundled_builder()

    def test_test_branch_pipeline_rebuilds_only_when_switching_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            create_game(root)
            (root / "game/save").mkdir(parents=True)
            (root / "game/save/slot.sav").write_bytes(b"save")
            test_image = root / "SavageWind-test.AppImage"
            test_image.write_bytes(b"\x7fELF" + b"test")
            stat = test_image.stat()
            result = {"path": test_image, "digest": "b" * 64,
                      "commit": "c" * 40, "size": stat.st_size,
                      "mtime_ns": stat.st_mtime_ns, "selection": "test:" + "c" * 40}
            def build_game(root_, binary, emit, cancel):
                (root_ / "game/swgame.so").write_bytes(b"recompiled")
                (root_ / "game/swgame.so.id").write_bytes(b"build-id")
            with mock.patch.object(pr_build, "build_test_appimage", return_value=result) as build:
                with mock.patch.object(core, "build_game", side_effect=build_game):
                    with mock.patch.object(core, "fetch_latest", side_effect=AssertionError("official lookup")):
                        with mock.patch.object(core.subprocess, "Popen") as launch:
                            launch.return_value.pid = 42
                            answer = core.Pipeline(root, lambda *_: None, lambda *_: True,
                                                   threading.Event(), prs="test").run("__reuse__")
            self.assertEqual(answer, "launched")
            build.assert_called_once()
            state = core.read_state(root)
            self.assertEqual(state["runtime_source"], "test")
            self.assertEqual(state["test_commit"], "c" * 40)
            self.assertEqual(state["built_digest"], "b" * 64)
            self.assertEqual((root / "game/save/slot.sav").read_bytes(), b"save")
            self.assertEqual(launch.call_args.args[0][0], str(test_image))

    def test_listing_filters_wrong_base_and_trims_description(self):
        sha = "b" * 40
        items = [
            {"number": 636, "head": {"sha": sha}, "base": {"ref": "test"},
             "title": "Fix HUD icons", "body": "## What this changes\n\nFixes #613.\n"},
            {"number": 1, "head": {"sha": sha}, "base": {"ref": "main"},
             "title": "Different base", "body": "No"},
        ]
        class Reply:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def read(self, *_):
                return json.dumps(items).encode("utf-8")
        with mock.patch.object(pr_build.urllib.request, "urlopen", return_value=Reply()):
            with mock.patch.object(pr_build, "tls_context", return_value=None):
                result = pr_build.open_prs()
        self.assertEqual([row["number"] for row in result], [636])
        self.assertEqual(result[0]["description"], "Fixes #613.")

    def test_default_english_switch_and_persist(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(get_language(folder), "en")
            self.assertEqual(tr("Delete installation", "en"), "Delete installation")
            self.assertEqual(tr("Delete installation", "pt-BR"), "Excluir instalação")
            set_language(folder, "pt-BR")
            self.assertEqual(get_language(folder), "pt-BR")
            with self.assertRaises(ValueError):
                set_language(folder, "invalid")


if __name__ == "__main__":
    unittest.main()
