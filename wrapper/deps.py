"""Ask before installing missing source-build tools with the host package manager.

The launcher never runs a package manager silently, and yay is never run as root.
"""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time

from .core import Cancelled, InstallError, check_cancel


TOOLS = ("git", "podman")

FAMILIES = {
    "arch": {"arch", "cachyos", "endeavouros", "manjaro", "garuda", "artix"},
    "debian": {"debian", "ubuntu", "linuxmint", "pop", "elementary", "raspbian", "kali", "neon"},
    "fedora": {"fedora", "rhel", "centos", "rocky", "almalinux", "nobara"},
    "suse": {"opensuse", "opensuse-tumbleweed", "opensuse-leap", "sles", "suse"},
}


def system_info(path="/etc/os-release"):
    """Read os-release as data, not executable shell code."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise InstallError("Cannot identify the Linux distribution (/etc/os-release).") from error
    data = {}
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key not in ("ID", "ID_LIKE", "PRETTY_NAME"):
            continue
        try:
            pieces = shlex.split(value, posix=True)
        except ValueError:
            continue
        data[key] = " ".join(pieces)
    identifiers = [data.get("ID", "").lower(), *data.get("ID_LIKE", "").lower().split()]
    family = next((family for name in identifiers for family, ids in FAMILIES.items()
                   if name in ids), None)
    return {"family": family, "name": data.get("PRETTY_NAME") or data.get("ID") or "Linux"}


def missing_tools(which=shutil.which):
    return [tool for tool in TOOLS if not which(tool)]


def install_plan(info=None, which=shutil.which, geteuid=os.geteuid):
    """Return only literal, allow-listed package commands."""
    missing = missing_tools(which)
    if not missing:
        return None
    info = info or system_info()
    family = info["family"]
    if family is None:
        raise InstallError(
            f"Automatic dependency installation is not supported on {info['name']}. "
            "Install git and podman through your distribution's package manager.")
    packages = list(missing)
    if "podman" in missing and family == "debian":
        packages += ["uidmap", "slirp4netns", "fuse-overlayfs"]
    if "podman" in missing and family == "fedora":
        packages += ["fuse-overlayfs"]
    if "podman" in missing and family == "suse":
        packages += ["fuse-overlayfs"]
    if family == "arch" and which("yay") and geteuid() != 0:
        manager = "yay"
        commands = [["yay", "-S", "--needed", *packages]]
    else:
        managers = {"arch": "pacman", "debian": "apt-get",
                    "fedora": "dnf", "suse": "zypper"}
        manager = managers[family]
        if not which(manager):
            raise InstallError(f"{manager} was not found on {info['name']}.")
        if geteuid() == 0:
            privilege = []
        elif which("sudo"):
            privilege = ["sudo"]
        elif which("pkexec"):
            privilege = ["pkexec"]
        else:
            raise InstallError(
                "Administrator authorization is unavailable (requires sudo or pkexec).")
        if family == "arch":
            commands = [[*privilege, "pacman", "-S", "--needed", *packages]]
        elif family == "debian":
            commands = [[*privilege, "apt-get", "update"],
                        [*privilege, "apt-get", "install", "-y", *packages]]
        elif family == "fedora":
            commands = [[*privilege, "dnf", "install", "-y", *packages]]
        else:
            commands = [[*privilege, "zypper", "--non-interactive", "install", *packages]]
    return {"distro": info["name"], "manager": manager,
            "packages": packages, "commands": commands}


def command_preview(plan):
    return " && ".join(shlex.join(args) for args in plan["commands"])


def terminal_command(script, which=shutil.which):
    """Select an available terminal without evaluating untrusted input."""
    terminals = (
        ("konsole", lambda p: [p, "--noclose", "-e", "bash", "-c", script]),
        ("gnome-terminal", lambda p: [p, "--", "bash", "-c", script]),
        ("xfce4-terminal", lambda p: [p, "--disable-server", "--hold", "-x", "bash", "-c", script]),
        ("kitty", lambda p: [p, "--hold", "bash", "-c", script]),
        ("alacritty", lambda p: [p, "--hold", "-e", "bash", "-c", script]),
        ("xterm", lambda p: [p, "-hold", "-e", "bash", "-c", script]),
        ("x-terminal-emulator", lambda p: [p, "-e", "bash", "-c", script]),
    )
    for name, make in terminals:
        executable = which(name)
        if executable:
            return make(executable)
    return None


def host_environment():
    """Do not let AppImage-specific library paths break host package managers."""
    environment = dict(os.environ)
    for name in ("LD_LIBRARY_PATH", "LD_PRELOAD", "LD_AUDIT", "PYTHONHOME", "PYTHONPATH",
                 "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_PLUGIN_PATH", "APPDIR", "APPIMAGE"):
        environment.pop(name, None)
    return environment


def ensure_tools(root, emit, ask, cancel):
    """After GUI consent, install missing tools in a terminal and resume automatically."""
    plan = install_plan()
    if plan is None:
        return
    preview = command_preview(plan)
    message = (
        f"Distribution: {plan['distro']}\n"
        f"Missing build tools: {', '.join(missing_tools())}\n"
        f"Packages to install: {', '.join(plan['packages'])}\n\n"
        f"The installer will open a terminal and run:\n{preview}\n\n"
        "The package manager may request your administrator password. "
        "No packages will be installed without your permission.\n\n"
        "Allow installation and continue with the AppImage build?")
    if not ask(message):
        raise Cancelled()
    check_cancel(cancel)
    metadata = Path(root) / ".sw-wrapper"
    if metadata.is_symlink():
        raise InstallError("Installer metadata directory cannot be a symlink.")
    metadata.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dependencies-", dir=metadata) as folder:
        status = Path(folder) / "exit-code"
        # Only trusted, fixed package names enter this command.
        script = (
            "printf '\\nInstalling Savage Wind build dependencies...\\n\\n'\n"
            + preview + "\n"
            + "result=$?\n"
            + f"printf '%s\\n' \"$result\" > {shlex.quote(str(status))}\n"
            + "printf '\\nPackage installation finished (exit code %s).\\n' \"$result\"\n"
            + "read -r -p 'Press Enter to close this terminal...' _ || true\n"
            + "exit \"$result\"\n")
        terminal = terminal_command(script)
        if terminal is None:
            raise InstallError(
                "No supported terminal emulator was found to request package permissions. "
                f"Install manually: {preview}")
        emit("reveal", f"Installing build dependencies for {plan['distro']}...", None)
        emit("log", "Approved package installation: " + preview, None)
        try:
            process = subprocess.Popen(terminal, start_new_session=True,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, env=host_environment())
        except OSError as error:
            raise InstallError(f"Could not open the package manager terminal: {error}") from error
        started = time.monotonic()
        while not status.is_file():
            check_cancel(cancel)
            if time.monotonic() - started > 3600:
                raise InstallError("Package installation did not finish. Check the terminal.")
            if process.poll() is not None and time.monotonic() - started > 10:
                raise InstallError("Package installer terminal closed before reporting success.")
            time.sleep(0.2)
        try:
            result = int(status.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            raise InstallError("Package installation returned an invalid result.")
        if result != 0:
            raise InstallError(f"The package manager failed (exit code {result}). Check its terminal output.")
    still_missing = missing_tools()
    if still_missing:
        raise InstallError("The following tools are still missing after installation: "
                           + ", ".join(still_missing))
    emit("stage", "Build dependencies installed successfully.", None)
