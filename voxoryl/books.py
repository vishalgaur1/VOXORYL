from __future__ import annotations

"""
Books / great lines vault — local titles the owner cares about under data/books/.
Surfaces a random great line or story beat; optional quote research.
"""

import json
import random
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxoryl.config import settings
from voxoryl.knowledge import knowledge
from voxoryl.memory import memory


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def root() -> Path:
    p = settings.voxoryl_data_dir / "books"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _vault_path() -> Path:
    return root() / "vault.json"


def load_vault() -> dict[str, Any]:
    path = _vault_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"books": [], "lines": [], "updated_at": None, "last_surfaced_at": None}


def save_vault(data: dict[str, Any]) -> None:
    data["updated_at"] = _now()
    _vault_path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


STARTER_LINES = [
    {
        "book": "The Little Prince",
        "line": "What is essential is invisible to the eye.",
        "beat": "Seeing with the heart, not just the eyes.",
    },
    {
        "book": "Meditations",
        "line": "You have power over your mind — not outside events.",
        "beat": "Stoic focus on what you can control.",
    },
    {
        "book": "Atomic Habits",
        "line": "You do not rise to the level of your goals. You fall to the level of your systems.",
        "beat": "Systems beat willpower.",
    },
]


_JUNK_LINE = re.compile(
    r"no solid web hits|confidence:\s*low|rephrase the problem|docs:0|chatter:0|verify source",
    re.I,
)


def _is_junk_line(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 12:
        return True
    return bool(_JUNK_LINE.search(t))


def ensure_starters() -> None:
    vault = load_vault()
    # Scrub failed research scraps so random quotes stay good
    cleaned = [ln for ln in (vault.get("lines") or []) if not _is_junk_line(str(ln.get("line") or ""))]
    if len(cleaned) != len(vault.get("lines") or []):
        vault["lines"] = cleaned
        save_vault(vault)
    if vault.get("lines"):
        return
    for s in STARTER_LINES:
        vault.setdefault("lines", []).append({**s, "id": str(uuid.uuid4())[:8], "at": _now(), "source": "starter"})
        if not any(b.get("title", "").lower() == s["book"].lower() for b in vault.get("books") or []):
            vault.setdefault("books", []).append({"title": s["book"], "at": _now()})
    save_vault(vault)


def add_book(title: str, *, note: str = "") -> dict[str, Any]:
    title = (title or "").strip()
    if not title:
        return {"ok": False, "speak": "Which book should I add? Say: add book <title>."}
    vault = load_vault()
    for b in vault.get("books") or []:
        if str(b.get("title") or "").lower() == title.lower():
            return {"ok": True, "book": b, "speak": f"Already in your vault: {b.get('title')}."}
    entry = {"id": str(uuid.uuid4())[:8], "title": title[:160], "note": note[:400], "at": _now()}
    vault.setdefault("books", []).append(entry)
    save_vault(vault)
    try:
        knowledge.append_facts("Books", [f"Cares about: {title}" + (f" — {note}" if note else "")])
    except Exception:
        pass
    return {"ok": True, "book": entry, "speak": f"Added to your book vault: {title}."}


def add_line(line: str, *, book: str = "", beat: str = "") -> dict[str, Any]:
    line = (line or "").strip()
    if not line:
        return {"ok": False, "speak": "Paste the great line after: great line …"}
    vault = load_vault()
    entry = {
        "id": str(uuid.uuid4())[:8],
        "book": (book or "").strip()[:120],
        "line": line[:500],
        "beat": (beat or "").strip()[:240],
        "at": _now(),
        "source": "user",
    }
    vault.setdefault("lines", []).append(entry)
    vault["lines"] = vault["lines"][-300:]
    if book and not any(str(b.get("title") or "").lower() == book.lower() for b in vault.get("books") or []):
        vault.setdefault("books", []).append({"title": book[:160], "at": _now()})
    save_vault(vault)
    try:
        knowledge.append_facts("Books", [f"Great line ({book or '?'}): {line[:200]}"])
    except Exception:
        pass
    return {"ok": True, "line": entry, "speak": f"Saved great line{f' from {book}' if book else ''}."}


def random_line(*, mild: bool = True) -> dict[str, Any]:
    ensure_starters()
    vault = load_vault()
    lines = [ln for ln in (vault.get("lines") or []) if not _is_junk_line(str(ln.get("line") or ""))]
    if not lines:
        # restore starters if vault was all junk
        vault["lines"] = []
        save_vault(vault)
        ensure_starters()
        vault = load_vault()
        lines = vault.get("lines") or []
    if not lines:
        return {"ok": True, "empty": True, "speak": "No great lines yet — add one with: great line …"}
    pick = random.choice(lines)
    vault["last_surfaced_at"] = _now()
    save_vault(vault)
    book = pick.get("book") or "your vault"
    line = pick.get("line") or ""
    speak = f"From {book}: “{line}” Want another?"
    return {"ok": True, "pick": pick, "speak": speak, "mild": mild}


def related_line(message: str) -> dict[str, Any]:
    ensure_starters()
    vault = load_vault()
    lower = (message or "").lower()
    # knowledge first
    try:
        hits = knowledge.retrieve_relevant(message)
        ctx = ""
        if isinstance(hits, dict):
            ctx = str(hits.get("context") or "")
        elif isinstance(hits, list):
            ctx = " ".join(str(h) for h in hits[:3])
        if ctx and "book" in ctx.lower():
            pass
    except Exception:
        ctx = ""
    scored: list[tuple[int, dict[str, Any]]] = []
    for ln in vault.get("lines") or []:
        blob = f"{ln.get('book','')} {ln.get('line','')} {ln.get('beat','')}".lower()
        score = sum(1 for w in re.findall(r"[a-z]{4,}", lower) if w in blob)
        if score:
            scored.append((score, ln))
    if scored:
        scored.sort(key=lambda x: -x[0])
        pick = scored[0][1]
        speak = f"Related — {pick.get('book') or 'vault'}: “{pick.get('line')}”"
        return {"ok": True, "pick": pick, "speak": speak, "knowledge_snip": ctx[:200]}
    return random_line(mild=True)


async def research_quotes(book_title: str) -> dict[str, Any]:
    title = (book_title or "").strip()
    if not title:
        return {"ok": False, "speak": "Which book? Say: quote from <title>."}
    research: dict[str, Any] = {}
    try:
        from voxoryl.research import deep_research

        research = await deep_research(f"notable quotes from the book {title} genuine sources", conclude=True)
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "hint": "Research unavailable — add a great line manually.",
            "speak": f"Couldn't research quotes for {title} right now. You can paste a line with: great line …",
        }
    abstract = str(research.get("speak") or research.get("abstract") or research.get("conclusion") or "")[:600]
    if _is_junk_line(abstract):
        # Fall back to a vault line instead of storing research failure text
        fallback = random_line(mild=True)
        fallback["researched"] = False
        fallback["speak"] = (
            f"Couldn't pull a fresh web quote for {title}. "
            + str(fallback.get("speak") or "Want a vault line instead?")
        )
        return fallback
    if abstract and len(abstract) > 20:
        add_line(abstract[:280], book=title, beat="researched summary (verify source)")
    return {
        "ok": bool(research.get("ok", True)),
        "book": title,
        "research": research,
        "speak": abstract or f"No clear quotes found yet for {title}.",
    }


