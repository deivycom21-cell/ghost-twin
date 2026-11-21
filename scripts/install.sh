#!/usr/bin/env bash
# ghost-twin quick installer for Linux / macOS / Kali.
#   curl -fsSL https://raw.githubusercontent.com/deivycom21-cell/ghost-twin/main/scripts/install.sh | bash
# Options (env vars):
#   GHOST_TWIN_REF=main          git ref to install
#   GHOST_TWIN_HOME=~/.ghost-twin where the private virtualenv lives (when pipx is absent)
#   GHOST_TWIN_SKIP_DEPS=1        don't try to install Chromium system libraries
set -euo pipefail

REPO="https://github.com/deivycom21-cell/ghost-twin.git"
REF="${GHOST_TWIN_REF:-main}"
SPEC="git+${REPO}@${REF}"
HOME_DIR="${GHOST_TWIN_HOME:-$HOME/.ghost-twin}"
BIN_DIR="$HOME/.local/bin"

say()  { printf '\033[1;35m👻 %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!  %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✗  %s\033[0m\n' "$*"; exit 1; }

PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || die "Python 3.10+ is required (sudo apt install python3 python3-venv)"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "Python 3.10+ is required, found $("$PY" -V 2>&1)"
command -v git >/dev/null || die "git is required (sudo apt install git)"

# Debian/Ubuntu/Kali mark the system Python as externally managed (PEP 668), so we never
# pip-install into it: pipx if available, otherwise a private virtualenv.
if command -v pipx >/dev/null 2>&1; then
  say "Installing with pipx…"
  pipx install --force "$SPEC"
  GT_PY="$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null || echo "$HOME/.local/pipx/venvs")/ghost-twin/bin/python"
else
  say "Installing into a private virtualenv at $HOME_DIR/venv…"
  "$PY" -m venv "$HOME_DIR/venv" 2>/dev/null \
    || die "python3-venv is missing (sudo apt install python3-venv) — or install pipx"
  "$HOME_DIR/venv/bin/python" -m pip install --quiet --upgrade pip
  "$HOME_DIR/venv/bin/python" -m pip install --quiet --upgrade "$SPEC"
  mkdir -p "$BIN_DIR"
  ln -sf "$HOME_DIR/venv/bin/ghost-twin" "$BIN_DIR/ghost-twin"
  GT_PY="$HOME_DIR/venv/bin/python"
fi

say "Downloading headless Chromium (~150 MB, once)…"
"$GT_PY" -m playwright install chromium

if [ "$(uname -s)" = "Linux" ] && [ -z "${GHOST_TWIN_SKIP_DEPS:-}" ]; then
  if command -v sudo >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
    say "Installing Chromium system libraries (sudo)…"
    sudo "$GT_PY" -m playwright install-deps chromium \
      || warn "install-deps failed (unsupported distro?). On Kali try: sudo apt install -y libnss3 libatk-bridge2.0-0 libgbm1 libxkbcommon0 libasound2"
  fi
fi

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "Add $BIN_DIR to your PATH:  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc" ;;
esac

say "Done! Try:"
echo "    ghost-twin doctor"
echo "    ghost-twin demo &   # buggy demo app on :3000"
echo "    ghost-twin attack http://localhost:3000 --users 4 --duration 60s --dashboard"
