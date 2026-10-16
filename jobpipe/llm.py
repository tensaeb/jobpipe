"""Pluggable LLM access — Google Gemini via the Interactions API.

Uses the google-genai SDK with `client.interactions.create()`.
Default model: gemini-3.8-flash. Falls back to regex-only if the provider
is unreachable. Tracks rate limit usage and notifies via Telegram when
approaching thresholds.

IMPORTANT: Only the Gemini provider is supported. OpenAI-compatible endpoints
(ollama, groq, openrouter, …) are no longer served by this module. The
Interactions API is experimental (Beta) — see Google AI Studio docs.

The system prompt is passed as the top-level `system_instruction` parameter
(never folded into untrusted job text). Temperature and max_output_tokens are
passed via `generation_config`. JSON mode uses `response_mime_type` +
`response_format` as top-level parameters (never `extra_body`, which the
SDK may silently drop on retry).
"""
from __future__ import annotations

import json
import logging
import re as _re
import time
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from .config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_ENABLED,
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_RATE_LIMIT_THRESHOLD,
    LLM_RATE_LIMIT_WINDOW,
    LLM_MAX_RETRIES,
    LLM_BACKOFF_BASE,
)
from .db import now

log = logging.getLogger(__name__)
_client = None
_probe_result: bool | None = None
_probe_time: float | None = None
_PROBE_TTL_SECONDS = 300  # re-probe every 5 minutes

# --- rate limit tracking ---------------------------------------------------
# Module-level counters shared across all calls in this process.
# NOTE: in-process counters are empty in a separate CLI process; the header
# values below are best-effort (the SDK may not expose them).
_rl_remaining: int | None = None   # requests remaining in current window
_rl_limit: int | None = None       # total requests allowed per window
_rl_reset_at: float | None = None  # unix timestamp when window resets
_rl_notified_at: float | None = None  # last time we sent a threshold alert


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class AssistantReply:
    """One model step, normalised across providers."""
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


def _get_client():
    global _client
    if _client is None:
        from google import genai

        http_options = {}
        if LLM_BASE_URL:
            http_options["base_url"] = LLM_BASE_URL
        _client = genai.Client(api_key=LLM_API_KEY or None,
                               http_options=http_options or None)
    return _client


def _notify_rate_limit(msg: str) -> None:
    """Send a Telegram alert about rate limit status."""
    global _rl_notified_at
    now_ts = time.time()
    # Throttle notifications: max once per LLM_RATE_LIMIT_WINDOW seconds.
    if _rl_notified_at is not None and now_ts - _rl_notified_at < LLM_RATE_LIMIT_WINDOW:
        return
    _rl_notified_at = now_ts
    try:
        from . import notify
        notify.send(f"⚠ {msg}")
    except Exception:  # noqa: BLE001
        log.debug("could not send rate-limit Telegram alert")


def _record_rate_limit(remaining: int | None, limit: int | None, reset_at: float | None) -> None:
    """Store rate limit info from the last response and alert if nearing threshold."""
    global _rl_remaining, _rl_limit, _rl_reset_at
    _rl_remaining = remaining
    _rl_limit = limit
    _rl_reset_at = reset_at

    if remaining is not None and limit is not None and limit > 0:
        used_pct = (limit - remaining) / limit * 100
        if used_pct >= LLM_RATE_LIMIT_THRESHOLD * 100:
            _notify_rate_limit(
                f"LLM rate limit {used_pct:.0f}% used "
                f"({remaining}/{limit} remaining, resets at {time.ctime(reset_at) if reset_at else '?'})"
            )


