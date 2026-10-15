# jobpipe

**Personal job-application pipeline.** Discover jobs on Telegram → deduplicate → extract → score → alert → you approve → `.eml` → you press Send.

Designed for one person hunting software-engineering roles in a high-volume Telegram channel. **Human-in-the-loop by default: nothing is ever sent automatically.**

```
posts ──► dedup ──► extract ──► score ──► alert ──► YOU ──► .eml ──► you press Send
           (first,    (regex,   (vs        (phone)   approve
            no LLM)   LLM opt.)  profile)                         (never auto-sends)
```

## What this is, and what it isn't

| Is | Isn't |
|----|-------|
| A personal pipeline you run yourself | A mass-apply / auto-apply bot |
| Deterministic regex extraction first, LLM as optional upgrade | An LLM that decides what to apply to |
| You approve every `.eml` before anything leaves your machine | Autonomous sending without your consent |
| SQLite + local files; nothing leaves your machine unless you choose | A SaaS with your data on someone else's server |
| Telegram alerts for phone-actionable review | A dashboard or web UI |

**Core safety rule:** the only step with outside-world consequences is **send**, and that requires an explicit `APPROVE` decision + your `jobpipe send ID` (or `jobpipe approve ID --send --yes`). The agent, triage, and watcher never send email.

## Quick start

```bash
# 1. Create a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate          # macOS / Linux

# 2. Install dependencies
pip install -r requirements.txt    # core pipeline + agent + selftest
pip install -r requirements-optional.txt  # adds telethon (live Telegram watcher)

# 3. Configure
cp .env.example .env               # edit with your API keys, SMTP, etc.
cp profile.example.yaml profile.yaml   # your fact bank — never committed

# 4. Try it on sample data
python3 -m jobpipe ingest samples/posts.jsonl
python3 -m jobpipe process --no-alert
python3 -m jobpipe list
python3 -m jobpipe show 1 --raw
python3 -m jobpipe approve 1       # writes outbox/0001-*.eml — never sends
python3 -m jobpipe reject 2 --reason "angular only"
python3 -m jobpipe stats

# 5. Optional: test LLM connectivity
python3 -m jobpipe llm-test

# 6. Optional: start the live Telegram watcher
python3 -m jobpipe watch --backfill 50
```

## Virtualenv setup

Always run jobpipe inside a virtual environment:

```bash
# create once
python3 -m venv .venv

# activate every time you open a new terminal
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows

# install deps
pip install -r requirements.txt
pip install -r requirements-optional.txt   # for the live Telegram watcher

# verify
python3 -m jobpipe stats
```

**Python 3.10+** required. Your `.venv/` folder is gitignored. To leave the environment: `deactivate`.

**Using `uv` (faster alternative to pip):**

```bash
pip install uv
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
uv pip install -r requirements-optional.txt
```

## Configuration

