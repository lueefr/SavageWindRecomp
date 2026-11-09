"""Native Qt setup/progress UI, using the system KDE widget theme."""
import queue
import threading
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QHBoxLayout, QGroupBox, QFileDialog, QProgressBar, QPlainTextEdit,
    QMessageBox, QListWidget, QTabWidget)

from .core import Pipeline, Cancelled, InstallError, valid_game
from .migration import legacy_folder


class Window(QWidget):
    def __init__(self, root, autoplay=True):
        super().__init__()
        self.root = Path(root)
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.running = False
        self.setWindowTitle("Savage Wind — Instalador")
        self.resize(680, 510)
        layout = QVBoxLayout(self)
        heading = QLabel("Savage Wind")
        font = heading.font()
        font.setPointSize(font.pointSize() + 8)
        font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)
        subtitle = QLabel("Instalação portátil · Savage Wind oficial")
        layout.addWidget(subtitle)


        self.form = QGroupBox("Arquivos para instalação")
        fields = QVBoxLayout(self.form)
        intro = QLabel("Escolha os arquivos extraídos ou importe pacotes NSP/XCI do seu jogo.")
        intro.setWordWrap(True)
        fields.addWidget(intro)
        self.tabs = QTabWidget()
        extracted_page = QWidget()
        extracted_fields = QVBoxLayout(extracted_page)
        extracted_fields.addWidget(QLabel("Pasta contendo exefs/ e romfs/ já extraídos:"))
        choose_row = QHBoxLayout()
        self.source = QLineEdit()
        self.source.setPlaceholderText("Selecione a pasta com exefs/ e romfs/")
        choose_row.addWidget(self.source, 1)
        choose = QPushButton("Procurar pasta...")
        choose.clicked.connect(self.pick)
        choose_row.addWidget(choose)
        extracted_fields.addLayout(choose_row)
        self.tabs.addTab(extracted_page, "Pasta extraída")

        packages_page = QWidget()
        packages_fields = QVBoxLayout(packages_page)
        label = QLabel("Selecione seu jogo base NSP/XCI, atualização NSP e prod.keys.")
        label.setWordWrap(True)
        packages_fields.addWidget(label)
        self.packages = {}
        for name, title, pattern in (
                ("base", "Jogo base", "Pacotes Switch (*.nsp *.xci);;Todos (*)"),
                ("update", "Atualização", "Pacotes NSP (*.nsp);;Todos (*)"),
                ("keys", "Chaves", "prod.keys (*.keys);;Todos (*)")):
            packages_fields.addWidget(QLabel(title))
            row = QHBoxLayout()
            field = QLineEdit()
            row.addWidget(field, 1)
            browse = QPushButton("Procurar...")
            browse.clicked.connect(lambda checked=False, f=field, t=title, p=pattern:
                                   self.pick_file(f, t, p))
            row.addWidget(browse)
            packages_fields.addLayout(row)
            self.packages[name] = field
        self.tabs.addTab(packages_page, "NSP / XCI")
        fields.addWidget(self.tabs)
        destination = QLabel(f"Destino: {self.root / 'extracted'}")
        destination.setWordWrap(True)
        fields.addWidget(destination)
        note = QLabel("Saves, configurações, caches e downloads permanecem junto ao AppImage. "
                      "A importação pode exigir bastante espaço temporário.")
        note.setWordWrap(True)
        fields.addWidget(note)
        self.install = QPushButton("Instalar e jogar")
        self.install.clicked.connect(self.install_files)
        fields.addWidget(self.install)
        layout.addWidget(self.form)

        self.progress_box = QGroupBox("Progresso")
        tasks = QVBoxLayout(self.progress_box)
        self.stage = QLabel("Preparando...")
        self.stage.setWordWrap(True)
        tasks.addWidget(self.stage)
        self.step_names = ["Armazenamento", "Arquivos do jogo", "Atualizações", "Recompilação", "Iniciar jogo"]
        self.step_view = QListWidget()
        self.step_view.setFixedHeight(128)
        tasks.addWidget(self.step_view)
        self.mark_step(-1)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        tasks.addWidget(self.bar)
        buttons = QHBoxLayout()
        self.toggle = QPushButton("Mostrar log")
        self.toggle.clicked.connect(self.toggle_log)
        buttons.addWidget(self.toggle)
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.clicked.connect(self.abort)
        buttons.addWidget(self.cancel_button)
        tasks.addLayout(buttons)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(600)
        self.log.hide()
        tasks.addWidget(self.log)
        layout.addWidget(self.progress_box)
        self.progress_box.hide()
        layout.addStretch(1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.pump)
        self.timer.start(75)
        if autoplay and (valid_game(self.root / "extracted") or legacy_folder(self.root)):
            self.form.hide()
            QTimer.singleShot(0, lambda: self.start(None))

    def mark_step(self, stage):
        for i, name in enumerate(self.step_names):
            mark = "✓" if i < stage else "▶" if i == stage else "○"
            label = f"{mark}  {name}"
            if i >= self.step_view.count():
                self.step_view.addItem(label)
            else:
                self.step_view.item(i).setText(label)

    def phase(self, message):
        title = message.lower()
        if any(x in title for x in ("recompil", "código nativo", "[gamebuild]", "linking")):
            return 3
        if any(x in title for x in ("atualiza", "baixando", "versão", "oficial")):
            return 2
        if any(x in title for x in ("copiando", "arquivo", "analisando", "preparando exefs",
                                     "importando", "pacotes", "romfs", "exefs", "chaves")):
            return 1
        if "iniciando o savage wind" in title:
            return 4
        if any(x in title for x in ("armazenamento", "migrando", "migração")):
            return 0
        return None

    def pick(self):
        picked = QFileDialog.getExistingDirectory(self, "Pasta com os arquivos do jogo")
        if picked:
            self.source.setText(picked)

    def pick_file(self, field, title, pattern):
        picked, _ = QFileDialog.getOpenFileName(self, title, "", pattern)
        if picked:
            field.setText(picked)

    def install_files(self):
        if self.tabs.currentIndex() == 0:
            self.start(self.source.text().strip())
            return
        paths = {key: field.text().strip() for key, field in self.packages.items()}
        if any(not value or not Path(value).is_file() for value in paths.values()):
            QMessageBox.warning(self, "Arquivos inválidos",
                                "Selecione o NSP/XCI base, a atualização NSP e o arquivo prod.keys.")
            return
        self.start(paths)

    def toggle_log(self):
        hidden = self.log.isHidden()
        self.log.setVisible(hidden)
        self.toggle.setText("Ocultar log" if hidden else "Mostrar log")

    def abort(self):
        self.cancel_event.set()
        self.cancel_button.setEnabled(False)
        self.stage.setText("Cancelando a operação...")

    def closeEvent(self, event):
        if self.running:
            self.abort()
            event.ignore()
        else:
            event.accept()

    def start(self, source):
        if self.running:
            return
        if source and not isinstance(source, dict) and not valid_game(source):
            QMessageBox.warning(self, "Arquivos inválidos", "A pasta selecionada precisa conter exefs/main, exefs/main.npdm e romfs/.")
            return
        self.cancel_event = threading.Event()
        self.running = True
        self.form.hide()
        self.progress_box.show()
        self.cancel_button.setEnabled(True)
        self.bar.setRange(0, 0)
        self.log.clear()
        self.mark_step(-1)
        self.stage.setText("Iniciando...")
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
        def work():
            try:
                result = Pipeline(self.root, emit, ask, self.cancel_event).run(source)
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
                if kind == "log":
                    self.log.appendPlainText(message)
                    continue
                self.stage.setText(message)
                if kind == "stage":
                    step = self.phase(message)
                    if step is not None:
                        self.mark_step(step)
                    self.log.appendPlainText(message)
                if fraction is None:
                    self.bar.setRange(0, 0)
                else:
                    self.bar.setRange(0, 100)
                    self.bar.setValue(int(fraction))
            elif event[0] == "question":
                _, message, answer, gate = event
                response = QMessageBox.question(self, "Atualização do Savage Wind", message,
                                                QMessageBox.StandardButton.Yes |
                                                QMessageBox.StandardButton.No,
                                                QMessageBox.StandardButton.Yes)
                answer.append(response == QMessageBox.StandardButton.Yes)
                gate.set()
            elif event[0] == "done":
                self.running = False
                if event[1] == "launched":
                    QApplication.instance().quit()
                elif event[1] == "setup":
                    self.progress_box.hide()
                    self.form.show()
                else:
                    self.stage.setText("Operação cancelada.")
                    self.cancel_button.setEnabled(False)
            elif event[0] == "error":
                self.running = False
                self.cancel_button.setEnabled(False)
                self.stage.setText("Não foi possível concluir.")
                self.log.appendPlainText("ERRO: " + event[1])
                QMessageBox.critical(self, "Savage Wind", event[1])


def run(root, selftest=False, autoplay=True):
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Savage Wind")
    window = Window(root, autoplay=autoplay and not selftest)
    window.show()
    if selftest:
        app.processEvents()
        window.close()
        return 0
    return app.exec()
