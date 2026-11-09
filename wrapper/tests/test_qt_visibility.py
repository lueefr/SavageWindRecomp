"""Offscreen Qt regression tests: checking for updates must never flash the installer."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from wrapper import qt_ui
except ImportError:
    QTimer = QApplication = qt_ui = None


@unittest.skipIf(qt_ui is None, "PySide6 is not installed")
class HiddenLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def installed(root):
        game = root / "extracted"
        (game / "exefs").mkdir(parents=True)
        (game / "romfs").mkdir()
        (game / "exefs/main").write_bytes(b"NSO0")
        (game / "exefs/main.npdm").write_bytes(b"META")

    def test_installed_game_does_not_map_window_on_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.installed(root)
            QTimer.singleShot(0, self.app.quit)
            with mock.patch.object(qt_ui.Window, "start", autospec=True):
                with mock.patch.object(qt_ui.Window, "show", autospec=True) as show:
                    result = qt_ui.run(root, autoplay=True)
            self.assertEqual(result, 0)
            show.assert_not_called()

    def test_new_install_shows_form(self):
        with tempfile.TemporaryDirectory() as folder:
            QTimer.singleShot(0, self.app.quit)
            with mock.patch.object(qt_ui.Window, "show", autospec=True) as show:
                result = qt_ui.run(Path(folder), autoplay=True)
            self.assertEqual(result, 0)
            show.assert_called_once()

    def test_needed_work_reveals_progress_and_keeps_log_collapsed(self):
        with tempfile.TemporaryDirectory() as folder:
            window = qt_ui.Window(Path(folder), autoplay=False)
            self.assertFalse(window.isVisible())
            window.reveal("Instalando atualização oficial...")
            self.app.processEvents()
            self.assertTrue(window.isVisible())
            self.assertTrue(window.progress_box.isVisible())
            self.assertFalse(window.log.isVisible())
            window.close()


if __name__ == "__main__":
    unittest.main()
