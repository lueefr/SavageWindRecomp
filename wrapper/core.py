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
import ssl
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
    # Do not silently follow links that could redirect saves or caches outside the AppImage folder.
    for name in ("save", "cache", "fonts", "extracted", ".sw-wrapper", ".xdg"):
        if (root / name).is_symlink():
            raise InstallError(f"O armazenamento portátil contém um link externo: {name}")
    for sub in ("cache/mesa", ".xdg/config", ".xdg/data", ".xdg/state", ".sw-wrapper/tmp"):
        path = root / sub
        if path.is_symlink():
            raise InstallError(f"O armazenamento portátil contém um link externo: {sub}")
        path.mkdir(parents=True, exist_ok=True)
    env.update({
        # HOME is redirected only for the official game and its helpers, not the desktop UI.
        # This also catches libraries which ignore XDG_* and write directly to ~/.
        "HOME": str(root),
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
                "SAVAGEWIND_SETUP_FROM", "SAVAGEWIND_NO_LAUNCHER",
                "SAVAGEWIND_SAVE_DIR", "SAVAGEWIND_SETTINGS"):
        env.pop(key, None)
    return env


def cpu_target():
    # Upstream generates a different library ID for the compatible and fast x86-64 targets.
    # Inspect capabilities, not CPU model names, so changing computers is inexpensive to detect.
    arch = platform.machine().lower()
    forced = os.environ.get("SAVAGEWIND_COMPAT_CPU")
    if forced and forced != "0":
        return arch + ":compatible"
    if arch not in ("x86_64", "amd64"):
        return arch + ":native"
    try:
        with open("/proc/cpuinfo", encoding="ascii", errors="replace") as cpu:
            for line in cpu:
                if line.startswith("flags") and ":" in line:
                    flags = set(line.partition(":")[2].split())
                    if {"avx2", "fma", "bmi2"} <= flags:
                        return "x86-64-v3"
                    if {"sse4_2", "popcnt"} <= flags:
                        return "x86-64-v2"
                    return "x86-64-unsupported"
    except OSError:
        pass
    # Do not guess CPU features if /proc is unavailable.
    return arch + ":unknown"


def valid_game(folder):
    folder = Path(folder)
    return (not folder.is_symlink()
            and not (folder / "exefs").is_symlink()
            and not (folder / "romfs").is_symlink()
            and not (folder / "exefs/main").is_symlink()
            and not (folder / "exefs/main.npdm").is_symlink()
            and (folder / "exefs/main").is_file()
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


def tls_context():
    # Frozen Python's OpenSSL may not find the host distribution's CA files.
    # Extend (never disable) its default trust store with the packaged Mozilla roots.
    context = ssl.create_default_context()
    bundle = os.environ.get("SAVAGEWIND_CA_BUNDLE")
    if not bundle:
        try:
            import certifi
        except ImportError:
            pass
        else:
            bundle = certifi.where()
    if bundle:
        path = Path(bundle)
        if not path.is_file():
            raise InstallError(f"O pacote de certificados HTTPS não foi encontrado: {path}")
        try:
            context.load_verify_locations(cafile=str(path))
        except ssl.SSLError as error:
            raise InstallError(f"Os certificados HTTPS incluídos são inválidos: {error}") from error
    return context


def fetch_latest():
    request = urllib.request.Request(RELEASES, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "SavageWindPortable/1.0",
    })
    with urllib.request.urlopen(request, timeout=10, context=tls_context()) as response:
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
        with os.fdopen(fd, "wb", buffering=CHUNK) as dest, urllib.request.urlopen(
                request, timeout=20, context=tls_context()) as src:
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



def adopt_release(info, root, emit, cancel):
    # Check only immediate, common AppImage locations. Never walk the entire home tree.
    root = Path(root).resolve()
    directories = (root, Path.home() / "Downloads", Path.home() / "Applications",
                   Path.home() / "AppImages", Path.home() / "Games", Path.home() / "Jogos")
    manual = os.environ.get("SAVAGEWIND_UPSTREAM_APPIMAGE")
    candidates = [Path(manual).expanduser()] if manual else []
    seen = set()
    for folder in directories:
        if not folder.is_dir() or folder.is_symlink():
            continue
        try:
            candidates.extend(p for p in folder.iterdir()
                              if "savagewind" in p.name.lower().replace("_", "").replace("-", "")
                              and p.name.lower().endswith(".appimage"))
        except OSError:
            continue
    for candidate in candidates:
        check_cancel(cancel)
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            if (candidate.is_symlink() or not candidate.is_file()
                    or candidate.resolve() == (root / "SavageWind-official.AppImage").resolve()
                    or candidate.stat().st_size != info["size"]):
                continue
            emit("stage", f"Verificando AppImage oficial local: {candidate.name}", None)
            if sha256(candidate, cancel) != info["digest"]:
                continue
            fd, tmpname = tempfile.mkstemp(prefix=".adopt-", dir=root / ".sw-wrapper")
            os.close(fd)
            temporary = Path(tmpname)
            try:
                with candidate.open("rb") as source, temporary.open("wb") as dest:
                    try:
                        fcntl.ioctl(dest.fileno(), FICLONE, source.fileno())
                    except OSError:
                        while block := source.read(CHUNK):
                            check_cancel(cancel)
                            dest.write(block)
                check_cancel(cancel)
                if sha256(temporary, cancel) != info["digest"]:
                    continue
                temporary.chmod(0o755)
                os.replace(temporary, root / "SavageWind-official.AppImage")
                emit("progress", "AppImage oficial local reutilizado com segurança.", 100)
                return root / "SavageWind-official.AppImage"
            finally:
                temporary.unlink(missing_ok=True)
        except OSError:
            continue
    return None


def process_groups(root):
    # On Linux, official Savage Wind spawns compilers into separate process groups.
    # Collect their groups before terminating the parent so orphan compilers do not survive.
    groups, seen, pending = set(), set(), [root]
    own = os.getpgrp()
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            children = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
            pending.extend(int(value) for value in children)
            group = os.getpgid(pid)
            if group > 0 and group != own:
                groups.add(group)
        except (OSError, ValueError):
            continue
    return groups


def stop_process(proc):
    # Freeze just the parent while taking a snapshot so it cannot spawn more jobs.
    try:
        os.kill(proc.pid, signal.SIGSTOP)
    except (ProcessLookupError, OSError):
        pass
    groups = process_groups(proc.pid)
    groups.add(proc.pid)  # Popen(..., start_new_session=True).
    groups.discard(os.getpgrp())
    for group in groups:
        try:
            os.killpg(group, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        os.kill(proc.pid, signal.SIGCONT)
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass
    # Terminate surviving detached compiler groups, not just the upstream session.
    for group in groups:
        try:
            os.killpg(group, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        proc.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
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
        # Nice the compiler process without a preexec_fn (unsafe in a threaded Qt/GTK host).
        try:
            os.setpriority(os.PRIO_PROCESS, proc.pid, 8)
        except (AttributeError, PermissionError, ProcessLookupError, OSError):
            pass
        output, ready = bytearray(), False
        started = time.monotonic()
        last_heartbeat = started
        with selectors.DefaultSelector() as sel:
            sel.register(proc.stdout, selectors.EVENT_READ)
            try:
                while True:
                    check_cancel(cancel)
                    if time.monotonic() - started > 3600:
                        raise InstallError("A recompilação ultrapassou uma hora.")
                    events = sel.select(timeout=0.35)
                    now = time.monotonic()
                    if now - last_heartbeat >= 2:
                        seconds = int(now - started)
                        emit("stage", f"Recompilando código nativo: {seconds // 60} min {seconds % 60:02d} s. "
                                      "O compilador oficial está em execução.", None)
                        last_heartbeat = now
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
    if (proc.returncode or not ready or not (root / "extracted/swgame.so").is_file()
            or not (root / "extracted/swgame.so.id").is_file()):
        raise InstallError(f"A recompilação falhou. Consulte {log_path}.")
    emit("progress", "Código do jogo preparado.", 100)



def import_packages(root, paths, emit, cancel):
    # The isolated importer runs as a process group: Cancel kills hactool as well.
    root = Path(root).resolve()
    base, update, keys = (Path(paths[key]).expanduser().resolve()
                          for key in ("base", "update", "keys"))
    if base == update or not all(p.is_file() for p in (base, update, keys)):
        raise InstallError("Selecione os arquivos base NSP/XCI, atualização NSP e prod.keys válidos.")
    if (root / "extracted").exists() or (root / "extracted").is_symlink():
        raise InstallError("A pasta extracted/ já existe. Não será sobrescrita.")
    bindir = os.environ.get("SAVAGEWIND_WRAPPER_BINDIR")
    if not bindir:
        raise InstallError("Os utilitários de importação não estão disponíveis nesta execução.")
    binroot = Path(bindir)
    importer, hactool = binroot / "swextract", binroot / "hactool"
    if not importer.is_file() or not hactool.is_file():
        raise InstallError("Este AppImage não contém os utilitários NSP/XCI verificados.")
    temp = Path(tempfile.mkdtemp(prefix=".import-", dir=root / ".sw-wrapper"))
    staged = temp / "game"
    command = [str(importer), "--base", str(base), "--update", str(update),
               "--keys", str(keys), "--hactool", str(hactool), "--dest", str(staged)]
    log_path = root / ".sw-wrapper/import.log"
    emit("stage", "Validando os pacotes NSP/XCI e as chaves selecionadas...", None)
    try:
        with log_path.open("w", encoding="utf-8") as logfile:
            proc = subprocess.Popen(command, cwd=root, env=portable_env(root),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    start_new_session=True, bufsize=0)
            lines, last_notice = bytearray(), time.monotonic()
            with selectors.DefaultSelector() as selection:
                selection.register(proc.stdout, selectors.EVENT_READ)
                try:
                    while True:
                        check_cancel(cancel)
                        events = selection.select(timeout=0.4)
                        now = time.monotonic()
                        if now - last_notice > 3:
                            emit("stage", "Importando os arquivos do jogo... "
                                          "Consulte o log para detalhes.", None)
                            last_notice = now
                        for key, _ in events:
                            chunk = os.read(key.fd, 16384)
                            if not chunk:
                                selection.unregister(key.fileobj)
                                continue
                            lines.extend(chunk)
                            while b"\n" in lines:
                                raw, _, tail = lines.partition(b"\n")
                                lines = bytearray(tail)
                                line = raw.decode("utf-8", "replace").strip()
                                if not line:
                                    continue
                                logfile.write(line + "\n")
                                emit("log", line, None)
                                if line.startswith("RomFS file "):
                                    import re
                                    match = re.match(r"RomFS file (\d+)/(\d+):", line)
                                    if match and int(match.group(2)):
                                        emit("progress", "Expandindo os arquivos do jogo...",
                                             round(100 * int(match.group(1)) / int(match.group(2)), 1))
                                elif line.startswith(("Checking ", "Inspect", "Merging ",
                                                      "Copying ", "Extracting ", "Expanding ",
                                                      "Verified ", "Game files ready")):
                                    emit("stage", line[:180], None)
                        if proc.poll() is not None and not selection.get_map():
                            break
                except BaseException:
                    stop_process(proc)
                    raise
            proc.wait()
            if lines:
                logfile.write(lines.decode("utf-8", "replace"))
        check_cancel(cancel)
        if proc.returncode != 0 or not valid_game(staged):
            raise InstallError(f"A importação falhou. Consulte {log_path}.")
        os.replace(staged, root / "extracted")
        emit("progress", "Pacotes importados e verificados.", 100)
    finally:
        shutil.rmtree(temp, ignore_errors=True)


class Pipeline:
    def __init__(self, root, emit, ask, cancel):
        self.root = Path(root).resolve()
        self.emit, self.ask, self.cancel = emit, ask, cancel
        self._last_progress = 0.0
        self._last_log = 0.0

    def status(self, kind, message, fraction=None):
        check_cancel(self.cancel)
        if kind in ("log", "stage"):
            # Keep every important line on disk, but bound the UI event rate.
            with (self.root / ".sw-wrapper/events.log").open("a", encoding="utf-8") as stream:
                stream.write(f"[{kind}] {message}\n")
        now = time.monotonic()
        if kind == "progress":
            if fraction != 100 and now - self._last_progress < 0.12:
                return
            self._last_progress = now
        elif kind == "log":
            if now - self._last_log < 0.035:
                return
            self._last_log = now
        self.emit(kind, message, fraction)

    def run(self, source=None):
        # Prevent two GUI instances from replacing the same AppImage or extracted files.
        folder = self.root / ".sw-wrapper"
        if folder.is_symlink():
            raise InstallError("A pasta .sw-wrapper não pode ser um link simbólico.")
        folder.mkdir(exist_ok=True)
        with (folder / "operation.lock").open("a+b") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise InstallError("Já existe uma operação do instalador em andamento.") from error
            # Hold this lock for the lifetime of the launched game, too.
            # A second updater must not replace game code while the first game uses it.
            self._lock_fd = lock.fileno()
            return self._run_locked(source)

    def _run_locked(self, source=None):
        root = self.root
        fd, temp = tempfile.mkstemp(prefix=".write-", dir=root)
        os.close(fd)
        os.unlink(temp)
        if (root / ".sw-wrapper").is_symlink():
            raise InstallError("A pasta .sw-wrapper não pode ser um link simbólico.")
        (root / ".sw-wrapper").mkdir(exist_ok=True)
        self.status("stage", "Preparando o armazenamento portátil...")
        from .rollback import Rollback
        pending = Rollback(root)
        if pending.folder.is_dir():
            self.status("reveal", "Recuperando uma atualização interrompida...")
            self.status("stage", "Recuperando uma atualização interrompida...")
            pending.restore()
        from .migration import legacy_folder, move_data
        state = read_state(root)
        legacy = legacy_folder(root)
        if legacy is not None and state.get("ignored_legacy") != str(legacy):
            collisions = [item.name for item in legacy.iterdir()
                          if (root / item.name).exists() or (root / item.name).is_symlink()]
            if collisions:
                self.status("log", "A migração antiga precisa de revisão: arquivos com "
                                  "os mesmos nomes já existem no destino (" +
                                  ", ".join(collisions[:5]) + ").")
            elif self.ask(f"Instalação anterior encontrada em {legacy}.\nDeseja mover os arquivos, saves e configurações para a pasta deste AppImage?"):
                self.status("reveal", "Migrando os arquivos da instalação anterior...")
                move_data(legacy, root, self.status, self.cancel)
            else:
                state["ignored_legacy"] = str(legacy)
                write_state(root, state)
        portable_env(root)
        state = read_state(root)
        if source and valid_game(root / "extracted"):
            raise InstallError("O jogo já está instalado nesta pasta. Seus arquivos não serão substituídos.")
        if not valid_game(root / "extracted") and not source:
            return "setup"
        # Check availability before copying many GiB of game files.
        if source and (root / "extracted").exists() and not valid_game(root / "extracted"):
            raise InstallError("A pasta extracted/ já existe, mas está incompleta. Não será sobrescrita.")
        self.status("stage", "Procurando atualizações oficiais...")
        installed = local_release(root, state)
        release_error = None
        try:
            remote = fetch_latest()
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, InstallError) as error:
            remote = None
            release_error = str(error)
            self.status("log", f"Atualização indisponível: {error}")
        rollback = None
        if remote and (not installed or state.get("official_digest") != remote["digest"]):
            if not installed or self.ask(f"Há uma atualização do Savage Wind ({remote['tag']}).\nDeseja instalá-la?"):
                self.status("reveal", f"Instalando Savage Wind {remote['tag']}...")
                if installed:
                    rollback = Rollback(root)
                    self.status("stage", "Protegendo a versão atual antes de atualizar...")
                    rollback.begin(dict(state))
                try:
                    installed = (adopt_release(remote, root, self.status, self.cancel)
                                 or download_release(remote, root, self.status, self.cancel))
                    stat = installed.stat()
                    state.update({"official_digest": remote["digest"], "official_tag": remote["tag"],
                                  "official_size": stat.st_size, "official_mtime_ns": stat.st_mtime_ns})
                    write_state(root, state)
                except BaseException:
                    if rollback:
                        rollback.restore()
                    raise
        try:
            if not installed:
                if release_error:
                    raise InstallError(
                        "Não foi possível consultar a release oficial por HTTPS. "
                        "Confira a conexão e o certificado do servidor.\n"
                        f"Detalhes: {release_error}")
                raise InstallError(
                    "Não existe AppImage oficial instalado e nenhuma release compatível pôde ser obtida.")
            check_cancel(self.cancel)
            if not valid_game(root / "extracted"):
                if isinstance(source, dict):
                    import_packages(root, source, self.status, self.cancel)
                elif source:
                    copy_game(root, source, self.status, self.cancel)
                else:
                    return "setup"
            # A rebuild is mandatory only after changing the executable or importing new game files.
            key = state.get("built_digest")
            target = cpu_target()
            cpu_changed = bool(state.get("built_cpu") and state["built_cpu"] != target)
            if (key != state.get("official_digest") or cpu_changed or
                    not (root / "extracted/swgame.so").is_file() or
                    not (root / "extracted/swgame.so.id").is_file()):
                self.status("reveal", "Preparando o código nativo do jogo...")
                if cpu_changed:
                    self.status("stage", "CPU diferente detectada; preparando código nativo compatível...")
                build_game(root, installed, self.status, self.cancel)
                state["built_digest"] = state.get("official_digest")
                state["built_cpu"] = target
                write_state(root, state)
            elif not state.get("built_cpu"):
                # Adopt a working install without forcing an unnecessary rebuild.
                state["built_cpu"] = target
                write_state(root, state)
            self.status("stage", "Iniciando o Savage Wind...")
            with (root / ".sw-wrapper/game.log").open("ab") as logfile:
                process = subprocess.Popen([str(installed), "--play", str(root / "extracted")],
                                           env=portable_env(root), cwd=root,
                                           start_new_session=True, stdout=subprocess.DEVNULL,
                                           stderr=logfile, pass_fds=(self._lock_fd,))
            if rollback:
                rollback.commit()
            self.status("log", f"Jogo iniciado (PID {process.pid}).")
            return "launched"
        except BaseException:
            if rollback:
                rollback.restore()
            raise
