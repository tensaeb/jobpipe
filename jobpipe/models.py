"""Pydantic schemas. This is the real prompt-injection boundary: the LLM's
output is validated into a fixed shape and nothing downstream executes it."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ParsedJob(BaseModel):
    title: str | None = None
    company: str | None = None
    location: str | None = None
    remote: bool | None = None
    employment_type: Literal["Full-time", "Part-time", "Contract", "Freelance", "Internship"] | None = None
    min_years: float | None = None
    max_years: float | None = None
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    salary_raw: str | None = None
    application_email: str | None = None
    contact_emails: list[str] = Field(default_factory=list)
    application_telegram: str | None = None
    application_url: str | None = None
    deadline_text: str | None = None
    urgency: Literal["explicit_deadline", "immediate", "none"] = "none"
    required_materials: list[str] = Field(default_factory=list)

    @field_validator("required_skills", "preferred_skills", "required_materials",
                     "contact_emails", mode="before")
    @classmethod
    def _listify(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return [str(s).strip() for s in v if str(s).strip()]

    @field_validator("min_years", "max_years", mode="before")
    @classmethod
    def _num(cls, v):
        if v in (None, "", "null"):
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if 0 <= f <= 40 else None

    @field_validator("title", "company", "location", mode="before")
    @classmethod
    def _clean(cls, v):
        if not v:
            return None
        s = str(v).strip().strip("*_#").strip()
        # Reject obvious model echoes / injected instructions.
        if len(s) > 120 or not s:
            return None
        return s
