#!/bin/bash
# Vinyl Living Room — one-time installer for macOS. Safe to run again (it updates).
set -euo pipefail

REPO="https://github.com/candydepression/vinyllivingroom.git"
DEST="$HOME/VinylLivingRoom"
LAUNCHER="$HOME/Desktop/Vinyl Living Room.command"
say() { printf "\n\033[1m%s\033[0m\n" "$1"; }

[ "$(uname)" = "Darwin" ] || { echo "This installer is for macOS."; exit 1; }

load_brew() {
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$b" ]; then eval "$("$b" shellenv)"; return 0; fi
  done
  return 1
}

# 1) Homebrew (the standard Mac package manager)
if ! command -v brew >/dev/null 2>&1 && ! load_brew; then
  say "Installing Homebrew. It will ask for your Mac password (typing is invisible — that's normal)."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  load_brew
fi

# 2) Tools
say "Installing ffmpeg, Python and git (first time takes a few minutes)…"
for pkg in ffmpeg python git; do
  brew list --formula "$pkg" >/dev/null 2>&1 || brew install "$pkg"
done

# 3) The app itself
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo "")"
if [ -d "$DEST/.git" ]; then
  say "Updating the app…"
  git -C "$DEST" pull --ff-only -q || echo "(Couldn't update; keeping the current version.)"
elif [ -n "$HERE" ] && [ -f "$HERE/vlr/__main__.py" ] && [ "$HERE" != "$DEST" ]; then
  say "Copying the app to $DEST…"            # running from a downloaded .zip
  mkdir -p "$DEST" && cp -R "$HERE"/. "$DEST"/
else
  say "Downloading the app…"
  git clone -q --depth 1 -b main "$REPO" "$DEST"
fi
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true

# 4) Python packages
"$DEST/setup.sh"

# 5) Desktop launcher
cat > "$LAUNCHER" <<LAUNCH
#!/bin/bash
exec "$DEST/start.sh"
LAUNCH
chmod +x "$LAUNCHER"

say "Done! Double-click “Vinyl Living Room” on your Desktop to start."
open -R "$LAUNCHER" 2>/dev/null || true
