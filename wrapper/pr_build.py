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
            # Prefer the first meaningful line over generic Markdown headings.
            summary = next((line.strip("-* >\\t") for line in body.splitlines()
                            if line.strip() and not line.lstrip().startswith(("#", "<", "![", "http", "`", "|"))),
                           "No description provided.")
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


def test_runtime(root, state):
    """Locate a previously built upstream test AppImage without network access."""
    root = Path(root)
    path = root / "SavageWind-test.AppImage"
    if (path.is_symlink() or not path.is_file() or
            state.get("runtime_source") != "test" or
            not state.get("test_digest") or not state.get("test_commit")):
        return None
    stat = path.stat()
    if stat.st_size != state.get("test_size") or stat.st_mtime_ns != state.get("test_mtime_ns"):
        return None
    return path


def bundled_builder():
    """Use only the toolchain shipped in the current portable AppImage."""
    folder = Path(os.environ.get("SAVAGEWIND_BUILDER_ROOT", "/nonexistent"))
    runner = Path(os.environ.get("SAVAGEWIND_BWRAP", "/nonexistent"))
    rootfs = folder / "rootfs"
    seed = folder / "seed"
    if not (rootfs / "bin/bash").exists() or not (rootfs / "usr/bin/git").exists():
        raise InstallError("This AppImage does not contain the embedded source-build toolchain. "
                           "Download the full portable AppImage preview, not the old lightweight build.")
    if not (seed / "build/zig/zig").is_file() or not (seed / "build/linux/x86_64/deps").is_dir():
        raise InstallError("The embedded compiler cache is incomplete; use a verified AppImage.")
    if not runner.is_file() or runner.is_symlink() or not os.access(runner, os.X_OK):
        raise InstallError("The embedded Linux build runner is missing or is not executable.")
    return rootfs, seed, runner


def sandbox_command(rootfs, runner, source, args, network=False):
    """Bind only a disposable checkout; never mount saves or game data."""
    command = [str(runner), "--die-with-parent", "--unshare-user", "--unshare-pid",
               "--unshare-ipc", "--unshare-uts",
               "--ro-bind", str(rootfs), "/", "--bind", str(source), "/src",
               "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
               "--chdir", "/src", "--setenv", "HOME", "/tmp",
               "--setenv", "GIT_TERMINAL_PROMPT", "0",
               "--setenv", "GIT_CONFIG_NOSYSTEM", "1",
               "--setenv", "GIT_CONFIG_GLOBAL", "/dev/null",
               "--setenv", "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"]
    if network:
        resolver = Path("/etc/resolv.conf").resolve()
        if resolver.is_file():
            command.extend(["--ro-bind", str(resolver), "/etc/resolv.conf"])
    else:
        # Build scripts, including untrusted PR code, cannot access the network.
        command.append("--unshare-net")
    command.extend(["--", *args])
    return command


def _host_env():
    """Strip paths inherited from the launcher AppImage before invoking bwrap."""
    environment = dict(os.environ)
    for name in ("LD_LIBRARY_PATH", "LD_PRELOAD", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
                 "PYTHONPATH", "PYTHONHOME", "APPIMAGE", "APPDIR", "OWD", "LD_AUDIT"):
        environment.pop(name, None)
    return environment


def _run(argv, cwd, emit, cancel, logfile):
    check_cancel(cancel)
    # Show the sandboxed command without leaking the host's temporary paths.
    emit("stage", "Executing source-build task...", None)
    try:
        process = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True,
                                   stdin=subprocess.DEVNULL, bufsize=0, env=_host_env())
    except OSError as error:
        raise InstallError(f"Could not start embedded Linux builder: {error}") from error
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
            raise InstallError(
                f"Embedded build command failed (exit {returncode}). See the source-build log. "
                "On hardened systems, unprivileged user namespaces may be disabled.")
    except BaseException:
        if process.poll() is None:
            stop_process(process)
        raise
    finally:
        process.stdout.close()


def _capture(command, source):
    process = subprocess.run(command, cwd=source, env=_host_env(),
                             capture_output=True, text=True, timeout=30, check=True)
    return process.stdout.strip()


