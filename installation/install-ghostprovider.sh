#!/usr/bin/env bash
# Install the paid ghostprovider from its private repository.
#
# Requires an access token (issued by the Telegram bot after purchase).
# The token works for 1 hour and is read-only. Provide it via GITHUB_TOKEN,
# --token=<...>, or enter it when prompted. The token is never saved to disk.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/iamnetuseragent/demo-ghostprovider/main/installation/install-ghostprovider.sh | GITHUB_TOKEN=<token> bash
#
# To update: ask the bot for a fresh token (/access) and re-run this command.
set -euo pipefail

REPO="https://github.com/iamnetuseragent/ghostprovider.git"
INSTALL_DIR="${HOME}/.local/share/ghostprovider"
BIN_DIR="${HOME}/.local/bin"
BIN_NAME="ghostprovider"

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
  err "systemctl not found. ghostprovider requires systemd."
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

# ── 4. Get access token ──
TOKEN="${GITHUB_TOKEN:-}"
for arg in "$@"; do
  case "$arg" in
    --token=*) TOKEN="${arg#--token=}" ;;
    --token) shift; TOKEN="${1:-}" ;;
  esac
done

if [ -z "$TOKEN" ]; then
  read -r -s -p "Enter your access token: " TOKEN
  echo ""
fi

if [ -z "$TOKEN" ]; then
  err "No access token provided."
fi

# ── 5. Clone private repository ──
info "=> Downloading ghostprovider..."
CLONE_URL=$(echo "$REPO" | sed "s|https://|https://x-access-token:${TOKEN}@|")
TMP_DIR="${INSTALL_DIR}.tmp"
rm -rf "$TMP_DIR"

if ! git clone --quiet --depth=1 "$CLONE_URL" "$TMP_DIR"; then
  rm -rf "$TMP_DIR"
  err "Failed to clone the repository. Your token may have expired — ask the bot for a new one with /access."
fi

# Never persist the token: drop the remote (and the URL it embeds) immediately.
git -C "$TMP_DIR" remote remove origin

rm -rf "$INSTALL_DIR"
mv "$TMP_DIR" "$INSTALL_DIR"

# ── 6. Create venv and install ──
info "=> Setting up virtual environment..."
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install --quiet "$INSTALL_DIR"

# ── 7. Create launcher script ──
info "=> Installing launcher to ${BIN_DIR}/..."
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/$BIN_NAME" << 'LAUNCHER'
#!/usr/bin/env bash
exec ~/.local/share/ghostprovider/.venv/bin/python3 -m ghostprovider "$@"
LAUNCHER
chmod +x "$BIN_DIR/$BIN_NAME"

# ── 8. Add to PATH if needed ──
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  PROFILE=""
  SHELL_NAME=""
  PATH_LINE=""

  case "${SHELL:-}" in
    */bash)
      PROFILE="$HOME/.bashrc"
      SHELL_NAME="bash"
      PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
      ;;
    */zsh)
      PROFILE="$HOME/.zshrc"
      SHELL_NAME="zsh"
      PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
      ;;
    */fish)
      PROFILE="$HOME/.config/fish/config.fish"
      SHELL_NAME="fish"
      PATH_LINE='set -gx PATH $HOME/.local/bin $PATH'
      ;;
    */ksh)
      PROFILE="$HOME/.kshrc"
      SHELL_NAME="ksh"
      PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
      ;;
    */csh|*/tcsh)
      PROFILE="$HOME/.tcshrc"
      SHELL_NAME="tcsh"
      PATH_LINE='setenv PATH "$HOME/.local/bin:$PATH"'
      ;;
    *)
      SHELL_NAME="unknown"
      ;;
  esac

  if [ -n "$PROFILE" ]; then
    warn ""
    warn "~/.local/bin is not in your PATH."
    read -r -p "Add PATH to $PROFILE? [Y/n] " answer
    if [[ "$answer" != "n" && "$answer" != "N" ]]; then
      mkdir -p "$(dirname "$PROFILE")"
      echo '' >> "$PROFILE"
      echo '# ghostprovider' >> "$PROFILE"
      echo "$PATH_LINE" >> "$PROFILE"
      ok "PATH added to $PROFILE"
      warn "Run:  source $PROFILE"
    else
      warn "Skipped. Add manually: $PATH_LINE"
    fi
  else
    warn ""
    warn "~/.local/bin is not in your PATH."
    warn "Unknown shell ($SHELL). Add manually:"
    warn "  export PATH=\"\$HOME/.local/bin:\$PATH\""
  fi
fi

ok ""
ok "Installation complete!"
ok "Run with:  ghostprovider"
ok "To update later, ask the bot for a fresh token (/access) and re-run this command."
ok ""
