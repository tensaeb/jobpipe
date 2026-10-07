# jobpipe

Personal job-application pipeline. Telegram discovery → dedup → extract → score → alert → you approve → `.eml` you send.

Runs with **no credentials and no LLM**. Both are optional upgrades.

```
posts ──► dedup ──► extract ──► score ──► alert ──► YOU ──► .eml ──► you press Send
          (first,   (regex,     (vs        (phone)   approve
           no LLM)   LLM opt.)   profile)
```

## Quick start

```bash
pip install -r requirements.txt            # core: 5 deps, full pipeline + agent + tests
# pip install -r requirements-optional.txt # adds the live Telegram watcher
cp profile.example.yaml profile.yaml       # your fact bank is never committed
python3 -m jobpipe ingest samples/posts.jsonl
python3 -m jobpipe process --no-alert
python3 -m jobpipe list
python3 -m jobpipe show 1 --raw
python3 -m jobpipe approve 1        # writes outbox/0001-*.eml — never sends
python3 -m jobpipe reject 2 --reason "angular only"
python3 -m jobpipe sent 1           # after you actually send it
python3 -m jobpipe stats

# optional, needs an LLM provider (free tiers — see "Free LLM providers"):
python3 -m jobpipe llm-test         # ping the configured provider
python3 -m jobpipe agent            # interactive AI agent over your queue
python3 -m jobpipe triage           # agent verdicts for every undecided job
```

`python3 selftest.py` → 69 behavioural assertions, no network (a local mock
OpenAI server is spun up in-process for the agent loop).

## Commands

| Command | Does |
|---|---|
| `ingest FILE` | Load posts from `.jsonl` (`{id, source, url, body}`) or a `.txt` |
| `process` | Parse + score everything unprocessed; alerts on score ≥ `ALERT_MIN_SCORE` |
| `list [--all] [--bucket X]` | Ranked queue |
| `show ID [--raw]` | Full record, flags, blockers |
| `approve ID` | Generate email body + `.eml` with CV attached. **Never sends.** |
| `reject ID [--reason]` | Log a rejection (feeds the training signal) |
| `sent ID` | Mark sent manually (if you sent it yourself) |
| `send ID [--yes]` | Send ONE approved application over SMTP |
| `autosend` | Send all approved + eligible applications |
| `policy ID` | Explain exactly why a job can or cannot be sent |
| `smtp-test` | Verify SMTP credentials, send nothing |
| `followups` | Applications awaiting a nudge |
| `stats` | Counts + follow-ups due |
| `watch [--backfill N]` | Live Telethon watcher |
| `agent [MSG] [--act]` | AI agent over the queue (interactive if MSG omitted) |
| `triage [--act]` | Agent verdicts (`approve/reject/skip` + reason) for every undecided job |
| `llm-test` | Ping the configured LLM provider and print latency |

## AI agent

```bash
python3 -m jobpipe llm-test                    # is the provider reachable?
python3 -m jobpipe agent                       # interactive: ask anything
python3 -m jobpipe agent "which job should I apply to first?"
python3 -m jobpipe agent --act "approve job 4 if it fits my profile"
python3 -m jobpipe triage                      # preview the whole queue
python3 -m jobpipe triage --act                # ...and record the decisions
```

The agent is a bounded tool loop (default `AGENT_MAX_TURNS=12`) with six
tools over the pipeline's own data: `list_jobs`, `show_job`, `pipeline_stats`,
`explain_policy`, `profile_summary`, and — only with `--act` — `decide`.

What it can and cannot do:

- **Read-only by default.** Without `--act` the model cannot change anything;
  calling `decide` anyway returns an error to the model.
- `decide` can `approve` (generates the `.eml` — **never sends**) or `reject`
  (logs a decision). Quarantined jobs are refused, double-approves hit an
  idempotency guard, and reasons are stored as `agent: ...` in the
  `decisions` feedback loop.
- **There is no send tool.** Sending email stays `jobpipe send ID`, gated by
  the policy checks, with `DRY_RUN=true` and `AUTO_APPLY=false` by default.
