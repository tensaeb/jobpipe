"""
matcher.py — deterministic job/CV matching against profile.yaml.

Design rules (from plan-review.md):
  1. No LLM. Scoring is reproducible and auditable.
  2. Three buckets, not a 0-100 leaderboard. The number is a sort key, not a measurement.
  3. Noisy extracted fields (salary, "mandatory" skills, years) DEMOTE and FLAG.
     They never hard-reject. The only hard stops are provably-absent skills
     and an unresolved blocking data conflict.
  4. Skill matches are weighted by EVIDENCE TIER. A job that matches only
     skills from the CV's competencies list is a shallow match, however many
     keywords line up. This is the correction that collapses the original
     plan's inflated frontend/AI scores.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

import yaml

# Evidence tier -> credit given for a matched required skill.
TIER_WEIGHT = {
    "named": 1.00,     # named in an employment bullet
    "inferred": 0.70,  # role scope implies it, technology unnamed
    "project": 0.60,   # portfolio project only
    "listed": 0.35,    # competencies list only — unevidenced claim
}

# Score composition.
W_REQUIRED = 0.70
W_PREFERRED = 0.15
W_EXPERIENCE = 0.15

BUCKET_APPLY = 65
BUCKET_REVIEW = 40

# Map the many ways a posting writes a skill onto profile.yaml's keys.
ALIASES = {
    "react.js": "React", "reactjs": "React", "react 18": "React",
    "next": "Next.js", "nextjs": "Next.js", "next js": "Next.js",
    "node": "Node.js", "nodejs": "Node.js", "node js": "Node.js",
    "express.js": "Express", "expressjs": "Express",
    "nest": "NestJS", "nest.js": "NestJS", "nestjs": "NestJS",
    "ts": "TypeScript", "js": "JavaScript", "es6": "JavaScript",
    "react-native": "React Native", "rn": "React Native",
    "tailwind": "Tailwind CSS", "tailwindcss": "Tailwind CSS",
    "redux": "Redux Toolkit", "rtk": "Redux Toolkit",
    "postgres": "PostgreSQL", "psql": "PostgreSQL",
    "mongo": "MongoDB", "mongo db": "MongoDB",
    "rest": "REST APIs", "rest api": "REST APIs", "restful apis": "REST APIs",
    "api development": "REST APIs", "apis": "REST APIs",
    "graph ql": "GraphQL",
    "sklearn": "Scikit-learn", "scikit learn": "Scikit-learn",
    "tf": "TensorFlow", "tensor flow": "TensorFlow",
    "gcp": "Google Cloud", "google cloud platform": "Google Cloud",
    "amazon web services": "AWS",
    "ci cd": "CI/CD", "cicd": "CI/CD", "ci/cd pipelines": "CI/CD",
    "mern": "MERN",  # expanded below
    "ui ux": "UI/UX", "ux/ui": "UI/UX", "ui/ux design": "UI/UX",
    "auth": "RBAC / auth", "authentication": "RBAC / auth", "rbac": "RBAC / auth",
    "testing": "Unit testing", "unit tests": "Unit testing",
    "agile": "Agile / sprint planning", "scrum": "Agile / sprint planning",
    "k8s": "Kubernetes", "vue.js": "Vue", "vuejs": "Vue",
    "dot net": ".NET", "dotnet": ".NET", "c#": ".NET",
}

# Stack shorthands that expand to several skills.
EXPANSIONS = {
    "MERN": ["MongoDB", "Express", "React", "Node.js"],
    "MEAN": ["MongoDB", "Express", "Angular", "Node.js"],
}


def normalize(skill: str) -> str:
    s = unicodedata.normalize("NFKD", skill).strip().lower()
    s = re.sub(r"[\u2010-\u2015]", "-", s)
    s = re.sub(r"\s+", " ", s)
    return ALIASES.get(s, skill.strip())


def expand(skills: Iterable[str]) -> list[str]:
    out: list[str] = []
    for s in skills:
        n = normalize(s)
        out.extend(EXPANSIONS.get(n, [n]))
    # de-dupe, preserve order
    seen, uniq = set(), []
    for s in out:
        k = s.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    return uniq


@dataclass
class Job:
    id: str
    title: str
    company: str | None = None
    required: list[str] = field(default_factory=list)
    preferred: list[str] = field(default_factory=list)
    min_years: float | None = None
    work_mode: str | None = "Remote"
    salary_raw: str | None = None
    salary_confidence: str = "low"   # low|medium|high — only 'high' may gate
    application_email: str | None = None
    notes: str = ""


@dataclass
class Match:
    job: Job
    score: int
    bucket: str
    required_hits: list[tuple[str, str, float]]
    required_misses: list[str]
    blockers: list[str]
    flags: list[str]
    evidence_depth: float   # share of required-skill credit from real evidence


class Matcher:
    def __init__(self, profile_path: str = "profile.yaml"):
        self.p = yaml.safe_load(open(profile_path))
        self.skills = {k.lower(): v for k, v in self.p["skills"].items()}
        self.skill_names = {k.lower(): k for k in self.p["skills"]}
        self.absent = {a.lower() for a in self.p["absent"]}
        self.years = self.p["experience"]["total_months_employed"] / 12.0
        self.blocking_conflicts = [
            cid for cid, c in (self.p.get("conflicts") or {}).items()
            if c.get("action") == "BLOCK_SENDING"
        ]

    def _lookup(self, skill: str) -> tuple[str | None, float]:
        """Return (tier, weight) for a job skill against the fact bank."""
        k = skill.lower()
        if k in self.skills:
            return self.skills[k]["tier"], TIER_WEIGHT[self.skills[k]["tier"]]
        if k in self.absent:
            return "absent", 0.0
        return None, 0.0

    def match(self, job: Job) -> Match:
        req = expand(job.required)
        pref = expand(job.preferred)

        hits: list[tuple[str, str, float]] = []
        misses: list[str] = []
        blockers: list[str] = []
        flags: list[str] = []

        credit = 0.0
        real_credit = 0.0  # credit from named/inferred/project only
        for s in req:
            tier, w = self._lookup(s)
            if tier == "absent":
                blockers.append(f"{s}: explicitly not in CV")
                misses.append(s)
            elif tier is None:
                misses.append(s)
            else:
                hits.append((s, tier, w))
                credit += w
                if tier != "listed":
                    real_credit += w

        req_cov = credit / len(req) if req else 0.0
        evidence_depth = (real_credit / credit) if credit else 0.0

        pref_credit = sum(self._lookup(s)[1] for s in pref)
        pref_cov = pref_credit / len(pref) if pref else 0.0

        # Experience: full credit at or above the ask, graceful decay below.
        if job.min_years is None:
            exp_fit = 0.8
        elif self.years >= job.min_years:
            exp_fit = 1.0
        else:
            exp_fit = max(0.0, 1 - (job.min_years - self.years) / max(job.min_years, 1) * 1.5)
            if self.years < job.min_years * 0.6:
                flags.append(
                    f"EXPERIENCE_SHORTFALL: asks {job.min_years}y, CV supports {self.years:.1f}y"
                )

        score = 100 * (W_REQUIRED * req_cov + W_PREFERRED * pref_cov + W_EXPERIENCE * exp_fit)
        score_damping = 1.0

        # --- Flags: demote and warn, never silently delete -------------------
        if len(req) < 4:
            # Short requirement lists make coverage cheap: matching 3 of 3
            # looks identical to matching 8 of 8 but carries far less signal.
            flags.append(
                f"LOW_INFORMATION: only {len(req)} requirements extracted — "
                "score is weakly determined, read the post"
            )
            score_damping = 0.88
        else:
            score_damping = 1.0
        if credit and evidence_depth < 0.45:
            flags.append(
                "SHALLOW_EVIDENCE: match rests on competencies-list skills with "
                "no narrative backing — expect a technical screen to probe it"
            )
            score *= 0.85
        if job.salary_raw and job.salary_confidence != "high":
            flags.append(f"SALARY_UNVERIFIED: {job.salary_raw!r} — do not gate on this")
        if not job.company:
            flags.append("UNKNOWN_EMPLOYER: verify before sending")
        if job.application_email and re.search(
            r"@(gmail|yahoo|outlook|hotmail|proton)\.", job.application_email, re.I
        ):
            flags.append("PERSONAL_EMAIL_DOMAIN: employer unverified")
        if self.p["candidate"].get("github") is None:
            flags.append("PROFILE_GAP: no GitHub URL on CV — channel often requires it")
        for cid in self.blocking_conflicts:
            flags.append(f"BLOCKED: unresolved conflict {cid} — resolve before sending")

        score *= score_damping

        # --- Bucket ----------------------------------------------------------
        if blockers:
            bucket = "SKIP"
        elif score >= BUCKET_APPLY:
            bucket = "APPLY"
        elif score >= BUCKET_REVIEW:
            bucket = "REVIEW"
        else:
            bucket = "SKIP"

        # A blocking data conflict can never produce an auto-send.
        if self.blocking_conflicts and bucket == "APPLY":
            bucket = "APPLY (HOLD)"

        return Match(job, round(score), bucket, hits, misses, blockers, flags, evidence_depth)


def cv_variant(match: Match) -> str:
    """Pick one of the static CV variants deterministically. No LLM.

    Looks at BOTH title and required skills: a post titled "Senior Software
    Engineer" asking for TensorFlow/Pandas is an ML role regardless of title.
    """
    t = (match.job.title or "").lower()
    req = {s.lower() for s, _, _ in match.required_hits} | {s.lower() for s in match.required_misses}

    ml = {"tensorflow", "scikit-learn", "pandas", "numpy", "opencv", "pytorch",
          "machine learning", "streamlit", "jupyter", "llm", "langchain", "rag"}
    mobile = {"react native", "flutter", "swift", "kotlin", "dart", "mobile development"}
    backend = {"node.js", "express", "nestjs", "django", "fastapi", "rest apis",
               "postgresql", "mongodb", "mysql", "graphql", "redis", "celery",
               "backend development", "database design"}
    frontend = {"react", "next.js", "tailwind css", "redux toolkit", "typescript",
                "vue", "svelte", "angular", "ui/ux", "frontend development"}

    # Title is a strong signal when it is explicit.
    if any(k in t for k in ("ml engineer", "machine learning", "ai engineer", "data scien")):
        return "cv_ai_ml.docx"
    if any(k in t for k in ("mobile", "react native", "android", "ios", "flutter")):
        return "cv_mobile.docx"
    if "full stack" in t or "full-stack" in t or "fullstack" in t:
        return "cv_fullstack.docx"

    # Otherwise let the required skills decide.
    scores = {
        "cv_ai_ml.docx": len(req & ml) * 1.5,   # ML keywords are highly specific
        "cv_mobile.docx": len(req & mobile) * 1.5,
        "cv_backend.docx": len(req & backend),
        "cv_frontend.docx": len(req & frontend),
    }
    best = max(scores, key=lambda k: scores[k])
    if scores[best] == 0:
        # Fall back to a weaker title hint, then full-stack.
        if any(k in t for k in ("frontend", "front-end", "front end", "ui")):
            return "cv_frontend.docx"
        if any(k in t for k in ("backend", "back-end", "back end", "api")):
            return "cv_backend.docx"
        return "cv_fullstack.docx"
    # A role spanning both halves is full-stack (e.g. MERN).
    b, f = scores["cv_backend.docx"], scores["cv_frontend.docx"]
    if b > 0 and f > 0 and (b + f) >= 4:
        return "cv_fullstack.docx"
    return best
