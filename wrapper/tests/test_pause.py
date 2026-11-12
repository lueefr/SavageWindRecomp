"""Pausing source and native compilation without losing the active subprocess."""
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from wrapper import core, pr_build
from wrapper.pause import PauseControl


def stopped(pid):
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="ascii")
    except OSError:
        return False
    state = re.search(r"(?m)^State:\s+([A-Z])", status)
    return state is not None and state.group(1) in ("T", "t")


def spin(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class PauseTests(unittest.TestCase):
    def test_clock_freezes_and_resume_preserves_elapsed_time(self):
        control = PauseControl()
        first = control.clock()
        self.assertTrue(control.pause())
        paused = control.clock()
        time.sleep(0.13)
        self.assertAlmostEqual(control.clock(), paused, delta=0.02)
        self.assertFalse(control.pause())
        self.assertTrue(control.resume())
        self.assertFalse(control.resume())
        self.assertLess(control.clock() - first, 0.08)

    def test_cancel_while_paused_does_not_deadlock(self):
        control = PauseControl()
        cancel = threading.Event()
        errors = []
        control.pause()
        worker = threading.Thread(target=lambda: self._wait(control, cancel, errors))
        worker.start()
        try:
            cancel.set()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [core.Cancelled])
        finally:
            control.resume()
            worker.join(timeout=2)

    @staticmethod
    def _wait(control, cancel, errors):
        try:
            control.wait(cancel)
        except BaseException as error:
            errors.append(type(error))

    def test_pauses_and_resumes_source_compiler_process(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            control = PauseControl()
            cancel = threading.Event()
            cancel.pause = control
            errors = []
            command = [sys.executable, "-u", "-c",
                       "import time; print('working', flush=True); time.sleep(1.2)"]
            def build():
                try:
                    with (root / "source.log").open("w") as log:
                        pr_build._run(command, root, lambda *_: None, cancel, log)
                except BaseException as error:
                    errors.append(error)
            worker = threading.Thread(target=build)
            worker.start()
            try:
                self.assertTrue(spin(lambda: control._process is not None))
                proc = control._process
                self.assertTrue(control.pause())
                self.assertTrue(spin(lambda: stopped(proc.pid)))
                frozen = control.clock()
                time.sleep(0.15)
                self.assertAlmostEqual(control.clock(), frozen, delta=0.02)
                self.assertTrue(control.resume())
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])
                self.assertIn("working", (root / "source.log").read_text())
            finally:
                control.resume()
                cancel.set()
                worker.join(timeout=4)

    def test_pauses_and_resumes_native_game_compilation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".sw-wrapper").mkdir()
            (root / "root").mkdir()
            binary = root / "native-test"
            binary.write_text("#!" + sys.executable + "\n"
                              "import time\n"
                              "print('[launcher] setup: -> READY', flush=True)\n"
                              "time.sleep(1.3)\n"
                              "print('[gamebuild] done in 1', flush=True)\n")
            binary.chmod(0o755)
            cancel = threading.Event()
            control = PauseControl()
            cancel.pause = control
            errors = []
            def build():
                try:
                    with mock.patch.object(core, "compiled_pair", return_value=True):
                        core._run_game_build(root, binary, lambda *_: None,
                                             cancel, root / "root")
                except BaseException as error:
                    errors.append(error)
            worker = threading.Thread(target=build)
            worker.start()
            try:
                self.assertTrue(spin(lambda: control._process is not None))
                proc = control._process
                self.assertTrue(control.pause())
                self.assertTrue(spin(lambda: stopped(proc.pid)))
                self.assertTrue(control.resume())
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])
                self.assertIn("accepted=True", (root / ".sw-wrapper/build.log").read_text())
            finally:
                control.resume()
                cancel.set()
                worker.join(timeout=4)


if __name__ == "__main__":
    unittest.main()
