# Eight-problem remediation pass

This diff resolves the eight blocking concerns raised in the prior review: phone-actionable Telegram alerts, dedup correctness, preference-aware scoring, subject-line extraction and placeholder answers, actor-labeled decisions with a reply command, watcher robustness, housekeeping, and LLM model-name validation. All eight problem areas have code present. Seven are fully correct. One — the edited-message path — has a confirmed correctness gap, and there are two smaller issues worth fixing before production.

Watch for: (1) the edited-message handler re-scores a post but never writes the updated score to the database, so a significant edit silently leaves stale data; (2) the bot polling loop runs `while True` with no restart logic — one unhandled exception at the `_bot_api` call layer silently kills alerting; (3) the `_daily_count` heartbeat counter never resets at midnight, so it over-reports on long-running processes.

**Verdict**: NEEDS_CHANGES

---

## High-level view

The Telegram bot integration (`notify.py`) delivers inline Approve/Reject/Info buttons, `/approve N`, `/reject N`, `/info N` text commands, and `.eml` forwarding after approval. The polling loop runs in `asyncio.to_thread` and opens its own SQLite connection to satisfy `check_same_thread`. The gap: `start_bot_listener` catches the initial crash but the `while True` inside `send_bot_command_updates` has no restart envelope — an unhandled exception at the `_bot_api` layer exits the thread permanently, killing alerting until the watcher restarts.

Dedup is correctly reworked. `token_sort_ratio` removes the subset-containment false positives, the 0.6 length guard prevents short generic posts from matching unrelated long ones, and emails/handles are preserved in normalization so two different recruiters using the same template no longer collapse.

Preference scoring maps all five requested dimensions: on-site work mode, night-shift timezone mismatch, salary floor, employment type, and non-engineering role filter. Each violation damps the score by 0.85 and appends a flag, never a blocker.

Subject-line extraction lands at every layer: `extract.py` regex, LLM prompt field list, `ParsedJob` model, `subject_for()` composer, and `approve()`. The one gap: `approve()` re-extracts with `regex_extract()` only, so an instruction the LLM caught in a prose format the regex misses is lost at approval time — `subject_instruction` is not stored in the `jobs` table.

Actor labeling introduces a confirmed three-way distinction (`user`/`agent`/`bot`) in `decisions`. Agent and bot paths call `approve()` then post-hoc `UPDATE` the actor. This works under the current single-threaded design but is fragile — the cleaner fix is an `actor=` parameter on `approve()` itself.

The edited-message handler has a confirmed bug: it re-scores the post and fires a Telegram alert when the score shifts by ≥5, but never writes the new score, bucket, or flags back to the `jobs` row. A subsequent `approve` call rebuilds the match from the stale stored row. The heartbeat's `_daily_count` also never resets at midnight, so the "processed today" counter is actually "processed since last restart."

---

<details>
<summary>Issues (5)</summary>

1. **Edited-post score not persisted** — `_edited` in `watcher.py` re-scores the post and sends an alert when the score shifts by ≥5, but never updates the `jobs` row. The DB retains the old score, bucket, flags, and CV variant, so a subsequent `approve` builds materials from stale data. Add an `UPDATE jobs SET score=?, bucket=?, flags_json=?, cv_variant=? WHERE id=?` after the alert fires, or introduce a `pipeline.update_job_score()` helper.

2. **Bot listener exits permanently on unexpected exception** — `send_bot_command_updates` handles a failed poll response but does not catch exceptions that escape the per-update `try/continue` block. An unexpected error from `_bot_api` or `json.loads` exits the thread; `start_bot_listener` logs "bot listener crashed" once and never restarts. Wrap the `while True` body in an outer `try/except Exception` that logs and continues.

3. **Subject-instruction lost for LLM-only extractions** — `approve()` re-extracts with `regex_extract()` only. An instruction the LLM found in a prose format the regex misses ("Please use the role and your name in your email subject") is absent at approval time because `subject_instruction` is not stored in the `jobs` table. Add a `subject_instruction` column populated at `process_post` time.

4. **`_daily_count` reports since restart, not today** — The counter never resets at midnight. The heartbeat says "processed today: N" but N is the lifetime count. Reset when the calendar date changes, or change the label.

5. **`approve()` actor post-hoc correction is fragile** — `_bot_approve` and the agent's `_t_decide` both call `approve()` (which writes `actor='user'`) and then correct it with a separate `UPDATE`. This works in the current single-threaded design but would silently mis-attribute concurrent approvals. Add an `actor=` parameter to `pipeline.approve()`.

</details>

---

<details>
<summary>Details</summary>

