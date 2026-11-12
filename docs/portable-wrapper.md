# Linux Portable Launcher

This fork uses the **unmodified official** [Savage Wind Recomp](https://github.com/Revivedx/SavageWindRecomp) AppImage. It supplies a separate native Linux launcher (Qt on KDE Plasma, GTK on GNOME) and does not distribute the game or keys.

## Installation

For the current draft PR, download the portable launcher AppImage from the artifacts of a successful [PR checks workflow](https://github.com/lueefr/SavageWindRecomp/actions/workflows/installer-pr.yml). Put it in a physical writable directory, make it executable, and run it. The draft PR does not publish a stable release automatically.

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

## First launch and installed AppImage history

The launcher looks for an existing AppImage before presenting the normal first-run chooser. A recognised official `Savage_Wind-*.AppImage` (or `SavageWind-official.AppImage`) in the same directory is checked against **the SHA-256 and size advertised by upstream GitHub releases**, including older releases. If it matches, it is adopted safely and the actual tag is recorded; an update prompt can then offer the latest release. If no executable can be verified, the chooser defaults to official upstream, with `test` and PR-built alternatives. An unidentified development AppImage is not assigned a made-up commit or executed as a verified official release.

The portable `.sw-wrapper/runtime-history.json` stores the active source, version tag or `test` commit, SHA-256, selected PR revisions and upstream base, the UTC update time, and a history of previous versions. When an automatic update is accepted, its linked changelog is saved to the new history entry. Native build identity is also retained to avoid unnecessary recompilation if `state.json` is lost. A missing state file can be reconstructed from the history **only after hashing and validating the matching on-disk AppImage**.

A declined update keeps the previous AppImage and native game library. Without **Don't ask anymore**, it is prompted again at the next start; opting out is stored per source in `.sw-wrapper/update-prefs.json`. A failed update restores the old executable, native code, state and version history. Deleting the installation retains the audit trail, appends an installation-reset event, clears the active version, and preserves saves and configuration. Neither local file names alone nor an unverified build log are sufficient proof of an official release.

## Pause and resume compilation

The **Pause** button in both Qt (KDE) and GTK (GNOME) progress windows
suspends the active compiler process group with Linux `SIGSTOP`. This applies
to local `test`/PR AppImage builds (including their Git/Podman build steps)
and to native-game compilation (`swgame.so`), including tracked child compiler
process groups. **Resume** sends `SIGCONT` and continues the same build without
discarding the downloaded sources, temporary build files, or partial compilation
output. The time spent paused does not count toward source-build ETAs or the
native compiler's one-hour watchdog.

Pausing is temporary and supported **while the launcher remains running**:
it is not an on-disk checkpoint or a means to resume after closing the
launcher, rebooting, or killing the process. Clicking **Cancel** while paused
first releases the frozen process groups and then proceeds through normal
cancellation and cleanup. Progress indicators remain frozen while paused.
The game itself is not suspended by this control.

## Automatic updates for the portable launcher itself

The launcher independently checks **versioned releases of this fork**
([lueefr/SavageWindRecomp](https://github.com/lueefr/SavageWindRecomp/releases)),
not the official Savage Wind game releases. Only published `portable-vX.Y.Z`
releases with an exact `SavageWind-Portable-x86_64.AppImage` filename, an
official GitHub asset SHA-256 and an expected size are accepted. Unversioned
`portable-preview` releases are ignored.

The AppImage includes `launcher-version.json` containing the source commit
and, for tagged releases, its portable version. For an older launcher without
packaged metadata, the updater recognizes a published image by SHA-256.
Newer development previews are compared against the latest release commit
before any update prompt: they are **never silently downgraded**. Unknown
local builds are left untouched rather than assigned invented versions.

When a newer portable launcher is available, the **same native Qt/GTK update
dialog** shows a brief linked changelog, defaults to **No**, and offers
**Don't ask anymore**. Declining without the checkbox repeats the question
on subsequent launches. Opt-out is stored separately under the `launcher`
key in `.sw-wrapper/update-prefs.json`; it does not disable updates to the
official game, test branch or selected PRs.

On acceptance, the new launcher AppImage is downloaded into the same portable
directory, checked against the GitHub asset size and SHA-256, and atomically
replaces **only the currently running portable launcher file**. The previous
image is retained as `.sw-wrapper/launcher-previous.AppImage` for recovery,
and the installed update is recorded in `.sw-wrapper/launcher.json`.
If validation or installation fails, the original file stays available and
gameplay/setup may continue. `root/`, `game/`, saves, native code, and the
Savage Wind game AppImages are never overwritten by this self-updater.

The already running launcher continues the current operation; the updated
launcher code takes effect **on the next launch**. No GUI window is shown
solely for a silent update check when no newer release exists.

## Native recompilation and progress

The upstream application performs its own native game-code compilation. The wrapper monitors the upstream process and its `root/.gamebuild/` work files for generated code, modules and compiled objects, while preserving the official toolchain and output.

The displayed percentage is an **approximation based on measured work**, not a guaranteed upstream progress value. The ETA becomes available only when enough samples exist. Long, silent stages are possible; UI updates do not prove success or failure.

A completed native build can be reused if the official version, CPU target and on-disk library identity match. An older build rejected solely because the upstream process was silent can be recovered conservatively when logs, artifacts and timestamps support that conclusion. An explicit upstream `NOT READY` result remains a failure.

Diagnostics are saved under `.sw-wrapper/`, including `build.log` and `import.log`. The GUI displays new stages without endlessly appending duplicate progress messages. Cancellation stops import or compilation subprocesses and cleans incomplete temporary work.

The previous official version and compiled artifacts can be retained for rollback when updates fail. Saves and settings are not part of that replacement.

## Diagnosing long AppImage packaging stages

In the upstream `test` and PR source-build paths, the stage formerly labelled
"Package AppImage" runs `tools/linux/package.sh`. That script also downloads
and builds pinned SDL3, FFmpeg and shaderc dependencies, compiles the Savage Wind
executable, fetches Zig and finally runs appimagetool. A long period in this
stage does not necessarily mean the squashfs assembly itself has stalled.

The launcher now calls this **Build dependencies and package AppImage**. When
upstream output identifies a subtask, it displays the observed subtask without
inventing a completion percentage. If a packaging subprocess stops emitting
output for 30 seconds, the status and the corresponding
`.sw-wrapper/test-build.log` or `pr-build.log` report its silence duration,
PID and available Linux process states, with further notices at 30-second
intervals. Log data is flushed regularly so it can be inspected while building.
A completed parent process no longer waits indefinitely when a detached child
still holds the inherited stdout pipe open. This is a diagnostic aid, not
proof of forward progress; if no computation is occurring, examine the log and
cancel or resume the build as appropriate.

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

The `test` option clones upstream's `test` branch and builds it **without any PR merges**. It records the exact checked-out commit and SHA-256 of the produced AppImage, saved as `SavageWind-test.AppImage`. The test AppImage is independent of both the official release (`SavageWind-official.AppImage`) and the PR-patched version (`SavageWind-custom.AppImage`). When the test option is selected again, the installer checks the latest upstream `test` commit with `git ls-remote`. If it matches the recorded commit, the existing `SavageWind-test.AppImage` is reused, and only missing or incompatible native game code is rebuilt. If upstream has changed, it requests confirmation before building the new AppImage. Normal game launches check the latest test branch commit online (unless update prompts were disabled), show the linked changelog when the commit changes, and reuse the installed AppImage when the update is declined. The compiler log is `.sw-wrapper/test-build.log`.

PR builds use a temporary checkout under .sw-wrapper/pr-build-*, fetch each selected PR and compare its SHA before merging. Source builds use host Git and **rootless Podman**. When either is missing, the launcher identifies the Linux distribution, shows the exact package names and install command, and asks for permission before opening a terminal to install them. Once installation succeeds, the build resumes. PR-controlled build scripts execute inside rootless Podman without mounting the extracted game, saves, or prod.keys. The generated AppImage is installed atomically as SavageWind-custom.AppImage, keeping SavageWind-official.AppImage unchanged. Source compilation containers use `--userns=keep-id` so created files belong to the launching Linux user, not subordinate container UIDs. When an older launcher generated the entire test AppImage but failed before recording its state, a subsequent launch can offer to reuse that existing executable rather than recompiling everything. Recovery requires an existing ELF AppImage, a completed packaging log, matching modification order and explicit user approval; if the older log lacks a full commit SHA, the recovered revision is clearly marked as unverified. Declining recovery starts a fresh build. Disposable checkouts are removed after the output is installed. If older rootless builds left inaccessible files, cleanup is retried only on that temporary checkout through `podman unshare`; cleanup failures are logged without discarding a successfully packaged AppImage or interrupting state persistence. Diagnostic output is saved in .sw-wrapper/pr-build.log. An internet connection and sufficient disk space are required.

**Security:** PR code is not trusted. The installer requires explicit consent before compiling. Although compilation is contained, the produced program will run as the user when launched; review all selected PRs carefully. No official SHA-256 digest is available for user-built AppImages.

When valid files are already present in root/, the NSP/XCI import fields are disabled and **Use existing game files** becomes available. When the current AppImage is missing, the setup interface opens rather than silently attempting an official download. Native code is reused only if the recorded build digest and CPU target match; otherwise the local game code is rebuilt.

**Delete installation** removes root/, extracted/, downloaded official/test/PR AppImages, compiled native code, caches and temporary build files, but explicitly preserves saves, configuration folders and files, user profiles, language preferences and the portable launcher. A double confirmation with the literal word DELETE is required; symbolic links and concurrent operations are refused.

These are development features: synthetic tests do not guarantee that unrelated PRs can be merged or that a new AppImage works in-game. The portable installer itself is packaged through tools/linux/build_wrapper.sh and .github/workflows/wrapper.yml, not the upstream make appimage target.

## Automatic build dependency installation (experimental)

The **portable launcher stays lightweight**. It no longer embeds an Ubuntu root filesystem, Clang/LLVM, Zig, a copy of Git, or bubblewrap. It uses the original upstream Podman build container for source compilation. Toolchain downloads occur only when the user intentionally starts a local upstream `test` or PR build and approves any missing system packages.

Before a source build, the installer checks whether **Git** and **Podman** are available. It separately checks that the kernel provides a working TUN device for pasta networking. When a tool is absent, it identifies the distribution from `/etc/os-release`, displays the exact packages and commands, and asks the user to approve installation. It never executes a package manager without permission. Package installation runs in a separate terminal so password prompts can be handled normally. The build continues automatically after the package manager succeeds. It does not run `yay` as root.

Supported package managers: **Arch Linux and Arch derivatives** (prefer `yay`, otherwise `pacman`), **Debian/Ubuntu derivatives** (`apt-get`), **Fedora/RHEL derivatives** (`dnf`) and **openSUSE** (`zypper`). Other systems receive a clear message requesting manual installation; immutable or managed Linux installations may require distribution-specific administration.

The package manager modifies the **host operating system**, rather than downloading dependencies into the game directory. User authorization is mandatory, and the terminal shows any requested administrator authentication. An installed Podman engine still needs rootless containers / user namespace support and valid `/etc/subuid` and `/etc/subgid` configuration. If rootless Podman is unusable, the installer reports a diagnostic rather than switching to privileged containers.

The source builder uses `podman run --network=pasta` with Podman 6-compatible rootless networking. It extracts the upstream Ubuntu 22.04 toolchain's single `RUN` recipe, executes it in a temporary container, and uses `podman commit` to cache the compiler image by recipe hash; subsequent builds reuse it. It does not call `podman build --network=slirp4netns` or install the removed `slirp4netns` driver. Source generation and AppImage packaging also run under rootless Podman with pasta. The launcher checks whether `/dev/net/tun` actually works before cloning the source and, when necessary, explicitly asks for permission to load the kernel's `tun` module. If the module cannot be loaded or the kernel does not expose TUN, it stops with an actionable error rather than attempting host networking. The build never uses `--network=host` and does not mount player game files, saves, or keys.

Compilation executes within a rootless Podman container, while the portable launcher never includes the user's game files, keys or saves in that container. Build dependencies and source are fetched over the network during the local build. The official upstream AppImage option does not require Podman or Git and does not trigger package installation.

CI checks the small launcher, Python/Qt behavior and distro-detection tests; it does not install host packages or run a potentially heavy upstream source compilation on every pull request. Keep this PR in draft until a real `test` and PR build have been tested on the target systems.

## Native game-code staging for current upstream test builds

The upstream's current `test` runtime treats `<SAVAGEWIND_DATA_ROOT>/extracted` as its required game directory and refuses some game-code cleanup operations outside that directory. The portable layout deliberately stores the extracted game in `root/` and saves/settings in `game/`. During local `test` and combined-PR native-code setup, the installer **renames** `root/` to `game/extracted/` for the duration of the compiler, then renames it back, with no RomFS duplication. The final native `swgame.so` and `swgame.so.id` files are moved into `game/`; the user's saves remain untouched. Official release builds retain their previously working setup path.

The temporary move is protected by the install operation lock and a persistent `.sw-wrapper/code-stage` marker. If the launcher or operating system crashes, the next installer operation restores `root/` from `game/extracted/` **before** checking game files. If both directories exist unexpectedly, it refuses to overwrite or delete either. A failed or cancelled build restores the original directory on exit. The built-in progress meter follows the temporary working path. Missing native-code artifacts are diagnosed using the upstream `game/savagewind.log` (which records the actual code-generation/linker errors, unlike the launcher stdout pipe).

The presence of a packaged AppImage does **not** imply that the player's native game code has been built for it. Compilation and validation of the game library are a separate step. Never remove `root/`, `game/`, or the save files simply because native-code validation failed.


## Independent build, native recompilation, and play actions

The **Source builds** page has separate controls:

- **Build AppImage**: choose the current official release, upstream `test` or one or more open PRs to download/compile the executable. This action is displayed only when a runtime AppImage is already installed. After the AppImage is ready, if valid game files are already present in `root/`, the installer asks whether to also compile the native game library `swgame.so` **using this AppImage**. The dialog warns that recompilation can take considerable time and replace the existing native library. **No is the default** and finishes successfully without recompiling or modifying saves/settings; **Yes** explicitly starts the native-code build and publishes its new library and ID in `game/`, without launching the game. If no game files are present, the question is skipped. Compiling the AppImage itself requires no game files, and unchanged test/PR selections can reuse an existing AppImage.
- **Recompile game code**: explicit opt-in to regenerate the player's \`swgame.so\`. Before starting, optionally select **Build upstream test branch (no PRs)** or **Build from selected pull requests** to build/select that AppImage first. With neither selected, use the official runtime (download it with permission if missing). An already-installed custom executable can be reused without reselecting PRs. The native build remains available as a separate action and is never triggered automatically after an AppImage build without the user's explicit Yes.
- **Play existing game**: launch the currently selected AppImage using the existing native library and save files, without recompilation. If the library is absent, the launcher asks the user to explicitly invoke native compilation. The upstream loader may reject a library compiled against an incompatible executable; this is not fixed by silently recompiling.
- **Install and play**: for a first-time installation or game-file import, preserves the previous first-run import workflow. Importing game files may need initial native-code compilation.

AppImage builds display their **own six-step progress list**: Fetch sources, Apply pull requests, Prepare compiler, Compile runtime, Package AppImage, and Publish AppImage. They do not reuse the native game-recompilation steps. The UI displays elapsed time and an ETA based on measured stage durations recorded in \`.sw-wrapper/source-times.json\`; on a first build it states that the ETA is still being calculated, and may provide a clearly marked preliminary estimate once enough stages finish. Estimates from later runs use actual recorded timings, which can vary with changes in upstream code and hardware.

## Remove installation but keep saves and configuration

**Delete installation** still requires explicitly entering \`DELETE\` and an exclusive installer lock. It removes game files under \`root/\`/\`extracted/\`, installed official/test/custom AppImages (including old FSR2 artifacts), generated \`swgame.so\`/ID, caches, temporary build data and logs. It **does not delete** \`game/save/\`, \`game/saves/\`, save states, \`game/config/\`, \`game/configs/\`, \`game/profiles/\`, \`game/savagewind.ini\`, compatible configuration files, \`game/.xdg/config/\`, \`game/.xdg/data/\` or the interface's \`.sw-wrapper/gui.json\`. The portable launcher and unrelated files next to it are also retained. If there are symlinks or unexpected file types in deletion targets, reset stops rather than following them. The retained \`game/\` directory can be reused by a fresh installation.


## First launch and automatic AppImage updates

On normal startup, the launcher first looks for the selected Savage Wind AppImage. If no AppImage exists, a native source-selection dialog opens with **Official upstream AppImage** as the default. The other choices are **Upstream test branch** and **Build with pull requests**. Choosing PRs opens the Source builds page so individual PRs can be selected. If the game files are already present, the official/test choice immediately installs or builds the AppImage, recompiles native game code with that AppImage, and launches the game. Otherwise, the user first imports their own game files, then compilation and game launch follow. The game's native library cannot be generated without the user's game files.

For an existing installation, the launcher checks updates on each normal startup, separately by source:

- **Official:** compare the currently installed verified AppImage SHA-256 with the latest upstream published AppImage release.
- **Test:** check the current upstream test branch commit against the installed source commit. No release is required.
- **Selected PRs:** check the current upstream test base commit and the latest head SHA of every tracked selected PR. New custom builds record the complete PR selection and base commit, not just its hash. Pre-existing custom builds without that metadata require one manual PR rebuild to enable precise update checking.

When changes exist, a native Qt/GTK confirmation dialog shows a short changelog with working GitHub links (releases, commits and PRs). It has **Yes**, **No** (the default) and an initially unchecked **Don't ask anymore** option. A refused update **without** this checkbox reappears at the next launch. Checking the box saves a per-source setting in the portable update-prefs.json file in .sw-wrapper/; prompts for other runtimes remain independent. If the GitHub check fails (for example offline), the previous AppImage launches normally without a network-related native recompilation.

Choosing **No** launches the existing executable with the existing native game library and leaves saves and configs untouched. Choosing **Yes** installs/builds that specific update, recompiles the native game code with the new AppImage and starts the game. A failed update restores the previous AppImage, native library and metadata from the temporary rollback. User save files and configs are not part of that backup.

**Build AppImage** is available only once an AppImage is installed, for explicit replacement/customization. It can fetch the official release, compile upstream test or apply selected PRs. After such a manual build it offers optional native game compilation with a separate default-No confirmation, and does not automatically launch the game. **Recompile game code** is an independent action.

Missing build dependencies are requested only after approving a test/PR update. The installer never changes the host system or fetches build dependencies just to check for updates. Deleting the game installation preserves update notification preferences, as well as saves and settings.
