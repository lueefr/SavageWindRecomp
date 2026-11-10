#!/usr/bin/env python3
"""Regression tests for main-only, wrapper-scoped portable release planning."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import portable_release as release


class PortableReleaseTests(unittest.TestCase):
    def setUp(self):
        self.saved = Path.cwd()
        self.temporary = tempfile.TemporaryDirectory()
        os.chdir(self.temporary.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "CI Test")
        self.git("config", "user.email", "ci@example.invalid")
        self.write("README.md", "# Initial docs\n")
        self.write("wrapper/core.py", "# Base wrapper\n")
        self.commit("Initial wrapper")
        self.git("tag", "portable-preview")

    def tearDown(self):
        os.chdir(self.saved)
        self.temporary.cleanup()

    @staticmethod
    def git(*args):
        return subprocess.check_output(("git", *args), text=True).strip()

    @staticmethod
    def write(name, contents):
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)

    def test_filter_excludes_documentation_and_tests(self):
        included = (
            "wrapper/core.py", "wrapper/qt_ui.py",
            "tools/linux/build_wrapper.sh", "tools/linux/wrapper_AppRun",
            "tools/linux/nsp_vendor/extract_exefs.py",
            "tools/build_hactool.py", "docs/icon/icon_1024.png",
            ".github/workflows/wrapper.yml",
        )
        excluded = (
            "README.md", "CHANGELOG.md", "docs/portable-wrapper.md",
            "wrapper/tests/test_core.py", "wrapper/README.md",
            "tools/linux/nsp_vendor/README.md", "tools/linux/nsp_vendor/LICENSE",
            "runtime/nvn/vulkan/fsr3.cpp", "tools/linux/test_import_game.py",
        )
        for path in included:
            with self.subTest(path=path):
                self.assertTrue(release.is_wrapper_file(path))
        for path in excluded:
            with self.subTest(path=path):
                self.assertFalse(release.is_wrapper_file(path))

    def test_documentation_only_does_not_release(self):
        self.write("README.md", "# Updated docs\n")
        self.write("CHANGELOG.md", "Docs only\n")
        self.commit("Docs only")
        base, tag, changed = release.release_plan()
        self.assertEqual(base, "portable-preview")
        self.assertEqual(tag, "portable-v0.1.0")
        self.assertEqual(changed, [])

    def test_wrapper_changes_create_versioned_notes(self):
        self.write("README.md", "# Docs only\n")
        self.commit("Documentation update")
        self.write("wrapper/core.py", "# Actual wrapper change\n")
        self.commit("Repair wrapper")
        base, tag, changed = release.release_plan()
        self.assertEqual((base, tag, changed),
                         ("portable-preview", "portable-v0.1.0", ["wrapper/core.py"]))
        messages = release.change_messages(base)
        self.assertTrue(any("Repair wrapper" in msg for msg in messages))
        self.assertFalse(any("Documentation update" in msg for msg in messages))
        self.git("tag", "portable-v0.1.0")
        self.write("wrapper/core.py", "# Another change\n")
        self.commit("Another wrapper change")
        base, tag, changed = release.release_plan()
        self.assertEqual((base, tag, changed),
                         ("portable-v0.1.0", "portable-v0.1.1", ["wrapper/core.py"]))

    def test_reverted_wrapper_change_does_not_release(self):
        self.write("wrapper/core.py", "# Temporary change\n")
        self.commit("Temporary wrapper edit")
        self.write("wrapper/core.py", "# Base wrapper\n")
        self.commit("Revert wrapper edit")
        self.assertEqual(release.release_plan()[2], [])


if __name__ == "__main__":
    unittest.main()