Copy `.env.example` → `.env`. Everything is optional; the pipeline runs with no credentials and no LLM (regex-only fallback).

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `gemini` | `gemini`, `groq`, `openrouter`, `cerebras`, `mistral`, `ollama`, `openai`, `deepseek`, `custom` |
| `LLM_BASE_URL` | provider's | Override — any OpenAI-compatible endpoint |
| `LLM_MODEL` | provider's | Override — model name |
| `LLM_API_KEY` | provider's env var | e.g. `GEMINI_API_KEY`; local Ollama needs none |
| `LLM_ENABLED` | `auto` | `auto` (probe once, fall back to regex), `on`, `off` |
| `AGENT_MAX_TURNS` | `12` | Bound on the agent's tool loop |
| `ALERT_MIN_SCORE` | `40` | Minimum score for Telegram alerts |
| `DEDUP_FUZZ` | `92` | RapidFuzz threshold; lower = more aggressive dedup |
| `MAX_POST_AGE_DAYS` | `21` | Backfill age cutoff |
| `AUTO_APPLY` | `false` | Master switch for automatic sending (OFF by default) |
| `DRY_RUN` | `true` | Simulate SMTP sends unless disabled |
| `AUTO_APPLY_MIN_SCORE` | `70` | Score floor for auto-send |
| `DAILY_SEND_LIMIT` | `10` | Max sends per day |
| `ALLOW_PERSONAL_EMAIL` | `false` | Permit Gmail/Yahoo/etc. recipient domains |
| `REQUIRE_CV_ATTACHMENT` | `true` | Block send if CV variant missing |
| `BLOCKED_DOMAINS` | `` | Comma-separated blocklisted recipient domains |
| `SMTP_HOST` | `smtp.gmail.com` | Outgoing mail server |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | | Gmail address (App Password, not login) |
| `SMTP_PASS` | | Gmail App Password |
| `SMTP_STARTTLS` | `true` | Use STARTTLS |
| `SMTP_AUTH` | `true` | Use SMTP AUTH |
| `SENDER_NAME` | `Your Name` | Name in generated application emails |
| `SENDER_EMAIL` | | Your email address |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | | From my.telegram.org (watcher only) |
| `TELEGRAM_CHANNELS` | `tech_revolution_world` | Comma-separated channel usernames |
| `TELEGRAM_BOT_TOKEN` | | From @BotFather (alerts + commands) |
| `TELEGRAM_ALERT_CHAT_ID` | | Your chat ID for alerts |

## CLI reference

| Command | Purpose |
|---------|---------|
| `jobpipe ingest FILE.jsonl` | Load posts from a JSONL file |
| `jobpipe process [--no-alert]` | Extract + score all pending posts |
| `jobpipe list [--all] [--bucket X]` | List jobs (excludes rejected/duplicate/skipped by default) |
| `jobpipe show ID [--raw]` | Show a job's full record |
| `jobpipe approve ID [--send] [--yes]` | Generate `.eml` materials; `--send` also delivers via SMTP |
| `jobpipe reject ID [--reason TEXT]` | Reject a job |
| `jobpipe sent ID` | Mark an application as sent by you |
| `jobpipe send ID [--yes]` | Send one approved application |
| `jobpipe autosend [--manual]` | Send all eligible approved applications |
| `jobpipe policy ID [--manual]` | Explain why a job can/cannot be sent |
| `jobpipe smtp-test` | Verify SMTP credentials, send nothing |
| `jobpipe followups` | Applications awaiting a nudge |
| `jobpipe reply ID --status interview\|offer\|rejected\|ghosted` | Record employer response |
| `jobpipe stats` | Pipeline statistics |
| `jobpipe watch [--backfill N]` | Live Telegram watcher |
| `jobpipe agent [MESSAGE] [--act] [--max-turns N]` | AI agent over your queue |
| `jobpipe triage [--act] [--limit N]` | Agent reviews every undecided job |
| `jobpipe llm-test` | Check LLM provider connectivity |

## Pipeline stages

### 1. Ingest

Raw posts are stored in the `posts` table. Posts shorter than 40 characters are discarded. Deduplication happens at ingest: exact body-hash match first, then fuzzy matching (RapidFuzz `token_sort_ratio`) against the last 400 posts. Re-ingesting the same source message ID is a no-op.

### 2. Dedup (before any LLM call)

Two layers:
1. **`body_hash`** — SHA-256 of normalized text (catches verbatim reposts for free)
2. **Fuzzy** — RapidFuzz on normalized body vs recent posts (catches edited reposts)

A third **metadata fingerprint** (company + title + location/URL) catches the same job arriving from another source. Channel furniture (`#hashtags`, "join us", "share with friends") and non-contact job-board URLs are stripped before comparison; `t.me/` and `wa.me/` recruiter contacts are preserved.

### 3. Extract

**Regex first.** A deterministic pass extracts title, company, location, remote, employment type, years, salary, emails, Telegram handles, URLs, materials, urgency, deadline, and subject-line instructions. This alone produces a usable job record.

**Optional LLM refinement.** If an LLM is reachable, it fills gaps the regex missed (prose-y fields like company names from narrative text). The LLM never overwrites high-confidence regex results (emails, URLs) and its output is Pydantic-validated into a fixed schema. Without an LLM, everything still works in regex-only mode.

