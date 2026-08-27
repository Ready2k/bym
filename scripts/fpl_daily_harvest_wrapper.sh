#!/bin/bash
# Wrapper launchd actually invokes for the FPL daily harvest (see
# scripts/fpl_daily_harvest.py for the harvest logic itself). Logs every
# run, and once the Python script signals FPL_HARVEST_COMPLETE, removes
# its own LaunchAgent so it never fires again — the "stop automatically
# once every harvest season is complete" requirement, implemented here
# rather than in the Python script because only the wrapper can reach
# launchctl/the plist file.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$REPO_DIR/data/fpl_daily_harvest.log"
PLIST_LABEL="com.bym.fpl-daily-harvest"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"

cd "$REPO_DIR" || exit 1

{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) starting fpl_daily_harvest ==="
  OUTPUT="$(/opt/homebrew/bin/uv run python scripts/fpl_daily_harvest.py 2>&1)"
  echo "$OUTPUT"
  if echo "$OUTPUT" | grep -q "FPL_HARVEST_COMPLETE"; then
    echo "Harvest complete — removing the LaunchAgent so it never fires again."
    launchctl bootout "gui/$(id -u)/${PLIST_LABEL}" 2>&1
    rm -f "$PLIST_PATH"
  fi
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) finished ==="
} >> "$LOG_FILE" 2>&1
