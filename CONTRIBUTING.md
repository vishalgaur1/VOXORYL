# Contributing to VOXORYL

Thanks for helping **Voxy** stay local-first and honest.

## Before you start

1. Fork + clone, use Python **3.11+**, create a venv, `pip install -r requirements.txt`.
2. First run: `scripts/install.ps1` / `install.sh` (or `python -m voxoryl.bootstrap`). Config lands in the OS user-data folder as `config.env` — never commit secrets.
3. Run: `python scripts/launch_voxoryl.py --console` or `python run.py`.

## What we want

- Fixes and features that keep **tools on-device** and degrade honestly on macOS.
- Clear PR descriptions: what changed, how you tested (OS + steps).
- Small PRs beat giant refactors.

## What we don’t want

- Secrets, real `.env` keys, or private `data/` dumps.
- Cloud-only shortcuts that break the local default.
- Brand renames or retired product-name leftovers.

## Process

1. Open an issue for bigger ideas (or a draft PR if it’s small).
2. Branch from `main`, keep commits focused.
3. Add/adjust tests under `tests/` when behavior changes.
4. Open a PR with the checklist in `.github/PULL_REQUEST_TEMPLATE.md`.

Questions: open an issue or email **vishalgaur2002@gmail.com** (commercial licensing is separate — see `COMMERCIAL_LICENSE.md`).
