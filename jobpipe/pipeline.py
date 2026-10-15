"""Orchestration: ingest -> dedup -> extract -> score -> alert -> approve.

Ordering matters. Dedup runs before extraction so repeated posts never reach
the LLM, and idempotency is enforced at two levels (source message id, then
normalized body) so the same job can never generate two applications.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import compose, notify
from .config import ALERT_MIN_SCORE
from .db import connect, jl, now, ul
from .dedup import body_hash, find_near_duplicate, metadata_fingerprint
from .extract import extract
from .score import score

log = logging.getLogger(__name__)


def ingest_post(con, source: str, body: str, source_msg_id=None, url=None, posted_at=None):
    """Store a raw post. Returns (post_id, status) where status is
    new | duplicate_exact | duplicate_near | seen."""
    body = (body or "").strip()
    if len(body) < 40:
        return None, "too_short"

    if source_msg_id is not None:
        row = con.execute(
            "SELECT id FROM posts WHERE source=? AND source_msg_id=?",
            (source, str(source_msg_id)),
        ).fetchone()
        if row:
            return row["id"], "seen"

    h = body_hash(body)
    row = con.execute("SELECT id FROM posts WHERE body_hash=?", (h,)).fetchone()
    exact_dupe = row["id"] if row else None

    near = None
    if exact_dupe is None:
        near = find_near_duplicate(con, body)

    dup_of, kind = None, None
    if exact_dupe is not None:
        dup_of, kind = exact_dupe, "duplicate_exact"
    elif near is not None:
        dup_of, kind = near[0], "duplicate_near"

    cur = con.execute(
        "INSERT INTO posts(source, source_msg_id, url, body, body_hash, posted_at,"
        " fetched_at, duplicate_of, dup_kind) VALUES(?,?,?,?,?,?,?,?,?)",
        (source, str(source_msg_id) if source_msg_id is not None else None,
         url, body, h, posted_at, now(), dup_of, kind),
    )
    con.commit()
    return cur.lastrowid, kind or "new"


def process_post(con, post_id: int, alert: bool = True) -> int | None:
    """Extract, score and store a job for one post. Returns job_id or None."""
    post = con.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if post is None:
        return None
    if con.execute("SELECT id FROM jobs WHERE post_id=?", (post_id,)).fetchone():
        return None  # idempotent

    parsed, method = extract(post["body"])

    # A post with no contact route and no title is channel chatter, not a job.
    if not parsed.title and not (parsed.application_email or parsed.application_telegram):
        return None

    match, cv = score(parsed, job_id=str(post_id), raw_text=post["body"])

    fp = metadata_fingerprint(parsed.company, parsed.title, parsed.location, parsed.application_url)
    dupe_of = None
    if fp:
        row = con.execute(
            "SELECT id FROM jobs WHERE fingerprint=? ORDER BY id LIMIT 1", (fp,)
        ).fetchone()
        if row:
            dupe_of = row["id"]
            match.flags.append(f"DUPLICATE_OF_JOB_{dupe_of}")

    quarantined = any(f.startswith("QUARANTINED") for f in match.flags)
    ambiguous = any(f.startswith("MULTIPLE_CONTACTS") for f in match.flags)
    # Never store an auto-resolved recipient we cannot trust.
    recipient = None if (quarantined or ambiguous) else parsed.application_email

    if quarantined:
        status = "QUARANTINED"
    elif dupe_of:
        status = "DUPLICATE"
    elif match.bucket == "SKIP":
        status = "SKIPPED"
    else:
        status = "NEW"

    cur = con.execute(
        """INSERT INTO jobs(post_id,title,company,location,remote,employment_type,min_years,
            required_json,preferred_json,salary_raw,salary_confidence,application_email,
            application_telegram,application_url,deadline_text,urgency,materials_json,
            extracted_by,score,bucket,evidence_depth,flags_json,blockers_json,cv_variant,
            fingerprint,duplicate_of,status,subject_instruction,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (post_id, parsed.title, parsed.company, parsed.location,
         int(parsed.remote) if parsed.remote is not None else None,
         parsed.employment_type, parsed.min_years,
         jl(parsed.required_skills), jl(parsed.preferred_skills),
         parsed.salary_raw, "low", recipient,
         parsed.application_telegram, parsed.application_url,
         parsed.deadline_text, parsed.urgency, jl(parsed.required_materials),
         method, match.score, match.bucket, round(match.evidence_depth, 3),
         jl(match.flags), jl(match.blockers), cv, fp, dupe_of, status,
         parsed.subject_instruction, now()),
    )
    con.commit()
    job_id = cur.lastrowid

    if alert and not dupe_of and match.score >= ALERT_MIN_SCORE:
        notify.send_with_buttons(job_id, notify.format_alert(
            job_id, match, parsed, cv, post["url"], include_commands=False
        ))

    return job_id


