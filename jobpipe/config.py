"""Configuration. Everything overridable by env; nothing secret has a default."""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # optional
    pass

ROOT = Path(__file__).resolve().parent.parent

# --- storage ---------------------------------------------------------------
DB_PATH = Path(os.getenv("DB_PATH", ROOT / "data" / "jobpipe.db"))
OUTBOX = Path(os.getenv("OUTBOX", ROOT / "outbox"))
PROFILE = Path(os.getenv("PROFILE", ROOT / "profile.yaml"))
CV_DIR = Path(os.getenv("CV_DIR", ROOT / "cv"))

# --- telegram --------------------------------------------------------------
TG_API_ID = os.getenv("TELEGRAM_API_ID")
TG_API_HASH = os.getenv("TELEGRAM_API_HASH")
TG_CHANNELS = [c.strip() for c in os.getenv("TELEGRAM_CHANNELS", "tech_revolution_world").split(",") if c.strip()]
TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_ALERT_CHAT_ID = os.getenv("TELEGRAM_ALERT_CHAT_ID")

# --- llm (optional; pipeline degrades to regex-only without it) ------------
# Every preset below exposes an OpenAI-compatible endpoint, so one client
# covers local + hosted. LLM_PROVIDER picks a preset; LLM_BASE_URL / LLM_MODEL
# / LLM_API_KEY always win when set. API keys are read from the provider's
# own env var (e.g. GEMINI_API_KEY) unless LLM_API_KEY is set.
# All free tiers work here: gemini, groq, openrouter, cerebras, mistral,
# ollama (local). See `jobpipe llm-test`.
LLM_PROVIDERS: dict[str, dict] = {
    "ollama":     {"base_url": "http://localhost:11434/v1",
                   "model": "qwen3:8b", "key_env": []},
    "gemini":     {"base_url": "https://generativelanguage.googleapis.com/v1beta/",
                   "model": "gemini-3.8-flash",
                   "key_env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"]},
    "groq":       {"base_url": "https://api.groq.com/openai/v1",
                   "model": "llama-3.3-70b-versatile", "key_env": ["GROQ_API_KEY"]},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1",
                   "model": "nvidia/llama-3.1-nemotron-70b-instruct:free",
                   "key_env": ["OPENROUTER_API_KEY"]},
    "cerebras":   {"base_url": "https://api.cerebras.ai/v1",
                   "model": "qwen-3-32b", "key_env": ["CEREBRAS_API_KEY"]},
    "mistral":    {"base_url": "https://api.mistral.ai/v1",
                   "model": "mistral-small-latest", "key_env": ["MISTRAL_API_KEY"]},
    "openai":     {"base_url": "https://api.openai.com/v1",
                   "model": "gpt-4o-mini", "key_env": ["OPENAI_API_KEY"]},
    "deepseek":   {"base_url": "https://api.deepseek.com/v1",
                   "model": "deepseek-chat", "key_env": ["DEEPSEEK_API_KEY"]},
    "custom":     {"base_url": None, "model": None, "key_env": []},
}


def resolve_llm(environ: dict | None = None) -> dict:
    """Pure settings resolution: provider preset <- environment overrides.
    Takes a mapping so tests can exercise precedence without touching env."""
    env = os.environ if environ is None else environ
    provider = (env.get("LLM_PROVIDER") or "gemini").strip().lower()
    preset = LLM_PROVIDERS.get(provider, LLM_PROVIDERS["custom"])
    base_url = env.get("LLM_BASE_URL") or preset["base_url"] or "http://localhost:11434/v1"
    model = env.get("LLM_MODEL") or preset["model"] or "qwen3:8b"
    api_key = (env.get("LLM_API_KEY") or "").strip()
    if not api_key:
        api_key = next((env[e] for e in preset["key_env"] if env.get(e)), "")
    if not api_key:
        api_key = "ollama"  # placeholder; local endpoints don't validate it
    return {"provider": provider, "base_url": base_url, "model": model,
            "api_key": api_key, "key_env": list(preset["key_env"])}


_LLM = resolve_llm()
LLM_PROVIDER = _LLM["provider"]
LLM_BASE_URL = _LLM["base_url"]
LLM_MODEL = _LLM["model"]
LLM_API_KEY = _LLM["api_key"]
LLM_ENABLED = os.getenv("LLM_ENABLED", "auto").strip().lower()  # auto | on | off

# --- rate limit handling ------------------------------------------------------
LLM_RATE_LIMIT_THRESHOLD = float(os.getenv("LLM_RATE_LIMIT_THRESHOLD", "0.75"))  # notify at 75% used
LLM_RATE_LIMIT_WINDOW = int(os.getenv("LLM_RATE_LIMIT_WINDOW", "300"))  # seconds between notifications
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))  # max retries on 429
LLM_BACKOFF_BASE = float(os.getenv("LLM_BACKOFF_BASE", "2.0"))  # base backoff seconds

# --- agent ------------------------------------------------------------------
AGENT_MAX_TURNS = int(os.getenv("AGENT_MAX_TURNS", "12"))  # tool loop bound

# --- behaviour -------------------------------------------------------------
ALERT_MIN_SCORE = int(os.getenv("ALERT_MIN_SCORE", "40"))  # REVIEW and up
DEDUP_FUZZ = int(os.getenv("DEDUP_FUZZ", "82"))  # token_sort_ratio threshold (was 92 for token_set_ratio)
MAX_POST_AGE_DAYS = int(os.getenv("MAX_POST_AGE_DAYS", "21"))

# --- sending (auto-apply) --------------------------------------------------
def _b(name, default="false"):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


AUTO_APPLY = _b("AUTO_APPLY")                    # master switch, OFF by default
DRY_RUN = _b("DRY_RUN", "true")                  # simulate sends unless disabled
AUTO_APPLY_MIN_SCORE = int(os.getenv("AUTO_APPLY_MIN_SCORE", "70"))
DAILY_SEND_LIMIT = int(os.getenv("DAILY_SEND_LIMIT", "10"))
ALLOW_PERSONAL_EMAIL = _b("ALLOW_PERSONAL_EMAIL")
REQUIRE_CV_ATTACHMENT = _b("REQUIRE_CV_ATTACHMENT", "true")
BLOCKED_DOMAINS = [d.strip().lower() for d in os.getenv("BLOCKED_DOMAINS", "").split(",") if d.strip()]

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")               # Gmail App Password, not your login
SMTP_STARTTLS = _b("SMTP_STARTTLS", "true")      # off only for a local test server
SMTP_AUTH = _b("SMTP_AUTH", "true")

# Identity used in generated application emails.
SENDER_NAME = os.getenv("SENDER_NAME", "Your Name")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "")

for d in (DB_PATH.parent, OUTBOX):
    d.mkdir(parents=True, exist_ok=True)
