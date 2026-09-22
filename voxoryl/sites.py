from __future__ import annotations

"""
Website factory: brief → static site files → approval → Vercel deploy.
Tech-ready for asks like "create a site for this hair salon".
"""

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from voxoryl.approvals import create_approval, get_approval, mark_executed
from voxoryl.config import settings
from voxoryl.llm import chat_local, parse_json_loose


SITE_SYSTEM = """You build a single beautiful marketing website as static files.
Return ONLY JSON:
{
  "slug": "short-kebab-name",
  "title": "Business Name",
  "tagline": "one line",
  "files": {
    "index.html": "<!DOCTYPE html>...",
    "styles.css": "...",
    "script.js": "..."
  },
  "notes": "short"
}
Hard design rules:
- One composition hero: brand name is the hero signal, one headline, one short line, one CTA.
- No purple-on-white AI look. No Inter/Roboto/Arial. Use distinctive Google Fonts links.
- Atmosphere via gradient or pattern — not flat white.
- No cards in the hero. Minimal cards only if needed for services.
- Sections: Hero, Services, About, Contact (omit phone/email/address lines if unknown — do not invent fake contacts).
- Mobile responsive. Semantic HTML. Real CTA buttons.
- Caveman content: short, punchy, no fluff.
- All CSS in styles.css, JS in script.js. index.html links them relatively.
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sites_root() -> Path:
    path = settings.voxoryl_data_dir / "sites"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:48] or "site") + "-" + uuid.uuid4().hex[:6]


async def generate_site_files(brief: str) -> dict[str, Any]:
    raw = await chat_local(
        [
            {"role": "system", "content": SITE_SYSTEM},
            {"role": "user", "content": f"Build the website for:\n{brief}"},
        ],
        temperature=0.55,
    )
    parsed = parse_json_loose(raw) or {}
    files = parsed.get("files") if isinstance(parsed.get("files"), dict) else {}
    # Fallback scaffold if model fails JSON
    if not files.get("index.html"):
        title = (parsed.get("title") or "New Site").strip() or "New Site"
        tagline = (parsed.get("tagline") or brief[:120]).strip()
        files = {
            "index.html": (
                "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'/>"
                "<meta name='viewport' content='width=device-width, initial-scale=1'/>"
                f"<title>{title}</title><link rel='stylesheet' href='styles.css'/>"
                "<link href='https://fonts.googleapis.com/css2?family=Syne:wght@700;800&family=IBM+Plex+Sans:wght@400;500&display=swap' rel='stylesheet'/>"
                "</head><body><header class='hero'><p class='brand'>"
                f"{title}</p><h1>{tagline}</h1><p class='sub'>Book today.</p>"
                "<a class='cta' href='#contact'>Get in touch</a></header>"
                "<section id='services'><h2>Services</h2><p>Tell us what you need — we tailor it.</p></section>"
                "<section id='contact'><h2>Contact</h2><p>Phone · Email · Address</p></section>"
                "<script src='script.js'></script></body></html>"
            ),
            "styles.css": (
                ":root{--ink:#121212;--accent:#c45c26;--bg:#f3efe7}"
                "*{box-sizing:border-box}body{margin:0;font-family:'IBM Plex Sans',sans-serif;color:var(--ink);background:var(--bg)}"
                ".hero{min-height:92vh;padding:4rem 6vw;background:radial-gradient(circle at 20% 20%,#ffe8d6,transparent 40%),linear-gradient(135deg,#1c1917,#3f2e25);color:#faf7f2}"
                ".brand{font-family:Syne,sans-serif;font-size:clamp(2.5rem,8vw,5rem);margin:0;letter-spacing:-.03em}"
                "h1{font-family:Syne,sans-serif;font-weight:700;max-width:16ch;font-size:clamp(1.4rem,3vw,2.2rem)}"
                ".cta{display:inline-block;margin-top:1.5rem;padding:.9rem 1.4rem;background:var(--accent);color:#fff;text-decoration:none;font-weight:600}"
                "section{padding:4rem 6vw}h2{font-family:Syne,sans-serif}"
            ),
            "script.js": "document.querySelectorAll('a[href^=\"#\"]').forEach(a=>a.addEventListener('click',e=>{const id=a.getAttribute('href');const el=document.querySelector(id);if(el){e.preventDefault();el.scrollIntoView({behavior:'smooth'})}}));",
        }
        parsed["title"] = title
        parsed["tagline"] = tagline
    slug = _slugify(str(parsed.get("slug") or parsed.get("title") or "site"))
    return {
        "ok": True,
        "slug": slug,
        "title": parsed.get("title") or slug,
        "tagline": parsed.get("tagline") or "",
        "files": files,
        "notes": parsed.get("notes") or "",
    }


def write_site(slug: str, files: dict[str, str], meta: dict[str, Any]) -> Path:
    root = _sites_root() / slug
    root.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        # only allow simple relative filenames
        safe = Path(name).name
        if not safe or ".." in name:
            continue
        (root / safe).write_text(str(content), encoding="utf-8")
    # vercel static project hints
    (root / "vercel.json").write_text(
        json.dumps({"cleanUrls": True, "trailingSlash": False}, indent=2),
        encoding="utf-8",
    )
    meta_path = root / "site.json"
    meta = {
        **meta,
        "slug": slug,
        "path": str(root.resolve()),
        "updated_at": _now(),
        "status": meta.get("status") or "draft",
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return root


async def create_website(brief: str, *, auto_queue_deploy: bool = True) -> dict[str, Any]:
    gen = await generate_site_files(brief)
    if not gen.get("ok"):
        return gen
    slug = str(gen["slug"])
    root = write_site(
        slug,
        gen["files"],
        {
            "title": gen.get("title"),
            "tagline": gen.get("tagline"),
            "brief": brief,
            "notes": gen.get("notes"),
            "status": "pending_deploy_approval",
            "created_at": _now(),
        },
    )
    preview = str((root / "index.html").resolve())
    approval = None
    if auto_queue_deploy:
        approval = create_approval(
            "site_deploy",
            title=f"Deploy site: {gen.get('title')}",
            summary=f"Local files ready at data/sites/{slug}. Approve to push to Vercel.",
            payload={"slug": slug, "path": str(root.resolve())},
            preview_path=preview,
        )
    return {
        "ok": True,
        "slug": slug,
        "title": gen.get("title"),
        "path": str(root.resolve()),
        "preview": preview,
        "files": list(gen["files"].keys()) + ["vercel.json", "site.json"],
        "approval_id": (approval or {}).get("id"),
        "approval_status": "pending" if approval else None,
        "speak": (
            f"Site draft ready for {gen.get('title')} → data/sites/{slug}. "
            + (
                f"Approve deploy: approve {(approval or {}).get('id')}"
                if approval
                else "Open index.html to preview."
            )
        ),
        "next": f"Open {preview} locally. Then: approve {(approval or {}).get('id')} to host on Vercel (needs VERCEL_TOKEN).",
    }


def list_sites(limit: int = 20) -> dict[str, Any]:
    items = []
    for p in sorted(_sites_root().glob("*/site.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            items.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return {"ok": True, "count": len(items), "sites": items}


def _collect_files(root: Path) -> list[dict[str, str]]:
    """Vercel deployment file entries (path + raw content as string)."""
    out = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name == "site.json":
            continue
        rel = path.relative_to(root).as_posix()
        # skip huge binaries
        if path.stat().st_size > 1_500_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        out.append({"file": rel, "data": text})
    return out


async def deploy_to_vercel(slug: str) -> dict[str, Any]:
    token = (settings.vercel_token or "").strip()
    root = _sites_root() / slug
    if not root.exists():
        return {"ok": False, "error": f"site not found: {slug}", "speak": "No local site with that slug."}
    if not token:
        return {
            "ok": False,
            "error": "VERCEL_TOKEN missing",
            "path": str(root.resolve()),
            "hint": "Set VERCEL_TOKEN (and optional VERCEL_TEAM_ID) in .env, then approve deploy again.",
            "speak": f"Site files ready at {root}. Add VERCEL_TOKEN to auto-host.",
            "manual": f"cd {root} && npx vercel --yes --token $VERCEL_TOKEN",
        }

    files = _collect_files(root)
    if not files:
        return {"ok": False, "error": "no deployable files"}

    # Convert to Vercel API format (v13 uses files with file+data)
    payload: dict[str, Any] = {
        "name": slug[:50],
        "projectSettings": {"framework": None},
        "files": [{"file": f["file"], "data": f["data"]} for f in files],
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    params = {}
    if (settings.vercel_team_id or "").strip():
        params["teamId"] = settings.vercel_team_id.strip()

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                "https://api.vercel.com/v13/deployments",
                headers=headers,
                params=params,
                json=payload,
            )
            body = r.json() if r.content else {}
            if r.status_code >= 400:
                return {
                    "ok": False,
                    "error": body.get("error", {}).get("message") if isinstance(body.get("error"), dict) else body or r.text[:400],
                    "status_code": r.status_code,
                    "speak": "Vercel deploy failed — check token/permissions.",
                }
            url = body.get("url") or body.get("alias")
            if url and not str(url).startswith("http"):
                url = f"https://{url}"
            meta_path = root / "site.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
            meta.update(
                {
                    "status": "deployed",
                    "vercel": {"id": body.get("id"), "url": url, "inspector": body.get("inspectorUrl")},
                    "deployed_at": _now(),
                }
            )
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            return {
                "ok": True,
                "slug": slug,
                "url": url,
                "deployment_id": body.get("id"),
                "speak": f"Live on Vercel: {url}" if url else "Deployed — check Vercel dashboard.",
                "vercel": body,
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "speak": f"Deploy error: {exc}"}


async def handle_site_approval(approval_id: str) -> dict[str, Any]:
    """If an approved site_deploy approval exists, deploy it."""
    item = get_approval(approval_id)
    if not item:
        return {"ok": False, "error": "approval not found"}
    if item.get("kind") != "site_deploy":
        return {"ok": False, "error": "not a site deploy approval", "item": item}
    if item.get("status") != "approved":
        return {"ok": False, "error": f"status is {item.get('status')}", "speak": "Approve it first."}
    slug = str((item.get("payload") or {}).get("slug") or "")
    result = await deploy_to_vercel(slug)
    if result.get("ok"):
        mark_executed(approval_id, result)
    return result


async def tool_sites(
    action: str = "create",
    *,
    brief: str = "",
    message: str = "",
    slug: str = "",
    approval_id: str = "",
) -> dict[str, Any]:
    action = (action or "create").lower().strip()
    text = brief or message
    if action in {"create", "build", "make", "new"}:
        if not text.strip():
            return {"ok": False, "error": "need a brief", "speak": "Say what business/site to build."}
        return await create_website(text)
    if action in {"list", "status"}:
        return list_sites()
    if action in {"deploy"}:
        m = re.search(r"\b([a-z0-9\-]{4,})\b", slug or message)
        s = slug or (m.group(1) if m else "")
        if not s:
            return {"ok": False, "error": "need slug"}
        return await deploy_to_vercel(s)
    if action in {"deploy_approved", "finalize"}:
        m = re.search(r"\b([a-f0-9]{8,10})\b", approval_id or message)
        aid = approval_id or (m.group(1) if m else "")
        if not aid:
            return {"ok": False, "error": "need approval id"}
        # ensure approved
        from voxoryl.approvals import decide

        decide(aid, approve=True)
        return await handle_site_approval(aid)
    return {"ok": False, "error": f"unknown action {action}"}
