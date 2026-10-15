"""jobpipe — personal job-application pipeline.

Telegram discovery → dedup → extract → score → alert → you approve → .eml → you send.
Human-in-the-loop by default: nothing is ever sent automatically.
"""

__all__ = [
    "cli",
    "config",
    "db",
    "dedup",
    "extract",
    "llm",
    "matcher",
    "models",
    "notify",
    "pipeline",
    "policy",
    "score",
    "sender",
    "utils",
    "watcher",
    "agent",
    "compose",
]

__version__ = "1.0.0"

