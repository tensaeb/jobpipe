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

from .config import TG_API_HASH, TG_API_ID, TG_CHANNELS
from .db import connect
from .pipeline import ingest_post, process_post

log = logging.getLogger(__name__)


async def run(backfill: int = 0):
    try:
        from telethon import TelegramClient, events
    except ImportError:
        raise SystemExit("pip install telethon")

    if not (TG_API_ID and TG_API_HASH):
        raise SystemExit("Set TELEGRAM_API_ID and TELEGRAM_API_HASH (my.telegram.org)")

    client = TelegramClient("telegram", int(TG_API_ID), TG_API_HASH)
    con = connect()

    async def handle(text, chat, msg_id, posted_at):
        src = getattr(chat, "username", None) or str(getattr(chat, "id", "unknown"))
        url = f"https://t.me/{src}/{msg_id}" if getattr(chat, "username", None) else None
        pid, status = ingest_post(
            con, source=f"telegram:{src}", body=text, source_msg_id=msg_id,
            url=url, posted_at=posted_at.isoformat() if posted_at else None,
        )
        if status in ("seen", "too_short"):
            return
        if status.startswith("duplicate"):
            log.info("post %s skipped (%s)", pid, status)
            return
        jid = process_post(con, pid)
        log.info("post %s -> job %s", pid, jid)

    @client.on(events.NewMessage(chats=TG_CHANNELS))
    async def _(event):
        try:
            await handle(event.message.message or "", await event.get_chat(),
                         event.message.id, event.message.date)
        except Exception:  # noqa: BLE001
            log.exception("handler failed")

    await client.start()
    log.info("watching: %s", ", ".join(TG_CHANNELS))

    if backfill:
        for ch in TG_CHANNELS:
            entity = await client.get_entity(ch)
            async for m in client.iter_messages(entity, limit=backfill):
                if m.message:
                    await handle(m.message, entity, m.id, m.date)
                await asyncio.sleep(0.2)  # be polite
        log.info("backfill complete")

    await client.run_until_disconnected()