def process_pending(con, alert: bool = True) -> list[int]:
    rows = con.execute(
        "SELECT p.id FROM posts p LEFT JOIN jobs j ON j.post_id = p.id"
        " WHERE j.id IS NULL AND p.duplicate_of IS NULL ORDER BY p.id"
    ).fetchall()
    out = []
    for r in rows:
        jid = process_post(con, r["id"], alert=alert)
        if jid:
            out.append(jid)
    return out


def _rebuild_match(con, job_id: int):
    """Reconstruct a Match from the stored row (no re-extraction)."""
    from .matcher import Job as MJob, Match as MMatch

    r = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if r is None:
        raise KeyError(job_id)
    mj = MJob(
        id=str(job_id), title=r["title"] or "Software Engineer", company=r["company"],
        required=ul(r["required_json"]), preferred=ul(r["preferred_json"]),
        min_years=r["min_years"], salary_raw=r["salary_raw"],
        application_email=r["application_email"],
    )
    from .score import matcher

    m = matcher().match(mj)
    return r, MMatch(mj, r["score"], r["bucket"], m.required_hits, m.required_misses,
                     ul(r["blockers_json"]), ul(r["flags_json"]), r["evidence_depth"] or 0.0)


def record_decision(con, job_id: int, action: str, reason: str | None = None,
                    actor: str = "user"):
    r = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    features = {
        "required": ul(r["required_json"]),
        "evidence_depth": r["evidence_depth"],
        "min_years": r["min_years"],
        "remote": r["remote"],
        "urgency": r["urgency"],
        "materials": ul(r["materials_json"]),
        "has_email": bool(r["application_email"]),
        "extracted_by": r["extracted_by"],
    }
    con.execute(
        "INSERT INTO decisions(job_id,action,reason,score,bucket,features,decided_at,actor)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (job_id, action, reason, r["score"], r["bucket"], jl(features), now(), actor),
    )
    con.commit()


def approve(con, job_id: int, actor: str = "user") -> dict:
    """Generate materials. Never sends. Returns a summary dict."""
    row, match = _rebuild_match(con, job_id)
    if row["status"] == "QUARANTINED":
        raise ValueError(
            f"job {job_id} is quarantined (adversarial post content). "
            "Refusing to generate materials."
        )
    post = con.execute("SELECT * FROM posts WHERE id=?", (row["post_id"],)).fetchone()

    # Re-extract subject_instruction: prefer the stored DB value (populated at
    # process_post time), fall back to a quick regex over the post body.
    subject_instr = row["subject_instruction"] if row["subject_instruction"] else None
    if subject_instr is None:
        from .extract import regex_extract as _re_extract
        _parsed_quick = _re_extract(post["body"])
        subject_instr = _parsed_quick.subject_instruction

    hard_blocks = [f for f in ul(row["flags_json"]) if f.startswith("BLOCKED:")]
    body = compose.write_body(match, post["body"],
                              required_materials=ul(row["materials_json"]))
    subject = compose.subject_for(match, subject_instr)
    eml = compose.build_eml(job_id, match, body, row["application_email"],
                            row["cv_variant"], subject_instruction=subject_instr)

    due = (datetime.now(timezone.utc) + timedelta(days=5)).date().isoformat()
    con.execute(
        "INSERT INTO applications(job_id,cv_variant,email_subject,email_body,eml_path,"
        "generated_at,follow_up_due) VALUES(?,?,?,?,?,?,?)",
        (job_id, row["cv_variant"], subject, body, str(eml), now(), due),
    )
    con.execute("UPDATE jobs SET status='MATERIALS_READY' WHERE id=?", (job_id,))
    con.commit()
    record_decision(con, job_id, "APPROVE", actor=actor)

    return {
        "job_id": job_id, "eml": str(eml), "subject": subject,
        "to": row["application_email"], "cv": row["cv_variant"],
        "blocked": hard_blocks, "body": body,
    }


def reject(con, job_id: int, reason: str | None = None, actor: str = "user"):
    con.execute("UPDATE jobs SET status='REJECTED' WHERE id=?", (job_id,))
    con.commit()
    record_decision(con, job_id, "REJECT", reason, actor=actor)


def update_job_score(con, job_id: int, match, cv: str):
    """Persist a re-scored match back to the jobs row.

    Called by the edited-message handler after detecting a score change ≥5,
    so that a subsequent approve() call builds materials from current data.
    """
    con.execute(
        "UPDATE jobs SET score=?, bucket=?, flags_json=?, cv_variant=? WHERE id=?",
        (match.score, match.bucket, jl(match.flags), cv, job_id),
    )
    con.commit()


def mark_sent(con, job_id: int):
    con.execute("UPDATE applications SET sent_at=? WHERE job_id=?", (now(), job_id))
    con.execute("UPDATE jobs SET status='SENT' WHERE id=?", (job_id,))
    con.commit()
