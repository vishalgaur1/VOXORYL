"""SQLite events + migrations — episodic perception/task ledger."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from voxoryl.config import settings

SCHEMA_VERSION = 1


def db_path() -> Path:
    root = settings.voxoryl_data_dir
    root.mkdir(parents=True, exist_ok=True)
    return root / "voxoryl.db"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def migrate() -> dict[str, Any]:
    conn = connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        current = int(row["version"]) if row else 0
        if current < 1:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL NOT NULL,
                  channel TEXT,
                  type TEXT,
                  payload TEXT
                );
                CREATE TABLE IF NOT EXISTS task_runs (
                  id TEXT PRIMARY KEY,
                  ts REAL,
                  goal TEXT,
                  ok INTEGER,
                  budget TEXT
                );
                CREATE TABLE IF NOT EXISTS tool_runs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL,
                  task_id TEXT,
                  tool TEXT,
                  ok INTEGER,
                  latency_ms INTEGER
                );
                CREATE TABLE IF NOT EXISTS perception_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL,
                  kind TEXT,
                  detail TEXT
                );
                CREATE TABLE IF NOT EXISTS transcriptions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL,
                  raw_text TEXT,
                  normalized_text TEXT,
                  asr_confidence REAL,
                  endpoint_confidence REAL,
                  duration_ms REAL
                );
                """
            )
            if row:
                conn.execute("UPDATE schema_version SET version=1")
            else:
                conn.execute("INSERT INTO schema_version(version) VALUES (1)")
            conn.commit()
            current = 1
        return {"ok": True, "version": current, "path": str(db_path())}
    finally:
        conn.close()


def log_event(channel: str, type_: str, payload: str = "") -> None:
    try:
        migrate()
        conn = connect()
        conn.execute(
            "INSERT INTO events(ts, channel, type, payload) VALUES (?,?,?,?)",
            (time.time(), channel, type_, payload[:4000]),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def log_transcription(
    raw: str,
    normalized: str,
    *,
    asr_confidence: float = 1.0,
    endpoint_confidence: float = 1.0,
    duration_ms: float = 0.0,
) -> None:
    try:
        migrate()
        conn = connect()
        conn.execute(
            "INSERT INTO transcriptions(ts, raw_text, normalized_text, asr_confidence, endpoint_confidence, duration_ms) VALUES (?,?,?,?,?,?)",
            (time.time(), raw, normalized, asr_confidence, endpoint_confidence, duration_ms),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def status() -> dict[str, Any]:
    try:
        info = migrate()
        conn = connect()
        n = conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
        conn.close()
        info["event_count"] = n
        return info
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
