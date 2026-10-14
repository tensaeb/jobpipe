"""Bridge from an extracted ParsedJob to the deterministic matcher."""
from __future__ import annotations

import re

import yaml

from .config import PROFILE
from .extract import injection_signals
from .matcher import Job, Match, Matcher, cv_variant
from .models import ParsedJob

_matcher: Matcher | None = None

# Shift / overnight patterns that may conflict with Africa/Addis_Ababa (UTC+3).
_NIGHT_SHIFT = re.compile(
    r"\b(night\s*shift|graveyard|overnight|us\s*hours?|"
    r"(?:1[0-9]|2[0-3])\s*(?::|h)\s*\d{2}\s*(?:\u2013|-|to)\s*\d{1,2}"
    r"|[6-9]\s*(?:pm|PM)\s*(?:\u2013|-|to)\s*[0-9]\s*(?:am|AM))\b",
    re.I,
)

# Role titles clearly outside software-engineering domain.
_NON_ENG_ROLES = re.compile(
    r"\b(manual\s+qa|qa\s+tester|qa\s+engineer|quality\s+assurance\s+engineer|"
    r"data\s+entry|business\s+analyst|scrum\s+master|project\s+manager|"
    r"hr\s+(?:manager|officer)|sales\s+engineer|customer\s+success)\b",
    re.I,
)


def matcher() -> Matcher:
    global _matcher
    if _matcher is None:
        _matcher = Matcher(str(PROFILE))
    return _matcher


def preference_flags(parsed: ParsedJob, prefs: dict, raw_text: str) -> list[str]:
    """Return a list of preference-violation flag strings.

    Each flag represents a single preference mismatch; the caller applies a
    0.85 damping factor per flag. These are never hard blockers.
    """
    flags: list[str] = []

    # a. Work mode: on-site only, but profile prefers remote/hybrid.
    if parsed.remote is False:
        work_modes = prefs.get("work_modes") or []
        if "On-site" not in work_modes:
            flags.append("PREF_ONSITE_ONLY: post is on-site; preference is Remote/Hybrid")

    # b. Shift / timezone conflict.
    if _NIGHT_SHIFT.search(raw_text or ""):
        tz = prefs.get("timezone", "")
        if str(tz).startswith("Africa/"):
            flags.append(
                "PREF_TIMEZONE: shift hours appear incompatible with Africa/ timezone"
            )

    # c. Minimum salary check.
    minimum_salary = prefs.get("minimum_salary")
    if minimum_salary is not None and parsed.salary_raw is not None:
        m = re.search(r"[\d,]+", parsed.salary_raw)
        if m:
            try:
                offered = float(m.group(0).replace(",", ""))
                if offered < float(minimum_salary):
                    flags.append(
                        f"PREF_SALARY_LOW: {parsed.salary_raw!r} below minimum {minimum_salary}"
                    )
            except (ValueError, TypeError):
                pass

    # d. Employment type mismatch.
    if parsed.employment_type is not None:
        employment_types = prefs.get("employment_types") or []
        if employment_types and parsed.employment_type not in employment_types:
            flags.append(
                f"PREF_EMPLOYMENT_TYPE: {parsed.employment_type} not in preferred "
                f"{employment_types}"
            )

    # e. Non-engineering role filter.
    title_lower = (parsed.title or "").lower()
    if _NON_ENG_ROLES.search(title_lower):
        flags.append(
            f"PREF_ROLE_TYPE: '{parsed.title}' is outside target engineering domain"
        )

    return flags


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

    # --- preference scoring -------------------------------------------------
    # Each violation damps the score by 0.85; these are warnings, not blockers.
    prefs = yaml.safe_load(open(str(PROFILE))).get("preferences", {})
    pref_flag_list = preference_flags(parsed, prefs, raw_text)
    m.flags.extend(pref_flag_list)
    for _ in pref_flag_list:
        m.score = round(m.score * 0.85)

    return m, cv_variant(m)
