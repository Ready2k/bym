#!/bin/bash
# Installs (or reinstalls, e.g. after moving this repo) the FPL daily
# harvest LaunchAgent. launchd plists need an absolute path baked in —
# there is no variable expansion at load time — so scripts/com.bym.
# fpl-daily-harvest.plist is a template (__REPO_DIR__ placeholder); this
# script fills it in with wherever the repo actually is right now and
# installs the result. Re-run this any time the repo directory moves.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST_LABEL="com.bym.fpl-daily-harvest"
TEMPLATE="$REPO_DIR/scripts/${PLIST_LABEL}.plist"
DEST="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"

if [ ! -f "$TEMPLATE" ]; then
  echo "Template not found: $TEMPLATE" >&2
  exit 1
fi

# Unload whatever is currently registered under this label, if anything —
# by label, not by path, so this finds it regardless of where it used to
# point before a move.
launchctl bootout "gui/$(id -u)/${PLIST_LABEL}" 2>/dev/null || true

mkdir -p "$HOME/Library/LaunchAgents"
sed "s#__REPO_DIR__#${REPO_DIR}#g" "$TEMPLATE" > "$DEST"

launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "Installed and loaded ${PLIST_LABEL} for ${REPO_DIR}"
