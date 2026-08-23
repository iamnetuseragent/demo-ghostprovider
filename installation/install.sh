#!/usr/bin/env bash
# demo-ghostprovider (Rust edition) installer.
#
# What this does NOT do:
#   - never asks for tokens or credentials (the demo repo is public)
#   - never reads from /dev/tty behind your back; prompts only when both
#     stdin and stdout are a real terminal, otherwise prints instructions
#   - never contacts anything except the git hosts listed below
#
# Requirements: Linux with systemd user session, git, cargo (rustup works).
set -euo pipefail

# Primary origin first, mirror second. Both serve identical content;
# the mirror exists so one takedown does not orphan the project.
REPOS=(
  "https://github.com/iamnetuseragent/demo-ghostprovider.git"
  "https://codeberg.org/iamnetuseragent/demo-ghostprovider.git"
)

SRC_DIR="${HOME}/.local/share/demo-ghostprovider"
BIN_DIR="${HOME}/.local/bin"
BIN_NAME="demo-ghostprovider"

info() { printf "\033[36m%s\033[0m\n" "$*"; }
ok()   { printf "\033[32m%s\033[0m\n" "$*"; }
warn() { printf "\033[33m%s\033[0m\n" "$*"; }
err()  { printf "\033[31m%s\033[0m\n" "$*" >&2; exit 1; }

case "$(uname -s)" in
  Linux*) ;;
  *) err "This installer supports Linux only. Detected: $(uname -s)" ;;
esac

command -v systemctl >/dev/null || err "systemctl not found. demo-ghostprovider requires systemd."
systemctl --user is-system-running >/dev/null 2>&1 \
  || err "Your systemd *user session* is not running (see 'systemctl --user is-system-running')."
command -v git      >/dev/null || err "git not found. Install git first."
command -v cargo    >/dev/null || err "cargo not found. Install Rust via https://rustup.rs (or your distro's rust+cargo)."

info "=> Fetching demo-ghostprovider sources..."
clone_ok=""
if [ -d "$SRC_DIR/.git" ]; then
  info "   existing checkout found, updating..."
  if git -C "$SRC_DIR" pull --ff-only; then
    clone_ok=yes
  else
    warn "   update failed; rebuilding from current sources."
    clone_ok=yes
  fi
else
  for repo in "${REPOS[@]}"; do
    host="$(printf '%s' "$repo" | sed -E 's#https://([^/]+)/.*#\1#')"
    info "   trying $host ..."
    if git clone --depth=1 "$repo" "$SRC_DIR"; then
      clone_ok=yes
      break
    fi
    warn "   $host unreachable or refused."
  done
  [ -n "$clone_ok" ] || err "All mirrors failed. Check your network and try again."
fi

info "=> Building (release profile, ~1 minute)..."
cargo build --release --manifest-path "$SRC_DIR/Cargo.toml"

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
