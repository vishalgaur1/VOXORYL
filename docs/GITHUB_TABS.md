# GitHub tabs — what each one is for

Short map for VOXORYL maintainers. Fill tabs that help batchmates; leave auto ones alone.

| Tab | Fill? | Purpose here |
|---|---|---|
| **Code** | Yes (always) | Source, README, Releases assets. Main face of the repo. |
| **Issues** | Templates yes; fake bugs no | Real bugs + honest roadmap enhancements. Templates live in `.github/ISSUE_TEMPLATE/`. |
| **Pull requests** | Only real work | Never open empty/spam PRs for “activity.” |
| **Actions** | Keep as-is | CI / Pages deploy. Don’t invent workflows for aura. |
| **Projects** | One simple board | Kanban for near-term roadmap (Todo / Doing / Done). Useful + looks alive. |
| **Wiki** | 3–4 plain pages | Human FAQ / roadmap — **not** a README dump. Home, FAQ, Roadmap, optional Architecture. |
| **Security** | Already set | `SECURITY.md` + GitHub Security tab. Don’t invent vulns. |
| **Insights** | Leave alone | Traffic, contributors, community — GitHub fills this. |
| **Settings → Pages** | Live site | Static site from `/docs` on `main` → https://vishalgaur1.github.io/VOXORYL/ |

## Aura rules (keep / skip)

**Do:** real Wiki pages, issue templates, 2–3 honest `enhancement` issues, one Project board with real cards.

**Don’t:** lorem-ipsum Wiki, duplicate README into Wiki, fake stars/PRs, spam closed issues.

## Wiki git note

The Wiki is a **separate** git repo: `https://github.com/vishalgaur1/VOXORYL.wiki.git`.  
It only exists after the **first page** is created in the GitHub UI (Clone fails with “repository not found” until then).

**Seed (one-time):**

1. Open https://github.com/vishalgaur1/VOXORYL/wiki → **Create the first page**
2. Title: `Home` — paste from [`docs/wiki/Home.md`](wiki/Home.md) → Save
3. Then from a terminal:

```powershell
git clone https://github.com/vishalgaur1/VOXORYL.wiki.git
cd VOXORYL.wiki
copy ..\VOXORYL\docs\wiki\*.md .
git add *.md _Sidebar.md
git commit -m "Add FAQ, Roadmap, Architecture, Sidebar"
git push
```

Page sources live in-repo under `docs/wiki/` so we don’t invent fluff twice.

## Project board (needs `project` token scope)

`gh project` needs a token with the `project` scope (Cursor’s `gh` login is missing it). Create once in the UI:

1. https://github.com/vishalgaur1/VOXORYL/projects → **New project** → Board → name **VOXORYL**
2. Columns: **Todo** / **Doing** / **Done**
3. Add cards linked to issues:
   - #1 Reels inbox polish → Todo
   - #2 First-run bootstrap → Todo or Doing
   - #3 Computer-use harden → Todo

Or after `gh auth refresh -s project,read:project`:

```powershell
gh project create --owner vishalgaur1 --title VOXORYL
```

## Useful links

- Repo: https://github.com/vishalgaur1/VOXORYL  
- Wiki: https://github.com/vishalgaur1/VOXORYL/wiki  
- Pages: https://vishalgaur1.github.io/VOXORYL/  
- Releases: https://github.com/vishalgaur1/VOXORYL/releases  