def _extract_rate_limit_headers(response) -> tuple[int | None, int | None, float | None]:
    """Best-effort extraction of rate limit info from a google-genai response.

    The google-genai SDK does not always expose HTTP headers directly, so we
    try several attribute paths and fall back to None.
    """
    remaining = None
    limit = None
    reset_at = None

    # Try common attribute locations.
    for attr in ("response", "_response", "http_response", "raw_response"):
        resp = getattr(response, attr, None)
        if resp is None:
            continue
        headers = getattr(resp, "headers", None) or getattr(resp, "header", None)
        if headers:
            remaining = headers.get("x-ratelimit-remaining") or headers.get("X-RateLimit-Remaining")
            limit = headers.get("x-ratelimit-limit") or headers.get("X-RateLimit-Limit")
            reset = headers.get("x-ratelimit-reset") or headers.get("X-RateLimit-Reset")
            if reset is not None:
                try:
                    reset_at = float(reset)
                except (ValueError, TypeError):
                    reset_at = None
            break

    if remaining is not None:
        try:
            remaining = int(remaining)
        except (ValueError, TypeError):
            remaining = None
    if limit is not None:
        try:
            limit = int(limit)
        except (ValueError, TypeError):
            limit = None

    return remaining, limit, reset_at


def _create(messages, *, tools=None, temperature=0, max_tokens=None, json_mode=False):
    """Call the Gemini Interactions API with rate limit handling.

    `system_instruction` is passed as a top-level parameter (not folded into
    the input text). `temperature` and `max_output_tokens` go through
    `generation_config`. JSON mode uses `response_mime_type` as a top-level
    parameter.

    Retries with exponential backoff on 429 (rate limited) responses.
    Notifies via Telegram when remaining requests drop below the configured
    threshold.
    """
    from google.genai import types

    # Split system instruction from the rest so it is never mixed with
    # untrusted user/job text.
    system_instruction = None
    parts = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            system_instruction = content
        else:
            parts.append(content)
    input_text = "\n\n".join(parts)

    # Generation config.
    gc_kwargs = {}
    if temperature is not None:
        gc_kwargs["temperature"] = temperature
    if max_tokens is not None:
        gc_kwargs["max_output_tokens"] = max_tokens
    generation_config = types.GenerationConfig(**gc_kwargs) if gc_kwargs else None

    kwargs: dict = {
        "model": LLM_MODEL,
        "input": input_text,
    }
    if system_instruction:
        kwargs["system_instruction"] = system_instruction
    if generation_config:
        kwargs["generation_config"] = generation_config
    if tools:
        kwargs["tools"] = tools
    if json_mode:
        kwargs["response_mime_type"] = "application/json"

    last_exc = None
    for attempt in range(LLM_MAX_RETRIES):
        try:
            response = _get_client().interactions.create(**kwargs)
            # Record rate limit info if available.
            remaining, limit, reset_at = _extract_rate_limit_headers(response)
            _record_rate_limit(remaining, limit, reset_at)
            return response
        except Exception as e:  # noqa: BLE001
            last_exc = e
            # Check for 429 rate limited.
            status = getattr(e, "status_code", None) or getattr(e, "code", None)
            if status == 429:
                # Try to extract retry-after from error message or headers.
                retry_after = None
                msg = str(e)
                m = _re.search(r"retry.?after.?[:\s]+(\d+)", msg, _re.I)
                if m:
                    retry_after = int(m.group(1))
                if retry_after is None and hasattr(e, "response"):
                    headers = getattr(e.response, "headers", None) or {}
                    retry_after = headers.get("retry-after") or headers.get("Retry-After")
                    if retry_after is not None:
                        try:
                            retry_after = float(retry_after)
                        except (ValueError, TypeError):
                            retry_after = None

                backoff = retry_after if retry_after is not None else LLM_BACKOFF_BASE * (2 ** attempt)
                log.warning(
                    "LLM rate limited (429), retrying in %.0fs (attempt %d/%d)",
                    backoff, attempt + 1, LLM_MAX_RETRIES,
                )
                _notify_rate_limit(f"LLM rate limited (429) — backing off {backoff:.0f}s")
                time.sleep(backoff)
                continue
            # Non-429 error: don't retry, just raise.
            raise
    # Exhausted retries.
    raise last_exc


