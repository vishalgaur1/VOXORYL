from __future__ import annotations

"""
Local vault for legal / govt / ID documents used when helping fill forms (ITR, etc.).
Files never leave the machine. Voxoryl only lists/reads metadata + text extracts you store.
"""

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxoryl.config import settings
from voxoryl.identity import resolve_identity
from voxoryl.knowledge import knowledge


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def vault_root() -> Path:
    p = settings.voxoryl_data_dir / "docs_vault"
    p.mkdir(parents=True, exist_ok=True)
    (p / "inbox").mkdir(parents=True, exist_ok=True)
    return p


def _index_path() -> Path:
    return vault_root() / "index.json"


def _load_index() -> list[dict[str, Any]]:
    path = _index_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("docs", [])
    except json.JSONDecodeError:
        return []


def _save_index(docs: list[dict[str, Any]]) -> None:
    _index_path().write_text(json.dumps(docs, indent=2, ensure_ascii=False), encoding="utf-8")


DOC_TAGS = {
    "pan": ("pan", "permanent account"),
    "aadhaar": ("aadhaar", "aadhar", "uidai"),
    "passport": ("passport",),
    "form16": ("form 16", "form16", "tds"),
    "itr": ("itr", "income tax", "return"),
    "bank": ("bank statement", "passbook", "ifsc"),
    "salary": ("salary slip", "payslip"),
    "gst": ("gst",),
    "ticket": ("ticket", "boarding", "pnr"),
    "id": ("id", "license", "dl ", "driving"),
}


def classify_name(name: str) -> list[str]:
    n = name.lower()
    tags = []
    for tag, keys in DOC_TAGS.items():
        if any(k in n for k in keys):
            tags.append(tag)
    return tags or ["general"]


def register_file(src: Path, *, label: str = "", tags: list[str] | None = None) -> dict[str, Any]:
    src = Path(src)
    if not src.exists():
        return {"ok": False, "error": "file not found"}
    dest_dir = vault_root() / "files"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    tags = tags or classify_name(src.name + " " + label)
    entry = {
        "id": re.sub(r"[^a-z0-9]+", "-", src.stem.lower())[:40],
        "label": label or src.stem,
        "path": str(dest.resolve()),
        "name": dest.name,
        "tags": tags,
        "added_at": _now(),
    }
    docs = [d for d in _load_index() if d.get("path") != entry["path"]]
    docs.append(entry)
    _save_index(docs)
    knowledge.append_facts(
        "Documents",
        [f"Owner has local vault doc '{entry['label']}' tags={','.join(tags)}"],
    )
    return {"ok": True, "doc": entry, "speak": f"Stored locally: {entry['label']} ({', '.join(tags)})"}


def list_docs(tag: str = "") -> dict[str, Any]:
    docs = _load_index()
    # also index loose inbox files
    for p in (vault_root() / "inbox").glob("*"):
        if p.is_file() and not any(d.get("name") == p.name for d in docs):
            register_file(p, label=p.stem)
    docs = _load_index()
    if tag:
        docs = [d for d in docs if tag.lower() in [t.lower() for t in (d.get("tags") or [])] or tag.lower() in (d.get("label") or "").lower()]
    return {"ok": True, "count": len(docs), "docs": docs, "vault": str(vault_root().resolve())}


def find_for_task(task: str) -> dict[str, Any]:
    """Suggest which vault docs to pull for ITR / passport / booking forms."""
    t = task.lower()
    wanted: list[str] = []
    if any(k in t for k in ("itr", "income tax", "tax return", "form 16")):
        wanted = ["pan", "form16", "bank", "salary", "aadhaar"]
    elif any(k in t for k in ("passport",)):
        wanted = ["passport", "aadhaar", "pan"]
    elif any(k in t for k in ("gst",)):
        wanted = ["gst", "pan", "bank"]
    elif any(k in t for k in ("ticket", "flight", "train", "booking")):
        wanted = ["passport", "id", "ticket"]
    else:
        wanted = ["pan", "aadhaar", "id"]
    docs = _load_index()
    matched = []
    missing = []
    for tag in wanted:
        hits = [d for d in docs if tag in (d.get("tags") or [])]
        if hits:
            matched.extend(hits)
        else:
            missing.append(tag)
    identity = resolve_identity()
    return {
        "ok": True,
        "task": task,
        "needed_tags": wanted,
        "matched": matched,
        "missing_tags": missing,
        "identity_fields": identity,
        "speak": (
            f"For this form I can use {len(matched)} local doc(s). "
            + (f"Missing in vault: {', '.join(missing)}. Drop files into data/docs_vault/inbox/." if missing else "Vault looks complete for the basics.")
        ),
        "hint": "Flash-fill will paste identity fields; docs stay local for you to upload on the official site.",
    }


async def tool_docs(
    action: str = "list",
    *,
    message: str = "",
    path: str = "",
    tag: str = "",
    label: str = "",
) -> dict[str, Any]:
    action = (action or "list").lower()
    if action in {"list", "status"}:
        return list_docs(tag=tag)
    if action in {"add", "register", "store"}:
        p = Path(path) if path else None
        if not p or not p.exists():
            # newest inbox
            inbox = sorted((vault_root() / "inbox").glob("*"), key=lambda x: x.stat().st_mtime, reverse=True)
            p = inbox[0] if inbox else None
        if not p:
            return {"ok": False, "error": "no file", "hint": "Put PDF/JPG into data/docs_vault/inbox/", "speak": "Drop a doc into data/docs_vault/inbox/ first."}
        return register_file(p, label=label or p.stem)
    if action in {"for_task", "itr", "prepare", "pull"}:
        return find_for_task(message or tag or "general form")
    return list_docs()
