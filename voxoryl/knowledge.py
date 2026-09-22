from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxoryl.config import settings
from voxoryl.llm import chat_local, parse_json_loose
from voxoryl.memory import memory


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class KnowledgeStore:
    """Topic-headed life log: new subjects get new ## headings; facts append under them."""

    def __init__(self) -> None:
        self.path = settings.knowledge_path
        self._ensure_file()

    def _owner(self) -> str:
        return str(memory.read().get("profile", {}).get("owner") or "Owner")

    def _ensure_file(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            return
        owner = self._owner()
        self.path.write_text(
            (
                f"# {owner}'s Knowledge Log\n\n"
                "Voxoryl stores what you tell it here — one heading per topic, "
                "facts and asks listed underneath. New topics create new headings.\n"
                "Related topics are linked as [[Wikilinks]] and shown on the mind map.\n\n"
            ),
            encoding="utf-8",
        )

    def read(self) -> str:
        self._ensure_file()
        return self.path.read_text(encoding="utf-8")

    def list_headings(self) -> list[str]:
        text = self.read()
        return re.findall(r"^##\s+(.+?)\s*$", text, flags=re.MULTILINE)

    def sections(self) -> dict[str, list[str]]:
        """Parse ## Heading blocks into {heading: [fact strings]}."""
        text = self.read()
        parts = re.split(r"^##\s+", text, flags=re.MULTILINE)
        out: dict[str, list[str]] = {}
        for part in parts[1:]:
            lines = part.splitlines()
            if not lines:
                continue
            heading = lines[0].strip()
            facts = []
            for line in lines[1:]:
                m = re.match(r"^-\s+\*\*[^*]+\*\*\s+[—\-]\s+(.+)$", line.strip())
                if m:
                    facts.append(m.group(1).strip())
                elif line.strip().startswith("- "):
                    facts.append(line.strip()[2:].strip())
            out[heading] = facts
        return out

    def _match_heading(self, candidate: str, existing: list[str]) -> str | None:
        c = candidate.strip().lower()
        if not c:
            return None
        for h in existing:
            hl = h.lower()
            if c == hl or c in hl or hl in c:
                return h
            if c.rstrip("s") == hl.rstrip("s"):
                return h
        return None

    def retrieve_relevant(self, query: str, *, limit: int = 4) -> dict[str, Any]:
        """
        Find knowledge sections related to a query so Voxoryl can reference them.
        Hybrid: keyword score + optional embedding similarity (nomic-embed-text).
        """
        q = query.strip().lower()
        if not q:
            return {"ok": True, "matches": [], "context": ""}

        # Never retrieve vault noise for greetings / fillers
        if re.match(
            r"^(hello|hi|hey|yo|thanks|thank you|ty|thx|ok|okay|cool|bye|goodbye|"
            r"good (morning|afternoon|evening|night)|how are you|what'?s up)[\s!.?]*$",
            q,
        ):
            return {"ok": True, "matches": [], "context": ""}

        sections = self.sections()
        if not sections:
            return {"ok": True, "matches": [], "context": ""}

        tokens = {
            t
            for t in re.findall(r"[a-z0-9]{3,}", q)
            if t not in {"the", "and", "for", "you", "what", "about", "with", "have", "this", "that"}
        }
        from voxoryl.mindmap import mindmap

        scored: list[tuple[float, str, list[str], list[dict[str, Any]]]] = []
        for heading, facts in sections.items():
            score = 0.0
            hl = heading.lower()
            if hl in q or any(w in q for w in hl.split() if len(w) > 2):
                score += 5.0
            blob = " ".join(facts).lower()
            for t in tokens:
                if t in hl:
                    score += 2.0
                if t in blob:
                    score += 1.0
            for t in tokens:
                if t.rstrip("s") == hl.rstrip("s"):
                    score += 3.0
            neighbors = mindmap.neighbors(heading)
            for nb in neighbors:
                nhl = str(nb.get("heading", "")).lower()
                if nhl and (nhl in q or any(t in nhl for t in tokens)):
                    score += 1.5
            scored.append((score, heading, facts, neighbors))

        # Embedding boost (async bridge via sync event loop helper)
        try:
            import asyncio
            from voxoryl.embeddings import rank_by_embedding

            docs = {h: "\n".join(f) for h, f in sections.items()}

            async def _emb() -> dict[str, float]:
                ranks = await rank_by_embedding(query, docs, limit=limit * 2)
                return {h: s for h, s in ranks}

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                # Caller is async — embedding applied in retrieve_relevant_async
                emb_scores = {}
            else:
                emb_scores = asyncio.run(_emb())
            if emb_scores:
                scored = [
                    (kw + 8.0 * emb_scores.get(h, 0.0), h, f, n) for kw, h, f, n in scored
                ]
        except Exception:
            pass

        scored = [s for s in scored if s[0] > 0]
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:limit]
        matches = []
        blocks = []
        for score, heading, facts, neighbors in top:
            matches.append(
                {
                    "heading": heading,
                    "score": round(score, 2),
                    "facts": facts[-8:],
                    "links": neighbors,
                }
            )
            fact_lines = "\n".join(f"  - {f}" for f in facts[-8:]) or "  - (no facts yet)"
            link_bits = ", ".join(f"{n['heading']} ({n.get('relation')})" for n in neighbors[:4]) or "none yet"
            blocks.append(f"## {heading}\n{fact_lines}\n  Links: {link_bits}")

        return {"ok": True, "matches": matches, "context": "\n\n".join(blocks)}

    async def retrieve_relevant_async(self, query: str, *, limit: int = 4) -> dict[str, Any]:
        """Async hybrid retrieve with embeddings (preferred from agent)."""
        base = self.retrieve_relevant(query, limit=max(limit, 8))
        sections = self.sections()
        if not sections:
            return base
        # Strong keyword hit → skip embed model swap (keeps qwen warm on 6GB VRAM)
        top_kw = (base.get("matches") or [{}])[0].get("score") or 0
        if float(top_kw) >= 5.0:
            return base
        try:
            from voxoryl.embeddings import rank_by_embedding
            from voxoryl.mindmap import mindmap

            # Only embed keyword candidates + a few extras, not the whole vault
            candidates = {m["heading"]: "\n".join(sections.get(m["heading"], [])) for m in (base.get("matches") or [])}
            if len(candidates) < 6:
                for h, facts in list(sections.items())[:8]:
                    candidates.setdefault(h, "\n".join(facts))
            ranks = await rank_by_embedding(query, candidates, limit=limit)
            if not ranks:
                return base
            matches = []
            blocks = []
            for heading, sim in ranks:
                facts = sections.get(heading, [])
                neighbors = mindmap.neighbors(heading)
                kw = next((m["score"] for m in base.get("matches", []) if m["heading"] == heading), 0)
                matches.append(
                    {
                        "heading": heading,
                        "score": round(float(kw) + 8.0 * float(sim), 2),
                        "embedding": round(float(sim), 4),
                        "facts": facts[-8:],
                        "links": neighbors,
                    }
                )
                fact_lines = "\n".join(f"  - {f}" for f in facts[-8:]) or "  - (no facts yet)"
                link_bits = ", ".join(f"{n['heading']} ({n.get('relation')})" for n in neighbors[:4]) or "none yet"
                blocks.append(f"## {heading}\n{fact_lines}\n  Links: {link_bits}")
            seen = {m["heading"] for m in matches}
            for m in base.get("matches", []):
                if m["heading"] not in seen and len(matches) < limit:
                    matches.append(m)
                    facts = m.get("facts") or []
                    fact_lines = "\n".join(f"  - {f}" for f in facts) or "  - (no facts yet)"
                    blocks.append(f"## {m['heading']}\n{fact_lines}")
            return {"ok": True, "matches": matches[:limit], "context": "\n\n".join(blocks[:limit])}
        except Exception:
            return base

    def append_facts(self, heading: str, facts: list[str], *, date: str | None = None) -> dict[str, Any]:
        """Create heading if missing; append dated bullets under it."""
        self._ensure_file()
        heading = re.sub(r"\s+", " ", heading.strip().title()) or "General"
        facts = [f.strip() for f in facts if f and f.strip()]
        if not facts:
            return {"ok": False, "error": "no facts", "heading": heading}

        existing = self.list_headings()
        matched = self._match_heading(heading, existing)
        created = False
        if matched:
            heading = matched
        else:
            created = True

        stamp = date or _today()
        text = self.read()
        section = ""
        if not created:
            m = re.search(
                rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)",
                text,
                flags=re.MULTILINE | re.DOTALL,
            )
            section = m.group(1) if m else ""
        section_norm = re.sub(r"[*_`]", "", section).lower()

        fresh: list[str] = []
        for fact in facts:
            key = fact.strip().rstrip(".").lower()
            if key and key not in section_norm:
                fresh.append(fact.strip())
                section_norm += "\n" + key
        if not fresh:
            return {
                "ok": True,
                "heading": heading,
                "created_heading": False,
                "facts": [],
                "deduped": True,
                "path": str(self.path.resolve()),
                "date": stamp,
            }

        bullets = "\n".join(f"- **{stamp}** — {fact}" for fact in fresh)

        text = text.rstrip() + "\n"
        if created:
            text += f"\n## {heading}\n\n{bullets}\n"
        else:
            pattern = re.compile(
                rf"(^##\s+{re.escape(heading)}\s*\n)(.*?)(?=^##\s+|\Z)",
                flags=re.MULTILINE | re.DOTALL,
            )

            def _inject(m: re.Match[str]) -> str:
                body = m.group(2).rstrip()
                if body:
                    return f"{m.group(1)}{body}\n{bullets}\n\n"
                return f"{m.group(1)}\n{bullets}\n\n"

            new_text, n = pattern.subn(_inject, text, count=1)
            if n == 0:
                text += f"\n## {heading}\n\n{bullets}\n"
                created = True
            else:
                text = new_text

        self.path.write_text(text.rstrip() + "\n", encoding="utf-8")
        return {
            "ok": True,
            "heading": heading,
            "created_heading": created,
            "facts": fresh,
            "path": str(self.path.resolve()),
            "date": stamp,
        }

    def inject_wikilinks(self, heading: str, linked_headings: list[str]) -> None:
        """Add a Related line with [[Wikilinks]] under a heading (Obsidian-style)."""
        if not linked_headings:
            return
        text = self.read()
        pattern = re.compile(
            rf"(^##\s+{re.escape(heading)}\s*\n)(.*?)(?=^##\s+|\Z)",
            flags=re.MULTILINE | re.DOTALL,
        )
        links = " · ".join(f"[[{h}]]" for h in linked_headings)

        def _inject(m: re.Match[str]) -> str:
            body = m.group(2)
            if "Related:" in body:
                body = re.sub(r"^Related:.*$", f"Related: {links}", body, count=1, flags=re.MULTILINE)
                if "Related:" not in body:
                    body = f"Related: {links}\n{body}"
            else:
                body = f"Related: {links}\n{body}"
            return f"{m.group(1)}{body}"

        new_text, n = pattern.subn(_inject, text, count=1)
        if n:
            self.path.write_text(new_text.rstrip() + "\n", encoding="utf-8")

    def context_block(self, max_chars: int = 2500) -> str:
        text = self.read().strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 20] + "\n… (truncated)"


