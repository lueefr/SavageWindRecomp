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
    # Prefer the user's official system installation, without installing a duplicate.
    return external() or managed(root)


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
    if (root / ".sw-wrapper").is_symlink() or folder(root).is_symlink():
        raise InstallError("SWMM portable storage cannot be a symbolic link.")
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
    executable = _bridge(root) / "savagewind"
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
    if executable.is_file() and executable.read_text(encoding="utf-8") == script:
        if not os.access(executable, os.X_OK):
            executable.chmod(0o755)
        return executable
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


def _shortcut_path(root, env):
    """A distinct app-menu entry per portable game; never replace SWMM's own entry."""
    data_home = Path(env.get("XDG_DATA_HOME") or (Path.home() / ".local/share"))
    if not data_home.is_absolute():
        data_home = Path.home() / ".local/share"
    applications = data_home / "applications"
    fingerprint = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:12]
    return applications / f"savagewind-portable-mods-{fingerprint}.desktop"


def _desktop_quote(path):
    """Escape a single Desktop Entry Exec argument, not a shell command."""
    value = str(path).replace("\\", "\\\\").replace('"', '\\"')
    value = value.replace("$", "\\$").replace("\x60", "\\\x60").replace("%", "%%")
    return '"' + value + '"'


def _atomic_text(path, text, mode=0o644):
    path = Path(path)
    if path.is_symlink():
        raise InstallError(f"Refusing to overwrite a symbolic link: {path}")
    if path.exists() and not path.is_file():
        raise InstallError(f"Not a regular shortcut file: {path}")
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return
    if path.parent.is_symlink():
        raise InstallError(f"Refusing to write through a symbolic link: {path.parent}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".mods-desktop-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _shortcuts(root, launcher, manager, env):
    """Offer a normal application-menu shortcut without needing --config."""
    launcher = Path(launcher).resolve()
    root = Path(root).resolve()
    icon = folder(root) / "swmm.png"
    if not icon.is_file() and not icon.is_symlink():
        icon = manager.parent / "swmm.png"
    icon_value = str(icon) if icon.is_file() and not icon.is_symlink() else "applications-games"
    entry = "\n".join((
        "[Desktop Entry]",
        "Type=Application",
        "Name=Savage Wind Portable - Mods",
        "Comment=Manage portable Savage Wind mods with the official SWMM",
        "Exec=" + _desktop_quote(launcher) + " --mods",
        "Icon=" + icon_value,
        "Terminal=false",
        "Categories=Game;Utility;",
        "StartupNotify=true",
        "",
    ))
    _atomic_text(root / "SavageWind-Mods.desktop", entry)
    _atomic_text(_shortcut_path(root, env), entry)


def prepare(root, launcher=None, register=True, environ=None):
    """Automatically bridge an existing SWMM installation on every portable launch.

    Never start SWMM or install packages here. The official executable, its
    profiles, configuration, cache and overlay stay distinct from game files.
    """
    root = Path(root).resolve()
    manager = installed(root)
    if manager is None or not (root / "root/romfs").is_dir():
        return None
    env = dict(os.environ if environ is None else environ)
    launcher = launcher or env.get("APPIMAGE")
    if not launcher:
        return None
    shim = _shim(root, launcher)
    bridge = shim.parent
    env["SAVAGEWIND_DATA_ROOT"] = str(bridge)
    # Do not inherit AppImage's bundled libraries or a different game's overlay.
    for key in ("APPIMAGE", "APPDIR", "LD_LIBRARY_PATH", "LD_PRELOAD",
                "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "PYTHONPATH",
                "PYTHONHOME", "SAVAGEWIND_ROMFS_OVERLAY"):
        env.pop(key, None)
    local = manager == managed(root)
    if local:
        config = folder(root) / "config/settings.yml"
        storage = folder(root) / "data"
        command = [str(manager), "--portable"]
        base = folder(root)
    else:
        # Keep the user-installed official SWMM binary unchanged. Never overwrite
        # the user's global SWMM settings, profiles or application launcher.
        base = folder(root) / "external"
        config = base / "config/SWMM/settings.yml"
        storage = base / "data/SWMM"
        command = [str(manager)]
    _settings(config, shim, storage)
    # The stock Linux SWMM also uses XDG and system services. Keep its files in
    # this portable installation without changing any existing global config.
    home = base / "home"
    home.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(home)
    if not local:
        env["XDG_CONFIG_HOME"] = str(base / "config")
        env["XDG_DATA_HOME"] = str(base / "data")
    env["XDG_CACHE_HOME"] = str(base / "cache")
    env["XDG_STATE_HOME"] = str(base / "state")
    if register:
        _shortcuts(root, launcher, manager, os.environ if environ is None else environ)
    return manager, command, env


def launch(root, launcher=None):
    root = Path(root).resolve()
    result = prepare(root, launcher=launcher)
    if result is None:
        raise InstallError("SWMM or the extracted game files are unavailable.")
    manager, command, env = result
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
