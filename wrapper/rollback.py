"""Zero-copy rollback for upstream updates and interrupted native-code rebuilds."""
import json
import os
from pathlib import Path
import shutil

from .core import InstallError, write_state

FILES = {
    "official.AppImage": "SavageWind-official.AppImage",
    "swgame.so": "extracted/swgame.so",
    "swgame.so.id": "extracted/swgame.so.id",
}


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
            for name, relative in FILES.items():
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
        for name, relative in FILES.items():
            backup = self.folder / name
            if not backup.is_file():
                continue
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                raise InstallError("Um link simbólico impede a restauração segura.")
            os.replace(backup, target)
        write_state(self.root, state)
        shutil.rmtree(self.folder)
        return True

    def commit(self):
        if self.folder.is_dir():
            shutil.rmtree(self.folder)