### Bot listener resilience gap

The `while True` poll loop in `send_bot_command_updates` handles a failed `_bot_api` response with `time.sleep(5)` and `continue`. That protects against transient network failures. It does not protect against exceptions that escape the per-update `try/continue` block further down — for example, an unexpected exception from `_bot_api` itself or from `json.loads` in the callback parser. When that happens, the thread exits silently; `start_bot_listener` logs "bot listener crashed" once and then the watcher continues without any bot handling. Adding an outer `try/except Exception` around the `while True` body that logs and continues is the minimal fix.

### Subject-instruction lost between ingestion and approval (confirmed)

`approve()` re-extracts `subject_instruction` with `regex_extract()` at approval time — no LLM, no DB column. A post where the instruction is in a prose format the regex misses ("Please use the role and your name in your email subject") will produce a generic subject at approval time even though the LLM extracted it correctly during ingestion. The field is not stored in `jobs`, so there is no way to recover it without re-running the LLM.

The options are: (1) add a `subject_instruction` column to the `jobs` table and populate it at `process_post` time, or (2) call `llm.complete_json()` again at approval time only when the regex finds nothing. Option 1 is the right long-term solution; option 2 has lower blast radius.

### Edited-post score not persisted (confirmed)

`_edited` in `watcher.py` re-extracts and re-scores a post, then sends a Telegram alert when the score shifts by ≥5. It never writes the new `score`, `bucket`, `flags_json`, or `cv_variant` back to the `jobs` row. A reviewer who sees the alert and runs `jobpipe approve N` gets materials built from the stale stored row — wrong score, wrong flags, potentially wrong CV variant.

Fix: after the score-delta check, update the row:

```python
await asyncio.to_thread(
    con.execute,
    "UPDATE jobs SET score=?, bucket=?, flags_json=?, cv_variant=? WHERE id=?",
    (match.score, match.bucket, jl(match.flags), cv_variant, row["id"]),
)
con.commit()
```

or extract this into a `pipeline.update_job_score()` helper.

### `_daily_count` reports lifetime, not today

`_daily_count` is a module-level integer that increments on every processed post and never resets. The heartbeat says "processed today: N" but N is "processed since last restart". On a watcher that runs for multiple days this misleads. The fix is either to track a `(date, count)` pair and reset when the date changes, or change the heartbeat message to "processed this session".

</details>

---

<details>
<summary>File map</summary>

| File | What changed |
|---|---|
| `jobpipe/notify.py` | Added `send_with_buttons`, `_bot_api`, `_send_document`, `_format_info`, `_bot_approve`, `send_bot_command_updates`, `start_bot_listener` |
| `jobpipe/dedup.py` | Switched to `token_sort_ratio`, added 0.6 length guard, preserved emails/handles in normalization |
| `jobpipe/score.py` | Added `preference_flags()` with five preference dimensions; score damping per flag |
| `jobpipe/extract.py` | Added `_SUBJ_RE`, `subject_instruction` extraction in `regex_extract()` and merge in `extract()` |
| `jobpipe/models.py` | Added `subject_instruction: str | None` field to `ParsedJob` |
| `jobpipe/compose.py` | Updated `subject_for()` to substitute instruction tokens; added `[YOUR ANSWER: …]` placeholder block in `write_body()` |
| `jobpipe/db.py` | Added `actor TEXT` column to `decisions` via safe `ALTER TABLE` migration |
| `jobpipe/pipeline.py` | Added `actor=` param to `record_decision`; `approve()` re-extracts `subject_instruction`; `approve()` passes it to compose |
| `jobpipe/cli.py` | Added `cmd_reply`, `cmd_followups`, LLM availability warning at startup, `--send`/`--yes` flags on approve, `llm-test` model-in-list output |
| `jobpipe/watcher.py` | `asyncio.to_thread` for blocking calls; `MessageEdited` handler; `heartbeat` coroutine; per-handler error alerts; `MAX_POST_AGE_DAYS` cutoff in backfill |
| `jobpipe/llm.py` | `available()` now validates model name against provider list; 1-token fallback when `/models` is unavailable |
| `jobpipe/config.py` | Corrected model preset names; `SENDER_EMAIL` defaults to `""`; `DEDUP_FUZZ` updated to 82; `MAX_POST_AGE_DAYS` exported |
| `.gitignore` | Added `jobpipe.bundle` |
| `deploy/push_to_github.sh` | Added `\.bundle$` to the leak-check pattern |
| `RUNBOOK.md` | Updated expected test count to 69 |

Full diff: `git diff main` from the project root.

</details>
