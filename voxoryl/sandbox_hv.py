from __future__ import annotations

"""
Hyper-V disposable sandbox for testing software you don't fully trust yet.

Flow (owner-authorized, local PC only):
  1) Create / start a throwaway VM (or use a template checkpoint)
  2) Copy the sample into the VM for testing
  3) Record a verdict: safe | suspicious | malware
  4) If safe → optional host install path noted, then DESTROY the sandbox VM to free disk/RAM
  5) If not safe → destroy without installing on the main PC

This is isolation plumbing — not malware analysis as a service, not AV replacement.
"""

import asyncio
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxoryl.config import settings
from voxoryl.memory import memory


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root() -> Path:
    p = settings.voxoryl_data_dir / "hv_sandbox"
    p.mkdir(parents=True, exist_ok=True)
    (p / "inbox").mkdir(parents=True, exist_ok=True)
    (p / "reports").mkdir(parents=True, exist_ok=True)
    return p


def _state_path() -> Path:
    return _root() / "state.json"


def load_state() -> dict[str, Any]:
    path = _state_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"vms": [], "last": None}


def save_state(data: dict[str, Any]) -> None:
    _state_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


async def _ps(script: str, *, timeout: float = 90.0) -> dict[str, Any]:
    """Run PowerShell; Hyper-V cmdlets usually need Admin."""
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return {"ok": False, "error": "timeout", "stdout": "", "stderr": ""}
        out = (out_b or b"").decode("utf-8", errors="ignore").strip()
        err = (err_b or b"").decode("utf-8", errors="ignore").strip()
        return {"ok": proc.returncode == 0, "code": proc.returncode, "stdout": out, "stderr": err}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "stdout": "", "stderr": ""}


async def hyperv_available() -> dict[str, Any]:
    r = await _ps(
        "if (Get-Command Get-VM -ErrorAction SilentlyContinue) { 'yes' } else { 'no' }"
    )
    yes = (r.get("stdout") or "").strip().lower() == "yes"
    feat = await _ps(
        "(Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All "
        "-ErrorAction SilentlyContinue).State"
    )
    return {
        "ok": True,
        "cmdlets": yes,
        "feature_state": (feat.get("stdout") or "").strip() or "unknown",
        "admin_hint": "Hyper-V cmdlets need an elevated PowerShell / admin Voxoryl terminal.",
        "speak": (
            "Hyper-V cmdlets available."
            if yes
            else "Hyper-V not available in this shell — enable Hyper-V Windows feature and run Voxoryl as Admin."
        ),
        "raw": {"cmdlets": r, "feature": feat},
    }


async def list_vms() -> dict[str, Any]:
    r = await _ps(
        "Get-VM | Select-Object Name,State,CPUUsage,MemoryAssigned | ConvertTo-Json -Compress"
    )
    vms: list[Any] = []
    if r.get("ok") and r.get("stdout"):
        try:
            data = json.loads(r["stdout"])
            vms = data if isinstance(data, list) else [data]
        except json.JSONDecodeError:
            vms = [{"raw": r["stdout"]}]
    state = load_state()
    voxoryl_vms = [v for v in (state.get("vms") or []) if not v.get("destroyed")]
    return {
        "ok": bool(r.get("ok")),
        "hyperv_vms": vms,
        "voxoryl_sandboxes": voxoryl_vms,
        "speak": f"{len(vms)} Hyper-V VM(s); {len(voxoryl_vms)} Voxoryl sandbox record(s).",
        "raw": r,
    }


