#!/bin/bash
# Mirror avo-telemetry's social_outbox into ~/Documents/Social Outbox.
#
# Reads origin/main via fetch + archive rather than `git pull`, deliberately:
# the local avo-telemetry checkout regularly carries unpushed commits from other
# seats, and a pull there can stop on a conflict that has nothing to do with the
# packs. Fetch + archive never touches the working tree, so the folder stays
# current even when the checkout is mid-divergence.
set -uo pipefail
REPO="$HOME/avo-telemetry"
# NOT ~/Documents: macOS TCC blocks launchd agents from writing there, so the
# scheduled sync failed while a hand-run from Terminal worked. The real folder
# lives in the home root and ~/Documents/Social Outbox is a symlink to it.
DEST="$HOME/social-outbox"
STAMP="$DEST/.last-sync"

cd "$REPO" || exit 1
git fetch -q origin main || exit 1
mkdir -p "$DEST"
# Extract straight from the fetched ref. --strip-components drops the
# social_outbox/ prefix so brands sit at the top of the folder.
git archive origin/main social_outbox 2>/dev/null \
  | tar -x -m -C "$DEST" --strip-components=1 || exit 1
date "+%Y-%m-%d %H:%M %Z" > "$STAMP"
