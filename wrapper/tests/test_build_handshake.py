"""Exercise the official build handshake with a tiny local executable."""
import os
from pathlib import Path
import tempfile
import threading
import unittest

from wrapper import core


def helper(root, output, exit_code=0, artifacts=True, boot=False):
    script = root / "official-test"
    body = [
        "#!/usr/bin/env python3",
        "import os, pathlib, sys, time",
        "folder = pathlib.Path(sys.argv[1])",
        "assert folder.is_dir()",
        "assert os.environ['SAVAGEWIND_DATA_ROOT'] == str(folder.parent / 'game')",
        "assert os.environ.get('SAVAGEWIND_LAUNCHER_EXTRACT') == ''",
    ]
    if artifacts:
        body += [
            "(folder / 'swgame.so').write_bytes(b'compiled ELF placeholder')",
            "(folder / 'swgame.so.id').write_text('official-built-id\\n')",
        ]
    body += [f"print({line!r}, flush=True)" for line in output]
    if boot:
        body.append("time.sleep(30)")
    body.append(f"sys.exit({exit_code})")
    script.write_text("\n".join(body) + "\n")
    script.chmod(0o755)
    return script


class BuildHandshakeTests(unittest.TestCase):
    def run_build(self, output, exit_code=0, artifacts=True, boot=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "root").mkdir()
            (root / ".sw-wrapper").mkdir()
            program = helper(root, output, exit_code=exit_code, artifacts=artifacts, boot=boot)
            events = []
            try:
                core.build_game(root, program, lambda *args: events.append(args), threading.Event())
                self.assertTrue((root / "game/swgame.so").is_file())
                self.assertTrue((root / "game/swgame.so.id").is_file())
                self.assertFalse((root / "root/swgame.so").exists())
                return True, events, (root / ".sw-wrapper/build.log").read_text()
            except core.InstallError as error:
                return False, str(error), (root / ".sw-wrapper/build.log").read_text()

    def test_silent_compiler_still_updates_measured_percentage(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "root").mkdir()
            (root / ".sw-wrapper").mkdir()
            script = root / "silent-compiler"
            script.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, sys, time\n"
                "game = pathlib.Path(sys.argv[1])\n"
                "work = game / '.gamebuild'\n"
                "work.mkdir()\n"
                "(work / 'layout.txt').write_text('main 1000 100\\n')\n"
                "(work / 'gen_main.log').write_text('main: progress 50/100 files 5\\n')\n"
                "(work / 'main_000.o').write_bytes(b'object')\n"
                "time.sleep(2.2)\n"
                "(game / 'swgame.so').write_bytes(b'compiled library')\n"
                "(game / 'swgame.so.id').write_text('valid-id\\n')\n"
            )
            script.chmod(0o755)
            events = []
            core.build_game(root, script, lambda *args: events.append(args),
                            threading.Event())
            measured = [(message, percent) for kind, message, percent in events
                        if kind == "live" and percent is not None and percent > 0]
            self.assertTrue(measured, "The UI must display a measured percentage without stdout")
            self.assertTrue(any("Gerando C e compilando" in msg for msg, _ in measured))
            self.assertTrue((root / "game/swgame.so").exists())

    def test_ready_and_complete_artifacts(self):
        ok, _, log = self.run_build(("[gamebuild] done in 30s",
                                     "[launcher] setup: Game files ready. -> READY"))
        self.assertTrue(ok)
        self.assertIn("accepted=True", log)

    def test_completed_build_without_ready_is_recovered(self):
        ok, _, log = self.run_build(("[gamebuild] done in 30s",))
        self.assertTrue(ok)
        self.assertIn("build_done=True", log)

    def test_successful_compilation_survives_post_build_process_error(self):
        ok, _, log = self.run_build(("[gamebuild] done in 30s",), exit_code=1)
        self.assertTrue(ok)
        self.assertIn("exit=1", log)

    def test_silent_success_with_new_native_pair_is_accepted(self):
        ok, _, log = self.run_build(())
        self.assertTrue(ok)
        self.assertIn("newly_built=True", log)
        self.assertIn("accepted=True", log)

    def test_silent_nonzero_exit_is_rejected(self):
        ok, message, log = self.run_build((), exit_code=1)
        self.assertFalse(ok)
        self.assertIn("accepted=False", log)

    def test_explicit_not_ready_is_not_ignored(self):
        ok, message, log = self.run_build(("[gamebuild] done in 30s",
                                            "[launcher] setup: game invalid -> NOT READY"))
        self.assertFalse(ok)
        self.assertIn("NOT READY", message)

    def test_missing_native_code_cannot_be_accepted(self):
        ok, message, log = self.run_build(("[gamebuild] done in 30s",), artifacts=False)
        self.assertFalse(ok)
        self.assertIn("swgame.so", message)

    def test_unconfirmed_build_is_not_assumed_good(self):
        ok, message, log = self.run_build(("[gamebuild] generator starting",), exit_code=1)
        self.assertFalse(ok)
        self.assertIn("confirmação", message)

    def test_game_started_instead_of_exiting_build_hook(self):
        ok, _, log = self.run_build(("[gamebuild] done in 30s",
                                    "nn::init::Start at 12345 (init, init, fini)"), boot=True)
        self.assertTrue(ok)
        self.assertIn("game_started=True", log)


if __name__ == "__main__":
    unittest.main()
