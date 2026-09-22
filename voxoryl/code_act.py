from __future__ import annotations

"""
smolagents-inspired code-as-action: small models write short Python, we exec in a sandbox.
Restricted to VOXORYL_WORKSPACE — no network, limited builtins.
"""

import ast
import contextlib
import io
from typing import Any

from voxoryl.config import settings
from voxoryl.llm import chat_local


FORBIDDEN = {
    "eval",
    "exec",
    "open",
    "__import__",
    "compile",
    "input",
    "breakpoint",
    "memoryview",
}


def _safe(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            return False
        if isinstance(child, ast.Attribute) and child.attr.startswith("__"):
            return False
        if isinstance(child, ast.Name) and child.id in FORBIDDEN:
            return False
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id in FORBIDDEN:
            return False
    return True


async def generate_code_action(goal: str) -> str:
    raw = await chat_local(
        [
            {
                "role": "system",
                "content": (
                    "Write a short Python snippet for the goal. No imports. "
                    "Use only: print, len, range, str, int, float, list, dict, Path-like via WORKSPACE str paths already given. "
                    "Variables available: WORKSPACE (str path), RESULT (dict you may fill). "
                    "Return ONLY code, no markdown."
                ),
            },
            {"role": "user", "content": goal},
        ],
        temperature=0.1,
    )
    code = raw.strip()
    if code.startswith("```"):
        code = code.strip("`")
        if code.startswith("python"):
            code = code[6:].strip()
    return code


def run_sandbox_code(code: str) -> dict[str, Any]:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return {"ok": False, "error": f"syntax: {exc}"}
    if not _safe(tree):
        return {"ok": False, "error": "Code rejected by sandbox (imports/unsafe calls)."}

    workspace = str(settings.voxoryl_workspace.resolve())
    settings.voxoryl_workspace.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {}
    stdout = io.StringIO()
    safe_builtins = {
        "print": print,
        "len": len,
        "range": range,
        "str": str,
        "int": int,
        "float": float,
        "list": list,
        "dict": dict,
        "min": min,
        "max": max,
        "sum": sum,
        "sorted": sorted,
        "enumerate": enumerate,
        "zip": zip,
        "True": True,
        "False": False,
        "None": None,
    }
    glb = {"__builtins__": safe_builtins, "WORKSPACE": workspace, "RESULT": result}
    try:
        with contextlib.redirect_stdout(stdout):
            exec(compile(tree, "<voxoryl_sandbox>", "exec"), glb, glb)  # noqa: S102 — intentional sandbox
        return {
            "ok": True,
            "stdout": stdout.getvalue()[:4000],
            "result": glb.get("RESULT") or result,
            "code": code[:2000],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "stdout": stdout.getvalue()[:1000], "code": code[:2000]}


async def tool_code_act(goal: str) -> dict[str, Any]:
    code = await generate_code_action(goal)
    ran = run_sandbox_code(code)
    ran["goal"] = goal
    return ran