def seed_sources(seed, source, cancel):
    """Copy prebuilt, version-pinned dependencies; no host packages or online pip."""
    for suffix in ("linux/src", "linux/x86_64/deps", "linux/x86_64/gen-venv", "zig"):
        check_cancel(cancel)
        item = seed / "build" / suffix
        if not item.is_dir() or item.is_symlink():
            raise InstallError(f"Missing bundled build component: {suffix}")
        destination = source / "build" / suffix
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(item, destination, symlinks=True, dirs_exist_ok=True)
    check_cancel(cancel)


def build_pr_appimage(root, prs, emit, cancel):
    """Build selected PRs on top of upstream test."""
    return build_upstream_appimage(root, emit, cancel, prs=validated(prs))


def build_test_appimage(root, emit, cancel):
    """Build upstream test without applying any PR."""
    return build_upstream_appimage(root, emit, cancel)


def build_upstream_appimage(root, emit, cancel, prs=None):
    """Use the embedded toolchain and offline sandbox for source compilation."""
    root = Path(root).resolve()
    mode = "test" if prs is None else "pr"
    if prs is not None:
        prs = validated(prs)
    rootfs, seed, runner = bundled_builder()
    work = root / ".sw-wrapper"
    if work.is_symlink():
        raise InstallError("Installer working directory cannot be a symbolic link.")
    work.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f"{mode}-build-", dir=work))
    log = work / f"{mode}-build.log"
    try:
        with log.open("w", encoding="utf-8") as output:
            source = stage / "source"
            source.mkdir()
            def sandbox(argv, network=False):
                return sandbox_command(rootfs, runner, source, argv, network=network)
            emit("reveal", "Building upstream test AppImage..." if prs is None
                 else "Building a custom AppImage from selected PRs...", None)
            emit("stage", "Downloading upstream source using the bundled Git client...", None)
            _run(sandbox(["git", "clone", "--no-tags", "--single-branch", "--branch", "test",
                          UPSTREAM, "."], network=True), stage, emit, cancel, output)
            for pr in prs or []:
                check_cancel(cancel)
                number = pr["number"]
                emit("stage", f"Applying PR #{number}...", None)
                _run(sandbox(["git", "fetch", "origin", f"refs/pull/{number}/head"],
                             network=True), stage, emit, cancel, output)
                actual = _capture(sandbox(["git", "rev-parse", "FETCH_HEAD"]), stage)
                if actual != pr["sha"]:
                    raise InstallError(f"PR #{number} changed during setup. Refresh the PR list.")
                _run(sandbox(["git", "-c", "user.name=Savage Wind Portable",
                              "-c", "user.email=portable@localhost", "merge", "--no-edit",
                              "--no-ff", actual]), stage, emit, cancel, output)
            commit = _capture(sandbox(["git", "rev-parse", "HEAD"]), stage)
            if not SHA.fullmatch(commit):
                raise InstallError("Git returned an invalid source commit ID.")
            emit("log", f"Source revision: {commit}", None)
            emit("stage", "Preparing bundled compiler and prebuilt libraries...", None)
            seed_sources(seed, source, cancel)
            # No download or execution outside our bounded, network-isolated build process.
            emit("stage", "Building with embedded Clang, Python and AppImage tools...", None)
            _run(sandbox(["bash", "tools/linux/gen_helper.sh", "x86_64"]), stage, emit, cancel, output)
            _run(sandbox(["bash", "tools/linux/package.sh", "x86_64"]), stage, emit, cancel, output)
            results = list((source / "build/dist").glob("Savage_Wind-*-x86_64.AppImage"))
            if len(results) != 1 or results[0].is_symlink() or results[0].stat().st_size < 1024 * 1024:
                raise InstallError("The build did not produce exactly one x86_64 AppImage.")
            with results[0].open("rb") as stream:
                if stream.read(4) != b"\x7fELF":
                    raise InstallError("The generated AppImage is not an ELF executable.")
            check_cancel(cancel)
            dest = root / ("SavageWind-test.AppImage" if prs is None else "SavageWind-custom.AppImage")
            if dest.is_symlink():
                raise InstallError("The build output destination is a symbolic link.")
            fd, temp = tempfile.mkstemp(prefix=".source-image-", dir=root)
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
            emit("progress", "Upstream test AppImage built successfully." if prs is None
                 else "Custom AppImage built successfully.", 100)
            return {"path": dest, "digest": digest, "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns, "commit": commit,
                    "selection": f"test:{commit}" if prs is None else selection_key(prs)}
    finally:
        # Keep saves, existing AppImages and the trusted installed builder untouched.
        shutil.rmtree(stage)
