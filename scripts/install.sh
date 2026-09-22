#!/usr/bin/env bash
# VOXORYL install entry (macOS / Linux).
# Default: Setup Wizard. Use --cli for terminal-only bootstrap.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CLI=0
ARGS=()
for a in "$@"; do
  if [[ "$a" == "--cli" ]]; then
    CLI=1
  else
    ARGS+=("$a")
  fi
done

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

if [[ "$CLI" -eq 1 ]]; then
  exec python3 "$ROOT/scripts/bootstrap_voxoryl.py" "${ARGS[@]+"${ARGS[@]}"}"
fi

echo "    Opening Setup Wizard (pass --cli for terminal-only bootstrap)…"
exec python3 "$ROOT/scripts/setup_voxoryl.py" "${ARGS[@]+"${ARGS[@]}"}"
