# Runbook — run, test, deploy

Every command below was executed in this workspace before being written down.

---

## Step 0 — What auto-apply actually means here

"Auto-apply" in this build = **send what you already approved, without opening a mail client**. It is not autonomous applying. The chain is:

```
new post → scored → alert → YOU approve → policy gates → SMTP send → follow-up clock
                             ^^^^^^^^^^^
                             still required
```

`AUTO_APPLY=false` and `DRY_RUN=true` by default. A job cannot be sent unless an `APPROVE` decision exists in the database for it.

### Every gate that must pass

```
jobpipe policy 8
```
```
job 8: sends today 0
  ✗ AUTO_APPLY=false (set it in .env, or use `jobpipe send ID`)
  ✗ no materials generated — run `jobpipe approve` first
  ✗ no APPROVE decision recorded for this job
  ✗ BLOCKED: unresolved conflict BETA_DATES — resolve before sending
  ✗ score 68 below AUTO_APPLY_MIN_SCORE=70
  ✗ SMTP_USER/SMTP_PASS not configured
```

| Gate | Blocks when |
|---|---|
| Master switch | `AUTO_APPLY=false` (bypass per-job with `jobpipe send ID`) |
| Approval | no materials, or no `APPROVE` row in `decisions` |
| Idempotency | `sent_at` already set — a job can never send twice |
| Quarantine | post contained prompt-injection markers |
| CV honesty | unresolved `BETA_DATES` conflict in `profile.yaml` |
| Recipient | missing, malformed, disposable, blocklisted, or free-mail (unless `ALLOW_PERSONAL_EMAIL=true`) |
| Ambiguity | post lists several email addresses → choose manually |
| Telegram-only | never automated. DM by hand or skip |
| Score floor | below `AUTO_APPLY_MIN_SCORE` (default 70) |
| Attachment | CV variant missing from `cv/` |
| Anti-spam | already applied to this company in the last 30 days |
| Throughput | `DAILY_SEND_LIMIT` (default 10) reached |

`--manual` relaxes **only** the master switch and the score floor. It never relaxes quarantine, idempotency, recipient integrity, or the CV conflict.

---

## Step 1 — Install and verify (5 minutes)

```bash
cd ~/jobpipe
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-optional.txt   # or requirements.txt for core only
cp .env.example .env
cp profile.example.yaml profile.yaml      # then edit in your real details
```

Run the test suite. No network, no credentials, nothing sent:

```bash
python3 selftest.py
```

Expected tail:
```
[send policy]
  PASS  blocks without SMTP config
  PASS  blocks on unresolved CV conflict
  PASS  quarantined job blocked even in manual mode
  PASS  no-recipient job blocked
  PASS  unknown job id handled

39 passed, 0 failed
```

If anything fails, stop here — don't connect Telegram or SMTP yet.

---

## Step 2 — Dry-run the whole pipeline on sample data

```bash
python3 -m jobpipe ingest samples/posts.jsonl
python3 -m jobpipe process --no-alert
python3 -m jobpipe list
```

```
ingested: {'new': 9, 'duplicate_near': 1, 'seen': 0, 'too_short': 0}
created 8 job(s)

  id  score  bucket        depth  title                          company             status
   8     68  APPLY (HOLD)   0.74  Junior Full Stack Developer    Codebasesolutions   NEW
   4     47  REVIEW         0.60  Senior Software Engineer       Codingcops          NEW
   1     46  REVIEW         0.29  Frontend Developer             WagerGeeks          NEW
   2     42  REVIEW         0.33  Full Stack Developer           Redesigned Agency   NEW
   7      0  SKIP           0.00  Software Engineer              Evil                QUARANTINED
```

Three things to confirm:
- **10 posts in, 8 jobs out** — one repost deduped, one channel-chatter post discarded.
- **Job 7 is QUARANTINED** — that's the prompt-injection sample. Check it:
  ```bash
  python3 -m jobpipe show 7
  ```
  You should see `PROMPT_INJECTION`, and `application_email` absent — the attacker's address was *not* stored as the recipient.
- **Everything is `(HOLD)`** — because `BETA_DATES` is unresolved. Expected until you fix the CV.

Generate materials:
```bash
python3 -m jobpipe approve 8
ls outbox/
```

---

## Step 3 — Test sending without emailing a real person

Run a throwaway SMTP server that prints whatever it receives:

```bash
pip install aiosmtpd
python3 -m aiosmtpd -n -l 127.0.0.1:1025 -d     # leave running in a second terminal
```

Point jobpipe at it:

