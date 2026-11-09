#!/usr/bin/env bash
# Small portable launcher, without bundling or building a fork of the upstream game runtime.
set -eo pipefail
cd "$(dirname "$0")/../.."
ARCH=$(uname -m)
if [ "$#" -gt 0 ]; then ARCH=$1; fi
if [ "$ARCH" != x86_64 ]; then
    echo "Only x86_64 is packaged with a verified Qt frontend so far" >&2
    exit 2
fi
PYTHON=python3
if [ -n "$PYTHON_WRAPPER" ]; then PYTHON=$PYTHON_WRAPPER; fi
"$PYTHON" -c 'import PySide6, PyInstaller' >/dev/null || {
    echo "The builder needs PySide6 and PyInstaller" >&2
    exit 2
}
ROOT=build/wrapper
APPDIR="$ROOT/AppDir"
mkdir -p "$ROOT" build/dist
rm -rf "$APPDIR"
"$PYTHON" -m PyInstaller --noconfirm --clean --onedir --name swqt --distpath "$ROOT/dist" \
    --workpath "$ROOT/work" --specpath "$ROOT" wrapper/qt_entry.py
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/wrapper"
cp -R "$ROOT/dist/swqt" "$APPDIR/usr/bin/"
cp wrapper/__init__.py wrapper/core.py wrapper/launch.py wrapper/gtk_ui.py "$APPDIR/usr/share/wrapper/"
cp tools/linux/wrapper_AppRun "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"
cp tools/linux/wrapper.desktop "$APPDIR/savagewind-portable.desktop"
cp docs/icon/icon_1024.png "$APPDIR/savagewind-portable.png"
ln -s savagewind-portable.png "$APPDIR/.DirIcon"
"$APPDIR/usr/bin/swqt/swqt" --root "$PWD/$ROOT" --selftest
APPIMAGETOOL=build/linux/src/appimagetool-x86_64.AppImage
if [ ! -f "$APPIMAGETOOL" ]; then
    mkdir -p "$(dirname "$APPIMAGETOOL")"
    curl -fL --retry 3 \
        https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage \
        -o "$APPIMAGETOOL"
fi
echo 'ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0' \
    "$APPIMAGETOOL" | sha256sum --check --status
chmod +x "$APPIMAGETOOL"
OUTPUT="build/dist/SavageWind-Portable-$ARCH.AppImage"
APPIMAGE_EXTRACT_AND_RUN=1 ARCH="$ARCH" "$APPIMAGETOOL" --no-appstream "$APPDIR" "$OUTPUT"
echo "Created $OUTPUT"