### 4. Score

Jobs are matched against `profile.yaml` using **evidence tiers**:

| Tier | Meaning | Weight |
|------|---------|--------|
| `named` | Named explicitly in an employment bullet | 1.00 |
| `inferred` | Role scope implies it; technology unnamed | 0.70 |
| `project` | Portfolio project only | 0.60 |
| `listed` | Competencies list only — no narrative backing | 0.35 |

Three buckets, not a 0–100 leaderboard:
- **APPLY (≥65)** — strong evidenced match
- **REVIEW (≥40)** — worth a human look
- **SKIP (<40)** — not a fit

Noisy fields (salary, "mandatory" skills, years) demote and flag but never hard-reject. The only hard stops are provably-absent skills (e.g. Svelte, Vue, Kubernetes) and unresolved data conflicts (e.g. the `BETA_DATES` CV/letter mismatch).

### 5. Alert

Scored jobs land in SQLite. Jobs at or above `ALERT_MIN_SCORE` are sent to your Telegram via the bot with inline **Approve / Reject / Info** buttons. The alert carries everything needed to decide from your phone: title, company, score, matched/missing skills, evidence depth, contact details, CV variant, and the approve/reject commands.

### 6. Approve (human action)

You run `jobpipe approve ID`. This:
- Refuses quarantined (adversarial) posts
- Picks a CV variant deterministically (AI/ML, mobile, full-stack, backend, frontend)
- Writes the email body (LLM if available, template fallback otherwise)
- Appends `[YOUR ANSWER: ...]` placeholders for requested materials (salary, notice period, GitHub, intro video, portfolio)
- Builds a `.eml` file in `outbox/` with the CV attached
- Records an `APPROVE` decision in `decisions`

Nothing is sent. You double-click the `.eml`, review it, fill in placeholders, and press Send in your mail client.

### 7. Send (human action)

Run `jobpipe send ID` (or `jobpipe approve ID --send --yes`). `policy.check()` runs every gate:
- `AUTO_APPLY` master switch (off by default; bypass per-job with `--manual`)
- Approval state, idempotency (never send twice), quarantine, CV conflict
- Recipient integrity (valid, non-disposable, non-blocklisted, not personal unless allowed)
- Multiple contacts → choose manually; Telegram-only → DM manually
- Score floor for auto-send, CV attachment present, no unfilled placeholders
- Anti-spam (30-day per-employer cooldown), daily send limit, SMTP credentials

### 8. Feedback loop

Every approve/reject writes to `decisions` with a feature snapshot. After ~150 rows, you can train a classifier on your real choices — it will beat any hand-tuned weight vector.

## Prompt injection protection

Adversarial posts are **quarantined, not merely ignored**. The extraction model has no tools, its output is Pydantic-validated, and nothing downstream executes model output. But the residual attack is *routing*: an injected address could get your CV drafted to an attacker. So:

- Posts with prompt-injection markers → `QUARANTINED`, score 0, `approve` refuses
- Posts with multiple email addresses → recipient never auto-filled (choose manually)
- Recipient addresses are verified against the original post text before SMTP send

## Telegram watcher

```bash
python3 -m jobpipe watch --backfill 100
```

Read-only. Never joins channels, never sends, never DMs, never reacts. Sleeps 200 ms between backfill reads. **Join channels manually in the Telegram app first.**

This automates a **user account** tied to your phone number. The realistic risk is rate-limiting or a ban, not a lawsuit. Treat `telegram.session` like a password — it's gitignored.

If you configure `TELEGRAM_BOT_TOKEN`, you also get inline Approve/Reject buttons on alerts and can send `/approve 123`, `/reject 123`, `/info 123` commands from your phone.

## LLM providers

Every provider in `config.py` exposes an OpenAI-compatible endpoint, so one client covers local and hosted. Swap providers with `LLM_PROVIDER`:

- **ollama** (default, local, `qwen3:8b`) — your data never leaves the machine
- **gemini**, **groq**, **openrouter**, **cerebras**, **mistral**, **openai**, **deepseek** — free tiers work
- **custom** — any OpenAI-compatible URL via `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY`

