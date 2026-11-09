# Savage Wind Portable (Linux)

A separate wrapper for the **official**, unmodified Savage Wind AppImage.
It does not compile or ship another fork of the game runtime.

## Portable data

The physical directory containing `SavageWind-Portable-x86_64.AppImage` is the
data root. The official game creates all its files there:

```text
Games/SavageWind/
  SavageWind-Portable-x86_64.AppImage
  SavageWind-official.AppImage
  extracted/                   # the player’s own ExeFS and RomFS
  save/                        # game saves
  cache/                       # shader and Mesa caches
  savagewind.ini               # upstream settings
  .xdg/                        # XDG data, config and state
  .sw-wrapper/                 # installer state, logs and temporary files
```

`SAVAGEWIND_DATA_ROOT`, `XDG_DATA_HOME`, `XDG_CONFIG_HOME`,
`XDG_STATE_HOME`, `XDG_CACHE_HOME`, `MESA_SHADER_CACHE_DIR` and `TMPDIR` are
set before launching the official game. There is **no fallback** to
`~/.local/share/savagewind`: the physical AppImage directory must be writable.
An existing older installation there is never deleted automatically.

When `extracted/` is ready, the game is launched directly. On each run, the
wrapper checks official stable **and** prerelease versions and asks before
replacing an installed version. Updates are streamed with an SHA-256 check
and atomically installed, without touching saves. The compiler runs only
when a different official binary or missing game code requires it.

## Native interface

The setup follows the TotK-PC-Recomp form and progress workflow: input folder,
stage descriptions, a progress bar, expandable log (collapsed by default)
and Cancel. Qt Widgets are used on KDE Plasma; GTK on GNOME.

**Current limitation:** import of already extracted `exefs/` and `romfs/`
is supported. Direct NSP/XCI merging is not implemented yet and must not be
presented as available.

## Build

```sh
python -m pip install PySide6==6.10.0 pyinstaller==6.22.3
bash tools/linux/build_wrapper.sh x86_64
```

Qt is packaged with PyInstaller in **onedir** mode so the application does
not extract a onefile Python runtime at each launch. GNOME uses the host
Python 3 + PyGObject/GTK 3 when installed; otherwise the bundled Qt GUI
is used. The installed game is downloaded on first launch; it is not bundled
inside the small launcher AppImage.

To run from source, use `python3 -m wrapper.launch --root DIR`. For tests:
`python3 -m unittest discover -s wrapper/tests -v`.
