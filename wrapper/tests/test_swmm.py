"""SWMM portable integration: safe archive handling, paths and modded launches."""
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import threading
import unittest
from unittest import mock

from wrapper import core, swmm


def binary():
    return b"\x7fELF" + b"\x00" * 4096


def archive(*, executable=True, malicious=False):
    file = BytesIO()
    with tarfile.open(fileobj=file, mode="w:xz") as packed:
        content = binary() if executable else b"not an ELF"
        for name, data in (("./swmm", content), ("./swmm.png", b"PNG")):
            item = tarfile.TarInfo(name)
            item.size = len(data)
            packed.addfile(item, BytesIO(data))
        if malicious:
            item = tarfile.TarInfo("../../evil")
            item.size = 5
            packed.addfile(item, BytesIO(b"evil!"))
    return file.getvalue()


def release(data):
    return {
        "tag_name": "v0.1.3", "prerelease": False, "draft": False,
        "assets": [{
            "name": swmm.ASSET, "size": len(data),
            "browser_download_url": swmm.SITE + "/releases/download/v0.1.3/" + swmm.ASSET,
            "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
        }],
    }


class Source(BytesIO):
    def __enter__(self):
        return self


class ManagerTests(unittest.TestCase):
    def test_manager_can_install_without_running_shell_scripts(self):
        data = archive(malicious=True)
        root = Path(tempfile.mkdtemp())
        try:
            release_info = release(data)
            # The public release archive is over 1 MiB; synthetic test data isn't.
            release_info["assets"][0]["size"] = len(data)
            with mock.patch.object(swmm, "_release", return_value={
                    "tag": "v0.1.3",
                    "url": swmm.SITE + "/releases/download/v0.1.3/" + swmm.ASSET,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest()}):
                with mock.patch.object(swmm.urllib.request, "urlopen", return_value=Source(data)):
                    result = swmm.install(root, lambda *_: None, threading.Event())
            self.assertEqual(result, swmm.folder(root) / "swmm")
            self.assertEqual(result.read_bytes(), binary())
            self.assertTrue(os.access(result, os.X_OK))
            self.assertFalse((root / "evil").exists())
            self.assertTrue((swmm.folder(root) / "release.json").is_file())
        finally:
            import shutil
            shutil.rmtree(root)

    def test_bad_digest_retains_existing_manager_and_mods(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dest = swmm.folder(root)
            (dest / "data/profiles/Default").mkdir(parents=True)
            (dest / "data/profiles/Default/mod.yml").write_text("enabled: true")
            old = dest / "swmm"
            old.write_bytes(binary())
            old.chmod(0o755)
            raw = archive()
            with mock.patch.object(swmm, "_release", return_value={
                    "tag": "v0.1.3",
                    "url": swmm.SITE + "/releases/download/v0.1.3/" + swmm.ASSET,
                    "size": len(raw), "sha256": "f" * 64}):
                with mock.patch.object(swmm.urllib.request, "urlopen", return_value=Source(raw)):
                    with self.assertRaises(core.InstallError):
                        swmm.install(root, lambda *_: None, threading.Event())
            self.assertEqual(old.read_bytes(), binary())
            self.assertTrue((dest / "data/profiles/Default/mod.yml").is_file())

    def test_cancel_download_keeps_existing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = archive()
            cancel = threading.Event()
            cancel.set()
            with mock.patch.object(swmm, "_release", return_value={
                    "tag": "v0.1.3",
                    "url": swmm.SITE + "/releases/download/v0.1.3/" + swmm.ASSET,
                    "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}):
                with mock.patch.object(swmm.urllib.request, "urlopen", return_value=Source(data)):
                    with self.assertRaises(core.Cancelled):
                        swmm.install(root, lambda *_: None, cancel)
            self.assertFalse(swmm.managed(root))

    def test_bridge_is_local_and_refuses_modified_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "root/romfs").mkdir(parents=True)
            first = swmm._bridge(root)
            self.assertEqual((first / "extracted").resolve(), root / "root")
            self.assertEqual(swmm._bridge(root), first)
            (first / "extracted").unlink()
            (first / "extracted").symlink_to(Path(folder).parent)
            with self.assertRaises(core.InstallError):
                swmm._bridge(root)

    def test_shim_uses_launcher_without_rewriting_data_roots(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            appimage = root / "SavageWind-Portable-x86_64.AppImage"
            appimage.write_bytes(binary())
            (root / "root/romfs").mkdir(parents=True)
            shim = swmm._shim(root, str(appimage))
            self.assertTrue(os.access(shim, os.X_OK))
            script = shim.read_text()
            self.assertIn("--mods-play --root", script)
            self.assertIn(str(appimage), script)
            self.assertNotIn("curl", script)
            self.assertEqual(shim.parent / "extracted", swmm.folder(root) / "bridge/extracted")
            self.assertEqual((shim.parent / "extracted").resolve(), root / "root")

    def test_external_swmm_remains_unmodified_and_uses_isolated_xdg(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as external:
            root = Path(folder)
            manager = Path(external) / "swmm"
            manager.write_bytes(binary())
            manager.chmod(0o755)
            (root / "root/romfs").mkdir(parents=True)
            image = root / "SavageWind-Portable-x86_64.AppImage"
            image.write_bytes(binary())
            with mock.patch.object(swmm, "installed", return_value=manager):
                with mock.patch.object(swmm, "managed", return_value=None):
                    with mock.patch.object(swmm, "_shortcuts"):
                        with mock.patch.object(swmm.subprocess, "Popen") as popen:
                            swmm.launch(root, launcher=str(image))
            args, kwargs = popen.call_args
            self.assertEqual(args[0], [str(manager)])
            self.assertIn(str(root / ".sw-wrapper/swmm/external"), kwargs["env"]["XDG_CONFIG_HOME"])
            self.assertEqual(kwargs["env"]["SAVAGEWIND_DATA_ROOT"],
                             str(root / ".sw-wrapper/swmm/bridge"))
            self.assertTrue((root / ".sw-wrapper/swmm/external/config/SWMM/settings.yml").is_file())
            self.assertEqual(manager.read_bytes(), binary())

    def test_local_swmm_launch_uses_portable_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manager = swmm.folder(root) / "swmm"
            manager.parent.mkdir(parents=True)
            manager.write_bytes(binary())
            manager.chmod(0o755)
            (root / "root/romfs").mkdir(parents=True)
            image = root / "SavageWind-Portable-x86_64.AppImage"
            image.write_bytes(binary())
            with mock.patch.object(swmm, "_shortcuts"):
                with mock.patch.object(swmm.subprocess, "Popen") as popen:
                    swmm.launch(root, launcher=str(image))
            args, kwargs = popen.call_args
            self.assertEqual(args[0], [str(manager), "--portable"])
            self.assertEqual(kwargs["env"]["SAVAGEWIND_DATA_ROOT"],
                             str(root / ".sw-wrapper/swmm/bridge"))
            self.assertTrue((swmm.folder(root) / "config/settings.yml").is_file())

    def test_existing_official_manager_registers_portable_menu_shortcut(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as apps:
            root = Path(folder)
            (root / "root/romfs").mkdir(parents=True)
            launcher = root / "SavageWind-Portable-x86_64.AppImage"
            launcher.write_bytes(binary())
            manager = root / "official-bin/swmm"
            manager.parent.mkdir()
            manager.write_bytes(binary())
            manager.chmod(0o755)
            env = {"APPIMAGE": str(launcher), "XDG_DATA_HOME": str(Path(apps) / "share")}
            with mock.patch.object(swmm, "installed", return_value=manager), \
                 mock.patch.object(swmm, "managed", return_value=None):
                result = swmm.prepare(root, environ=env)
                self.assertEqual(result[0], manager)
                self.assertEqual(result[1], [str(manager)])
                self.assertEqual(result[2]["SAVAGEWIND_DATA_ROOT"],
                                 str(swmm.folder(root) / "bridge"))
                self.assertEqual(result[2]["HOME"],
                                 str(swmm.folder(root) / "external/home"))
                menu = swmm._shortcut_path(root, env)
                self.assertTrue(menu.is_file())
                desktop = menu.read_text()
                self.assertIn("Savage Wind Portable - Mods", desktop)
                self.assertIn(" --mods", desktop)
                self.assertNotIn("--config", desktop)
                self.assertIn(str(launcher), desktop)
                self.assertEqual((root / "SavageWind-Mods.desktop").read_text(), desktop)
                self.assertEqual((swmm.folder(root) / "bridge/extracted").resolve(),
                                 root / "root")
                self.assertIn("executable: ", (swmm.folder(root) /
                    "external/config/SWMM/settings.yml").read_text())
                before = menu.stat().st_mtime_ns
                settings = swmm.folder(root) / "external/config/SWMM/settings.yml"
                settings.write_text(settings.read_text() + "\n# user customization\n")
                swmm.prepare(root, environ=env)
                self.assertEqual(menu.stat().st_mtime_ns, before)
                self.assertIn("# user customization", settings.read_text())

    def test_no_swmm_installed_does_not_create_shortcuts(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as apps:
            root = Path(folder)
            (root / "root/romfs").mkdir(parents=True)
            launcher = root / "SavageWind-Portable-x86_64.AppImage"
            launcher.write_bytes(binary())
            env = {"APPIMAGE": str(launcher), "XDG_DATA_HOME": apps}
            with mock.patch.object(swmm, "installed", return_value=None):
                self.assertIsNone(swmm.prepare(root, environ=env))
            self.assertFalse((root / "SavageWind-Mods.desktop").exists())
            self.assertFalse(swmm._shortcut_path(root, env).exists())

    def test_auto_prepare_runs_on_normal_start_without_opening_swmm(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with mock.patch.object(swmm, "prepare", return_value=(True, [], {})) as prepare:
                with mock.patch("wrapper.launcher_updates.check", return_value=None):
                    result = core.Pipeline(root, lambda *_: None, lambda _: False,
                                           threading.Event(), mode="launcher_check").run()
            self.assertEqual(result, "setup")
            prepare.assert_called_once_with(root)

    def test_overlay_is_forwarded_to_game_only_if_swmm_has_deployed_mods(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = swmm.folder(root) / "data/overlay/Actor"
            path.mkdir(parents=True)
            (path / "mod.bin").write_bytes(b"mod")
            env = core.portable_env(root, base={"HOME": "/unused"}, play=True)
            self.assertEqual(env["SAVAGEWIND_ROMFS_OVERLAY"],
                             str(swmm.folder(root) / "data/overlay"))
            env = core.portable_env(root, base={
                "SAVAGEWIND_ROMFS_OVERLAY": "/specific-overlay",
                "SWMM_LAUNCHED": "1"}, play=True)
            self.assertEqual(env["SAVAGEWIND_ROMFS_OVERLAY"], "/specific-overlay")
            env = core.portable_env(root, base={"SWMM_LAUNCHED": "1"}, play=True)
            self.assertNotIn("SAVAGEWIND_ROMFS_OVERLAY", env)

    def test_reset_preserves_swmm_profiles_and_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            keep = swmm.folder(root) / "data/profiles/Default"
            keep.mkdir(parents=True)
            (keep / "mod.yml").write_text("mod")
            (root / "root").mkdir()
            core.reset_install(root, "DELETE")
            self.assertEqual((keep / "mod.yml").read_text(), "mod")
            self.assertFalse((root / "root").exists())


if __name__ == "__main__":
    unittest.main()
