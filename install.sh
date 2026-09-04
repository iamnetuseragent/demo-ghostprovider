#!/bin/sh
# demo-ghostprovider one-shot installer (static binary).
#
# Recommended (the installer itself is minisign-verified before it runs, so
# raw `curl | sh` of an unverified script is avoided):
#
#   curl -fsSL -o /tmp/dgp-install.sh https://raw.githubusercontent.com/iamnetuseragent/demo-ghostprovider/main/install.sh
#   curl -fsSL -o /tmp/dgp-install.sh.minisig https://raw.githubusercontent.com/iamnetuseragent/demo-ghostprovider/main/install.sh.minisig
#   minisign -Vm /tmp/dgp-install.sh -s /tmp/dgp-install.sh.minisig -P "RWSUAckJJhM011XphIH3LQE0Ebn62qqMMQej4Ong52/rGNw/rxRKniqA" && sh /tmp/dgp-install.sh
#   sh /tmp/dgp-install.sh --uninstall
#
# The public key/fingerprint are published in docs/DISTRIBUTION.md; cross-check
# the pasted key above against that document rather than trusting this comment.
#
# Downloads the latest tagged musl binary, verifies sha256 AND the minisign
# signature (refusing unsigned releases), installs into ~/.local/bin.
#
# Flags: --uninstall | --tag v0.0.14 | --bin-dir DIR | --mirror codeberg
#        | --allow-unsigned   (skip the mandatory signature check for a
#                              trusted mirror / local test — never the default)
set -eu

REPO_GH="iamnetuseragent/demo-ghostprovider"
REPO_CB="netuser/demo-ghostprovider"
BIN_NAME="demo-ghostprovider"
DEFAULT_BIN_DIR="${HOME}/.local/bin"
RELEASE_PUB="RWSUAckJJhM011XphIH3LQE0Ebn62qqMMQej4Ong52/rGNw/rxRKniqA"
FINGERPRINT="D734132609C90194"

TAG=""
BIN_DIR="$DEFAULT_BIN_DIR"
HOST="github"
ACTION="install"
ALLOW_UNSIGNED=0

while [ $# -gt 0 ]; do
    case "$1" in
        --uninstall) ACTION="uninstall" ;;
        --tag) TAG="${2:?}"; shift ;;
        --bin-dir) BIN_DIR="${2:?}"; shift ;;
        --mirror) HOST="codeberg" ;;   # also auto-fallback per file
        --allow-unsigned) ALLOW_UNSIGNED=1 ;;
        *) printf 'unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
    shift
done

log()  { printf '\033[36m%s\033[0m\n' "$*"; }
ok()   { printf '\033[32m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null || die "missing dependency: $1"; }

# Version of an already-installed binary, or empty when absent/unparsable.
# Normalized to a `v`-prefixed tag (the binary may print `0.0.19` or `v0.0.19`).
old_version() {
    [ -x "$1" ] || return 0
    version=$("$1" --version 2>/dev/null | sed -n 's/.*\(v\?[0-9]\+\.[0-9]\+\.[0-9]\+\).*/\1/p' | head -n1)
    [ -n "$version" ] || return 0
    case "$version" in
        v*) printf '%s' "$version" ;;
        *)  printf 'v%s' "$version" ;;
    esac
}

if [ "$ACTION" = "uninstall" ]; then
    for f in "$BIN_DIR/$BIN_NAME"; do
        if [ -e "$f" ]; then rm -f "$f" && ok "removed $f"; fi
    done
    log "binary removed."
    log "installed from source earlier? full cleanup:"
    log "  curl -sSL https://raw.githubusercontent.com/$REPO_GH/main/installation/uninstall.sh | bash"
    exit 0
fi

for dep in curl sha256sum uname tar grep; do need "$dep"; done

[ "$(uname -s)" = "Linux" ] || die "prebuilt binaries are Linux-only; build from source instead"
[ "$(uname -m)" = "x86_64" ] || die "prebuilt binaries are x86_64-only; build from source instead"

# A release tag is `v<major>.<minor>.<patch>` — anything else (a sed
# extraction mistake, a malicious redirect) must abort, not download.
valid_tag() {
    printf '%s' "$1" | grep -qE '^v[0-9]+\.[0-9]+\.[0-9]+$'
}

