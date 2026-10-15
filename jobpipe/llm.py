"""Pluggable LLM access — the entire abstraction.

Every provider in config.LLM_PROVIDERS (Ollama, Google Gemini, Groq,
OpenRouter, Cerebras, Mistral, OpenAI, DeepSeek, custom) exposes an
OpenAI-compatible chat-completions endpoint, so one client covers local and
hosted. Swap providers with a single env var: LLM_PROVIDER.
The value here is the *validation*, not the indirection.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from .config import LLM_API_KEY, LLM_BASE_URL, LLM_ENABLED, LLM_MODEL, LLM_PROVIDER

log = logging.getLogger(__name__)
_client = None
_probe_result: bool | None = None
_probe_time: float | None = None
_PROBE_TTL_SECONDS = 300  # re-probe every 5 minutes so a cold-start blip
                           # doesn't disable the LLM for the life of the process.


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
        from openai import OpenAI

        _client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, timeout=120.0)
    return _client


def _create(messages, *, tools=None, temperature=0, max_tokens=None, json_mode=False):
    kwargs: dict = {"model": LLM_MODEL, "messages": messages, "temperature": temperature}
    if tools:
        kwargs["tools"] = tools
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    return _get_client().chat.completions.create(**kwargs)


def _robust_create(messages, **kw):
    """Providers differ on optional params (response_format, max_tokens).
    Retry once without them rather than lose the call.

    Tracks which optional arguments were actually emitted to the provider so
    the retry drops exactly those (e.g. json_mode maps to response_format).
    """
    emitted = set(kw.keys())
    try:
        return _create(messages, **kw)
    except Exception as e:  # noqa: BLE001
        # Determine which optional params were actually sent.
        sent_optional = emitted & {"response_format", "max_tokens", "json_mode"}
        if not sent_optional:
            raise
        # Build a clean kwargs without the optional params that may be
        # rejected by this provider.
        retry_kw = {k: v for k, v in kw.items() if k not in sent_optional}
        log.debug(
            "strict call failed (%s); retrying without optional params %s",
            e, sorted(sent_optional),
        )
        return _create(messages, **retry_kw)


def available() -> bool:
    """True if an LLM is reachable. Cached with a 5-minute TTL so a cold-start
    blip doesn't disable the LLM for the life of a 24/7 watcher."""
    global _probe_result, _probe_time
    if LLM_ENABLED == "off":
        return False
    now_ts = time.time()
    if _probe_result is not None and _probe_time is not None \
            and now_ts - _probe_time < _PROBE_TTL_SECONDS:
        return _probe_result
    try:
        model_list = _get_client().models.list()
        # Gemini's OpenAI-compatible /models returns ids with a "models/"
        # prefix (e.g. "models/gemini-2.0-flash"). Strip it for comparison.
        ids = [m.id for m in (model_list.data if hasattr(model_list, "data")
                               else list(model_list))]
        normalized = {i.split("/", 1)[-1] for i in ids}
        if ids and LLM_MODEL not in normalized:
            log.warning(
                "LLM model %r not found in provider's model list "
                "(available: %s ...). Falling back to regex-only.",
                LLM_MODEL,
                ", ".join(ids[:5]),
            )
            _probe_result = False
            _probe_time = now_ts
            return False
        _probe_result = True
        _probe_time = now_ts
    except Exception as first:  # noqa: BLE001  # some gateways omit /models
        log.debug("models.list probe failed (%s); trying a 1-token chat", first)
        try:
            _create([{"role": "user", "content": "ping"}], max_tokens=1, temperature=0)
            _probe_result = True
            _probe_time = now_ts
        except Exception as e:  # noqa: BLE001
            if LLM_ENABLED == "on":
                log.warning("LLM_ENABLED=on but provider unreachable: %s", e)
            _probe_result = False
            _probe_time = now_ts
    return _probe_result


def complete_json(system: str, user: str, schema: type[BaseModel]) -> BaseModel | None:
    """Return a validated model instance, or None on any failure.

    Never raises into the pipeline: a parse failure must downgrade a job to
    regex-only extraction, not lose it.
    """
    if not available():
        return None
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    try:
        r = _robust_create(messages, temperature=0, json_mode=True)
        raw = r.choices[0].message.content or "{}"
        # Models sometimes wrap JSON in prose or fences.
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
        return (r.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001
        log.warning("LLM call failed: %s", e)
        return None


def complete_tools(messages: list[dict], tools: list[dict],
                   temperature: float = 0.2) -> AssistantReply | None:
    """One agent step. Returns None when the LLM is off or unreachable, so
    the agent can degrade to a clear message instead of an exception."""
    if not available():
        return None
    try:
        r = _create(messages, tools=tools, temperature=temperature)
        msg = r.choices[0].message
    except Exception as e:  # noqa: BLE001
        log.warning("agent step failed: %s", e)
        return None
    calls: list[ToolCall] = []
    for tc in (getattr(msg, "tool_calls", None) or []):
        raw = tc.function.arguments or "{}"
        try:
            args = json.loads(raw)
        except json.JSONDecodeError:
            args = {"_raw": raw}
        calls.append(ToolCall(id=tc.id or f"call_{len(calls)}",
                              name=tc.function.name, arguments=args))
    return AssistantReply(content=msg.content, tool_calls=calls)


def test_provider() -> dict:
    """Connectivity check for `jobpipe llm-test`. Sends a tiny ping;
    never prints the key."""
    out = {"provider": LLM_PROVIDER, "base_url": LLM_BASE_URL, "model": LLM_MODEL,
           "api_key": "set" if LLM_API_KEY not in ("", "ollama") else "not set (local ok)",
           "ok": False}
    t0 = time.time()
    try:
        r = _create([{"role": "user", "content": "Reply with exactly: pong"}],
                    max_tokens=20, temperature=0)
        out["reply"] = (r.choices[0].message.content or "").strip()[:80]
        out["ok"] = True
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:300]
    out["latency_ms"] = int((time.time() - t0) * 1000)
    return out

