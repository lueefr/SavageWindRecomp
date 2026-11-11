"""Portable Savage Wind launcher engine. No game data, keys or third-party runtime are shipped."""
import errno
from contextlib import contextmanager
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

from .build_progress import BuildMonitor

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


def game_files(root):
    return Path(root) / "root"


def game_data(root):
    return Path(root) / "game"


def portable_env(root, base=None, play=False):
    root = Path(root).resolve()
    data = game_data(root)
    env = dict(os.environ if base is None else base)
    # Do not follow links to locations outside the portable folder.
    for name in ("root", "game", "extracted", ".sw-wrapper"):
        if (root / name).is_symlink():
            raise InstallError(f"O armazenamento portátil contém um link externo: {name}")
    for sub in ("save", "cache", "fonts", ".xdg", ".sw-wrapper", "cache/mesa",
                ".xdg/config", ".xdg/data", ".xdg/state", ".sw-wrapper/tmp"):
        path = data / sub
        if path.is_symlink():
            raise InstallError(f"O armazenamento portátil contém um link externo: game/{sub}")
        path.mkdir(parents=True, exist_ok=True)
    env.update({
        # The official game writes all saves, settings, caches and runtime data here.
        "HOME": str(data),
        "SAVAGEWIND_DATA_ROOT": str(data),
        "XDG_CONFIG_HOME": str(data / ".xdg/config"),
        "XDG_DATA_HOME": str(data / ".xdg/data"),
        "XDG_STATE_HOME": str(data / ".xdg/state"),
        "XDG_CACHE_HOME": str(data / "cache"),
        "MESA_SHADER_CACHE_DIR": str(data / "cache/mesa"),
        "TMPDIR": str(data / ".sw-wrapper/tmp"),
    })
    # AppImage's injected paths and library overrides must not leak into a nested AppImage.
    for key in ("APPIMAGE", "APPDIR", "OWD", "LD_LIBRARY_PATH", "LD_PRELOAD",
                "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "GTK_PATH",
                "PYTHONPATH", "PYTHONHOME", "SAVAGEWIND_LAUNCHER_EXTRACT",
                "SAVAGEWIND_SETUP_FROM", "SAVAGEWIND_NO_LAUNCHER",
                "SAVAGEWIND_SAVE_DIR", "SAVAGEWIND_SETTINGS",
                "SAVAGEWIND_GAME_LIB"):
        env.pop(key, None)
    if play:
        # The recompilation output is stored apart from the original exefs/romfs.
        env["SAVAGEWIND_GAME_LIB"] = str(data / "swgame.so")
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
        from .runtime_tracking import recover
        return recover(root)


def write_state(root, state, changes=None):
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
        from .runtime_tracking import record
        record(root, state, changes=changes)
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


def _local_images(root):
    """Only inspect AppImages in the portable directory, never the whole home."""
    root = Path(root)
    paths = [root / f"SavageWind-{name}.AppImage"
             for name in ("official", "test", "custom")]
    paths.extend(sorted(root.glob("Savage_Wind-*.AppImage")))
    for path in dict.fromkeys(paths):
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size < 1024 * 1024:
                continue
            with path.open("rb") as file:
                if file.read(4) == b"\x7fELF":
                    yield path
        except OSError:
            continue


def untracked_appimage(root):
    """Signal that an existing runtime needs source identification on first use."""
    return next(_local_images(root), None) is not None


def identify_local_appimage(root, emit, cancel):
    """Adopt an existing official AppImage only after checking upstream SHA-256.

    Development builds without original revision metadata are deliberately not
    treated as verified official releases or assigned invented commit hashes.
    """
    root = Path(root)
    state = read_state(root)
    if installed_runtime(root, state):
        return installed_runtime(root, state)
    candidates = list(_local_images(root))
    official = [p for p in candidates if p.name == "SavageWind-official.AppImage"
                or p.name.startswith("Savage_Wind-")]
    if not official:
        if candidates:
            emit("log", "Local test/PR AppImage detected without verified source "
                 "metadata. Select its source and rebuild to restore tracking.", None)
        return None
    request = urllib.request.Request(RELEASES, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "SavageWindPortable/1.0",
    })
    try:
        with urllib.request.urlopen(request, timeout=10, context=tls_context()) as response:
            releases = json.load(response)
        if not isinstance(releases, list):
            raise InstallError("Invalid upstream release catalogue.")
    except (urllib.error.URLError, OSError, ValueError, InstallError) as error:
        emit("log", f"Cannot identify the untracked AppImage while offline: {error}", None)
        return None
    identities = {}
    for release in releases:
        try:
            info = latest([release])
        except (InstallError, KeyError, TypeError, ValueError):
            continue
        identities[(info["digest"], info["size"])] = info
    for candidate in official:
        check_cancel(cancel)
        try:
            size = candidate.stat().st_size
            digest = sha256(candidate, cancel)
        except OSError:
            continue
        info = identities.get((digest, size))
        if info is None:
            continue
        target = root / "SavageWind-official.AppImage"
        if target.is_symlink():
            raise InstallError("The official AppImage destination is a symbolic link.")
        if candidate != target:
            from shutil import copyfileobj
            fd, temp = tempfile.mkstemp(prefix=".adopt-", dir=root / ".sw-wrapper")
            try:
                with os.fdopen(fd, "wb") as output, candidate.open("rb") as source:
                    copyfileobj(source, output, CHUNK)
                    output.flush()
                    os.fsync(output.fileno())
                if sha256(temp, cancel) != info["digest"]:
                    raise InstallError("Local AppImage changed during verification.")
                os.chmod(temp, 0o755)
                os.replace(temp, target)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        stat = target.stat()
        state.update({
            "runtime_source": "official",
            "official_digest": info["digest"],
            "official_tag": info["tag"],
            "official_size": stat.st_size,
            "official_mtime_ns": stat.st_mtime_ns,
        })
        write_state(root, state)
        emit("log", f"Identified installed official AppImage: {info['tag']} "
             f"(SHA-256 {info['digest'][:12]}).", None)
        return target
    emit("log", "Found an AppImage, but it does not match a verified official "
         "release. Choose a source to install or rebuild it.", None)
    return None


