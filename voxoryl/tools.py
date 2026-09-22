from __future__ import annotations

import asyncio
import email
import imaplib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from email.header import decode_header
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx

from voxoryl.config import settings
from voxoryl.memory import memory


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_under(root: Path, target: Path) -> Path:
    root = root.resolve()
    target = (root / target).resolve() if not target.is_absolute() else target.resolve()
    if root not in target.parents and target != root:
        raise ValueError(f"Path escapes sandbox: {target}")
    return target


# --- Research -----------------------------------------------------------------


async def tool_research(query: str, *, deep: bool = True) -> dict[str, Any]:
    """Always-on multi-lane research (docs > reference > discounted community)."""
    from voxoryl.research import tool_research as _deep

    return await _deep(query, deep=deep)


async def tool_market_scan(topic: str | None = None) -> dict[str, Any]:
    profile = memory.read().get("profile", {})
    projects = profile.get("projects") or []
    focus = topic or (", ".join(projects) if projects else "indie AI startups local agents")
    queries = [
        f"{focus} market trends 2026",
        f"{focus} competitor landscape",
        f"{focus} content marketing ideas",
    ]
    findings = []
    for q in queries:
        findings.append(await tool_research(q))
    brief_path = settings.voxoryl_data_dir / "market_briefs.md"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"\n## Market scan {_now()}\nFocus: {focus}\n"]
    for f in findings:
        lines.append(f"### {f.get('heading')}\n{f.get('abstract') or 'No abstract'}\n")
        for rel in f.get("related") or []:
            lines.append(f"- {rel.get('title')} ({rel.get('url')})")
        lines.append("")
    with brief_path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    memory.remember_fact(f"Market scan saved for: {focus}", tags=["market", "research"])
    return {"ok": True, "focus": focus, "findings": findings, "path": str(brief_path)}


# --- Notes / Obsidian ---------------------------------------------------------


async def tool_notes(note: str, title: str | None = None) -> dict[str, Any]:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    safe_title = re.sub(r"[^\w\s\-]+", "", (title or "voxoryl-note")).strip().replace(" ", "-")[:60] or "voxoryl-note"
    body = f"# {title or 'Voxoryl note'}\n\n_{stamp}_\n\n{note.strip()}\n"

    inbox = settings.voxoryl_data_dir / "notes_inbox.md"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    with inbox.open("a", encoding="utf-8") as f:
        f.write(f"\n## {stamp} — {title or 'note'}\n{note.strip()}\n")

    written = [str(inbox)]
    vault = settings.obsidian_vault
    if vault:
        vault = Path(vault)
        if vault.exists():
            dest_dir = vault / "Voxoryl"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{stamp}-{safe_title}.md"
            dest.write_text(body, encoding="utf-8")
            written.append(str(dest))

    memory.remember_fact(f"Saved note: {(title or note)[:120]}", tags=["notes"])
    return {"ok": True, "paths": written, "obsidian": bool(vault and Path(vault).exists())}


# --- Memory -------------------------------------------------------------------


async def tool_remember(text: str, kind: str = "fact") -> dict[str, Any]:
    if kind == "project":
        memory.update_profile(projects=[text])
        return {"ok": True, "stored": "project", "text": text}
    if kind == "goal":
        memory.update_profile(goals=[text])
        return {"ok": True, "stored": "goal", "text": text}
    if kind == "preference":
        memory.update_profile(preferences=[text])
        return {"ok": True, "stored": "preference", "text": text}
    entry = memory.remember_fact(text, tags=["manual"])
    return {"ok": True, "stored": "fact", "entry": entry}


# --- GitHub -------------------------------------------------------------------


