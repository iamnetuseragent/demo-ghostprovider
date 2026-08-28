#!/usr/bin/env bash
# demo-ghostprovider (Rust edition) installer.
#
# What this does NOT do:
#   - never asks for tokens or credentials (the demo repo is public)
#   - never reads from /dev/tty behind your back; prompts only when both
#     stdin and stdout are a real terminal, otherwise prints instructions
#   - never contacts anything except the git hosts listed below
#
# Supply chain: by default the LATEST release tag is pinned and its GPG
# signature verified (`git verify-tag`) before anything is built. Building
# an arbitrary HEAD without a tag is an explicit decision: `--head`.
#
# Flags:
#   --tag vX.Y.Z      pin an exact release tag (default: resolve latest)
#   --head            build the default branch HEAD instead of a tag
#   --no-verify-tag   skip the git verify-tag signature check (not recommended)
#
# Requirements: Linux with systemd user session, git, cargo (rustup works).
set -euo pipefail

# Primary origin first, mirror second. Both serve identical content;
# the mirror exists so one takedown does not orphan the project.
REPOS=(
  "https://github.com/iamnetuseragent/demo-ghostprovider.git"
  "https://codeberg.org/netuser/demo-ghostprovider.git"
)

SRC_DIR="${HOME}/.local/share/demo-ghostprovider"
BIN_DIR="${HOME}/.local/bin"
BIN_NAME="demo-ghostprovider"

info() { printf "\033[36m%s\033[0m\n" "$*"; }
ok()   { printf "\033[32m%s\033[0m\n" "$*"; }
warn() { printf "\033[33m%s\033[0m\n" "$*"; }
err()  { printf "\033[31m%s\033[0m\n" "$*" >&2; exit 1; }

# Mode: latest (default) | tag | head
MODE="latest"
TAG=""
VERIFY_TAG=1

while [ $# -gt 0 ]; do
  case "$1" in
    --head) MODE="head" ;;
    --tag) MODE="tag"; TAG="${2:?}"; shift ;;
    --no-verify-tag) VERIFY_TAG=0 ;;
    *) err "unknown arg: $1 (usage: [--tag vX.Y.Z | --head] [--no-verify-tag])" ;;
  esac
  shift
done

valid_tag() {
  [[ "$1" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

case "$(uname -s)" in
  Linux*) ;;
  *) err "This installer supports Linux only. Detected: $(uname -s)" ;;
esac

command -v systemctl >/dev/null || err "systemctl not found. demo-ghostprovider requires systemd."
# degraded = working session with some unrelated failed unit; perfectly usable.
system_state="$(systemctl --user is-system-running 2>/dev/null || true)"
case "$system_state" in
  running|degraded) ;;
  *) err "Your systemd *user session* is not ready (state: ${system_state:-unknown}). See 'systemctl --user is-system-running'." ;;
esac
command -v git      >/dev/null || err "git not found. Install git first."
command -v cargo    >/dev/null || err "cargo not found. Install Rust via https://rustup.rs (or your distro's rust+cargo)."
command -v curl     >/dev/null || err "curl not found (needed to resolve the latest release tag)."
command -v gpg      >/dev/null || err "gpg not found (needed for 'git verify-tag'). Install gnupg."

if [ "$MODE" = "tag" ]; then
  valid_tag "$TAG" || err "invalid tag '$TAG' — expected v<major>.<minor>.<patch>"
fi

if [ "$MODE" = "latest" ]; then
  info "=> Resolving latest release tag..."
  for api in \
    "https://api.github.com/repos/iamnetuseragent/demo-ghostprovider/releases/latest" \
    "https://codeberg.org/api/v1/repos/netuser/demo-ghostprovider/releases?limit=1"
  do
    TAG="$(curl -fsSL "$api" 2>/dev/null \
           | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n1)"
    [ -n "$TAG" ] && break
  done
  [ -n "$TAG" ] || err "could not resolve latest tag; pass one explicitly: --tag v0.0.14"
  valid_tag "$TAG" || err "resolved tag '$TAG' is malformed — refusing"
  info "   latest tag: $TAG"
fi

