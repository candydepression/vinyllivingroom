#!/bin/bash
# Starts the drop-in page. Quietly updates the app first when possible.
cd "$(dirname "$0")"
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$("$b" shellenv)" && break; done

if [ -d .git ]; then
  before="$(git rev-parse HEAD 2>/dev/null)"
  git pull --ff-only -q 2>/dev/null || true
  [ "$before" != "$(git rev-parse HEAD 2>/dev/null)" ] && echo "Updated to the latest version."
fi
want="$(shasum requirements.txt | cut -d' ' -f1)"
if [ ! -x .venv/bin/python ] || [ "$(cat .venv/.req 2>/dev/null)" != "$want" ]; then
  ./setup.sh && echo "$want" > .venv/.req
fi
clear
echo "Vinyl Living Room is running. Leave this window open (you can minimise it)."
echo "Close it when you're done."
echo
exec ./vinyl app