async def tool_github(action: str = "list", repo: str = "", path: str = "README.md") -> dict[str, Any]:
    token = settings.github_token.strip()
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "VoxorylLocal/0.2"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            if action == "list":
                if settings.github_user:
                    url = f"https://api.github.com/users/{settings.github_user}/repos?sort=updated&per_page=15"
                elif token:
                    url = "https://api.github.com/user/repos?sort=updated&per_page=15"
                else:
                    return {
                        "ok": False,
                        "error": "Set GITHUB_TOKEN or GITHUB_USER in .env",
                        "hint": "https://github.com/settings/tokens",
                    }
                r = await client.get(url)
                if r.status_code >= 400:
                    return {"ok": False, "error": r.text}
                repos = [
                    {
                        "name": x.get("full_name"),
                        "url": x.get("html_url"),
                        "desc": x.get("description"),
                        "updated": x.get("updated_at"),
                    }
                    for x in r.json()
                ]
                memory.remember_fact(f"Listed {len(repos)} GitHub repos", tags=["github"])
                return {"ok": True, "repos": repos}

            if action == "readme" and repo:
                r = await client.get(f"https://api.github.com/repos/{repo}/readme")
                if r.status_code >= 400:
                    return {"ok": False, "error": r.text}
                import base64

                content = base64.b64decode(r.json().get("content", "")).decode("utf-8", errors="replace")
                return {"ok": True, "repo": repo, "readme": content[:6000]}

            if action == "file" and repo and path:
                r = await client.get(f"https://api.github.com/repos/{repo}/contents/{path}")
                if r.status_code >= 400:
                    return {"ok": False, "error": r.text}
                import base64

                data = r.json()
                if isinstance(data, list):
                    return {"ok": True, "repo": repo, "path": path, "entries": [e.get("name") for e in data]}
                content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
                return {"ok": True, "repo": repo, "path": path, "content": content[:8000]}

    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    return {"ok": False, "error": f"Unknown github action: {action}"}


# --- Email --------------------------------------------------------------------


