#!/usr/bin/env bash
set -euo pipefail

REPO="https://github.com/iamnetuseragent/demo-ghostprovider.git"
INSTALL_DIR="${HOME}/.local/share/demo-ghostprovider"
BIN_DIR="${HOME}/.local/bin"
BIN_NAME="demo-ghostprovider"

info()  { printf "\033[36m%s\033[0m\n" "$*"; }
ok()    { printf "\033[32m%s\033[0m\n" "$*"; }
warn()  { printf "\033[33m%s\033[0m\n" "$*"; }
err()   { printf "\033[31m%s\033[0m\n" "$*" >&2; exit 1; }

# ── 1. Check OS ──
case "$(uname -s)" in
  Linux*) ;;
  *) err "This installer supports Linux only. Detected: $(uname -s)";;
esac

if ! command -v systemctl &>/dev/null; then
  err "systemctl not found. demo-ghostprovider requires systemd."
fi

# ── 2. Check Python ──
if ! command -v python3 &>/dev/null; then
  err "python3 not found. Install Python 3.10+ first."
fi

PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
  err "Python 3.10+ required. Found: ${PY_MAJOR}.${PY_MINOR}"
fi

# ── 3. Check git ──
if ! command -v git &>/dev/null; then
  err "git not found. Install git first."
fi

# ── 4. Clone ──
info "=> Downloading demo-ghostprovider..."
if [ -d "$INSTALL_DIR/.git" ]; then
  info "=> Updating existing installation..."
  git -C "$INSTALL_DIR" pull --ff-only || warn "Could not pull, using existing version"
else
  rm -rf "$INSTALL_DIR"
  git clone --depth=1 "$REPO" "$INSTALL_DIR"
fi

# ── 5. Create venv and install ──
info "=> Setting up virtual environment..."
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install --quiet "$INSTALL_DIR"

# ── 6. Create launcher script ──
info "=> Installing launcher to ${BIN_DIR}/..."
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/$BIN_NAME" << 'LAUNCHER'
#!/usr/bin/env bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec ~/.local/share/demo-ghostprovider/.venv/bin/python3 -m demo_ghostprovider "$@"
LAUNCHER
chmod +x "$BIN_DIR/$BIN_NAME"

# ── 7. Add to PATH if needed ──
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  # Detect shell
  if [ -n "$ZSH_VERSION" ] || [[ "${SHELL:-}" == */zsh ]]; then
    PROFILE="$HOME/.zshrc"
    SHELL_NAME="zsh"
  else
    PROFILE="$HOME/.bashrc"
    SHELL_NAME="bash"
  fi

  warn ""
  warn "~/.local/bin is not in your PATH."
  read -r -p "Add PATH to $PROFILE? [Y/n] " answer
  if [[ "$answer" != "n" && "$answer" != "N" ]]; then
    echo '' >> "$PROFILE"
    echo '# demo-ghostprovider' >> "$PROFILE"
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$PROFILE"
    ok "PATH added to $PROFILE"
    warn "Run:  source $PROFILE"
  else
    warn "Skipped. Add manually: export PATH=\"\$HOME/.local/bin:\$PATH\""
  fi
fi

ok ""
ok "Installation complete!"
ok "Run with:  demo-ghostprovider"
ok ""
