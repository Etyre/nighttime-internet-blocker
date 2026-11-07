#!/bin/bash
# Builds the menu bar app, the Dock app and the icon into build/. Thin wrapper so the project has the
# standard build.sh at the top level; the real script is menubar/build.sh (needs Xcode). Safe to rerun.
exec "$(cd "$(dirname "$0")" && pwd)/menubar/build.sh" "$@"
