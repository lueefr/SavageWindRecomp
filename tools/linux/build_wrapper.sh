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
"$PYTHON" -c 'import PySide6, PyInstaller, cryptography, certifi' >/dev/null || {
    echo "The builder needs PySide6, PyInstaller, cryptography and certifi" >&2
    exit 2
}
ROOT=build/wrapper
APPDIR="$ROOT/AppDir"
mkdir -p "$ROOT" build/dist
rm -rf "$APPDIR"
"$PYTHON" -m PyInstaller --noconfirm --clean --onedir --name swqt --collect-data certifi --distpath "$ROOT/dist" \
    --workpath "$ROOT/work" --specpath "$ROOT" wrapper/qt_entry.py
# Ship one isolated import helper; the installation never depends on system Python modules.
"$PYTHON" -m PyInstaller --noconfirm --clean --onefile --name swextract \
    --paths tools/linux/nsp_vendor --distpath "$ROOT/dist" \
    --workpath "$ROOT/extract-work" --specpath "$ROOT" tools/linux/import_game.py
"$PYTHON" tools/build_hactool.py --src "$PWD/$ROOT/hactool-src" --out "$PWD/$ROOT/hactool"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/lib" "$APPDIR/usr/share/wrapper"
# Host GTK Python does not necessarily have certifi installed; ship the same CA roots.
"$PYTHON" -c 'import certifi, shutil, sys; shutil.copyfile(certifi.where(), sys.argv[1])' \
    "$APPDIR/usr/share/wrapper/ca-bundle.pem"
cp -R "$ROOT/dist/swqt" "$APPDIR/usr/bin/"
cp "$ROOT/dist/swextract" "$ROOT/hactool/hactool" "$APPDIR/usr/bin/"
cp "$ROOT/hactool/LICENSE" "$APPDIR/usr/bin/hactool-LICENSE.txt"
cp tools/linux/nsp_vendor/LICENSE "$APPDIR/usr/bin/nsp-extractor-LICENSE.txt"
# hactool can link dynamically to mbedTLS; bundle only those runtime libraries.
ldd "$APPDIR/usr/bin/hactool" | awk '/=> \/\// { print $3 }' | while read -r lib; do
    case "$(basename "$lib")" in libmbed*.so*) cp -L "$lib" "$APPDIR/usr/lib/" ;; esac
done
patchelf --set-rpath '$ORIGIN/../lib' "$APPDIR/usr/bin/hactool"
cp wrapper/__init__.py wrapper/core.py wrapper/build_progress.py wrapper/launch.py wrapper/gtk_ui.py \
    wrapper/migration.py wrapper/rollback.py wrapper/pr_build.py wrapper/deps.py wrapper/i18n.py \
    wrapper/updates.py wrapper/runtime_tracking.py wrapper/pause.py wrapper/launcher_updates.py "$APPDIR/usr/share/wrapper/"
# Record the actual checkout and release tag so preview builds cannot be mistaken for old releases.
PORTABLE_COMMIT=$(git rev-parse HEAD 2>/dev/null || true)
"$PYTHON" - "$APPDIR/usr/share/wrapper/launcher-version.json" "${SAVAGEWIND_PORTABLE_TAG:-}" "$PORTABLE_COMMIT" <<'PY'
import json
import re
import sys
from pathlib import Path

tag = sys.argv[2] if re.fullmatch(r"portable-v[0-9]+\.[0-9]+\.[0-9]+", sys.argv[2]) else None
commit = sys.argv[3] if re.fullmatch(r"[0-9a-f]{40}", sys.argv[3]) else None
Path(sys.argv[1]).write_text(json.dumps({"tag": tag, "commit": commit}, indent=2) + "\n")
PY
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
