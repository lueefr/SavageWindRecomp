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
