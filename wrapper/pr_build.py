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
import time
import urllib.request

from .deps import ensure_tools, ensure_tun, host_environment

from .core import Cancelled, InstallError, check_cancel, read_state, sha256, stop_process, tls_context

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


SOURCE_STAGES = ("Fetch sources", "Apply pull requests", "Prepare compiler",
                 "Compile runtime", "Build dependencies and package AppImage", "Publish AppImage")


def elapsed_text(seconds):
    total = max(0, int(seconds))
    if total >= 3600:
        return f"{total // 3600}h {total // 60 % 60:02d}min"
    if total >= 60:
        return f"{total // 60}min {total % 60:02d}s"
    return f"{total}s"


class SourceTimer:
    """Measure real build stages; estimate time from recorded past runs."""
    def __init__(self, root, mode, emit, clock=time.monotonic):
        self.root, self.mode, self.emit, self.clock = Path(root), mode, emit, clock
        self.path = self.root / ".sw-wrapper/source-times.json"
        self.started = clock()
        self.current = -1
        self.phase_started = self.started
        self.durations = {}
        self.last_tick = 0.0
        self.activity = None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            previous = data.get(mode, {})
            self.history = {int(k): float(v) for k, v in previous.items()
                            if str(k).isdigit() and 0 <= int(k) < len(SOURCE_STAGES)
                            and isinstance(v, (int, float)) and 0 < v < 86400}
        except (OSError, ValueError, TypeError, AttributeError):
            self.history = {}

    def step(self, index):
        now = self.clock()
        if self.current >= 0:
            self.durations[self.current] = max(0.001, now - self.phase_started)
        self.current = index
        self.phase_started = now
        self.emit("source-step", SOURCE_STAGES[index], index)
        self.tick(force=True)

    def set_activity(self, description):
        """Report an observed packaging substep without inventing a percentage."""
        self.activity = description

    def tick(self, force=False):
        if self.current < 0:
            return
        now = self.clock()
        if not force and now - self.last_tick < 1.0:
            return
        self.last_tick = now
        spent = now - self.started
        elapsed_stage = now - self.phase_started
        pending = range(self.current, len(SOURCE_STAGES))
        known = all(index in self.history for index in pending)
        if known:
            remaining = max(0, self.history[self.current] - elapsed_stage)
            remaining += sum(self.history[i] for i in range(self.current + 1, len(SOURCE_STAGES)))
            eta = f"ETA estimado: ~{elapsed_text(remaining)} (execuções anteriores)"
        elif len(self.durations) >= 2:
            # A preliminary, explicitly approximate ETA from completed stages only.
            mean = sum(self.durations.values()) / len(self.durations)
            remaining = max(0, mean - elapsed_stage) + mean * (len(SOURCE_STAGES) - self.current - 1)
            eta = f"ETA preliminar: ~{elapsed_text(remaining)} (pode variar)"
        else:
            eta = "ETA: calculando..."
        details = f"Decorrido: {elapsed_text(spent)} · {eta}"
        if self.current == 4 and self.activity:
            details += "\n" + self.activity
        self.emit("source-eta", details, None)

    def finish(self):
        if self.current >= 0:
            self.durations[self.current] = max(0.001, self.clock() - self.phase_started)
        if self.path.is_symlink():
            raise InstallError("Cannot write build timings through a symbolic link.")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        existing = data.get(self.mode, {})
        if not isinstance(existing, dict):
            existing = {}
        for idx, duration in self.durations.items():
            old = existing.get(str(idx))
            if isinstance(old, (int, float)) and 0 < old < 86400:
                duration = 0.65 * float(old) + 0.35 * duration
            existing[str(idx)] = round(duration, 1)
        data[self.mode] = existing
        fd, temp = tempfile.mkstemp(prefix=".source-times-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(data, out)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        self.emit("source-eta", f"Concluído em {elapsed_text(self.clock() - self.started)} · ETA: 0s", None)


def _package_detail(line):
    """Recognize observable upstream activity, not guessed completion percentages."""
    lower = line.lower()
    if "appimagetool" in lower or "creating filesystem" in lower or "mksquashfs" in lower:
        return "Creating the AppImage filesystem"
    if "shaderc" in lower or "glslang" in lower or "spirv-tools" in lower:
        return "Building shaderc and SPIR-V dependencies"
    if "ffmpeg" in lower or "libavcodec" in lower:
        return "Building FFmpeg dependencies"
    if "sdl3" in lower or "libsdl" in lower:
        return "Building SDL3 dependencies"
    if "fetch_zig" in lower or "zig-linux" in lower:
        return "Fetching the Zig toolchain"
    if "savagewind" in lower and ("clang" in lower or "make " in lower):
        return "Compiling the Savage Wind executable"
    if "ninja" in lower or "cmake" in lower or "building" in lower:
        return "Compiling native dependencies"
    return None


def _process_states(pid):
    """Small, bounded /proc snapshot; permissions may hide container processes."""
    pending, seen, counts = [pid], set(), {}
    while pending and len(seen) < 96:
        child = pending.pop()
        if child in seen:
            continue
        seen.add(child)
        try:
            stat = Path(f"/proc/{child}/stat").read_text()
            state = stat.rsplit(")", 1)[-1].split()[0]
            counts[state] = counts.get(state, 0) + 1
            direct = Path(f"/proc/{child}/task/{child}/children").read_text()
            pending.extend(int(value) for value in direct.split())
        except (OSError, ValueError, IndexError):
            continue
    return ", ".join(f"{key}:{value}" for key, value in sorted(counts.items())) or "unavailable"


def _run(argv, cwd, emit, cancel, logfile, meter=None):
    """Stream build output without executing build scripts on the host."""
    check_cancel(cancel)
    pause = getattr(cancel, "pause", None)
    if pause is not None:
        pause.wait(cancel)
    emit("stage", "Running: " + " ".join(argv[:4]) + "...", None)
    try:
        process = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True,
                                   stdin=subprocess.DEVNULL, bufsize=0,
                                   env=host_environment())
    except OSError as error:
        raise InstallError(f"Could not start build tool {argv[0]}: {error}") from error
    if pause is not None:
        try:
            pause.attach(process)
        except OSError:
            stop_process(process)
            raise
    pending = bytearray()
    recent = []
    clock = pause.clock if pause is not None else time.monotonic
    last_output = clock()
    last_notice = last_output
    packaging = meter is not None and meter.current == 4
    if packaging:
        meter.set_activity("Preparing dependencies and packaging the AppImage")
    try:
        with selectors.DefaultSelector() as sel:
            sel.register(process.stdout, selectors.EVENT_READ)
            while sel.get_map():
                check_cancel(cancel)
                if pause is not None:
                    pause.wait(cancel)
                if meter is not None:
                    meter.tick()
                for key, _ in sel.select(timeout=0.3):
                    data = os.read(key.fd, 16384)
                    if data:
                        last_output = clock()
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
                        if packaging:
                            detail = _package_detail(line)
                            if detail and detail != meter.activity:
                                meter.set_activity(detail)
                                meter.tick(force=True)
                        emit("log", line, None)
                now = clock()
                silent = now - last_output
                if packaging and silent >= 30 and now - last_notice >= 30:
                    state = _process_states(process.pid)
                    message = (f"Packaging still running: no new output for "
                               f"{elapsed_text(silent)}; process {process.pid}; "
                               f"states {state}. This is not a completion percentage.")
                    emit("log", message, None)
                    logfile.write("[wrapper] " + message + "\n")
                    logfile.flush()
                    meter.set_activity(f"No new output for {elapsed_text(silent)} · "
                                       f"Process states: {state}")
                    meter.tick(force=True)
                    last_notice = now
                if process.poll() is not None and silent > 5 and sel.get_map():
                    # A detached compiler may inherit stdout after Podman exits.
                    # Do not wait forever for a pipe held open by another process.
                    message = ("Build command exited, but its output pipe remained "
                               "open. Checking the command exit status.")
                    emit("log", message, None)
                    logfile.write("[wrapper] " + message + "\n")
                    break
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
        if pause is not None:
            pause.resume()
        if process.poll() is None:
            stop_process(process)
        raise
    finally:
        if pause is not None:
            pause.detach(process)
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


