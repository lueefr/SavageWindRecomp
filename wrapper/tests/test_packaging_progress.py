"""Regression checks for long, quiet upstream AppImage packaging."""
from pathlib import Path
import sys
import tempfile
import threading
import unittest

from wrapper import pr_build


class PackagingProgressTests(unittest.TestCase):
    def test_upstream_dependency_and_appimage_substeps_are_observable(self):
        cases = {
            "Building SDL3-3.4.16": "SDL3",
            "Building FFmpeg libraries": "FFmpeg",
            "Compiling shaderc_shared": "shaderc",
            "Preparing zig-linux-x86_64": "Zig",
            "appimagetool --runtime-file": "AppImage filesystem",
        }
        for output, expectation in cases.items():
            with self.subTest(output=output):
                self.assertIn(expectation, pr_build._package_detail(output))

    def test_unrecognized_output_does_not_claim_progress(self):
        self.assertIsNone(pr_build._package_detail("checking archive signatures"))

    def test_package_command_updates_activity_without_guessing_percent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".sw-wrapper").mkdir()
            events = []
            emit = lambda *event: events.append(event)
            meter = pr_build.SourceTimer(root, "test", emit)
            meter.step(4)
            script = ("print('Building SDL3 dependency', flush=True);"
                      "print('Creating filesystem...', flush=True)")
            with (root / "package.log").open("w") as log:
                pr_build._run([sys.executable, "-u", "-c", script], root,
                              emit, threading.Event(), log, meter=meter)
            details = [item[1] for item in events if item[0] == "source-eta"]
            self.assertTrue(any("SDL3 dependencies" in entry for entry in details))
            self.assertTrue(any("AppImage filesystem" in entry for entry in details))
            self.assertFalse(any(item[0] == "progress" for item in events))
            self.assertIn("Creating filesystem", (root / "package.log").read_text())


if __name__ == "__main__":
    unittest.main()