async def tool_email(limit: int = 8) -> dict[str, Any]:
    host = settings.email_imap_host.strip()
    user = settings.email_imap_user.strip()
    password = settings.email_imap_password
    if not (host and user and password):
        return {
            "ok": False,
            "error": "Email not configured",
            "hint": "Set EMAIL_IMAP_HOST, EMAIL_IMAP_USER, EMAIL_IMAP_PASSWORD in .env",
        }

    def _fetch() -> list[dict[str, str]]:
        mail = imaplib.IMAP4_SSL(host)
        mail.login(user, password)
        mail.select(settings.email_imap_folder)
        _, data = mail.search(None, "ALL")
        ids = data[0].split()[-limit:]
        messages = []
        for num in reversed(ids):
            _, msg_data = mail.fetch(num, "(RFC822.HEADER)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            def dec(val: str | None) -> str:
                if not val:
                    return ""
                parts = decode_header(val)
                out = []
                for text, enc in parts:
                    if isinstance(text, bytes):
                        out.append(text.decode(enc or "utf-8", errors="replace"))
                    else:
                        out.append(text)
                return "".join(out)

            messages.append(
                {
                    "from": dec(msg.get("From")),
                    "subject": dec(msg.get("Subject")),
                    "date": dec(msg.get("Date")),
                }
            )
        mail.logout()
        return messages

    try:
        messages = await asyncio.to_thread(_fetch)
        memory.remember_fact(f"Checked email: {len(messages)} recent messages", tags=["email"])
        return {"ok": True, "messages": messages}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# --- Computer / workspace -----------------------------------------------------


async def tool_computer(action: str, path: str = "", query: str = "") -> dict[str, Any]:
    root = settings.voxoryl_workspace
    root.mkdir(parents=True, exist_ok=True)

    try:
        if action == "list":
            target = _safe_under(root, Path(path or "."))
            if not target.exists():
                return {"ok": False, "error": f"Missing: {target}"}
            entries = []
            for p in sorted(target.iterdir()):
                entries.append({"name": p.name, "type": "dir" if p.is_dir() else "file", "size": p.stat().st_size if p.is_file() else None})
            return {"ok": True, "path": str(target), "entries": entries[:200]}

        if action == "search":
            hits = []
            q = (query or path).lower()
            for p in root.rglob("*"):
                if q in p.name.lower():
                    hits.append(str(p.relative_to(root)))
                if len(hits) >= 40:
                    break
            return {"ok": True, "query": q, "hits": hits}

        if action in {"delete", "unlink", "remove"}:
            from voxoryl.safety import may_delete_path, record_file_write, safe_unlink

            # Prefer explicit path; fall back to query. Absolute paths allowed only under project.
            raw = (path or query or "").strip()
            if not raw:
                return {"ok": False, "error": "Provide path to delete", "speak": "Which file should I delete?"}
            target = Path(raw)
            if not target.is_absolute():
                target = _safe_under(root, target)
            gate = may_delete_path(target)
            if not gate.get("ok"):
                return gate
            result = safe_unlink(target)
            if result.get("ok") and result.get("deleted"):
                record_file_write(target, created=False, source="tool_computer_delete")
            return result

        if action == "organize":
            target = _safe_under(root, Path(path or "."))
            buckets = {"docs": {".md", ".txt", ".pdf", ".doc", ".docx"}, "images": {".png", ".jpg", ".jpeg", ".webp", ".gif"}, "video": {".mp4", ".mov", ".mkv"}, "code": {".py", ".js", ".ts", ".json"}, "other": set()}
            moved = []
            for p in list(target.iterdir()):
                if not p.is_file():
                    continue
                ext = p.suffix.lower()
                bucket = "other"
                for name, exts in buckets.items():
                    if ext in exts:
                        bucket = name
                        break
                dest_dir = target / bucket
                dest_dir.mkdir(exist_ok=True)
                dest = dest_dir / p.name
                shutil.move(str(p), str(dest))
                moved.append(f"{p.name} → {bucket}/")
            memory.remember_fact(f"Organized {len(moved)} files in workspace", tags=["computer"])
            return {"ok": True, "moved": moved, "path": str(target)}

        if action == "open":
            target = query or path
            if not target:
                return {"ok": False, "error": "Provide path or URL to open"}

            def _open() -> None:
                if sys.platform.startswith("win"):
                    import os

                    os.startfile(target)  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.run(["open", target], check=False)
                else:
                    subprocess.run(["xdg-open", target], check=False)

            await asyncio.to_thread(_open)
            return {"ok": True, "opened": target}

        if action == "mkdir":
            target = _safe_under(root, Path(path))
            target.mkdir(parents=True, exist_ok=True)
            try:
                from voxoryl.safety import record_file_write

                record_file_write(target, created=True, source="tool_computer_mkdir")
            except Exception:
                pass
            return {"ok": True, "created": str(target)}

        if action == "write":
            from voxoryl.safety import is_under_project, record_file_write

            raw = (path or "").strip()
            if not raw:
                return {"ok": False, "error": "Provide path to write"}
            target = Path(raw)
            if not target.is_absolute():
                target = _safe_under(root, target)
            elif not is_under_project(target):
                from voxoryl.safety import refuse

                return refuse("file", detail=f"Write outside project blocked: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(query or "", encoding="utf-8")
            record_file_write(target, created=True, source="tool_computer_write")
            return {"ok": True, "written": str(target), "chars": len(query or "")}

    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    return {"ok": False, "error": f"Unknown computer action: {action}"}


# --- Marketing ----------------------------------------------------------------


async def tool_marketing(brief: str, channel: str = "general") -> dict[str, Any]:
    from voxoryl.llm import chat_local

    prompt = (
        f"You are Voxoryl writing marketing for Saint.\n"
        f"Channel: {channel}\nBrief: {brief}\n\n"
        "Produce: 1 hook, 3 post variants, 5 hashtags, 1 CTA. Passionate, specific, not cringe."
    )
    copy = await chat_local(
        [
            {"role": "system", "content": "Write sharp marketing copy."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.8,
    )
    out = settings.voxoryl_data_dir / "marketing"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    path = out / f"{stamp}-{channel}.md"
    path.write_text(f"# Marketing — {channel}\n\n{brief}\n\n{copy}\n", encoding="utf-8")
    await tool_notes(copy, title=f"Marketing {channel}")
    memory.remember_fact(f"Marketing pack for {channel}: {brief[:100]}", tags=["marketing"])
    return {"ok": True, "channel": channel, "path": str(path), "copy": copy}


# --- AI video -----------------------------------------------------------------


async def tool_video(prompt: str, mode: str = "script") -> dict[str, Any]:
    from voxoryl.llm import chat_local

    if mode == "script":
        script = await chat_local(
            [
                {
                    "role": "system",
                    "content": "You write cinematic short-form AI video scripts and shot lists.",
                },
                {
                    "role": "user",
                    "content": (
                        f"Create a 20–30s AI video plan for: {prompt}\n"
                        "Include: title, voiceover, 5 shots with visual prompts suitable for ComfyUI/SVD, music mood."
                    ),
                },
            ],
            temperature=0.75,
        )
        out = settings.voxoryl_data_dir / "video_jobs"
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        path = out / f"{stamp}-script.md"
        path.write_text(script, encoding="utf-8")
        memory.remember_fact(f"Video script: {prompt[:100]}", tags=["video"])
        return {"ok": True, "mode": "script", "path": str(path), "script": script}

    # Probe local ComfyUI for generation-ready status
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.comfyui_url}/system_stats")
            ready = r.status_code == 200
            stats = r.json() if ready else {}
    except Exception as exc:
        return {
            "ok": False,
            "mode": "generate",
            "error": f"ComfyUI not reachable at {settings.comfyui_url}: {exc}",
            "hint": "Install ComfyUI locally for AI video; Voxoryl already writes scripts/prompts.",
            "script_fallback": await tool_video(prompt, mode="script"),
        }

    job = {
        "prompt": prompt,
        "queued_at": _now(),
        "comfyui": settings.comfyui_url,
        "note": "Wire a workflow JSON next — ComfyUI is online.",
        "stats": stats,
    }
    out = settings.voxoryl_data_dir / "video_jobs"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"job-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(job, indent=2), encoding="utf-8")
    return {"ok": True, "mode": "generate", "comfyui_ready": ready, "path": str(path), "job": job}


# --- Plan execution -----------------------------------------------------------


async def execute_plan(request: str, synthesis: dict[str, Any]) -> dict[str, Any]:
    needs = [str(x).lower() for x in (synthesis.get("needs_tools") or [])]
    text = request.lower()
    actions: list[dict[str, Any]] = []

    def wants(*keys: str) -> bool:
        return any(k in needs or k in text for k in keys)

    if wants("research", "market"):
        if "market" in text or "market" in needs:
            actions.append({"tool": "market_scan", "result": await tool_market_scan()})
        else:
            actions.append({"tool": "research", "result": await tool_research(request)})
    if wants("notes", "obsidian"):
        actions.append({"tool": "notes", "result": await tool_notes(str(synthesis.get("decision", request)))})
    if wants("memory"):
        actions.append({"tool": "memory", "result": await tool_remember(str(synthesis.get("decision", request))[:300])})
    if wants("knowledge"):
        from voxoryl.knowledge import log_message_to_knowledge

        actions.append({"tool": "knowledge", "result": await log_message_to_knowledge(request)})
    if wants("github"):
        actions.append({"tool": "github", "result": await tool_github("list")})
    if wants("email"):
        actions.append({"tool": "email", "result": await tool_email()})
    if wants("computer", "organize", "desktop"):
        act = "organize" if "organize" in text or "rearrange" in text else "list"
        actions.append({"tool": "computer", "result": await tool_computer(act)})
    if wants("screen", "click", "type", "fill"):
        from voxoryl.screen import tool_screen

        act = "look" if "look" in text or "see" in text or "describe" in text else "act"
        actions.append({"tool": "screen", "result": await tool_screen(action=act, goal=request)})
    if wants("marketing", "promo", "copy"):
        actions.append({"tool": "marketing", "result": await tool_marketing(request)})
    if wants("video", "reel", "short"):
        actions.append({"tool": "video", "result": await tool_video(request, mode="script")})

    if not actions:
        actions.append(
            {
                "tool": "memory",
                "result": await tool_remember(
                    f"Council decision for '{request[:80]}': {str(synthesis.get('decision', ''))[:200]}"
                ),
            }
        )
    return {"actions": actions}


# Back-compat aliases
tool_notes_stub = tool_notes
