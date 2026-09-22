# Vendored inspiration (patterns implemented under voxoryl/, not full forks)

These projects informed Voxoryl power-ups. We did not vendor megarepos; we ported the patterns that fit a 4B/6GB box.

| Source | What we took | Where in Voxoryl |
|---|---|---|
| LocalClaw | Router → deterministic pipelines | `voxoryl/pipelines.py` |
| smolagents | Code-as-actions | `voxoryl/code_act.py` |
| Outlines / schema agents | JSON schema lock + repair | `voxoryl/schema_lock.py` |
| SLM-default LLM-fallback papers | Groq verifier cascade | `voxoryl/verifier.py` |
| Heartwood / MindForge | Knowledge graph + mind map | `voxoryl/knowledge.py`, `mindmap.py` |
| MCP servers | Tool registry file | `setup/mcp.servers.json` |
| edge-agent | Guardrail/router/fallback idea | verifier + pipelines |

Enable MCP servers by editing `setup/mcp.servers.json` (`enabled: true`) once Node/npx is available.