knowledge = KnowledgeStore()


EXTRACT_SYSTEM = """You organize the owner's life knowledge for Voxoryl.
Return ONLY JSON:
{
  "skip": false,
  "heading": "Short topic title (e.g. Bikes, Email, Voxoryl Project)",
  "facts": ["Third-person fact sentences using the owner's name"]
}
Rules:
- Prefer an existing heading from the list when it fits (same topic).
- Create a new short Title-Case heading only for a genuinely new topic.
- facts: concrete, reusable knowledge — likes, owns, did X on date, preferences, project status.
- Always include at least one fact when the message shares personal info OR asks about a lasting topic.
- Use the owner's name in facts when known (from identity / memory), e.g. "<Owner> owns a bicycle".
- If they mention a date/event, put the date in the fact text.
- skip=true ONLY for empty noise or pure system commands with zero lasting content
  (e.g. "hi", "check email" with nothing else). When in doubt, do NOT skip.
- heading should be plural topic nouns when natural: Bikes, Projects, Marketing — not full sentences.
"""


async def log_message_to_knowledge(message: str) -> dict[str, Any]:
    """Classify a user message into a topic heading, append facts, and update the mind-map links."""
    from voxoryl.mindmap import mindmap

    lower = (message or "").strip().lower()
    # Never log greetings / how-are-you into the vault (causes topic bleed)
    if re.match(
        r"^(hello|hi|hey|yo|thanks|ok|okay|how are you|what's up|whats up)\b",
        lower,
    ) or (
        len(lower.split()) <= 28
        and any(m in lower for m in ("how are you", "i am your creator", "i'm your creator", "nice to meet"))
        and not any(k in lower for k in ("bike", "triumph", "project is", "remember"))
    ):
        return {"ok": True, "skipped": True, "reason": "social chat — not lasting knowledge"}

    owner = knowledge._owner()
    existing = knowledge.list_headings()
    existing_line = ", ".join(existing) if existing else "(none yet — create the first heading)"

    raw = await chat_local(
        [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Owner name: {owner}\n"
                    f"Existing headings: {existing_line}\n\n"
                    f"Message:\n{message}\n\n"
                    "Extract heading + facts JSON."
                ),
            },
        ],
        temperature=0.1,
    )
    parsed = parse_json_loose(raw) or {}
    if parsed.get("skip"):
        return {"ok": True, "skipped": True, "reason": "no lasting knowledge"}

    heading = str(parsed.get("heading") or "General").strip()
    facts = parsed.get("facts") or []
    if isinstance(facts, str):
        facts = [facts]
    facts = [str(f).strip() for f in facts if str(f).strip()]

    if not facts:
        facts = [f"{owner} asked Voxoryl: {message.strip()[:240]}"]

    result = knowledge.append_facts(heading, facts)
    if not result.get("ok"):
        return result

    sections = knowledge.sections()
    used_heading = str(result.get("heading") or heading)
    # Sync graph always; skip extra LLM link-discovery on pure questions (keeps 4B responsive)
    is_question = bool(re.search(r"\b(what|who|when|where|which|how|do i|did i|my)\b", message.lower()))
    if is_question and len(facts) <= 1 and "asked Voxoryl" in (facts[0] if facts else ""):
        graph = mindmap.sync_from_knowledge(sections)
        mindmap.render_html(graph)
        link_info = {"links_added": 0, "skipped_llm": True}
    else:
        link_info = await mindmap.discover_links(used_heading, facts, sections)
    linked = [n["heading"] for n in mindmap.neighbors(used_heading)]
    if linked:
        knowledge.inject_wikilinks(used_heading, linked)
    result["mindmap"] = {
        "links_added": link_info.get("links_added"),
        "html": str(settings.mindmap_html_path.resolve()),
        "neighbors": linked,
        "skipped_llm": link_info.get("skipped_llm", False),
    }
    return result


async def tool_knowledge(
    action: str = "log",
    heading: str = "",
    text: str = "",
    message: str = "",
) -> dict[str, Any]:
    if action == "read":
        return {"ok": True, "path": str(knowledge.path.resolve()), "content": knowledge.read()}
    if action == "headings":
        return {"ok": True, "headings": knowledge.list_headings()}
    if action == "retrieve":
        return knowledge.retrieve_relevant(message or text)
    if action == "mindmap":
        from voxoryl.mindmap import mindmap

        graph = mindmap.sync_from_knowledge(knowledge.sections())
        path = mindmap.render_html(graph)
        return {"ok": True, "path": path, "nodes": len(graph.get("nodes", [])), "edges": len(graph.get("edges", []))}
    if action == "add" and heading and text:
        result = knowledge.append_facts(heading, [text])
        from voxoryl.mindmap import mindmap

        sections = knowledge.sections()
        await mindmap.discover_links(str(result.get("heading") or heading), [text], sections)
        return result
    return await log_message_to_knowledge(message or text)