def installed_runtime(root, state):
    """Check the selected AppImage; a missing executable always requires setup."""
    if state.get("runtime_source") == "custom":
        from .pr_build import custom_runtime
        return custom_runtime(root, state)
    if state.get("runtime_source") == "test":
        from .pr_build import test_runtime
        return test_runtime(root, state)
    return local_release(root, state)


def reset_install(root, confirmation):
    """Remove installed game/runtime artifacts but preserve saves and settings."""
    if confirmation != "DELETE":
        raise InstallError("Type DELETE to confirm. Saves and settings will be preserved.")
    root = Path(root).resolve()
    if root in (Path("/"), Path.home()):
        raise InstallError("Refusing to delete an unsafe directory.")
    metadata = root / ".sw-wrapper"
    if metadata.is_symlink():
        raise InstallError("Installer metadata is a symbolic link.")
    metadata.mkdir(exist_ok=True)
    with (metadata / "operation.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise InstallError("Close the game and other installers first.") from error
        data = game_data(root)
        targets = [root / name for name in (
            "root", "extracted", "SavageWind-official.AppImage",
            "SavageWind-custom.AppImage", "SavageWind-test.AppImage",
            "SavageWind-FSR2.AppImage")]
        # Do not follow symlinks, including game/ and private metadata paths.
        if data.is_symlink():
            raise InstallError("Refusing to delete inside a symlinked game/ directory.")
        if data.exists() and not data.is_dir():
            raise InstallError("game/ is not a directory.")
        # Keep both Linux and upstream user save/configuration locations.
        protected = {"save", "saves", "savestates", "config", "configs",
                     "profiles", ".config", "savagewind.ini"}
        extensions = {".ini", ".cfg", ".conf", ".toml", ".json", ".yaml", ".yml"}
        if data.is_dir():
            for child in data.iterdir():
                if child.name in protected or child.suffix.lower() in extensions:
                    continue
                if child.name == ".xdg":
                    if child.is_symlink() or not child.is_dir():
                        raise InstallError("game/.xdg must be a real directory.")
                    # Application settings and per-application data may contain saves.
                    for nested in child.iterdir():
                        if nested.name not in ("config", "data"):
                            targets.append(nested)
                    continue
                targets.append(child)
        for child in metadata.iterdir():
            if child.name not in ("gui.json", "update-prefs.json", "runtime-history.json", "operation.lock"):
                targets.append(child)
        # Check every affected location before making any destructive changes.
        for target in targets:
            if target.is_symlink():
                raise InstallError(f"Refusing to delete a symlink: {target}")
            if target.exists() and not (target.is_file() or target.is_dir()):
                raise InstallError(f"Unexpected installation file: {target}")
        for target in targets:
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        if (data / ".xdg").is_dir() and not any((data / ".xdg").iterdir()):
            (data / ".xdg").rmdir()
        if data.is_dir() and not any(data.iterdir()):
            data.rmdir()
        from .runtime_tracking import mark_reset
        mark_reset(root)


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
    target = game_files(root)
    if target.exists() or target.is_symlink():
        raise InstallError("A pasta root/ já existe. Não será sobrescrita.")
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
                pause = getattr(cancel, "pause", None)
                if pause is not None:
                    pause.wait(cancel)
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


def recover_code_stage(root, emit=None):
    """Restore the original portable game directory after an interrupted setup."""
    root = Path(root)
    marker = root / ".sw-wrapper/code-stage"
    if marker.is_symlink():
        raise InstallError("The native-code staging marker cannot be a symbolic link.")
    if not marker.exists():
        return False
    if marker.read_text(encoding="ascii", errors="replace").strip() != "game/extracted":
        raise InstallError("Unknown native-code staging state; game files were left untouched.")
    source = game_files(root)
    temporary = game_data(root) / "extracted"
    if source.is_symlink() or temporary.is_symlink():
        raise InstallError("The native-code staging folders cannot be symbolic links.")
    if not source.exists() and temporary.is_dir():
        os.replace(temporary, source)
        if emit:
            emit("log", "Recovered root/ after an interrupted native-code build.", None)
    elif source.is_dir() and not temporary.exists():
        pass
    else:
        raise InstallError(
            "Native-code setup is incomplete: root/ and game/extracted/ have "
            "an unexpected state. No game files were deleted.")
    marker.unlink()
    return True


@contextmanager
def stage_native_code(root):
    """Make the upstream-required data-root/extracted layout without copying RomFS.

    The launcher retains root/ as its public storage layout and game/ for saves.
    This is a same-filesystem rename under the installation operation lock.
    """
    root = Path(root)
    origin = game_files(root)
    temporary = game_data(root) / "extracted"
    marker = root / ".sw-wrapper/code-stage"
    if not valid_game(origin):
        raise InstallError("Native-code staging requires a complete root/ game folder.")
    if origin.is_symlink() or temporary.exists() or temporary.is_symlink() or marker.exists():
        raise InstallError(
            "Cannot stage game files: root/, game/extracted/ or the staging "
            "marker is already occupied. Nothing was overwritten.")
    game_data(root).mkdir(parents=True, exist_ok=True)
    with marker.open("x", encoding="ascii") as file:
        file.write("game/extracted\n")
        file.flush()
        os.fsync(file.fileno())
    try:
        os.replace(origin, temporary)
    except BaseException:
        marker.unlink()
        raise
    try:
        yield temporary
    finally:
        # If an earlier cleanup failed, never overwrite either source folder.
        recover_code_stage(root)


def compiled_code(root, staging=False):
    # The official compiler writes next to exefs/romfs. After success we keep
    # the native library inside game/ and pass its path to the official loader.
    location = game_files(root) if staging else game_data(root)
    return compiled_pair(location)


def compiled_pair(location):
    location = Path(location)
    lib = location / "swgame.so"
    identity = location / "swgame.so.id"
    if lib.is_symlink() or identity.is_symlink():
        return False
    try:
        if not lib.is_file() or lib.stat().st_size == 0:
            return False
        with identity.open("rb") as stream:
            value = stream.read(128).strip()
        return bool(value) and len(value) < 128 and all(32 <= b <= 126 for b in value)
    except OSError:
        return False


def recover_completed_code(root, installed, state, emit):
    # A prior wrapper may have rejected a completed silent build. Trust the
    # captured exit status only when the native pair is complete and was written
    # after the exact official AppImage currently installed.
    if state.get("built_digest") or not state.get("official_digest"):
        return False
    log = Path(root) / ".sw-wrapper/build.log"
    try:
        with log.open("rb") as file:
            file.seek(0, os.SEEK_END)
            file.seek(max(0, file.tell() - 65536))
            tail = file.read()
    except OSError:
        return False
    if b"-> NOT READY" in tail or b"not_ready=True" in tail:
        return False
    confirmed = b"[gamebuild] done in " in tail
    silent = (b"exit=0" in tail and b"artifacts=True" in tail
              and b"not_ready=False" in tail)
    if not (confirmed or silent):
        return False
    source = game_files(root) if compiled_code(root, staging=True) else game_data(root)
    if not compiled_code(root, staging=(source == game_files(root))):
        return False
    try:
        official_time = Path(installed).stat().st_mtime_ns
        if any((source / name).stat().st_mtime_ns < official_time
               for name in ("swgame.so", "swgame.so.id")):
            return False
    except OSError:
        return False
    if confirmed and not silent:
        # Legacy logs contain the build target; require the same CPU family.
        target = cpu_target()
        if target not in ("x86-64-v2", "x86-64-v3"):
            return False
        targets = [line for line in tail.splitlines() if line.startswith(b"[gamebuild] target")]
        if not targets or ("-march=" + target).encode() not in targets[-1]:
            return False
    if source == game_files(root):
        publish_code(root)
    state["built_digest"] = state["official_digest"]
    state["built_cpu"] = cpu_target()
    write_state(root, state)
    emit("log", "Biblioteca nativa recuperada da compilação anterior; "
                "reutilizando game/swgame.so sem recompilar.", None)
    return True


def publish_code(root):
    # Both files are on the same filesystem; reject links and incomplete pairs.
    source = game_files(root)
    dest = game_data(root)
    if not compiled_code(root, staging=True):
        raise InstallError("O compilador não gerou uma biblioteca nativa completa em root/.")
    if dest.is_symlink():
        raise InstallError("game/ não pode ser um link simbólico.")
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("swgame.so", "swgame.so.id"):
        if (dest / name).is_symlink():
            raise InstallError("game/ contém um link simbólico que impede a publicação segura.")
    for name in ("swgame.so", "swgame.so.id"):
        os.replace(source / name, dest / name)


def build_game(root, binary, emit, cancel):
    # New upstream runtime requires <data root>/extracted as a real directory.
    # Only locally built test/PR AppImages need the staging layout.
    source = read_state(root).get("runtime_source")
    if source in ("test", "custom"):
        with stage_native_code(root) as staged:
            _run_game_build(root, binary, emit, cancel, staged)
    else:
        _run_game_build(root, binary, emit, cancel, game_files(root))
    publish_code(root)
    emit("log", "Native code moved into game/swgame.so.", None)
    emit("progress", "Native code validated.", 100)


def _run_game_build(root, binary, emit, cancel, source):
    emit("stage", "Preparando o código nativo do jogo...", None)
    emit("log", "O compilador oficial será executado. Esta etapa pode consumir CPU e RAM.", None)
    env = portable_env(root)
    env["SAVAGEWIND_LAUNCHER_EXTRACT"] = ""
    env["SAVAGEWIND_HIDDEN"] = "1"
    log_path = root / ".sw-wrapper/build.log"
    upstream_log = game_data(root) / "savagewind.log"
    try:
        upstream_offset = upstream_log.stat().st_size
    except OSError:
        upstream_offset = 0
    pause = getattr(cancel, "pause", None)
    clock = pause.clock if pause is not None else time.monotonic
    if pause is not None:
        pause.wait(cancel)
    # The upstream's test setup entry returns zero even on a failed setup.
    # A completed compile and a valid library can recover from a missing READY
    # message or a failure after linking, but never override explicit NOT READY.
    with open(log_path, "w", encoding="utf-8") as logfile:
        build_started_ns = time.time_ns()
        proc = subprocess.Popen([str(binary), str(source)],
                                cwd=root, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=True, bufsize=0)
        if pause is not None:
            try:
                pause.attach(proc)
            except OSError:
                stop_process(proc)
                raise
        # Nice the compiler process without a preexec_fn (unsafe in a threaded Qt/GTK host).
        try:
            os.setpriority(os.PRIO_PROCESS, proc.pid, 8)
        except (AttributeError, PermissionError, ProcessLookupError, OSError):
            pass
        output, ready, not_ready, compiled, entered_game = bytearray(), False, False, False, False
        started = clock()
        monitor = BuildMonitor(root, started=started, game_dir=source)
        last_heartbeat, last_log = started, started
        with selectors.DefaultSelector() as sel:
            sel.register(proc.stdout, selectors.EVENT_READ)
            try:
                while True:
                    check_cancel(cancel)
                    if pause is not None:
                        pause.wait(cancel)
                    if clock() - started > 3600:
                        raise InstallError("A recompilação ultrapassou uma hora.")
                    events = sel.select(timeout=0.35)
                    now = clock()
                    if now - last_heartbeat >= 1:
                        progress = monitor.sample(now)
                        emit("live", progress.message + "\n" + progress.details, progress.percent)
                        if now - last_log >= 10:
                            message = "[progresso] " + progress.details
                            logfile.write(message + "\n")
                            logfile.flush()
                            last_log = now
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
                            if "[launcher] setup:" in line:
                                if "-> READY" in line:
                                    ready = True
                                elif "-> NOT READY" in line:
                                    not_ready = True
                            if line.startswith("[gamebuild] done in "):
                                compiled = True
                            if "nn::init::Start at" in line:
                                entered_game = True
                            if line:
                                emit("log", line, None)
                                if "[gamebuild]" in line:
                                    emit("stage", line[:160], None)
                    # Some upstream execution paths enter gameplay instead of
                    # exiting the test hook. Detect this and relaunch through
                    # the portable path rather than waiting until the 1h timeout.
                    if entered_game:
                        if compiled_pair(source):
                            logfile.write("[wrapper] O executável oficial iniciou o jogo durante a preparação. "
                                          "Reiniciando pelo fluxo portátil.\n")
                            emit("log", "O executável oficial entrou no jogo. Reiniciando pelo portable...", None)
                            stop_process(proc)
                            break
                        stop_process(proc)
                        raise InstallError(
                            "O upstream entrou no jogo, mas não há código nativo válido "
                            "na pasta portátil. Consulte o log e confira o caminho [paths] data.")
                    if proc.poll() is not None and not sel.get_map():
                        break
            except BaseException:
                if pause is not None:
                    pause.resume()
                stop_process(proc)
                raise
            finally:
                if pause is not None:
                    pause.detach(proc)
        proc.wait()
        if output:
            logfile.write(output.decode("utf-8", errors="replace"))
        artifact_ok = compiled_pair(source)
        # Silent upstream runs may write logs into their own data folder.
        # A zero exit plus a newly written native pair is a stronger signal
        # than the absence of READY/done text from the captured pipe.
        newly_built = False
        if artifact_ok:
            try:
                newly_built = all(
                    (source / name).stat().st_mtime_ns >= build_started_ns - 1_000_000_000
                    for name in ("swgame.so", "swgame.so.id"))
            except OSError:
                pass
        accepted = artifact_ok and not not_ready and (
            ready and proc.returncode == 0 or compiled or entered_game
            or (proc.returncode == 0 and newly_built))
        logfile.write(f"[wrapper] Finalizado em {clock() - started:.1f}s; "
                      f"exit={proc.returncode}; ready={ready}; not_ready={not_ready}; "
                      f"build_done={compiled}; game_started={entered_game}; "
                      f"artifacts={artifact_ok}; newly_built={newly_built}; "
                      f"accepted={accepted}.\n")
        logfile.flush()
    if not accepted:
        reasons = []
        if not artifact_ok:
            reasons.append("swgame.so ou swgame.so.id ausente/inválido na pasta portátil")
        if not_ready:
            reasons.append("o upstream informou explicitamente NOT READY")
        if not (ready or compiled or entered_game or (proc.returncode == 0 and newly_built)):
            reasons.append("não foi encontrada confirmação de compilação ou execução")
        if proc.returncode and not (compiled or entered_game):
            reasons.append(f"o processo terminou com código {proc.returncode}")
        detail = "; ".join(reasons) or f"estado inesperado, código de saída {proc.returncode}"
        # The upstream logs gamebuild failures to its own data root, not stdout.
        # Surface only diagnostic lines written during this attempt.
        try:
            with upstream_log.open("rb") as stream:
                size = stream.seek(0, os.SEEK_END)
                stream.seek(min(upstream_offset, size))
                diagnostics = stream.read(256 * 1024).decode("utf-8", "replace")
            relevant = [line.strip() for line in diagnostics.splitlines()
                        if line.startswith(("[gamebuild]", "[launcher] FAILED",
                                            "[launcher] game code:", "[paths] refusing"))]
            for line in relevant[-12:]:
                emit("log", "[upstream] " + line[:700], None)
            failures = [line for line in relevant if
                        "failed" in line.lower() or "couldn't" in line.lower()
                        or "refusing" in line.lower()]
            if failures:
                detail += "; upstream: " + failures[-1][:300]
        except OSError:
            pass
        emit("log", f"[wrapper] Falha ao validar o código nativo: {detail}", None)
        raise InstallError(
            f"O código nativo não foi validado: {detail}. "
            f"Consulte o log completo em {log_path}.")
    if not ready:
        emit("log", "Compilação recuperada: o código nativo foi concluído, "
                    "embora o upstream não tenha retornado a confirmação READY.", None)
    return



def import_detail(title, done, total, count, total_count, unit, started):
    """Render one replaceable four-line snapshot from measured progress."""
    ratio = 100 * done / total if total else 0
    elapsed = time.monotonic() - started
    if elapsed >= 4 and done > 0 and done < total:
        remaining = max(0, int(elapsed * (total - done) / done))
        eta = f"{remaining // 60:02d}:{remaining % 60:02d}"
    elif done >= total and total:
        eta = "00:00"
    else:
        eta = "calculando..."
    return (f"{title}: {ratio:.1f}%\n"
            f"{title.removeprefix('Copiando ')}: {ratio:.1f}% "
            f"({done // CHUNK} / {total // CHUNK} MiB)\n"
            f"{count} de {total_count} {unit}\n"
            f"ETA: {eta}")


def import_packages(root, paths, emit, cancel):
    # The isolated importer runs as a process group: Cancel kills hactool as well.
    root = Path(root).resolve()
    base, update, keys = (Path(paths[key]).expanduser().resolve()
                          for key in ("base", "update", "keys"))
    if base == update or not all(p.is_file() for p in (base, update, keys)):
        raise InstallError("Selecione os arquivos base NSP/XCI, atualização NSP e prod.keys válidos.")
    if game_files(root).exists() or game_files(root).is_symlink():
        raise InstallError("A pasta root/ já existe. Não será sobrescrita.")
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
            lines = bytearray()
            phase_start = time.monotonic()
            phase_name = None
            import re
            with selectors.DefaultSelector() as selection:
                selection.register(proc.stdout, selectors.EVENT_READ)
                try:
                    while True:
                        check_cancel(cancel)
                        events = selection.select(timeout=0.4)
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
                                nca = re.match(r"NCA progress (base|update) (\d+)/(\d+)", line)
                                romfs = re.match(r"RomFS file (\d+)/(\d+):", line)
                                if nca and int(nca.group(3)):
                                    name = nca.group(1)
                                    if phase_name != name:
                                        phase_name, phase_start = name, time.monotonic()
                                    done, total = int(nca.group(2)), int(nca.group(3))
                                    label = "base" if name == "base" else "atualização"
                                    title = f"Copiando NCA {label}"
                                    snapshot = import_detail(title, done, total,
                                                             int(done == total), 1,
                                                             "arquivo NCA", phase_start)
                                    emit("live", snapshot, round(100 * done / total, 1))
                                elif romfs and int(romfs.group(2)):
                                    if phase_name != "romfs":
                                        phase_name, phase_start = "romfs", time.monotonic()
                                    count, total = int(romfs.group(1)), int(romfs.group(2))
                                    # The file count is known only after the RomFS index is read.
                                    elapsed = time.monotonic() - phase_start
                                    if elapsed >= 4 and count > 1 and count < total:
                                        remaining = int(elapsed * (total - count) / count)
                                        eta = f"{remaining // 60:02d}:{remaining % 60:02d}"
                                    elif count == total:
                                        eta = "00:00"
                                    else:
                                        eta = "calculando..."
                                    snapshot = (f"Extraindo RomFS: {100 * count / total:.1f}%\n"
                                                f"Arquivos extraídos: {count} / {total}\n"
                                                f"{count} de {total} arquivos\n"
                                                f"ETA: {eta}")
                                    emit("live", snapshot, round(100 * count / total, 1))
                                elif re.match(r"Extracted \\d+ RomFS files", line):
                                    # The live file counter already displays this milestone.
                                    continue
                                elif line.startswith(("Checking ", "Inspect", "Merging ",
                                                      "Copying ", "Extracting ", "Expanding ",
                                                      "Verified ", "Game files ready")):
                                    emit("stage", line[:180], None)
                                else:
                                    emit("log", line, None)
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
        os.replace(staged, game_files(root))
        emit("progress", "Pacotes importados e verificados.", 100)
    finally:
        shutil.rmtree(temp, ignore_errors=True)


class Pipeline:
    def __init__(self, root, emit, ask, cancel, prs=None, mode="install",
                 ask_update=None):
        self.root = Path(root).resolve()
        self.emit, self.ask, self.cancel = emit, ask, cancel
        self.prs = prs  # None: preserve runtime; []: official; "test": upstream test; list: PRs
        self.ask_update = ask_update
        if mode not in ("install", "build_only", "play", "native", "startup"):
            raise ValueError("Invalid portable installer operation")
        self.mode = mode
        self._last_progress = 0.0
        self._last_log = 0.0

    def status(self, kind, message, fraction=None):
        pause = getattr(self.cancel, "pause", None)
        if pause is not None:
            pause.wait(self.cancel)
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

    def launch_existing(self, root, installed):
        self.status("stage", "Iniciando o Savage Wind...")
        with (root / ".sw-wrapper/game.log").open("ab") as logfile:
            process = subprocess.Popen([str(installed), "--play", str(game_files(root))],
                                       env=portable_env(root, play=True), cwd=root,
                                       start_new_session=True, stdout=subprocess.DEVNULL,
                                       stderr=logfile, pass_fds=(self._lock_fd,))
        self.status("log", f"Jogo iniciado (PID {process.pid}).")
        return "launched"

    def build_source_only(self):
        """Build or select one source AppImage without touching game data or native code."""
        root = self.root
        state = read_state(root)
        if self.prs == "test":
            from .pr_build import build_test_appimage, test_runtime, upstream_test_head
            installed = test_runtime(root, state)
            rebuild = True
            if installed and state.get("test_commit"):
                try:
                    head = upstream_test_head()
                    rebuild = head != state["test_commit"]
                    if not rebuild:
                        self.status("log", f"Upstream test {head[:12]} has not changed; reusing the AppImage.")
                except InstallError as error:
                    if not self.ask(f"Could not check upstream test: {error}\n"
                                    "Reuse the existing AppImage without checking for updates?"):
                        return "setup"
                    rebuild = False
            if rebuild:
                if not self.ask("Build an AppImage from upstream test? This only builds "
                                "the executable; game files, swgame.so and saves remain untouched."):
                    return "setup"
                result = build_test_appimage(root, self.status, self.cancel, ask=self.ask)
                installed = result["path"]
                state.update({
                    "runtime_source": "test", "test_digest": result["digest"],
                    "test_commit": result["commit"], "test_size": result["size"],
                    "test_mtime_ns": result["mtime_ns"],
                })
                write_state(root, state)
            elif state.get("runtime_source") != "test":
                raise InstallError("Cannot reuse a test AppImage that is not the selected runtime.")
        elif isinstance(self.prs, list) and self.prs:
            from .pr_build import build_pr_appimage, custom_runtime, selection_key
            desired = selection_key(self.prs)
            installed = custom_runtime(root, state)
            base_changed = False
            if installed and state.get("custom_base"):
                from .pr_build import upstream_test_head
                try:
                    base_changed = upstream_test_head() != state["custom_base"]
                except InstallError as error:
                    self.status("log", f"Cannot check PR build base: {error}")
                    base_changed = True
            if not installed or state.get("custom_selection") != desired or base_changed:
                if not self.ask("Compile an AppImage using the selected PRs? PR code may be "
                                "unreviewed. Game code, files and saves will not be modified."):
                    return "setup"
                result = build_pr_appimage(root, self.prs, self.status, self.cancel, ask=self.ask)
                installed = result["path"]
                state.update({
                    "runtime_source": "custom", "custom_digest": result["digest"],
                    "custom_selection": result["selection"], "custom_size": result["size"],
                    "custom_mtime_ns": result["mtime_ns"],
                    "custom_prs": [{"number": pr["number"], "sha": pr["sha"]}
                                   for pr in self.prs],
                    "custom_base": result.get("base_commit"),
                })
                write_state(root, state)
            else:
                if not state.get("custom_prs"):
                    state["custom_prs"] = [{"number": p["number"], "sha": p["sha"]}
                                           for p in self.prs]
                    write_state(root, state)
                self.status("log", "Selected PRs and upstream base are unchanged; reusing the AppImage.")
        elif self.prs == []:
            self.status("stage", "Checking the official Savage Wind release...")
            remote = fetch_latest()
            installed = local_release(root, state)
            if not installed or state.get("official_digest") != remote["digest"]:
                if not self.ask(f"Download Savage Wind {remote['tag']} from official upstream?"):
                    return "setup"
                installed = (adopt_release(remote, root, self.status, self.cancel)
                             or download_release(remote, root, self.status, self.cancel))
                stat = installed.stat()
                state.update({
                    "official_digest": remote["digest"], "official_tag": remote["tag"],
                    "official_size": stat.st_size,
                    "official_mtime_ns": stat.st_mtime_ns,
                })
            else:
                self.status("log", "The official AppImage is already current; reusing it.")
            state["runtime_source"] = "official"
            write_state(root, state)
        else:
            raise InstallError("Choose the official release, test branch or a PR selection.")
        self.status("source-finish", f"AppImage ready: {installed.name}", 100)
        self.status("log", "Executable build complete. Native game code and saves are unchanged.")
        return "built"

    def compile_native(self, root, installed):
        """Compile native game code only after explicit permission or a native action."""
        self.status("native-start", "Recompiling native game code...", None)
        self.status("reveal", "Recompiling game code at your request...")
        build_game(root, installed, self.status, self.cancel)
        state = read_state(root)
        active = (state.get("test_digest") if state.get("runtime_source") == "test" else
                  state.get("custom_digest") if state.get("runtime_source") == "custom" else
                  state.get("official_digest"))
        state["built_digest"] = active
        state["built_cpu"] = cpu_target()
        write_state(root, state)
        self.status("progress", "Native game code compiled. Use Play to launch.", 100)
        return "native-built"

    def offer_native_after_appimage(self):
        """Offer, but never force, native compilation with the selected AppImage."""
        root = self.root
        if not valid_game(game_files(root)):
            # Do not lose this final decision to the verbose log rate limiter.
            self._last_log = 0.0
            self.status("log", "AppImage built. Native game compilation was not offered "
                        "because root/ does not contain valid game files.", None)
            return "built"
        question = (
            "The AppImage is ready.\n\n"
            "Recompile the game's native library (swgame.so) using this AppImage now?\n\n"
            "This can take a long time and will replace the existing native library. "
            "Saves, settings and extracted game files will be preserved.\n\n"
            "Choose No to finish without recompiling the game.")
        if not self.ask(question):
            self.status("log", "Native game recompilation declined. "
                        "The existing swgame.so and saves were preserved.", None)
            return "built"
        check_cancel(self.cancel)
        state = read_state(root)
        installed = installed_runtime(root, state)
        if not installed:
            raise InstallError("The newly built AppImage could not be verified. "
                               "Native game recompilation was not started.")
        # The full setup path prepares the same runtime environment before a
        # native build. The AppImage-only path deliberately skips this until consent.
        try:
            portable_env(root)
            recover_code_stage(root, self.status)
            return self.compile_native(root, installed)
        except InstallError as error:
            raise InstallError(
                "The AppImage was built and saved successfully, but the optional "
                "native game recompilation failed. The AppImage is still available. "
                f"Details: {error}") from error

    def select_native_runtime(self):
        """Resolve the explicitly selected executable before recompiling game code."""
        root = self.root
        if self.prs == "test" or (isinstance(self.prs, list) and self.prs):
            result = self.build_source_only()
            if result != "built":
                return None
            return installed_runtime(root, read_state(root))
        if self.prs == []:
            state = read_state(root)
            installed = local_release(root, state)
            if not installed:
                remote = fetch_latest()
                if not remote:
                    raise InstallError("An official AppImage is required but no release is available.")
                if not self.ask("Download the official AppImage before explicitly recompiling "
                                "the game's native code?"):
                    return None
                installed = (adopt_release(remote, root, self.status, self.cancel)
                             or download_release(remote, root, self.status, self.cancel))
                stat = installed.stat()
                state.update({
                    "official_digest": remote["digest"], "official_tag": remote["tag"],
                    "official_size": stat.st_size, "official_mtime_ns": stat.st_mtime_ns,
                })
            state["runtime_source"] = "official"
            write_state(root, state)
            return installed
        return installed_runtime(root, read_state(root))

    def start_installed(self):
        """Check the selected runtime for updates before normal gameplay."""
        from .updates import check_update, suppress, suppressed
        from .rollback import Rollback
        from .pr_build import build_test_appimage, build_pr_appimage

        root = self.root
        state = read_state(root)
        installed = installed_runtime(root, state)
        if not installed or not valid_game(game_files(root)):
            return "setup"
        source = state.get("runtime_source", "official")
        update = None
        if not suppressed(root, source):
            if source == "custom" and not state.get("custom_prs"):
                self.status("log", "This custom AppImage predates PR revision tracking. "
                            "Re-select and rebuild its PRs once to enable update checks.")
            else:
                self.status("stage", "Checking for updates to the selected AppImage...")
                try:
                    update = check_update(state)
                except Exception as error:
                    # GitHub metadata failures are never grounds to block an
                    # already installed and working offline game.
                    self.status("log", f"Update check unavailable ({source}): {error}. "
                                "Using the installed AppImage.")
        else:
            self.status("log", f"Update notifications for {source} are disabled.")
        if update is not None:
            if self.ask_update is not None:
                accepted, stop_asking = self.ask_update(update)
            else:
                accepted, stop_asking = self.ask(
                    f"{update['title']}\n"
                    "An update is available. Update the AppImage and recompile "
                    "the native game code now?"), False
            if stop_asking:
                suppress(root, source)
                self.status("log", f"User disabled future {source} update prompts.")
            if accepted:
                self.status("reveal", "Updating the selected AppImage and game code...")
                previous = dict(state)
                rollback = Rollback(root)
                rollback.begin(previous)
                try:
                    if source == "official":
                        info = update["release"]
                        installed = (adopt_release(info, root, self.status, self.cancel)
                                     or download_release(info, root, self.status, self.cancel))
                        stat = installed.stat()
                        state.update({
                            "official_digest": info["digest"],
                            "official_tag": info["tag"],
                            "official_size": stat.st_size,
                            "official_mtime_ns": stat.st_mtime_ns,
                        })
                    elif source == "test":
                        result = build_test_appimage(root, self.status, self.cancel,
                                                     ask=self.ask)
                        if result["commit"] != update["commit"]:
                            raise InstallError("Upstream test changed during the build. "
                                               "The installed version will be restored.")
                        installed = result["path"]
                        state.update({
                            "test_digest": result["digest"], "test_commit": result["commit"],
                            "test_size": result["size"],
                            "test_mtime_ns": result["mtime_ns"],
                        })
                    else:
                        result = build_pr_appimage(root, update["prs"], self.status,
                                                   self.cancel, ask=self.ask)
                        if (result.get("base_commit") and
                                result["base_commit"] != update["base_commit"]):
                            raise InstallError("Upstream test changed during the PR build. "
                                               "The installed version will be restored.")
                        installed = result["path"]
                        state.update({
                            "custom_digest": result["digest"],
                            "custom_selection": result["selection"],
                            "custom_size": result["size"],
                            "custom_mtime_ns": result["mtime_ns"],
                            "custom_prs": update["prs"],
                            "custom_base": result.get("base_commit", update["base_commit"]),
                        })
                    write_state(root, state)
                    self.status("native-start", "Recompiling the game after updating the AppImage...", None)
                    build_game(root, installed, self.status, self.cancel)
                    state["built_digest"] = (state.get("official_digest") if source == "official" else
                                             state.get("test_digest") if source == "test" else
                                             state.get("custom_digest"))
                    state["built_cpu"] = cpu_target()
                    write_state(root, state, changes=update)
                    rollback.commit()
                    self.status("log", "AppImage and native game code updated successfully.")
                except BaseException:
                    rollback.restore()
                    self.status("log", "Update did not finish; the previous AppImage, "
                                "native game library and state were restored.")
                    raise
                return self.launch_existing(root, installed)
            self.status("log", "Update declined; launching the existing AppImage unchanged.")
        active_digest = state.get(source + "_digest")
        if not compiled_code(root) or state.get("built_digest") != active_digest:
            self.status("reveal", "Preparing native game code for the first launch...")
            build_game(root, installed, self.status, self.cancel)
            state["built_digest"] = (state.get("official_digest") if source == "official" else
                                     state.get("test_digest") if source == "test" else
                                     state.get("custom_digest"))
            state["built_cpu"] = cpu_target()
            write_state(root, state)
        return self.launch_existing(root, installed)

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
        from_setup = source == "__reuse__"
        if from_setup:
            source = None
        root = self.root
        fd, temp = tempfile.mkstemp(prefix=".write-", dir=root)
        os.close(fd)
        os.unlink(temp)
        if (root / ".sw-wrapper").is_symlink():
            raise InstallError("A pasta .sw-wrapper não pode ser um link simbólico.")
        (root / ".sw-wrapper").mkdir(exist_ok=True)
        if self.mode == "build_only":
            result = self.build_source_only()
            if result != "built":
                return result
            return self.offer_native_after_appimage()
        self.status("stage", "Preparando o armazenamento portátil...")
        from .rollback import Rollback
        pending = Rollback(root)
        if pending.folder.is_dir():
            self.status("reveal", "Recuperando uma atualização interrompida...")
            self.status("stage", "Recuperando uma atualização interrompida...")
            pending.restore()
        # A power loss during a test/PR native-code build can leave root/ in
        # game/extracted/. Restore it before checking the portable installation.
        recover_code_stage(root, self.status)
        from .migration import legacy_folder, move_data, organize_portable
        organize_portable(root, self.status, self.cancel)
        state = read_state(root)
        legacy = legacy_folder(root)
        if legacy is not None and state.get("ignored_legacy") != str(legacy):
            collisions = [item.name for item in legacy.iterdir()
                          if (game_files(root) if item.name == "extracted"
                              else game_data(root) / item.name).exists()
                          or (game_files(root) if item.name == "extracted"
                              else game_data(root) / item.name).is_symlink()]
            if collisions:
                self.status("log", "A migração antiga precisa de revisão: arquivos com "
                                  "os mesmos nomes já existem no destino (" +
                                  ", ".join(collisions[:5]) + ").")
            elif self.ask(f"Instalação anterior encontrada em {legacy}.\nDeseja mover os arquivos, saves e configurações para a pasta deste AppImage?"):
                self.status("reveal", "Migrando os arquivos da instalação anterior...")
                move_data(legacy, root, self.status, self.cancel)
                organize_portable(root, self.status, self.cancel)
            else:
                state["ignored_legacy"] = str(legacy)
                write_state(root, state)
        portable_env(root)
        state = read_state(root)
        if self.mode == "startup":
            if not installed_runtime(root, state) and untracked_appimage(root):
                identify_local_appimage(root, self.status, self.cancel)
            return self.start_installed()
        if self.mode in ("play", "native"):
            if not valid_game(game_files(root)):
                raise InstallError("No valid game files in root/. Import your own game files first.")
            # Play never builds anything. Recompile may optionally select test/PR
            # or official first, but only after the user's explicit request.
            installed = (self.select_native_runtime() if self.mode == "native" else
                         installed_runtime(root, state))
            if installed is None:
                return "setup"
            state = read_state(root)
            if self.mode == "play":
                if not compiled_code(root):
                    raise InstallError("No valid native game library in game/. "
                                       "Use 'Recompile game code' explicitly if needed.")
                if state.get("built_cpu") and state["built_cpu"] != cpu_target():
                    raise InstallError("The native game code targets another CPU. "
                                       "Recompilation must be requested explicitly.")
                active = (state.get("test_digest") if state.get("runtime_source") == "test" else
                          state.get("custom_digest") if state.get("runtime_source") == "custom" else
                          state.get("official_digest"))
                if state.get("built_digest") != active:
                    self.status("log", "Launching with existing swgame.so without rebuilding. "
                                "The new AppImage may require a matching library; the upstream "
                                "loader will check compatibility.")
                return self.launch_existing(root, installed)
            return self.compile_native(root, installed)
        build_only = False  # An explicit AppImage-only operation exits above.
        if source and valid_game(game_files(root)):
            raise InstallError("O jogo já está instalado nesta pasta. Seus arquivos não serão substituídos.")
        if not build_only and not valid_game(game_files(root)) and not source:
            return "setup"
        if (not build_only and not from_setup and source is None and
                installed_runtime(root, state) is None):
            return "setup"
        # Check availability before copying many GiB of game files.
        if source and game_files(root).exists() and not valid_game(game_files(root)):
            raise InstallError("A pasta root/ já existe, mas está incompleta. Não será sobrescrita.")
        # Source builds are opt-in and do not replace the official executable.
        rollback = None
        current_source = state.get("runtime_source", "official")
        test = current_source == "test" if self.prs is None else self.prs == "test"
        custom = current_source == "custom" if self.prs is None else isinstance(self.prs, list) and bool(self.prs)
        installed = installed_runtime(root, state) if self.prs is None else None
        release_error = None
        if test:
            from .pr_build import build_test_appimage, test_runtime
            installed = test_runtime(root, state)
            if self.prs == "test":
                # Reuse the already-built test AppImage when upstream has not moved.
                # Failed native-code validation must never force another full runtime build.
                changed = True
                if installed and state.get("test_commit"):
                    from .pr_build import upstream_test_head
                    try:
                        current = upstream_test_head()
                        changed = current != state["test_commit"]
                        if not changed:
                            self.status("log", "Upstream test has not changed (" +
                                        current[:12] + "). Reusing the existing AppImage.")
                    except InstallError as error:
                        self.status("log", f"Cannot check upstream test for updates: {error}")
                        if not self.ask("The upstream test revision could not be checked. "
                                        "Use the existing locally built AppImage? "
                                        "Choose No to cancel."):
                            return "setup"
                        changed = False
                if changed:
                    if not self.ask("Build the latest upstream test branch locally? "
                                    "This is development code that may be unstable. "
                                    "The official AppImage and your saves will be preserved."):
                        return "setup"
                    result = build_test_appimage(root, self.status, self.cancel, ask=self.ask)
                    installed = result["path"]
                    state.update({
                        "runtime_source": "test", "test_digest": result["digest"],
                        "test_commit": result["commit"], "test_size": result["size"],
                        "test_mtime_ns": result["mtime_ns"],
                    })
                    write_state(root, state)
            if not installed:
                return "setup"
        elif custom:
            from .pr_build import build_pr_appimage, custom_runtime, selection_key
            desired = selection_key(self.prs) if self.prs else state.get("custom_selection")
            installed = custom_runtime(root, state)
            if self.prs and (not installed or state.get("custom_selection") != desired):
                if not self.ask("Selected PRs contain unreviewed third-party code. "
                                "Building and running them may execute arbitrary code. "
                                "Continue only if you trust every author. Continue?"):
                    return "setup"
                result = build_pr_appimage(root, self.prs, self.status, self.cancel, ask=self.ask)
                installed = result["path"]
                state.update({
                    "runtime_source": "custom", "custom_digest": result["digest"],
                    "custom_selection": result["selection"], "custom_size": result["size"],
                    "custom_mtime_ns": result["mtime_ns"],
                    "custom_prs": [{"number": pr["number"], "sha": pr["sha"]}
                                   for pr in self.prs],
                    "custom_base": result.get("base_commit"),
                })
                write_state(root, state)
            if not installed:
                return "setup"
        else:
            if self.prs is not None:
                state["runtime_source"] = "official"
                write_state(root, state)
            self.status("stage", "Checking official releases...")
            installed = local_release(root, state)
            try:
                remote = fetch_latest()
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, InstallError) as error:
                remote = None
                release_error = str(error)
                self.status("log", f"Update unavailable: {error}")
            if remote and (not installed or state.get("official_digest") != remote["digest"]):
                if not installed or self.ask(f"Savage Wind {remote['tag']} is available. Install it?"):
                    self.status("reveal", f"Installing Savage Wind {remote['tag']}...")
                    if installed:
                        rollback = Rollback(root)
                        self.status("stage", "Backing up the current version...")
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
            if not valid_game(game_files(root)):
                if isinstance(source, dict):
                    import_packages(root, source, self.status, self.cancel)
                elif source:
                    copy_game(root, source, self.status, self.cancel)
                else:
                    return "setup"
            # A rebuild is mandatory only after changing the executable or importing new game files.
            if not (custom or test):
                recover_completed_code(root, installed, state, self.status)
            active_digest = (state.get("test_digest") if test else
                             state.get("custom_digest") if custom else state.get("official_digest"))
            key = state.get("built_digest")
            target = cpu_target()
            cpu_changed = bool(state.get("built_cpu") and state["built_cpu"] != target)
            if (key != active_digest or cpu_changed or
                    not compiled_code(root)):
                self.status("reveal", "Preparando o código nativo do jogo...")
                if cpu_changed:
                    self.status("stage", "CPU diferente detectada; preparando código nativo compatível...")
                build_game(root, installed, self.status, self.cancel)
                state["built_digest"] = active_digest
                state["built_cpu"] = target
                write_state(root, state)
            elif not state.get("built_cpu"):
                # Adopt a working install without forcing an unnecessary rebuild.
                state["built_cpu"] = target
                write_state(root, state)
            if rollback:
                rollback.commit()
            return self.launch_existing(root, installed)
        except BaseException:
            if rollback:
                rollback.restore()
            raise
