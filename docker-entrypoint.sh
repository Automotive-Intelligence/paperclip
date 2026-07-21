#!/bin/sh
set -e
# boot-pull model + gated stock from Blob into the HOME/cache layout (idempotent, deduped)
python -m tools.media_worker.boot || echo "boot-pull skipped or partial (see logs)"
exec "$@"
