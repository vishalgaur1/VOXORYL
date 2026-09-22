from __future__ import annotations

import os

USE_MOCK = os.getenv("VOXORYL_MOCK", "").strip() in {"1", "true", "yes"}

MOCK_VOICES = {
    "strategist": (
        "Prioritize compounding work around Saint's active projects. "
        "Avoid one-off busywork. Recommendation: capture the goal in memory, "
        "then research one sharp market angle before building."
    ),
    "critic": (
        "We don't have enough project context yet. Jumping into automation without "
        "notes/github connectors risks shallow advice. Recommendation: ask Saint "
        "for the current build focus, then run research."
    ),
    "builder": (
        "Concrete next actions: 1) save project name to memory, 2) run research tool, "
        "3) write a note with three experiment ideas. Recommendation: execute those three."
    ),
    "scout": (
        "Market signal: small local agents with tool use are rising. Pair research with "
        "a weekly idea digest. Recommendation: daily autonomy brief focused on Saint's niche."
    ),
}