def _robust_create(messages, **kw):
    """Create with one controlled fallback.

    If JSON mode (response_mime_type) is rejected by the provider, retry as
    plain text instead of silently dropping structured output. Other optional
    parameters are not silently dropped — a failure propagates.
    """
    try:
        return _create(messages, **kw)
    except Exception as e:  # noqa: BLE001
        if kw.get("response_mime_type") == "application/json":
            log.warning(
                "JSON mode rejected (%s); retrying as plain text — structured "
                "output may be lost", e,
            )
            kw2 = {k: v for k, v in kw.items() if k != "response_mime_type"}
            return _create(messages, **kw2)
        raise


def available() -> bool:
    """True if an LLM is reachable. Cached with a 5-minute TTL.

    Uses `count_tokens` (a cheap metadata call) instead of a full generation
    to avoid spending quota on every probe. Falls back to a minimal
    generation if the SDK does not expose `count_tokens`.
    """
    global _probe_result, _probe_time
    if LLM_ENABLED == "off":
        return False
    now_ts = time.time()
    if _probe_result is not None and _probe_time is not None \
            and now_ts - _probe_time < _PROBE_TTL_SECONDS:
        return _probe_result
    try:
        client = _get_client()
        from google.genai import types
        try:
            # Cheap metadata call: count tokens, no generation.
            client.models.count_tokens(model=LLM_MODEL, contents="ping")
        except (AttributeError, TypeError):
            # count_tokens not available; fall back to a 1-token generation.
            client.interactions.create(
                model=LLM_MODEL,
                input="ping",
                generation_config=types.GenerationConfig(max_output_tokens=1),
            )
        _probe_result = True
        _probe_time = now_ts
    except Exception as e:  # noqa: BLE001
        if LLM_ENABLED == "on":
            log.warning("LLM_ENABLED=on but provider unreachable: %s", e)
        _probe_result = False
        _probe_time = now_ts
    return _probe_result


def complete_json(system: str, user: str, schema: type[BaseModel]) -> BaseModel | None:
    """Return a validated model instance, or None on any failure."""
    if not available():
        return None
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    try:
        r = _robust_create(messages, temperature=0, json_mode=True)
        raw = r.output_text or "{}"
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]
        return schema.model_validate(json.loads(raw))
    except (ValidationError, json.JSONDecodeError) as e:
        log.warning("LLM output failed validation: %s", e)
    except Exception as e:  # noqa: BLE001
        log.warning("LLM call failed: %s", e)
    return None


def complete_text(system: str, user: str, max_tokens: int = 500) -> str | None:
    if not available():
        return None
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    try:
        r = _robust_create(messages, temperature=0.3, max_tokens=max_tokens)
        return (r.output_text or "").strip()
    except Exception as e:  # noqa: BLE001
        log.warning("LLM call failed: %s", e)
        return None


def complete_tools(messages: list[dict], tools: list[dict],
                   temperature: float = 0.2) -> AssistantReply | None:
    """One agent step — DISABLED pending port to Interactions tool format.

    Tool definitions are still in OpenAI format, but the Interactions API
    expects `types.Tool` with `function_declarations`, and multi-turn
    tool/result messages are not yet supported. Return None so callers
    degrade gracefully instead of behaving unpredictably.
    """
    log.warning("agent disabled: complete_tools not ported to Interactions API")
    return None


def test_provider() -> dict:
    """Connectivity check for `jobpipe llm-test`."""
    out = {"provider": LLM_PROVIDER, "base_url": LLM_BASE_URL, "model": LLM_MODEL,
           "api_key": "set" if LLM_API_KEY not in ("", "ollama") else "not set (local ok)",
           "ok": False}
    t0 = time.time()
    try:
        r = _create([{"role": "user", "content": "Reply with exactly: pong"}],
                    max_tokens=20, temperature=0)
        out["reply"] = (r.output_text or "").strip()[:80]
        out["ok"] = True
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:300]
    out["latency_ms"] = int((time.time() - t0) * 1000)
    return out


def rate_limit_status() -> dict:
    """Return current rate limit info for `jobpipe stats` or CLI display."""
    return {
        "remaining": _rl_remaining,
        "limit": _rl_limit,
        "reset_at": _rl_reset_at,
        "reset_ctime": time.ctime(_rl_reset_at) if _rl_reset_at else None,
    }