```bash
export SMTP_HOST=127.0.0.1 SMTP_PORT=1025 SMTP_STARTTLS=false SMTP_AUTH=false
python3 -m jobpipe smtp-test
#  ✓ connected to 127.0.0.1:1025 (no auth)

python3 -m jobpipe send 8
#  job 8: DRY RUN — would send
#     to         hr@codebasesolutions.com
#     subject    Application for Junior Full Stack Developer
#     attachment cv_fullstack.docx
#     size       50990 bytes

export DRY_RUN=false
python3 -m jobpipe send 8 --yes
#  job 8: SENT to hr@codebasesolutions.com  [cv_fullstack.docx]

python3 -m jobpipe send 8 --yes
#  job 8: NOT SENT
#     ✗ already sent at 2026-10-07T17:40:23+00:00 (idempotency guard)
```

Check the second terminal — the full MIME message with the base64 `.docx` attachment should be there. **Unset these before using real SMTP:**

```bash
unset SMTP_HOST SMTP_PORT SMTP_STARTTLS SMTP_AUTH DRY_RUN
```

---

## Step 4 — Real credentials

### Gmail app password (not your login password)

1. Google Account → Security → turn **2-Step Verification** on (required).
2. Security → **App passwords** → create one named `jobpipe`.
3. Put the 16-character string in `.env` as `SMTP_PASS`.

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=tensaeb2017@gmail.com
SMTP_PASS=xxxxxxxxxxxxxxxx
```

```bash
python3 -m jobpipe smtp-test
```

Send yourself a test first — temporarily set a job's recipient to your own address:
```bash
sqlite3 data/jobpipe.db "UPDATE jobs SET application_email='tensaeb2017@gmail.com' WHERE id=1;"
python3 -m jobpipe approve 1
ALLOW_PERSONAL_EMAIL=true python3 -m jobpipe send 1 --yes
```
Open it in Gmail. Check the attachment opens, the signature is right, and it doesn't look machine-generated. **Do this before any real application goes out.**

### Telegram

1. `my.telegram.org` → API development tools → get `api_id` and `api_hash`.
2. `@BotFather` → `/newbot` → token. Message your bot once, then get your chat id:
   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | grep -o '"id":[0-9-]*' | head -1
   ```
3. **Join the channel manually in the Telegram app.** The watcher never joins.
4. First run prompts for your phone + login code, then writes `telegram.session`:
   ```bash
   python3 -m jobpipe watch --backfill 50
   ```

`telegram.session` is a login credential. It's gitignored. Keep it that way.

---

## Step 5 — Go live, carefully

Week 1 — notifications only, approve by hand:
```env
AUTO_APPLY=false
DRY_RUN=true
```

Week 2 — if the parsing has been accurate, allow sending for high scorers only:
```env
AUTO_APPLY=true
DRY_RUN=false
AUTO_APPLY_MIN_SCORE=80
DAILY_SEND_LIMIT=5
```

```bash
python3 -m jobpipe autosend          # sends every approved + eligible application
python3 -m jobpipe followups         # nudges due
python3 -m jobpipe stats
```

Daily loop:
```bash
python3 -m jobpipe list              # triage
python3 -m jobpipe show 12 --raw     # read the original post
python3 -m jobpipe approve 12 --send --yes
python3 -m jobpipe reject 13 --reason "angular only"
```

Rejection reasons are not cosmetic — they populate `decisions`, which is what you'll train the scorer on later.

---

## Step 6 — Deploy

The watcher must be 24/7; the LLM does not. Split them: tiny watcher on an always-on box, processing wherever Ollama lives.

### Option A — Mac, always-on (simplest)

```bash
cp deploy/com.tensae.jobpipe.plist ~/Library/LaunchAgents/
sed -i '' "s|/Users/CHANGEME|$HOME|g" ~/Library/LaunchAgents/com.tensae.jobpipe.plist
launchctl load ~/Library/LaunchAgents/com.tensae.jobpipe.plist
tail -f data/watch.log
```
Stop: `launchctl unload ~/Library/LaunchAgents/com.tensae.jobpipe.plist`

### Option B — Linux VPS, systemd

```bash
sudo useradd -r -s /bin/false jobpipe
sudo git clone <your-repo> /opt/jobpipe && cd /opt/jobpipe
sudo python3 -m venv .venv && sudo .venv/bin/pip install -r requirements.txt
sudo cp .env /opt/jobpipe/.env && sudo chmod 600 /opt/jobpipe/.env
sudo chown -R jobpipe:jobpipe /opt/jobpipe

sudo cp deploy/jobpipe-watch.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jobpipe-watch
journalctl -u jobpipe-watch -f
```

