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
                with mock.patch.object(qt_ui.Window, "prompt_initial_source", autospec=True):
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
            with mock.patch.object(qt_ui.Window, "prompt_initial_source", autospec=True):
                with mock.patch.object(qt_ui.Window, "show", autospec=True) as show:
                    result = qt_ui.run(Path(folder), autoplay=True)
            self.assertEqual(result, 0)
            show.assert_called_once()

    def test_initial_chooser_defaults_to_official_and_starts_install_flow(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.installed(root)
            window = qt_ui.Window(root, autoplay=False)
            try:
                def choose_official(box):
                    self.assertIn("No Savage Wind AppImage", box.text())
                    button = box.defaultButton()
                    self.assertIn("Official", button.text())
                    button.click()
                    return 0
                with mock.patch.object(qt_ui.QMessageBox, "exec", choose_official):
                    with mock.patch.object(window, "start") as start:
                        window.prompt_initial_source()
                self.assertFalse(window.test_branch.isChecked())
                self.assertFalse(window.custom.isChecked())
                self.assertTrue(window.prs_changed)
                start.assert_called_once_with("__reuse__")
            finally:
                window.close()

    def test_pause_button_toggles_resume_and_cancel_unfreezes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            window = qt_ui.Window(root, autoplay=False)
            try:
                window.show()
                self.assertFalse(window.pause_button.isEnabled())
                window.running = True
                window.pause_button.setEnabled(True)
                window.toggle_pause()
                self.assertTrue(window.pause_control.paused)
                self.assertEqual(window.pause_button.text(), "Resume")
                self.assertEqual(window.stage.text(), "Paused")
                window.toggle_pause()
                self.assertFalse(window.pause_control.paused)
                self.assertEqual(window.pause_button.text(), "Pause")
                window.toggle_pause()
                window.abort()
                self.assertFalse(window.pause_control.paused)
                self.assertFalse(window.pause_button.isEnabled())
                self.assertTrue(window.cancel_event.is_set())
                window.running = False
            finally:
                window.running = False
                window.pause_control.resume()
                window.close()

    def test_build_button_only_shows_when_installed_runtime_exists(self):
        from wrapper.core import write_state
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.installed(root)
            window = qt_ui.Window(root, autoplay=False)
            try:
                window.show()
                window.game_status()
                self.assertFalse(window.build_button.isVisible())
                self.assertTrue(window.install.isVisible())
                image = root / "SavageWind-official.AppImage"
                image.write_bytes(b"\x7fELF" + b"x" * 1024)
                st = image.stat()
                write_state(root, {"runtime_source": "official",
                                   "official_digest": "1" * 64,
                                   "official_size": st.st_size,
                                   "official_mtime_ns": st.st_mtime_ns})
                window.game_status()
                self.assertTrue(window.build_button.isVisible())
                self.assertFalse(window.install.isVisible())
            finally:
                window.close()

    def test_update_dialog_has_clickable_links_and_default_opt_out_unchecked(self):
        from PySide6.QtWidgets import QDialog, QTextBrowser, QCheckBox, QDialogButtonBox
        with tempfile.TemporaryDirectory() as folder:
            window = qt_ui.Window(Path(folder), autoplay=False)
            try:
                data = {
                    "title": "Savage Wind update",
                    "html": "<b>Updates</b><p><a href='https://github.com/"
                            "Revivedx/SavageWindRecomp/pull/636'>PR #636</a></p>"}
                def reject(dialog):
                    browser = dialog.findChild(QTextBrowser)
                    self.assertTrue(browser.openExternalLinks())
                    self.assertIn("PR #636", browser.toPlainText())
                    optout = dialog.findChild(QCheckBox)
                    self.assertFalse(optout.isChecked())
                    buttons = dialog.findChild(QDialogButtonBox)
                    self.assertTrue(buttons.button(QDialogButtonBox.StandardButton.No).isDefault())
                    return QDialog.DialogCode.Rejected
                with mock.patch.object(qt_ui.QDialog, "exec", reject):
                    self.assertEqual(window.update_dialog(data), (False, False))
            finally:
                window.close()

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

    def test_test_branch_option_is_exclusive_with_pull_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            window = qt_ui.Window(root, autoplay=False)
            try:
                window.custom.setChecked(True)
                self.assertFalse(window.test_branch.isChecked())
                window.test_branch.setChecked(True)
                self.assertTrue(window.test_branch.isChecked())
                self.assertFalse(window.custom.isChecked())
                self.assertFalse(window.pr_list.isVisible())
                self.assertEqual(window.tabs.tabText(2), "Compilações locais" if window.language == "pt-BR" else "Source builds")
                with mock.patch.object(window, "start") as start:
                    with mock.patch.object(qt_ui.QMessageBox, "warning") as warning:
                        window.source.setText("")
                        window.install_files()
                # With no game files, a source folder is still required.
                start.assert_not_called()
                warning.assert_called_once()
            finally:
                window.close()

    def test_test_branch_uses_existing_game_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.installed(root)
            window = qt_ui.Window(root, autoplay=False)
            try:
                window.test_branch.setChecked(True)
                self.assertFalse(window.custom.isChecked())
                with mock.patch.object(window, "start") as start:
                    window.install_files()
                start.assert_called_once_with("__reuse__")
            finally:
                window.close()

    def test_appimage_only_button_never_starts_native_or_game(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            window = qt_ui.Window(root, autoplay=False)
            try:
                window.test_branch.setChecked(True)
                with mock.patch.object(window, "start") as start:
                    window.build_image()
                start.assert_called_once_with(None, mode="build_only", prs="test")
            finally:
                window.close()

    def test_explicit_native_recompile_uses_optional_test_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.installed(root)
            window = qt_ui.Window(root, autoplay=False)
            try:
                window.test_branch.setChecked(True)
                with mock.patch.object(window, "start") as start:
                    window.recompile_game()
                start.assert_called_once_with("__reuse__", mode="native", prs="test")
                with mock.patch.object(window, "start") as start:
                    window.play_existing()
                start.assert_called_once_with("__reuse__", mode="play", prs=None)
            finally:
                window.close()

    def test_source_steps_and_eta_do_not_use_game_recompilation_steps(self):
        with tempfile.TemporaryDirectory() as folder:
            window = qt_ui.Window(Path(folder), autoplay=False)
            try:
                window.events.put(("event", "source-step", "Prepare compiler", 2))
                window.events.put(("event", "source-eta",
                                   "Decorrido: 1min 30s · ETA estimado: ~4min", None))
                window.pump()
                self.assertTrue(window._source_mode)
                self.assertIn("Prepare compiler", window.step_view.item(2).text())
                self.assertIn("ETA", window.eta.text())
                window.events.put(("event", "native-start", "Recompiling native game code...", None))
                window.pump()
                self.assertFalse(window._source_mode)
                self.assertIn("Recompilation", window.step_view.item(3).text())
            finally:
                window.close()

    def test_post_appimage_prompt_defaults_to_no_and_is_translated(self):
        from threading import Event
        from PySide6.QtWidgets import QMessageBox
        from wrapper.i18n import tr
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            window = qt_ui.Window(root, autoplay=False)
            try:
                language_index = window.language_box.findData("pt-BR")
                window.language_box.setCurrentIndex(language_index)
                message = (
                    "The AppImage is ready.\n\n"
                    "Recompile the game's native library (swgame.so) using this AppImage now?\n\n"
                    "This can take a long time and will replace the existing native library. "
                    "Saves, settings and extracted game files will be preserved.\n\n"
                    "Choose No to finish without recompiling the game.")
                self.assertIn("Deseja recompilar", tr(message, "pt-BR"))
                answer, gate = [], Event()
                window.events.put(("question", message, answer, gate))
                with mock.patch.object(qt_ui.QMessageBox, "question",
                                       return_value=QMessageBox.StandardButton.No) as dialog:
                    window.pump()
                self.assertTrue(gate.is_set())
                self.assertEqual(answer, [False])
                self.assertIn("Deseja recompilar", dialog.call_args.args[2])
                self.assertEqual(dialog.call_args.args[-1],
                                 QMessageBox.StandardButton.No)
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
                self.assertEqual(
                    [window.tabs.tabText(i) for i in range(window.tabs.count())],
                    ["Extracted folder", "NSP / XCI", "Source builds"])
                index = window.language_box.findData("pt-BR")
                window.language_box.setCurrentIndex(index)
                self.assertEqual(window.form.title(), "Arquivos do jogo")
                self.assertEqual(
                    [window.tabs.tabText(i) for i in range(window.tabs.count())],
                    ["Pasta extraída", "NSP / XCI", "Compilações locais"])
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
