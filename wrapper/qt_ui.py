"""Native Qt setup/progress UI, using the system KDE widget theme."""
import queue
import threading
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QHBoxLayout, QGroupBox, QFileDialog, QProgressBar, QPlainTextEdit,
    QMessageBox, QListWidget, QListWidgetItem, QTabWidget, QComboBox, QCheckBox, QInputDialog,
    QDialog, QDialogButtonBox, QTextBrowser)

from .core import (Pipeline, Cancelled, InstallError, valid_game, compiled_code,
                   read_state, installed_runtime, reset_install)
from .migration import legacy_folder
from .i18n import LANGUAGES, get_language, set_language, tr, progress_text
from .pr_build import open_prs
from .updates import localized


class Window(QWidget):
    def __init__(self, root, autoplay=True):
        super().__init__()
        self.root = Path(root)
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.running = False
        self.language = get_language(self.root)
        self.local_labels = []
        self.tab_labels = []
        self.pr_entries = []
        self.prs_changed = False
        self.source_tab = 0
        state = read_state(self.root)
        self.current_custom = state.get("runtime_source") == "custom"
        self.current_test = state.get("runtime_source") == "test"
        self.has_game = valid_game(self.root / "root")
        self.old_game = valid_game(self.root / "extracted")
        self.autostart = bool(autoplay and self.has_game and installed_runtime(self.root, state))
        self.setWindowTitle(tr("Savage Wind — Installer", self.language))
        self.resize(750, 680)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        heading = QLabel("Savage Wind")
        font = heading.font()
        font.setPointSize(font.pointSize() + 8)
        font.setBold(True)
        heading.setFont(font)
        top.addWidget(heading, 1)
        lang_title = QLabel()
        self.label(lang_title, "Language")
        top.addWidget(lang_title)
        self.language_box = QComboBox()
        for code, name in LANGUAGES:
            self.language_box.addItem(name, code)
        index = self.language_box.findData(self.language)
        self.language_box.setCurrentIndex(max(0, index))
        self.language_box.currentIndexChanged.connect(self.language_changed)
        top.addWidget(self.language_box)
        layout.addLayout(top)
        subtitle = QLabel()
        self.label(subtitle, "Portable installer · Savage Wind Recomp")
        layout.addWidget(subtitle)

        self.form = QGroupBox()
        self.label(self.form, "Game files", "setTitle")
        fields = QVBoxLayout(self.form)
        intro = QLabel()
        self.label(intro, "Choose extracted files or import your own NSP/XCI packages.")
        intro.setWordWrap(True)
        fields.addWidget(intro)
        self.tabs = QTabWidget()
        extracted_page = QWidget()
        extracted_fields = QVBoxLayout(extracted_page)
        game_label = QLabel()
        self.label(game_label, "Path containing exefs/ and romfs/:")
        extracted_fields.addWidget(game_label)
        choose_row = QHBoxLayout()
        self.source = QLineEdit()
        choose_row.addWidget(self.source, 1)
        self.browse_source = QPushButton()
        self.label(self.browse_source, "Browse...")
        self.browse_source.clicked.connect(self.pick)
        choose_row.addWidget(self.browse_source)
        extracted_fields.addLayout(choose_row)
        self.tabs.addTab(extracted_page, tr("Extracted folder", self.language))
        self.tab_labels.append((extracted_page, "Extracted folder"))

        packages_page = QWidget()
        packages_fields = QVBoxLayout(packages_page)
        help_label = QLabel()
        self.label(help_label, "Base NSP/XCI, update NSP and prod.keys from your own copy.")
        help_label.setWordWrap(True)
        packages_fields.addWidget(help_label)
        self.packages = {}
        for name, title, pattern in (
                ("base", "Base game", "Switch packages (*.nsp *.xci);;All files (*)"),
                ("update", "Update", "NSP packages (*.nsp);;All files (*)"),
                ("keys", "Keys", "prod.keys (*.keys);;All files (*)")):
            title_label = QLabel()
            self.label(title_label, title)
            packages_fields.addWidget(title_label)
            row = QHBoxLayout()
            field = QLineEdit()
            row.addWidget(field, 1)
            browse = QPushButton()
            self.label(browse, "Browse...")
            browse.clicked.connect(lambda checked=False, f=field, t=title, p=pattern:
                                   self.pick_file(f, t, p))
            row.addWidget(browse)
            packages_fields.addLayout(row)
            self.packages[name] = field
        self.tabs.addTab(packages_page, tr("NSP / XCI", self.language))
        self.tab_labels.append((packages_page, "NSP / XCI"))
        self.tabs.currentChanged.connect(self.remember_source_tab)
        fields.addWidget(self.tabs)
        self.existing_label = QLabel()
        self.existing_label.setWordWrap(True)
        fields.addWidget(self.existing_label)
        self.existing_label.setText(
            tr("Native game library detected." if compiled_code(self.root)
               else "No compiled game detected.", self.language) + " " +
            tr("AppImage detected." if installed_runtime(self.root, state)
               else "AppImage missing — setup required.", self.language))
        note = QLabel()
        self.label(note, "Saves, settings and caches are stored in game/ next to this launcher.")
        note.setWordWrap(True)
        fields.addWidget(note)

        self.runtime_box = QGroupBox()
        self.label(self.runtime_box, "Runtime source", "setTitle")
        runtime_fields = QVBoxLayout(self.runtime_box)
        self.source_hint = QLabel()
        self.label(self.source_hint, "Leave both unchecked to use the official upstream AppImage.")
        self.source_hint.setWordWrap(True)
        runtime_fields.addWidget(self.source_hint)
        self.test_branch = QCheckBox()
        self.label(self.test_branch, "Build upstream test branch (no PRs)", "setText")
        self.test_branch.setChecked(self.current_test)
        self.test_branch.toggled.connect(self.test_toggled)
        runtime_fields.addWidget(self.test_branch)
        self.test_info = QLabel()
        self.label(self.test_info, "Compiles the latest upstream test commit locally; no pull requests are applied.")
        self.test_info.setWordWrap(True)
        runtime_fields.addWidget(self.test_info)
        self.custom = QCheckBox()
        self.label(self.custom, "Build from selected pull requests", "setText")
        self.custom.setChecked(self.current_custom)
        runtime_fields.addWidget(self.custom)
        self.pr_intro = QLabel()
        self.label(self.pr_intro, "Select PRs to merge with upstream test. Missing build tools can be installed with your permission.")
        self.pr_intro.setWordWrap(True)
        runtime_fields.addWidget(self.pr_intro)
        self.pr_refresh = QPushButton()
        self.label(self.pr_refresh, "Refresh pull requests")
        self.pr_refresh.clicked.connect(self.fetch_prs)
        runtime_fields.addWidget(self.pr_refresh)
        self.pr_list = QListWidget()
        self.pr_list.setWordWrap(True)
        self.pr_list.setMinimumHeight(140)
        runtime_fields.addWidget(self.pr_list)
        self.custom.toggled.connect(self.custom_toggled)
        self.custom_toggled(self.current_custom)
        self.test_toggled(self.current_test)
        self.tabs.addTab(self.runtime_box, tr("Source builds", self.language))
        self.tab_labels.append((self.runtime_box, "Source builds"))

        actions = QHBoxLayout()
        self.install = QPushButton()
        self.label(self.install, "Install and play")
        self.install.clicked.connect(self.install_files)
        actions.addWidget(self.install)
        mode_actions = QHBoxLayout()
        self.play_button = QPushButton()
        self.label(self.play_button, "Play existing game")
        self.play_button.clicked.connect(self.play_existing)
        mode_actions.addWidget(self.play_button)
        self.build_button = QPushButton()
        self.label(self.build_button, "Build AppImage")
        self.build_button.clicked.connect(self.build_image)
        mode_actions.addWidget(self.build_button)
        self.native_button = QPushButton()
        self.label(self.native_button, "Recompile game code")
        self.native_button.clicked.connect(self.recompile_game)
        mode_actions.addWidget(self.native_button)
        self.delete_button = QPushButton()
        self.label(self.delete_button, "Delete installation")
        self.delete_button.clicked.connect(self.delete_installation)
        actions.addWidget(self.delete_button)
        fields.addLayout(actions)
        fields.addLayout(mode_actions)
        layout.addWidget(self.form)

        self.progress_box = QGroupBox()
        self.label(self.progress_box, "Progress", "setTitle")
        tasks = QVBoxLayout(self.progress_box)
        self.stage = QLabel()
        self.label(self.stage, "Preparing...")
        self.stage.setWordWrap(True)
        tasks.addWidget(self.stage)
        self.eta = QLabel("")
        self.eta.setWordWrap(True)
        tasks.addWidget(self.eta)
        self.step_names = ["Storage", "Game files", "Updates", "Recompilation", "Launch game"]
        self.source_steps = ["Fetch sources", "Apply pull requests", "Prepare compiler",
                             "Compile runtime", "Package AppImage", "Publish AppImage"]
        self._source_mode = False
        self.step_view = QListWidget()
        self.step_view.setFixedHeight(128)
        tasks.addWidget(self.step_view)
        self.mark_step(-1)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        tasks.addWidget(self.bar)
        buttons = QHBoxLayout()
        self.toggle = QPushButton()
        self.label(self.toggle, "Show log")
        self.toggle.clicked.connect(self.toggle_log)
        buttons.addWidget(self.toggle)
        buttons.addStretch(1)
        self.back_button = QPushButton()
        self.label(self.back_button, "Back")
        self.back_button.clicked.connect(self.back)
        self.back_button.hide()
        buttons.addWidget(self.back_button)
        self.cancel_button = QPushButton()
        self.label(self.cancel_button, "Cancel")
        self.cancel_button.clicked.connect(self.abort)
        buttons.addWidget(self.cancel_button)
        tasks.addLayout(buttons)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.live_start = None
        self.log.setMaximumBlockCount(600)
        self.log.hide()
        tasks.addWidget(self.log)
        layout.addWidget(self.progress_box)
        self.progress_box.hide()
        layout.addStretch(1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.pump)
        self.timer.start(75)
        self.game_status()
        if self.autostart:
            self.form.hide()
            QTimer.singleShot(0, lambda: self.start(None, mode="startup"))
        elif autoplay and not installed_runtime(self.root, state):
            QTimer.singleShot(0, self.prompt_initial_source)

    def label(self, widget, message, setter="setText"):
        self.local_labels.append((widget, message, setter))
        getattr(widget, setter)(tr(message, self.language))

    def language_changed(self):
        self.language = self.language_box.currentData() or "en"
        set_language(self.root, self.language)
        self.setWindowTitle(tr("Savage Wind — Installer", self.language))
        for widget, message, setter in self.local_labels:
            getattr(widget, setter)(tr(message, self.language))
        for page, message in self.tab_labels:
            self.tabs.setTabText(self.tabs.indexOf(page), tr(message, self.language))
        self.mark_step(getattr(self, "_step_current", -1))
        self.game_status()

    def test_toggled(self, checked):
        if checked:
            self.custom.setChecked(False)
        self.test_info.setVisible(checked)

    def custom_toggled(self, checked):
        if checked:
            self.test_branch.setChecked(False)
        self.pr_intro.setVisible(checked)
        self.pr_refresh.setVisible(checked)
        self.pr_list.setVisible(checked)

    def remember_source_tab(self, index):
        if index in (0, 1):
            self.source_tab = index

    def prompt_initial_source(self):
        if self.running or installed_runtime(self.root, read_state(self.root)):
            return
        box = QMessageBox(self)
        box.setWindowTitle(tr("Choose AppImage source", self.language))
        box.setText(tr("No Savage Wind AppImage is installed. Which version would you like to use?",
                       self.language))
        box.setInformativeText(tr("The official release is recommended. To use PRs, select them on the Source builds page.",
                                   self.language))
        official = box.addButton(tr("Official upstream AppImage", self.language),
                                 QMessageBox.ButtonRole.AcceptRole)
        test = box.addButton(tr("Upstream test branch", self.language),
                             QMessageBox.ButtonRole.AcceptRole)
        prs = box.addButton(tr("Build with pull requests", self.language),
                            QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(official)
        box.exec()
        selected = box.clickedButton()
        if selected not in (official, test, prs):
            return
        self.test_branch.setChecked(selected is test)
        self.custom.setChecked(selected is prs)
        self.prs_changed = True
        if selected is prs:
            self.tabs.setCurrentIndex(self.tabs.indexOf(self.runtime_box))
            self.form.show()
            self.fetch_prs()
            return
        if valid_game(self.root / "root"):
            self.start("__reuse__")
        else:
            self.form.show()
            self.tabs.setCurrentIndex(0)

    def update_dialog(self, info):
        info = localized(info, self.language)
        dialog = QDialog(self if self.isVisible() else None)
        dialog.setWindowTitle(info["title"])
        dialog.resize(550, 385)
        layout = QVBoxLayout(dialog)
        details = QTextBrowser(dialog)
        details.setOpenExternalLinks(True)
        details.setHtml(info["html"])
        layout.addWidget(details, 1)
        ignore = QCheckBox(tr("Don't ask anymore", self.language), dialog)
        layout.addWidget(ignore)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes |
                                   QDialogButtonBox.StandardButton.No, parent=dialog)
        actions.accepted.connect(dialog.accept)
        actions.rejected.connect(dialog.reject)
        actions.button(QDialogButtonBox.StandardButton.No).setDefault(True)
        layout.addWidget(actions)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        return accepted, ignore.isChecked()

    def selected_prs(self):
        return [item.data(Qt.ItemDataRole.UserRole)
                for index in range(self.pr_list.count())
                if (item := self.pr_list.item(index)).checkState() == Qt.CheckState.Checked]

    def fetch_prs(self):
        if self.running:
            return
        self.pr_refresh.setEnabled(False)
        self.pr_refresh.setText(tr("Loading pull requests...", self.language))
        def worker():
            try:
                self.events.put(("pr-list", open_prs()))
            except Exception as error:
                self.events.put(("pr-error", str(error)))
        threading.Thread(target=worker, daemon=True).start()

    def game_status(self):
        state = read_state(self.root)
        existing = valid_game(self.root / "root") or valid_game(self.root / "extracted")
        runtime = installed_runtime(self.root, state)
        self.install.setVisible(not existing or not runtime)
        self.play_button.setVisible(existing and bool(runtime))
        self.play_button.setEnabled(existing and bool(runtime) and compiled_code(self.root))
        self.native_button.setVisible(existing and bool(runtime))
        self.build_button.setVisible(bool(runtime))
        if existing:
            where = self.root / ("root" if valid_game(self.root / "root") else "extracted")
            self.source.setText(str(where))
            self.source.setEnabled(False)
            self.browse_source.setEnabled(False)
            self.tabs.setTabEnabled(1, False)
            self.install.setText(tr("Use existing game files", self.language))
        else:
            self.source.setEnabled(True)
            self.browse_source.setEnabled(True)
            self.tabs.setTabEnabled(1, True)
            self.install.setText(tr("Install and play", self.language))
        self.existing_label.setText(
            tr("Native game library detected." if compiled_code(self.root)
               else "No compiled game detected.", self.language) + " " +
            tr("AppImage detected." if installed_runtime(self.root, state)
               else "AppImage missing — setup required.", self.language))
        self.delete_button.setEnabled(any((self.root / p).exists() for p in
            ("root", "game", "extracted", "SavageWind-official.AppImage", "SavageWind-custom.AppImage", "SavageWind-test.AppImage")))

    def delete_installation(self):
        if self.running:
            return
        warning = tr("Delete extracted game files, compiled code and AppImages? Saves and settings are preserved.", self.language)
        reply = QMessageBox.warning(self, tr("Delete installation", self.language), warning,
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        typed, ok = QInputDialog.getText(self, tr("Delete installation", self.language),
                                         tr("Type DELETE to confirm:", self.language))
        if not ok:
            return
        try:
            reset_install(self.root, typed)
        except InstallError as error:
            QMessageBox.critical(self, "Savage Wind", str(error))
            return
        self.source.clear()
        self.current_custom = False
        self.current_test = False
        self.custom.setChecked(False)
        self.test_branch.setChecked(False)
        self.pr_list.clear()
        for field in self.packages.values():
            field.clear()
        self.game_status()
        QMessageBox.information(self, "Savage Wind", tr("Installation deleted.", self.language))

    def reveal(self, message):
        # The update probe runs without mapping the installer's native window.
        self.stage.setText(progress_text(message, self.language))
        self.form.hide()
        self.progress_box.show()
        if not self.isVisible():
            self.show()
            QApplication.instance().setQuitOnLastWindowClosed(True)

    def mark_step(self, stage):
        self._step_current = stage
        names = self.source_steps if self._source_mode else self.step_names
        self.step_view.setFixedHeight(158 if self._source_mode else 128)
        if self.step_view.count() != len(names):
            self.step_view.clear()
        for i, name in enumerate(names):
            mark = "✓" if i < stage else "▶" if i == stage else "○"
            label = f"{mark}  {tr(name, self.language)}"
            if i >= self.step_view.count():
                self.step_view.addItem(label)
            else:
                self.step_view.item(i).setText(label)

    def phase(self, message):
        title = message.lower()
        if any(x in title for x in ("recompil", "código nativo", "[gamebuild]", "linking", "compil", "native game code", "appimage")):
            return 3
        if any(x in title for x in ("atualiza", "baixando", "versão", "oficial", "update", "download", "official")):
            return 2
        if any(x in title for x in ("copiando", "arquivo", "analisando", "copying", "game files", "preparing exefs",
                                     "importando", "pacotes", "romfs", "exefs", "chaves")):
            return 1
        if "iniciando o savage wind" in title:
            return 4
        if any(x in title for x in ("armazenamento", "migrando", "migração")):
            return 0
        return None

    def pick(self):
        picked = QFileDialog.getExistingDirectory(self, tr("Game files", self.language))
        if picked:
            self.source.setText(picked)

    def pick_file(self, field, title, pattern):
        picked, _ = QFileDialog.getOpenFileName(self, title, "", pattern)
        if picked:
            field.setText(picked)

    def install_files(self):
        self.prs_changed = self.test_branch.isChecked() or bool(self.custom.isChecked() and self.selected_prs())
        if self.custom.isChecked() and not self.prs_changed and not self.current_custom:
            QMessageBox.warning(self, "Savage Wind", tr("Select at least one pull request.", self.language))
            return
        if not self.custom.isChecked() and not self.test_branch.isChecked():
            self.prs_changed = True
        if valid_game(self.root / "root") or valid_game(self.root / "extracted"):
            self.start("__reuse__")
            return
        if self.source_tab == 0:
            folder = self.source.text().strip()
            if not folder:
                QMessageBox.warning(self, tr("Select a folder", self.language), tr("Please select a folder containing exefs/ and romfs/.", self.language))
                return
            self.start(folder)
            return
        paths = {key: field.text().strip() for key, field in self.packages.items()}
        if any(not value or not Path(value).is_file() for value in paths.values()):
            QMessageBox.warning(self, tr("Invalid game files", self.language),
                                tr("Please select valid base, update and prod.keys files.", self.language))
            return
        self.start(paths)

    def selected_source(self):
        if self.test_branch.isChecked():
            return "test"
        if self.custom.isChecked():
            selection = self.selected_prs()
            if not selection:
                QMessageBox.warning(self, "Savage Wind",
                                    tr("Select at least one pull request.", self.language))
                return None
            return selection
        return []

    def build_image(self):
        selection = self.selected_source()
        if selection is not None:
            self.start(None, mode="build_only", prs=selection)

    def recompile_game(self):
        if self.custom.isChecked() and not self.selected_prs() and self.current_custom:
            self.start("__reuse__", mode="native", prs=None)
            return
        if not valid_game(self.root / "root"):
            QMessageBox.warning(self, "Savage Wind",
                                tr("Please import game files before recompiling.", self.language))
            return
        selection = self.selected_source()
        if selection is not None:
            self.start("__reuse__", mode="native", prs=selection)

    def play_existing(self):
        if valid_game(self.root / "root"):
            self.start("__reuse__", mode="play", prs=None)

    def append_log(self, message, live=False):
        # Replace only the active progress block; completed steps remain in history.
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if live and self.live_start is not None:
            cursor.setPosition(self.live_start)
            cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(message)
        else:
            prefix = "\n" if not self.log.document().isEmpty() else ""
            position = cursor.position() + len(prefix)
            cursor.insertText(prefix + message)
            self.live_start = position if live else None
        self.log.setTextCursor(cursor)

    def toggle_log(self):
        hidden = self.log.isHidden()
        self.log.setVisible(hidden)
        self.toggle.setText(tr("Hide log" if hidden else "Show log", self.language))

    def abort(self):
        self.cancel_event.set()
        self.cancel_button.setEnabled(False)
        self.stage.setText(tr("Cancelling...", self.language))

    def back(self):
        if self.running:
            return
        self.progress_box.hide()
        self.form.show()
        self.back_button.hide()
        self.game_status()

    def closeEvent(self, event):
        if self.running:
            self.abort()
            event.ignore()
        else:
            event.accept()

    def start(self, source, mode="install", prs=None):
        if self.running:
            return
        if source and source != "__reuse__" and not isinstance(source, dict) and not valid_game(source):
            QMessageBox.warning(self, tr("Invalid game files", self.language), tr("Please select a folder containing exefs/ and romfs/.", self.language))
            return
        self.cancel_event = threading.Event()
        self.running = True
        self.form.hide()
        self.progress_box.show()
        self.back_button.hide()
        self.cancel_button.setEnabled(True)
        self.bar.setRange(0, 0)
        self.log.clear()
        self.live_start = None
        self.log.hide()
        self.toggle.setText(tr("Show log", self.language))
        self._source_mode = mode == "build_only"
        self.mark_step(-1)
        self.stage.setText(tr("Starting...", self.language))
        self.eta.clear()
        def emit(kind, message, fraction):
            self.events.put(("event", kind, message, fraction))
        def ask(message):
            gate = threading.Event()
            answer = []
            self.events.put(("question", message, answer, gate))
            while not gate.wait(0.1):
                if self.cancel_event.is_set():
                    raise Cancelled()
            return bool(answer and answer[0])
        def ask_update(info):
            gate = threading.Event()
            answer = []
            self.events.put(("update-question", info, answer, gate))
            while not gate.wait(0.1):
                if self.cancel_event.is_set():
                    raise Cancelled()
            return answer[0] if answer else (False, False)
        if mode == "install":
            prs = ("test" if self.test_branch.isChecked() else
                   self.selected_prs() if self.custom.isChecked() else []) if self.prs_changed else None
        def work():
            try:
                result = Pipeline(self.root, emit, ask, self.cancel_event, prs=prs,
                                  mode=mode, ask_update=ask_update).run(source)
                self.events.put(("done", result))
            except Cancelled:
                self.events.put(("done", "cancelled"))
            except Exception as error:
                self.events.put(("error", str(error)))
        threading.Thread(target=work, name="SavageWindWorker", daemon=True).start()

    def pump(self):
        for _ in range(90):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == "event":
                _, kind, message, fraction = event
                if kind == "reveal":
                    self.reveal(message)
                    continue
                if kind == "native-start":
                    self._source_mode = False
                    self.mark_step(-1)
                    self.eta.clear()
                    self.stage.setText(tr(message, self.language))
                    self.bar.setRange(0, 0)
                    continue
                if kind == "source-step":
                    self._source_mode = True
                    self.mark_step(int(fraction))
                    self.stage.setText(tr(message, self.language))
                    self.bar.setRange(0, 0)
                    self.append_log(message)
                    continue
                if kind == "source-eta":
                    self.eta.setText(progress_text(message, self.language))
                    continue
                if kind == "source-finish":
                    self._source_mode = True
                    self.mark_step(len(self.source_steps))
                    self.stage.setText(tr(message, self.language))
                    self.bar.setRange(0, 100)
                    self.bar.setValue(100)
                    self.append_log(message)
                    continue
                if kind == "log":
                    self.append_log(message)
                    continue
                if kind == "live":
                    self.append_log(message, live=True)
                    self.stage.setText(progress_text(message.splitlines()[0], self.language))
                else:
                    self.stage.setText(message)
                if kind == "stage":
                    step = None if self._source_mode else self.phase(message)
                    if step is not None:
                        self.mark_step(step)
                    self.append_log(message)
                if fraction is None:
                    self.bar.setRange(0, 0)
                else:
                    self.bar.setRange(0, 100)
                    self.bar.setValue(int(fraction))
            elif event[0] == "update-question":
                _, info, answer, gate = event
                try:
                    answer.append(self.update_dialog(info))
                finally:
                    gate.set()
            elif event[0] == "question":
                _, message, answer, gate = event
                # A modal question alone is shown; declining must not reveal the installer.
                parent = self if self.isVisible() else None
                response = QMessageBox.question(parent, "Savage Wind", progress_text(tr(message, self.language), self.language),
                                                QMessageBox.StandardButton.Yes |
                                                QMessageBox.StandardButton.No,
                                                QMessageBox.StandardButton.No)
                answer.append(response == QMessageBox.StandardButton.Yes)
                gate.set()
            elif event[0] == "pr-list":
                selected = {pr["number"] for pr in self.selected_prs()}
                self.pr_list.clear()
                self.pr_entries = event[1]
                if not event[1]:
                    self.pr_list.addItem(tr("No open pull requests targeting test.", self.language))
                for pr in event[1]:
                    item = QListWidgetItem(f"#{pr['number']} — {pr['title']}\n{pr['description']}")
                    item.setData(Qt.ItemDataRole.UserRole, pr)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked if pr["number"] in selected else Qt.CheckState.Unchecked)
                    item.setToolTip(pr["url"] + "\n" + pr["description"])
                    self.pr_list.addItem(item)
                self.pr_refresh.setEnabled(True)
                self.pr_refresh.setText(tr("Refresh pull requests", self.language))
            elif event[0] == "pr-error":
                self.pr_refresh.setEnabled(True)
                self.pr_refresh.setText(tr("Refresh pull requests", self.language))
                QMessageBox.warning(self, "GitHub", event[1])
            elif event[0] == "done":
                self.running = False
                if event[1] == "launched":
                    QApplication.instance().quit()
                elif event[1] in ("built", "native-built"):
                    self.cancel_button.setEnabled(False)
                    self.back_button.show()
                    self.mark_step(len(self.source_steps) if event[1] == "built" else 3)
                    self.stage.setText(tr("AppImage build completed." if event[1] == "built"
                                          else "Game code recompilation completed.", self.language))
                    self.bar.setRange(0, 100)
                    self.bar.setValue(100)
                elif event[1] == "setup":
                    self.progress_box.hide()
                    self.form.show()
                    self.game_status()
                    if not self.isVisible():
                        self.show()
                        QApplication.instance().setQuitOnLastWindowClosed(True)
                else:
                    self.reveal(tr("Operation cancelled.", self.language))
                    self.cancel_button.setEnabled(False)
                    self.back_button.show()
            elif event[0] == "error":
                self.running = False
                self.cancel_button.setEnabled(False)
                self.stage.setText(tr("Could not finish.", self.language))
                self.back_button.show()
                self.append_log("ERRO: " + event[1])
                self.reveal(tr("Could not finish.", self.language))
                QMessageBox.critical(self, "Savage Wind", event[1])


def run(root, selftest=False, autoplay=True):
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Savage Wind")
    window = Window(root, autoplay=autoplay and not selftest)
    # No mapped window when launching an existing installation without updates.
    app.setQuitOnLastWindowClosed(not window.autostart)
    if selftest or not window.autostart:
        window.show()
    if selftest:
        app.processEvents()
        window.close()
        return 0
    return app.exec()
