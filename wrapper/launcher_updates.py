"""Update the portable launcher itself from versioned fork releases (not game builds)."""
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import urllib.error
import urllib.request

from .core import Cancelled, InstallError, check_cancel, tls_context

REPO = "lueefr/SavageWindRecomp"
API = "https://api.github.com/repos/" + REPO
SITE = "https://github.com/" + REPO
RELEASES = API + "/releases?per_page=100"
ASSET = "SavageWind-Portable-x86_64.AppImage"
VERSIONS = re.compile(r"portable-v(\d+)\.(\d+)\.(\d+)\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _json(url):
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "SavageWindPortable/1.0",
    })
    with urllib.request.urlopen(request, timeout=12, context=tls_context()) as source:
        return json.load(source)


def _image(root, env=None):
    """Only permit an actual, local, writable launcher AppImage to self-update."""
    env = os.environ if env is None else env
    name = env.get("APPIMAGE")
    if not name:
        return None
    path = Path(name).expanduser().absolute()
    if (path.parent.resolve() != Path(root).resolve()
            or not path.name.startswith("SavageWind-Portable")
            or not path.name.endswith(".AppImage")
            or path.is_symlink() or not path.is_file()):
        return None
    try:
        with path.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                return None
    except OSError:
        return None
    return path


def _build_info(env=None):
    env = os.environ if env is None else env
    path = env.get("SAVAGEWIND_LAUNCHER_METADATA")
    if not path:
        bindir = env.get("SAVAGEWIND_WRAPPER_BINDIR")
        if bindir:
            path = str(Path(bindir).parent.parent / "share/wrapper/launcher-version.json")
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        tag, commit = data.get("tag"), data.get("commit")
        return {"tag": tag if isinstance(tag, str) and VERSIONS.fullmatch(tag) else None,
                "commit": commit if isinstance(commit, str) and SHA.fullmatch(commit) else None}
    except (OSError, ValueError):
        return {}


def _versions(releases):
    """Only signed-by-the-GitHub-API digest metadata for our exact AppImage qualifies."""
    found = []
    if not isinstance(releases, list):
        raise InstallError("Invalid launcher release catalogue.")
    for item in releases:
        if not isinstance(item, dict) or item.get("draft") or not item.get("published_at"):
            continue
        tag = item.get("tag_name")
        match = VERSIONS.fullmatch(tag) if isinstance(tag, str) else None
        if not match:
            continue
        for asset in item.get("assets") or []:
            digest = asset.get("digest", "")
            address = SITE + "/releases/download/" + tag + "/" + ASSET
            if (asset.get("name") != ASSET or asset.get("browser_download_url") != address
                    or not isinstance(digest, str) or not digest.startswith("sha256:")
                    or not DIGEST.fullmatch(digest[7:])
                    or type(asset.get("size")) is not int or asset["size"] < 1024 * 1024):
                continue
            found.append({
                "tag": tag, "version": tuple(map(int, match.groups())),
                "sha256": digest[7:], "size": asset["size"],
                "url": address, "body": item.get("body") or "",
                "commit": item.get("target_commitish"),
            })
    return sorted(found, key=lambda item: item["version"])


def _digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _installed(image, versions, env=None):
    """Unknown previews are not mistaken for old releases and downgraded."""
    build = _build_info(env)
    tag = build.get("tag")
    if tag:
        return {"tag": tag, "version": tuple(map(int, VERSIONS.fullmatch(tag).groups()))}
    commit = build.get("commit")
    if commit:
        return {"commit": commit}
    digest = _digest(image)
    known = next((entry for entry in versions if entry["sha256"] == digest), None)
    return {"tag": known["tag"], "version": known["version"]} if known else None


def _newer(latest, installed):
    if installed is None:
        return False
    if "version" in installed:
        return latest["version"] > installed["version"]
    before, after = latest.get("commit"), installed.get("commit")
    if not (isinstance(before, str) and SHA.fullmatch(before)
            and isinstance(after, str) and SHA.fullmatch(after)):
        return False
    if before == after:
        return False
    result = _json(API + "/compare/" + before + "..." + after)
    # The installed preview can be ahead of the latest release. Never downgrade it.
    return result.get("status") == "behind"


def _anchor(url, title):
    if not url.startswith(SITE + "/"):
        return html.escape(title)
    return '<a href="' + html.escape(url, quote=True) + '">' + html.escape(title) + '</a>'


