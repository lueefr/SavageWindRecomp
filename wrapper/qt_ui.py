"""Native Qt setup/progress UI, using the system KDE widget theme."""
import queue
import threading
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QHBoxLayout, QGroupBox, QFileDialog, QProgressBar, QPlainTextEdit,
    QMessageBox, QListWidget)

from .core import Pipeline, Cancelled, InstallError, valid_game


class Window(QWidget):
    def __init__(self, root):
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

        self.form = QGroupBox("Instalar os arquivos do jogo")
        fields = QVBoxLayout(self.form)
        intro = QLabel("Selecione a pasta dos seus arquivos extraídos, contendo exefs/ e romfs/.")
        intro.setWordWrap(True)
        fields.addWidget(intro)
        choose_row = QHBoxLayout()
        self.source = QLineEdit()
        self.source.setPlaceholderText("Pasta com exefs/ e romfs/")
        choose_row.addWidget(self.source, 1)
        choose = QPushButton("Procurar...")
        choose.clicked.connect(self.pick)
        choose_row.addWidget(choose)
        fields.addLayout(choose_row)
        destination = QLabel(f"Destino: {self.root / 'extracted'}")
        destination.setWordWrap(True)
        fields.addWidget(destination)
        note = QLabel("Os saves, configurações e caches permanecem nesta mesma pasta, ao lado do AppImage.")
        note.setWordWrap(True)
        fields.addWidget(note)
        self.install = QPushButton("Instalar e jogar")
        self.install.clicked.connect(lambda: self.start(self.source.text().strip()))
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
        if valid_game(self.root / "extracted"):
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
        if any(x in title for x in ("copiando", "arquivo", "analisando", "preparando exefs")):
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
        if source and not valid_game(source):
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


def run(root, selftest=False):
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Savage Wind")
    window = Window(root)
    window.show()
    if selftest:
        app.processEvents()
        window.close()
        return 0
    return app.exec()
