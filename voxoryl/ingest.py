from __future__ import annotations

"""
Share / reel / WhatsApp-forward ingest.
Turn "save this reel" links & pasted tips into searchable knowledge.
"""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from voxoryl.config import settings
from voxoryl.knowledge import knowledge
from voxoryl.llm import chat_local, parse_json_loose


EXTRACT_SYSTEM = """You turn a social/productivity reel, short, or forwarded tip into lasting knowledge.
Return ONLY JSON:
{
  "heading": "Saved Tools|Productivity Tips|Repos|Learning|Apps",
  "title": "short title",
  "summary": "1-2 sentence what it teaches",
  "tips": ["actionable tip 1", "use X for Y"],
  "repos_or_apps": [{"name":"...", "use_for":"..."}],
  "tags": ["tag"]
}
Rules:
- Prefer concrete tools, repos, workflows the owner can reuse later.
- tips must be standalone facts (third person or imperative is fine).
- If content is empty/noise: tips=[] and summary explains that.
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _vault_path() -> Path:
    path = settings.voxoryl_data_dir / "reel_vault.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "# Reel & share vault\n\n"
            "Tips rescued from Instagram / YouTube / WhatsApp so they stay usable.\n\n",
            encoding="utf-8",
        )
    return path


def _inbox_dir() -> Path:
    path = settings.voxoryl_data_dir / "share_inbox"
    path.mkdir(parents=True, exist_ok=True)
    return path


def extract_urls(text: str) -> list[str]:
    return re.findall(r"https?://[^\s<>\"']+", text or "")


async def _fetch_page_text(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "VoxorylLocal/1.0"})
            if r.status_code >= 400:
                return ""
            html = r.text
            # crude strip
            text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
            text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:6000]
    except Exception:
        return ""


async def _yt_dlp_transcript(url: str) -> str:
    """Optional: pull auto-subs via yt-dlp if installed."""
    import asyncio
    import tempfile

    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--skip-download",
            "--write-auto-sub",
            "--sub-lang",
            "en.*,en",
            "--sub-format",
            "vtt/best",
            "-o",
            "%(id)s",
            url,
            cwd=tempfile.gettempdir(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=45)
    except Exception:
        return ""

    # Find newest vtt near temp
    tmp = Path(tempfile.gettempdir())
    vtts = sorted(tmp.glob("*.vtt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not vtts:
        return ""
    try:
        raw = vtts[0].read_text(encoding="utf-8", errors="ignore")
        lines = []
        for line in raw.splitlines():
            if not line.strip() or line.startswith("WEBVTT") or "-->" in line or line.strip().isdigit():
                continue
            lines.append(re.sub(r"<[^>]+>", "", line).strip())
        # dedupe consecutive
        out: list[str] = []
        for line in lines:
            if not line:
                continue
            if out and out[-1] == line:
                continue
            out.append(line)
        return " ".join(out)[:8000]
    except Exception:
        return ""


async def gather_source_text(*, url: str = "", text: str = "", from_screen: str = "") -> dict[str, Any]:
    parts: list[str] = []
    meta: dict[str, Any] = {"url": url or None, "sources": []}
    if text.strip():
        parts.append(text.strip())
        meta["sources"].append("pasted")
    if from_screen.strip():
        parts.append(f"On-screen / vision transcript:\n{from_screen.strip()}")
        meta["sources"].append("screen")
    urls = extract_urls(url) or extract_urls(text)
    if url and url not in urls:
        urls.insert(0, url)
    for u in urls[:2]:
        meta["url"] = meta["url"] or u
        sub = await _yt_dlp_transcript(u)
        if sub:
            parts.append(f"Transcript from {u}:\n{sub}")
            meta["sources"].append("yt-dlp")
            continue
        page = await _fetch_page_text(u)
        if page:
            parts.append(f"Page text from {u}:\n{page}")
            meta["sources"].append("page")
    body = "\n\n".join(parts).strip()
    meta["chars"] = len(body)
    return {"ok": bool(body), "text": body, **meta}


async def distill_and_save(source_text: str, *, url: str | None = None) -> dict[str, Any]:
    if not source_text.strip():
        return {"ok": False, "error": "empty source — paste the caption/transcript or open the reel on screen"}

    raw = await chat_local(
        [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {
                "role": "user",
                "content": f"URL: {url or '(none)'}\n\nContent:\n{source_text[:7000]}",
            },
        ],
        temperature=0.2,
    )
    parsed = parse_json_loose(raw) or {}
    title = str(parsed.get("title") or "Shared tip").strip()
    summary = str(parsed.get("summary") or "").strip()
    tips = [str(t).strip() for t in (parsed.get("tips") or []) if str(t).strip()]
    repos = parsed.get("repos_or_apps") or []
    heading = str(parsed.get("heading") or "Saved Tools").strip() or "Saved Tools"
    tags = [str(t).strip() for t in (parsed.get("tags") or []) if str(t).strip()]

    for item in repos:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            use_for = str(item.get("use_for") or "").strip()
            if name:
                tips.append(f"Tool/repo `{name}` — use for: {use_for or 'see vault'}")

    if not tips and summary:
        tips = [summary]

    facts = []
    if summary:
        facts.append(f"{title}: {summary}" + (f" ({url})" if url else ""))
    for tip in tips[:12]:
        facts.append(tip if not url else f"{tip} [src: {url}]")

    know = knowledge.append_facts(heading, facts) if facts else {"ok": False, "error": "no tips extracted"}

    # Append structured vault entry
    vault = _vault_path()
    block = (
        f"## {title} — {_now()}\n"
        f"- URL: {url or 'n/a'}\n"
        f"- Tags: {', '.join(tags) or 'none'}\n"
        f"- Summary: {summary or '—'}\n"
    )
    for tip in tips[:12]:
        block += f"- Tip: {tip}\n"
    block += "\n"
    vault.write_text(vault.read_text(encoding="utf-8") + block, encoding="utf-8")

    return {
        "ok": True,
        "heading": heading,
        "title": title,
        "summary": summary,
        "tips": tips,
        "repos_or_apps": repos,
        "tags": tags,
        "knowledge": know,
        "vault": str(vault.resolve()),
        "speak": f"Saved “{title}” under {heading} — {len(tips)} tip(s) in the vault.",
    }


async def tool_ingest_share(
    *,
    url: str = "",
    text: str = "",
    message: str = "",
    use_screen: bool = False,
) -> dict[str, Any]:
    """
    Ingest a reel/link/WhatsApp forward into knowledge + reel_vault.md.
    """
    from voxoryl.screen import computer_use_enabled, tool_screen

    blob = " ".join(x for x in (message, text, url) if x).strip()
    urls = extract_urls(blob)
    primary = url.strip() or (urls[0] if urls else "")
    pasted = text.strip() or (message.strip() if not primary or primary not in message else message.replace(primary, "").strip())

    screen_text = ""
    if use_screen or (not pasted and not primary and computer_use_enabled()):
        look = await tool_screen(
            action="look",
            goal="Read any reel, short, or tip visible on screen. Transcribe captions and the main advice.",
            question="Transcribe the productivity/code tip or reel caption on screen. List tools, repos, and what they're for.",
        )
        if look.get("ok"):
            screen_text = str((look.get("vision") or {}).get("description") or "")

    gathered = await gather_source_text(url=primary, text=pasted or blob, from_screen=screen_text)
    if not gathered.get("ok"):
        # Drain share_inbox drop folder
        inbox_bits = []
        for p in sorted(_inbox_dir().glob("*.txt"))[:5]:
            try:
                inbox_bits.append(p.read_text(encoding="utf-8", errors="ignore"))
                p.rename(p.with_suffix(".done.txt"))
            except Exception:
                pass
        if inbox_bits:
            gathered = await gather_source_text(text="\n\n".join(inbox_bits))

    if not gathered.get("ok"):
        return {
            "ok": False,
            "error": "No content to ingest",
            "hint": "Paste a link + caption, drop a .txt into data/share_inbox/, or open the reel and say 'save this reel from my screen'.",
            "speak": "I need a link, pasted caption, inbox file, or the reel visible on screen.",
        }

    saved = await distill_and_save(str(gathered.get("text") or ""), url=gathered.get("url"))
    saved["gather"] = {k: gathered.get(k) for k in ("sources", "chars", "url")}
    return saved


async def tool_whatsapp(action: str = "status", message: str = "", to: str = "") -> dict[str, Any]:
    """
    Lightweight WhatsApp bridge.
    - status: explain how to use
    - send: open/focus WhatsApp Desktop and paste message (needs computer use)
    - ingest: treat message as a forward/reel share
    """
    action = (action or "status").lower().strip()
    if action in {"ingest", "save", "forward"}:
        return await tool_ingest_share(text=message, message=message, use_screen=False)

    if action == "send":
        from voxoryl.screen import computer_use_enabled, tool_screen

        if not computer_use_enabled():
            return {
                "ok": False,
                "error": "computer use disabled",
                "hint": "Set COMPUTER_USE_ENABLED=true and open WhatsApp Desktop, then retry.",
            }
        if not message.strip():
            return {"ok": False, "error": "empty message"}
        goal = (
            f"Open or focus WhatsApp Desktop. "
            + (f"Search/open chat with {to}. " if to else "Use the currently open chat. ")
            + f"Click the message box and paste this exact message, then send:\n{message[:1500]}"
        )
        result = await tool_screen(action="act", goal=goal)
        return {
            "ok": bool(result.get("ok")),
            "action": "send",
            "to": to or "(current chat)",
            "result": result,
            "speak": result.get("speak") or "Tried to send via WhatsApp Desktop.",
        }

    return {
        "ok": True,
        "action": "status",
        "inbox": str(_inbox_dir().resolve()),
        "vault": str(_vault_path().resolve()),
        "hint": (
            "Share a reel: paste link/caption or drop .txt in share_inbox, or say 'save this reel'. "
            "Talk: open WhatsApp Desktop and say 'send on WhatsApp: …'. "
            "Forwards: 'ingest this WhatsApp tip: …'."
        ),
        "speak": "WhatsApp bridge ready — save forwards to the vault, or send via Desktop when computer use is on.",
    }
