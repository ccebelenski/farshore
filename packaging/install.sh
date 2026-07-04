#!/usr/bin/env bash
# FARSHORE installer — download the right prebuilt binary and put it on PATH.
#
#   curl -fsSL https://raw.githubusercontent.com/OWNER/farshore/main/packaging/install.sh | bash
#
# No Python, no uv, no build. This detects your OS/arch, pulls the matching
# single-file binary from GitHub Releases, and drops it in ~/.local/bin.
# (Structure cribbed from opencode's `install`; layout follows Claude Code's
# versioned-dir + symlink pattern so upgrades are atomic and rollback is easy.)
#
# Flags:
#   --version <v>     install a specific release (default: latest)
#   --binary <path>   install from a LOCAL binary instead of downloading
#                     (used to test this script before any release exists)
#   --no-modify-path  don't touch shell rc files
set -euo pipefail

# Fill this in at publish time (or override: FARSHORE_REPO=you/farshore ...).
REPO="${FARSHORE_REPO:-ccebelenski/farshore}"
APP=farshore

RED='\033[0;31m'; DIM='\033[0;2m'; NC='\033[0m'
err() { echo -e "${RED}error:${NC} $1" >&2; exit 1; }

version=""; binary_path=""; no_modify_path=false
while [ $# -gt 0 ]; do
    case "$1" in
        --version) version="${2:?--version needs a value}"; shift 2 ;;
        --binary)  binary_path="${2:?--binary needs a path}"; shift 2 ;;
        --no-modify-path) no_modify_path=true; shift ;;
        -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
        *) echo "warning: unknown option '$1'" >&2; shift ;;
    esac
done

# Where the versioned binaries live, and the stable symlink that points at one.
LIB_DIR="$HOME/.local/share/$APP/versions"
BIN_DIR="$HOME/.local/bin"
mkdir -p "$LIB_DIR" "$BIN_DIR"

install_binary() {  # <src-file> <version-label>
    local src="$1" ver="$2"
    local dest="$LIB_DIR/$ver"
    install -m 755 "$src" "$dest"
    ln -sf "$dest" "$BIN_DIR/$APP"
    echo -e "${DIM}installed${NC} $APP $ver ${DIM}->${NC} $BIN_DIR/$APP"
}

if [ -n "$binary_path" ]; then
    [ -f "$binary_path" ] || err "no binary at $binary_path"
    install_binary "$binary_path" "local"
else
    # --- detect platform (matches build_binary.sh asset names) ---
    os=$(uname -s | tr '[:upper:]' '[:lower:]')
    case "$os" in darwin*) os=darwin ;; linux*) os=linux ;;
        mingw*|msys*|cygwin*) os=windows ;; *) err "unsupported OS: $os" ;; esac
    arch=$(uname -m)
    case "$arch" in aarch64|arm64) arch=arm64 ;; x86_64|amd64) arch=x64 ;;
        *) err "unsupported arch: $arch" ;; esac
    target="$os-$arch"

    ext=tar.gz; [ "$os" = windows ] && ext=zip
    asset="$APP-$target.$ext"

    if [ -z "$version" ]; then
        base="https://github.com/$REPO/releases/latest/download"
    else
        version="${version#v}"
        base="https://github.com/$REPO/releases/download/v$version"
    fi

    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    echo -e "${DIM}downloading${NC} $asset"
    curl -fsSL "$base/$asset" -o "$tmp/$asset" \
        || err "could not download $base/$asset (no such release/asset?)"
    if [ "$ext" = tar.gz ]; then tar -xzf "$tmp/$asset" -C "$tmp"
    else command -v unzip >/dev/null || err "unzip required"; unzip -q "$tmp/$asset" -d "$tmp"; fi
    install_binary "$tmp/$APP"* "${version:-latest}"
fi

# --- put ~/.local/bin on PATH if it isn't already ---
if [ "$no_modify_path" != true ] && [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    line="export PATH=\"$BIN_DIR:\$PATH\""
    case "$(basename "${SHELL:-bash}")" in
        zsh)  rc="$HOME/.zshrc" ;;
        fish) rc="$HOME/.config/fish/config.fish"; line="fish_add_path $BIN_DIR" ;;
        *)    rc="$HOME/.bashrc" ;;
    esac
    if [ -e "$rc" ] && grep -qF "$line" "$rc"; then
        :  # already added on a previous run; don't duplicate the line
    elif [ -w "$rc" ] || [ ! -e "$rc" ]; then
        printf '\n# %s\n%s\n' "$APP" "$line" >> "$rc"
        echo -e "${DIM}added${NC} $BIN_DIR ${DIM}to PATH in${NC} $rc ${DIM}(restart your shell)${NC}"
    else
        echo -e "${DIM}add this to your shell rc:${NC} $line"
    fi
fi

echo
echo "  FARSHORE installed.  Run:  $APP"
