"""Probe Voxoryl like Cursor would — ask, print reply, flag odd answers.

Usage:
  .\\.venv\\Scripts\\python.exe scripts\\probe_voxoryl.py
  .\\.venv\\Scripts\\python.exe scripts\\probe_voxoryl.py "open notepad"
  .\\.venv\\Scripts\\python.exe scripts\\probe_voxoryl.py --suite
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

BASES = ("http://127.0.0.1:3848", "http://127.0.0.1:3847")


def _req(method: str, url: str, body: dict | None = None, timeout: float = 90.0) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"} if body is not None else {}
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def find_base() -> str:
    for base in BASES:
        try:
            s = _req("GET", f"{base}/api/status", timeout=3)
            if s.get("ok") and s.get("ollama") and str(s.get("data_dir") or "").startswith(("D:", "C:", "/")):
                # Prefer Windows local path
                if "Voxoryl" in str(s.get("data_dir")) or str(s.get("data_dir", "")).startswith(("D:", "C:")):
                    return base
        except Exception:
            continue
    for base in BASES:
        try:
            s = _req("GET", f"{base}/api/status", timeout=3)
            if s.get("ok") and s.get("ollama"):
                return base
        except Exception:
            continue
    raise SystemExit("Voxoryl server not reachable on 3848/3847 with Ollama up.")


def ask(base: str, message: str) -> dict:
    t0 = time.perf_counter()
    out = _req("POST", f"{base}/api/ask", {"message": message, "execute": True}, timeout=120)
    out["_ms"] = int((time.perf_counter() - t0) * 1000)
    return out


def flag_off(message: str, speak: str, mode: str) -> list[str]:
    flags: list[str] = []
    low = (speak or "").lower()
    msg = (message or "").lower()
    if any(k in msg for k in ("open chrome", "khol", "chrome")) and any(
        k in low for k in ("text-based", "cannot control", "can't control", "nahi, main seedhe")
    ):
        flags.append("denied_pc_control")
    if any(k in msg for k in ("open chrome", "chrome khol")) and mode not in {"pipeline", "direct"} and "opening" not in low:
        flags.append("did_not_open_app")
    if "i am here to assist" in low or "how can i help you today" in low:
        if any(k in msg for k in ("sure", "go for it", "another", "more")):
            flags.append("lost_context_greeting")
    if len(speak or "") > 700:
        flags.append("overlong_reply")
    return flags


SUITE = [
    "hello",
    "Chrome browser khol sakta hai tu?",
    "open notepad",
    "Give me a short quote from any book",
    "sure go for it",
    "volume up",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("message", nargs="?", help="Single message to send")
    ap.add_argument("--suite", action="store_true", help="Run canned probe suite")
    ap.add_argument("--clear", action="store_true", help="Clear chat before asking")
    args = ap.parse_args()
    base = find_base()
    print(f"base={base}")
    if args.clear:
        print(_req("POST", f"{base}/api/chat/clear"))
    messages = SUITE if args.suite else [args.message or "hello"]
    for msg in messages:
        try:
            out = ask(base, msg)
        except urllib.error.HTTPError as e:
            print(f"FAIL {msg!r}: HTTP {e.code}")
            continue
        speak = str(out.get("speak") or "")
        mode = str(out.get("mode") or "")
        pipe = str(out.get("pipeline") or "")
        flags = flag_off(msg, speak, mode)
        print(f"\n> {msg}")
        print(f"  mode={mode} pipeline={pipe} ms={out.get('_ms')}")
        print(f"  speak={speak[:300]}")
        if flags:
            print(f"  FLAGS={flags}")
    chat = _req("GET", f"{base}/api/chat")
    print(f"\nchat_messages={chat.get('message_count')} summary_chars={chat.get('summary_chars')}")


if __name__ == "__main__":
    main()
