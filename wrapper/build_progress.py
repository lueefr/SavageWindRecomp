"""Read the official game's temporary build logs without patching its executable."""
from collections import deque
from dataclasses import dataclass
from pathlib import Path
import math
import os
import re
import time

GEN_LINE = re.compile(rb": progress (\d+)/(\d+) files (\d+)")
MODULE = re.compile(r"^[a-zA-Z0-9_]{1,15}$")


def duration(seconds):
    value = max(0, int(seconds))
    return f"{value // 3600}h {value // 60 % 60:02d}min" if value >= 3600 else (
        f"{value // 60}min {value % 60:02d}s" if value >= 60 else f"{value}s")


@dataclass
class Snapshot:
    phase: str
    percent: float | None
    message: str
    details: str
    eta: int | None = None


class BuildMonitor:
    def __init__(self, root, started=None, game_dir=None):
        self.work = (Path(game_dir) if game_dir is not None
                     else Path(root) / "root") / ".gamebuild"
        self.started = time.monotonic() if started is None else started
        self.best = 0.0
        self.samples = deque()
        self.layout = None
        self.logs = {}
        self.last = None

    def modules(self):
        if self.layout is not None:
            return self.layout
        path = self.work / "layout.txt"
        try:
            with path.open(encoding="ascii", errors="replace") as file:
                lines = file.readlines()
        except OSError:
            return []
        found = []
        for line in lines:
            parts = line.split()
            if len(parts) < 3 or not MODULE.fullmatch(parts[0]):
                continue
            try:
                size = int(parts[2], 16)
            except ValueError:
                continue
            if size > 0:
                found.append((parts[0], size))
        if found:
            self.layout = found
        return found

    def gen_status(self, name):
        path = self.work / f"gen_{name}.log"
        try:
            stat = path.stat()
            size = stat.st_size
            if size == 0:
                return self.logs.get(name, (0, 0, 0))[:3]
            cached = self.logs.get(name)
            if cached and cached[3:] == (size, stat.st_mtime_ns):
                return cached[:3]
            with path.open("rb") as file:
                file.seek(max(0, size - 16384))
                data = file.read(16384)
            matches = list(GEN_LINE.finditer(data))
            if matches:
                match = matches[-1]
                done, total, chunks = (int(match.group(i)) for i in (1, 2, 3))
                if total > 0 and 0 <= done <= total and chunks >= 0:
                    self.logs[name] = (done, total, chunks, size, stat.st_mtime_ns)
            return self.logs.get(name, (0, 0, 0, 0, 0))[:3]
        except OSError:
            return self.logs.get(name, (0, 0, 0, 0, 0))[:3]

    def compiled(self):
        objects = 0
        linking = False
        try:
            with os.scandir(self.work) as files:
                for item in files:
                    if item.name.endswith(".o") and item.is_file(follow_symlinks=False):
                        objects += 1
                    elif item.name == "link.rsp" and item.is_file(follow_symlinks=False):
                        linking = True
        except OSError:
            return 0, False
        return objects, linking

    def estimate(self, current, now):
        if current is None or current < 0.06 or now - self.started < 20:
            return None
        self.samples.append((now, current))
        while self.samples and now - self.samples[0][0] > 90:
            self.samples.popleft()
        if len(self.samples) < 2:
            return None
        first, last = self.samples[0], self.samples[-1]
        interval = last[0] - first[0]
        advance = last[1] - first[1]
        if interval < 15 or advance < 0.015:
            return None
        rate = advance / interval
        seconds = (0.95 - current) / rate
        if not math.isfinite(seconds) or seconds <= 0 or seconds > 6 * 3600:
            return None
        return int(seconds)

    def sample(self, now=None):
        now = time.monotonic() if now is None else now
        elapsed = duration(now - self.started)
        modules = self.modules()
        if not modules:
            return Snapshot("layout", None, f"Analisando módulos do jogo · {elapsed}",
                            f"Identificando os módulos NSO e preparando a geração de C ({elapsed}).")
        total_weight = sum(weight for _, weight in modules)
        generated, predicted, chunks, started = 0.0, 2 + len(modules), 0, 0
        descriptions = []
        for name, weight in modules:
            done, all_functions, files = self.gen_status(name)
            fraction = done / all_functions if all_functions else 0.0
            generated += weight / total_weight * fraction
            chunks += files
            if all_functions:
                started += 1
                predicted += max(files, math.ceil(files / fraction)) if fraction > 0 else 0
                descriptions.append(f"{name}: {done:,}/{all_functions:,} funções ({fraction * 100:.0f}%), {files} partes")
            else:
                descriptions.append(f"{name}: analisando o módulo")
        complete, linking = self.compiled()
        if linking:
            self.best = max(self.best, 95.0)
            return Snapshot("link", 95.0, f"Vinculando a biblioteca nativa · 95% · {elapsed}",
                            f"Vinculando {complete} objetos à biblioteca swgame.so. Tempo decorrido: {elapsed}.")
        if not started:
            return Snapshot("generate", None, f"Gerando código C · {elapsed}",
                            f"Os geradores estão iniciando. {complete} objetos compilados; {elapsed}.")
        # The official engine weights code generation by module size (20%), compilation (75%),
        # and linking (5%). Compiled objects give an observable estimate of the second part.
        compile_ratio = complete / max(predicted, complete, 1) if started == len(modules) else 0.0
        observed = 100 * (0.20 * generated + 0.75 * compile_ratio)
        self.best = min(94.0, max(self.best, observed))
        value = round(self.best, 1)
        eta = self.estimate(self.best / 100, now)
        eta_text = f"ETA estimado: {duration(eta)} até a vinculação" if eta is not None else "ETA: calculando..."
        title = f"Gerando C e compilando · ≈{value:.1f}% · {eta_text}"
        details = (f"Gerador: {'; '.join(descriptions)}. "
                   f"Compilador: {complete}/{predicted if started == len(modules) else '?'} objetos concluídos "
                   f"(inclui cache); {chunks} partes geradas. {eta_text}. Decorrido: {elapsed}.")
        return Snapshot("compile", value, title, details, eta)
