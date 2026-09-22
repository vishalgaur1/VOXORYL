from __future__ import annotations

"""
US business lead scout (public search) + MJML outreach drafts with approval gates.
Never sends without explicit approve. SMTP optional.
"""

import json
import re
import smtplib
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from voxoryl.config import settings
from voxoryl.llm import chat_local, parse_json_loose
from voxoryl.tools import tool_research


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _leads_dir() -> Path:
    path = settings.voxoryl_data_dir / "leads"
    path.mkdir(parents=True, exist_ok=True)
    (path / "emails").mkdir(parents=True, exist_ok=True)
    return path


def _store_path() -> Path:
    return _leads_dir() / "leads.json"


def _load_leads() -> list[dict[str, Any]]:
    path = _store_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("leads", [])
    except json.JSONDecodeError:
        return []


def _save_leads(leads: list[dict[str, Any]]) -> None:
    _store_path().write_text(json.dumps(leads, indent=2, ensure_ascii=False), encoding="utf-8")


def _email_re(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text or "")))


def _phone_re(text: str) -> list[str]:
    found = re.findall(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", text or "")
    return list(dict.fromkeys(f.strip() for f in found))


def _looks_like_real_site(url: str) -> bool:
    u = (url or "").lower()
    if not u.startswith("http"):
        return False
    weak = ("facebook.com", "instagram.com", "yelp.com", "yellowpages", "bbb.org", "mapquest", "tripadvisor", "linkedin.com", "tiktok.com", "nextdoor.com")
    return not any(w in u for w in weak)


EXTRACT_LEADS_SYSTEM = """Extract US local business leads from search results that may need a website.
Return ONLY JSON:
{
  "leads":[
    {
      "name":"Business Name",
      "niche":"plumber|dentist|...",
      "city":"City",
      "state":"ST",
      "phone":"",
      "email":"",
      "website":"",
      "source_url":"",
      "needs_website_score":0-10,
      "why":"short reason"
    }
  ]
}
Rules:
- Prefer businesses with only Facebook/Yelp/directory listings (high needs_website_score).
- If they already have a clear professional .com site, score low (0-3) or skip.
- Use only info present in the results. Empty string if unknown.
- Max 8 leads. US only.
"""


MJML_SYSTEM = """You write cold outreach for a web design / rebuild offer.
Return ONLY JSON:
{
  "subject":"...",
  "preview":"inbox preview text",
  "headline":"...",
  "body_html_paragraphs":["p1","p2","p3"],
  "cta_label":"Book a free 15-min site audit",
  "cta_url":"https://github.com/vishalgaur1/VOXORYL",
  "ps":"one line PS"
}
Rules:
- Specific to THIS business. No spammy ALL CAPS. No fake urgency.
- Mention you noticed they may lack a strong website (from the lead why).
- Caveman-clear: short paragraphs. Professional, not butler fluff.
- cta_url must be a real URL from settings or the sender; never invent placeholder domains.
"""


BASE_MJML = """<mjml>
  <mj-head>
    <mj-preview>{{preview}}</mj-preview>
    <mj-attributes>
      <mj-all font-family="Inter, Helvetica, Arial, sans-serif" />
      <mj-text color="#1a1a1a" font-size="16px" line-height="1.55" />
      <mj-button background-color="#0f766e" color="#ffffff" border-radius="6px" font-weight="600" />
    </mj-attributes>
    <mj-style>
      .muted {{ color: #64748b; font-size: 13px; }}
    </mj-style>
  </mj-head>
  <mj-body background-color="#f1f5f9">
    <mj-section background-color="#0f172a" padding="28px 24px">
      <mj-column>
        <mj-text color="#f8fafc" font-size="22px" font-weight="700" padding="0">{{sender_brand}}</mj-text>
        <mj-text color="#94a3b8" font-size="13px" padding="8px 0 0 0">Websites that get local customers</mj-text>
      </mj-column>
    </mj-section>
    <mj-section background-color="#ffffff" padding="32px 28px 8px">
      <mj-column>
        <mj-text font-size="24px" font-weight="700" padding="0 0 16px 0">{{headline}}</mj-text>
        {{paragraphs}}
        <mj-button href="{{cta_url}}" padding="24px 0 8px 0">{{cta_label}}</mj-button>
        <mj-text css-class="muted" padding="16px 0 0 0">{{ps}}</mj-text>
      </mj-column>
    </mj-section>
    <mj-section padding="20px 28px">
      <mj-column>
        <mj-text css-class="muted" align="center" padding="0">
          Sent to {{business_name}} · {{city_state}} · You can reply to this email.
        </mj-text>
      </mj-column>
    </mj-section>
  </mj-body>
</mjml>
"""


def mjml_to_html(mjml: str) -> str:
    """Best-effort MJML→HTML. Uses mjml CLI if present, else a readable HTML fallback."""
    import shutil
    import subprocess
    import tempfile

    bin_path = shutil.which("mjml")
    if bin_path:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "mail.mjml"
            dst = Path(tmp) / "mail.html"
            src.write_text(mjml, encoding="utf-8")
            try:
                r = subprocess.run(
                    [bin_path, str(src), "-o", str(dst)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                if r.returncode == 0 and dst.exists():
                    return dst.read_text(encoding="utf-8")
            except Exception:
                pass
    # Fallback HTML (keeps structure good enough to send)
    text = mjml
    # crude extract of mj-text bodies
    paras = re.findall(r"<mj-text[^>]*>(.*?)</mj-text>", text, flags=re.S)
    button = re.search(r'<mj-button[^>]*href="([^"]+)"[^>]*>(.*?)</mj-button>', text, flags=re.S)
    cleaned = []
    for p in paras:
        p = re.sub(r"<[^>]+>", "", p).strip()
        if p:
            cleaned.append(p)
    cta = ""
    if button:
        cta = f'<p style="margin:28px 0"><a href="{button.group(1)}" style="background:#0f766e;color:#fff;padding:12px 20px;border-radius:6px;text-decoration:none;font-weight:600">{re.sub(r"<[^>]+>", "", button.group(2)).strip()}</a></p>'
    body = "".join(f"<p style='margin:0 0 14px;font:16px/1.55 Inter,Helvetica,Arial,sans-serif;color:#1a1a1a'>{p}</p>" for p in cleaned[1:4] or cleaned)
    title = cleaned[0] if cleaned else "Hello"
    return f"""<!DOCTYPE html><html><body style="margin:0;background:#f1f5f9">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:24px">
<table width="560" style="background:#fff;border-radius:8px;overflow:hidden">
<tr><td style="background:#0f172a;padding:24px;color:#f8fafc;font:700 20px Inter,Helvetica,Arial,sans-serif">{title}</td></tr>
<tr><td style="padding:28px">{body}{cta}</td></tr>
</table></td></tr></table></body></html>"""


async def scout_leads(
    *,
    niche: str = "local service businesses",
    location: str = "USA",
    limit: int = 8,
    message: str = "",
) -> dict[str, Any]:
    """Search public web for businesses that likely need websites."""
    niche = (niche or "local businesses").strip()
    location = (location or "USA").strip()
    # Parse from freeform message if needed
    msg = message.strip()
    if msg:
        # crude: "plumbers in Texas that need websites"
        m = re.search(r"(.+?)\s+in\s+([A-Za-z\s,]+?)(?:\s+that|\s+who|\s+need|$)", msg, flags=re.I)
        if m:
            niche = m.group(1).strip()
            location = m.group(2).strip()
        niche = re.sub(r"\b(find|scout|businesses?|that need websites?|needing websites?)\b", "", niche, flags=re.I).strip() or niche

    queries = [
        f"{niche} in {location} facebook page -website",
        f"{niche} {location} yelp no website",
        f"best {niche} {location} contact phone",
        f"{niche} {location} \"call now\" directory",
    ]
    blobs = []
    related_all = []
    for q in queries[:3]:
        res = await tool_research(q)
        if not res.get("ok"):
            continue
        related_all.extend(res.get("related") or [])
        blobs.append(f"QUERY: {q}\nABSTRACT: {res.get('abstract')}\nRESULTS:\n" + "\n".join(
            f"- {r.get('title')} | {r.get('url')} | {r.get('snippet')}" for r in (res.get("related") or [])[:6]
        ))

    if not blobs:
        return {"ok": False, "error": "search returned nothing", "speak": "No search hits — try a tighter city + niche."}

    raw = await chat_local(
        [
            {"role": "system", "content": EXTRACT_LEADS_SYSTEM},
            {"role": "user", "content": f"Niche: {niche}\nLocation: {location}\n\n" + "\n\n".join(blobs)[:9000]},
        ],
        temperature=0.2,
    )
    parsed = parse_json_loose(raw) or {}
    extracted = parsed.get("leads") or []

    # Enrich phones/emails from snippets + filter
    leads_store = _load_leads()
    existing_names = {str(l.get("name") or "").lower() for l in leads_store}
    added = []
    for item in extracted[: max(1, min(limit, 12))]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name.lower() in existing_names:
            continue
        score = int(item.get("needs_website_score") or 0)
        website = str(item.get("website") or "")
        source = str(item.get("source_url") or "")
        # bump score if only directory presence
        if website and not _looks_like_real_site(website):
            score = max(score, 7)
            item["website"] = ""
        if source and not website and not _looks_like_real_site(source):
            score = max(score, 6)
        if score < 5:
            continue
        # harvest contact from related snippets
        blob = " ".join(
            f"{r.get('title')} {r.get('snippet')} {r.get('url')}" for r in related_all if name.lower()[:12] in (r.get("title") or "").lower()
        )
        emails = _email_re(blob) or _email_re(str(item.get("email") or ""))
        phones = _phone_re(blob) or _phone_re(str(item.get("phone") or ""))
        lead = {
            "id": str(uuid.uuid4())[:8],
            "name": name,
            "niche": str(item.get("niche") or niche),
            "city": str(item.get("city") or ""),
            "state": str(item.get("state") or ""),
            "phone": phones[0] if phones else str(item.get("phone") or ""),
            "email": emails[0] if emails else str(item.get("email") or ""),
            "website": website if _looks_like_real_site(website) else "",
            "source_url": source,
            "needs_website_score": score,
            "why": str(item.get("why") or "Likely weak/no dedicated website"),
            "status": "new",
            "created_at": _now(),
            "email_draft_id": None,
        }
        leads_store.append(lead)
        existing_names.add(name.lower())
        added.append(lead)

    _save_leads(leads_store)
    summary = "; ".join(f"{l['name']} ({l.get('city') or '?'}/{l.get('phone') or 'no phone'})" for l in added[:5]) or "none"
    return {
        "ok": True,
        "niche": niche,
        "location": location,
        "added": len(added),
        "leads": added,
        "total_stored": len(leads_store),
        "path": str(_store_path().resolve()),
        "speak": f"Scouted {len(added)} leads needing sites in {location}. {summary}. Drafts need your approve.",
        "next": "Say 'draft outreach for lead <id>' or 'draft emails for new leads', then 'approve lead <id>', then 'send approved leads' if SMTP set.",
    }


def list_leads(status: str | None = None, limit: int = 30) -> dict[str, Any]:
    leads = _load_leads()
    if status:
        leads = [l for l in leads if l.get("status") == status]
    leads = list(reversed(leads))[:limit]
    return {"ok": True, "count": len(leads), "leads": leads}


def get_lead(lead_id: str) -> dict[str, Any] | None:
    for lead in _load_leads():
        if lead.get("id") == lead_id:
            return lead
    return None


async def draft_outreach(
    lead_id: str = "",
    *,
    for_all_new: bool = False,
    sender_brand: str = "",
    cta_url: str = "",
) -> dict[str, Any]:
    leads = _load_leads()
    targets = []
    if for_all_new:
        targets = [l for l in leads if l.get("status") == "new"][:5]
    elif lead_id:
        lead = next((l for l in leads if l.get("id") == lead_id), None)
        if lead:
            targets = [lead]
    if not targets:
        return {"ok": False, "error": "no leads to draft", "speak": "No matching leads. Scout first."}

    brand = sender_brand or getattr(settings, "outreach_brand", None) or "VOXORYL"
    brand = str(brand)
    cta = cta_url or getattr(settings, "outreach_cta_url", None) or "https://github.com/vishalgaur1/VOXORYL"
    drafts = []

    for lead in targets:
        raw = await chat_local(
            [
                {"role": "system", "content": MJML_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Business: {lead.get('name')}\nNiche: {lead.get('niche')}\n"
                        f"City/State: {lead.get('city')} {lead.get('state')}\n"
                        f"Why needs site: {lead.get('why')}\n"
                        f"Phone: {lead.get('phone')}\nEmail: {lead.get('email')}\n"
                        f"Sender brand: {brand}\nCTA url: {cta}\n"
                    ),
                },
            ],
            temperature=0.4,
        )
        copy = parse_json_loose(raw) or {}
        paragraphs = copy.get("body_html_paragraphs") or [
            f"Hi — I came across {lead.get('name')} while looking at {lead.get('niche')} businesses in {lead.get('city') or 'your area'}.",
            f"{lead.get('why') or 'It looks like you may not have a strong dedicated website yet.'} A fast, mobile-first site usually pays for itself in calls.",
            "I build clean sites for local businesses. Want a free 15-minute audit? No pitch deck spam.",
        ]
        para_mjml = "\n".join(f'<mj-text padding="0 0 14px 0">{p}</mj-text>' for p in paragraphs)
        city_state = ", ".join(x for x in [str(lead.get("city") or ""), str(lead.get("state") or "")] if x)
        mjml = (
            BASE_MJML.replace("{{preview}}", str(copy.get("preview") or "Quick idea for your website"))
            .replace("{{sender_brand}}", brand)
            .replace("{{headline}}", str(copy.get("headline") or f"A sharper site for {lead.get('name')}"))
            .replace("{{paragraphs}}", para_mjml)
            .replace("{{cta_url}}", str(copy.get("cta_url") or cta))
            .replace("{{cta_label}}", str(copy.get("cta_label") or "Book a free 15-min site audit"))
            .replace("{{ps}}", str(copy.get("ps") or "If timing is bad, say so — I will not follow up forever."))
            .replace("{{business_name}}", str(lead.get("name")))
            .replace("{{city_state}}", city_state or "USA")
        )
        html = mjml_to_html(mjml)
        draft_id = str(uuid.uuid4())[:8]
        base = _leads_dir() / "emails" / f"{lead['id']}_{draft_id}"
        mjml_path = Path(str(base) + ".mjml")
        html_path = Path(str(base) + ".html")
        meta_path = Path(str(base) + ".json")
        mjml_path.write_text(mjml, encoding="utf-8")
        html_path.write_text(html, encoding="utf-8")
        meta = {
            "draft_id": draft_id,
            "lead_id": lead["id"],
            "subject": copy.get("subject") or f"Quick idea for {lead.get('name')}'s website",
            "to_email": lead.get("email") or "",
            "to_phone": lead.get("phone") or "",
            "status": "pending_approval",
            "mjml": str(mjml_path.resolve()),
            "html": str(html_path.resolve()),
            "created_at": _now(),
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        lead["status"] = "pending_approval"
        lead["email_draft_id"] = draft_id
        lead["email_meta"] = str(meta_path.resolve())
        lead["email_subject"] = meta["subject"]
        drafts.append(meta)

    _save_leads(leads)
    return {
        "ok": True,
        "drafted": len(drafts),
        "drafts": drafts,
        "speak": f"Drafted {len(drafts)} MJML emails — pending your approval. Review in data/leads/emails/ then approve.",
        "approval_hint": "Say: approve lead <id>  |  approve all pending  |  reject lead <id>",
    }


def set_approval(lead_id: str = "", *, approve_all_pending: bool = False, approve: bool = True) -> dict[str, Any]:
    leads = _load_leads()
    changed = []
    for lead in leads:
        if approve_all_pending and lead.get("status") == "pending_approval":
            lead["status"] = "approved" if approve else "rejected"
            changed.append(lead["id"])
        elif lead_id and lead.get("id") == lead_id:
            lead["status"] = "approved" if approve else "rejected"
            changed.append(lead["id"])
    _save_leads(leads)
    if not changed:
        return {"ok": False, "error": "no matching leads", "speak": "Nothing to approve."}
    word = "Approved" if approve else "Rejected"
    return {"ok": True, "changed": changed, "speak": f"{word}: {', '.join(changed)}. Ready to send only if SMTP configured."}


def _smtp_configured() -> bool:
    return bool(
        getattr(settings, "smtp_host", "")
        and getattr(settings, "smtp_user", "")
        and getattr(settings, "smtp_password", "")
    )


def send_approved(*, lead_id: str = "", dry_run: bool = False) -> dict[str, Any]:
    """Send approved drafts via SMTP. Never sends pending/rejected."""
    leads = _load_leads()
    sent = []
    skipped = []
    for lead in leads:
        if lead_id and lead.get("id") != lead_id:
            continue
        if lead.get("status") != "approved":
            continue
        meta_path = lead.get("email_meta")
        if not meta_path or not Path(meta_path).exists():
            skipped.append({"id": lead["id"], "reason": "missing draft"})
            continue
        meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
        to_addr = (meta.get("to_email") or lead.get("email") or "").strip()
        html_path = Path(meta.get("html") or "")
        if not to_addr:
            skipped.append({"id": lead["id"], "reason": "no email on lead — HTML saved for manual send", "html": str(html_path)})
            lead["status"] = "ready_manual"
            continue
        if not html_path.exists():
            skipped.append({"id": lead["id"], "reason": "missing html"})
            continue
        if dry_run or not _smtp_configured():
            skipped.append(
                {
                    "id": lead["id"],
                    "reason": "dry_run or SMTP not configured — files ready",
                    "html": str(html_path),
                    "subject": meta.get("subject"),
                    "to": to_addr,
                }
            )
            lead["status"] = "ready_manual"
            continue
        try:
            html = html_path.read_text(encoding="utf-8")
            msg = MIMEMultipart("alternative")
            msg["Subject"] = meta.get("subject") or "Website idea"
            msg["From"] = getattr(settings, "smtp_from", None) or settings.smtp_user
            msg["To"] = to_addr
            msg.attach(MIMEText(html, "html", "utf-8"))
            with smtplib.SMTP(settings.smtp_host, int(getattr(settings, "smtp_port", 587) or 587), timeout=30) as smtp:
                smtp.starttls()
                smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.sendmail(msg["From"], [to_addr], msg.as_string())
            lead["status"] = "sent"
            lead["sent_at"] = _now()
            sent.append({"id": lead["id"], "to": to_addr})
        except Exception as exc:
            skipped.append({"id": lead["id"], "reason": str(exc)})

    _save_leads(leads)
    return {
        "ok": True,
        "sent": sent,
        "skipped": skipped,
        "smtp_configured": _smtp_configured(),
        "speak": (
            f"Sent {len(sent)}. Skipped {len(skipped)} "
            f"(manual HTML under data/leads/emails/ if no SMTP/email)."
        ),
    }


async def tool_leads(
    action: str = "scout",
    *,
    message: str = "",
    niche: str = "",
    location: str = "",
    lead_id: str = "",
    limit: int = 8,
) -> dict[str, Any]:
    action = (action or "scout").lower().strip()
    if action in {"scout", "find", "search"}:
        return await scout_leads(niche=niche or "local businesses", location=location or "USA", limit=limit, message=message)
    if action in {"list", "status"}:
        st = None
        lower = message.lower()
        for s in ("new", "pending_approval", "approved", "sent", "rejected", "ready_manual"):
            if s.replace("_", " ") in lower or s in lower:
                st = s
                break
        return list_leads(status=st)
    if action in {"draft", "mjml", "email"}:
        for_all = "all" in message.lower() or not lead_id
        # extract id
        m = re.search(r"\b([a-f0-9]{8})\b", message or lead_id)
        lid = lead_id or (m.group(1) if m else "")
        return await draft_outreach(lead_id=lid, for_all_new=for_all and not lid)
    if action in {"approve", "reject"}:
        approve = action == "approve"
        all_pending = "all" in (message or "").lower()
        m = re.search(r"\b([a-f0-9]{8})\b", message or lead_id)
        return set_approval(lead_id=lead_id or (m.group(1) if m else ""), approve_all_pending=all_pending, approve=approve)
    if action in {"send"}:
        m = re.search(r"\b([a-f0-9]{8})\b", message or lead_id)
        return send_approved(lead_id=lead_id or (m.group(1) if m else ""), dry_run="dry" in (message or "").lower())
    return {"ok": False, "error": f"unknown action {action}"}
