from __future__ import annotations

"""
Generic approval queue — propose → human approve → execute.
Used by media finalize, lead outreach send, and future automations.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxoryl.config import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path() -> Path:
    p = settings.voxoryl_data_dir / "approvals.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> list[dict[str, Any]]:
    path = _path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("items", [])
    except json.JSONDecodeError:
        return []


def _save(items: list[dict[str, Any]]) -> None:
    _path().write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def create_approval(
    kind: str,
    *,
    title: str,
    summary: str = "",
    payload: dict[str, Any] | None = None,
    preview_path: str = "",
) -> dict[str, Any]:
    items = _load()
    item = {
        "id": uuid.uuid4().hex[:10],
        "kind": kind,
        "title": title,
        "summary": summary,
        "payload": payload or {},
        "preview_path": preview_path,
        "status": "pending",
        "created_at": _now(),
        "updated_at": _now(),
    }
    items.append(item)
    _save(items)
    return item


def list_approvals(status: str | None = "pending", limit: int = 40) -> dict[str, Any]:
    items = _load()
    if status:
        items = [i for i in items if i.get("status") == status]
    items = list(reversed(items))[:limit]
    return {"ok": True, "count": len(items), "items": items}


def get_approval(approval_id: str) -> dict[str, Any] | None:
    for item in _load():
        if item.get("id") == approval_id:
            return item
    return None


def decide(
    approval_id: str = "",
    *,
    approve: bool = True,
    notes: str = "",
    approve_all_pending: bool = False,
    kind: str = "",
) -> dict[str, Any]:
    items = _load()
    changed: list[str] = []
    for item in items:
        if approve_all_pending and item.get("status") == "pending":
            if kind and item.get("kind") != kind:
                continue
            item["status"] = "approved" if approve else "rejected"
            item["notes"] = notes
            item["updated_at"] = _now()
            changed.append(item["id"])
        elif approval_id and item.get("id") == approval_id:
            item["status"] = "approved" if approve else "rejected"
            if notes:
                item["notes"] = notes
            item["updated_at"] = _now()
            changed.append(item["id"])
    _save(items)
    if not changed:
        return {"ok": False, "error": "no matching approvals", "speak": "Nothing to approve."}
    verb = "Approved" if approve else "Rejected"
    return {
        "ok": True,
        "changed": changed,
        "status": "approved" if approve else "rejected",
        "speak": f"{verb} {len(changed)} item(s).",
    }


def mark_executed(approval_id: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
    items = _load()
    for item in items:
        if item.get("id") == approval_id:
            item["status"] = "executed"
            item["result"] = result or {}
            item["updated_at"] = _now()
            _save(items)
            return item
    return {"ok": False, "error": "not found"}


async def tool_approvals(
    action: str = "list",
    *,
    approval_id: str = "",
    message: str = "",
    kind: str = "",
    notes: str = "",
) -> dict[str, Any]:
    action = (action or "list").lower()
    msg = (message or "").lower()
    if action in {"list", "pending", "status"}:
        st = "pending"
        if "approved" in msg:
            st = "approved"
        elif "rejected" in msg:
            st = "rejected"
        return list_approvals(status=st)

    import re

    m = re.search(r"\b([a-f0-9]{8,10})\b", message or approval_id)
    aid = approval_id or (m.group(1) if m else "")

    if action in {"approve", "ok", "yes"} or "approve" in msg:
        all_pending = "all" in msg
        result = decide(aid, approve=True, notes=notes, approve_all_pending=all_pending, kind=kind)
        # Auto-run site deploys on approve
        if result.get("ok"):
            follow = []
            for changed_id in result.get("changed") or []:
                item = get_approval(changed_id)
                if item and item.get("kind") == "site_deploy":
                    from voxoryl.sites import handle_site_approval

                    follow.append(await handle_site_approval(changed_id))
            if follow:
                result["deployments"] = follow
                live = next((f.get("url") for f in follow if f.get("url")), None)
                if live:
                    result["speak"] = f"Approved + deployed: {live}"
                elif follow[0].get("speak"):
                    result["speak"] = follow[0]["speak"]
        return result
    if action in {"reject", "no", "deny"} or "reject" in msg:
        all_pending = "all" in msg
        return decide(aid, approve=False, notes=notes, approve_all_pending=all_pending, kind=kind)
    if action == "get":
        item = get_approval(aid)
        return {"ok": bool(item), "item": item}
    return list_approvals()