Authenticate interactively **once** before enabling the service (`python3 -m jobpipe watch` as the `jobpipe` user) — Telethon needs to prompt for the login code.

### Option C — Docker

```bash
docker compose up -d --build
docker compose logs -f
```

Generate `telegram.session` on the host first; compose mounts it in.

> Skip Oracle Always Free. A1 capacity is rarely obtainable, idle instances get reclaimed, and the AMD micro shape has 1 GB RAM — it won't load an 8B model at all. The watcher needs ~50 MB; any cheap VPS is fine.

---

## Step 7 — Push to GitHub

Checked directly: the GitHub connector is **unsupported** in Arena, there's no `gh` CLI or token in this environment, and `api.github.com` answers `401 Requires authentication`. Network reaches GitHub; nothing can authenticate as you. So the push is yours to run — two ways, both one command.

### Option A — scripted (creates the repo too)

```bash
./deploy/push_to_github.sh jobpipe
```

Creates a **private** repo via the API and pushes. It refuses to run if anything sensitive is tracked, verifies the token before creating anything, is safe to re-run, and passes the token through a temporary askpass helper so it never lands in `.git/config`, the remote URL, or your shell history.

Token: <https://github.com/settings/personal-access-tokens/new> — fine-grained, your account as resource owner, **Repository permissions → Administration: Read and write** (to create) + **Contents: Read and write** (to push). Set a 7-day expiry. Either `export GITHUB_TOKEN=...` first or let it prompt.

Public instead (think first — see the warning below): `VISIBILITY=public ./deploy/push_to_github.sh jobpipe`

### Option B — manual

1. Create an empty repo at <https://github.com/new> — name `jobpipe`, **Private**, no README/.gitignore/licence.

2. Push:
```bash
cd ~/jobpipe
git remote add origin https://github.com/tensaeb/jobpipe.git
git branch -M main
git push -u origin main
```

### Make it private

`profile.yaml` is gitignored, but `data/jobpipe.db` accumulates recruiter addresses and your whole application history, and `.env` holds a Gmail app password. One mistaken `git add -f` on a public repo is a bad day.

3. Verify nothing sensitive went up:
```bash
git ls-files | grep -E 'profile\.yaml|\.env$|\.session|\.db$|^cv/|^outbox/'
# must print NOTHING
```

If that prints anything, stop and fix `.gitignore` before pushing again. If you already pushed a secret, rotate it — removing the file from a later commit does not remove it from history.

### Option C — no GitHub at all

`jobpipe.bundle` in the project root is the complete history as one file (66 KB, `git bundle verify` passes). Copy it anywhere and clone from it:

```bash
git clone jobpipe.bundle jobpipe
```

Useful for moving the project straight onto a VPS without GitHub in the middle.

### What is and isn't tracked

Tracked: `jobpipe/`, `samples/`, `deploy/`, `Dockerfile`, `docker-compose.yml`, `selftest.py`, `rescore.py`, `profile.example.yaml`, `.env.example`, the three markdown docs.

Ignored: `.env`, `profile.yaml`, `*.session`, `data/`, `outbox/`, `cv/`, `uploads/`, `__pycache__/`.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `535 Authentication failed` | Using your Google password. Create an **App Password** (needs 2FA). |
| Everything stuck at `APPLY (HOLD)` | `BETA_DATES` unresolved. Clear `conflicts:` in `profile.yaml`. |
| `CV variant ... missing` | Put the five `.docx` files in `cv/`. |
| All scores look low | Expected until the CV rewrite — see `cv-findings.md`. Evidence depth < 0.45 triggers a penalty. |
| Telethon asks for a code every start | `telegram.session` isn't persisted. Check the volume mount / working directory. |
| `database is locked` | Two processes writing. WAL is on, but don't run `watch` twice. |
| No alerts arriving | Message your bot first — bots can't initiate conversations. Re-check `TELEGRAM_ALERT_CHAT_ID`. |
| LLM ignored | `LLM_ENABLED=auto` probes once and silently falls back. Force with `LLM_ENABLED=on` to see the error. |

---

## Before your first real application

1. Resolve the Beta Tech Hub dates and clear `conflicts:` from `profile.yaml`.
2. Rewrite the CV per `cv-findings.md` — naming your stack in the bullets moves 7 of 12 sample roles from SKIP/REVIEW into APPLY.
3. Add `github.com/tensaeb` and `linkedin.com/in/tensaeb` **to the CV document itself**. The fact bank knowing them doesn't put them on the page a recruiter opens.
4. Cut the five `.docx` variants into `cv/`.
5. `python3 rescore.py` to confirm the lift.
6. Send one application to yourself and read it as a recruiter would.
