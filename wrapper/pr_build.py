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

from .deps import ensure_tools, host_environment

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


def _run(argv, cwd, emit, cancel, logfile):
    """Stream build output without executing build scripts on the host."""
    check_cancel(cancel)
    emit("stage", "Running: " + " ".join(argv[:4]) + "...", None)
    try:
        process = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True,
                                   stdin=subprocess.DEVNULL, bufsize=0,
                                   env=host_environment())
    except OSError as error:
        raise InstallError(f"Could not start build tool {argv[0]}: {error}") from error
    pending = bytearray()
    recent = []
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
                        recent.append(line)
                        if len(recent) > 16:
                            recent.pop(0)
                        emit("log", line, None)
        if pending:
            logfile.write(pending.decode("utf-8", "replace")[:1200] + "\n")
        status = process.wait()
        if status:
            detail = next((item for item in reversed(recent)
                           if "error" in item.lower() or "setup network:" in item.lower()
                           or item.lstrip().startswith("E:")),
                          recent[-1] if recent else "No output captured.")
            raise InstallError(f"{argv[0]} exited with code {status}: {detail[:280]}. "
                               "See source-build log for details.")
    except BaseException:
        if process.poll() is None:
            stop_process(process)
        raise
    finally:
        process.stdout.close()


def _capture(argv, cwd):
    check = subprocess.run(argv, cwd=cwd, env=host_environment(),
                           capture_output=True, text=True, timeout=30)
    if check.returncode:
        raise InstallError(f"Git failed: {check.stderr.strip()[:500]}")
    return check.stdout.strip()


