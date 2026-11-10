"""Native GTK setup/progress UI, respecting the GNOME/Adwaita theme."""
import queue
import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from .core import (Pipeline, Cancelled, InstallError, valid_game, compiled_code,
                   read_state, installed_runtime, reset_install)
from .migration import legacy_folder
from .i18n import LANGUAGES, get_language, set_language, tr, progress_text
from .pr_build import open_prs


class Window(Gtk.Window):
    def __init__(self, root):
        super().__init__(title="Savage Wind — Installer")
        self.root = Path(root)
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.running = False
        self.progress_busy = True
        self.language = get_language(self.root)
        self.local_labels = []
        self.tab_labels = []
        self.pr_checks = []
        self.prs_changed = False
        self.source_tab = 0
        self.current_custom = read_state(self.root).get("runtime_source") == "custom"
        self.current_test = read_state(self.root).get("runtime_source") == "test"
        self.set_title(tr("Savage Wind — Installer", self.language))
        self.set_default_size(740, 670)
        self.set_border_width(18)
        self.connect("delete-event", self.close_window)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add(page)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label()
        title.set_markup('<span size="xx-large" weight="bold">Savage Wind</span>')
        title.set_halign(Gtk.Align.START)
        top.pack_start(title, True, True, 0)
        lang = Gtk.Label()
        self.label(lang, "Language")
        top.pack_start(lang, False, False, 0)
        self.language_box = Gtk.ComboBoxText()
        for code, name in LANGUAGES:
            self.language_box.append(code, name)
        self.language_box.set_active_id(self.language)
        self.language_box.connect("changed", self.language_changed)
        top.pack_start(self.language_box, False, False, 0)
        page.pack_start(top, False, False, 0)
        subtitle = Gtk.Label()
        self.label(subtitle, "Portable installer · Savage Wind Recomp")
        subtitle.set_halign(Gtk.Align.START)
        page.pack_start(subtitle, False, False, 0)

        self.form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        form_title = Gtk.Label()
        self.label(form_title, "Game files")
        form_title.set_halign(Gtk.Align.START)
        self.form.pack_start(form_title, False, False, 0)
        instructions = Gtk.Label()
        self.label(instructions, "Choose extracted files or import your own NSP/XCI packages.")
        instructions.set_halign(Gtk.Align.START)
        instructions.set_line_wrap(True)
        self.form.pack_start(instructions, False, False, 0)
        self.tabs = Gtk.Notebook()
        extracted_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        intro = Gtk.Label()
        self.label(intro, "Path containing exefs/ and romfs/:")
        intro.set_halign(Gtk.Align.START)
        extracted_page.pack_start(intro, False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.source = Gtk.Entry()
        row.pack_start(self.source, True, True, 0)
        self.browse_source = Gtk.Button()
        self.label(self.browse_source, "Browse...", "set_label")
        self.browse_source.connect("clicked", self.pick)
        row.pack_start(self.browse_source, False, False, 0)
        extracted_page.pack_start(row, False, False, 0)
        self.tabs.append_page(extracted_page, Gtk.Label(label=tr("Extracted folder", self.language)))
        self.tab_labels.append((extracted_page, "Extracted folder"))

        packages_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        help_text = Gtk.Label()
        self.label(help_text, "Base NSP/XCI, update NSP and prod.keys from your own copy.")
        help_text.set_halign(Gtk.Align.START)
        help_text.set_line_wrap(True)
        packages_page.pack_start(help_text, False, False, 0)
        self.packages = {}
        for name, title in (("base", "Base game"), ("update", "Update"), ("keys", "Keys")):
            label = Gtk.Label()
            self.label(label, title)
            label.set_halign(Gtk.Align.START)
            packages_page.pack_start(label, False, False, 0)
            pkg_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            field = Gtk.Entry()
            pkg_row.pack_start(field, True, True, 0)
            browse = Gtk.Button()
            self.label(browse, "Browse...", "set_label")
            browse.connect("clicked", self.pick_package, field, title)
            pkg_row.pack_start(browse, False, False, 0)
            packages_page.pack_start(pkg_row, False, False, 0)
            self.packages[name] = field
        self.tabs.append_page(packages_page, Gtk.Label(label=tr("NSP / XCI", self.language)))
        self.tab_labels.append((packages_page, "NSP / XCI"))
        self.tabs.connect("switch-page", self.remember_source_tab)
        self.form.pack_start(self.tabs, False, False, 0)
        self.existing_label = Gtk.Label()
        self.existing_label.set_halign(Gtk.Align.START)
        self.existing_label.set_line_wrap(True)
        self.form.pack_start(self.existing_label, False, False, 0)
        note = Gtk.Label()
        self.label(note, "Saves, settings and caches are stored in game/ next to this launcher.")
        note.set_halign(Gtk.Align.START)
        note.set_line_wrap(True)
        self.form.pack_start(note, False, False, 0)

        runtime_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        runtime_header = Gtk.Label()
        self.label(runtime_header, "Runtime source")
        runtime_header.set_halign(Gtk.Align.START)
        runtime_box.pack_start(runtime_header, False, False, 0)
        source_hint = Gtk.Label()
        self.label(source_hint, "Leave both unchecked to use the official upstream AppImage.")
        source_hint.set_line_wrap(True)
        source_hint.set_halign(Gtk.Align.START)
        runtime_box.pack_start(source_hint, False, False, 0)
        self.test_branch = Gtk.CheckButton()
        self.label(self.test_branch, "Build upstream test branch (no PRs)", "set_label")
        self.test_branch.set_active(self.current_test)
        self.test_branch.connect("toggled", self.test_toggled)
        runtime_box.pack_start(self.test_branch, False, False, 0)
        self.test_info = Gtk.Label()
        self.label(self.test_info, "Compiles the latest upstream test commit locally; no pull requests are applied.")
        self.test_info.set_line_wrap(True)
        self.test_info.set_halign(Gtk.Align.START)
        runtime_box.pack_start(self.test_info, False, False, 0)
        self.custom = Gtk.CheckButton()
        self.label(self.custom, "Build from selected pull requests", "set_label")
        self.custom.set_active(self.current_custom)
        self.custom.connect("toggled", self.custom_toggled)
        runtime_box.pack_start(self.custom, False, False, 0)
        self.pr_intro = Gtk.Label()
        self.label(self.pr_intro, "Select PRs to merge with upstream test. The build tools are included in this AppImage.")
        self.pr_intro.set_line_wrap(True)
        self.pr_intro.set_halign(Gtk.Align.START)
        runtime_box.pack_start(self.pr_intro, False, False, 0)
        self.pr_refresh = Gtk.Button()
        self.label(self.pr_refresh, "Refresh pull requests", "set_label")
        self.pr_refresh.connect("clicked", self.fetch_prs)
        runtime_box.pack_start(self.pr_refresh, False, False, 0)
        self.pr_scroll = Gtk.ScrolledWindow()
        self.pr_scroll.set_min_content_height(110)
        self.pr_list = Gtk.ListBox()
        self.pr_scroll.add(self.pr_list)
        runtime_box.pack_start(self.pr_scroll, False, False, 0)
        self.tabs.append_page(runtime_box, Gtk.Label(label=tr("Source builds", self.language)))
        self.tab_labels.append((runtime_box, "Source builds"))
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.install_button = Gtk.Button()
        self.label(self.install_button, "Install and play", "set_label")
        self.install_button.connect("clicked", self.install)
        actions.pack_start(self.install_button, True, True, 0)
        self.delete_button = Gtk.Button()
        self.label(self.delete_button, "Delete installation", "set_label")
        self.delete_button.connect("clicked", self.delete_installation)
        actions.pack_start(self.delete_button, False, False, 0)
        self.form.pack_start(actions, False, False, 0)
        page.pack_start(self.form, False, False, 0)

        self.progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.stage = Gtk.Label()
        self.label(self.stage, "Preparing...")
        self.stage.set_halign(Gtk.Align.START)
        self.stage.set_line_wrap(True)
        self.progress_box.pack_start(self.stage, False, False, 0)
        self.step_names = ["Storage", "Game files", "Updates", "Recompilation", "Launch game"]
        self.step_labels = []
        for name in self.step_names:
            label = Gtk.Label()
            label.set_halign(Gtk.Align.START)
            self.step_labels.append(label)
            self.progress_box.pack_start(label, False, False, 0)
        self.mark_step(-1)
        self.bar = Gtk.ProgressBar()
        self.progress_box.pack_start(self.bar, False, False, 0)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.log_expander = Gtk.Expander()
        self.label(self.log_expander, "Show log", "set_label")
        self.log_expander.connect("notify::expanded", lambda obj, _: obj.set_label(
            tr("Hide log" if obj.get_expanded() else "Show log", self.language)))
        buttons.pack_start(self.log_expander, True, True, 0)
        self.back_button = Gtk.Button()
        self.label(self.back_button, "Back", "set_label")
        self.back_button.connect("clicked", self.back)
        buttons.pack_start(self.back_button, False, False, 0)
        self.cancel_button = Gtk.Button()
        self.label(self.cancel_button, "Cancel", "set_label")
        self.cancel_button.connect("clicked", self.abort)
        buttons.pack_start(self.cancel_button, False, False, 0)
        self.progress_box.pack_start(buttons, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(170)
        self.log = Gtk.TextView()
        self.live_mark = None
        self.log.set_editable(False)
        self.log.set_cursor_visible(False)
        self.log.set_monospace(True)
        scroll.add(self.log)
        self.log_expander.add(scroll)
        page.pack_start(self.progress_box, True, True, 0)
        self.progress_box.hide()
        self.game_status()
        self.custom_toggled()
        self.test_toggled()
        GLib.timeout_add(90, self.pump)

    def label(self, widget, source, method="set_text"):
        self.local_labels.append((widget, source, method))
        getattr(widget, method)(tr(source, self.language))

    def language_changed(self, *_):
        self.language = self.language_box.get_active_id() or "en"
        set_language(self.root, self.language)
        self.set_title(tr("Savage Wind — Installer", self.language))
        for widget, source, method in self.local_labels:
            getattr(widget, method)(tr(source, self.language))
        for item, source in self.tab_labels:
            self.tabs.set_tab_label_text(item, tr(source, self.language))
        self.mark_step(getattr(self, "_step_current", -1))
        self.game_status()

    def mark_step(self, step):
        self._step_current = step
        for i, name in enumerate(self.step_names):
            mark = "✓" if i < step else "▶" if i == step else "○"
            self.step_labels[i].set_text(mark + "  " + tr(name, self.language))

    def test_toggled(self, *_):
        active = self.test_branch.get_active()
        if active:
            self.custom.set_active(False)
        self.test_info.set_visible(active)

    def custom_toggled(self, *_):
        enabled = self.custom.get_active()
        if enabled:
            self.test_branch.set_active(False)
        for widget in (self.pr_intro, self.pr_refresh, self.pr_scroll):
            widget.set_visible(enabled)

    def remember_source_tab(self, _, __, index):
        if index in (0, 1):
            self.source_tab = index

    def selected_prs(self):
        return [pr for pr, check in self.pr_checks if check.get_active()]

    def fetch_prs(self, *_):
        if self.running:
            return
        self.pr_refresh.set_sensitive(False)
        self.pr_refresh.set_label(tr("Loading pull requests...", self.language))
        def worker():
            try:
                self.events.put(("pr-list", open_prs()))
            except Exception as error:
                self.events.put(("pr-error", str(error)))
        threading.Thread(target=worker, daemon=True).start()

    def game_status(self):
        state = read_state(self.root)
        existing = valid_game(self.root / "root") or valid_game(self.root / "extracted")
        if existing:
            where = self.root / ("root" if valid_game(self.root / "root") else "extracted")
            self.source.set_text(str(where))
            self.source.set_sensitive(False)
            self.browse_source.set_sensitive(False)
            self.tabs.get_nth_page(1).set_sensitive(False)
            self.install_button.set_label(tr("Use existing game files", self.language))
        else:
            self.source.set_sensitive(True)
            self.browse_source.set_sensitive(True)
            self.tabs.get_nth_page(1).set_sensitive(True)
            self.install_button.set_label(tr("Install and play", self.language))
        self.existing_label.set_text(
            tr("Native game library detected." if compiled_code(self.root)
               else "No compiled game detected.", self.language) + " " +
            tr("AppImage detected." if installed_runtime(self.root, state)
               else "AppImage missing — setup required.", self.language))
        self.delete_button.set_sensitive(any((self.root / p).exists() for p in
            ("root", "game", "extracted", "SavageWind-official.AppImage", "SavageWind-custom.AppImage", "SavageWind-test.AppImage")))

    def delete_installation(self, *_):
        if self.running:
            return
        warning = tr("This permanently removes root/, game/, all saves, and downloaded/built AppImages. The portable launcher remains.", self.language)
        if not self.alert(warning, question=True):
            return
        dialog = Gtk.Dialog(title=tr("Delete installation", self.language),
                            transient_for=self, flags=Gtk.DialogFlags.MODAL)
        dialog.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        dialog.add_button(tr("Delete installation", self.language), Gtk.ResponseType.OK)
        field = Gtk.Entry()
        field.set_placeholder_text(tr("Type DELETE to confirm:", self.language))
        dialog.get_content_area().pack_start(field, False, False, 8)
        dialog.show_all()
        response = dialog.run()
        typed = field.get_text() if response == Gtk.ResponseType.OK else ""
        dialog.destroy()
        if not typed:
            return
        try:
            reset_install(self.root, typed)
        except InstallError as error:
            self.alert(str(error))
            return
        self.source.set_text("")
        self.current_custom = False
        self.current_test = False
        self.custom.set_active(False)
        self.test_branch.set_active(False)
        self.pr_checks = []
        for child in self.pr_list.get_children():
            self.pr_list.remove(child)
        for entry in self.packages.values():
            entry.set_text("")
        self.game_status()
        self.alert(tr("Installation deleted.", self.language))

    def reveal(self, message):
        # The native progress window is mapped only when there is real work.
        self.stage.set_text(progress_text(message, self.language))
        self.show_all()
        self.form.hide()
        self.back_button.hide()
        self.log_expander.set_expanded(False)

    def phase(self, message):
        lower = message.lower()
        if any(x in lower for x in ("recompil", "código nativo", "[gamebuild]", "linking", "compil", "native game code", "appimage")):
            return 3
        if any(x in lower for x in ("atualiza", "baixando", "versão", "oficial", "update", "download", "official")):
            return 2
        if any(x in lower for x in ("copiando", "arquivo", "analisando", "copying", "game files", "importando",
                                     "pacotes", "romfs", "exefs", "chaves")):
            return 1
        if "iniciando o savage wind" in lower:
            return 4
        if any(x in lower for x in ("armazenamento", "migrando", "migração")):
            return 0
        return None

    def pick(self, *_):
        dialog = Gtk.FileChooserDialog(title=tr("Game files", self.language), parent=self,
             action=Gtk.FileChooserAction.SELECT_FOLDER,
             buttons=(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                      Gtk.STOCK_OPEN, Gtk.ResponseType.OK))
        if dialog.run() == Gtk.ResponseType.OK:
            self.source.set_text(dialog.get_filename())
        dialog.destroy()

    def pick_package(self, _, field, title):
        dialog = Gtk.FileChooserDialog(title=title, parent=self,
            action=Gtk.FileChooserAction.OPEN,
            buttons=(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                     Gtk.STOCK_OPEN, Gtk.ResponseType.OK))
        if dialog.run() == Gtk.ResponseType.OK:
            field.set_text(dialog.get_filename())
        dialog.destroy()

    def install(self, *_):
        self.prs_changed = self.test_branch.get_active() or bool(self.custom.get_active() and self.selected_prs())
        if self.custom.get_active() and not self.prs_changed and not self.current_custom:
            self.alert(tr("Select at least one pull request.", self.language))
            return
        if not self.custom.get_active() and not self.test_branch.get_active():
            self.prs_changed = True
        if valid_game(self.root / "root") or valid_game(self.root / "extracted"):
            self.start("__reuse__")
            return
        if self.source_tab == 0:
            source = self.source.get_text().strip()
            if not valid_game(source):
                self.alert(tr("Please select a folder containing exefs/ and romfs/.", self.language))
                return
        else:
            source = {name: entry.get_text().strip() for name, entry in self.packages.items()}
            if any(not path or not Path(path).is_file() for path in source.values()):
                self.alert(tr("Please select valid base, update and prod.keys files.", self.language))
                return
        self.start(source)

    def append_log(self, message, live=False):
        # Replace only the active progress block; preserve finished steps above it.
        buf = self.log.get_buffer()
        if live and self.live_mark is not None:
            start = buf.get_iter_at_mark(self.live_mark)
            buf.delete(start, buf.get_end_iter())
            buf.insert(buf.get_end_iter(), message)
            return
        self.live_mark = None
        if buf.get_char_count():
            buf.insert(buf.get_end_iter(), "\n")
        if live:
            self.live_mark = buf.create_mark(None, buf.get_end_iter(), True)
        buf.insert(buf.get_end_iter(), message)

    def alert(self, message, question=False):
        dialog = Gtk.MessageDialog(transient_for=self if self.get_visible() else None, modal=True,
            message_type=Gtk.MessageType.QUESTION if question else Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.YES_NO if question else Gtk.ButtonsType.OK,
            text=message)
        result = dialog.run()
        dialog.destroy()
        return result == Gtk.ResponseType.YES

    def abort(self, *_):
        self.cancel_event.set()
        self.cancel_button.set_sensitive(False)
        self.stage.set_text(tr("Cancelling...", self.language))

    def back(self, *_):
        if self.running:
            return
        self.progress_box.hide()
        self.form.show_all()
        self.custom_toggled()
        self.test_toggled()

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
        self.back_button.hide()
        self.log_expander.set_expanded(False)
        self.cancel_button.set_sensitive(True)
        self.stage.set_text(tr("Preparing...", self.language))
        self.log.get_buffer().set_text("")
        self.live_mark = None
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
        prs = ("test" if self.test_branch.get_active() else
               self.selected_prs() if self.custom.get_active() else []) if self.prs_changed else None
        def work():
            try:
                result = Pipeline(self.root, emit, ask, self.cancel_event, prs=prs).run(source)
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
                if kind == "reveal":
                    self.reveal(message)
                    continue
                if kind == "log":
                    self.append_log(message)
                    continue
                if kind == "live":
                    self.append_log(message, live=True)
                    self.stage.set_text(progress_text(message.splitlines()[0], self.language))
                else:
                    self.stage.set_text(message)
                if kind == "stage":
                    step = self.phase(message)
                    if step is not None:
                        self.mark_step(step)
                    self.append_log(message)
                self.progress_busy = fraction is None
                if fraction is None:
                    self.bar.pulse()
                else:
                    self.bar.set_fraction(float(fraction) / 100)
            elif event[0] == "question":
                _, message, answer, gate = event
                answer.append(self.alert(progress_text(message, self.language), question=True))
                gate.set()
            elif event[0] == "pr-list":
                selected = {pr["number"] for pr in self.selected_prs()}
                for child in self.pr_list.get_children():
                    self.pr_list.remove(child)
                self.pr_checks = []
                if not event[1]:
                    self.pr_list.add(Gtk.Label(label=tr("No open pull requests targeting test.", self.language)))
                for pr in event[1]:
                    check = Gtk.CheckButton(label=f"#{pr['number']} — {pr['title']}\n{pr['description']}")
                    check.set_active(pr["number"] in selected)
                    check.set_tooltip_text(pr["url"] + "\n" + pr["description"])
                    self.pr_list.add(check)
                    self.pr_checks.append((pr, check))
                self.pr_list.show_all()
                self.pr_refresh.set_sensitive(True)
                self.pr_refresh.set_label(tr("Refresh pull requests", self.language))
            elif event[0] == "pr-error":
                self.pr_refresh.set_sensitive(True)
                self.pr_refresh.set_label(tr("Refresh pull requests", self.language))
                self.alert(event[1])
            elif event[0] == "done":
                self.running = False
                if event[1] == "launched":
                    Gtk.main_quit()
                elif event[1] == "setup":
                    self.show_all()
                    self.progress_box.hide()
                    self.form.show_all()
                    self.custom_toggled()
                    self.test_toggled()
                    self.game_status()
                else:
                    self.reveal(tr("Operation cancelled.", self.language))
                    self.cancel_button.set_sensitive(False)
                    self.back_button.show()
            elif event[0] == "error":
                self.running = False
                self.cancel_button.set_sensitive(False)
                self.stage.set_text(tr("Could not finish.", self.language))
                self.back_button.show()
                self.append_log("ERRO: " + event[1])
                self.reveal(tr("Could not finish.", self.language))
                self.alert(event[1])
        if self.running and self.progress_busy:
            self.bar.pulse()
        return True


def run(root, selftest=False, autoplay=True):
    if selftest:
        return 0
    window = Window(root)
    autostart = bool(autoplay and valid_game(Path(root) / "root") and installed_runtime(root, read_state(root)))
    if autostart:
        # Never show an empty installer before checking the official release.
        GLib.idle_add(lambda: window.start(None) or False)
    else:
        window.show_all()
        window.custom_toggled()
        window.test_toggled()
        window.progress_box.hide()
    Gtk.main()
    return 0
