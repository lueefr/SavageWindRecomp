"""Distro detection and explicit-permission dependency installer tests."""
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from wrapper import deps
from wrapper.core import Cancelled, InstallError


def available(*names):
    return lambda name: f"/usr/bin/{name}" if name in names else None


class Dependencies(unittest.TestCase):
    def test_os_release_arch_and_id_like(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "os-release"
            path.write_text('NAME=Test\nID=cachyos\nPRETTY_NAME="CachyOS Linux"\n')
            self.assertEqual(deps.system_info(path)["family"], "arch")
            path.write_text('ID=unknown\nID_LIKE="ubuntu debian"\nPRETTY_NAME="A derivative"\n')
            self.assertEqual(deps.system_info(path)["family"], "debian")

    def test_arch_prefers_yay_without_sudo(self):
        plan = deps.install_plan(
            {"family": "arch", "name": "Arch Linux"},
            which=available("yay", "pacman", "sudo"), geteuid=lambda: 1000)
        self.assertEqual(plan["manager"], "yay")
        self.assertEqual(plan["commands"], [["yay", "-S", "--needed", "git", "podman"]])
        self.assertNotIn("sudo", deps.command_preview(plan))

    def test_arch_falls_back_to_pacman(self):
        plan = deps.install_plan(
            {"family": "arch", "name": "Arch Linux"},
            which=available("pacman", "sudo"), geteuid=lambda: 1000)
        self.assertEqual(plan["commands"], [["sudo", "pacman", "-S", "--needed",
                                               "git", "podman"]])

    def test_ubuntu_installs_rootless_helpers(self):
        plan = deps.install_plan(
            {"family": "debian", "name": "Ubuntu"},
            which=available("git", "apt-get", "pkexec"), geteuid=lambda: 1000)
        self.assertEqual(plan["packages"], ["podman", "uidmap", "slirp4netns", "fuse-overlayfs"])
        self.assertEqual(plan["commands"][0], ["pkexec", "apt-get", "update"])
        self.assertEqual(plan["commands"][1][:4], ["pkexec", "apt-get", "install", "-y"])

    def test_fedora_and_opensuse(self):
        for family, manager in (("fedora", "dnf"), ("suse", "zypper")):
            with self.subTest(family=family):
                plan = deps.install_plan(
                    {"family": family, "name": family},
                    which=available(manager, "sudo"), geteuid=lambda: 1000)
                self.assertEqual(plan["manager"], manager)
                self.assertIn("git", plan["packages"])
                self.assertIn("podman", plan["packages"])

    def test_no_install_when_tools_exist(self):
        with mock.patch.object(deps, "install_plan", return_value=None):
            ask = mock.Mock()
            deps.ensure_tools(Path.home(), lambda *_: None, ask, threading.Event())
            ask.assert_not_called()

    def test_unsupported_distro_does_not_execute_packages(self):
        with self.assertRaisesRegex(InstallError, "not supported"):
            deps.install_plan({"family": None, "name": "Unknown"},
                              which=available(), geteuid=lambda: 1000)

    def test_user_must_explicitly_approve(self):
        plan = {"distro": "Arch Linux", "manager": "yay",
                "packages": ["git", "podman"],
                "commands": [["yay", "-S", "--needed", "git", "podman"]]}
        with mock.patch.object(deps, "install_plan", return_value=plan):
            with mock.patch.object(deps.subprocess, "Popen") as process:
                asked = []
                def deny(message):
                    asked.append(message)
                    return False
                with self.assertRaises(Cancelled):
                    deps.ensure_tools(Path.home(), lambda *_: None, deny, threading.Event())
                process.assert_not_called()
                self.assertIn("Arch Linux", asked[0])
                self.assertIn("yay -S --needed git podman", asked[0])

    def test_approved_install_runs_terminal_and_resumes(self):
        import shlex
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = {"distro": "Arch Linux", "manager": "yay",
                    "packages": ["git", "podman"],
                    "commands": [["yay", "-S", "--needed", "git", "podman"]]}
            commands = []
            def choose_terminal(script):
                commands.append(script)
                return ["fake-terminal", script]
            def fake_process(argv, **kwargs):
                import re
                match = re.search(r'printf .*\$result" > (.*)', argv[-1])
                self.assertIsNotNone(match)
                path = Path(shlex.split(match.group(1))[0])
                path.write_text("0\n", encoding="utf-8")
                return mock.Mock(poll=lambda: None)
            with mock.patch.object(deps, "install_plan", return_value=plan):
                with mock.patch.object(deps, "terminal_command", side_effect=choose_terminal):
                    with mock.patch.object(deps.subprocess, "Popen", side_effect=fake_process):
                        with mock.patch.object(deps, "missing_tools", side_effect=[["git", "podman"], []]):
                            messages = []
                            deps.ensure_tools(root, lambda *args: messages.append(args),
                                              lambda _: True, threading.Event())
            self.assertEqual(len(commands), 1)
            self.assertIn("yay -S --needed git podman", commands[0])
            self.assertTrue(any("installed successfully" in msg[1] for msg in messages))

    def test_package_failure_stops_before_source_build(self):
        import shlex
        with tempfile.TemporaryDirectory() as folder:
            plan = {"distro": "Arch Linux", "manager": "yay",
                    "packages": ["git", "podman"],
                    "commands": [["yay", "-S", "--needed", "git", "podman"]]}
            def fake_process(argv, **kwargs):
                import re
                match = re.search(r'printf .*\$result" > (.*)', argv[-1])
                Path(shlex.split(match.group(1))[0]).write_text("1\n")
                return mock.Mock(poll=lambda: None)
            with mock.patch.object(deps, "install_plan", return_value=plan):
                with mock.patch.object(deps, "terminal_command",
                                       side_effect=lambda script: ["console", script]):
                    with mock.patch.object(deps.subprocess, "Popen", side_effect=fake_process):
                        with mock.patch.object(deps, "missing_tools", return_value=["git", "podman"]):
                            with self.assertRaisesRegex(InstallError, "package manager failed"):
                                deps.ensure_tools(folder, lambda *_: None, lambda _: True,
                                                  threading.Event())

    def test_terminal_for_arch_kde(self):
        cmd = deps.terminal_command("echo success", which=available("konsole"))
        self.assertEqual(cmd[:3], ["/usr/bin/konsole", "--noclose", "-e"])
        self.assertEqual(cmd[-1], "echo success")

    def test_no_terminal_is_not_silent(self):
        self.assertIsNone(deps.terminal_command("echo hi", which=available()))

    def test_cleans_appimage_library_overrides(self):
        with mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/tmp/appimage",
                                          "QT_PLUGIN_PATH": "/tmp/plugin",
                                          "HOME": "/home/user"}):
            cleaned = deps.host_environment()
        self.assertNotIn("LD_LIBRARY_PATH", cleaned)
        self.assertNotIn("QT_PLUGIN_PATH", cleaned)
        self.assertEqual(cleaned["HOME"], "/home/user")


if __name__ == "__main__":
    unittest.main()
