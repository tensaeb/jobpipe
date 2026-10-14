"""SMTP delivery for approved applications.

Why SMTP + app password rather than the Gmail API: `gmail.send` is a sensitive
scope, and while the OAuth consent screen is in Testing your refresh token dies
every 7 days. For a single-user personal tool that is a weekly outage for no
benefit. An app password is one secret, no consent screen, no verification.

Nothing here decides WHETHER to send. policy.check() does that.
"""
from __future__ import annotations

import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from . import notify, policy
from .config import (
    CV_DIR,
    DRY_RUN,
    SENDER_EMAIL,
    SENDER_NAME,
    SMTP_HOST,
    SMTP_AUTH,
    SMTP_PASS,
    SMTP_PORT,
    SMTP_STARTTLS,
    SMTP_USER,
)
from .db import now

log = logging.getLogger(__name__)


def build_message(to_addr: str, subject: str, body: str, cv_variant: str | None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=SENDER_EMAIL.rsplit("@", 1)[-1])
    msg["Reply-To"] = SENDER_EMAIL
    msg.set_content(body)
    if cv_variant:
        cv = CV_DIR / cv_variant
        if cv.exists():
            msg.add_attachment(
                cv.read_bytes(),
                maintype="application",
                subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
                filename=cv.name,
            )
    return msg


def _deliver(msg: EmailMessage) -> str:
    """Actually talk to the SMTP server. Returns the server response line."""
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=45) as s:
        s.ehlo()
        if SMTP_STARTTLS:
            s.starttls(context=ssl.create_default_context())
            s.ehlo()
        if SMTP_AUTH:
            s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
    return "250 accepted"


def send_one(con, job_id: int, *, manual: bool = False, force_dry: bool | None = None) -> dict:
    """Send one approved application. Returns a result dict; never raises on
    a policy block (that's a normal outcome), only on transport failure."""
    reasons, ctx = policy.check(con, job_id, manual=manual)
    if reasons:
        return {"job_id": job_id, "sent": False, "blocked": reasons}

    job, app = ctx["job"], ctx["app"]
    dry = DRY_RUN if force_dry is None else force_dry

    msg = build_message(
        job["application_email"], app["email_subject"], app["email_body"], app["cv_variant"]
    )
    has_cv = any(part.get_filename() for part in msg.iter_attachments())

    # Verify the recipient address was present in the original post text —
    # guards against an LLM-supplied address that never appeared in the post.
    post_row = con.execute(
        "SELECT p.body FROM posts p JOIN jobs j ON j.post_id = p.id WHERE j.id=?",
        (job_id,),
    ).fetchone()
    if post_row and job["application_email"]:
        if job["application_email"].lower() not in (post_row["body"] or "").lower():
            log.warning(
                "job %s: recipient %r does not appear in the original post text "
                "(address may be LLM-supplied — verify before sending)",
                job_id,
                job["application_email"],
            )

    if dry:
        return {
            "job_id": job_id, "sent": False, "dry_run": True,
            "to": job["application_email"], "subject": app["email_subject"],
            "attachment": app["cv_variant"] if has_cv else None,
            "bytes": len(bytes(msg)), "blocked": [],
        }

    resp = _deliver(msg)

    con.execute(
        "UPDATE applications SET sent_at=?, notes=COALESCE(notes,'')||? WHERE id=?",
        (now(), f"[smtp {resp}]", app["id"]),
    )
    con.execute("UPDATE jobs SET status='SENT' WHERE id=?", (job_id,))
    con.commit()

    log.info("sent job %s to %s", job_id, job["application_email"])
    notify.send(
        f"\u2709\ufe0f Application sent\n<b>{_e(job['title'])}</b>"
        f"{' @ ' + _e(job['company']) if job['company'] else ''}\n"
        f"To: <code>{_e(job['application_email'])}</code>\n"
        f"CV: {app['cv_variant']}\nFollow-up due: {app['follow_up_due']}"
    )

    return {
        "job_id": job_id, "sent": True, "to": job["application_email"],
        "subject": app["email_subject"], "attachment": app["cv_variant"] if has_cv else None,
        "blocked": [],
    }


def send_batch(con, *, manual: bool = False, limit: int = 50) -> list[dict]:
    """Send every approved-and-eligible application, newest-highest-score first."""
    rows = con.execute(
        "SELECT a.job_id FROM applications a JOIN jobs j ON j.id = a.job_id"
        " WHERE a.sent_at IS NULL AND j.status='MATERIALS_READY'"
        " ORDER BY j.score DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        out.append(send_one(con, r["job_id"], manual=manual))
    return out


def _e(s) -> str:
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def smtp_selftest() -> tuple[bool, str]:
    """Verify credentials without sending anything."""
    if SMTP_AUTH and not (SMTP_USER and SMTP_PASS):
        return False, "SMTP_USER/SMTP_PASS not set"
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.ehlo()
            if SMTP_STARTTLS:
                s.starttls(context=ssl.create_default_context())
                s.ehlo()
            if SMTP_AUTH:
                s.login(SMTP_USER, SMTP_PASS)
        return True, f"connected to {SMTP_HOST}:{SMTP_PORT}" + (f" as {SMTP_USER}" if SMTP_AUTH else " (no auth)")
    except smtplib.SMTPAuthenticationError as e:
        return False, f"auth rejected ({e.smtp_code}) — use a Gmail App Password, not your login"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
