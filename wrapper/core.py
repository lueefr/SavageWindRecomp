"""Portable Savage Wind launcher engine. No game data, keys or third-party runtime are shipped."""
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

RELEASES = "https://api.github.com/repos/Revivedx/SavageWindRecomp/releases?per_page=100"
ASSETS = "https://github.com/Revivedx/SavageWindRecomp/releases/download/"
CHUNK = 1024 * 1024
FICLONE = 0x40049409


class Cancelled(Exception):
    pass


class InstallError(Exception):
    pass


def portable_root(env=None, explicit=None):
    env = env if env is not None else os.environ
    path = explicit or env.get("APPIMAGE")
    if not path:
        raise InstallError("Execute o AppImage ou informe --root para testes.")
    image = Path(path).expanduser().resolve()
    folder = image if explicit and image.is_dir() else image.parent
    if not folder.is_dir():
        raise InstallError("A pasta do AppImage não existe.")
    return folder


def portable_env(root, base=None):
    root = Path(root).resolve()
    env = dict(os.environ if base is None else base)
    for sub in ("cache/mesa", ".xdg/config", ".xdg/data", ".xdg/state", ".sw-wrapper/tmp"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    env.update({
        "SAVAGEWIND_DATA_ROOT": str(root),
        "XDG_CONFIG_HOME": str(root / ".xdg/config"),
        "XDG_DATA_HOME": str(root / ".xdg/data"),
        "XDG_STATE_HOME": str(root / ".xdg/state"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "MESA_SHADER_CACHE_DIR": str(root / "cache/mesa"),
        "TMPDIR": str(root / ".sw-wrapper/tmp"),
    })
    # AppImage's injected paths and library overrides must not leak into a nested AppImage.
    for key in ("APPIMAGE", "APPDIR", "OWD", "LD_LIBRARY_PATH", "LD_PRELOAD",
                "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "GTK_PATH",
                "PYTHONPATH", "PYTHONHOME", "SAVAGEWIND_LAUNCHER_EXTRACT",
                "SAVAGEWIND_SETUP_FROM", "SAVAGEWIND_NO_LAUNCHER"):
        env.pop(key, None)
    return env


def valid_game(folder):
    folder = Path(folder)
    return ((folder / "exefs/main").is_file()
            and (folder / "exefs/main.npdm").is_file()
            and (folder / "romfs").is_dir())


def latest(releases, arch=None):
    arch = arch or ("x86_64" if platform.machine() in ("x86_64", "AMD64")
                    else "aarch64" if platform.machine() in ("aarch64", "arm64") else "")
    if not arch:
        raise InstallError("Arquitetura de CPU não suportada.")
    found = []
    for release in releases:
        if release.get("draft") or not release.get("published_at"):
            continue
        for asset in release.get("assets") or []:
            name, digest = asset.get("name", ""), asset.get("digest", "")
            url, size = asset.get("browser_download_url", ""), asset.get("size", 0)
            prefix = ASSETS + str(release.get("tag_name", "")) + "/"
            if (name.startswith("Savage_Wind-") and name.endswith("-" + arch + ".AppImage")
                    and isinstance(digest, str) and digest.startswith("sha256:")
                    and len(digest) == 71 and all(c in "0123456789abcdefABCDEF" for c in digest[7:])
                    and isinstance(url, str) and url == prefix + name and isinstance(size, int) and size > 1000000):
                found.append((release["published_at"], {
                    "tag": release["tag_name"], "digest": digest[7:].lower(),
                    "url": url, "size": size,
                }))
    if not found:
        raise InstallError("O upstream não possui AppImage verificado para esta arquitetura.")
    return max(found, key=lambda item: item[0])[1]


def fetch_latest():
    request = urllib.request.Request(RELEASES, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "SavageWindPortable/1.0",
    })
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.load(response)
    return latest(result)


def sha256(path, cancel=None):
    digest = hashlib.sha256()
    with open(path, "rb", buffering=0) as handle:
        while block := handle.read(CHUNK):
            if cancel and cancel.is_set():
                raise Cancelled()
            digest.update(block)
    return digest.hexdigest()


def read_state(root):
    try:
        with open(Path(root) / ".sw-wrapper/state.json", encoding="utf-8") as source:
            state = json.load(source)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(root, state):
    directory = Path(root) / ".sw-wrapper"
    directory.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".state-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump(state, target, ensure_ascii=False, indent=2)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp, directory / "state.json")
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def local_release(root, state):
    path = Path(root) / "SavageWind-official.AppImage"
    if not path.is_file() or path.is_symlink() or not state.get("official_digest"):
        return None
    # Fast check: trusted on-disk metadata is persisted after an atomic, verified download.
    # Recompute only after the file size or mtime changes.
    st = path.stat()
    if st.st_size != state.get("official_size"):
        return None
    if st.st_mtime_ns != state.get("official_mtime_ns"):
        return None
    return path


def check_cancel(cancel):
    if cancel.is_set():
        raise Cancelled("Operação cancelada.")


def collect_files(source, cancel):
    files = []
    for folder in ("exefs", "romfs"):
        base = source / folder
        for current, subdirs, names in os.walk(base, followlinks=False):
            check_cancel(cancel)
            subdirs[:] = [x for x in subdirs if not (Path(current) / x).is_symlink()]
            for name in names:
                path = Path(current) / name
                if path.is_symlink() or not path.is_file():
                    raise InstallError("A pasta do jogo contém links simbólicos ou arquivos inválidos.")
                files.append((path, path.relative_to(source), path.stat().st_size))
    return files


def copy_game(root, source, emit, cancel):
    source = Path(source).expanduser().resolve()
    if not valid_game(source):
        raise InstallError("Selecione uma pasta contendo exefs/main, exefs/main.npdm e romfs/.")
    target = root / "extracted"
    if target.exists():
        raise InstallError("A pasta extracted/ já existe. Não será sobrescrita.")
    emit("stage", "Analisando os arquivos do jogo...", None)
    files = collect_files(source, cancel)
    total = sum(size for _, _, size in files)
    finished = 0
    stage = Path(tempfile.mkdtemp(prefix=".install-", dir=root / ".sw-wrapper"))
    try:
        for original, rel, size in files:
            check_cancel(cancel)
            dest = stage / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Btrfs and other reflink-capable filesystems copy large game files CoW in O(1).
            try:
                with open(original, "rb") as src, open(dest, "wb") as dst:
                    cloned = False
                    try:
                        fcntl.ioctl(dst.fileno(), FICLONE, src.fileno())
                        cloned = True
                    except OSError:
                        while block := src.read(CHUNK):
                            check_cancel(cancel)
                            dst.write(block)
                            finished += len(block)
                            emit("progress", "Copiando arquivos do jogo...",
                                 round(100 * finished / total, 1) if total else 100)
                    if cloned:
                        finished += size
            except OSError as error:
                raise InstallError(f"Erro ao copiar {rel}: {error}") from error
            if total:
                emit("progress", f"Preparando {rel}", round(100 * finished / total, 1))
            emit("log", f"Preparado: {rel} ({size} bytes)", None)
        check_cancel(cancel)
        os.replace(stage, target)
        emit("progress", "Arquivos do jogo preparados.", 100)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def download_release(info, root, emit, cancel):
    target = root / "SavageWind-official.AppImage"
    fd, temp = tempfile.mkstemp(prefix=".download-", dir=root / ".sw-wrapper")
    try:
        hasher, transferred = hashlib.sha256(), 0
        request = urllib.request.Request(info["url"], headers={"User-Agent": "SavageWindPortable/1.0"})
        emit("stage", f"Baixando Savage Wind {info['tag']}...", 0)
        with os.fdopen(fd, "wb", buffering=CHUNK) as dest, urllib.request.urlopen(request, timeout=20) as src:
            while data := src.read(CHUNK):
                check_cancel(cancel)
                transferred += len(data)
                if transferred > info["size"]:
                    raise InstallError("O download ultrapassou o tamanho esperado.")
                hasher.update(data)
                dest.write(data)
                emit("progress", f"Baixando: {transferred // CHUNK} / {info['size'] // CHUNK} MiB",
                     round(transferred / info["size"] * 100, 1))
            dest.flush()
            os.fsync(dest.fileno())
        check_cancel(cancel)
        if transferred != info["size"] or hasher.hexdigest() != info["digest"]:
            raise InstallError("O SHA-256 do download não corresponde à release oficial.")
        with open(temp, "rb") as downloaded:
            if downloaded.read(4) != b"\x7fELF":
                raise InstallError("O arquivo recebido não é um AppImage ELF válido.")
        os.chmod(temp, 0o755)
        os.replace(temp, target)
        emit("progress", "Download verificado e instalado.", 100)
        return target
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def stop_process(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def build_game(root, binary, emit, cancel):
    emit("stage", "Preparando o código nativo do jogo...", None)
    emit("log", "O compilador oficial será executado. Esta etapa pode consumir CPU e RAM.", None)
    env = portable_env(root)
    env["SAVAGEWIND_LAUNCHER_EXTRACT"] = ""
    env["SAVAGEWIND_HIDDEN"] = "1"
    log_path = root / ".sw-wrapper/build.log"
    # The upstream's test setup entry returns 0 even when setup failed; check its READY line.
    with open(log_path, "w", encoding="utf-8") as logfile:
        proc = subprocess.Popen([str(binary), str(root / "extracted")],
                                cwd=root, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=True, bufsize=0)
        output, ready = bytearray(), False
        started = time.monotonic()
        with selectors.DefaultSelector() as sel:
            sel.register(proc.stdout, selectors.EVENT_READ)
            try:
                while True:
                    check_cancel(cancel)
                    if time.monotonic() - started > 3600:
                        raise InstallError("A recompilação ultrapassou uma hora.")
                    events = sel.select(timeout=0.35)
                    for key, _ in events:
                        block = os.read(key.fd, 8192)
                        if not block:
                            sel.unregister(key.fileobj)
                            continue
                        output.extend(block)
                        while b"\n" in output:
                            line, _, remain = output.partition(b"\n")
                            output = bytearray(remain)
                            line = line.decode("utf-8", errors="replace").strip()
                            logfile.write(line + "\n")
                            if "-> READY" in line:
                                ready = True
                            if line:
                                emit("log", line, None)
                                if any(word in line for word in ("[gamebuild]", "Reading the game code", "Recompiling", "linking")):
                                    emit("stage", line[:160], None)
                    if proc.poll() is not None and not sel.get_map():
                        break
            except (Cancelled, InstallError):
                stop_process(proc)
                raise
        proc.wait()
        if output:
            logfile.write(output.decode("utf-8", errors="replace"))
    if proc.returncode or not ready or not (root / "extracted/swgame.so").is_file():
        raise InstallError(f"A recompilação falhou. Consulte {log_path}.")
    emit("progress", "Código do jogo preparado.", 100)


class Pipeline:
    def __init__(self, root, emit, ask, cancel):
        self.root = Path(root).resolve()
        self.emit, self.ask, self.cancel = emit, ask, cancel

    def status(self, kind, message, fraction=None):
        check_cancel(self.cancel)
        self.emit(kind, message, fraction)

    def run(self, source=None):
        root = self.root
        self.status("stage", "Preparando o armazenamento portátil...")
        fd, temp = tempfile.mkstemp(prefix=".write-", dir=root)
        os.close(fd)
        os.unlink(temp)
        (root / ".sw-wrapper").mkdir(exist_ok=True)
        portable_env(root)
        state = read_state(root)
        if not valid_game(root / "extracted"):
            if source:
                copy_game(root, source, self.status, self.cancel)
            else:
                return "setup"
        self.status("stage", "Procurando atualizações oficiais...")
        installed = local_release(root, state)
        try:
            remote = fetch_latest()
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, InstallError) as error:
            remote = None
            self.status("log", f"Atualização indisponível: {error}")
        if remote and (not installed or state.get("official_digest") != remote["digest"]):
            if not installed or self.ask(f"Há uma atualização do Savage Wind ({remote['tag']}).\nDeseja instalá-la?"):
                installed = download_release(remote, root, self.status, self.cancel)
                stat = installed.stat()
                state.update({"official_digest": remote["digest"], "official_tag": remote["tag"],
                              "official_size": stat.st_size, "official_mtime_ns": stat.st_mtime_ns})
                write_state(root, state)
        if not installed:
            raise InstallError("Não existe AppImage oficial instalado e não foi possível baixá-lo.")
        check_cancel(self.cancel)
        # A rebuild is mandatory only after changing the executable or importing new game files.
        key = state.get("built_digest")
        if (key != state.get("official_digest") or
                not (root / "extracted/swgame.so").is_file() or
                not (root / "extracted/swgame.so.id").is_file()):
            build_game(root, installed, self.status, self.cancel)
            state["built_digest"] = state.get("official_digest")
            write_state(root, state)
        self.status("stage", "Iniciando o Savage Wind...")
        process = subprocess.Popen([str(installed), "--play", str(root / "extracted")],
                                   env=portable_env(root), cwd=root,
                                   start_new_session=True, stdout=subprocess.DEVNULL,
                                   stderr=(root / ".sw-wrapper/game.log").open("ab"))
        self.status("log", f"Jogo iniciado (PID {process.pid}).")
        return "launched"
