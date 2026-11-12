"""Source-build timeline reports measured durations and realistic ETA."""
from pathlib import Path
import json
import tempfile
import unittest

from wrapper.pr_build import SOURCE_STAGES, SourceTimer, elapsed_text


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class SourceETA(unittest.TestCase):
    def test_elapsed_display(self):
        self.assertEqual(elapsed_text(4.4), "4s")
        self.assertEqual(elapsed_text(65), "1min 05s")
        self.assertEqual(elapsed_text(3690), "1h 01min")

    def test_first_compilation_reports_uncertainty_then_approximation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".sw-wrapper").mkdir()
            events = []
            clock = Clock()
            report = SourceTimer(root, "test", lambda *event: events.append(event), clock=clock)
            report.step(0)
            self.assertTrue(any("ETA: calculando" in e[1] for e in events
                                if e[0] == "source-eta"))
            clock.advance(8)
            report.step(1)
            clock.advance(12)
            report.step(2)
            report.tick(force=True)
            self.assertTrue(any("ETA preliminar" in e[1] for e in events
                                if e[0] == "source-eta"))
            self.assertEqual([e[2] for e in events if e[0] == "source-step"],
                             [0, 1, 2])

    def test_completed_stages_are_saved_and_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".sw-wrapper").mkdir()
            events = []
            clock = Clock()
            timer = SourceTimer(root, "test", lambda *e: events.append(e), clock=clock)
            for index in range(len(SOURCE_STAGES)):
                timer.step(index)
                clock.advance(40 + index * 5)
                timer.tick(force=True)
            timer.finish()
            path = root / ".sw-wrapper/source-times.json"
            values = json.loads(path.read_text())["test"]
            self.assertEqual(len(values), len(SOURCE_STAGES))
            self.assertTrue(all(float(value) > 0 for value in values.values()))
            self.assertTrue(any("Concluído em" in e[1] for e in events
                                if e[0] == "source-eta"))
            second = []
            another = SourceTimer(root, "test", lambda *e: second.append(e), clock=clock)
            another.step(0)
            self.assertTrue(any("ETA estimado" in e[1] and "execuções anteriores" in e[1]
                                for e in second if e[0] == "source-eta"))

    def test_timing_history_is_separate_for_test_and_prs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".sw-wrapper").mkdir()
            clock = Clock()
            first = SourceTimer(root, "test", lambda *_: None, clock=clock)
            first.step(0)
            clock.advance(20)
            first.finish()
            second_events = []
            second = SourceTimer(root, "pr", lambda *e: second_events.append(e), clock=clock)
            second.step(0)
            self.assertTrue(any("ETA: calculando" in e[1] for e in second_events
                                if e[0] == "source-eta"))


if __name__ == "__main__":
    unittest.main()
