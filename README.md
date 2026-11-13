

**A Linux-native, self-contained launcher for [Savage Wind Recomp](https://github.com/Revivedx/SavageWindRecomp).** This independent fork focuses on installing, updating, and running the upstream game from one portable folder without changing the official game distribution.

> **Upstream:** [Revivedx/SavageWindRecomp](https://github.com/Revivedx/SavageWindRecomp) — original game recompilation, engine, assets tooling, and official releases. This launcher is an independent community project, not an official upstream release.

## Download

**[Download SavageWind-Portable-x86_64.AppImage](https://github.com/lueefr/SavageWindRecomp/releases/latest/download/SavageWind-Portable-x86_64.AppImage)** · [Release notes](https://github.com/lueefr/SavageWindRecomp/releases/latest)

On Linux x86-64:

```sh
chmod +x SavageWind-Portable-x86_64.AppImage
./SavageWind-Portable-x86_64.AppImage
```

The launcher uses native Qt on KDE Plasma and GTK on GNOME. It downloads and verifies the **official** Savage Wind AppImage separately and starts the unmodified upstream executable. It does not bundle the game or bypass game ownership requirements.

## Features

- **Self-contained layout.** Extracted game data, the compiled game library, saves, settings, and caches remain next to the portable AppImage.
- **Guided first-run import.** Select an existing `exefs/` + `romfs/` extraction, or import your own base/update NSP or XCI packages with your own keys.
- **Verified upstream updates.** Check official releases, verify downloaded files, and offer updates without replacing the upstream binary with an unofficial copy.
- **Reusable native recompilation.** Avoid unnecessary rebuilds when the official executable, compiled code, and CPU target already match.
- **Real installation progress.** Show import, file-copying, and best-effort native compilation progress, with an ETA only when sufficient measurements exist.
- **Preserved saves and rollback.** Handle migrations carefully, retain local data, and keep installation logs for debugging.
- **No home-directory game installation.** Game data is redirected to the portable location through dedicated environment variables.

## Portable folder layout

```text
SavageWind/
├── SavageWind-Portable-x86_64.AppImage
├── SavageWind-official.AppImage
├── root/
│   ├── exefs/
│   └── romfs/
├── game/
│   ├── swgame.so
│   ├── swgame.so.id
│   ├── save/
│   ├── cache/
│   ├── savagewind.ini
│   └── .xdg/
└── .sw-wrapper/
    ├── build.log
    └── import.log
```

`root/` contains extracted game files. `game/` contains generated native code and runtime user data. `.sw-wrapper/` contains installer state, temporary files and logs. The official AppImage remains a separate, untouched file. The exact set of cache directories and logs may vary with the version.

## Getting started

1. Download the portable AppImage from the preview release linked above and place it in the folder where you want the game to live.
2. Make the file executable and launch it.
3. Supply compatible files from **your own legally obtained Nintendo Switch game copy**. The importer validates the selected packages and checks required disk space before extraction.
4. Allow the official runtime to compile the native game library locally on your computer. Compilation can take a long time on older CPUs.
5. On subsequent launches, the wrapper checks for verified official updates and reuses a compatible native library when possible.

Run `./SavageWind-Portable-x86_64.AppImage --config` to open the installer or configuration UI explicitly.

For more information see [portable launcher details](docs/portable-wrapper.md) and [Linux platform notes](docs/linux.md).

## Requirements and limitations

- Linux on x86-64 with a working Vulkan 1.3 driver. The original game's minimum requirements still apply; consult [upstream documentation](https://github.com/Revivedx/SavageWindRecomp).
- The installer requires sufficient free space for temporary extraction and rebuilding. Package verification, native build progress, and process output can vary by upstream version.
- An upstream update may require recompilation. The wrapper attempts to preserve saves and migrate older portable layouts safely.
- The project does **not** contain a game dump, Nintendo keys, firmware, or copyrighted game assets. Do not upload or redistribute them.
- Portable tests and AppImage build checks do not guarantee that every upstream version or GPU will work.

## Development and upstream attribution

The portable application is implemented primarily in `wrapper/` and `tools/linux/`. It retains the upstream runtime and build infrastructure for compatibility and integration testing. Tests are under `wrapper/tests/` and `tools/linux/test_import_game.py`. The portable AppImage workflow is `.github/workflows/wrapper.yml`.

Original project: **[Savage Wind Recomp by Revivedx](https://github.com/Revivedx/SavageWindRecomp)**. This fork is not affiliated with or endorsed by the upstream maintainers. All components remain subject to their respective licenses; see [LICENSE](LICENSE) and third-party notices.
