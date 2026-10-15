"""Telegram alerting via your own bot. Phone-actionable by design: the alert
carries everything needed to decide, because the ASAP jobs will land when you
are not at your laptop."""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from .config import TG_ALERT_CHAT_ID, TG_BOT_TOKEN
from .matcher import Match
from .utils import html_escape

log = logging.getLogger(__name__)

ICON = {"APPLY": "\U0001f7e2", "APPLY (HOLD)": "\U0001f7e1", "REVIEW": "\U0001f535", "SKIP": "\u26aa"}


def format_alert(
    job_id: int,
    match: Match,
    parsed,
    cv_variant: str,
    url: str | None,
    include_commands: bool = True,
) -> str:
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
    if include_commands:
        lines.append(f"\n<code>jobpipe approve {job_id}</code> | <code>jobpipe reject {job_id}</code>")
    return "\n".join(lines)


def _esc(s) -> str:
    return html_escape(s)


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


def send_with_buttons(job_id: int, text: str) -> bool:
    """Send an alert with inline Approve / Reject / Info buttons.

    Uses the Bot API sendMessage endpoint with reply_markup. Falls back to
    plain send() if the bot token is not configured (e.g., only the user
    client is running) so terminal users still see the text.
    """
    if not (TG_BOT_TOKEN and TG_ALERT_CHAT_ID):
        return send(text)

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "\u2705 Approve", "callback_data": f"approve:{job_id}"},
                {"text": "\u274c Reject", "callback_data": f"reject:{job_id}"},
            ],
            [
                {"text": "\U0001f4cb Info", "callback_data": f"info:{job_id}"},
            ],
        ]
    }
    data = urllib.parse.urlencode(
        {
            "chat_id": TG_ALERT_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
            "reply_markup": json.dumps(reply_markup),
        }
    ).encode()
    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage", data=data, timeout=20
        ) as r:
            return r.status == 200
    except Exception as e:  # noqa: BLE001
        log.warning("notify (buttons) failed: %s — falling back to plain send", e)
        return send(text)


def _bot_api(path: str, payload: dict) -> dict | None:
    """Helper: POST to the Bot API and return the parsed JSON response."""
    data = urllib.parse.urlencode(payload).encode()
    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/{path}", data=data, timeout=20
        ) as r:
            return json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        log.warning("bot api %s failed: %s", path, e)
        return None


