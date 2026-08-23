#!/usr/bin/env bash
# demo-ghostprovider (Rust edition) uninstaller.
#
# Removes everything the installer/deployer created:
#   - demo-* systemd user units (stopped, disabled, unit files deleted)
#   - the launcher binary
#   - the installation directory (source checkout + build + cloned services)
#   - the state directory (deploy registry, net log, per-service secrets)
#
# Interactive sessions get one confirmation (default: yes).
# Piped runs (`curl | bash`) proceed without prompting.
set -euo pipefail

INSTALL_DIR="${HOME}/.local/share/demo-ghostprovider"
BIN_DIR="${HOME}/.local/bin"
BIN_NAME="demo-ghostprovider"
STATE_DIR="${HOME}/.local/state/demo-ghostprovider"
UNIT_DIR="${HOME}/.config/systemd/user"

info() { printf "\033[36m%s\033[0m\n" "$*"; }
ok()   { printf "\033[32m%s\033[0m\n" "$*"; }
warn() { printf "\033[33m%s\033[0m\n" "$*"; }

confirm() {
    # confirm <question> -> 0 yes / 1 no; auto-yes when stdin is not a tty
    [ -t 0 ] || return 0
    local answer
    read -r -p "$1 [Y/n] " answer </dev/tty || return 0
    ! [[ "$answer" =~ ^[Nn]$ ]]
}

command -v systemctl >/dev/null && {
    info "=> Stopping and removing demo-* user units..."
    {
        systemctl --user list-units --all --type=service --plain --no-legend 2>/dev/null | awk '{print $1}' || true
        systemctl --user list-unit-files --type=service --plain --no-legend 2>/dev/null | awk '{print $1}' || true
        # Units registered in state may already be stopped; catch them too.
        if [ -f "${STATE_DIR}/state.json" ]; then
            grep -o '"unit_name"[[:space:]]*:[[:space:]]*"[^"]*"' \
                "${STATE_DIR}/state.json" | cut -d'"' -f4 || true
        fi
    } | sort -u | { grep '^demo-' || true; } | while read -r unit; do
        systemctl --user stop "$unit" 2>/dev/null || true
        systemctl --user disable "$unit" 2>/dev/null || true
        rm -f "${UNIT_DIR}/${unit}"
    done
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user reset-failed 2>/dev/null || true
}

if [ -f "${BIN_DIR}/${BIN_NAME}" ]; then
    info "=> Removing ${BIN_DIR}/${BIN_NAME}..."
    rm -f "${BIN_DIR}/${BIN_NAME}"
fi

if [ -d "${STATE_DIR}" ]; then
    info "=> Removing ${STATE_DIR} (registry, net log, secrets)..."
    rm -rf "${STATE_DIR}"
fi

if [ -d "${INSTALL_DIR}" ]; then
    warn ""
    warn "This also removes the installed program AND all deployed service data:"
    warn "  ${INSTALL_DIR}"
    if confirm "Remove it?"; then
        info "=> Removing ${INSTALL_DIR}..."
        rm -rf "${INSTALL_DIR}"
    else
        warn "Kept ${INSTALL_DIR} — remove manually later if desired."
    fi
fi

ok ""
ok "demo-ghostprovider has been uninstalled."
ok ""
