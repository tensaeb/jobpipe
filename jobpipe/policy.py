"""Send policy — every gate that must pass before an email leaves the machine.

Auto-apply here means "send what YOU already approved, without you opening a
mail client". It is not autonomous application. The approval step still exists
and `AUTO_APPLY` is off by default.

Each check returns a reason string when it BLOCKS. An empty list means send.
Checks are ordered cheapest-and-most-fatal first.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .config import (
    ALLOW_PERSONAL_EMAIL,
    AUTO_APPLY,
    AUTO_APPLY_MIN_SCORE,
    BLOCKED_DOMAINS,
    CV_DIR,
    DAILY_SEND_LIMIT,
    REQUIRE_CV_ATTACHMENT,
    SMTP_AUTH,
    SMTP_PASS,
    SMTP_USER,
)
from .db import ul

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
FREEMAIL = re.compile(r"@(gmail|yahoo|outlook|hotmail|proton(mail)?|icloud|aol|mail)\.", re.I)
DISPOSABLE = re.compile(r"@(mailinator|guerrillamail|10minutemail|tempmail|yopmail)\.", re.I)


def sends_today(con) -> int:
    return con.execute(
        "SELECT COUNT(*) FROM applications WHERE sent_at IS NOT NULL"
        " AND date(sent_at) = date('now')"
    ).fetchone()[0]


def check(con, job_id: int, *, manual: bool = False) -> tuple[list[str], dict]:
    """Returns (blocking_reasons, context). manual=True relaxes only the
    AUTO_APPLY master switch and the score floor — never the safety gates."""
    reasons: list[str] = []

    job = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if job is None:
        return [f"job {job_id} does not exist"], {}

    app = con.execute(
        "SELECT * FROM applications WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,)
    ).fetchone()

    ctx = {"job": job, "app": app}

    # --- master switch ------------------------------------------------------
    if not manual and not AUTO_APPLY:
        reasons.append("AUTO_APPLY=false (set it in .env, or use `jobpipe send ID`)")

    # --- approval state -----------------------------------------------------
    if app is None:
        reasons.append("no materials generated — run `jobpipe approve` first")
    elif app["sent_at"]:
        reasons.append(f"already sent at {app['sent_at']} (idempotency guard)")

    if job["status"] == "QUARANTINED":
        reasons.append("job is QUARANTINED (adversarial post content)")
    if job["status"] == "REJECTED":
        reasons.append("job was rejected")
    if job["status"] == "DUPLICATE":
        reasons.append(f"duplicate of job {job['duplicate_of']}")

    # Human-only approvals: agent-signed APPROVE decisions (e.g. from triage
    # --act or the agent) do not satisfy the send gate. Only a user or the
    # Telegram bot (acting on the user's behalf) can authorize a send.
    approved = con.execute(
        "SELECT 1 FROM decisions WHERE job_id=? AND action='APPROVE'"
        " AND actor IN ('user','bot') LIMIT 1", (job_id,)
    ).fetchone()
    if not approved:
        reasons.append(
            "no human APPROVE decision recorded for this job "
            "(agent approvals do not count)"
        )

    # --- unresolved data conflicts (CV honesty) -----------------------------
    for f in ul(job["flags_json"]):
        if f.startswith("BLOCKED:"):
            reasons.append(f)
        if f.startswith("QUARANTINED"):
            reasons.append(f)

    # --- recipient integrity ------------------------------------------------
    to = (job["application_email"] or "").strip()
    if not to:
        reasons.append("no recipient address (apply manually)")
    else:
        if not EMAIL_RE.match(to):
            reasons.append(f"recipient {to!r} is not a valid address")
        if DISPOSABLE.search(to):
            reasons.append(f"recipient uses a disposable domain: {to}")
        domain = to.rsplit("@", 1)[-1].lower()
        if any(domain == b or domain.endswith("." + b) for b in BLOCKED_DOMAINS):
            reasons.append(f"recipient domain {domain} is blocklisted")
        if FREEMAIL.search(to) and not ALLOW_PERSONAL_EMAIL:
            reasons.append(
                f"recipient {to} is a personal mailbox — employer unverified "
                "(set ALLOW_PERSONAL_EMAIL=true to permit)"
            )

    multi = [f for f in ul(job["flags_json"]) if f.startswith("MULTIPLE_CONTACTS")]
    if multi:
        reasons.append("post lists several contacts — pick one manually")

    # --- never automate these -----------------------------------------------
    if job["application_telegram"] and not to:
        reasons.append("Telegram-only application — never automated, DM manually")

    # --- quality floor ------------------------------------------------------
    if not manual and (job["score"] or 0) < AUTO_APPLY_MIN_SCORE:
        reasons.append(f"score {job['score']} below AUTO_APPLY_MIN_SCORE={AUTO_APPLY_MIN_SCORE}")

    # --- materials ----------------------------------------------------------
    if app is not None and REQUIRE_CV_ATTACHMENT:
        cv = CV_DIR / (app["cv_variant"] or "")
        if not cv.exists():
            reasons.append(f"CV variant {app['cv_variant']} missing from {CV_DIR}")

    # --- unfilled placeholder check ----------------------------------------
    # write_body() appends [YOUR ANSWER: ...] markers when a post requests
    # salary, notice period, GitHub, etc. Transmitting these to recruiters
    # would be embarrassing — block until the user fills them in the .eml.
    # Check both the stored DB body and the .eml file on disk independently;
    # if EITHER still has unfilled placeholders, block the send.
    placeholder_blocked = False
    if app is not None:
        db_body = app["email_body"] or ""
        if "[YOUR ANSWER:" in db_body:
            placeholder_blocked = True

        # Also check the .eml file on disk (the user may have edited it).
        eml_path = app["eml_path"]
        if eml_path:
            try:
                from email import policy as _email_policy
                from email.parser import BytesParser
                p = Path(eml_path)
                if p.exists():
                    _msg = BytesParser(policy=_email_policy.default).parse(p.open("rb"))
                    eml_body = _msg.get_content() or ""
                    if "[YOUR ANSWER:" in eml_body:
                        placeholder_blocked = True
            except Exception:  # noqa: BLE001
                pass

    if placeholder_blocked:
        reasons.append(
            "email body contains unfilled [YOUR ANSWER: ...] placeholders — "
            "edit the .eml before sending"
        )

    # --- don't spam the same employer ---------------------------------------
    if job["company"]:
        prior = con.execute(
            "SELECT COUNT(*) FROM applications a JOIN jobs j ON j.id=a.job_id"
            " WHERE a.sent_at IS NOT NULL AND lower(j.company)=lower(?)"
            " AND a.sent_at > datetime('now','-30 days') AND j.id != ?",
            (job["company"], job_id),
        ).fetchone()[0]
        if prior:
            reasons.append(f"already applied to {job['company']} in the last 30 days")

    # --- throughput ---------------------------------------------------------
    n = sends_today(con)
    if n >= DAILY_SEND_LIMIT:
        reasons.append(f"daily send limit reached ({n}/{DAILY_SEND_LIMIT})")
    ctx["sends_today"] = n

    # --- transport ----------------------------------------------------------
    if SMTP_AUTH and not (SMTP_USER and SMTP_PASS):
        reasons.append("SMTP_USER/SMTP_PASS not configured")

    return reasons, ctx