def _description(latest, installed):
    page = SITE + "/releases/tag/" + latest["tag"]
    current = installed.get("tag") or (installed.get("commit") or "")[:12]
    changes = []
    for line in latest["body"].splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line[2:])
        line = re.sub(r"[*_\x60]+", "", line).strip()
        if line:
            changes.append("<li>" + html.escape(line[:180]) + "</li>")
        if len(changes) == 4:
            break
    if not changes:
        changes.append("<li>See the linked release notes for details.</li>")
    return (
        "<b>Portable launcher update available</b>"
        "<p>Installed launcher: " + html.escape(current or "unknown") +
        "<p>Available launcher: " + _anchor(page, latest["tag"]) + "</p>"
        "<p>This updates the portable launcher itself, not the Savage Wind game."
        " Your saves, game data and compiled code are preserved.</p>"
        "<ul>" + "".join(changes) + "</ul>"
    )


def check(root, env=None):
    image = _image(root, env)
    if image is None:
        return None
    releases = _versions(_json(RELEASES))
    if not releases:
        return None
    latest = releases[-1]
    installed = _installed(image, releases, env)
    if not _newer(latest, installed):
        return None
    return {
        "source": "launcher", "title": "Savage Wind Portable — Launcher update",
        "html": _description(latest, installed), "release": latest,
        "revision": latest["sha256"],
    }


def _write_state(root, info):
    path = Path(root) / ".sw-wrapper/launcher.json"
    if path.is_symlink():
        raise InstallError("Launcher update state cannot be a symlink.")
    path.parent.mkdir(exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".launcher-state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(info, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install(root, update, emit, cancel, env=None):
    """Atomically replace only this AppImage after verified download; retain rollback."""
    root = Path(root).resolve()
    image = _image(root, env)
    if image is None:
        raise InstallError("The running portable launcher AppImage could not be verified.")
    info = update["release"]
    if info["url"] != SITE + "/releases/download/" + info["tag"] + "/" + ASSET:
        raise InstallError("Unexpected launcher download URL.")
    work = root / ".sw-wrapper"
    if work.is_symlink():
        raise InstallError("Launcher update folder cannot be a symlink.")
    work.mkdir(exist_ok=True)
    backup = work / "launcher-previous.AppImage"
    if backup.is_symlink():
        raise InstallError("Launcher backup cannot be a symlink.")
    fd, temporary = tempfile.mkstemp(prefix=".launcher-download-", dir=root)
    stage = Path(temporary)
    previous = work / ".launcher-previous-next"
    try:
        emit("stage", "Downloading portable launcher " + info["tag"] + "...", None)
        request = urllib.request.Request(info["url"], headers={
            "User-Agent": "SavageWindPortable/1.0",
        })
        received = 0
        sha = hashlib.sha256()
        with os.fdopen(fd, "wb") as output, urllib.request.urlopen(
                request, timeout=30, context=tls_context()) as stream:
            while chunk := stream.read(1024 * 1024):
                check_cancel(cancel)
                received += len(chunk)
                if received > info["size"]:
                    raise InstallError("Launcher download exceeded its advertised size.")
                output.write(chunk)
                sha.update(chunk)
                emit("progress", "Downloading portable launcher...",
                     round(received * 100 / info["size"], 1))
            output.flush()
            os.fsync(output.fileno())
        check_cancel(cancel)
        if received != info["size"] or sha.hexdigest() != info["sha256"]:
            raise InstallError("The portable launcher SHA-256 or size did not match the release.")
        with stage.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                raise InstallError("The downloaded launcher is not an ELF AppImage.")
        current = image.stat()
        os.chmod(stage, stat.S_IMODE(current.st_mode) | 0o111)
        if previous.is_symlink():
            raise InstallError("Temporary launcher backup is a symlink.")
        previous.unlink(missing_ok=True)
        os.link(image, previous)
        os.replace(previous, backup)
        check_cancel(cancel)
        try:
            os.replace(stage, image)
            _write_state(root, {
                "tag": info["tag"], "sha256": info["sha256"],
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "release_url": SITE + "/releases/tag/" + info["tag"],
                "changelog": update["html"],
            })
        except BaseException:
            if backup.is_file():
                os.replace(backup, image)
            raise
        emit("progress", "Portable launcher updated. The next start uses the new version.", 100)
        return image
    finally:
        stage.unlink(missing_ok=True)
        previous.unlink(missing_ok=True)
