#!/usr/bin/env bash
# VOXORYL launcher (macOS / Linux) — thin wrapper around scripts/launch_voxoryl.py
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
else
  PY="$(command -v python)"
fi

if [[ ! -f "$ROOT/.env" && -f "$ROOT/.env.example" ]]; then
  echo "[voxoryl] No .env found — copy .env.example to .env and fill keys as needed."
fi

exec "$PY" "$ROOT/scripts/launch_voxoryl.py" "$@"
