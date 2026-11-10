# Linux Portable Launcher

This fork uses the **unmodified official** [Savage Wind Recomp](https://github.com/Revivedx/SavageWindRecomp) AppImage. It supplies a separate native Linux launcher (Qt on KDE Plasma, GTK on GNOME) and does not distribute the game or keys.

## Installation

Download the [portable preview AppImage](https://github.com/lueefr/SavageWindRecomp/releases/latest/download/SavageWind-Portable-x86_64.AppImage), put it in the directory where you want to keep the installation, make it executable, and run it.

On first launch, select your own extracted `exefs/` and `romfs/` directory, or use the NSP/XCI importer with your own base game, update and `prod.keys`. The installer verifies its inputs and checks temporary disk space before proceeding. Importing and compiling may take a long time.

The AppImage resides in a **physical, writable directory**, not its temporary AppImage mount. The typical layout is:

```text
SavageWind/
  SavageWind-Portable-x86_64.AppImage
  SavageWind-official.AppImage
  root/
    exefs/
    romfs/
  game/
    swgame.so
    swgame.so.id
    save/
    cache/
    fonts/
    savagewind.ini
    .xdg/
  .sw-wrapper/
    build.log
    import.log
```

`root/` contains game files from the user's copy; `game/` contains locally compiled native code, saves and configuration. The wrapper publishes `swgame.so` and its ID to `game/` after an upstream compilation finishes, and directs the official launcher to this library using `SAVAGEWIND_GAME_LIB`.

The HTTPS downloader bundles a maintained Mozilla certificate store, validates TLS, verifies its downloads, and does not silently accept unverified upstream builds.

## Updates and runtime isolation

Each launch can check official upstream releases, including prereleases. The wrapper compares verified SHA-256 digests and can reuse a matching official AppImage already present. It asks before replacing an installed official version. The downloaded game executable is neither patched nor repackaged.

With an unchanged installation, the UI can remain completely hidden while the launcher checks for updates, validates the existing build, and starts the game. The installation interface opens for required setup, recovery, migration, or errors; `--config` opens it explicitly:

```sh
./SavageWind-Portable-x86_64.AppImage --config
```

The wrapper sets `SAVAGEWIND_DATA_ROOT`, `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME`, `MESA_SHADER_CACHE_DIR` and `TMPDIR` before launching the upstream executable. The game is therefore intended to keep its runtime state within `game/` rather than `~/.local/share/savagewind/`.

Previous portable layouts are migrated carefully: `extracted/` becomes `root/`, and previous native libraries and save data are moved to `game/`. Conflicts are treated as errors rather than overwritten blindly. Data outside the portable installation is migrated only with user consent.

## Native recompilation and progress

The upstream application performs its own native game-code compilation. The wrapper monitors the upstream process and its `root/.gamebuild/` work files for generated code, modules and compiled objects, while preserving the official toolchain and output.

The displayed percentage is an **approximation based on measured work**, not a guaranteed upstream progress value. The ETA becomes available only when enough samples exist. Long, silent stages are possible; UI updates do not prove success or failure.

A completed native build can be reused if the official version, CPU target and on-disk library identity match. An older build rejected solely because the upstream process was silent can be recovered conservatively when logs, artifacts and timestamps support that conclusion. An explicit upstream `NOT READY` result remains a failure.

Diagnostics are saved under `.sw-wrapper/`, including `build.log` and `import.log`. The GUI displays new stages without endlessly appending duplicate progress messages. Cancellation stops import or compilation subprocesses and cleans incomplete temporary work.

The previous official version and compiled artifacts can be retained for rollback when updates fail. Saves and settings are not part of that replacement.

## Requirements and limitations

- Linux x86-64 and a Vulkan-compatible GPU/driver meeting the official game's requirements.
- A compatible, legally acquired game copy; no game, keys, firmware, or copyrighted Nintendo assets are distributed.
- Significant free space for NSP/XCI extraction and temporary BKTR/RomFS processing.
- The importer uses pinned local extraction helpers, validates checksums and reads user-supplied keys only during import.
- The wrapper never guarantees a particular compilation time or frame rate. It remains dependent on the upstream release's command-line behavior and game format.

Unit tests are in `wrapper/tests/` and `tools/linux/test_import_game.py`. The portable AppImage is built by `.github/workflows/wrapper.yml`; synthetic tests do not replace a real game launch test.

For original runtime features and game issues, consult the **[upstream Savage Wind Recomp repository](https://github.com/Revivedx/SavageWindRecomp)**. This fork is independent and concentrates on portable distribution.

## Experimental local PR builds

The setup window defaults to **English**. Its language selector offers English, Brazilian Portuguese, Russian and Simplified Chinese. The selection is saved separately from the game language in .sw-wrapper/gui.json.

The **Source builds** tab offers three exclusive executable sources: leave both checkboxes unchecked to use the verified official upstream AppImage (default); choose **Build upstream test branch (no PRs)** to compile the latest available commit from Revivedx/SavageWindRecomp's `test` branch; or choose **Build from selected pull requests** to merge one or more open upstream PRs targeting `test`. Refresh the PR list, read each title and description, and tick the PRs to combine. PR merge conflicts abort the build rather than silently dropping patches.

The `test` option clones upstream's `test` branch and builds it **without any PR merges**. It records the exact checked-out commit and SHA-256 of the produced AppImage, saved as `SavageWind-test.AppImage`. The test AppImage is independent of both the official release (`SavageWind-official.AppImage`) and the PR-patched version (`SavageWind-custom.AppImage`). Clicking Install again while the test option is selected explicitly builds the current `test` head again; ordinary launches reuse the saved build without fetching new source automatically. The compiler log is `.sw-wrapper/test-build.log`.

PR builds use a temporary checkout under .sw-wrapper/pr-build-*, fetch each selected PR and compare its SHA before merging. The host runs Git and rootless Podman only. All PR-controlled compilation scripts run in a container with just the source checkout mounted, without the extracted game, save files or prod.keys. The custom AppImage is installed atomically as SavageWind-custom.AppImage, leaving SavageWind-official.AppImage untouched. Diagnostic output is stored in .sw-wrapper/pr-build.log. The build requires git and podman, sufficient disk space, and an internet connection.

**Security:** PR code is not trusted. The installer requires explicit consent before compiling. Although compilation is contained, the produced program will run as the user when launched; review all selected PRs carefully. No official SHA-256 digest is available for user-built AppImages.

When valid files are already present in root/, the NSP/XCI import fields are disabled and **Use existing game files** becomes available. When the current AppImage is missing, the setup interface opens rather than silently attempting an official download. Native code is reused only if the recorded build digest and CPU target match; otherwise the local game code is rebuilt.

**Delete installation** permanently removes root/, game/ including saves and configuration, extracted/, downloaded official, PR-built or test-branch AppImages, and the installation state. It does not remove the portable launcher, unrelated files or the language preference. A double confirmation with the literal word DELETE is required; symbolic links and concurrent operations are refused.

These are development features: synthetic tests do not guarantee that unrelated PRs can be merged or that a new AppImage works in-game. The portable installer itself is packaged through tools/linux/build_wrapper.sh and .github/workflows/wrapper.yml, not the upstream make appimage target.
