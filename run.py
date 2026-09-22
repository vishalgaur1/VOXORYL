"""Run Voxoryl locally (developer console).

  python run.py

Product / desktop (silent, starts Ollama + widget, logs to data/logs/):
  scripts\\launch-voxoryl.vbs
  .\\.venv\\Scripts\\python.exe scripts\\launch_voxoryl.py
  .\\.venv\\Scripts\\python.exe scripts\\launch_voxoryl.py --console
"""

from __future__ import annotations

import argparse

import uvicorn

from voxoryl.config import settings


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Voxoryl API (developer mode — console logs)")
    parser.add_argument(
        "--host",
        default=settings.voxoryl_host,
        help="Bind host (default from .env VOXORYL_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.voxoryl_port,
        help="Bind port (default from .env VOXORYL_PORT; launcher may use 3848)",
    )
    args = parser.parse_args()
    uvicorn.run(
        "voxoryl.main:app",
        host=args.host,
        port=args.port,
        reload=False,
    )
