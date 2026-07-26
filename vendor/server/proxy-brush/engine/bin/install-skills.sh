#!/bin/bash
# Compatibility wrapper. The draw_proxy installer copies real files and never
# installs repository symlinks.
set -euo pipefail

ENGINE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DRAW_ROOT="$(cd "$ENGINE_ROOT/.." && pwd)"

exec python3 "$DRAW_ROOT/scripts/install_skills.py" "$@"
