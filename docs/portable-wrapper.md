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

PR builds use a temporary checkout under .sw-wrapper/pr-build-*, fetch each selected PR and compare its SHA before merging. Source builds use host Git and **rootless Podman**. When either is missing, the launcher identifies the Linux distribution, shows the exact package names and install command, and asks for permission before opening a terminal to install them. Once installation succeeds, the build resumes. PR-controlled build scripts execute inside rootless Podman without mounting the extracted game, saves, or prod.keys. The generated AppImage is installed atomically as SavageWind-custom.AppImage, keeping SavageWind-official.AppImage unchanged. Source compilation containers use `--userns=keep-id` so created files belong to the launching Linux user, not subordinate container UIDs. When an older launcher generated the entire test AppImage but failed before recording its state, a subsequent launch can offer to reuse that existing executable rather than recompiling everything. Recovery requires an existing ELF AppImage, a completed packaging log, matching modification order and explicit user approval; if the older log lacks a full commit SHA, the recovered revision is clearly marked as unverified. Declining recovery starts a fresh build. Disposable checkouts are removed after the output is installed. If older rootless builds left inaccessible files, cleanup is retried only on that temporary checkout through `podman unshare`; cleanup failures are logged without discarding a successfully packaged AppImage or interrupting state persistence. Diagnostic output is saved in .sw-wrapper/pr-build.log. An internet connection and sufficient disk space are required.

**Security:** PR code is not trusted. The installer requires explicit consent before compiling. Although compilation is contained, the produced program will run as the user when launched; review all selected PRs carefully. No official SHA-256 digest is available for user-built AppImages.

When valid files are already present in root/, the NSP/XCI import fields are disabled and **Use existing game files** becomes available. When the current AppImage is missing, the setup interface opens rather than silently attempting an official download. Native code is reused only if the recorded build digest and CPU target match; otherwise the local game code is rebuilt.

**Delete installation** permanently removes root/, game/ including saves and configuration, extracted/, downloaded official, PR-built or test-branch AppImages, and the installation state. It does not remove the portable launcher, unrelated files or the language preference. A double confirmation with the literal word DELETE is required; symbolic links and concurrent operations are refused.

These are development features: synthetic tests do not guarantee that unrelated PRs can be merged or that a new AppImage works in-game. The portable installer itself is packaged through tools/linux/build_wrapper.sh and .github/workflows/wrapper.yml, not the upstream make appimage target.

## Automatic build dependency installation (experimental)

The **portable launcher stays lightweight**. It no longer embeds an Ubuntu root filesystem, Clang/LLVM, Zig, a copy of Git, or bubblewrap. It uses the original upstream Podman build container for source compilation. Toolchain downloads occur only when the user intentionally starts a local upstream `test` or PR build and approves any missing system packages.

Before a source build, the installer checks whether **Git** and **Podman** are available. It separately checks that the kernel provides a working TUN device for pasta networking. When a tool is absent, it identifies the distribution from `/etc/os-release`, displays the exact packages and commands, and asks the user to approve installation. It never executes a package manager without permission. Package installation runs in a separate terminal so password prompts can be handled normally. The build continues automatically after the package manager succeeds. It does not run `yay` as root.

Supported package managers: **Arch Linux and Arch derivatives** (prefer `yay`, otherwise `pacman`), **Debian/Ubuntu derivatives** (`apt-get`), **Fedora/RHEL derivatives** (`dnf`) and **openSUSE** (`zypper`). Other systems receive a clear message requesting manual installation; immutable or managed Linux installations may require distribution-specific administration.

The package manager modifies the **host operating system**, rather than downloading dependencies into the game directory. User authorization is mandatory, and the terminal shows any requested administrator authentication. An installed Podman engine still needs rootless containers / user namespace support and valid `/etc/subuid` and `/etc/subgid` configuration. If rootless Podman is unusable, the installer reports a diagnostic rather than switching to privileged containers.

The source builder uses `podman run --network=pasta` with Podman 6-compatible rootless networking. It extracts the upstream Ubuntu 22.04 toolchain's single `RUN` recipe, executes it in a temporary container, and uses `podman commit` to cache the compiler image by recipe hash; subsequent builds reuse it. It does not call `podman build --network=slirp4netns` or install the removed `slirp4netns` driver. Source generation and AppImage packaging also run under rootless Podman with pasta. The launcher checks whether `/dev/net/tun` actually works before cloning the source and, when necessary, explicitly asks for permission to load the kernel's `tun` module. If the module cannot be loaded or the kernel does not expose TUN, it stops with an actionable error rather than attempting host networking. The build never uses `--network=host` and does not mount player game files, saves, or keys.

Compilation executes within a rootless Podman container, while the portable launcher never includes the user's game files, keys or saves in that container. Build dependencies and source are fetched over the network during the local build. The official upstream AppImage option does not require Podman or Git and does not trigger package installation.

CI checks the small launcher, Python/Qt behavior and distro-detection tests; it does not install host packages or run a potentially heavy upstream source compilation on every pull request. Keep this PR in draft until a real `test` and PR build have been tested on the target systems.
