"""Persist installed AppImage identity and an auditable, portable update history."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile

NAME = "runtime-history.json"
DIGEST = re.compile(r"[0-9a-f]{64}\\Z")


def identity(state):
    """Extract only the runtime facts needed for later identification."""
    source = state.get("runtime_source", "official")
    if source not in ("official", "test", "custom"):
        return None
    digest = state.get(source + "_digest")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        return None
    result = {
        "source": source, "sha256": digest,
        "size": state.get(source + "_size"),
        "mtime_ns": state.get(source + "_mtime_ns"),
        "version": (state.get("official_tag") if source == "official" else
                    state.get("test_commit") if source == "test" else
                    state.get("custom_base")),
    }
    if source == "custom":
        result["prs"] = state.get("custom_prs") or []
        result["selection"] = state.get("custom_selection")
    return result


def _path(root):
    path = Path(root) / ".sw-wrapper" / NAME
    if path.is_symlink() or path.parent.is_symlink():
        from .core import InstallError
        raise InstallError("Runtime history must not be a symbolic link.")
    return path


def read(root):
    path = _path(root)
    try:
        with path.open(encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, dict) and isinstance(data.get("history"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"schema": 1, "current": None, "history": []}


def _save(root, document):
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".runtime-history-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(document, file, ensure_ascii=False, indent=2)
            file.write("\\n")
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def record(root, state, changes=None):
    """Append on identity changes; enrich the same event after a successful update."""
    current = identity(state)
    if current is None:
        return
    document = read(root)
    existing = document.get("current")
    comparable = ("source", "sha256", "version", "prs", "selection")
    changed = not isinstance(existing, dict) or any(
        existing.get(key) != current.get(key) for key in comparable)
    if changed:
        entry = dict(current)
        entry["updated_at"] = datetime.now(timezone.utc).isoformat()
        entry["changes"] = []
        document["history"].append(entry)
        document["current"] = entry
    else:
        entry = document["current"]
        entry.update(current)
    if changes is not None:
        # Store the escaped changelog with its original clickable GitHub links.
        title = str(changes.get("title") or "")[:200]
        details = str(changes.get("html") or "")[:12000]
        entry["changes"] = [{"title": title, "html": details}]
    if changed or changes is not None or not _path(root).is_file():
        _save(root, document)


def recover(root):
    """Reconstruct missing state only when the tracked AppImage matches its SHA-256."""
    current = read(root).get("current")
    if not isinstance(current, dict):
        return {}
    source = current.get("source")
    if source not in ("official", "test", "custom"):
        return {}
    path = Path(root) / f"SavageWind-{source}.AppImage"
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        stat = path.stat()
        if (stat.st_size != current.get("size") or
                stat.st_mtime_ns != current.get("mtime_ns") or
                not DIGEST.fullmatch(str(current.get("sha256") or ""))):
            return {}
        with path.open("rb") as file:
            if file.read(4) != b"\\x7fELF":
                return {}
        from .core import sha256
        if sha256(path) != current["sha256"]:
            return {}
    except OSError:
        return {}
    state = {
        "runtime_source": source,
        source + "_digest": current["sha256"],
        source + "_size": current["size"],
        source + "_mtime_ns": current["mtime_ns"],
    }
    if source == "official":
        state["official_tag"] = current.get("version")
    elif source == "test":
        state["test_commit"] = current.get("version")
    else:
        state["custom_base"] = current.get("version")
        state["custom_prs"] = current.get("prs", [])
        state["custom_selection"] = current.get("selection")
    return state
