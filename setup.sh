#!/usr/bin/env bash
# One-time setup on a Mac. Safe to run again.
set -euo pipefail
cd "$(dirname "$0")"

for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$("$b" shellenv)" && break; done
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is needed first. Install it from https://brew.sh, then run ./setup.sh again."
  exit 1
fi
command -v ffmpeg >/dev/null 2>&1 || brew install ffmpeg
PY="$(brew --prefix)/bin/python3"
[ -x "$PY" ] || brew install python
"$PY" -c 'import sys; assert sys.version_info >= (3, 11)' 2>/dev/null || brew upgrade python

[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
chmod +x vinyl start.sh install.sh "Vinyl Living Room.command"
shasum requirements.txt | cut -d' ' -f1 > .venv/.req
