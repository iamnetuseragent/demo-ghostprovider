#!/bin/sh
# demo-ghostprovider one-shot installer (static binary).
#
#   curl -sSL https://raw.githubusercontent.com/iamnetuseragent/demo-ghostprovider/main/install.sh | sh
#   curl -sSL https://raw.githubusercontent.com/iamnetuseragent/demo-ghostprovider/main/install.sh | sh -s -- --uninstall
#
# Downloads the latest tagged musl binary, verifies sha256 (always) and
# the minisign signature (when present), installs into ~/.local/bin.
#
# Flags: --uninstall | --tag v0.0.13 | --bin-dir DIR | --mirror codeberg
set -eu

REPO_GH="iamnetuseragent/demo-ghostprovider"
REPO_CB="netuser/demo-ghostprovider"
BIN_NAME="demo-ghostprovider"
DEFAULT_BIN_DIR="${HOME}/.local/bin"
RELEASE_PUB="RWQ+PeAmW6BzNqV8Io2xcC1hUxoJxffAGBg/o2YXsU9DZ6I3I4ivWDv3"
FINGERPRINT="3673A05B26E03D3E"

TAG=""
BIN_DIR="$DEFAULT_BIN_DIR"
HOST="github"
ACTION="install"

while [ $# -gt 0 ]; do
    case "$1" in
        --uninstall) ACTION="uninstall" ;;
        --tag) TAG="${2:?}"; shift ;;
        --bin-dir) BIN_DIR="${2:?}"; shift ;;
        --mirror) HOST="codeberg" ;;   # also auto-fallback per file
        *) printf 'unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
    shift
done

log()  { printf '\033[36m%s\033[0m\n' "$*"; }
ok()   { printf '\033[32m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null || die "missing dependency: $1"; }

if [ "$ACTION" = "uninstall" ]; then
    for f in "$BIN_DIR/$BIN_NAME"; do
        if [ -e "$f" ]; then rm -f "$f" && ok "removed $f"; fi
    done
    log "binary removed."
    log "installed from source earlier? full cleanup:"
    log "  curl -sSL https://raw.githubusercontent.com/$REPO_GH/main/installation/uninstall.sh | bash"
    exit 0
fi

for dep in curl sha256sum uname tar; do need "$dep"; done

[ "$(uname -s)" = "Linux" ] || die "prebuilt binaries are Linux-only; build from source instead"
[ "$(uname -m)" = "x86_64" ] || die "prebuilt binaries are x86_64-only; build from source instead"

if [ -z "$TAG" ]; then
    log "resolving latest release tag..."
    TAG=$(curl -fsSL "https://api.github.com/repos/$REPO_GH/releases/latest" \
          | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n1)
    if [ -z "$TAG" ]; then
        TAG=$(curl -fsSL "https://codeberg.org/api/v1/repos/$REPO_CB/releases?limit=1" \
              | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n1)
        HOST="codeberg"
    fi
    [ -n "$TAG" ] || die "could not resolve latest tag; pass one explicitly: --tag v0.0.13"
fi

case "$HOST" in
    github)  BASE="https://github.com/$REPO_GH/releases/download/$TAG" ;;
    codeberg) BASE="https://codeberg.org/$REPO_CB/releases/download/$TAG" ;;
esac

ART="$BIN_NAME-$TAG-x86_64-linux-musl"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

fetch() { # fetch <name> -> 0 when downloaded
    curl -fsSL -o "$TMP/$1" "$BASE/$1" || curl -fsSL -o "$TMP/$1" \
        "https://github.com/$REPO_GH/releases/download/$TAG/$1"
}

log "downloading $TAG..."
fetch "$ART"           || die "download failed: $ART"
fetch "SHA256SUMS"     || die "download failed: SHA256SUMS"

( cd "$TMP" && sha256sum -c SHA256SUMS ) || die "checksum mismatch — aborting"

if fetch "SHA256SUMS.minisig"; then
    need minisign
    ( cd "$TMP" && minisign -Vm SHA256SUMS -P "$RELEASE_PUB" ) \
        || die "signature verification FAILED — aborting"
    ok "minisign signature verified (key $FINGERPRINT)"
else
    warn "release is UNSIGNED (SHA256SUMS.minisig missing) — verified by checksum only."
    warn "expected once signing secrets are configured; see docs/DISTRIBUTION.md ($FINGERPRINT)"
fi

mkdir -p "$BIN_DIR"
install -m755 "$TMP/$ART" "$BIN_DIR/$BIN_NAME"
ok "installed: $BIN_DIR/$BIN_NAME"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not in PATH. Add to your shell profile:"
       warn "  export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac
log "run it:  $BIN_NAME"
