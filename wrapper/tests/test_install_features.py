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

    def test_test_branch_build_uses_rootless_podman_without_merging_prs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            revision = "b" * 40
            calls = []
            def fake_run(argv, cwd, emit, cancel, logfile):
                calls.append(argv)
                if argv[:2] == ["git", "clone"]:
                    source = Path(argv[-1])
                    tools = source / "tools/linux"
                    tools.mkdir(parents=True)
                    (tools / "Containerfile").write_text(
                        "FROM docker.io/library/ubuntu:22.04\n"
                        "ARG DEBIAN_FRONTEND=noninteractive\n"
                        "RUN apt-get update && apt-get install -y clang-21\n")
                if "tools/linux/package.sh" in argv:
                    output = Path(cwd) / "build/dist"
                    output.mkdir(parents=True, exist_ok=True)
                    (output / "Savage_Wind-test-x86_64.AppImage").write_bytes(
                        b"\x7fELF" + b"x" * 1_100_000)
            with mock.patch.object(pr_build, "ensure_tools") as deps:
                with mock.patch.object(pr_build, "_podman_ready") as ready:
                    with mock.patch.object(pr_build, "ensure_tun") as tun:
                        with mock.patch.object(pr_build, "_image_exists", return_value=False):
                            with mock.patch.object(pr_build.subprocess, "run",
                                                   return_value=mock.Mock(returncode=0)):
                                with mock.patch.object(pr_build, "_run", side_effect=fake_run):
                                    with mock.patch.object(pr_build, "_capture", return_value=revision):
                                        result = pr_build.build_test_appimage(
                                            root, lambda *_: None, threading.Event(),
                                            ask=lambda _: True)
            self.assertEqual(result["path"], root / "SavageWind-test.AppImage")
            self.assertEqual(result["commit"], revision)
            self.assertEqual(result["selection"], "test:" + revision)
            self.assertTrue(result["path"].is_file())
            self.assertTrue(any(x[:2] == ["git", "clone"] and "test" in x for x in calls))
            self.assertFalse(any(x[:2] == ["podman", "build"] for x in calls))
            self.assertTrue(any(x[:2] == ["podman", "commit"] for x in calls))
            self.assertEqual(sum(x[:2] == ["podman", "run"] for x in calls), 3)
            for command in calls:
                if command[:2] == ["podman", "run"]:
                    self.assertIn("--network=pasta", command)
                    self.assertNotIn("--network=host", command)
                    if "--volume" in command:
                        self.assertIn("--userns=keep-id", command)
                        self.assertIn("HOME=/tmp", command)
                    else:
                        self.assertNotIn("--userns=keep-id", command)
            self.assertFalse(any("refs/pull/" in " ".join(x) for x in calls))
            self.assertFalse(any(x[:2] == ["make", "appimage"] for x in calls))
            deps.assert_called_once()
            ready.assert_called_once()
            tun.assert_called_once()

    def test_recover_successful_test_appimage_without_building(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / ".sw-wrapper"
            state.mkdir()
            (state / "test-build.log").write_text(
                "Packaging game\nCreating filesystem\nSuccess\n")
            image = root / "SavageWind-test.AppImage"
            image.write_bytes(b"\x7fELF" + b"x" * 1_100_000)
            messages = []
            with mock.patch.object(pr_build, "ensure_tools") as tools:
                with mock.patch.object(pr_build, "_run") as build:
                    result = pr_build.build_test_appimage(
                        root, lambda *args: messages.append(args),
                        threading.Event(), ask=lambda text: True)
            tools.assert_not_called()
            build.assert_not_called()
            self.assertEqual(result["path"], image)
            self.assertTrue(result["commit"].startswith("unverified-local-"))
            self.assertEqual(len(result["digest"]), 64)
            self.assertTrue(any("Recovered" in entry[1] for entry in messages))

    def test_recovery_is_explicit_and_requires_completed_log(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / ".sw-wrapper"
            state.mkdir()
            log = state / "test-build.log"
            log.write_text("Source revision: " + "a" * 40 + "\nSuccess\n")
            image = root / "SavageWind-test.AppImage"
            image.write_bytes(b"\x7fELF" + b"x" * 1_100_000)
            asked = []
            def decline(message):
                asked.append(message)
                return False
            self.assertIsNone(pr_build._recover_test_build(
                root, lambda *_: None, decline, threading.Event()))
            self.assertIn("aaaaaaaaaaaa", asked[0])
            self.assertEqual(pr_build._recover_test_build(
                root, lambda *_: None, lambda _: True, threading.Event())["commit"],
                "a" * 40)
            log.write_text("Source revision: " + "a" * 40 + "\nNo completion yet\n")
            self.assertIsNone(pr_build._recover_test_build(
                root, lambda *_: None, lambda _: True, threading.Event()))

    def test_cleanup_permission_error_uses_rootless_namespace_only(self):
        with tempfile.TemporaryDirectory() as folder:
            stage = Path(folder) / "test-build-safe"
            stage.mkdir()
            (stage / "source").mkdir()
            events = []
            def remove(argv, **kwargs):
                self.assertEqual(argv[:5],
                                 ["podman", "unshare", "rm", "-rf", "--"])
                self.assertEqual(argv[5], str(stage))
                self.assertNotIn("sudo", argv)
                (stage / "source").rmdir()
                stage.rmdir()
                return mock.Mock(returncode=0, stderr="")
            with mock.patch.object(pr_build.shutil, "rmtree",
                                   side_effect=PermissionError(13, "Permission denied", "build/zig")):
                with mock.patch.object(pr_build.subprocess, "run", side_effect=remove):
                    pr_build._cleanup_stage(stage, lambda *args: events.append(args))
            self.assertFalse(stage.exists())
            self.assertTrue(any("cleaned through rootless Podman" in x[1] for x in events))

    def test_cleanup_fails_without_replacing_build_result(self):
        with tempfile.TemporaryDirectory() as folder:
            stage = Path(folder) / "test-build-leftover"
            stage.mkdir()
            events = []
            with mock.patch.object(pr_build.shutil, "rmtree",
                                   side_effect=PermissionError(13, "Permission denied", "build/zig")):
                with mock.patch.object(pr_build.subprocess, "run",
                                       return_value=mock.Mock(returncode=1,
                                                              stderr="permission denied")):
                    pr_build._cleanup_stage(stage, lambda *args: events.append(args))
            self.assertTrue(stage.exists())
            self.assertTrue(any("Temporary checkout was retained" in x[1] for x in events))

    def test_toolchain_recipe_with_comments_and_continuations(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "Containerfile"
            path.write_text(
                "# Ubuntu toolchain\nFROM docker.io/library/ubuntu:22.04\n"
                "ARG DEBIAN_FRONTEND=noninteractive\n"
                "RUN apt-get update && apt-get install -y --no-install-recommends \\\n"
                "    ca-certificates python3 \\\n"
                "    # This comment is skipped by Docker too\n"
                "    clang-21 \\\n"
                "    && echo ready\n")
            script = pr_build._toolchain_recipe(path)
            self.assertEqual(script,
                             "apt-get update && apt-get install -y --no-install-recommends "
                             "ca-certificates python3 clang-21 && echo ready")
            path.write_text(path.read_text() + "COPY /etc/passwd /tmp/passwd\n")
            with self.assertRaises(core.InstallError):
                pr_build._toolchain_recipe(path)

    def test_existing_compiler_image_is_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            source = work / "source"
            tools = source / "tools/linux"
            tools.mkdir(parents=True)
            (tools / "Containerfile").write_text(
                "FROM docker.io/library/ubuntu:22.04\n"
                "ARG DEBIAN_FRONTEND=noninteractive\n"
                "RUN apt-get update && apt-get install -y clang-21\n")
            with mock.patch.object(pr_build, "_image_exists", return_value=True):
                with mock.patch.object(pr_build, "_run") as run:
                    image = pr_build._prepare_toolchain(source, work,
                                                         lambda *_: None,
                                                         threading.Event(), None)
            self.assertTrue(image.startswith("localhost/savagewind-toolchain:recipe-"))
            run.assert_not_called()

    def test_toolchain_container_is_removed_on_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            source = work / "source"
            tools = source / "tools/linux"
            tools.mkdir(parents=True)
            (tools / "Containerfile").write_text(
                "FROM docker.io/library/ubuntu:22.04\n"
                "ARG DEBIAN_FRONTEND=noninteractive\n"
                "RUN apt-get update && apt-get install -y clang-21\n")
            with mock.patch.object(pr_build, "_image_exists", return_value=False):
                with mock.patch.object(pr_build, "_run",
                                       side_effect=core.InstallError("apt failed")):
                    with mock.patch.object(pr_build.subprocess, "run",
                                           return_value=mock.Mock(returncode=0)) as remove:
                        with self.assertRaisesRegex(core.InstallError, "apt failed"):
                            pr_build._prepare_toolchain(source, work, lambda *_: None,
                                                         threading.Event(), None)
            self.assertEqual(remove.call_args.args[0][:3], ["podman", "rm", "--force"])

    def test_tun_preflight_stops_before_source_download(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(pr_build, "ensure_tools"):
                with mock.patch.object(pr_build, "_podman_ready"):
                    with mock.patch.object(pr_build, "ensure_tun",
                                           side_effect=core.Cancelled()) as tun:
                        with mock.patch.object(pr_build, "_run") as runner:
                            with self.assertRaises(core.Cancelled):
                                pr_build.build_test_appimage(
                                    folder, lambda *_: None, threading.Event(),
                                    ask=lambda _: False)
                            runner.assert_not_called()
                            tun.assert_called_once()

    def test_user_declining_package_install_does_not_run_build(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with mock.patch.object(pr_build, "ensure_tools",
                                   side_effect=core.Cancelled()) as deps:
                with mock.patch.object(pr_build, "_run") as runner:
                    with self.assertRaises(core.Cancelled):
                        pr_build.build_test_appimage(
                            root, lambda *_: None, threading.Event(), ask=lambda _: False)
            deps.assert_called_once()
            runner.assert_not_called()

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
