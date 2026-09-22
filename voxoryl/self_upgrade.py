from __future__ import annotations

"""
Self-upgrade backlog → Cursor.
Log bugs / feature requests / improvements; optionally draft a Cursor agent prompt
and a light PR workflow stub (gh if available — never force push).
"""

import json
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxoryl.config import settings
from voxoryl.knowledge import knowledge
from voxoryl.memory import memory


ROOT = Path(__file__).resolve().parent.parent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_path() -> Path:
    p = settings.voxoryl_data_dir / "self_upgrade.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _prompts_dir() -> Path:
    p = settings.voxoryl_data_dir / "self_upgrade_prompts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def append_item(
    text: str,
    *,
    kind: str = "feature",
    source: str = "user",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {"ok": False, "speak": "What should I log? Describe the bug or feature."}
    item = {
        "id": str(uuid.uuid4())[:8],
        "at": _now(),
        "kind": kind,
        "source": source,
        "text": text[:2000],
        "meta": meta or {},
        "status": "open",
    }
    with _log_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
    try:
        knowledge.append_facts("Self Upgrade", [f"[{kind}] {text[:240]}"])
    except Exception:
        pass
    memory.remember_fact(f"self_upgrade: {text[:120]}", tags=["self_upgrade", kind])
    return {"ok": True, "item": item, "speak": f"Logged ({kind}): {text[:160]}"}


def list_items(*, limit: int = 20, status: str = "open") -> dict[str, Any]:
    path = _log_path()
    items: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if status and status != "all" and obj.get("status") != status:
                continue
            items.append(obj)
    items = items[-limit:]
    speak = (
        f"{len(items)} open self-upgrade item(s). Latest: " + (items[-1].get("text", "")[:100] if items else "none")
    )
    return {"ok": True, "items": items, "speak": speak}


def draft_cursor_prompt(item_text: str, *, item_id: str = "") -> dict[str, Any]:
    body = f"""# Voxoryl self-upgrade — implement in repo

You are working in the Voxoryl repo. Implement the following improvement as TECH READY scaffolding that matches existing patterns (voxoryl/*.py, pipelines.py, agent.py, main.py APIs, setup/voxoryl.setup.json, setup/IDEAS.md). Personal data under data/ only. Do NOT commit unless asked. Do NOT force-push.

## Request
{item_text}

## Done when
- Module runs / degrades gracefully with clear hints
- Wired into match_pipeline + agent dispatch + /api if appropriate
- Smoke-test imports pass
"""
    fname = f"cursor_prompt_{item_id or str(uuid.uuid4())[:8]}.md"
    path = _prompts_dir() / fname
    path.write_text(body, encoding="utf-8")
    # also drop a copy under setup for visibility if wanted
    setup_copy = ROOT / "setup" / "self_upgrade_inbox.md"
    try:
        prev = setup_copy.read_text(encoding="utf-8") if setup_copy.exists() else "# Self-upgrade inbox\n\n"
        setup_copy.write_text(prev + f"\n## { _now() } ({item_id})\n\n{item_text}\n", encoding="utf-8")
    except Exception:
        pass
    return {
        "ok": True,
        "path": str(path),
        "prompt": body,
        "speak": f"Drafted a Cursor agent prompt at {path}. Open it in Cursor and ask the agent to implement + open a PR.",
    }


def pr_workflow_stub(*, title: str = "", body: str = "") -> dict[str, Any]:
    """Documented / best-effort gh flow — never force push."""
    gh = shutil.which("gh")
    git = shutil.which("git")
    steps = [
        "git checkout -b feat/self-upgrade-<id>",
        "git add -A && git commit -m \"feat: <summary>\"",
        "git push -u origin HEAD",
        'gh pr create --title "..." --body "..."',
    ]
    if not gh:
        return {
            "ok": True,
            "gh": False,
            "steps": steps,
            "hint": "Install GitHub CLI (gh) for PR creation.",
            "speak": "PR stub ready. Install `gh`, then: branch → commit → push -u → gh pr create. Never force-push.",
        }
    # status only — do not create PR unless user explicitly asks via action=create_pr later
    status = {"git": bool(git), "gh": True}
    try:
        r = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=8)
        status["gh_auth_ok"] = r.returncode == 0
        status["gh_auth"] = (r.stdout or r.stderr or "")[:300]
    except Exception as exc:
        status["gh_auth_ok"] = False
        status["error"] = str(exc)
    return {
        "ok": True,
        "gh": True,
        "status": status,
        "steps": steps,
        "title": title,
        "body": body,
        "speak": "GitHub CLI found. When the Cursor agent finishes, push a branch and run gh pr create — I won't force-push.",
        "rule": "never force push",
    }


def _strip_prefix(message: str, prefixes: tuple[str, ...]) -> str:
    raw = (message or "").strip()
    lower = raw.lower()
    for p in prefixes:
        if lower.startswith(p):
            return raw[len(p) :].strip(" :,-")
    return raw


async def tool_self_upgrade(action: str = "log", *, message: str = "", kind: str = "") -> dict[str, Any]:
    action = (action or "log").lower().strip()
    lower = (message or "").lower()

    if action in {"list", "backlog"} or "list upgrades" in lower or "self upgrade list" in lower:
        return list_items()

    if action in {"pr", "pr_stub", "workflow"} or "ask cursor to" in lower and "pr" in lower:
        return pr_workflow_stub()

    if action in {"cursor", "prompt", "draft"} or "ask cursor to" in lower:
        text = _strip_prefix(
            message,
            (
                "ask cursor to",
                "ask cursor",
                "draft cursor prompt",
                "add this feature to yourself",
            ),
        )
        logged = append_item(text or message, kind=kind or "feature", source="user")
        draft = draft_cursor_prompt(text or message, item_id=(logged.get("item") or {}).get("id"))
        draft["item"] = logged.get("item")
        draft["speak"] = str(logged.get("speak") or "") + " " + str(draft.get("speak") or "")
        return draft

    if action in {"bug", "fix"} or any(k in lower for k in ("fix your bug", "log bug", "you have a bug")):
        text = _strip_prefix(message, ("fix your bug", "log bug", "bug:", "fix bug"))
        return append_item(text or message, kind="bug", source="user")

    if action in {"cant", "gap"} or any(
        k in lower for k in ("voxoryl can't", "voxoryl cant", "you can't yet", "you cant yet", "cannot do yet")
    ):
        text = _strip_prefix(
            message,
            ("voxoryl can't", "voxoryl cant", "you can't yet", "you cant yet", "add this feature to yourself"),
        )
        logged = append_item(text or message, kind=kind or "capability_gap", source="user")
        draft = draft_cursor_prompt(text or message, item_id=(logged.get("item") or {}).get("id"))
        return {
            "ok": True,
            "item": logged.get("item"),
            "cursor_prompt": draft.get("path"),
            "speak": (
                "Fair — I logged that gap and drafted a Cursor prompt so we can grow into it. "
                + str(draft.get("path") or "")
            ),
        }

    # default log feature
    text = _strip_prefix(message, ("add this feature to yourself", "improve yourself", "self upgrade:"))
    kind_final = kind or ("bug" if "bug" in lower else "feature")
    return append_item(text or message, kind=kind_final, source="user")
