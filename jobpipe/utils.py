"""Shared helpers used across the jobpipe package.

Kept dependency-free (stdlib only) so every module can import it without
circular-import risk.
"""
from __future__ import annotations

import re
import unicodedata

_HTML_ESCAPE_CHARS = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}


def html_escape(s: str | None) -> str:
    """Escape HTML special characters. None -> empty string."""
    if s is None:
        return ""
    out: list[str] = []
    for ch in str(s):
        out.append(_HTML_ESCAPE_CHARS.get(ch, ch))
    return "".join(out)


def slugify(text: str | None, max_len: int = 40) -> str:
    """Lowercase, replace non-alphanumerics with hyphens, trim, truncate."""
    if not text:
        return "job"
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:max_len] or "job"


def normalize_skill(skill: str) -> str:
    """Normalize a skill string for comparison (NFKD, lowercase, stripped)."""
    return unicodedata.normalize("NFKD", skill or "").strip().lower()


def parse_bool(value: str | None, default: str = "false") -> bool:
    """Parse a truthy/falsy environment variable string."""
    return str(value or default).strip().lower() in ("1", "true", "yes", "on")


def safe_float(value, lo: float = 0.0, hi: float = 40.0):
    """Parse a float clamped to [lo, hi]; None on failure."""
    if value in (None, "", "null"):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if lo <= f <= hi else None
