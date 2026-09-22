from __future__ import annotations

"""
Bank / UPI statement ingest — remember spend categories, nudge where to slow down.
Local only under data/bank_statements/. Never uploaded.
"""

import csv
import io
import json
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxoryl.config import settings
from voxoryl.knowledge import knowledge
from voxoryl.llm import chat_local, parse_json_loose
from voxoryl.memory import memory
from voxoryl.prefs import resolve_or_ask, get_value_mode


CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("food_delivery", ("swiggy", "zomato", "zepto", "blinkit", "instamart", "dunzo", "eatclub")),
    ("groceries", ("bigbasket", "jiomart", "dmart", "reliance fresh", "nature's basket")),
    ("transport", ("uber", "ola", "rapido", "metro", "irctc", "redbus", "petrol", "fuel", "parking")),
    ("shopping", ("amazon", "flipkart", "myntra", "ajio", "meesho", "nykaa")),
    ("subscriptions", ("netflix", "spotify", "prime", "youtube", "hotstar", "disney", "apple.com", "google *")),
    ("dining_out", ("restaurant", "cafe", "starbucks", "barista", "ccd", "dominos", "pizza hut", "kfc", "mcdonald")),
    ("bills", ("electricity", "bescom", "airtel", "jio", "vi ", "broadband", "wifi", "gas bill", "rent")),
    ("transfers", ("upi", "imps", "neft", "rtgs", "paytm", "phonepe", "gpay", "google pay")),
    ("atm_cash", ("atm", "cash wdl", "withdrawal")),
    ("salary", ("salary", "payroll", "neft cr salary")),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def root() -> Path:
    p = settings.voxoryl_data_dir / "bank_statements"
    p.mkdir(parents=True, exist_ok=True)
    (p / "inbox").mkdir(parents=True, exist_ok=True)
    return p


def _ledger_path() -> Path:
    return root() / "ledger.json"


def load_ledger() -> dict[str, Any]:
    path = _ledger_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"months": {}, "imports": [], "updated_at": None}


def save_ledger(data: dict[str, Any]) -> None:
    data["updated_at"] = _now()
    _ledger_path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def categorize(narration: str) -> str:
    n = (narration or "").lower()
    for cat, keys in CATEGORY_RULES:
        if any(k in n for k in keys):
            return cat
    return "other"


AMOUNT_RE = re.compile(
    r"(?P<sign>[-+])?\s*(?:rs\.?|inr|₹)?\s*(?P<amt>\d{1,3}(?:,\d{2,3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)",
    re.I,
)


def parse_statement_text(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    # Try CSV
    try:
        sample = text[:2000]
        if sample.count(",") >= 3 and "\n" in sample:
            reader = csv.DictReader(io.StringIO(text))
            for r in reader:
                narr = (
                    r.get("Narration")
                    or r.get("Description")
                    or r.get("Particulars")
                    or r.get("Merchant")
                    or r.get("Details")
                    or ""
                )
                amt_s = r.get("Amount") or r.get("Debit") or r.get("Withdrawal") or r.get("Debit Amount") or ""
                credit = r.get("Credit") or r.get("Deposit") or ""
                if not narr and not amt_s:
                    vals = list(r.values())
                    narr = str(vals[1] if len(vals) > 1 else vals[0] or "")
                    amt_s = str(vals[-1] if vals else "")
                amt = _to_float(amt_s) or 0.0
                if credit and _to_float(credit):
                    amt = -abs(_to_float(credit) or 0.0)  # credit as negative spend
                if amt == 0:
                    continue
                # treat positive as debit/spend by default
                spend = abs(amt) if amt > 0 or not credit else 0.0
                if credit and _to_float(credit):
                    spend = 0.0
                if spend <= 0 and amt < 0:
                    spend = 0.0
                if spend <= 0:
                    # if only Amount column and looks like debit
                    spend = abs(amt)
                rows.append(
                    {
                        "narration": str(narr)[:160],
                        "amount": round(spend, 2),
                        "category": categorize(str(narr)),
                    }
                )
            if rows:
                return rows[:500]
    except Exception:
        pass

    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) < 6:
            continue
        m = AMOUNT_RE.search(line)
        if not m:
            continue
        amt = _to_float(m.group("amt"))
        if amt is None or amt < 1:
            continue
        narr = (line[: m.start()] + line[m.end() :]).strip(" -|\t")
        if not narr:
            narr = line[:80]
        # skip obvious credits labeled
        if any(k in line.lower() for k in ("salary credited", "interest credit", "refund")):
            continue
        rows.append({"narration": narr[:160], "amount": round(amt, 2), "category": categorize(narr)})
    return rows[:500]


def _to_float(s: Any) -> float | None:
    if s is None:
        return None
    t = str(s).strip().replace(",", "").replace("₹", "").replace("Rs", "").replace("INR", "")
    t = re.sub(r"[^\d.\-]", "", t)
    try:
        return float(t)
    except ValueError:
        return None


def month_key(dt: datetime | None = None) -> str:
    d = dt or datetime.now(timezone.utc)
    return d.strftime("%Y-%m")