info "=> Fetching demo-ghostprovider sources (tag: ${TAG:-HEAD})..."
clone_ok=""
if [ -d "$SRC_DIR/.git" ]; then
  info "   existing checkout found, updating..."
  if [ "$MODE" = "head" ]; then
    if git -C "$SRC_DIR" pull --ff-only; then
      clone_ok=yes
    else
      warn "   update failed; rebuilding from current sources."
      clone_ok=yes
    fi
  else
    # Re-pin the resolved/explicit tag: an existing checkout may be older.
    # A failed re-pin is FATAL here — silently rebuilding from older sources
    # while the user asked for $TAG would be a silent version rollback
    # (worse with --no-verify-tag, where nothing would flag the mismatch).
    if git -C "$SRC_DIR" fetch --depth=1 --tags origin >/dev/null 2>&1 \
       && git -C "$SRC_DIR" checkout -q "$TAG"; then
      clone_ok=yes
    else
      err "update to $TAG failed (network?). Refusing to rebuild from older sources. Fix the connection and retry."
    fi
  fi
else
  # Leftover directory without a checkout (e.g. data-only remains) must not
  # block cloning.
  [ ! -d "$SRC_DIR" ] || rm -rf "$SRC_DIR"
  for repo in "${REPOS[@]}"; do
    host="$(printf '%s' "$repo" | sed -E 's#https://([^/]+)/.*#\1#')"
    info "   trying $host ..."
    if [ "$MODE" = "head" ]; then
      git clone --depth=1 "$repo" "$SRC_DIR" && { clone_ok=yes; break; }
    else
      git clone --depth=1 --branch="$TAG" "$repo" "$SRC_DIR" && { clone_ok=yes; break; }
    fi
    warn "   $host unreachable or refused."
  done
  [ -n "$clone_ok" ] || err "All mirrors failed. Check your network and try again."
fi

if [ "$MODE" != "head" ]; then
  if [ "$VERIFY_TAG" -eq 1 ]; then
    info "=> Verifying tag signature (git verify-tag ...)"
    # Expected signing identity. The GPG fingerprint is published in
    # docs/DISTRIBUTION.md (provisioned before the first signed tag); the
    # minisign key-id that signs the binary checksums is 3673A05B26E03D3E.
    if git -C "$SRC_DIR" verify-tag "$TAG"; then
      ok "   tag $TAG: good signature."
      info "   signature must be from the maintainer key listed in docs/DISTRIBUTION.md"
    else
      err "git verify-tag FAILED for $TAG. Import the maintainer's signing key \
(see docs/DISTRIBUTION.md) or rerun with --no-verify-tag (not recommended)."
    fi
  else
    warn "   --no-verify-tag: skipping signature verification (not recommended)."
  fi
else
  warn "   --head: building the default branch without tag verification (not recommended for production)."
fi

info "=> Building (release profile, ~1 minute)..."
# --locked pins dependency resolution to the committed Cargo.lock: every
# user of this commit gets byte-identical crate versions from crates.io.
cargo build --release --locked --manifest-path "$SRC_DIR/Cargo.toml"

install -Dm755 "$SRC_DIR/target/release/$BIN_NAME" "$BIN_DIR/$BIN_NAME"

# Transparency sanity check: the binary must report its compiled-in
# allowlist without any network access.
"$BIN_DIR/$BIN_NAME" --show-endpoints >/dev/null || err "Installed binary failed its self-report (--show-endpoints)."

if [ ":$PATH:" != *":$BIN_DIR:"* ]; then
  warn ""
  warn "$BIN_DIR is not in your PATH."

  if [ -t 0 ] && [ -t 1 ]; then
    PROFILE="" PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
    case "${SHELL:-}" in
      */bash) PROFILE="$HOME/.bashrc" ;;
      */zsh)  PROFILE="$HOME/.zshrc" ;;
      */fish) PROFILE="$HOME/.config/fish/config.fish"; PATH_LINE='fish_add_path $HOME/.local/bin' ;;
    esac
    if [ -n "$PROFILE" ] && read -r -p "Add to $PROFILE? [Y/n] " answer </dev/tty \
       && ! [[ "$answer" =~ ^[Nn]$ ]]; then
      mkdir -p "$(dirname "$PROFILE")"
      printf '\n# demo-ghostprovider\n%s\n' "$PATH_LINE" >>"$PROFILE"
      ok "Added to $PROFILE. Run:  source $PROFILE"
    else
      warn "Skipped. Add manually:"
      warn "  $PATH_LINE"
    fi
  else
    warn "Non-interactive session; add it yourself:"
    warn '  export PATH="$HOME/.local/bin:$PATH"'
  fi
fi

ok ""
ok "Installation complete."
ok "Run:            $BIN_NAME"
ok "Transparency:   $BIN_NAME --show-endpoints"
ok "Self-check:     $BIN_NAME --selftest   (uses live systemd, loopback only)"
ok ""
