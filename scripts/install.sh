#!/usr/bin/env bash
# Zero-setup VOXORYL install (macOS / Linux).
# Thin wrapper around scripts/bootstrap_voxoryl.py
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> VOXORYL install"
if [[ "$(uname -s)" == "Darwin" ]]; then
  echo "    User data: ~/Library/Application Support/VOXORYL"
else
  echo "    User data: \${XDG_DATA_HOME:-~/.local/share}/voxoryl"
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.11+ required. Install python3 and re-run." >&2
  exit 1
fi

python3 - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY

exec python3 "$ROOT/scripts/bootstrap_voxoryl.py" "$@"
