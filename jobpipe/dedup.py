"""Deduplication — runs BEFORE any LLM call.

The target channel reposts heavily and most posts carry no company and no URL,
so metadata fingerprinting alone fails. Two layers:

  1. body_hash  — exact match on normalized text (free, catches verbatim reposts)
  2. fuzzy      — RapidFuzz on normalized body vs recent posts (catches edits)

A third metadata fingerprint catches the same job arriving from another source.
"""
from __future__ import annotations

import hashlib
import re

from rapidfuzz import fuzz

from .config import DEDUP_FUZZ

_URL = re.compile(r"https?://\S+")
_HANDLE = re.compile(r"@[A-Za-z0-9_]{4,32}")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_NONWORD = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")
# Channel furniture that changes between otherwise-identical reposts.
_NOISE = re.compile(
    r"(join\s+us|share\s+with\s+friends|#\w+|apply\s+fast|t\.me/\S+|"
    r"forwarded\s+from.*|follow\s+@\S+|\bshare\b)",
    re.I,
)


def normalize_body(text: str) -> str:
    """Aggressively normalize so cosmetic repost edits collapse together."""
    t = text.lower()
    t = _URL.sub(" ", t)
    t = _EMAIL.sub(" ", t)
    t = _HANDLE.sub(" ", t)
    t = _NOISE.sub(" ", t)
    t = _NONWORD.sub(" ", t)
    return _WS.sub(" ", t).strip()


def body_hash(text: str) -> str:
    return hashlib.sha256(normalize_body(text).encode("utf-8")).hexdigest()


def metadata_fingerprint(company, title, location, url) -> str | None:
    """Cross-source fingerprint. Returns None when there isn't enough signal."""
    parts = [(company or "").strip().lower(), (title or "").strip().lower()]
    if not parts[1]:
        return None
    # Title alone is far too weak; require a company or a URL to anchor it.
    anchor = (company or "").strip().lower() or (url or "").strip().lower()
    if not anchor:
        return None
    parts += [(location or "").strip().lower(), (url or "").strip().lower()]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def find_near_duplicate(con, text: str, exclude_post_id: int | None = None):
    """Return (post_id, similarity) of the closest recent near-duplicate, or None."""
    norm = normalize_body(text)
    if len(norm) < 40:
        return None
    rows = con.execute(
        "SELECT id, body FROM posts ORDER BY id DESC LIMIT 400"
    ).fetchall()
    best, best_score = None, 0
    for r in rows:
        if exclude_post_id is not None and r["id"] == exclude_post_id:
            continue
        s = fuzz.token_set_ratio(norm, normalize_body(r["body"]))
        if s > best_score:
            best, best_score = r["id"], s
    if best is not None and best_score >= DEDUP_FUZZ:
        return best, best_score
    return None