def merge_rows(rows: list[dict[str, Any]], *, source: str = "") -> dict[str, Any]:
    ledger = load_ledger()
    mk = month_key()
    month = ledger.setdefault("months", {}).setdefault(mk, {"by_category": {}, "total_spend": 0.0, "tx_count": 0})
    by_cat: dict[str, float] = defaultdict(float, {k: float(v) for k, v in (month.get("by_category") or {}).items()})
    total = float(month.get("total_spend") or 0)
    for r in rows:
        amt = float(r.get("amount") or 0)
        if amt <= 0:
            continue
        cat = str(r.get("category") or "other")
        by_cat[cat] += amt
        total += amt
    month["by_category"] = {k: round(v, 2) for k, v in sorted(by_cat.items(), key=lambda x: -x[1])}
    month["total_spend"] = round(total, 2)
    month["tx_count"] = int(month.get("tx_count") or 0) + len(rows)
    ledger["months"][mk] = month
    ledger.setdefault("imports", []).append(
        {"id": str(uuid.uuid4())[:8], "at": _now(), "source": source[:200], "rows": len(rows), "month": mk}
    )
    ledger["imports"] = ledger["imports"][-50:]
    save_ledger(ledger)
    return coach(mk)


def coach(month: str | None = None) -> dict[str, Any]:
    ledger = load_ledger()
    mk = month or month_key()
    m = (ledger.get("months") or {}).get(mk) or {}
    by_cat = m.get("by_category") or {}
    total = float(m.get("total_spend") or 0)
    if not by_cat:
        return {
            "ok": False,
            "speak": "No bank data yet. Drop a statement CSV/TXT into data/bank_statements/inbox/.",
            "hint": "data/bank_statements/inbox/",
        }
    ranked = sorted(by_cat.items(), key=lambda x: -float(x[1]))
    top = ranked[0]
    tips = []
    for cat, amt in ranked[:4]:
        if cat in {"food_delivery", "dining_out", "shopping", "subscriptions"} and float(amt) > 0:
            tips.append(f"Slow down on {cat.replace('_', ' ')} (Rs {amt:,.0f} this month).")
    mode = get_value_mode()
    if mode == "quality":
        tone = "You chose quality spend — still flag habit leaks, not every treat."
    elif mode == "budget":
        tone = "Budget mode: cut the top leak first."
    else:
        tone = "Balanced: trim the loudest category without killing joy."
    speak = (
        f"{mk}: ~Rs {total:,.0f} tracked. Biggest: {top[0].replace('_', ' ')} Rs {float(top[1]):,.0f}. "
        + (tips[0] if tips else "Looks steady.")
        + f" ({tone})"
    )
    knowledge.append_facts(
        "Spending",
        [f"{mk} spend ~Rs {total:,.0f}; top {top[0]} Rs {float(top[1]):,.0f}."] + tips[:2],
    )
    memory.remember_fact(speak[:240], tags=["spend", "bank", mk])
    return {
        "ok": True,
        "month": mk,
        "total_spend": total,
        "by_category": by_cat,
        "tips": tips,
        "value_mode": mode,
        "speak": speak,
    }


async def ingest_text(text: str, *, source: str = "") -> dict[str, Any]:
    rows = parse_statement_text(text)
    if len(rows) < 2:
        # LLM assist for messy exports
        raw = await chat_local(
            [
                {
                    "role": "system",
                    "content": (
                        "Extract debit/spend rows from a bank/UPI statement. "
                        'JSON only: {"rows":[{"narration":"...","amount":123.45}]} amounts in INR numbers.'
                    ),
                },
                {"role": "user", "content": text[:7000]},
            ],
            temperature=0.1,
        )
        data = parse_json_loose(raw) or {}
        for r in data.get("rows") or []:
            if isinstance(r, dict) and r.get("amount"):
                narr = str(r.get("narration") or "")
                rows.append(
                    {
                        "narration": narr[:160],
                        "amount": float(r["amount"]),
                        "category": categorize(narr),
                    }
                )
    if not rows:
        return {"ok": False, "error": "no transactions parsed", "speak": "Could not parse statement — try CSV export."}
    return merge_rows(rows, source=source)


async def ingest_inbox() -> dict[str, Any]:
    inbox = root() / "inbox"
    files = [p for p in inbox.iterdir() if p.is_file() and p.suffix.lower() in {".txt", ".csv", ".json", ".md"}]
    if not files:
        return {
            "ok": False,
            "hint": "Drop bank/UPI statement into data/bank_statements/inbox/",
            "speak": "No bank statement files in inbox.",
        }
    last = None
    for f in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:3]:
        last = await ingest_text(f.read_text(encoding="utf-8", errors="ignore"), source=f.name)
        if last.get("ok"):
            done = inbox / "processed"
            done.mkdir(exist_ok=True)
            dest = done / f.name
            if not dest.exists():
                f.rename(dest)
    return last or {"ok": False, "speak": "Parse failed."}


async def tool_spend(
    action: str = "coach",
    message: str = "",
    path: str = "",
) -> dict[str, Any]:
    act = (action or "coach").lower()
    lower = (message or "").lower()
    # Preferencing for "how hard to push cutbacks" — never assume
    if act in {"coach", "advice"} and "slow down" in lower:
        pref = resolve_or_ask("spending cutbacks")
        if pref.get("needs_preference"):
            return pref
    if act in {"ingest", "import", "add"} or any(
        k in lower for k in ("bank statement", "upi statement", "here is my statement", "spend history")
    ):
        if path and Path(path).exists():
            return await ingest_text(Path(path).read_text(encoding="utf-8", errors="ignore"), source=path)
        if len(message) > 80 and "statement" in lower:
            return await ingest_text(message, source="paste")
        return await ingest_inbox()
    if act in {"summary", "coach", "status", "this month"}:
        return coach()
    return coach()