def _podman_ready():
    """Report rootless Podman failures before launching a lengthy build."""
    try:
        result = subprocess.run(["podman", "info", "--format", "json"],
                                env=host_environment(), capture_output=True,
                                text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InstallError(f"Cannot initialize rootless Podman: {error}") from error
    if result.returncode:
        raise InstallError("Rootless Podman could not start. Check user namespaces and "
                           "/etc/subuid and /etc/subgid. " + result.stderr.strip()[-700:])
    try:
        info = json.loads(result.stdout)
    except ValueError as error:
        raise InstallError("Podman returned invalid runtime information.") from error
    security = (info.get("host") or {}).get("security") or {}
    if not security.get("rootless"):
        raise InstallError("Local source builds require rootless Podman. "
                           "Please run the launcher as your regular user.")


def _toolchain_recipe(path):
    """Extract one shell RUN instruction without executing Dockerfile text on the host."""
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise InstallError(f"Cannot read upstream toolchain recipe: {error}") from error
    instructions, run_parts = [], []
    for original in content.splitlines():
        line = original.strip()
        if not line or line.startswith("#"):
            continue
        if run_parts and not run_parts[-1].endswith("\\"):
            raise InstallError("Unsupported extra instructions in upstream Containerfile.")
        if line.startswith(("FROM ", "ARG ", "RUN ")) and (
                not run_parts or not run_parts[-1].endswith("\\")):
            instructions.append(line.split(maxsplit=1)[0])
        if instructions and instructions[-1] == "RUN":
            if line.startswith("RUN "):
                line = line[4:]
            run_parts.append(line)
    if (instructions != ["FROM", "ARG", "RUN"] or
            not re.search(r"(?m)^FROM docker\.io/library/ubuntu:22\.04\s*$", content) or
            not re.search(r"(?m)^ARG DEBIAN_FRONTEND=noninteractive\s*$", content) or
            not run_parts):
        raise InstallError("Unsupported upstream Containerfile. Expected Ubuntu 22.04 with one RUN command.")
    parts = [part[:-1].rstrip() if part.endswith("\\") else part for part in run_parts]
    recipe = " ".join(parts).strip()
    if not recipe.startswith("apt-get update && apt-get install "):
        raise InstallError("The upstream toolchain recipe has changed unexpectedly.")
    return recipe


def _image_exists(image):
    try:
        result = subprocess.run(["podman", "image", "exists", image],
                                env=host_environment(), stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InstallError(f"Cannot check the local Podman toolchain cache: {error}") from error
    return result.returncode == 0


def _prepare_toolchain(source, stage, emit, cancel, logfile):
    """Install dependencies using rootless podman run; Buildah build rejects slirp4netns."""
    recipe = _toolchain_recipe(source / "tools/linux/Containerfile")
    digest = hashlib.sha256(recipe.encode("utf-8")).hexdigest()[:20]
    image = f"localhost/savagewind-toolchain:recipe-{digest}"
    if _image_exists(image):
        emit("log", f"Using cached local compiler image {image}", None)
        return image
    container = f"sw-toolchain-{stage.name}"
    emit("stage", "Preparing Linux compiler with rootless Podman...", None)
    emit("log", "Using podman run with slirp4netns; podman build cannot use this network in rootless mode.", None)
    try:
        _run(["podman", "run", "--name", container, "--arch", "amd64",
              "--network=slirp4netns", "-e", "DEBIAN_FRONTEND=noninteractive",
              "docker.io/library/ubuntu:22.04", "/bin/sh", "-ec", recipe],
             stage, emit, cancel, logfile)
        check_cancel(cancel)
        _run(["podman", "commit", container, image], stage, emit, cancel, logfile)
    finally:
        # A failed download or cancellation must not leave an orphan container behind.
        try:
            subprocess.run(["podman", "rm", "--force", container], env=host_environment(),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            emit("log", f"Could not clean temporary Podman container {container}.", None)
    return image


def build_pr_appimage(root, prs, emit, cancel, ask=None):
    """Compile upstream test with selected pinned PRs in rootless Podman."""
    return build_upstream_appimage(root, emit, cancel, ask=ask, prs=validated(prs))


def build_test_appimage(root, emit, cancel, ask=None):
    """Compile upstream test without merging any PRs."""
    return build_upstream_appimage(root, emit, cancel, ask=ask)


def build_upstream_appimage(root, emit, cancel, ask=None, prs=None):
    """Install missing host build tools only with consent; keep saves untouched."""
    root = Path(root).resolve()
    mode = "test" if prs is None else "pr"
    if prs is not None:
        prs = validated(prs)
    if ask is None:
        # Unit tests and programmatic callers must not silently install packages.
        def ask(_):
            return False
    ensure_tools(root, emit, ask, cancel)
    _podman_ready()
    work = root / ".sw-wrapper"
    if work.is_symlink():
        raise InstallError("Installer working directory cannot be a symlink.")
    work.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f"{mode}-build-", dir=work))
    log = work / f"{mode}-build.log"
    try:
        with log.open("w", encoding="utf-8") as output:
            source = stage / "source"
            emit("reveal", "Building upstream test AppImage..." if prs is None
                 else "Building a custom AppImage from selected PRs...", None)
            emit("stage", "Downloading upstream source...", None)
            _run(["git", "clone", "--no-tags", "--single-branch", "--branch", "test",
                  UPSTREAM, str(source)], stage, emit, cancel, output)
            for pr in prs or []:
                check_cancel(cancel)
                number = pr["number"]
                emit("stage", f"Applying PR #{number}...", None)
                _run(["git", "-C", str(source), "fetch", "origin",
                      f"refs/pull/{number}/head"], stage, emit, cancel, output)
                actual = _capture(["git", "rev-parse", "FETCH_HEAD"], source)
                if actual != pr["sha"]:
                    raise InstallError(f"PR #{number} changed. Refresh the PR list before building.")
                _run(["git", "-c", "user.name=Savage Wind Portable",
                      "-c", "user.email=portable@localhost", "-c",
                      "core.hooksPath=/dev/null", "merge", "--no-edit",
                      "--no-ff", actual], source, emit, cancel, output)
            commit = _capture(["git", "rev-parse", "HEAD"], source)
            if not SHA.fullmatch(commit):
                raise InstallError("Git returned an invalid source revision.")
            emit("log", f"Source revision: {commit}", None)
            image = _prepare_toolchain(source, stage, emit, cancel, output)
            run = ["podman", "run", "--rm", "--network=slirp4netns", "--arch", "amd64",
                   "--volume", f"{source}:/src:Z", "--workdir", "/src", image]
            emit("stage", "Generating the Linux build in Podman...", None)
            _run(run + ["bash", "tools/linux/gen_helper.sh", "x86_64"],
                 source, emit, cancel, output)
            emit("stage", "Packaging the custom AppImage in Podman...", None)
            _run(run + ["bash", "tools/linux/package.sh", "x86_64"],
                 source, emit, cancel, output)
            results = list((source / "build/dist").glob("Savage_Wind-*-x86_64.AppImage"))
            if len(results) != 1 or results[0].is_symlink() or results[0].stat().st_size < 1024 * 1024:
                raise InstallError("The build did not produce exactly one x86_64 AppImage.")
            with results[0].open("rb") as stream:
                if stream.read(4) != b"\x7fELF":
                    raise InstallError("The produced AppImage is not an ELF executable.")
            check_cancel(cancel)
            dest = root / ("SavageWind-test.AppImage" if prs is None else "SavageWind-custom.AppImage")
            if dest.is_symlink():
                raise InstallError("The custom AppImage destination is a symlink.")
            fd, temp = tempfile.mkstemp(prefix=".custom-image-", dir=root)
            try:
                with os.fdopen(fd, "wb") as target, results[0].open("rb") as built:
                    shutil.copyfileobj(built, target, length=1024 * 1024)
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
            emit("progress", "Upstream test AppImage built." if prs is None
                 else "Custom AppImage built.", 100)
            return {"path": dest, "digest": digest, "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns, "commit": commit,
                    "selection": f"test:{commit}" if prs is None else selection_key(prs)}
    finally:
        # Remove only the temporary source checkout, never game files or saves.
        shutil.rmtree(stage)
