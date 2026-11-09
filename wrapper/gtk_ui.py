"""Native GTK setup/progress UI, respecting the GNOME/Adwaita theme."""
import queue
import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from .core import Pipeline, Cancelled, valid_game


class Window(Gtk.Window):
    def __init__(self, root):
        super().__init__(title="Savage Wind — Instalador")
        self.root = Path(root)
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.running = False
        self.progress_busy = True
        self.set_default_size(680, 510)
        self.set_border_width(18)
        self.connect("delete-event", self.close_window)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add(page)
        title = Gtk.Label()
        title.set_markup('<span size="xx-large" weight="bold">Savage Wind</span>')
        title.set_halign(Gtk.Align.START)
        page.pack_start(title, False, False, 0)
        sub = Gtk.Label(label="Instalação portátil · Savage Wind oficial")
        sub.set_halign(Gtk.Align.START)
        page.pack_start(sub, False, False, 0)

        self.form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        form_title = Gtk.Label(label="Instalar os arquivos do jogo")
        form_title.set_halign(Gtk.Align.START)
        self.form.pack_start(form_title, False, False, 0)
        instructions = Gtk.Label(label="Selecione a pasta dos seus arquivos extraídos, contendo exefs/ e romfs/.")
        instructions.set_halign(Gtk.Align.START)
        instructions.set_line_wrap(True)
        self.form.pack_start(instructions, False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.source = Gtk.Entry()
        self.source.set_placeholder_text("Pasta com exefs/ e romfs/")
        row.pack_start(self.source, True, True, 0)
        pick = Gtk.Button(label="Procurar...")
        pick.connect("clicked", self.pick)
        row.pack_start(pick, False, False, 0)
        self.form.pack_start(row, False, False, 0)
        dest = Gtk.Label(label=f"Destino: {self.root / 'extracted'}")
        dest.set_halign(Gtk.Align.START)
        dest.set_line_wrap(True)
        self.form.pack_start(dest, False, False, 0)
        note = Gtk.Label(label="Saves, configurações e caches ficam junto ao AppImage.")
        note.set_halign(Gtk.Align.START)
        self.form.pack_start(note, False, False, 0)
        install = Gtk.Button(label="Instalar e jogar")
        install.connect("clicked", self.install)
        self.form.pack_start(install, False, False, 0)
        page.pack_start(self.form, False, False, 0)

        self.progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.stage = Gtk.Label(label="Preparando...")
        self.stage.set_halign(Gtk.Align.START)
        self.stage.set_line_wrap(True)
        self.progress_box.pack_start(self.stage, False, False, 0)
        self.step_names = ["Armazenamento", "Arquivos do jogo", "Atualizações", "Recompilação", "Iniciar jogo"]
        self.step_labels = []
        for name in self.step_names:
            label = Gtk.Label(label="○  " + name)
            label.set_halign(Gtk.Align.START)
            self.step_labels.append(label)
            self.progress_box.pack_start(label, False, False, 0)
        self.bar = Gtk.ProgressBar()
        self.progress_box.pack_start(self.bar, False, False, 0)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.log_expander = Gtk.Expander(label="Mostrar log")
        buttons.pack_start(self.log_expander, True, True, 0)
        self.cancel_button = Gtk.Button(label="Cancelar")
        self.cancel_button.connect("clicked", self.abort)
        buttons.pack_start(self.cancel_button, False, False, 0)
        self.progress_box.pack_start(buttons, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(170)
        self.log = Gtk.TextView()
        self.log.set_editable(False)
        self.log.set_cursor_visible(False)
        self.log.set_monospace(True)
        scroll.add(self.log)
        self.log_expander.add(scroll)
        page.pack_start(self.progress_box, True, True, 0)
        self.progress_box.hide()
        GLib.timeout_add(90, self.pump)

    def mark_step(self, step):
        for i, name in enumerate(self.step_names):
            mark = "✓" if i < step else "▶" if i == step else "○"
            self.step_labels[i].set_text(mark + "  " + name)

    def phase(self, message):
        lower = message.lower()
        if any(x in lower for x in ("recompil", "código nativo", "[gamebuild]", "linking")):
            return 3
        if any(x in lower for x in ("atualiza", "baixando", "versão", "oficial")):
            return 2
        if any(x in lower for x in ("copiando", "arquivo", "analisando")):
            return 1
        if "iniciando o savage wind" in lower:
            return 4
        if any(x in lower for x in ("armazenamento", "migrando", "migração")):
            return 0
        return None

    def pick(self, *_):
        dialog = Gtk.FileChooserDialog(title="Pasta com os arquivos do jogo", parent=self,
             action=Gtk.FileChooserAction.SELECT_FOLDER,
             buttons=(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                      Gtk.STOCK_OPEN, Gtk.ResponseType.OK))
        if dialog.run() == Gtk.ResponseType.OK:
            self.source.set_text(dialog.get_filename())
        dialog.destroy()

    def install(self, *_):
        source = self.source.get_text().strip()
        if not valid_game(source):
            self.alert("A pasta precisa conter exefs/main, exefs/main.npdm e romfs/.")
            return
        self.start(source)

    def alert(self, message, question=False):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True,
            message_type=Gtk.MessageType.QUESTION if question else Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.YES_NO if question else Gtk.ButtonsType.OK,
            text=message)
        result = dialog.run()
        dialog.destroy()
        return result == Gtk.ResponseType.YES

    def abort(self, *_):
        self.cancel_event.set()
        self.cancel_button.set_sensitive(False)
        self.stage.set_text("Cancelando a operação...")

    def close_window(self, *_):
        if self.running:
            self.abort()
            return True
        return False

    def start(self, source):
        if self.running:
            return
        self.running = True
        self.cancel_event = threading.Event()
        self.form.hide()
        self.progress_box.show_all()
        self.log_expander.set_expanded(False)
        self.cancel_button.set_sensitive(True)
        self.stage.set_text("Preparando...")
        self.log.get_buffer().set_text("")
        self.mark_step(-1)
        self.progress_busy = True
        def emit(kind, message, fraction):
            self.events.put(("event", kind, message, fraction))
        def ask(message):
            gate, answer = threading.Event(), []
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
        threading.Thread(target=work, daemon=True).start()

    def pump(self):
        for _ in range(90):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == "event":
                _, kind, message, fraction = event
                if kind == "log":
                    buf = self.log.get_buffer()
                    buf.insert(buf.get_end_iter(), message + "\n")
                    continue
                self.stage.set_text(message)
                if kind == "stage":
                    step = self.phase(message)
                    if step is not None:
                        self.mark_step(step)
                    buf = self.log.get_buffer()
                    buf.insert(buf.get_end_iter(), message + "\n")
                self.progress_busy = fraction is None
                if fraction is None:
                    self.bar.pulse()
                else:
                    self.bar.set_fraction(float(fraction) / 100)
            elif event[0] == "question":
                _, message, answer, gate = event
                answer.append(self.alert(message, question=True))
                gate.set()
            elif event[0] == "done":
                self.running = False
                if event[1] == "launched":
                    Gtk.main_quit()
                elif event[1] == "setup":
                    self.progress_box.hide()
                    self.form.show_all()
                else:
                    self.stage.set_text("Operação cancelada.")
                    self.cancel_button.set_sensitive(False)
            elif event[0] == "error":
                self.running = False
                self.cancel_button.set_sensitive(False)
                self.stage.set_text("Não foi possível concluir.")
                buf = self.log.get_buffer()
                buf.insert(buf.get_end_iter(), "ERRO: " + event[1] + "\n")
                self.alert(event[1])
        if self.running and self.progress_busy:
            self.bar.pulse()
        return True


def run(root, selftest=False):
    if selftest:
        return 0
    window = Window(root)
    window.show_all()
    window.progress_box.hide()
    if valid_game(Path(root) / "extracted"):
        GLib.idle_add(lambda: window.start(None) or False)
    Gtk.main()
    return 0