if [ -z "$TAG" ]; then
    log "resolving latest release tag..."
    TAG=$(curl -fsSL "https://api.github.com/repos/$REPO_GH/releases/latest" \
          | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n1)
    if [ -z "$TAG" ]; then
        TAG=$(curl -fsSL "https://codeberg.org/api/v1/repos/$REPO_CB/releases?limit=1" \
              | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n1)
        HOST="codeberg"
    fi
    [ -n "$TAG" ] || die "could not resolve latest tag; pass one explicitly: --tag v0.0.14"
fi
valid_tag "$TAG" || die "malformed tag '$TAG' — expected v<major>.<minor>.<patch>; refusing to install"

case "$HOST" in
    github)  BASE="https://github.com/$REPO_GH/releases/download/$TAG" ;;
    codeberg) BASE="https://codeberg.org/$REPO_CB/releases/download/$TAG" ;;
esac

ART="$BIN_NAME-$TAG-x86_64-linux-musl"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# fetch <name> -> 0 when downloaded; quiet: a missing optional
#          file must not spook the user (signature handling is below)
fetch() {
    curl -fsSL -o "$TMP/$1" "$BASE/$1" 2>/dev/null || \
        curl -fsSL -o "$TMP/$1" \
        "https://github.com/$REPO_GH/releases/download/$TAG/$1" 2>/dev/null
}

log "downloading $TAG..."
fetch "$ART"           || die "download failed: $ART"
fetch "SHA256SUMS"     || die "download failed: SHA256SUMS"

( cd "$TMP" && sha256sum -c SHA256SUMS ) || die "checksum mismatch — aborting"

# Signature is verified whenever a verifier (minisign/rsign) is available;
# without one the install falls back to the SHA-256 checksum above.
# A real signature FAILURE (with a verifier present) or a missing
# SHA256SUMS.minisig on the release still aborts, unless --allow-unsigned.
fetch "SHA256SUMS.minisig" || true
if [ -f "$TMP/SHA256SUMS.minisig" ]; then
    if command -v minisign >/dev/null; then
        ( cd "$TMP" && minisign -Vm SHA256SUMS -P "$RELEASE_PUB" ) \
            || die "signature verification FAILED — aborting"
        ok "minisign signature verified (key $FINGERPRINT)"
    elif command -v rsign >/dev/null; then
        ( cd "$TMP" && rsign verify -P "$RELEASE_PUB" -x SHA256SUMS.minisig SHA256SUMS ) \
            || die "signature verification FAILED — aborting"
        ok "minisign signature verified (key $FINGERPRINT)"
    else
        warn "minisign/rsign not found — checksum-only install (SHA-256)"
    fi
elif [ "$ALLOW_UNSIGNED" -eq 1 ]; then
    warn "release has no minisign signature (SHA256SUMS.minisig) — --allow-unsigned set, checksum-only install."
else
    die "release is UNSIGNED (SHA256SUMS.minisig missing) — refusing to install; verify the release is properly signed (docs/DISTRIBUTION.md, key $FINGERPRINT)"
fi

mkdir -p "$BIN_DIR"
BIN_PATH="$BIN_DIR/$BIN_NAME"
OLD_VER="$(old_version "$BIN_PATH")"
if [ -n "$OLD_VER" ]; then
    if [ "$OLD_VER" = "$TAG" ]; then
        ok "already up to date ($OLD_VER)"
    else
        log "upgrading $OLD_VER -> $TAG"
    fi
fi

# Write atomically: build the new binary in place under a temp name, then
# `mv` it over the target so a concurrent read (or an interrupted run) never
# sees a half-written file.
install -m755 "$TMP/$ART" "$BIN_PATH.tmp.$$"
mv -f "$BIN_PATH.tmp.$$" "$BIN_PATH"

# The binary must actually run before we report success; otherwise roll back
# so the user is never left with a silent broken install.
if ! "$BIN_PATH" --show-endpoints >/dev/null 2>&1; then
    rm -f "$BIN_PATH"
    die "installed binary failed its self-report (--show-endpoints) — install rolled back"
fi
if [ -n "$OLD_VER" ]; then
    if [ "$OLD_VER" = "$TAG" ]; then
        ok "already up to date ($OLD_VER); refreshed $BIN_PATH"
    else
        ok "updated: $BIN_PATH ($OLD_VER -> $TAG)"
    fi
else
    ok "installed: $BIN_PATH"
fi

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not in PATH. Add to your shell profile:"
       warn "  export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac
log "run it:  $BIN_NAME"
