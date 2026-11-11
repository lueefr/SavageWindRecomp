"""Source-specific startup updates and user-controlled notification preferences."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from wrapper import core, pr_build, updates


OLD = "a" * 40
NEW = "b" * 40
PR_OLD = "c" * 40
PR_NEW = "d" * 40


def game(root):
    source = root / "root"
    (source / "exefs").mkdir(parents=True)
    (source / "romfs").mkdir()
    (source / "exefs/main").write_bytes(b"NSO0")
    (source / "exefs/main.npdm").write_bytes(b"META")
    (root / "game/save").mkdir(parents=True)
    (root / "game/save/slot.sav").write_bytes(b"save")
    (root / "game/swgame.so").write_bytes(b"native-original")
    (root / "game/swgame.so.id").write_bytes(b"native-id")


def install(root, source="test"):
    if source == "official":
        name, digest = "SavageWind-official.AppImage", "0" * 64
    elif source == "test":
        name, digest = "SavageWind-test.AppImage", "1" * 64
    else:
        name, digest = "SavageWind-custom.AppImage", "2" * 64
    image = root / name
    image.write_bytes(b"\x7fELF" + b"old-runtime")
    stat = image.stat()
    state = {
        "runtime_source": source, f"{source}_digest": digest,
        f"{source}_size": stat.st_size, f"{source}_mtime_ns": stat.st_mtime_ns,
        "built_digest": digest, "built_cpu": core.cpu_target(),
    }
    if source == "test":
        state["test_commit"] = OLD
    if source == "custom":
        prs = [{"number": 636, "sha": PR_OLD}]
        state.update({"custom_selection": pr_build.selection_key(prs),
                      "custom_prs": prs, "custom_base": OLD})
    if source == "official":
        state["official_tag"] = "old-tag"
    core.write_state(root, state)
    return image, state


class UpdateDiscoveryTests(unittest.TestCase):
    def test_official_release_change_has_real_changelog_link(self):
        result = {"tag": "v2", "digest": "f" * 64, "url": "https://example.test",
                  "size": 2300000}
        with mock.patch.object(updates, "fetch_latest", return_value=result):
            with mock.patch.object(updates, "_github_json",
                                   return_value={"body": "## Changes\n- Fix mouse input\n- Better menus\n"}):
                available = updates.check_update({
                    "runtime_source": "official", "official_digest": "0" * 64,
                    "official_tag": "v1"})
        self.assertEqual(available["source"], "official")
        self.assertIn("Fix mouse input", available["html"])
        self.assertIn("https://github.com/Revivedx/SavageWindRecomp/releases/tag/v2",
                      available["html"])
        self.assertIn("<a href=", available["html"])

    def test_test_branch_detects_commit_and_linked_comparison(self):
        def github(path):
            if path == "/branches/test":
                return {"commit": {"sha": NEW}}
            if path == "/compare/" + OLD + "..." + NEW:
                return {"commits": [{"sha": NEW, "commit": {
                    "message": "Fix rendering and menu scaling\nMore detail"}}]}
            raise AssertionError(path)
        with mock.patch.object(updates, "_github_json", side_effect=github):
            available = updates.check_update({"runtime_source": "test",
                                              "test_commit": OLD})
        self.assertEqual(available["commit"], NEW)
        self.assertIn("Fix rendering and menu scaling", available["html"])
        self.assertIn("https://github.com/Revivedx/SavageWindRecomp/commit/" + NEW,
                      available["html"])
        with mock.patch.object(updates, "_github_json",
                               return_value={"commit": {"sha": NEW}}):
            self.assertIsNone(updates.check_update({"runtime_source": "test",
                                                    "test_commit": NEW}))

    def test_custom_checks_selected_pr_and_base(self):
        def github(path):
            if path == "/branches/test":
                return {"commit": {"sha": OLD}}
            if path == "/pulls/636":
                return {"base": {"ref": "test"}, "head": {"sha": PR_NEW},
                        "title": "Fix menu"}
            if path.startswith("/compare/"):
                return {"commits": [{"sha": PR_NEW, "commit": {"message": "Menu fix"}}]}
            raise AssertionError(path)
        old = [{"number": 636, "sha": PR_OLD}]
        with mock.patch.object(updates, "_github_json", side_effect=github):
            available = updates.check_update({
                "runtime_source": "custom", "custom_base": OLD, "custom_prs": old})
        self.assertEqual(available["prs"], [{"number": 636, "sha": PR_NEW}])
        self.assertIn("pull/636", available["html"])
        self.assertIn("Menu fix", available["html"])

    def test_custom_base_moves_even_with_unchanged_prs(self):
        def github(path):
            if path == "/branches/test":
                return {"commit": {"sha": NEW}}
            if path == "/pulls/636":
                return {"base": {"ref": "test"}, "head": {"sha": PR_OLD}}
            if path.startswith("/compare/"):
                return {"commits": []}
            raise AssertionError(path)
        with mock.patch.object(updates, "_github_json", side_effect=github):
            available = updates.check_update({
                "runtime_source": "custom", "custom_base": OLD,
                "custom_prs": [{"number": 636, "sha": PR_OLD}]})
        self.assertEqual(available["base_commit"], NEW)
        self.assertIn("test", available["html"])

    def test_changelog_escapes_untrusted_github_text(self):
        data = {"body": "- <script>alert(1)</script>\n- Fixed buttons"}
        with mock.patch.object(updates, "fetch_latest",
                               return_value={"tag": "v2", "digest": "f" * 64}):
            with mock.patch.object(updates, "_github_json", return_value=data):
                change = updates.check_update({"runtime_source": "official",
                                               "official_digest": "0" * 64})
        self.assertNotIn("<script>", change["html"])
        self.assertIn("&lt;script&gt;", change["html"])

    def test_source_specific_persistent_suppression(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            updates.suppress(root, "test")
            self.assertTrue(updates.suppressed(root, "test"))
            self.assertFalse(updates.suppressed(root, "official"))
            self.assertFalse(updates.suppressed(root, "custom"))
            self.assertTrue((root / ".sw-wrapper/update-prefs.json").is_file())


class StartupTests(unittest.TestCase):
    def test_decline_without_checkbox_reprompts_next_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            game(root)
            image, state = install(root, "test")
            asked = []
            details = {"source": "test", "commit": NEW, "title": "test",
                       "html": "<a href='https://github.com/Revivedx/SavageWindRecomp/commits/test'>test</a>"}
            def refuse(info):
                asked.append(info)
                return False, False
            with mock.patch.object(updates, "check_update", return_value=details) as checker:
                with mock.patch.object(core, "build_game",
                                       side_effect=AssertionError("unexpected native build")):
                    with mock.patch.object(core.subprocess, "Popen") as launched:
                        launched.return_value.pid = 123
                        for _ in range(2):
                            answer = core.Pipeline(
                                root, lambda *_: None, lambda _: True,
                                threading.Event(), mode="startup",
                                ask_update=refuse).run()
                            self.assertEqual(answer, "launched")
            self.assertEqual(checker.call_count, 2)
            self.assertEqual(len(asked), 2)
            self.assertEqual((root / "game/save/slot.sav").read_bytes(), b"save")
            self.assertEqual((root / "game/swgame.so").read_bytes(), b"native-original")
            self.assertTrue(image.is_file())
            self.assertFalse(updates.suppressed(root, "test"))

    def test_dont_ask_anymore_suppresses_only_current_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            game(root)
            install(root, "test")
            details = {"source": "test", "commit": NEW, "title": "test", "html": ""}
            with mock.patch.object(updates, "check_update", return_value=details) as checker:
                with mock.patch.object(core.subprocess, "Popen") as launched:
                    launched.return_value.pid = 123
                    run = lambda choose: core.Pipeline(
                        root, lambda *_: None, lambda _: True, threading.Event(),
                        mode="startup", ask_update=choose).run()
                    self.assertEqual(run(lambda _: (False, True)), "launched")
                    self.assertEqual(run(lambda _: (_ for _ in ()).throw(
                        AssertionError("should not prompt"))), "launched")
            checker.assert_called_once()
            self.assertTrue(updates.suppressed(root, "test"))
            self.assertFalse(updates.suppressed(root, "official"))

    def test_accept_test_compiles_native_then_launches(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            game(root)
            old, state = install(root, "test")
            details = {"source": "test", "commit": NEW, "title": "test", "html": ""}
            def build(root_, emit, cancel, ask=None):
                path = root_ / "SavageWind-test.AppImage"
                path.write_bytes(b"\x7fELFnew-test")
                stat = path.stat()
                return {"path": path, "digest": "f" * 64, "commit": NEW,
                        "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
            phases = []
            def native(root_, binary, emit, cancel):
                phases.append(("native", binary))
                self.assertEqual(binary, old)
                (root_ / "game/swgame.so").write_bytes(b"native-updated")
                (root_ / "game/swgame.so.id").write_bytes(b"updated-id")
            with mock.patch.object(updates, "check_update", return_value=details):
                with mock.patch.object(pr_build, "build_test_appimage", side_effect=build):
                    with mock.patch.object(core, "build_game", side_effect=native):
                        with mock.patch.object(core.subprocess, "Popen") as launched:
                            launched.return_value.pid = 200
                            result = core.Pipeline(
                                root, lambda *_: None, lambda _: True,
                                threading.Event(), mode="startup",
                                ask_update=lambda _: (True, False)).run()
            self.assertEqual(result, "launched")
            self.assertEqual(len(phases), 1)
            self.assertEqual((root / "game/swgame.so").read_bytes(), b"native-updated")
            self.assertEqual((root / "game/save/slot.sav").read_bytes(), b"save")
            self.assertEqual(core.read_state(root)["test_commit"], NEW)
            self.assertEqual(core.read_state(root)["built_digest"], "f" * 64)
            self.assertFalse((root / ".sw-wrapper/rollback").exists())

    def test_failed_update_restores_previous_executable_and_native(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            game(root)
            old, state = install(root, "test")
            previous = old.read_bytes()
            details = {"source": "test", "commit": NEW, "title": "test", "html": ""}
            def build(root_, emit, cancel, ask=None):
                path = root_ / "SavageWind-test.AppImage"
                path.write_bytes(b"\x7fELFnew-test")
                stat = path.stat()
                return {"path": path, "digest": "f" * 64, "commit": NEW,
                        "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
            with mock.patch.object(updates, "check_update", return_value=details):
                with mock.patch.object(pr_build, "build_test_appimage", side_effect=build):
                    with mock.patch.object(core, "build_game",
                                           side_effect=core.InstallError("native failed")):
                        with self.assertRaisesRegex(core.InstallError, "native failed"):
                            core.Pipeline(root, lambda *_: None, lambda _: True,
                                          threading.Event(), mode="startup",
                                          ask_update=lambda _: (True, False)).run()
            self.assertEqual(old.read_bytes(), previous)
            self.assertEqual((root / "game/swgame.so").read_bytes(), b"native-original")
            self.assertEqual((root / "game/save/slot.sav").read_bytes(), b"save")
            self.assertEqual(core.read_state(root)["test_commit"], OLD)
            self.assertFalse((root / ".sw-wrapper/rollback").exists())

    def test_accept_official_release_updates_and_recompiles_before_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            game(root)
            image, state = install(root, "official")
            new_release = {"tag": "v2", "digest": "f" * 64,
                           "size": 2000000, "url": "https://example.invalid"}
            offered = {"source": "official", "title": "official", "html": "",
                       "release": new_release}
            events = []
            def download(info, root_, emit, cancel):
                self.assertEqual(info, new_release)
                path = root_ / "SavageWind-official.AppImage"
                path.write_bytes(b"\x7fELFnew-release")
                return path
            def native(root_, binary, emit, cancel):
                self.assertEqual(binary, image)
                (root_ / "game/swgame.so").write_bytes(b"native-v2")
                (root_ / "game/swgame.so.id").write_bytes(b"native-v2-id")
            with mock.patch.object(updates, "check_update", return_value=offered):
                with mock.patch.object(core, "adopt_release", return_value=None):
                    with mock.patch.object(core, "download_release", side_effect=download):
                        with mock.patch.object(core, "build_game", side_effect=native):
                            with mock.patch.object(core.subprocess, "Popen") as started:
                                started.return_value.pid = 123
                                answer = core.Pipeline(
                                    root, lambda *e: events.append(e), lambda _: True,
                                    threading.Event(), mode="startup",
                                    ask_update=lambda _: (True, False)).run()
            self.assertEqual(answer, "launched")
            self.assertEqual(core.read_state(root)["official_tag"], "v2")
            self.assertEqual(core.read_state(root)["built_digest"], "f" * 64)
            self.assertEqual((root / "game/swgame.so").read_bytes(), b"native-v2")
            self.assertEqual((root / "game/save/slot.sav").read_bytes(), b"save")

    def test_accept_updated_pr_head_tracks_new_commits(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            game(root)
            image, state = install(root, "custom")
            new_prs = [{"number": 636, "sha": PR_NEW}]
            offered = {"source": "custom", "title": "custom", "html": "",
                       "prs": new_prs, "base_commit": NEW}
            def build(root_, selected, emit, cancel, ask=None):
                self.assertEqual(selected, new_prs)
                path = root_ / "SavageWind-custom.AppImage"
                path.write_bytes(b"\x7fELFnew-custom")
                stat = path.stat()
                return {"path": path, "digest": "f" * 64,
                        "selection": pr_build.selection_key(selected),
                        "base_commit": NEW, "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns}
            def native(root_, binary, emit, cancel):
                (root_ / "game/swgame.so").write_bytes(b"new-custom-native")
                (root_ / "game/swgame.so.id").write_bytes(b"new-custom-id")
            with mock.patch.object(updates, "check_update", return_value=offered):
                with mock.patch.object(pr_build, "build_pr_appimage", side_effect=build):
                    with mock.patch.object(core, "build_game", side_effect=native):
                        with mock.patch.object(core.subprocess, "Popen") as launched:
                            launched.return_value.pid = 400
                            response = core.Pipeline(
                                root, lambda *_: None, lambda _: True,
                                threading.Event(), mode="startup",
                                ask_update=lambda _: (True, False)).run()
            self.assertEqual(response, "launched")
            self.assertEqual(core.read_state(root)["custom_prs"], new_prs)
            self.assertEqual(core.read_state(root)["custom_base"], NEW)
            self.assertEqual(core.read_state(root)["built_digest"], "f" * 64)
            self.assertEqual((root / "game/save/slot.sav").read_bytes(), b"save")

    def test_localization_keeps_changelog_links(self):
        original = {"title": "Savage Wind — Atualização dos PRs",
                    "html": "<b>Atualização da compilação com PRs</b>"
                            "<p>PRs monitorados: <a href='https://github.com/"
                            "Revivedx/SavageWindRecomp/pull/636'>#636</a></p>"}
        translated = updates.localized(original, "en")
        self.assertEqual(translated["title"], "Savage Wind — PR updates")
        self.assertIn("Tracked PRs:", translated["html"])
        self.assertIn("https://github.com/Revivedx/SavageWindRecomp/pull/636",
                      translated["html"])
        self.assertEqual(updates.localized(original, "pt-BR"), original)

    def test_offline_update_check_still_launches_cached_game(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            game(root)
            install(root, "test")
            with mock.patch.object(updates, "check_update",
                                   side_effect=core.InstallError("offline")):
                with mock.patch.object(core.subprocess, "Popen") as launched:
                    launched.return_value.pid = 300
                    self.assertEqual(core.Pipeline(
                        root, lambda *_: None, lambda _: True,
                        threading.Event(), mode="startup").run(), "launched")

    def test_reset_keeps_dont_ask_preferences(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            game(root)
            install(root, "official")
            updates.suppress(root, "official")
            core.reset_install(root, "DELETE")
            self.assertTrue(updates.suppressed(root, "official"))
            self.assertEqual((root / "game/save/slot.sav").read_bytes(), b"save")


if __name__ == "__main__":
    unittest.main()