async def create_sandbox(*, name: str = "", memory_mb: int = 2048) -> dict[str, Any]:
    """
    Create a lightweight Generation-2 VM shell.
    Disk/ISO attachment is left to the owner template — we track lifecycle + destroy.
    """
    avail = await hyperv_available()
    if not avail.get("cmdlets"):
        return {**avail, "ok": False}

    sid = str(uuid.uuid4())[:8]
    vm_name = (name or f"VoxorylSandbox-{sid}").strip()
    # New-VM without VHD still allocates management object; prefer differencing from template if set
    template = (os_environ_template())
    script_parts = [
        f"$ErrorActionPreference='Stop'",
        f"$name='{vm_name}'",
    ]
    if template:
        # Clone from existing VM (template must exist and be off ideally)
        script_parts += [
            f"$t=Get-VM -Name '{template}' -ErrorAction Stop",
            f"Checkpoint-VM -Name '{template}' -SnapshotName 'VoxorylBase' -ErrorAction SilentlyContinue",
            # Export/import is heavy — document instead of auto-export here
            f"Write-Output 'TEMPLATE={template}'",
            f"Write-Output 'Use a differencing disk from your golden image; creating empty VM shell.'",
        ]
    script_parts += [
        f"if (Get-VM -Name $name -ErrorAction SilentlyContinue) {{ throw 'VM exists' }}",
        f"New-VM -Name $name -Generation 2 -MemoryStartupBytes {int(memory_mb)}MB -NoVHD | Out-Null",
        f"Set-VMProcessor -VMName $name -Count 2",
        f"Set-VMMemory -VMName $name -DynamicMemoryEnabled $true -MinimumBytes 512MB -MaximumBytes {int(memory_mb)}MB",
        f"Get-VM -Name $name | Select-Object Name,State | ConvertTo-Json -Compress",
    ]
    r = await _ps("; ".join(script_parts), timeout=120.0)
    ok = bool(r.get("ok"))
    entry = {
        "id": sid,
        "name": vm_name,
        "created_at": _now(),
        "memory_mb": memory_mb,
        "template": template or None,
        "verdict": None,
        "destroyed": False,
        "sample": None,
        "host_install": None,
    }
    state = load_state()
    if ok:
        state.setdefault("vms", []).append(entry)
        state["last"] = entry
        save_state(state)
        memory.remember_fact(f"Hyper-V sandbox created: {vm_name}", tags=["sandbox", "hyperv"])
    speak = (
        f"Sandbox VM '{vm_name}' created (empty shell). Attach your golden VHD/ISO, test the sample, then verdict + destroy."
        if ok
        else f"Create failed (need Admin + Hyper-V): {(r.get('stderr') or r.get('error') or '')[:200]}"
    )
    return {
        "ok": ok,
        "vm": entry if ok else None,
        "speak": speak,
        "hint": (
            "Recommended: keep a golden 'VoxorylGolden' VM with Windows + tools; "
            "for each sample, clone/differencing disk, test, destroy. Saves space vs keeping junk VMs."
        ),
        "raw": r,
        "inbox": str((_root() / "inbox").resolve()),
    }


def os_environ_template() -> str:
    import os

    return (os.environ.get("VOXORYL_HV_TEMPLATE") or "").strip()


async def stage_sample(path: str = "") -> dict[str, Any]:
    """Park a file in the sandbox inbox for copying into the VM (manual or shared folder)."""
    inbox = _root() / "inbox"
    if path:
        src = Path(path)
        if not src.exists():
            return {"ok": False, "speak": f"File not found: {path}"}
        dest = inbox / src.name
        shutil.copy2(src, dest)
    else:
        files = list(inbox.glob("*"))
        if not files:
            return {
                "ok": False,
                "speak": "Drop the suspicious installer into data/hv_sandbox/inbox/ first.",
                "inbox": str(inbox.resolve()),
            }
        dest = max(files, key=lambda p: p.stat().st_mtime)
    state = load_state()
    last = state.get("last") or {}
    if last and not last.get("destroyed"):
        last["sample"] = str(dest)
        state["last"] = last
        for v in state.get("vms") or []:
            if v.get("id") == last.get("id"):
                v["sample"] = str(dest)
        save_state(state)
    return {
        "ok": True,
        "sample": str(dest),
        "speak": f"Staged {dest.name} in sandbox inbox. Copy into the VM (shared folder / ISO), test there only.",
    }


async def set_verdict(verdict: str, *, note: str = "", install_path: str = "") -> dict[str, Any]:
    v = (verdict or "").strip().lower()
    if v in {"ok", "clean", "good"}:
        v = "safe"
    if v in {"bad", "malware", "virus"}:
        v = "malware"
    if v not in {"safe", "suspicious", "malware"}:
        return {
            "ok": False,
            "speak": "Verdict must be: safe | suspicious | malware. I won't install on the main PC unless you say safe.",
        }
    state = load_state()
    last = state.get("last")
    if not last or last.get("destroyed"):
        return {"ok": False, "speak": "No active sandbox — create one first."}
    last["verdict"] = v
    last["verdict_note"] = (note or "")[:400]
    last["verdict_at"] = _now()
    if v == "safe" and install_path:
        last["host_install"] = install_path
    state["last"] = last
    for item in state.get("vms") or []:
        if item.get("id") == last.get("id"):
            item.update(last)
    save_state(state)

    report = {
        "vm": last.get("name"),
        "verdict": v,
        "note": note,
        "sample": last.get("sample"),
        "at": _now(),
    }
    rp = _root() / "reports" / f"verdict-{last.get('id')}.json"
    rp.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if v == "safe":
        speak = (
            f"Marked SAFE. Next: install on main PC only from a trusted copy, then say "
            f"'destroy sandbox' to wipe the VM and free space/RAM."
        )
        if install_path:
            speak += f" Noted host path: {install_path}"
    elif v == "suspicious":
        speak = "Marked SUSPICIOUS — do NOT install on main PC. Say 'destroy sandbox' to wipe it."
    else:
        speak = "Marked MALWARE — do NOT install on main PC. Destroy the sandbox now."
    memory.remember_fact(f"Sandbox verdict {v} for {last.get('name')}", tags=["sandbox", "hyperv", v])
    return {"ok": True, "verdict": v, "vm": last, "report": str(rp), "speak": speak}


