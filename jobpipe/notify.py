"""Telegram alerting via your own bot. Phone-actionable by design: the alert
carries everything needed to decide, because the ASAP jobs will land when you
are not at your laptop."""
from __future__ import annotations

import logging
import urllib.parse
import urllib.request

from .config import TG_ALERT_CHAT_ID, TG_BOT_TOKEN
from .matcher import Match

log = logging.getLogger(__name__)

ICON = {"APPLY": "\U0001f7e2", "APPLY (HOLD)": "\U0001f7e1", "REVIEW": "\U0001f535", "SKIP": "\u26aa"}


def format_alert(job_id: int, match: Match, parsed, cv_variant: str, url: str | None) -> str:
    j = match.job
    lines = [
        f"{ICON.get(match.bucket, '')} <b>{match.bucket}</b> \u2014 score {match.score}",
        f"<b>{_esc(j.title or 'Unknown role')}</b>",
    ]
    if j.company:
        lines.append(f"Company: {_esc(j.company)}")
    bits = []
    if parsed.remote:
        bits.append("Remote")
    if parsed.employment_type:
        bits.append(parsed.employment_type)
    if parsed.min_years:
        bits.append(f"{parsed.min_years:g}y+")
    if bits:
        lines.append(" \u2022 ".join(bits))
    if parsed.salary_raw:
        lines.append(f"Salary (unverified): {_esc(parsed.salary_raw)}")

    if match.required_hits:
        lines.append("\nMatched: " + ", ".join(f"{_esc(s)}" for s, _, _ in match.required_hits[:8]))
    if match.required_misses:
        lines.append("Missing: " + ", ".join(_esc(s) for s in match.required_misses[:8]))
    lines.append(f"Evidence depth: {match.evidence_depth:.2f}")

    if parsed.required_materials:
        lines.append("Wants: " + ", ".join(_esc(m) for m in parsed.required_materials))
    if parsed.application_email:
        lines.append(f"Email: <code>{_esc(parsed.application_email)}</code>")
    if parsed.application_telegram:
        lines.append(f"Telegram: @{_esc(parsed.application_telegram)} (apply manually)")

    if match.blockers:
        lines.append("\n\u26d4 " + "; ".join(_esc(b) for b in match.blockers))
    for f in match.flags[:6]:
        lines.append("\u26a0 " + _esc(f))

    lines.append(f"\nCV: <code>{cv_variant}</code>")
    if url:
        lines.append(url)
    lines.append(f"\n<code>jobpipe approve {job_id}</code> | <code>jobpipe reject {job_id}</code>")
    return "\n".join(lines)


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send(text: str) -> bool:
    if not (TG_BOT_TOKEN and TG_ALERT_CHAT_ID):
        log.info("notify: bot not configured, printing instead\n%s", text)
        return False
    data = urllib.parse.urlencode(
        {
            "chat_id": TG_ALERT_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode()
    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage", data=data, timeout=20
        ) as r:
            return r.status == 200
    except Exception as e:  # noqa: BLE001
        log.warning("notify failed: %s", e)
        return False
