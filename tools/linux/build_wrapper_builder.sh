#!/usr/bin/env bash
# Prepare the offline Linux compiler for the portable AppImage, on the CI runner only.
set -euo pipefail
cd "$(dirname "$0")/../.."
ARCH=x86_64
if [ "$#" -gt 0 ]; then ARCH=$1; fi
[ "$ARCH" = x86_64 ] || { echo "The embedded builder supports x86_64 only" >&2; exit 2; }

ROOT="$PWD/build/wrapper/builder"
SOURCE="$ROOT/upstream"
OUTPUT="$ROOT/bundle"
mkdir -p "$ROOT"
rm -rf "$SOURCE" "$OUTPUT"
mkdir -p "$OUTPUT/rootfs" "$OUTPUT/seed/build" "$OUTPUT/libs"

# The source checkout and all tool downloads occur in CI, never on the player's computer.
git clone --depth 1 --branch test https://github.com/Revivedx/SavageWindRecomp.git "$SOURCE"
git -C "$SOURCE" rev-parse HEAD > "$OUTPUT/upstream-commit"
cp "$SOURCE/tools/linux/Containerfile" "$ROOT/Containerfile"
# The Ubuntu security mirror may lag behind the package index during rolling updates.
# Use the same archive mirror for all Jammy suites to prevent transient 404 packages.
sed -i '/^FROM docker.io\/library\/ubuntu:22.04$/a RUN sed -i "s|http://security.ubuntu.com/ubuntu|http://archive.ubuntu.com/ubuntu|g" /etc/apt/sources.list \&\& rm -rf /var/lib/apt/lists/*' "$ROOT/Containerfile"
cat >> "$ROOT/Containerfile" <<'DOCKER'
# The launcher ships bubblewrap and a complete, preprepared source build toolchain.
RUN apt-get update && apt-get install -y --no-install-recommends bubblewrap \
    && rm -rf /var/lib/apt/lists/*
DOCKER
IMAGE="savagewind-portable-builder:embedded"
docker build --platform linux/amd64 -t "$IMAGE" -f "$ROOT/Containerfile" "$SOURCE/tools/linux"

# Build game-independent dependencies and cache the exact tools used by the upstream packager.
docker run --rm --platform linux/amd64 \
    -v "$SOURCE:/src" -w /src "$IMAGE" \
    bash -c 'tools/linux/gen_helper.sh x86_64 && tools/linux/package.sh x86_64'

for part in linux/src linux/x86_64/deps linux/x86_64/gen-venv zig zig-dl; do
    src="$SOURCE/build/$part"
    [ -d "$src" ] || { echo "Missing build cache: $part" >&2; exit 1; }
    mkdir -p "$OUTPUT/seed/build/$(dirname "$part")"
    cp -a "$src" "$OUTPUT/seed/build/$part"
done

# Export a read-only Ubuntu 22.04 toolchain, not the container engine.
container=$(docker create --platform linux/amd64 "$IMAGE")
trap 'docker rm -f "$container" >/dev/null 2>&1 || true' EXIT
docker export "$container" | tar -x --no-same-owner -C "$OUTPUT/rootfs" \
    --exclude='./dev/*' --exclude='./proc/*' --exclude='./sys/*'
docker rm -f "$container" >/dev/null
trap - EXIT

# Keep the guest runtime's shared libraries but omit documentation and package-manager caches.
rm -rf "$OUTPUT/rootfs/usr/share/doc" "$OUTPUT/rootfs/usr/share/man" \
       "$OUTPUT/rootfs/usr/share/info" "$OUTPUT/rootfs/usr/share/locale" \
       "$OUTPUT/rootfs/var/cache/apt" "$OUTPUT/rootfs/var/lib/apt/lists" \
       "$OUTPUT/rootfs/usr/share/icons" "$OUTPUT/rootfs/usr/share/backgrounds"
cp "$OUTPUT/rootfs/usr/bin/bwrap" "$OUTPUT/bwrap"

# Bundle bwrap's host-side libraries instead of asking players to install packages.
for name in libcap.so.2 libseccomp.so.2 libattr.so.1; do
    found=$(find "$OUTPUT/rootfs/lib" "$OUTPUT/rootfs/usr/lib" -name "$name" -print -quit 2>/dev/null || true)
    if [ -n "$found" ]; then cp -L "$found" "$OUTPUT/libs/$name"; fi
done
[ -x "$OUTPUT/bwrap" ] || { echo "Missing bubblewrap executable" >&2; exit 1; }
[ -x "$OUTPUT/rootfs/usr/bin/clang" ] || { echo "Missing Clang compiler" >&2; exit 1; }
[ -f "$OUTPUT/seed/build/zig/zig" ] || { echo "Missing bundled Zig compiler" >&2; exit 1; }
[ -f "$OUTPUT/seed/build/linux/x86_64/deps/lib/libSDL3.so" ] || { echo "Missing bundled SDL3 libraries" >&2; exit 1; }

du -sh "$OUTPUT/rootfs" "$OUTPUT/seed"
echo "Prepared embedded Linux builder for $(cat "$OUTPUT/upstream-commit")"