def _send_document(chat_id: str, file_path: str, caption: str = "") -> bool:
    """Send a file to a chat using multipart/form-data."""
    import io
    from pathlib import Path
    from urllib.request import Request

    p = Path(file_path)
    if not p.exists():
        log.warning("send_document: file not found: %s", file_path)
        return False

    boundary = "----JobPipeBoundary"
    body_parts = []

    def _field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode()

    body_parts.append(_field("chat_id", str(chat_id)))
    if caption:
        body_parts.append(_field("caption", caption))
        body_parts.append(_field("parse_mode", "HTML"))

    file_bytes = p.read_bytes()
    body_parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="document"; filename="{p.name}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        + file_bytes
        + b"\r\n"
    )
    body_parts.append(f"--{boundary}--\r\n".encode())

    body = b"".join(body_parts)
    req = Request(
        f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendDocument",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status == 200
    except Exception as e:  # noqa: BLE001
        log.warning("send_document failed: %s", e)
        return False


def _format_info(con, job_id: int) -> str:
    """Format a detailed job breakdown for the /info command."""
    r = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if r is None:
        return f"No job {job_id} found."

    from .db import ul

    lines = [
        f"<b>Job {job_id}: {_esc(r['title'] or 'Unknown')}</b>",
        f"Company: {_esc(r['company'] or '-')}",
        f"Score: {r['score'] or 0}  Bucket: {r['bucket'] or '-'}",
        f"Evidence depth: {r['evidence_depth'] or 0:.2f}",
        f"Status: {r['status']}",
        f"Remote: {bool(r['remote']) if r['remote'] is not None else '?'}",
        f"Employment: {r['employment_type'] or '-'}",
        f"Min years: {r['min_years'] or '-'}",
        f"Salary: {_esc(r['salary_raw'] or '-')}",
        f"Email: <code>{_esc(r['application_email'] or '-')}</code>",
    ]
    flags = ul(r["flags_json"])
    if flags:
        lines.append("Flags: " + "; ".join(_esc(f) for f in flags[:6]))
    blockers = ul(r["blockers_json"])
    if blockers:
        lines.append("\u26d4 Blockers: " + "; ".join(_esc(b) for b in blockers))
    required = ul(r["required_json"])
    if required:
        lines.append("Required: " + ", ".join(_esc(s) for s in required[:10]))
    return "\n".join(lines)


def _bot_approve(con, job_id: int) -> dict:
    """Approve a job from the bot path and record actor='bot'.

    Passes actor='bot' directly to pipeline.approve() so the decision row
    is written correctly in a single operation — no post-hoc UPDATE needed.
    """
    from .pipeline import approve as _pipeline_approve

    return _pipeline_approve(con, job_id, actor="bot")


def send_bot_command_updates() -> None:
    """Polling loop: processes getUpdates and handles button presses and
    /approve N, /reject N, /info N commands. Runs blocking; call via
    asyncio.to_thread or a thread.

    Opens its own SQLite connection so the connection is always used on
    the same thread — the default SQLite check_same_thread=True setting
    rejects cross-thread access, and this function runs in a thread pool
    spawned by asyncio.to_thread.
    """
    import re as _re
    import time

    if not (TG_BOT_TOKEN and TG_ALERT_CHAT_ID):
        return

    from .db import connect as _connect
    from .pipeline import reject as _reject

    con = _connect()

    offset = 0
    while True:
        # Outer guard: any unexpected exception (network, JSON parse, etc.)
        # logs and continues instead of killing the thread permanently.
        try:
            resp = _bot_api("getUpdates", {"offset": offset, "timeout": 30, "limit": 10})
            if resp is None or not resp.get("ok"):
                time.sleep(5)
                continue

            for update in resp.get("result", []):
                offset = update["update_id"] + 1

                # --- inline keyboard callbacks -------------------------------
                if "callback_query" in update:
                    cq = update["callback_query"]
                    cq_id = cq["id"]
                    data = cq.get("data", "")

                    try:
                        kind, _, id_str = data.partition(":")
                        job_id = int(id_str)
                    except (ValueError, AttributeError):
                        _bot_api("answerCallbackQuery", {"callback_query_id": cq_id,
                                                         "text": "Unknown command."})
                        continue

                    if kind == "approve":
                        try:
                            res = _bot_approve(con, job_id)
                            _bot_api("answerCallbackQuery", {"callback_query_id": cq_id,
                                                             "text": f"Approved job {job_id}"})
                            caption = f"\u2705 Approved job {job_id} — materials ready"
                            if res.get("eml"):
                                _send_document(TG_ALERT_CHAT_ID, res["eml"], caption)
                            else:
                                send(caption)
                        except ValueError as e:
                            _bot_api("answerCallbackQuery", {"callback_query_id": cq_id,
                                                             "text": str(e)[:200]})
                            send(f"\u26a0 Approve {job_id} failed: {_esc(str(e))}")

                    elif kind == "reject":
                        try:
                            _reject(con, job_id)
                            _bot_api("answerCallbackQuery", {"callback_query_id": cq_id,
                                                             "text": f"Rejected job {job_id}"})
                            send(f"\u274c Rejected job {job_id}")
                        except ValueError as e:
                            _bot_api("answerCallbackQuery", {"callback_query_id": cq_id,
                                                             "text": str(e)[:200]})

                    elif kind == "info":
                        _bot_api("answerCallbackQuery", {"callback_query_id": cq_id, "text": ""})
                        info_text = _format_info(con, job_id)
                        send(info_text)

                    else:
                        _bot_api("answerCallbackQuery", {"callback_query_id": cq_id,
                                                         "text": "Unknown button."})
                    continue

                # --- text commands -------------------------------------------
                if "message" not in update:
                    continue
                msg = update["message"]
                text = (msg.get("text") or "").strip()

                m = _re.match(r"^/approve\s+(\d+)", text, _re.I)
                if m:
                    job_id = int(m.group(1))
                    try:
                        res = _bot_approve(con, job_id)
                        caption = f"\u2705 Approved job {job_id} — materials ready"
                        if res.get("eml"):
                            _send_document(TG_ALERT_CHAT_ID, res["eml"], caption)
                        else:
                            send(caption)
                    except ValueError as e:
                        send(f"\u26a0 Approve {job_id} failed: {_esc(str(e))}")
                    continue

                m = _re.match(r"^/reject\s+(\d+)(?:\s+(.+))?", text, _re.I)
                if m:
                    job_id = int(m.group(1))
                    reason = (m.group(2) or "").strip() or None
                    try:
                        _reject(con, job_id, reason)
                        send(f"\u274c Rejected job {job_id}"
                             + (f" ({_esc(reason)})" if reason else ""))
                    except ValueError as e:
                        send(f"\u26a0 Reject {job_id} failed: {_esc(str(e))}")
                    continue

                m = _re.match(r"^/info\s+(\d+)", text, _re.I)
                if m:
                    job_id = int(m.group(1))
                    send(_format_info(con, job_id))
                    continue

        except Exception:  # noqa: BLE001
            log.exception("bot polling loop error — retrying in 10s")
            time.sleep(10)


async def start_bot_listener(con) -> None:
    """Async wrapper that runs the blocking polling loop in a thread.
    Launch with asyncio.create_task(start_bot_listener(con)) from the watcher.
    Exceptions are caught and logged so a bot failure never kills the watcher.

    The con argument is kept for backwards compatibility but is no longer
    used — send_bot_command_updates opens its own connection to satisfy
    SQLite's thread-safety requirement.
    """
    import asyncio

    if not (TG_BOT_TOKEN and TG_ALERT_CHAT_ID):
        return
    try:
        await asyncio.to_thread(send_bot_command_updates)
    except Exception:  # noqa: BLE001
        log.exception("bot listener crashed")