async def destroy_sandbox(name: str = "", *, force: bool = True) -> dict[str, Any]:
    state = load_state()
    last = state.get("last") or {}
    vm_name = (name or last.get("name") or "").strip()
    if not vm_name:
        return {"ok": False, "speak": "No sandbox name — create or list sandboxes first."}

    script = (
        f"$ErrorActionPreference='Stop'; "
        f"$n='{vm_name}'; "
        f"if (Get-VM -Name $n -ErrorAction SilentlyContinue) {{ "
        f"  if ((Get-VM -Name $n).State -ne 'Off') {{ Stop-VM -Name $n -Force -TurnOff }}; "
        f"  Remove-VM -Name $n -Force; "
        f"  'removed' "
        f"}} else {{ 'missing' }}"
    )
    r = await _ps(script, timeout=120.0)
    ok = bool(r.get("ok"))
    for item in state.get("vms") or []:
        if item.get("name") == vm_name:
            item["destroyed"] = True
            item["destroyed_at"] = _now()
    if last.get("name") == vm_name:
        last["destroyed"] = True
        last["destroyed_at"] = _now()
        state["last"] = last
    save_state(state)
    memory.remember_fact(f"Hyper-V sandbox destroyed: {vm_name}", tags=["sandbox", "hyperv", "cleanup"])

    verdict = last.get("verdict")
    extra = ""
    if verdict == "safe":
        extra = " Main PC stays clean of the throwaway VM — install only the trusted copy you approved."
    elif verdict in {"suspicious", "malware"}:
        extra = " Good — junk never touched the main install."
    speak = (
        f"Sandbox '{vm_name}' destroyed — disk/RAM freed."
        if ok
        else f"Destroy issue: {(r.get('stderr') or r.get('stdout') or r.get('error') or '')[:200]}"
    ) + extra
    return {"ok": ok, "name": vm_name, "verdict": verdict, "speak": speak, "raw": r}


async def tool_sandbox(
    action: str = "status",
    message: str = "",
    name: str = "",
    verdict: str = "",
    path: str = "",
) -> dict[str, Any]:
    act = (action or "status").lower()
    lower = (message or "").lower()

    if act in {"status", "available"} or "hyper-v status" in lower or "hyperv status" in lower:
        return await hyperv_available()
    if act in {"list"} or "list sandbox" in lower or "list vm" in lower:
        return await list_vms()
    if act in {"create", "new"} or any(
        k in lower for k in ("create sandbox", "new sandbox", "hyper-v sandbox", "test in hyper-v", "suspicious software")
    ):
        m = re.search(r"name\s+([A-Za-z0-9_\-]+)", message or "", re.I)
        return await create_sandbox(name=name or (m.group(1) if m else ""))
    if act in {"stage", "sample"} or "stage sample" in lower or "sandbox inbox" in lower:
        return await stage_sample(path)
    if act in {"verdict", "judge"} or any(k in lower for k in ("mark safe", "mark suspicious", "mark malware", "verdict")):
        v = verdict
        if not v:
            if "safe" in lower:
                v = "safe"
            elif "malware" in lower or "malicious" in lower:
                v = "malware"
            elif "suspicious" in lower:
                v = "suspicious"
        return await set_verdict(v, note=message[:400])
    if act in {"destroy", "delete", "wipe"} or any(
        k in lower for k in ("destroy sandbox", "delete sandbox", "wipe sandbox", "remove sandbox")
    ):
        m = re.search(r"sandbox\s+([A-Za-z0-9_\-]+)", message or "", re.I)
        return await destroy_sandbox(name or (m.group(1) if m else ""))

    # default help
    avail = await hyperv_available()
    return {
        "ok": True,
        "available": avail,
        "speak": (
            "Sandbox flow: create sandbox → drop file in data/hv_sandbox/inbox → test inside VM → "
            "mark safe|suspicious|malware → destroy sandbox (frees space). "
            + str(avail.get("speak") or "")
        ),
        "inbox": str((_root() / "inbox").resolve()),
    }
