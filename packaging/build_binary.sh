#!/usr/bin/env bash
# Build a single-file, zero-dependency FARSHORE executable with PyInstaller.
#
# The whole point: a player downloads ONE file and runs it — no Python, no
# uv, no `uv sync`. PyInstaller embeds CPython + textual + the game into a
# self-contained binary (the same idea as Bun's `--compile`, which is how
# opencode and Claude Code ship; ours is ~17 MB vs their ~240 MB because a
# Python runtime is far lighter than an embedded JS engine).
#
# One binary per OS/arch — PyInstaller cannot cross-compile, so this runs
# once on each target runner (see .github/workflows/release.yml). Locally it
# builds for the host platform only.
#
# Usage:  packaging/build_binary.sh
# Output: dist/farshore   (plus a dist/farshore-<os>-<arch>.tar.gz for release)
set -euo pipefail

cd "$(dirname "$0")/.."

NAME=farshore

# OS/arch tag for the release asset name, matching install.sh's detection.
os=$(uname -s | tr '[:upper:]' '[:lower:]')
case "$os" in
    darwin*) os=darwin ;;
    linux*)  os=linux ;;
    mingw*|msys*|cygwin*) os=windows ;;
esac
arch=$(uname -m)
case "$arch" in
    aarch64|arm64) arch=arm64 ;;
    x86_64|amd64)  arch=x64 ;;
esac
target="$os-$arch"

echo "building $NAME for $target ..."

# --collect-all textual: textual loads its default CSS (.tcss) via
# importlib.resources; a naive build ships the code but not those data files
# and the TUI crashes on first mount. This flag pulls in its data + submodules.
# The game's own CSS is inline Python strings, so no --collect for `empire`
# data is needed — just its submodules.
uv run --with pyinstaller pyinstaller \
    --onefile \
    --name "$NAME" \
    --paths src \
    --collect-all textual \
    --collect-submodules empire \
    --distpath dist \
    --workpath build/pyinstaller \
    --specpath build/pyinstaller \
    --noconfirm --clean \
    src/empire/__main__.py

# Smoke-test the artifact runs standalone (no Python env) before packaging it.
env -u VIRTUAL_ENV -u PYTHONPATH -u PYTHONHOME \
    "dist/$NAME" dump-map --profile SMALL --seed 0 >/dev/null
echo "smoke test passed: dist/$NAME runs standalone"

# Package as the release asset (tar.gz on unix, zip on windows) — install.sh
# downloads and extracts exactly this name.
if [ "$os" = "windows" ]; then
    ( cd dist && 7z a -tzip "$NAME-$target.zip" "$NAME.exe" >/dev/null )
    echo "release asset: dist/$NAME-$target.zip"
else
    ( cd dist && tar -czf "$NAME-$target.tar.gz" "$NAME" )
    echo "release asset: dist/$NAME-$target.tar.gz"
fi

ls -lh "dist/$NAME"*
