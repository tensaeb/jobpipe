"""Bridge from an extracted ParsedJob to the deterministic matcher."""
from __future__ import annotations

from .config import PROFILE
from .extract import injection_signals
from .matcher import Job, Match, Matcher, cv_variant
from .models import ParsedJob

_matcher: Matcher | None = None


def matcher() -> Matcher:
    global _matcher
    if _matcher is None:
        _matcher = Matcher(str(PROFILE))
    return _matcher


def score(parsed: ParsedJob, job_id: str = "?", raw_text: str = "") -> tuple[Match, str]:
    j = Job(
        id=job_id,
        title=parsed.title or "Unknown role",
        company=parsed.company,
        required=parsed.required_skills,
        preferred=parsed.preferred_skills,
        min_years=parsed.min_years,
        work_mode="Remote" if parsed.remote else None,
        salary_raw=parsed.salary_raw,
        salary_confidence="low",
        application_email=parsed.application_email,
    )
    m = matcher().match(j)

    # Channel-specific risk flags the matcher doesn't know about.
    if parsed.application_telegram and not parsed.application_email:
        m.flags.append("TELEGRAM_ONLY_CONTACT: no email — apply manually, never auto-DM")
    if "Intro video" in parsed.required_materials:
        m.flags.append("HIGH_EFFORT: intro video required")
    if "GitHub" in parsed.required_materials:
        m.flags.append("REQUIRES_GITHUB: post asks for a GitHub link")
    if parsed.urgency == "immediate":
        m.flags.append("URGENT: post says immediate/ASAP — apply today or skip")

    # --- adversarial content ------------------------------------------------
    # The LLM never obeys post content (no tools, schema-validated output).
    # The residual attack is ROUTING: inject an address so materials are
    # drafted to the attacker. So we quarantine rather than merely ignore.
    if raw_text:
        signals = injection_signals(raw_text)
        if signals:
            m.blockers.append(f"PROMPT_INJECTION: post contains {signals[0]!r}")
            m.flags.append("QUARANTINED: adversarial post — do not apply")
            m.bucket = "SKIP"
            m.score = 0

    if len(parsed.contact_emails) > 1:
        m.flags.append(
            "MULTIPLE_CONTACTS: " + ", ".join(parsed.contact_emails) +
            " — recipient NOT auto-filled, choose manually"
        )

    return m, cv_variant(m)
