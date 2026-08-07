#!/usr/bin/env bash
# Uninstall the paid ghostprovider installed via install-ghostprovider.sh.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/iamnetuseragent/demo-ghostprovider/main/installation/uninstall-ghostprovider.sh | bash
set -euo pipefail

INSTALL_DIR="${HOME}/.local/share/ghostprovider"
BIN_DIR="${HOME}/.local/bin"
BIN_NAME="ghostprovider"
CONFIG_DIR="${HOME}/.config/ghostprovider"

info()  { printf "\033[36m%s\033[0m\n" "$*"; }
ok()    { printf "\033[32m%s\033[0m\n" "$*"; }

# ── 1. Stop ghost-* services ──
info "=> Stopping ghost-* services..."
if command -v systemctl &>/dev/null; then
  systemctl --user list-units --type=service --plain --no-legend 2>/dev/null \
    | awk '{print $1}' | grep '^ghost-' | while read -r unit; do
      systemctl --user stop "$unit" 2>/dev/null || true
      systemctl --user disable "$unit" 2>/dev/null || true
      rm -f "${HOME}/.config/systemd/user/${unit}"
    done || true
  systemctl --user daemon-reload 2>/dev/null || true
fi

# ── 2. Remove installation directory ──
if [ -d "$INSTALL_DIR" ]; then
  info "=> Removing ${INSTALL_DIR}..."
  rm -rf "$INSTALL_DIR"
fi

# ── 3. Remove launcher script ──
if [ -f "$BIN_DIR/$BIN_NAME" ]; then
  info "=> Removing ${BIN_DIR}/${BIN_NAME}..."
  rm -f "$BIN_DIR/$BIN_NAME"
fi

# ── 4. Remove config / state ──
if [ -d "$CONFIG_DIR" ]; then
  info "=> Removing ${CONFIG_DIR}..."
  rm -rf "$CONFIG_DIR"
fi

ok ""
ok "ghostprovider has been uninstalled."
ok ""
