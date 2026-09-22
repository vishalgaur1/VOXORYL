from __future__ import annotations

"""
Identity / form fields pulled from memory + knowledge for flash form-fill.
"""

import re
from typing import Any

from voxoryl.knowledge import knowledge
from voxoryl.memory import memory


# Canonical keys the form-filler understands
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "full_name": ("full name", "name", "your name", "fullname", "applicant"),
    "first_name": ("first name", "given name", "fname"),
    "last_name": ("last name", "surname", "family name", "lname"),
    "email": ("email", "e-mail", "mail"),
    "phone": ("phone", "mobile", "tel", "telephone", "contact number"),
    "address": ("address", "street", "street address", "addr"),
    "city": ("city", "town"),
    "state": ("state", "province", "region"),
    "postal": ("postal", "zip", "zip code", "pincode", "pin code"),
    "country": ("country", "nation"),
    "company": ("company", "organization", "organisation", "employer"),
    "job_title": ("job title", "title", "role", "designation"),
}


def _split_name(full: str) -> tuple[str, str]:
    parts = [p for p in full.strip().split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def resolve_identity() -> dict[str, str]:
    """
    Build a flat identity map for forms.
    Priority: memory.profile.identity → knowledge facts → owner name.
    """
    data = memory.read()
    profile = data.get("profile") or {}
    identity = dict(profile.get("identity") or {})
    out: dict[str, str] = {k: str(v).strip() for k, v in identity.items() if str(v).strip()}

    owner = str(profile.get("owner") or "").strip()
    if owner:
        out.setdefault("full_name", owner)
        first, last = _split_name(owner)
        if first:
            out.setdefault("first_name", first)
        if last:
            out.setdefault("last_name", last)

    # Mine knowledge.md for common patterns
    blob = knowledge.read()
    patterns = {
        "email": r"\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b",
        "phone": r"\b(\+?\d[\d\-\s]{8,}\d)\b",
        "postal": r"\b(?:pin(?:code)?|zip)\s*[:=]?\s*(\d{5,6})\b",
    }
    for key, pat in patterns.items():
        if key in out:
            continue
        m = re.search(pat, blob, flags=re.I)
        if m:
            out[key] = m.group(1).strip()

    # Prefer ## Identity / Personal / Contact headings
    sections = knowledge.sections()
    for heading, facts in sections.items():
        hl = heading.lower()
        if not any(k in hl for k in ("identity", "personal", "contact", "profile", "address")):
            continue
        for fact in facts:
            fl = fact.lower()
            for key, aliases in FIELD_ALIASES.items():
                if key in out:
                    continue
                for alias in aliases:
                    if alias in fl and (":" in fact or " is " in fl or "—" in fact or "-" in fact):
                        # take after separator
                        for sep in (":", "—", " - ", " is "):
                            if sep in fact:
                                val = fact.split(sep, 1)[1].strip()
                                if val and len(val) < 200:
                                    out[key] = val
                                break

    return out


def identity_prompt_block() -> str:
    fields = resolve_identity()
    if not fields:
        return "(no identity fields saved yet — ask owner to save name/email/phone/address)"
    lines = [f"- {k}: {v}" for k, v in fields.items()]
    return "KNOWN FORM VALUES (paste these whole — never invent):\n" + "\n".join(lines)


def save_identity_fields(**fields: str) -> dict[str, Any]:
    cleaned = {k: str(v).strip() for k, v in fields.items() if str(v).strip()}
    if not cleaned:
        return {"ok": False, "error": "no fields"}
    data = memory.read()
    profile = data.get("profile") or {}
    identity = dict(profile.get("identity") or {})
    identity.update(cleaned)
    memory.update_profile(identity=identity)
    facts = [f"Owner {k.replace('_', ' ')}: {v}" for k, v in cleaned.items()]
    knowledge.append_facts("Identity", facts)
    return {"ok": True, "identity": identity}
