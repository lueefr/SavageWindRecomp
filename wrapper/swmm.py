"""Optional, portable integration with ustruct/SWMM's Linux mod manager."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request

from .core import InstallError, check_cancel, tls_context

REPO = "ustruct/SWMM"
API = "https://api.github.com/repos/" + REPO + "/releases/latest"
SITE = "https://github.com/" + REPO
ASSET = "swmm-x86_64-linux.tar.xz"
TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_FILE = 150 * 1024 * 1024


def folder(root):
    return Path(root).resolve() / ".sw-wrapper/swmm"


def managed(root):
    file = folder(root) / "swmm"
    return file if file.is_file() and not file.is_symlink() and os.access(file, os.X_OK) else None


def external():
    """Honor an existing installation, but never modify its executable."""
    candidates = [Path.home() / ".local/share/swmm/swmm"]
    command = shutil.which("swmm")
    if command:
        candidates.append(Path(command))
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
    return None


def installed(root):
    return managed(root) or external()


def _release():
    request = urllib.request.Request(API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "SavageWindPortable/SWMM",
    })
    with urllib.request.urlopen(request, timeout=15, context=tls_context()) as stream:
        info = json.load(stream)
    tag = info.get("tag_name")
    if not isinstance(tag, str) or not TAG.fullmatch(tag) or info.get("draft") or info.get("prerelease"):
        raise InstallError("The SWMM release metadata is not a verified stable release.")
    url = SITE + "/releases/download/" + tag + "/" + ASSET
    for entry in info.get("assets") or []:
        digest = entry.get("digest", "")
        if (entry.get("name") == ASSET and entry.get("browser_download_url") == url
                and isinstance(digest, str) and digest.startswith("sha256:")
                and SHA.fullmatch(digest[7:]) and isinstance(entry.get("size"), int)
                and 1024 * 1024 <= entry["size"] <= MAX_FILE):
            return {"tag": tag, "url": url, "size": entry["size"], "sha256": digest[7:]}
    raise InstallError("SWMM does not offer a verifiable Linux x86_64 archive in its latest release.")


def install(root, emit, cancel):
    """Download a SHA-256 verified archive; never execute a downloaded install script."""
    root = Path(root).resolve()
    base = root / ".sw-wrapper"
    dest = folder(root)
    if base.is_symlink() or dest.is_symlink():
        raise InstallError("Portable SWMM directory cannot be a symbolic link.")
    base.mkdir(exist_ok=True)
    dest.mkdir(exist_ok=True)
    info = _release()
    fd, name = tempfile.mkstemp(prefix=".swmm-", suffix=".tar.xz", dir=base)
    archive = Path(name)
    staged = Path(tempfile.mkdtemp(prefix=".swmm-stage-", dir=base))
    try:
        emit("stage", "Downloading SWMM " + info["tag"] + "...", None)
        request = urllib.request.Request(info["url"], headers={
            "User-Agent": "SavageWindPortable/SWMM",
        })
        sha = hashlib.sha256()
        total = 0
        with os.fdopen(fd, "wb") as target, urllib.request.urlopen(
                request, timeout=30, context=tls_context()) as stream:
            while chunk := stream.read(1024 * 1024):
                check_cancel(cancel)
                total += len(chunk)
                if total > info["size"]:
                    raise InstallError("SWMM archive exceeded its advertised size.")
                sha.update(chunk)
                target.write(chunk)
                emit("progress", "Downloading SWMM...", round(total * 100 / info["size"], 1))
        if total != info["size"] or sha.hexdigest() != info["sha256"]:
            raise InstallError("SWMM release SHA-256 or archive size mismatch.")
        with tarfile.open(archive, "r:xz") as packed:
            files = {}
            for member in packed:
                relative = member.name.removeprefix("./")
                if not member.isfile() or relative not in ("swmm", "swmm.png", "LICENSE"):
                    continue
                if relative in files or not 0 < member.size <= MAX_FILE:
                    raise InstallError("SWMM archive contains an invalid duplicate file.")
                files[relative] = member
            if "swmm" not in files:
                raise InstallError("SWMM release archive has no executable.")
            for name, member in files.items():
                check_cancel(cancel)
                source = packed.extractfile(member)
                if source is None:
                    raise InstallError("SWMM archive could not be read.")
                with source, (staged / name).open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                if name == "swmm":
                    os.chmod(staged / name, 0o755)
        new = staged / "swmm"
        with new.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                raise InstallError("SWMM archive does not contain a Linux ELF executable.")
        # Keep existing configuration, profiles, mods and previous executable intact.
        original = dest / "swmm"
        previous = dest / "swmm.previous"
        if original.is_symlink() or previous.is_symlink():
            raise InstallError("Existing SWMM binary/backup cannot be a symbolic link.")
        if original.exists():
            previous.unlink(missing_ok=True)
            os.replace(original, previous)
        try:
            os.replace(new, original)
        except BaseException:
            if previous.is_file():
                os.replace(previous, original)
            raise
        for name in ("swmm.png", "LICENSE"):
            if (staged / name).is_file() and not (dest / name).is_symlink():
                os.replace(staged / name, dest / name)
        (dest / "release.json").write_text(json.dumps(info, indent=2) + "\n")
        emit("progress", "SWMM installed in portable storage.", 100)
        return original
    finally:
        archive.unlink(missing_ok=True)
        shutil.rmtree(staged, ignore_errors=True)


def _bridge(root):
    """Expose root/ as extracted/ to SWMM without touching the native game files."""
    root = Path(root).resolve()
    original = root / "root"
    if original.is_symlink() or not (original / "romfs").is_dir():
        raise InstallError("Install the extracted game files in root/ before using SWMM.")
    bridge = folder(root) / "bridge"
    if bridge.is_symlink():
        raise InstallError("SWMM bridge cannot be a symbolic link.")
    bridge.mkdir(parents=True, exist_ok=True)
    link = bridge / "extracted"
    if link.is_symlink():
        if link.resolve() != original:
            raise InstallError("SWMM extraction link points to a different game directory.")
    elif link.exists():
        raise InstallError("SWMM extraction path is occupied by another directory.")
    else:
        link.symlink_to(original, target_is_directory=True)
    return bridge


def _shim(root, launcher):
    """The Play button in SWMM enters the portable launcher, not a separate game root."""
    root = Path(root).resolve()
    executable = folder(root) / "savagewind"
    if executable.is_symlink():
        raise InstallError("SWMM game launcher cannot be a symbolic link.")
    if launcher is None:
        raise InstallError("Run the portable launcher AppImage to configure SWMM.")
    launcher = Path(launcher).expanduser().resolve()
    if launcher.parent != root or not launcher.is_file():
        raise InstallError("The current launcher AppImage was not found in the portable directory.")
    script = ("#!/bin/sh\n"
              "exec " + shlex.quote(str(launcher)) + " --mods-play --root " +
              shlex.quote(str(root)) + " \"$@\"\n")
    fd, temporary = tempfile.mkstemp(prefix=".swmm-launch-", dir=executable.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(script)
        os.chmod(temporary, 0o755)
        os.replace(temporary, executable)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return executable


def _settings(path, shim, storage):
    """Seed settings once; SWMM owns and changes settings afterwards."""
    if path.is_symlink():
        raise InstallError("SWMM settings cannot be a symbolic link.")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "storage_dir: " + json.dumps(str(storage)) + "\n"
            "savage_wind:\n  executable: " + json.dumps(str(shim)) + "\n",
            encoding="utf-8",
        )


def launch(root, launcher=None):
    root = Path(root).resolve()
    manager = installed(root)
    if manager is None:
        raise InstallError("SWMM is not installed. Choose Install SWMM first.")
    bridge = _bridge(root)
    shim = _shim(root, launcher)
    env = dict(os.environ)
    env["SAVAGEWIND_DATA_ROOT"] = str(bridge)
    # Never let an enclosing launcher AppImage inject Qt/GTK libraries into SWMM.
    for key in ("APPIMAGE", "APPDIR", "LD_LIBRARY_PATH", "LD_PRELOAD",
                "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    local = manager == managed(root)
    if local:
        config = folder(root) / "config/settings.yml"
        storage = folder(root) / "data"
        _settings(config, shim, storage)
        command = [str(manager), "--portable"]
    else:
        # Honor a globally installed binary without writing to its global config.
        base = folder(root) / "external"
        env["XDG_CONFIG_HOME"] = str(base / "config")
        env["XDG_DATA_HOME"] = str(base / "data")
        config = base / "config/SWMM/settings.yml"
        storage = base / "data/SWMM"
        _settings(config, shim, storage)
        command = [str(manager)]
    log = folder(root) / "swmm.log"
    with log.open("ab") as output:
        return subprocess.Popen(command, cwd=manager.parent, env=env,
                                stdout=output, stderr=subprocess.STDOUT,
                                start_new_session=True)


def overlay(root):
    """Use SWMM's default portable deployment while preserving user-selected env."""
    root = Path(root).resolve()
    candidates = [folder(root) / "data/overlay",
                  folder(root) / "external/data/SWMM/overlay"]
    for path in candidates:
        if path.is_symlink() or not path.is_dir():
            continue
        if not path.resolve().is_relative_to(folder(root)):
            continue
        for _, _, filenames in os.walk(path):
            if filenames:
                return path
    return None
