from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from voxoryl.config import settings
from voxoryl.llm import chat_local, parse_json_loose


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", heading.strip().lower()).strip("-") or "topic"


class KnowledgeGraph:
    """
    Lightweight mind-map / knowledge graph inspired by Heartwood & MindForge patterns:
    topic nodes, typed edges, wikilinks in markdown, interactive HTML force graph.
    No heavy deps — JSON + LLM link discovery + vis-network in the browser.
    """

    def __init__(self) -> None:
        self.graph_path = settings.knowledge_graph_path
        self.html_path = settings.mindmap_html_path
        self.graph_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        if not self.graph_path.exists():
            return {"nodes": [], "edges": [], "updated_at": _now()}
        return json.loads(self.graph_path.read_text(encoding="utf-8"))

    def save(self, graph: dict[str, Any]) -> None:
        graph["updated_at"] = _now()
        self.graph_path.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")

    def ensure_node(self, graph: dict[str, Any], heading: str, *, fact_count: int = 0) -> str:
        nid = _slug(heading)
        nodes = graph.setdefault("nodes", [])
        for n in nodes:
            if n.get("id") == nid or n.get("label", "").lower() == heading.lower():
                n["label"] = heading
                n["fact_count"] = max(int(n.get("fact_count") or 0), fact_count)
                n["updated_at"] = _now()
                return str(n["id"])
        nodes.append(
            {
                "id": nid,
                "label": heading,
                "fact_count": fact_count,
                "updated_at": _now(),
            }
        )
        return nid

    def add_edge(
        self,
        graph: dict[str, Any],
        source: str,
        target: str,
        *,
        relation: str = "related_to",
        reason: str = "",
    ) -> bool:
        if source == target:
            return False
        edges = graph.setdefault("edges", [])
        for e in edges:
            if e.get("source") == source and e.get("target") == target and e.get("relation") == relation:
                if reason and reason not in (e.get("reason") or ""):
                    e["reason"] = reason
                return False
            # undirected-ish: also skip reverse related_to
            if (
                relation == "related_to"
                and e.get("source") == target
                and e.get("target") == source
                and e.get("relation") == "related_to"
            ):
                return False
        edges.append(
            {
                "source": source,
                "target": target,
                "relation": relation,
                "reason": reason,
                "at": _now(),
            }
        )
        return True

    def sync_from_knowledge(self, sections: dict[str, list[str]]) -> dict[str, Any]:
        graph = self.load()
        for heading, facts in sections.items():
            self.ensure_node(graph, heading, fact_count=len(facts))
        # Drop orphan nodes that vanished from knowledge.md
        keep = {_slug(h) for h in sections}
        graph["nodes"] = [n for n in graph.get("nodes", []) if n.get("id") in keep]
        graph["edges"] = [
            e
            for e in graph.get("edges", [])
            if e.get("source") in keep and e.get("target") in keep
        ]
        self.save(graph)
        return graph

    async def discover_links(self, heading: str, facts: list[str], sections: dict[str, list[str]]) -> dict[str, Any]:
        """Ask the local model which other topics connect to this one (Heartwood-style link prediction)."""
        others = [h for h in sections if h.lower() != heading.lower()]
        if not others:
            graph = self.sync_from_knowledge(sections)
            self.render_html(graph)
            return {"ok": True, "links_added": 0, "graph": graph}

        raw = await chat_local(
            [
                {
                    "role": "system",
                    "content": (
                        "You build a personal knowledge mind-map. Return ONLY JSON:\n"
                        '{"links":[{"to":"ExistingHeading","relation":"related_to|part_of|uses|mentions","reason":"short"}]}\n'
                        "Only link to headings from the provided list. 0-4 links. Skip weak connections."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"New/updated topic: {heading}\n"
                        f"Facts:\n- " + "\n- ".join(facts[:8]) + "\n\n"
                        f"Other topics: {', '.join(others)}\n"
                        "Propose real connections only."
                    ),
                },
            ],
            temperature=0.2,
        )
        parsed = parse_json_loose(raw) or {}
        links = parsed.get("links") or []
        graph = self.sync_from_knowledge(sections)
        src = self.ensure_node(graph, heading, fact_count=len(sections.get(heading, [])))
        added = 0
        for link in links:
            to_name = str(link.get("to") or "").strip()
            if not to_name:
                continue
            # fuzzy match to existing heading
            match = next((h for h in others if h.lower() == to_name.lower() or to_name.lower() in h.lower() or h.lower() in to_name.lower()), None)
            if not match:
                continue
            tgt = self.ensure_node(graph, match, fact_count=len(sections.get(match, [])))
            if self.add_edge(
                graph,
                src,
                tgt,
                relation=str(link.get("relation") or "related_to"),
                reason=str(link.get("reason") or "")[:200],
            ):
                added += 1
        # Keyword co-occurrence edges (deterministic, no LLM)
        added += self._cooccurrence_edges(graph, heading, sections)
        self.save(graph)
        self.render_html(graph)
        return {"ok": True, "links_added": added, "graph": graph}

    def _cooccurrence_edges(self, graph: dict[str, Any], heading: str, sections: dict[str, list[str]]) -> int:
        src_text = " ".join(sections.get(heading, [])).lower()
        src_id = _slug(heading)
        added = 0
        for other, facts in sections.items():
            if other.lower() == heading.lower():
                continue
            other_text = f"{other} " + " ".join(facts)
            tokens = {t for t in re.findall(r"[a-z0-9]{4,}", other.lower())}
            # also significant words from other heading
            hits = sum(1 for t in tokens if t in src_text)
            if hits >= 1 and (other.lower() in src_text or any(w in src_text for w in other.lower().split() if len(w) > 3)):
                tgt = self.ensure_node(graph, other, fact_count=len(facts))
                if self.add_edge(graph, src_id, tgt, relation="related_to", reason="shared keywords"):
                    added += 1
        return added

    def neighbors(self, heading: str, limit: int = 6) -> list[dict[str, Any]]:
        graph = self.load()
        nid = _slug(heading)
        out = []
        for e in graph.get("edges", []):
            if e.get("source") == nid:
                label = next((n["label"] for n in graph["nodes"] if n["id"] == e["target"]), e["target"])
                out.append({"heading": label, "relation": e.get("relation"), "reason": e.get("reason")})
            elif e.get("target") == nid:
                label = next((n["label"] for n in graph["nodes"] if n["id"] == e["source"]), e["source"])
                out.append({"heading": label, "relation": e.get("relation"), "reason": e.get("reason")})
            if len(out) >= limit:
                break
        return out

    def render_html(self, graph: dict[str, Any] | None = None) -> str:
        graph = graph or self.load()
        nodes = [
            {
                "id": n["id"],
                "label": n.get("label", n["id"]),
                "value": max(2, int(n.get("fact_count") or 1) + 1),
                "title": f"{n.get('label')}: {n.get('fact_count', 0)} facts",
            }
            for n in graph.get("nodes", [])
        ]
        edges = [
            {
                "from": e["source"],
                "to": e["target"],
                "label": e.get("relation") or "",
                "title": e.get("reason") or e.get("relation") or "",
                "arrows": "to",
            }
            for e in graph.get("edges", [])
        ]
        payload = json.dumps({"nodes": nodes, "edges": edges})
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Voxoryl Mind Map</title>
  <script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
  <style>
    :root {{ --bg:#0e1718; --text:#e7f0ec; --muted:#8aa09a; --teal:#2dd4bf; }}
    html, body {{ margin:0; height:100%; background:var(--bg); color:var(--text); font-family: "IBM Plex Sans", system-ui, sans-serif; }}
    header {{ padding:1rem 1.25rem; border-bottom:1px solid rgba(231,240,236,.12); display:flex; justify-content:space-between; align-items:center; gap:1rem; }}
    h1 {{ margin:0; font-size:1.25rem; letter-spacing:-.02em; }}
    p {{ margin:.25rem 0 0; color:var(--muted); font-size:.85rem; }}
    a {{ color:var(--teal); }}
    #map {{ height: calc(100% - 72px); }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>VOXORYL Mind Map</h1>
      <p>Auto-linked topics from your knowledge log · drag nodes · scroll to zoom</p>
    </div>
    <div>
      <a href="/">Command center</a> ·
      <a href="/widget">Widget</a> ·
      <a href="/api/knowledge/graph">Graph JSON</a>
    </div>
  </header>
  <div id="map"></div>
  <script>
    const data = {payload};
    const nodes = new vis.DataSet(data.nodes);
    const edges = new vis.DataSet(data.edges);
    const network = new vis.Network(document.getElementById('map'), {{ nodes, edges }}, {{
      nodes: {{
        shape: 'dot',
        scaling: {{ min: 12, max: 36 }},
        font: {{ color: '#e7f0ec', size: 14 }},
        color: {{ background: '#1aa894', border: '#2dd4bf', highlight: {{ background: '#d97757', border: '#f0a57a' }} }}
      }},
      edges: {{
        color: {{ color: 'rgba(138,160,154,0.55)', highlight: '#2dd4bf' }},
        font: {{ color: '#8aa09a', size: 10, strokeWidth: 0 }},
        smooth: {{ type: 'continuous' }}
      }},
      physics: {{ stabilization: true, barnesHut: {{ gravitationalConstant: -12000, springLength: 140 }} }},
      interaction: {{ hover: true, tooltipDelay: 80 }}
    }});
  </script>
</body>
</html>
"""
        self.html_path.write_text(html, encoding="utf-8")
        return str(self.html_path.resolve())


mindmap = KnowledgeGraph()
