#!/usr/bin/env bash
# Wrapper to run beets_harness.py inside the Nix-managed beets Python environment.
#
# Usage:
#   ./harness/run_beets_harness.sh /mnt/virtio/Music/AI/SomeArtist/SomeAlbum
#   ./harness/run_beets_harness.sh --pretend /mnt/virtio/Music/AI/SomeArtist
#
# The harness communicates over stdin/stdout using newline-delimited JSON.
# Beets logs go to stderr.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HARNESS="$SCRIPT_DIR/beets_harness.py"

# Resolve the beet wrapper to find its Python environment
BEET_BIN="$(command -v beet 2>/dev/null || echo "/etc/profiles/per-user/abl030/bin/beet")"

if [[ ! -x "$BEET_BIN" ]]; then
    echo "Error: beet not found at $BEET_BIN" >&2
    exit 1
fi

# Follow the wrapper chain to find .beet-wrapped
BEET_WRAPPED="$(grep -oP '(?<=exec -a "\$0" ")[^"]+' "$BEET_BIN")"
if [[ -z "$BEET_WRAPPED" ]]; then
    echo "Error: could not extract .beet-wrapped path from $BEET_BIN" >&2
    exit 1
fi

# Get the Python interpreter from .beet-wrapped's shebang
PYTHON="$(head -1 "$BEET_WRAPPED" | sed 's/^#!//')"
if [[ ! -x "$PYTHON" ]]; then
    echo "Error: Python not found at $PYTHON" >&2
    exit 1
fi

# Extract the site-packages list from the functools.reduce line in .beet-wrapped
# and build PYTHONPATH from it
PYTHONPATH="$(sed -n '3p' "$BEET_WRAPPED" \
    | grep -oP "'/nix/store/[^']+/site-packages'" \
    | tr -d "'" \
    | paste -sd: -)"

export PYTHONPATH

exec "$PYTHON" "$HARNESS" "$@"
