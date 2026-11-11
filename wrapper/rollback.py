"""Zero-copy rollback for upstream updates and interrupted native-code rebuilds."""
import json
import os
from pathlib import Path
import shutil

from .core import InstallError, write_state

FILES = {
    "swgame.so": "game/swgame.so",
    "swgame.so.id": "game/swgame.so.id",
}


def files_for(state):
    source = state.get("runtime_source", "official")
    if source not in ("official", "test", "custom"):
        raise InstallError("Unknown runtime source; cannot prepare a safe update rollback.")
    return {**FILES, f"{source}.AppImage": f"SavageWind-{source}.AppImage"}


class Rollback:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.folder = self.root / ".sw-wrapper/rollback"

    def begin(self, state):
        if self.folder.exists():
            raise InstallError("Existe uma atualização anterior pendente. Recupere-a primeiro.")
        self.folder.mkdir(mode=0o700)
        try:
            saved = self.folder / "state.json"
            with saved.open("w", encoding="utf-8") as file:
                json.dump(state, file)
                file.flush()
                os.fsync(file.fileno())
            history = self.root / ".sw-wrapper/runtime-history.json"
            if history.is_symlink():
                raise InstallError("Runtime history cannot be a symbolic link.")
            if history.is_file():
                shutil.copy2(history, self.folder / "runtime-history.json")
            for name, relative in files_for(state).items():
                source = self.root / relative
                if source.is_symlink():
                    raise InstallError("A atualização não sobrescreve links simbólicos.")
                if source.is_file():
                    os.replace(source, self.folder / name)
        except Exception:
            self.restore()
            raise

    def restore(self):
        if not self.folder.is_dir():
            return False
        state_path = self.folder / "state.json"
        if not state_path.is_file():
            raise InstallError("Backup incompleto. Os arquivos antigos foram preservados.")
        with state_path.open(encoding="utf-8") as file:
            state = json.load(file)
        for name, relative in files_for(state).items():
            backup = self.folder / name
            if not backup.is_file():
                continue
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                raise InstallError("Um link simbólico impede a restauração segura.")
            os.replace(backup, target)
        write_state(self.root, state)
        history = self.root / ".sw-wrapper/runtime-history.json"
        backup = self.folder / "runtime-history.json"
        if history.is_symlink():
            raise InstallError("A symlink prevents safe runtime history restoration.")
        if backup.is_file():
            os.replace(backup, history)
        else:
            history.unlink(missing_ok=True)
        shutil.rmtree(self.folder)
        return True

    def commit(self):
        if self.folder.is_dir():
            shutil.rmtree(self.folder)
