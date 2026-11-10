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
        game = root / "root"
        (game / "exefs").mkdir(parents=True)
        (game / "romfs").mkdir()
        (game / "exefs/main").write_bytes(b"NSO0")
        (game / "exefs/main.npdm").write_bytes(b"META")

    def test_installed_game_without_appimage_shows_setup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.installed(root)
            QTimer.singleShot(0, self.app.quit)
            with mock.patch.object(qt_ui.Window, "start", autospec=True):
                with mock.patch.object(qt_ui.Window, "show", autospec=True) as show:
                    result = qt_ui.run(root, autoplay=True)
            self.assertEqual(result, 0)
            show.assert_called_once()

    def test_installed_game_with_verified_appimage_stays_hidden(self):
        from wrapper.core import write_state
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.installed(root)
            binary = root / "SavageWind-official.AppImage"
            binary.write_bytes(b"\x7fELF" + b"x" * 1024)
            stat = binary.stat()
            write_state(root, {"official_digest": "a" * 64, "official_size": stat.st_size,
                               "official_mtime_ns": stat.st_mtime_ns})
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

    def test_existing_files_choose_reuse_without_import(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.installed(root)
            window = qt_ui.Window(root, autoplay=False)
            try:
                self.assertFalse(window.source.isEnabled())
                self.assertFalse(window.tabs.isTabEnabled(1))
                self.assertIn("AppImage missing", window.existing_label.text())
                with mock.patch.object(window, "start") as start:
                    window.install_files()
                start.assert_called_once_with("__reuse__")
            finally:
                window.close()

    def test_pr_tab_keeps_nsp_source_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            window = qt_ui.Window(root, autoplay=False)
            try:
                files = {}
                for name in ("base", "update", "keys"):
                    path = root / (name + ".nsp")
                    path.write_bytes(b"test")
                    window.packages[name].setText(str(path))
                    files[name] = str(path)
                window.tabs.setCurrentIndex(1)
                window.tabs.setCurrentIndex(2)
                self.assertEqual(window.source_tab, 1)
                with mock.patch.object(window, "start") as start:
                    window.install_files()
                start.assert_called_once_with(files)
            finally:
                window.close()

    def test_gui_language_defaults_to_english_and_persists(self):
        from wrapper.i18n import get_language
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            window = qt_ui.Window(root, autoplay=False)
            try:
                self.assertEqual(window.language, "en")
                self.assertEqual(window.form.title(), "Game files")
                index = window.language_box.findData("pt-BR")
                window.language_box.setCurrentIndex(index)
                self.assertEqual(window.form.title(), "Arquivos do jogo")
                self.assertEqual(get_language(root), "pt-BR")
            finally:
                window.close()

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
