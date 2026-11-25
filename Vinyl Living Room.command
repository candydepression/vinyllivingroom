#!/usr/bin/env bash
# Double-click me in Finder. Opens the drop-in page in your browser.
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || ./setup.sh
exec ./vinyl app
