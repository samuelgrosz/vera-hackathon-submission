#!/bin/sh
# Installs Vera on macOS or Linux.
#   curl -fsSL https://samuelgrosz.github.io/vera-hackathon-submission/install.sh | sh
#
# Optional settings (environment variables):
#   VERA_HOME=/some/folder   where to put Vera (default: ~/vera-cli)
#   VERA_REPO=<git url>      use a different repository
#   VERA_NO_PATH=1           do not edit your shell startup file
set -eu

REPO="${VERA_REPO:-https://github.com/samuelgrosz/vera-hackathon-submission.git}"
DEST="${VERA_HOME:-$HOME/vera-cli}"

say() { printf '%s\n' "$*"; }
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 \
  || fail "Git is not installed. Get it from https://git-scm.com/downloads, then run this again."

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 \
     && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$c"
    break
  fi
done
[ -n "$PY" ] \
  || fail "Python 3.10 or newer is required. Get it from https://www.python.org/downloads/, then run this again."

if [ -d "$DEST/.git" ]; then
  say "Updating Vera in $DEST"
  git -C "$DEST" pull --ff-only
elif [ -e "$DEST" ] && [ -n "$(ls -A "$DEST" 2>/dev/null)" ]; then
  fail "$DEST already exists and is not a Vera install. Set VERA_HOME to another folder and run this again."
else
  say "Downloading Vera into $DEST"
  git clone --depth 1 "$REPO" "$DEST"
fi

chmod +x "$DEST/vera" 2>/dev/null || true

add_path() {
  rc="$1"
  [ -f "$rc" ] || : > "$rc"
  if ! grep -Fqs "$DEST" "$rc"; then
    printf '\n# Vera\nexport PATH="%s:$PATH"\n' "$DEST" >> "$rc"
    say "Added Vera to your PATH in $rc"
  fi
}

if [ -z "${VERA_NO_PATH:-}" ]; then
  case "${SHELL:-}" in
    */zsh)  add_path "$HOME/.zshrc" ;;
    */bash) if [ "$(uname -s)" = "Darwin" ]; then add_path "$HOME/.bash_profile"; else add_path "$HOME/.bashrc"; fi ;;
    */fish) say "Fish shell: run  fish_add_path \"$DEST\"  to put Vera on your PATH." ;;
    *)      add_path "$HOME/.profile" ;;
  esac
fi

say ""
say "Vera is installed."
say "Open a new terminal and run:  vera /path/to/screening/dev"
say "Or start it right now with:   $DEST/vera /path/to/screening/dev"
