"""Move an older upstream data folder into the physical AppImage directory, with user consent."""
import errno
import fcntl
import os
from pathlib import Path
import shutil
import tempfile

from .core import CHUNK, FICLONE, Cancelled, InstallError, check_cancel


def legacy_folder(root, home=None, xdg=None):
    root = Path(root).resolve()
    home = Path.home() if home is None else Path(home)
    xdg = os.environ.get("XDG_DATA_HOME") if xdg is None else xdg
    options = []
    if xdg:
        options.append(Path(xdg).expanduser() / "savagewind")
    options.append(home / ".local/share/savagewind")
    for path in options:
        if not path.is_symlink() and path.is_dir() and path.resolve() != root:
            return path
    return None


def copy_entry(source, destination, cancel, progress):
    if source.is_symlink():
        raise InstallError(f"Um link simbólico precisa ser revisado manualmente: {source}")
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as opened, destination.open("wb") as written:
            try:
                fcntl.ioctl(written.fileno(), FICLONE, opened.fileno())
                progress(source.stat().st_size)
            except OSError:
                while block := opened.read(CHUNK):
                    check_cancel(cancel)
                    written.write(block)
                    progress(len(block))
        shutil.copystat(source, destination)
    elif source.is_dir():
        destination.mkdir(parents=True)
        for part in source.iterdir():
            check_cancel(cancel)
            copy_entry(part, destination / part.name, cancel, progress)
        shutil.copystat(source, destination)
    else:
        raise InstallError(f"Arquivo especial não será movido: {source}")


def file_size(source, cancel):
    if source.is_file():
        return source.stat().st_size
    total = 0
    for base, dirs, files in os.walk(source, followlinks=False):
        check_cancel(cancel)
        for dirname in dirs:
            if (Path(base) / dirname).is_symlink():
                raise InstallError("A instalação antiga contém links simbólicos e precisa de revisão manual.")
        for filename in files:
            f = Path(base) / filename
            if f.is_symlink():
                raise InstallError("A instalação antiga contém links simbólicos e precisa de revisão manual.")
            total += f.stat().st_size
    return total


def move_data(source, root, emit, cancel):
    source, root = Path(source).resolve(), Path(root).resolve()
    if source == root or not source.is_dir():
        return 0
    children = list(source.iterdir())
    # Preflight before moving even one byte: never overwrite or merge directories belonging to the user.
    for item in children:
        check_cancel(cancel)
        if item.is_symlink() or (root / item.name).exists():
            raise InstallError(f"Conflito de migração em {item.name}. Nenhum arquivo foi sobrescrito.")
    emit("stage", "Verificando o tamanho da instalação anterior...", None)
    sizes = [(item, file_size(item, cancel)) for item in children]
    total, done, moved = sum(n for _, n in sizes), 0, 0
    for item, size in sizes:
        check_cancel(cancel)
        target = root / item.name
        emit("stage", f"Migrando {item.name}...", round(done * 100 / total, 1) if total else None)
        try:
            os.rename(item, target)
            done += size
            moved += 1
            emit("log", f"Movido instantaneamente: {item.name}", None)
            continue
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise InstallError(f"Falha ao mover {item.name}: {error}") from error
        temp = Path(tempfile.mkdtemp(prefix=".migration-", dir=root / ".sw-wrapper"))
        staged = temp / item.name
        completed = 0
        def progress(length):
            nonlocal done, completed
            completed += length
            done += length
            emit("progress", f"Copiando {item.name}: {done // CHUNK} MiB",
                 round(done * 100 / total, 1) if total else 100)
        try:
            copy_entry(item, staged, cancel, progress)
            check_cancel(cancel)
            if completed != size:
                raise InstallError(f"Tamanho inconsistente ao migrar {item.name}. A origem foi preservada.")
            os.rename(staged, target)
            # The original is deleted only after the complete destination has been published.
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            moved += 1
            emit("log", f"Migrado e conferido: {item.name}", None)
        finally:
            if temp.exists():
                shutil.rmtree(temp)
    try:
        source.rmdir()
    except OSError:
        pass
    emit("progress", "Instalação antiga migrada com sucesso.", 100)
    return moved
