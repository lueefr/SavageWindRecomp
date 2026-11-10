"""Build an optional local AppImage from selected, pinned upstream pull requests.

Pull-request code is untrusted. Run it only after an explicit user opt-in.
Game files and prod.keys are never mounted into the build container.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import tempfile
import urllib.request

from .core import Cancelled, InstallError, check_cancel, sha256, stop_process, tls_context

UPSTREAM = "https://github.com/Revivedx/SavageWindRecomp.git"
API = "https://api.github.com/repos/Revivedx/SavageWindRecomp/pulls"
SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_PRS = 10


def open_prs():
    """Return metadata only; PR code is never executed while listing."""
    results = []
    for page in range(1, 5):
        url = f"{API}?state=open&per_page=100&page={page}"
        request = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "SavageWindPortable/1.0",
        })
        with urllib.request.urlopen(request, timeout=20, context=tls_context()) as response:
            items = json.load(response)
        if not isinstance(items, list):
            raise InstallError("GitHub returned an invalid pull-request listing.")
        for item in items:
            if item.get("base", {}).get("ref") != "test":
                continue
            number = item.get("number")
            head = item.get("head") or {}
            commit = head.get("sha", "")
            if not isinstance(number, int) or number <= 0 or not isinstance(commit, str) or not SHA.fullmatch(commit):
                continue
            body = (item.get("body") or "").strip()
            summary = next((line.strip("# *>- \t") for line in body.splitlines()
                            if line.strip("# *>- \t")), "No description provided.")
            results.append({
                "number": number,
                "sha": commit,
                "title": (item.get("title") or f"PR #{number}")[:180],
                "description": summary[:360],
                "draft": bool(item.get("draft")),
                "url": f"https://github.com/Revivedx/SavageWindRecomp/pull/{number}",
            })
        if len(items) < 100:
            break
    return results


def validated(prs):
    if not isinstance(prs, (list, tuple)) or not 1 <= len(prs) <= MAX_PRS:
        raise InstallError(f"Select between 1 and {MAX_PRS} pull requests.")
    result, seen = [], set()
    for entry in prs:
        if not isinstance(entry, dict):
            raise InstallError("Invalid pull request.")
        number, commit = entry.get("number"), entry.get("sha", "")
        if (type(number) is not int or number <= 0 or number in seen or
                not isinstance(commit, str) or not SHA.fullmatch(commit)):
            raise InstallError("Invalid or duplicate PR number / commit SHA.")
        seen.add(number)
        result.append({"number": number, "sha": commit})
    return result


def selection_key(prs):
    prs = validated(prs)
    return hashlib.sha256(json.dumps(prs, sort_keys=True).encode("ascii")).hexdigest()


def custom_runtime(root, state):
    """Do not trust stale metadata when a custom AppImage was modified."""
    root = Path(root)
    path = root / "SavageWind-custom.AppImage"
    if (path.is_symlink() or not path.is_file() or
            state.get("runtime_source") != "custom" or
            not state.get("custom_digest") or not state.get("custom_selection")):
        return None
    stat = path.stat()
    if stat.st_size != state.get("custom_size") or stat.st_mtime_ns != state.get("custom_mtime_ns"):
        return None
    return path


def _run(argv, cwd, emit, cancel, logfile):
    check_cancel(cancel)
    emit("stage", "Running: " + " ".join(argv[:4]) + "...", None)
    process = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True,
                               stdin=subprocess.DEVNULL, bufsize=0)
    pending = bytearray()
    try:
        with selectors.DefaultSelector() as sel:
            sel.register(process.stdout, selectors.EVENT_READ)
            while sel.get_map():
                check_cancel(cancel)
                for key, _ in sel.select(timeout=0.3):
                    data = os.read(key.fd, 16384)
                    if not data:
                        sel.unregister(key.fileobj)
                        continue
                    pending.extend(data)
                    if len(pending) > 1024 * 1024 and b"\n" not in pending:
                        pending = pending[-4096:]
                    while b"\n" in pending:
                        line, _, remain = pending.partition(b"\n")
                        pending = bytearray(remain)
                        line = line.decode("utf-8", "replace")[:1200]
                        logfile.write(line + "\n")
                        emit("log", line, None)
        if pending:
            logfile.write(pending.decode("utf-8", "replace")[:1200] + "\n")
        returncode = process.wait()
        if returncode:
            raise InstallError(f"Command failed (exit {returncode}): {argv[0]}. See PR build log.")
    except (Cancelled, Exception):
        if process.poll() is None:
            stop_process(process)
        raise
    finally:
        process.stdout.close()


def build_pr_appimage(root, prs, emit, cancel):
    """Merge pinned PR heads onto upstream test in order; build without game data."""
    root = Path(root).resolve()
    prs = validated(prs)
    if shutil.which("git") is None or shutil.which("podman") is None:
        raise InstallError("Local PR builds require git and rootless Podman installed on the host.")
    work = root / ".sw-wrapper"
    if work.is_symlink():
        raise InstallError("Installer working directory cannot be a symlink.")
    work.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="pr-build-", dir=work))
    log = work / "pr-build.log"
    try:
        with log.open("w", encoding="utf-8") as output:
            source = stage / "source"
            emit("reveal", "Building a custom AppImage from selected PRs...", None)
            _run(["git", "clone", "--no-tags", "--single-branch", "--branch", "test",
                  UPSTREAM, str(source)], stage, emit, cancel, output)
            # A merge commit needs an identity; no global Git configuration is changed.
            for pr in prs:
                check_cancel(cancel)
                number = pr["number"]
                emit("stage", f"Applying PR #{number}...", None)
                _run(["git", "fetch", "origin", f"refs/pull/{number}/head"],
                     source, emit, cancel, output)
                actual = subprocess.run(["git", "rev-parse", "FETCH_HEAD"],
                                        cwd=source, check=True, capture_output=True, text=True).stdout.strip()
                if actual != pr["sha"]:
                    raise InstallError(f"PR #{number} changed during setup; refresh the PR list and try again.")
                _run(["git", "-c", "user.name=Savage Wind Portable",
                      "-c", "user.email=portable@localhost", "merge", "--no-edit",
                      "--no-ff", actual], source, emit, cancel, output)
            # Run all PR-controlled build scripts inside a rootless container.
            # Never execute the PR's Makefile or appimage.sh directly on the host.
            emit("stage", "Compiling the custom AppImage in an isolated Podman container...", None)
            image = "localhost/savagewind-pr-build:amd64"
            _run(["podman", "build", "--arch", "amd64", "-t", image, "-f",
                  "tools/linux/Containerfile", "tools/linux"], source, emit, cancel, output)
            run = ["podman", "run", "--rm", "--arch", "amd64",
                   "-v", f"{source}:/src", "-w", "/src",
                   "-e", "SYSROOT=/src/build/linux/aarch64/sysroot", image]
            _run(run + ["tools/linux/gen_helper.sh", "x86_64"], source, emit, cancel, output)
            _run(run + ["tools/linux/package.sh", "x86_64"], source, emit, cancel, output)
            results = list((source / "build/dist").glob("Savage_Wind-*-x86_64.AppImage"))
            if len(results) != 1 or results[0].is_symlink() or results[0].stat().st_size < 1024 * 1024:
                raise InstallError("The build did not produce exactly one x86_64 AppImage.")
            with results[0].open("rb") as stream:
                if stream.read(4) != b"\x7fELF":
                    raise InstallError("The generated AppImage is not an ELF executable.")
            check_cancel(cancel)
            dest = root / "SavageWind-custom.AppImage"
            if dest.is_symlink():
                raise InstallError("The custom AppImage destination is a symlink.")
            fd, temp = tempfile.mkstemp(prefix=".custom-", dir=root)
            try:
                with os.fdopen(fd, "wb") as target, results[0].open("rb") as source_file:
                    shutil.copyfileobj(source_file, target, length=1024 * 1024)
                    target.flush()
                    os.fsync(target.fileno())
                os.chmod(temp, 0o755)
                digest = sha256(temp, cancel)
                check_cancel(cancel)
                os.replace(temp, dest)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            stat = dest.stat()
            emit("progress", "Custom AppImage built successfully.", 100)
            return {"path": dest, "digest": digest, "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns, "selection": selection_key(prs)}
    finally:
        # Only this uniquely-created directory is removed. Never delete root/game/saves.
        shutil.rmtree(stage)
