#!/usr/bin/env bash
# Build the clean public FARSHORE release tree from this repo.
#
# ALLOWLIST export: copies exactly the files that ship, into a fresh
# directory with a fresh git history (one initial commit). The internal
# repo's history, planning/ design notes, and lab probes never leave home.
#
# Usage:  scripts/export_release.sh [target-dir]     (default: ../farshore)
# Then:   cd <target> && git remote add origin <public-url> && git push -u origin main
set -euo pipefail

cd "$(dirname "$0")/.."
TARGET="${1:-../farshore}"

if [ -e "$TARGET" ]; then
    echo "error: $TARGET already exists — remove it or pick another dir" >&2
    exit 1
fi

# ---- what ships ------------------------------------------------------------
SHIP_DIRS=(src tests docs)
SHIP_FILES=(README.md LICENSE pyproject.toml Makefile uv.lock .gitignore)
# Lab equipment that stays home (dev probes; _arena/_naval_arena are test
# fixtures and _validation backs the CLI gate, so those three ship).
STRIP=(
    src/empire/_amphib_probe.py
    src/empire/_aggr_ab.py
    src/empire/_bias_check.py
    src/empire/_econ_trace.py
    src/empire/_eval_curves.py
)

mkdir -p "$TARGET"
for d in "${SHIP_DIRS[@]}"; do
    cp -r "$d" "$TARGET/"
done
find "$TARGET" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$TARGET" -name '*.pyc' -delete
for f in "${SHIP_FILES[@]}"; do
    cp "$f" "$TARGET/"
done
for f in "${STRIP[@]}"; do
    rm -f "$TARGET/$f"
done

# ---- sanity: nothing internal leaked ----------------------------------------
LEAKS=$(grep -rln "planning/" "$TARGET/src" "$TARGET/tests" --include='*.py' || true)
if [ -n "$LEAKS" ]; then
    echo "error: shipped code still references planning/:" >&2
    echo "$LEAKS" >&2
    exit 1
fi
for f in "${STRIP[@]}"; do
    if grep -rq "$(basename "$f" .py)" "$TARGET/src" "$TARGET/tests" 2>/dev/null; then
        echo "error: shipped tree references stripped probe $f" >&2
        exit 1
    fi
done

# ---- fresh history -----------------------------------------------------------
git -C "$TARGET" init -q -b main
git -C "$TARGET" add -A
git -C "$TARGET" commit -q -m "FARSHORE — Terra Incognita: initial public release"

# ---- verify the export stands alone ------------------------------------------
echo "verifying export (uv sync + pytest + ruff)..."
(
    cd "$TARGET"
    uv sync -q
    uv run python -m pytest -q
    uv run ruff check src tests
)

echo
echo "release tree ready: $TARGET"
git -C "$TARGET" log --oneline
