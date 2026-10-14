"""SQLite storage. Four tables; `decisions` is the feedback loop."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .config import DB_PATH

SCHEMA = """
PRAGMA journal_mode=WAL;

-- Raw inbound posts. Written by the watcher, never mutated.
CREATE TABLE IF NOT EXISTS posts (
    id            INTEGER PRIMARY KEY,
    source        TEXT NOT NULL,
    source_msg_id TEXT,
    url           TEXT,
    body          TEXT NOT NULL,
    body_hash     TEXT NOT NULL,
    posted_at     TEXT,
    fetched_at    TEXT NOT NULL,
    duplicate_of  INTEGER REFERENCES posts(id),
    dup_kind      TEXT,
    UNIQUE(source, source_msg_id)
);
CREATE INDEX IF NOT EXISTS idx_posts_hash ON posts(body_hash);

-- Parsed + scored jobs.
CREATE TABLE IF NOT EXISTS jobs (
    id                  INTEGER PRIMARY KEY,
    post_id             INTEGER NOT NULL REFERENCES posts(id),
    title               TEXT,
    company             TEXT,
    location            TEXT,
    remote              INTEGER,
    employment_type     TEXT,
    min_years           REAL,
    required_json       TEXT NOT NULL DEFAULT '[]',
    preferred_json      TEXT NOT NULL DEFAULT '[]',
    salary_raw          TEXT,
    salary_confidence   TEXT DEFAULT 'low',
    application_email   TEXT,
    application_telegram TEXT,
    application_url     TEXT,
    deadline_text       TEXT,
    urgency             TEXT,
    materials_json      TEXT NOT NULL DEFAULT '[]',
    extracted_by        TEXT,              -- regex | regex+llm
    score               INTEGER,
    bucket              TEXT,
    evidence_depth      REAL,
    flags_json          TEXT NOT NULL DEFAULT '[]',
    blockers_json       TEXT NOT NULL DEFAULT '[]',
    cv_variant          TEXT,
    fingerprint         TEXT,
    duplicate_of        INTEGER REFERENCES jobs(id),
    status              TEXT NOT NULL DEFAULT 'NEW',
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_fp ON jobs(fingerprint);

-- Every accept/reject you make, with the features that produced it.
-- After ~150 rows this beats any hand-tuned weight vector.
CREATE TABLE IF NOT EXISTS decisions (
    id         INTEGER PRIMARY KEY,
    job_id     INTEGER NOT NULL REFERENCES jobs(id),
    action     TEXT NOT NULL,            -- APPROVE | REJECT | SNOOZE
    reason     TEXT,
    score      INTEGER,
    bucket     TEXT,
    features   TEXT,                     -- json snapshot at decision time
    decided_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id              INTEGER PRIMARY KEY,
    job_id          INTEGER NOT NULL REFERENCES jobs(id),
    cv_variant      TEXT,
    email_subject   TEXT,
    email_body      TEXT,
    eml_path        TEXT,
    generated_at    TEXT NOT NULL,
    sent_at         TEXT,
    follow_up_due   TEXT,
    response_status TEXT,
    notes           TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    # Safe migration: add actor column if the DB predates this change.
    try:
        con.execute("ALTER TABLE decisions ADD COLUMN actor TEXT")
        con.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    return con


def jl(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def ul(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return default if default is not None else []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default if default is not None else []
