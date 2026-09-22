"""TargetResolver — intent → grounded UI target before adapters execute.

Never jump to vision when ARIA / UIA / recipe can ground the target.
"""

from __future__ import annotations

import re
from typing import Any


def resolve_target(
    intent: str,
    *,
    aria_nodes: list[dict[str, Any]] | None = None,
    uia_nodes: list[dict[str, Any]] | None = None,
    recipe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Input: "click Send"
    Output: {target, name, source, confidence, ref}
    Priority: recipe → ARIA/DOM → UIA → language guess (vision is adapter's last resort).
    """
    text = (intent or "").strip()
    name = ""
    m = re.search(
        r"(?:click|press|tap|open|select)\s+(?:the\s+)?(?:button\s+)?[\"']?(.+?)[\"']?\s*$",
        text,
        re.I,
    )
    if m:
        name = m.group(1).strip()
    else:
        name = text

    if recipe and recipe.get("ok"):
        return {
            "target": str(recipe.get("target") or "recipe"),
            "name": str(recipe.get("name") or name),
            "source": "recipe",
            "confidence": float(recipe.get("confidence") or 0.99),
            "ref": recipe.get("ref"),
            "url": recipe.get("url"),
            "llm_calls": 0,
            "vl_calls": 0,
        }

    hit = _match_nodes(name, aria_nodes or [], source="aria")
    if hit:
        return hit

    hit = _match_nodes(name, uia_nodes or [], source="uia")
    if hit:
        return hit

    return {
        "target": "button",
        "name": name,
        "source": "language",
        "confidence": 0.55 if name else 0.2,
        "ref": None,
        "llm_calls": 0,
        "vl_calls": 0,
    }


def _match_nodes(name: str, nodes: list[dict[str, Any]], *, source: str) -> dict[str, Any] | None:
    for node in nodes:
        n = str(node.get("name") or node.get("label") or node.get("AutomationId") or "")
        role = str(node.get("role") or node.get("ControlType") or "button")
        if n and name and n.lower() == name.lower():
            return {
                "target": role,
                "name": n,
                "source": source,
                "confidence": 0.98,
                "ref": node.get("ref") or node.get("id") or node.get("RuntimeId"),
                "llm_calls": 0,
                "vl_calls": 0,
            }
        if n and name and name.lower() in n.lower():
            return {
                "target": role,
                "name": n,
                "source": source,
                "confidence": 0.85,
                "ref": node.get("ref") or node.get("id") or node.get("RuntimeId"),
                "llm_calls": 0,
                "vl_calls": 0,
            }
    return None
