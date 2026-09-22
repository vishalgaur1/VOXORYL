from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxoryl.config import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, data: Any) -> None:
    _ensure_parent(path)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


class MemoryStore:
    """Long-term memory that grows as Voxoryl learns the owner's patterns."""

    def __init__(self) -> None:
        self.path = settings.memory_path
        if not self.path.exists():
            _save(
                self.path,
                {
                    "profile": {
                        "owner": "Owner",
                        "goals": [],
                        "preferences": [],
                        "projects": [],
                    },
                    "facts": [],
                    "episodes": [],
                    "updated_at": _now(),
                },
            )

    def read(self) -> dict[str, Any]:
        return _load(self.path, {})

    def remember_fact(self, fact: str, tags: list[str] | None = None) -> dict[str, Any]:
        data = self.read()
        entry = {"text": fact, "tags": tags or [], "at": _now()}
        data.setdefault("facts", []).append(entry)
        data["updated_at"] = _now()
        _save(self.path, data)
        return entry

    def remember_episode(self, summary: str, outcome: str) -> dict[str, Any]:
        data = self.read()
        entry = {"summary": summary, "outcome": outcome, "at": _now()}
        data.setdefault("episodes", []).append(entry)
        data["episodes"] = data["episodes"][-200:]
        data["updated_at"] = _now()
        _save(self.path, data)
        return entry

    def update_profile(self, **fields: Any) -> dict[str, Any]:
        data = self.read()
        profile = data.setdefault("profile", {})
        for key, value in fields.items():
            if isinstance(value, list) and isinstance(profile.get(key), list):
                merged = list(dict.fromkeys([*profile[key], *value]))
                profile[key] = merged
            else:
                profile[key] = value
        data["updated_at"] = _now()
        _save(self.path, data)
        return profile

    def context_block(self, limit: int = 12, query: str = "") -> str:
        data = self.read()
        profile = data.get("profile", {})
        facts = data.get("facts", [])[-limit:]
        episodes = data.get("episodes", [])[-6:]
        q = (query or "").lower()
        bike_ok = any(k in q for k in ("bike", "triumph", "motorcycle", "service", "400 xc"))
        lines = [
            f"Owner: {profile.get('owner', 'Saint')}",
            f"Goals: {', '.join(profile.get('goals', [])) or 'none yet'}",
            f"Projects: {', '.join(profile.get('projects', [])) or 'none yet'}",
            f"Preferences: {', '.join(profile.get('preferences', [])) or 'none yet'}",
        ]
        style = profile.get("work_style") or {}
        if style:
            lines.append("Work-style:")
            for k, v in style.items():
                if v and k != "neuro_profiles":  # don't force neuro into every chat
                    lines.append(f"- {k}: {v}")
        lines.append("Recent facts:")
        shown = 0
        if facts:
            for f in facts:
                text = str(f.get("text") or "")
                tl = text.lower()
                if not bike_ok and any(k in tl for k in ("triumph", "bike service", "1st service", "first service")):
                    continue
                lines.append(f"- {text}")
                shown += 1
            if not shown:
                lines.append("- none on-topic")
        else:
            lines.append("- none yet")
        lines.append("Recent chat (summaries only):")
        ep_n = 0
        if episodes:
            for e in episodes:
                summary = str(e.get("summary") or "")
                sl = summary.lower()
                if not bike_ok and any(k in sl for k in ("triumph", "bike", "service")):
                    continue
                # Never dump old assistant outcomes (they cause topic bleed)
                lines.append(f"- user: {summary[:160]}")
                ep_n += 1
            if not ep_n:
                lines.append("- none on-topic")
        else:
            lines.append("- none yet")
        lines.append(
            "HARD: Do not volunteer bike/service facts unless the user message is about bikes."
        )
        return "\n".join(lines)


class SkillStore:
    """Reusable skills Voxoryl must prefer using when relevant."""

    def __init__(self) -> None:
        self.path = settings.skills_path
        if not self.path.exists():
            _save(
                self.path,
                {
                    "skills": [
                        {
                            "id": "council_first",
                            "name": "Council First",
                            "always": True,
                            "description": "Before executing non-trivial work, run the council and wait for synthesis.",
                        },
                        {
                            "id": "remember_patterns",
                            "name": "Remember Patterns",
                            "always": True,
                            "description": "After each meaningful task, store what Saint wanted and what worked.",
                        },
                        {
                            "id": "research_before_advice",
                            "name": "Research Before Advice",
                            "always": False,
                            "description": "When Saint asks about markets or building ideas, gather fresh context first.",
                        },
                    ]
                },
            )

    def list_skills(self) -> list[dict[str, Any]]:
        return _load(self.path, {"skills": []}).get("skills", [])

    def add_skill(self, skill_id: str, name: str, description: str, always: bool = False) -> dict[str, Any]:
        data = _load(self.path, {"skills": []})
        skill = {"id": skill_id, "name": name, "always": always, "description": description}
        skills = [s for s in data.get("skills", []) if s.get("id") != skill_id]
        skills.append(skill)
        data["skills"] = skills
        _save(self.path, data)
        return skill

    def skills_prompt(self) -> str:
        skills = self.list_skills()
        always = [s for s in skills if s.get("always")]
        optional = [s for s in skills if not s.get("always")]
        lines = ["Always-on skills:"]
        if always:
            lines.extend(f"- {s['name']}: {s['description']}" for s in always)
        else:
            lines.append("- none")
        lines.append("Available skills:")
        if optional:
            lines.extend(f"- {s['name']}: {s['description']}" for s in optional)
        else:
            lines.append("- none")
        return "\n".join(lines)


memory = MemoryStore()
skills = SkillStore()
