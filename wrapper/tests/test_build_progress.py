"""Synthetic tests for observable native recompilation progress and ETA."""
import tempfile
import unittest
from pathlib import Path

from wrapper.build_progress import BuildMonitor, duration


class BuildProgressTests(unittest.TestCase):
    def test_elapsed_time(self):
        self.assertEqual(duration(9), "9s")
        self.assertEqual(duration(85), "1min 25s")

    def test_waiting_for_layout_has_indeterminate_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            snapshot = BuildMonitor(folder, started=0).sample(now=4)
            self.assertIsNone(snapshot.percent)
            self.assertEqual(snapshot.phase, "layout")

    def test_actual_generation_and_object_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder) / "root/.gamebuild"
            work.mkdir(parents=True)
            (work / "layout.txt").write_text("main 1000 8000\nsubsdk0 5000 2000\n")
            (work / "gen_main.log").write_text("main: progress 100/200 files 5\n")
            (work / "gen_subsdk0.log").write_text("subsdk0: progress 50/100 files 2\n")
            for name in ("layout.o", "gamelib_entry.o", "main_000.o", "main_001.o", "subsdk0_000.o"):
                (work / name).write_bytes(b"obj")
            snapshot = BuildMonitor(folder, started=0).sample(now=10)
            self.assertEqual(snapshot.phase, "compile")
            self.assertAlmostEqual(snapshot.percent, 30.8, delta=0.1)
            self.assertIn("main: 100/200", snapshot.details)
            self.assertIn("Compilador: 5/18", snapshot.details)
            self.assertIn("ETA: calculando", snapshot.message)

    def test_eta_uses_recent_measured_progress_only(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder) / "root/.gamebuild"
            work.mkdir(parents=True)
            (work / "layout.txt").write_text("main 1000 100\n")
            log = work / "gen_main.log"
            monitor = BuildMonitor(folder, started=0)
            for instant, done in ((25, 25), (45, 55), (65, 85)):
                log.write_text(f"main: progress {done}/100 files {done // 5}\n")
                snapshot = monitor.sample(now=instant)
            self.assertIsNotNone(snapshot.eta)
            self.assertIn("ETA estimado", snapshot.message)
            self.assertGreater(snapshot.eta, 0)

    def test_link_phase_is_measured_and_not_false_completed(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder) / "root/.gamebuild"
            work.mkdir(parents=True)
            (work / "layout.txt").write_text("main 1000 100\n")
            (work / "link.rsp").write_text("main_000.o\n")
            (work / "main_000.o").write_bytes(b"obj")
            snapshot = BuildMonitor(folder, started=0).sample(now=120)
            self.assertEqual(snapshot.phase, "link")
            self.assertEqual(snapshot.percent, 95)
            self.assertIn("Vinculando", snapshot.message)
            self.assertIsNone(snapshot.eta)

    def test_repeated_log_reads_observe_new_appended_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder) / "root/.gamebuild"
            work.mkdir(parents=True)
            (work / "layout.txt").write_text("main 1000 100\n")
            log = work / "gen_main.log"
            log.write_text("main: progress 20/100 files 2\n")
            monitor = BuildMonitor(folder, started=0)
            first = monitor.sample(now=5)
            with log.open("a") as file:
                file.write("main: progress 80/100 files 8\n")
            last = monitor.sample(now=10)
            self.assertGreater(last.percent, first.percent)


if __name__ == "__main__":
    unittest.main()