Test connectivity: `python3 -m jobpipe llm-test`

## Offline agent testing

`mock_llm.py` is a throwaway OpenAI-compatible server (stdlib only) that exercises the full agent loop with no API key and no network:

```bash
python3 mock_llm.py
LLM_PROVIDER=custom LLM_BASE_URL=http://127.0.0.1:8111/v1 LLM_MODEL=mock \
  python3 -m jobpipe agent "what is in the queue?"
```

## Deployment

The watcher must run 24/7 (~50 MB RAM, no GPU). The LLM does not — split them.

- **Docker:** `Dockerfile` + `docker-compose.yml` at the root. Mount `./profile.yaml`, `./cv`, `./data`, `./outbox`, and your `.env`.
- **macOS launchd:** `deploy/com.tensae.jobpipe.plist`
- **Linux systemd:** `deploy/jobpipe-watch.service`
- **Railway:** no longer recommended — the free plan is effectively gone ($1/month credit), and the ephemeral filesystem won't persist SQLite/session files.

### Pushing to GitHub

`deploy/push_to_github.sh` creates a private repo and pushes. It refuses to push secrets (`profile.yaml`, `.env`, `*.session`, `*.db`, `cv/`, `outbox/`, `uploads/`, `jobpipe.bundle`).

## Before your first real application

1. Resolve the `BETA_DATES` CV/employment-letter conflict in `profile.yaml` (blocks all sending until cleared).
2. Name your stack in experience bullets per `cv-findings.md` — React, Next.js, and Node.js should appear in bullets, not just the competencies list.
3. Add `github.com/tensaeb` and `linkedin.com/in/tensaeb` to the CV document itself.
4. Cut the five `.docx` CV variants into `cv/`.
5. `python3 rescore.py` to confirm the scoring lift.
6. Send one application to yourself and read it as a recruiter would.

## Not built, deliberately

Streamlit dashboard, Supabase, n8n, Oracle hosting, GitHub Actions, autonomous auto-apply, evidence-ID CV provenance, employer-verification layer, 11-state machine. See `plan-review.md`. Run this for two weeks and let real friction decide what's next.

## Project structure

```
jobpipe/            # Python package
├── __init__.py     # Package exports + version
├── __main__.py     # `python -m jobpipe` entry point
├── cli.py          # CLI commands (argparse subcommands)
├── config.py       # Environment configuration (env-first, no secret defaults)
├── db.py           # SQLite schema + connection + migrations
├── dedup.py        # Body-hash + fuzzy deduplication
├── extract.py      # Regex + optional LLM extraction
├── llm.py          # OpenAI-compatible client for all providers
├── matcher.py      # Evidence-tiered job/profile matching (no LLM)
├── models.py       # Pydantic schemas (prompt-injection boundary)
├── notify.py       # Telegram alerts + bot command listener
├── pipeline.py     # Orchestration: ingest → dedup → extract → score → alert → approve
├── policy.py       # Send-policy gates (human-in-the-loop enforcement)
├── score.py        # Bridge from ParsedJob to Matcher + adversarial checks
├── sender.py       # SMTP delivery + .eml building
├── utils.py        # Shared stdlib helpers (HTML escape, slugify, etc.)
└── watcher.py      # Telethon Telegram watcher (read-only)
samples/            # Sample posts (posts.jsonl)
cv/                 # CV variants (.docx) — gitignored, you provide these
deploy/             # launchd plist, systemd unit, push script
mock_llm.py         # Offline OpenAI-compatible test server
rescore.py          # Re-score sample roles against profile.yaml
selftest.py         # 69 behavioural tests, no network or credentials
profile.example.yaml# Sanitized fact bank template
.env.example        # Configuration template
Dockerfile          # Container image
docker-compose.yml  # Container orchestration
```

## Running tests

```bash
python3 selftest.py
```

69 tests, no network, no credentials, nothing sent. Covers dedup, extraction, scoring, adversarial quarantine, approve/reject, send policy, agent, triage, and the live mock-LLM loop.
