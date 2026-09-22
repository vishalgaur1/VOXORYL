from __future__ import annotations

"""
Detect local hardware so Voxoryl can recommend the right Ollama models.
Includes a simple Hardware Score (see docs/PRD-distribution.md).
"""

import json
import platform
import re
import shutil
import subprocess
from typing import Any


def _run(cmd: list[str], timeout: float = 12.0) -> str:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return (r.stdout or "") + (r.stderr or "")
    except Exception:
        return ""


def _ram_gb() -> float | None:
    system = platform.system()
    if system == "Windows":
        out = _run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory",
            ]
        )
        m = re.search(r"(\d+)", out.replace(",", ""))
        if m:
            return round(int(m.group(1)) / (1024**3), 1)
    elif system == "Linux":
        try:
            text = open("/proc/meminfo", encoding="utf-8").read()
            m = re.search(r"MemTotal:\s+(\d+)\s+kB", text)
            if m:
                return round(int(m.group(1)) / (1024**2), 1)
        except OSError:
            pass
    elif system == "Darwin":
        out = _run(["sysctl", "-n", "hw.memsize"])
        m = re.search(r"(\d+)", out)
        if m:
            return round(int(m.group(1)) / (1024**3), 1)
    return None


def _vram_via_nvidia_smi() -> list[dict[str, Any]]:
    if not shutil.which("nvidia-smi"):
        return []
    out = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total",
            "--format=csv,noheader,nounits",
        ]
    )
    gpus = []
    for line in out.splitlines():
        line = line.strip()
        if not line or "," not in line:
            continue
        name, mem = [p.strip() for p in line.split(",", 1)]
        try:
            gpus.append({"name": name, "vram_gb": round(float(mem) / 1024, 1), "source": "nvidia-smi"})
        except ValueError:
            continue
    return gpus


def _vram_via_windows_wmi() -> list[dict[str, Any]]:
    if platform.system() != "Windows":
        return []
    out = _run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM | ConvertTo-Json -Compress",
        ]
    )
    out = out.strip()
    if not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    gpus = []
    for item in data or []:
        name = str(item.get("Name") or "GPU")
        raw = item.get("AdapterRAM") or 0
        try:
            raw_i = int(raw)
        except (TypeError, ValueError):
            continue
        # AdapterRAM is often wrong for modern NVIDIA (reports 4GB cap) — still useful as hint
        if raw_i <= 0:
            continue
        gb = round(raw_i / (1024**3), 1)
        # Filter virtual/basic adapters
        low = name.lower()
        if any(x in low for x in ("microsoft basic", "remote desktop", "virtual")):
            continue
        gpus.append({"name": name, "vram_gb": gb, "source": "wmi"})
    return gpus


def _cpu_cores() -> int | None:
    try:
        import os

        n = os.cpu_count()
        return int(n) if n else None
    except Exception:
        return None


def hardware_score(hw: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Combined score → product tier label (lite / balanced / strong / beast / cpu_only).

    Not VRAM-only: RAM + VRAM + cores + OS features.
    """
    hw = hw or detect_hardware(include_score=False)
    ram = float(hw.get("ram_gb") or 0)
    vram = float(hw.get("best_vram_gb") or 0)
    cores = int(hw.get("cpu_cores") or 0)
    system = str(hw.get("os") or platform.system())

    # Weighted points (cap each signal)
    pts = 0.0
    pts += min(ram / 4.0, 8.0)  # 32GB → 8
    pts += min(vram * 1.5, 12.0)  # 8GB → 12
    pts += min(cores / 4.0, 3.0)  # 12 cores → 3
    if system == "Windows":
        pts += 0.5  # UIA / WebView2 path
    elif system == "Darwin":
        pts += 0.3

    if vram <= 0 and ram < 12:
        tier = "cpu_only"
    elif pts < 6 or (vram < 4 and ram < 12):
        tier = "lite"
    elif pts < 14 or vram < 8:
        tier = "balanced"
    elif pts < 22 or vram < 16:
        tier = "strong"
    else:
        tier = "beast"

    return {
        "score": round(pts, 1),
        "tier": tier,
        "ram_gb": ram or None,
        "best_vram_gb": vram or None,
        "cpu_cores": cores or None,
        "os": system,
    }


def detect_hardware(*, include_score: bool = True) -> dict[str, Any]:
    ram = _ram_gb()
    gpus = _vram_via_nvidia_smi() or _vram_via_windows_wmi()
    best_vram = max((g.get("vram_gb") or 0 for g in gpus), default=0.0)
    cpu = platform.processor() or platform.machine()
    cores = _cpu_cores()
    result: dict[str, Any] = {
        "ok": True,
        "os": platform.system(),
        "os_release": platform.release(),
        "cpu": cpu,
        "cpu_cores": cores,
        "ram_gb": ram,
        "gpus": gpus,
        "best_vram_gb": best_vram,
        "has_gpu": best_vram > 0 or bool(gpus),
    }
    if include_score:
        result["hardware_score"] = hardware_score(result)
    return result