def _prepare_toolchain(source, stage, emit, cancel, logfile, meter=None):
    """Install dependencies using rootless podman run and pasta networking."""
    recipe = _toolchain_recipe(source / "tools/linux/Containerfile")
    digest = hashlib.sha256(recipe.encode("utf-8")).hexdigest()[:20]
    image = f"localhost/savagewind-toolchain:recipe-{digest}"
    if _image_exists(image):
        emit("log", f"Using cached local compiler image {image}", None)
        return image
    container = f"sw-toolchain-{stage.name}"
    emit("stage", "Preparing Linux compiler with rootless Podman...", None)
    emit("log", "Using podman run with pasta; rootless Podman needs /dev/net/tun.", None)
    try:
        _run(["podman", "run", "--name", container, "--arch", "amd64",
              "--network=pasta", "-e", "DEBIAN_FRONTEND=noninteractive",
              "docker.io/library/ubuntu:22.04", "/bin/sh", "-ec", recipe],
             stage, emit, cancel, logfile, meter=meter)
        check_cancel(cancel)
        _run(["podman", "commit", container, image], stage, emit, cancel, logfile, meter=meter)
    finally:
        # A failed download or cancellation must not leave an orphan container behind.
        try:
            subprocess.run(["podman", "rm", "--force", container], env=host_environment(),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            emit("log", f"Could not clean temporary Podman container {container}.", None)
    return image


def _cleanup_stage(stage, emit):
    """Remove only our disposable checkout without masking a successful build."""
    try:
        shutil.rmtree(stage)
        return
    except OSError as error:
        emit("log", f"Temporary checkout cleanup needs namespace permissions: {error}", None)
    # Old rootless containers may leave subordinate-UID-owned files. Recover only
    # the freshly created, random stage folder; never touch root/, game/, or saves.
    try:
        result = subprocess.run(
            ["podman", "unshare", "rm", "-rf", "--", str(stage)],
            env=host_environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=90, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        emit("log", f"Temporary checkout was retained: {error}", None)
        return
    if result.returncode or stage.exists():
        detail = result.stderr.strip()[-350:] or "cleanup did not remove the directory"
        emit("log", f"Temporary checkout was retained: {detail}", None)
    else:
        emit("log", "Temporary checkout cleaned through rootless Podman.", None)


def _recover_test_build(root, emit, ask, cancel):
    """Offer to adopt a completed AppImage left by an interrupted cleanup."""
    image = root / "SavageWind-test.AppImage"
    log = root / ".sw-wrapper/test-build.log"
    if (image.is_symlink() or log.is_symlink() or
            not image.is_file() or not log.is_file()):
        return None
    state = read_state(root)
    if test_runtime(root, state):
        return None
    try:
        stat = image.stat()
        log_stat = log.stat()
        if stat.st_size < 1024 * 1024 or stat.st_mtime_ns < log_stat.st_mtime_ns:
            return None
        with image.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                return None
        with log.open("rb") as stream:
            head = stream.read(256 * 1024)
            stream.seek(max(0, log_stat.st_size - 256 * 1024))
            tail = stream.read()
    except OSError:
        return None
    revisions = re.findall(rb"(?m)^Source revision: ([0-9a-f]{40})\s*$", head)
    if not re.search(rb"(?m)^Success\s*$", tail):
        return None
    revision = revisions[-1].decode("ascii") if revisions else None
    label = revision[:12] if revision else "not recorded (previous launcher)"
    if not ask("An AppImage from the previous test-branch build appears complete, "
               f"but the installer failed during temporary file cleanup.\n\n"
               f"Source revision: {label}\n"
               f"File: {image}\n\n"
               "Its executable format and build completion log were checked, but "
               "an upstream checksum is unavailable. Reuse this local AppImage? "
               "Choose No to fetch and build the latest upstream test branch."):
        return None
    digest = sha256(image, cancel)
    commit = revision or f"unverified-local-{digest[:16]}"
    emit("log", "Recovered a previously packaged test AppImage. "
         + (f"Revision: {revision}" if revision else
            "The old build log did not record the full source revision."), None)
    return {"path": image, "digest": digest, "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns, "commit": commit,
            "selection": f"test:{commit}"}


def build_pr_appimage(root, prs, emit, cancel, ask=None):
    """Compile upstream test with selected pinned PRs in rootless Podman."""
    return build_upstream_appimage(root, emit, cancel, ask=ask, prs=validated(prs))


def upstream_test_head():
    """Read the latest upstream test SHA without fetching or compiling sources."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--heads", UPSTREAM, "test"],
            env=host_environment(), capture_output=True, text=True,
            timeout=25, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InstallError(f"Cannot check upstream test revision: {error}") from error
    if result.returncode:
        raise InstallError("Cannot check upstream test revision: " +
                           result.stderr.strip()[-450:])
    parts = result.stdout.strip().split()
    if len(parts) != 2 or parts[1] != "refs/heads/test" or not SHA.fullmatch(parts[0]):
        raise InstallError("Upstream test revision could not be verified.")
    return parts[0]


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
    if prs is None:
        recovered = _recover_test_build(root, emit, ask, cancel)
        if recovered is not None:
            return recovered
    ensure_tools(root, emit, ask, cancel)
    _podman_ready()
    # Podman 6 has removed slirp4netns; pasta requires a working host TUN device.
    ensure_tun(root, emit, ask, cancel)
    work = root / ".sw-wrapper"
    if work.is_symlink():
        raise InstallError("Installer working directory cannot be a symlink.")
    work.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f"{mode}-build-", dir=work))
    log = work / f"{mode}-build.log"
    pause = getattr(cancel, "pause", None)
    tracker = SourceTimer(root, mode, emit,
                          clock=pause.clock if pause is not None else time.monotonic)
    try:
        with log.open("w", encoding="utf-8") as output:
            source = stage / "source"
            emit("reveal", "Building upstream test AppImage..." if prs is None
                 else "Building a custom AppImage from selected PRs...", None)
            tracker.step(0)
            _run(["git", "clone", "--progress", "--no-tags", "--single-branch", "--branch", "test",
                  UPSTREAM, str(source)], stage, emit, cancel, output, meter=tracker)
            tracker.step(1)
            base_commit = _capture(["git", "rev-parse", "HEAD"], source)
            for pr in prs or []:
                check_cancel(cancel)
                number = pr["number"]
                emit("stage", f"Applying PR #{number}...", None)
                _run(["git", "-C", str(source), "fetch", "origin",
                      f"refs/pull/{number}/head"], stage, emit, cancel, output, meter=tracker)
                actual = _capture(["git", "rev-parse", "FETCH_HEAD"], source)
                if actual != pr["sha"]:
                    raise InstallError(f"PR #{number} changed. Refresh the PR list before building.")
                _run(["git", "-c", "user.name=Savage Wind Portable",
                      "-c", "user.email=portable@localhost", "-c",
                      "core.hooksPath=/dev/null", "merge", "--no-edit",
                      "--no-ff", actual], source, emit, cancel, output, meter=tracker)
            commit = _capture(["git", "rev-parse", "HEAD"], source)
            if not SHA.fullmatch(commit):
                raise InstallError("Git returned an invalid source revision.")
            output.write(f"Source revision: {commit}\n")
            output.flush()
            emit("log", f"Source revision: {commit}", None)
            tracker.step(2)
            image = _prepare_toolchain(source, stage, emit, cancel, output, meter=tracker)
            # Run as the host UID/GID so generated Zig files remain manageable.
            # This applies only to builds with the user's disposable checkout.
            # The earlier apt-get toolchain container still runs as container root.
            run = ["podman", "run", "--rm", "--network=pasta",
                   "--userns=keep-id", "--env", "HOME=/tmp",
                   "--arch", "amd64", "--volume", f"{source}:/src:Z",
                   "--workdir", "/src", image]
            tracker.step(3)
            _run(run + ["bash", "tools/linux/gen_helper.sh", "x86_64"],
                 source, emit, cancel, output, meter=tracker)
            tracker.step(4)
            _run(run + ["bash", "tools/linux/package.sh", "x86_64"],
                 source, emit, cancel, output, meter=tracker)
            tracker.step(5)
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
            tracker.finish()
            emit("progress", "Upstream test AppImage built." if prs is None
                 else "Custom AppImage built.", 100)
            return {"path": dest, "digest": digest, "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns, "commit": commit,
                    "base_commit": base_commit,
                    "selection": f"test:{commit}" if prs is None else selection_key(prs)}
    finally:
        # A cleanup failure is not a build failure; the AppImage may be complete.
        _cleanup_stage(stage, emit)
