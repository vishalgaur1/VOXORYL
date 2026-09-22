# Private data (legacy / placeholder in the repo)

**Your real memory, knowledge, reels, and logs no longer live here by default.**

VOXORYL stores personal data in the OS user-data folder:

| OS | Path |
|----|------|
| Windows | `%LOCALAPPDATA%\VOXORYL\` |
| macOS | `~/Library/Application Support/VOXORYL/` |
| Linux | `~/.local/share/voxoryl/` |

On first run (or `scripts/install.ps1` / `install.sh`), anything still under this repo `data/` is **copied once** into that folder — the repo copy is not deleted.

Developers who want the old layout: set `VOXORYL_USE_REPO_DATA=1`.

Never commit secrets or personal files. See the main README → **First run** and **Uninstall**.