- Post text reaches the model as labelled untrusted data; the system prompt
  forbids following instructions found in posts, and Pydantic validates every
  structured output (triage verdicts, extraction) at the boundary.

`triage` is the batch form: every undecided `NEW` job gets one verdict
(`approve | reject | skip`) with a reason and a confidence. Run it without
`--act` to preview, with `--act` to record.

Offline smoke test with no API key: `python3 mock_llm.py` in one terminal,
then `LLM_PROVIDER=custom LLM_BASE_URL=http://127.0.0.1:8111/v1 LLM_MODEL=mock
python3 -m jobpipe agent "walk me through the queue"`.

### Free LLM providers

Any OpenAI-compatible endpoint works; presets are selected with
`LLM_PROVIDER` (keys come from the provider's own env var):

| `LLM_PROVIDER` | Endpoint | Free tier | Key env var |
|---|---|---|---|
| `gemini` | Google Gemini API | ✅ | `GEMINI_API_KEY` |
| `groq` | Groq | ✅ | `GROQ_API_KEY` |
| `openrouter` | OpenRouter (`:free` models) | ✅ | `OPENROUTER_API_KEY` |
| `cerebras` | Cerebras | ✅ | `CEREBRAS_API_KEY` |
| `mistral` | Mistral | ✅ | `MISTRAL_API_KEY` |
| `ollama` | local (default) | ✅ | — |
| `openai` / `deepseek` / `custom` | any OpenAI-compatible URL | — | `OPENAI_API_KEY` / … |

```bash
cp .env.example .env        # then fill in one provider:
# LLM_PROVIDER=gemini
# GEMINI_API_KEY=your-key   # free at aistudio.google.com/apikey
python3 -m jobpipe llm-test
```

`LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` override any preset, so any
other provider (Mistral-compatible gateways, vLLM, LM Studio, …) works via
`LLM_PROVIDER=custom`. The LLM remains optional: `LLM_ENABLED=auto` falls
back to regex extraction and the template email body when unreachable.

## Auto-apply

"Auto-apply" means **send what you already approved**, not autonomous applying. `AUTO_APPLY=false` and `DRY_RUN=true` by default, and nothing sends without an `APPROVE` row in `decisions`.

```
new post → scored → alert → YOU approve → 12 policy gates → SMTP → follow-up clock
```

`jobpipe policy ID` prints every gate and why it passes or fails. The gates cover: master switch, approval state, idempotency, quarantine, unresolved CV conflicts, recipient integrity (malformed / disposable / blocklisted / free-mail), multiple-contact ambiguity, Telegram-only posts, score floor, missing CV attachment, same-employer-in-30-days, and a daily send cap.

`--manual` relaxes **only** the master switch and the score floor. Never quarantine, idempotency, recipient integrity, or the CV conflict.

Transport is SMTP with an app password rather than the Gmail API — see below. Full walkthrough including how to test sends against a local throwaway mail server: **`RUNBOOK.md`**.

## Design decisions

**Dedup runs before extraction.** The channel reposts constantly and most posts have no company and no URL, so metadata fingerprinting alone fails. Layer 1 is a SHA-256 of the normalized body (URLs, emails, handles, hashtags and promo lines stripped); layer 2 is RapidFuzz ≥ 92 against the last 400 posts. A repost never reaches the LLM and can never produce a second application.

**Flags, not hard rejects.** Salary, "mandatory" skills and years are noisy extracted fields. They demote and warn. The only hard stops are provably-absent skills (`absent:` in `profile.yaml`), adversarial content, and the unresolved `BETA_DATES` conflict — which appends `BLOCKED` to every job and downgrades any `APPLY` to `APPLY (HOLD)` until you fix the CV.

**Evidence tiers, not keyword counts.** Matches are weighted by how well your CV actually evidences the skill (`named` 1.0 / `inferred` 0.7 / `project` 0.6 / `listed` 0.35). A job matching only competencies-list entries gets `SHALLOW_EVIDENCE` and an 0.85× penalty. This is what stops React/Next.js keyword hits scoring 90+ on a CV whose bullets never mention them.

**No Gmail OAuth.** `gmail.send` is a sensitive scope; in Testing mode refresh tokens die every 7 days, and escaping that means Google verification for a single-user tool. Two transports instead: a `.eml` file you double-click (opens in any mail client, CV attached), or direct SMTP with a Gmail App Password. One secret, no consent screen, no weekly outage.

**The LLM writes only the email body.** The CV is a static hand-written variant picked by rule, so there is no fabrication surface. Without a reachable LLM, a template fallback is used and everything still works.

### Prompt injection

The model has no tools and its output is Pydantic-validated into a fixed schema, so post content can never cause an action. But the residual attack isn't "the model obeys" — it's **routing**: inject an address so your CV gets drafted to the attacker. Sample post 1009 does exactly this, and in the first build it worked: `collector@evil.test` landed in the recipient field purely because it appeared before the real one.

So adversarial posts are quarantined, not merely ignored:

```
[SKIP] score 0   status QUARANTINED
  BLOCKER  PROMPT_INJECTION: post contains 'ignore all previous instructions'
  flag     MULTIPLE_CONTACTS: collector@evil.test, hr@legitcorp.com
           — recipient NOT auto-filled, choose manually
```

`approve` refuses outright. Any post with more than one email address never gets an auto-filled recipient, injection or not.

### Feedback loop

Every approve/reject writes to `decisions` with a feature snapshot. After ~150 rows, fit a classifier on your real choices — it will beat any hand-tuned weight vector, including this one. That's the intended path for the scoring model; the current weights are a cold-start prior, not a conclusion.

## Configuration

Copy `.env.example` → `.env`. Everything is optional.

| Var | Default | Note |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | Preset: `gemini`, `groq`, `openrouter`, `cerebras`, `mistral`, `ollama`, `openai`, `deepseek`, `custom` |
| `LLM_BASE_URL` | provider's | Overrides the preset — any OpenAI-compatible URL |
| `LLM_MODEL` | provider's | Overrides the preset |
| `LLM_API_KEY` | provider's env var | e.g. `GEMINI_API_KEY`; local Ollama needs none |
| `LLM_ENABLED` | `auto` | `auto` probes once, falls back to regex-only |
| `AGENT_MAX_TURNS` | `12` | Bound on the agent's tool loop |
| `ALERT_MIN_SCORE` | `40` | REVIEW and above |
| `DEDUP_FUZZ` | `92` | Lower = more aggressive dedup |
| `TELEGRAM_*` | — | `api_id`/`api_hash` from my.telegram.org; bot token from @BotFather |

## Telegram watcher

`python3 -m jobpipe watch --backfill 100`

Read-only. Never joins, never sends, never DMs, sleeps 200 ms between backfill reads. **Join channels manually in the app first.**

This automates a user account tied to your phone number — the realistic risk is rate-limiting or a ban, not a lawsuit. `telegram.session` is a login credential and is gitignored; treat it like a password.

## Not built, deliberately

Streamlit dashboard, Supabase, n8n, Oracle hosting, GitHub Actions, auto-apply, evidence-ID CV provenance, employer-verification layer, 11-state machine. See `plan-review.md`. Run this for two weeks and let real friction decide what's next.

## Deploying

`RUNBOOK.md` has the step-by-step. Short version: the watcher must be 24/7 (~50 MB RAM), the LLM does not — split them. launchd plist and a hardened systemd unit are in `deploy/`; `Dockerfile` + `docker-compose.yml` at the root.

## Next

1. **Fix the CV** (`cv-findings.md`) — worth more than anything here.
2. Drop 4 variants into `cv/`: `cv_fullstack.docx`, `cv_frontend.docx`, `cv_backend.docx`, `cv_ai_ml.docx`, `cv_mobile.docx`.
3. Clear `BETA_DATES` from `profile.yaml` to unlock `APPLY`.
4. Add your GitHub URL to `profile.yaml` to clear `PROFILE_GAP`.
5. `watch --backfill 100`, then triage for a week before touching the scoring weights.