def _parse_add_book(message: str) -> str:
    m = re.search(r"add book\s*[:\-]?\s*(.+)$", message or "", re.I)
    if m:
        return m.group(1).strip().strip('"')
    return ""


def _parse_great_line(message: str) -> tuple[str, str]:
    raw = message or ""
    m = re.search(r"great line\s*(?:from\s+(.+?))?\s*[:\-]\s*(.+)$", raw, re.I | re.S)
    if m:
        return (m.group(2) or "").strip(), (m.group(1) or "").strip()
    m2 = re.search(r"great line\s+(.+)$", raw, re.I | re.S)
    if m2:
        return m2.group(1).strip(), ""
    return "", ""


def _parse_quote_from(message: str) -> str:
    m = re.search(r"quote from\s+(.+)$", message or "", re.I)
    if m:
        return m.group(1).strip().strip('"')
    return ""


async def tool_books(action: str = "random", *, message: str = "") -> dict[str, Any]:
    action = (action or "random").lower().strip()
    lower = (message or "").lower()
    ensure_starters()

    if action in {"add", "add_book"} or "add book" in lower:
        title = _parse_add_book(message) or re.sub(r"add book\s*", "", message or "", flags=re.I).strip()
        return add_book(title)

    if action in {"line", "great_line", "add_line"} or "great line" in lower:
        line, book = _parse_great_line(message)
        if not line:
            return {"ok": False, "speak": "Format: great line from Book: the quote here"}
        return add_line(line, book=book)

    if action in {"quote", "research"}:
        title = _parse_quote_from(message)
        # Vague "any book" → vault random, not failed web research
        if not title or title.lower() in {"any book", "a book", "some book", "books"}:
            return random_line(mild=True)
        return await research_quotes(title)

    if "quote from" in lower and action not in {"random", "line", "list"}:
        title = _parse_quote_from(message)
        if not title or title.lower() in {"any book", "a book", "some book", "books"}:
            return random_line(mild=True)
        return await research_quotes(title)

    if action in {"related", "retrieve"} or any(k in lower for k in ("related to", "about this book")):
        return related_line(message)

    if action in {"list", "vault"}:
        vault = load_vault()
        titles = [b.get("title") for b in vault.get("books") or []][:12]
        speak = ("Books: " + ", ".join(str(t) for t in titles)) if titles else "Vault empty — add book <title>."
        return {"ok": True, "books": vault.get("books"), "lines_count": len(vault.get("lines") or []), "speak": speak}

    # default: random great line
    return random_line(mild=True)


def daemon_book_nudge() -> dict[str, Any]:
    """Mild optional surface for wellbeing / daily tick — never spammy."""
    vault = load_vault()
    last = vault.get("last_surfaced_at")
    if last:
        try:
            prev = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - prev).total_seconds() < 20 * 3600:
                return {"skipped": True, "reason": "already surfaced today-ish"}
        except Exception:
            pass
    if not (vault.get("lines") or vault.get("books")):
        return {"skipped": True, "reason": "empty vault"}
    return random_line(mild=True)
