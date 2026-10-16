"""Telegram discovery via Telethon.

Operational notes (these matter more than the ToS discussion):
  * This automates a USER account tied to your phone number. The realistic
    risk is rate-limiting or a ban, not a lawsuit. Keep it quiet.
  * Join channels manually in the Telegram app first. The watcher never joins.
  * It never sends, never DMs, never reacts. Read-only.
  * telegram.session is a login credential. It is gitignored. Keep it that way.
"""
from __future__ import annotations

import asyncio
import logging
import time as _time
from datetime import datetime, timedelta, timezone

from .config import MAX_POST_AGE_DAYS, TG_API_HASH, TG_API_ID, TG_BOT_TOKEN, TG_CHANNELS
from .db import connect
from .notify import HEARTBEAT_FILE
from .pipeline import ingest_post, process_post, reprocess_job, update_job_score

log = logging.getLogger(__name__)

# Module-level state for the heartbeat.
_last_post_time: float | None = None
_daily_count: int = 0


def _in_thread(fn, *args, **kw):
    """Open a short-lived DB connection on the worker thread, call ``fn`` with
    it as the first argument, and close the connection when done.

    This satisfies SQLite's thread-affinity rule: a connection opened in the
    event-loop thread can never be used inside ``asyncio.to_thread``.
    """
    con = connect()
    try:
        return fn(con, *args, **kw)
    finally:
        con.close()


def _get_existing_job(con, src: str, msg_id: str):
    """Fetch an existing job for a source+msg_id pair. Pure function for
    asyncio.to_thread safety."""
    return con.execute(
        "SELECT j.id, j.score FROM jobs j JOIN posts p ON p.id=j.post_id "
        "WHERE p.source=? AND p.source_msg_id=?",
        (src, str(msg_id)),
    ).fetchone()


async def run(backfill: int = 0):
    try:
        from telethon import TelegramClient, events
    except ImportError:
        raise SystemExit("pip install telethon")

    if not (TG_API_ID and TG_API_HASH):
        raise SystemExit("Set TELEGRAM_API_ID and TELEGRAM_API_HASH (my.telegram.org)")

    client = TelegramClient("data/telegram", int(TG_API_ID), TG_API_HASH)

    async def handle(text, chat, msg_id, posted_at):
        global _last_post_time, _daily_count
        try:
            src = getattr(chat, "username", None) or str(getattr(chat, "id", "unknown"))
            url = f"https://t.me/{src}/{msg_id}" if getattr(chat, "username", None) else None
            pid, status = await asyncio.to_thread(
                _in_thread, ingest_post,
                source=f"telegram:{src}",
                body=text,
                source_msg_id=msg_id,
                url=url,
                posted_at=posted_at.isoformat() if posted_at else None,
            )
            if status in ("seen", "too_short"):
                return
            if status.startswith("duplicate"):
                log.info("post %s skipped (%s)", pid, status)
                return
            jid = await asyncio.to_thread(_in_thread, process_post, pid)
            log.info("post %s -> job %s", pid, jid)
            _last_post_time = _time.time()
            _daily_count += 1
            try:
                HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
                HEARTBEAT_FILE.write_text(str(_last_post_time))
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:
            from . import notify
            notify.send(f"\u26a0 Watcher error on msg {msg_id}: {exc!r}")
            log.exception("handler failed: msg_id=%s", msg_id)
            raise

    @client.on(events.NewMessage(chats=TG_CHANNELS))
    async def _(event):
        try:
            await handle(event.message.message or "", await event.get_chat(),
                         event.message.id, event.message.date)
        except Exception:  # noqa: BLE001
            log.exception("handler failed")

    @client.on(events.MessageEdited(chats=TG_CHANNELS))
    async def _edited(event):
        try:
            text = event.message.message or ""
            chat = await event.get_chat()
            msg_id = event.message.id
            src = getattr(chat, "username", None) or str(getattr(chat, "id", "unknown"))

            row = await asyncio.to_thread(
                _in_thread, _get_existing_job, f"telegram:{src}", str(msg_id)
            )
            if row is None:
                # Not seen before — treat as a new message.
                await handle(text, chat, msg_id, event.message.date)
                return

            old_score = row["score"] or 0
            # Re-extract and re-score to detect meaningful edits.
            from .extract import extract as _extract
            from .score import score as _score, cv_variant as _cv_variant
            parsed, _ = await asyncio.to_thread(_extract, text)
            match, cv = await asyncio.to_thread(_score, parsed, str(row["id"]), text)

            # Full edit reconciliation: persist the new score/bucket/flags/cv
            # AND refresh the stored body, skills, recipient, and other parsed
            # fields so that a subsequent approve() builds materials from the
            # edited post, not the stale original.
            if abs(match.score - old_score) >= 5:
                await asyncio.to_thread(
                    _in_thread, reprocess_job, row["id"], text
                )
                from . import notify
                # Re-fetch the updated row for the alert.
                r = await asyncio.to_thread(
                    _in_thread, lambda c: c.execute(
                        "SELECT * FROM jobs WHERE id=?", (row["id"],)
                    ).fetchone()
                )
                if r:
                    await asyncio.to_thread(
                        notify.send_with_buttons,
                        row["id"],
                        (
                            f"\u270f\ufe0f <b>Edited post</b> \u2014 score changed "
                            f"{old_score} \u2192 {r['score']}\n"
                            + notify.format_alert(
                                row["id"], match, parsed, cv, None,
                                include_commands=False,
                            )
                        ),
                    )
        except Exception:  # noqa: BLE001
            log.exception("edited-message handler failed")

    async def heartbeat():
        """Send a keep-alive ping every 30 minutes and touch the heartbeat file."""
        from . import notify
        while True:
            await asyncio.sleep(1800)
            mins = (
                int((_time.time() - _last_post_time) / 60)
                if _last_post_time else "?"
            )
            try:
                HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
                HEARTBEAT_FILE.write_text(str(_time.time()))
            except Exception:  # noqa: BLE001
                pass
            notify.send(
                f"\u2705 Watcher alive | last post: {mins} mins ago"
                f" | processed this session: {_daily_count}"
            )

    await client.start()
    log.info("watching: %s", ", ".join(TG_CHANNELS))

    # Start bot listener if a bot token is configured.
    if TG_BOT_TOKEN:
        from . import notify
        asyncio.create_task(notify.start_bot_listener())

    # Start heartbeat.
    asyncio.create_task(heartbeat())

    if backfill:
        cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_POST_AGE_DAYS)
        for ch in TG_CHANNELS:
            entity = await client.get_entity(ch)
            async for m in client.iter_messages(entity, limit=backfill):
                if m.message:
                    if m.date and m.date.astimezone(timezone.utc) < cutoff:
                        log.info(
                            "backfill: skipping post older than %d days", MAX_POST_AGE_DAYS
                        )
                        continue
                    await handle(m.message, entity, m.id, m.date)
                await asyncio.sleep(0.2)  # be polite
        log.info("backfill complete")

    await client.run_until_disconnected()
