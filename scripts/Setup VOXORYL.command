#!/bin/bash
# VOXORYL Setup Wizard (macOS — double-clickable .command)
cd "$(dirname "$0")/.." || exit 1
export PYTHONUNBUFFERED=1

if command -v python3 >/dev/null 2>&1; then
  exec python3 scripts/setup_voxoryl.py "$@"
fi

echo "Python 3.11+ required. Install python3 and re-run." >&2
read -r -p "Press Enter to close…"
exit 1
