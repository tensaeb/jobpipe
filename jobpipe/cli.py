from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import policy, sender
from .db import connect, ul
from .pipeline import approve, ingest_post, mark_sent, process_pending, reject

BUCKET_ORDER = {"APPLY": 0, "APPLY (HOLD)": 1, "REVIEW": 2, "SKIP": 3}


def cmd_ingest(args, con):
    n = {"new": 0, "duplicate_exact": 0, "duplicate_near": 0, "seen": 0, "too_short": 0}
    p = Path(args.file)
    if p.suffix == ".jsonl":
        records = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    else:  # one post per file, or --text
        records = [{"body": p.read_text()}]
    for r in records:
        _, st = ingest_post(
            con, source=r.get("source", "manual"), body=r.get("body", ""),
            source_msg_id=r.get("id"), url=r.get("url"), posted_at=r.get("posted_at"),
        )
        n[st] = n.get(st, 0) + 1
    print(f"ingested: {n}")


def cmd_process(args, con):
    ids = process_pending(con, alert=not args.no_alert)
    print(f"created {len(ids)} job(s): {ids}")


def cmd_list(args, con):
    q = "SELECT * FROM jobs"
    params: list = []
    where = []
    if not args.all:
        where.append("status NOT IN ('REJECTED','DUPLICATE','SKIPPED')")
    if args.bucket:
        where.append("bucket = ?")
        params.append(args.bucket)
    if where:
        q += " WHERE " + " AND ".join(where)
    rows = sorted(con.execute(q, params).fetchall(),
                  key=lambda r: (BUCKET_ORDER.get(r["bucket"], 9), -(r["score"] or 0)))
    if not rows:
        print("nothing to show")
        return
    print(f"{'id':>4}  {'score':>5}  {'bucket':<13} {'depth':>5}  {'title':<36} {'company':<20} status")
    print("-" * 110)
    for r in rows:
        print(f"{r['id']:>4}  {r['score'] or 0:>5}  {r['bucket'] or '':<13} "
              f"{r['evidence_depth'] or 0:>5.2f}  {(r['title'] or '?')[:35]:<36} "
              f"{(r['company'] or '-')[:19]:<20} {r['status']}")


def cmd_show(args, con):
    r = con.execute("SELECT * FROM jobs WHERE id=?", (args.id,)).fetchone()
    if not r:
        sys.exit(f"no job {args.id}")
    p = con.execute("SELECT * FROM posts WHERE id=?", (r["post_id"],)).fetchone()
    print(f"[{r['bucket']}] score {r['score']}  depth {r['evidence_depth']}  status {r['status']}")
    print(f"{r['title']} @ {r['company'] or '-'}   ({r['extracted_by']})")
    for k in ("location", "employment_type", "min_years", "salary_raw", "urgency",
              "deadline_text", "application_email", "application_telegram", "application_url"):
        if r[k]:
            print(f"  {k:<22}{r[k]}")
    print(f"  {'remote':<22}{bool(r['remote']) if r['remote'] is not None else '?'}")
    print(f"  {'required':<22}{', '.join(ul(r['required_json'])) or '-'}")
    print(f"  {'preferred':<22}{', '.join(ul(r['preferred_json'])) or '-'}")
    print(f"  {'materials':<22}{', '.join(ul(r['materials_json'])) or '-'}")
    print(f"  {'cv_variant':<22}{r['cv_variant']}")
    for b in ul(r["blockers_json"]):
        print(f"  BLOCKER               {b}")
    for f in ul(r["flags_json"]):
        print(f"  flag                  {f}")
    if args.raw and p:
        print("\n--- raw post ---\n" + p["body"])


def cmd_approve(args, con):
    res = approve(con, args.id)
    print(f"to      : {res['to'] or '(none — apply via Telegram/web manually)'}")
    print(f"subject : {res['subject']}")
    print(f"cv      : {res['cv']}")
    print(f"eml     : {res['eml']}")
    for b in res["blocked"]:
        print(f"\n!! {b}")
    print("\n--- body ---\n" + res["body"])
    if getattr(args, "send", False):
        print()
        _report(sender.send_one(con, args.id, manual=True,
                                force_dry=False if args.yes else None))
    else:
        print("\nOpen the .eml in your mail client, check it, press Send.")
        print(f"Then: jobpipe sent {args.id}")
        print(f"Or let jobpipe send it: jobpipe send {args.id} --yes")


def _report(res):
    jid = res["job_id"]
    if res.get("blocked"):
        print(f"job {jid}: NOT SENT")
        for r in res["blocked"]:
            print(f"   \u2717 {r}")
        return False
    if res.get("dry_run"):
        print(f"job {jid}: DRY RUN \u2014 would send")
        print(f"   to         {res['to']}")
        print(f"   subject    {res['subject']}")
        print(f"   attachment {res['attachment'] or '(none)'}")
        print(f"   size       {res['bytes']} bytes")
        print("   set DRY_RUN=false to send for real")
        return False
    print(f"job {jid}: SENT to {res['to']}  [{res['attachment'] or 'no attachment'}]")
    return True


def cmd_send(args, con):
    """Send ONE approved application. Bypasses the AUTO_APPLY master switch
    and the score floor, never the safety gates."""
    res = sender.send_one(con, args.id, manual=True,
                          force_dry=False if args.yes else None)
    _report(res)


def cmd_autosend(args, con):
    results = sender.send_batch(con, manual=args.manual)
    if not results:
        print("nothing approved and pending")
        return
    sent = sum(_report(r) for r in results)
    print(f"\n{sent} sent, {len(results) - sent} held")


def cmd_policy(args, con):
    reasons, ctx = policy.check(con, args.id, manual=args.manual)
    print(f"job {args.id}: sends today {ctx.get('sends_today', '?')}")
    if not reasons:
        print("  \u2713 all gates pass \u2014 eligible to send")
    for r in reasons:
        print(f"  \u2717 {r}")


def cmd_smtptest(args, con):
    ok, msg = sender.smtp_selftest()
    print(("\u2713 " if ok else "\u2717 ") + msg)
    if not ok:
        sys.exit(1)


def cmd_followups(args, con):
    rows = con.execute(
        "SELECT a.job_id, a.follow_up_due, j.title, j.company, j.application_email"
        " FROM applications a JOIN jobs j ON j.id=a.job_id"
        " WHERE a.sent_at IS NOT NULL AND a.response_status IS NULL"
        " AND a.follow_up_due <= date('now') ORDER BY a.follow_up_due"
    ).fetchall()
    if not rows:
        print("no follow-ups due")
        return
    for r in rows:
        print(f"job {r['job_id']:>4}  due {r['follow_up_due']}  "
              f"{(r['title'] or '?')[:34]:<35} {r['application_email'] or '-'}")
    from . import notify
    lines = ["\U0001f4c5 Follow-ups due:"]
    for r in rows:
        lines.append(
            f"  job {r['job_id']}  {r['follow_up_due']}  "
            f"{(r['title'] or '?')[:30]}  {r['application_email'] or '-'}"
            f"\n  \u2192 jobpipe reply {r['job_id']} --status ..."
        )
    notify.send("\n".join(lines))


def cmd_reject(args, con):
    reject(con, args.id, args.reason)
    print(f"rejected {args.id}" + (f" ({args.reason})" if args.reason else ""))


VALID_STATUSES = ("interview", "offer", "rejected", "ghosted")


def cmd_reply(args, con):
    """Record an employer's response to a sent application."""
    if args.status not in VALID_STATUSES:
        sys.exit(f"status must be one of: {', '.join(VALID_STATUSES)}")
    n = con.execute(
        "UPDATE applications SET response_status=? WHERE job_id=?",
        (args.status, args.id),
    ).rowcount
    if n == 0:
        sys.exit(f"no application found for job {args.id}")
    con.commit()
    print(f"job {args.id}: response_status set to '{args.status}'")
def cmd_sent(args, con):
    mark_sent(con, args.id)
    print(f"job {args.id} marked SENT")


def cmd_stats(args, con):
    def one(q):
        return con.execute(q).fetchone()[0]

    print(f"posts              {one('SELECT COUNT(*) FROM posts')}")
    print(f"jobs               {one('SELECT COUNT(*) FROM jobs')}")
    duplicates = one("SELECT COUNT(*) FROM jobs WHERE status='DUPLICATE'")
    print(f"  duplicates       {duplicates}")
    for b in ("APPLY", "APPLY (HOLD)", "REVIEW", "SKIP"):
        n = con.execute("SELECT COUNT(*) FROM jobs WHERE bucket=?", (b,)).fetchone()[0]
        print(f"  {b:<16} {n}")
    print(f"applications       {one('SELECT COUNT(*) FROM applications')}")
    print(f"  sent             {one('SELECT COUNT(*) FROM applications WHERE sent_at IS NOT NULL')}")
    print(f"decisions logged   {one('SELECT COUNT(*) FROM decisions')}")
    # Extraction method breakdown (regex vs regex+llm).
    total_jobs = one("SELECT COUNT(*) FROM jobs")
    if total_jobs:
        llm_jobs = one("SELECT COUNT(*) FROM jobs WHERE extracted_by='regex+llm'")
        regex_jobs = total_jobs - llm_jobs
        print(f"extraction: regex={regex_jobs}  regex+llm={llm_jobs}"
              f"  ({llm_jobs/total_jobs*100:.0f}% LLM-assisted)")

    # LLM rate limit status.
    from . import llm as _llm
    rl = _llm.rate_limit_status()
    if rl["limit"] is not None:
        used = rl["limit"] - rl["remaining"] if rl["remaining"] is not None else "?"
        print(f"LLM rate limit: {used}/{rl['limit']} used"
              f"  (reset {rl['reset_ctime'] or 'unknown'})")
    elif rl["remaining"] is not None:
        print(f"LLM rate limit: {rl['remaining']} remaining (limit unknown)")
    rows = con.execute(
        "SELECT follow_up_due, job_id FROM applications WHERE sent_at IS NOT NULL"
        " AND response_status IS NULL AND follow_up_due <= date('now')"
    ).fetchall()
    if rows:
        print("\nfollow-ups due:")
        for r in rows:
            print(f"  job {r['job_id']} (due {r['follow_up_due']})")


def cmd_watch(args, con):
    import asyncio

    from .watcher import run

    asyncio.run(run(backfill=args.backfill))


def cmd_agent(args, con):
    from . import llm
    from .agent import Agent

    agent = Agent(con, act=args.act, max_turns=args.max_turns)

    def event(name, a, result):
        preview = json.dumps(a, ensure_ascii=False)
        status = "ERROR" if isinstance(result, dict) and result.get("error") else "ok"
        print(f"  -> {name} {preview[:90]} [{status}]")

    if args.message:
        print(agent.ask(" ".join(args.message), on_event=event))
        return
    mode = "act (approve/reject enabled)" if args.act else "read-only"
    print(f"jobpipe agent — {llm.LLM_PROVIDER}/{llm.LLM_MODEL} — {mode}")
    print("Ask anything about your queue. Commands: /reset, /quit")
    while True:
        try:
            line = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("/quit", "/exit", "/q"):
            break
        if line == "/reset":
            agent.reset()
            print("(context cleared)")
            continue
        print(f"\n{agent.ask(line, on_event=event)}")


def cmd_triage(args, con):
    from .agent import triage

    results = triage(con, limit=args.limit, act=args.act)
    if not results:
        print("nothing to triage — no undecided NEW jobs")
        return
    print(f"{'job':>4}  {'score':>5}  {'bucket':<13} {'decision':<8} {'conf':>4}  "
          f"applied  reason")
    print("-" * 110)
    for r in results:
        conf = "" if r.get("confidence") is None else f"{r['confidence']:.2f}"
        applied = "yes" if r.get("applied") else ("ERR" if r.get("error") else "no")
        print(f"{r['job_id']:>4}  {r['score'] or 0:>5}  {(r['bucket'] or ''):<13} "
              f"{r['decision']:<8} {conf:>4}  {applied:<7}  {(r['reason'] or '')[:60]}")
        if r.get("error"):
            print(f"       error: {r['error']}")
    if not args.act:
        print("\nreview the above, then re-run with --act to record these decisions "
              "(approve only generates .eml — nothing is ever sent)")


def cmd_llmtest(args, con):
    from . import llm

    res = llm.test_provider()
    print(f"provider  : {res['provider']}")
    print(f"base_url  : {res['base_url']}")
    print(f"model     : {res['model']}")
    print(f"api key   : {res['api_key']}")
    if res["ok"]:
        print(f"reachable : yes ({res['latency_ms']} ms)")
        print(f"reply     : {res['reply']!r}")
        # Check whether the model name was confirmed in the provider's list.
        try:
            model_list = llm._get_client().models.list()
            ids = [m.id for m in (model_list.data if hasattr(model_list, "data")
                                   else list(model_list))]
            if ids:
                found = res["model"] in ids
                print(f"model in list : {'yes' if found else 'NO — not found in provider list'}")
            else:
                print("model in list : (provider returned empty list — cannot verify)")
        except Exception as e:
            print(f"model in list : (could not fetch model list: {e})")
    else:
        print(f"reachable : NO — {res['error']}")
        sys.exit(1)



def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from .config import LLM_ENABLED
    if LLM_ENABLED != "off":
        from . import llm as _llm
        if not _llm.available() and LLM_ENABLED == "on":
            print(
                "WARNING: LLM_ENABLED=on but the LLM provider is not reachable "
                "or the model was not found. Run `jobpipe llm-test` for details.",
                file=sys.stderr,
            )

    ap = argparse.ArgumentParser(prog="jobpipe")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ingest", help="load posts from a .jsonl or .txt file")
    s.add_argument("file")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("process", help="parse+score all unprocessed posts")
    s.add_argument("--no-alert", action="store_true")
    s.set_defaults(fn=cmd_process)

    s = sub.add_parser("list")
    s.add_argument("--all", action="store_true")
    s.add_argument("--bucket")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("show")
    s.add_argument("id", type=int)
    s.add_argument("--raw", action="store_true")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("approve", help="generate materials; --send to deliver")
    s.add_argument("id", type=int)
    s.add_argument("--send", action="store_true", help="send immediately after generating")
    s.add_argument("--yes", action="store_true", help="really send (override DRY_RUN)")
    s.set_defaults(fn=cmd_approve)

    s = sub.add_parser("reject")
    s.add_argument("id", type=int)
    s.add_argument("--reason")
    s.set_defaults(fn=cmd_reject)

    s = sub.add_parser("sent", help="mark an application as sent by you")
    s.add_argument("id", type=int)
    s.set_defaults(fn=cmd_sent)

    s = sub.add_parser("send", help="send ONE approved application")
    s.add_argument("id", type=int)
    s.add_argument("--yes", action="store_true", help="really send (override DRY_RUN)")
    s.set_defaults(fn=cmd_send)

    s = sub.add_parser("autosend", help="send all approved + eligible applications")
    s.add_argument("--manual", action="store_true",
                   help="bypass AUTO_APPLY switch and score floor")
    s.set_defaults(fn=cmd_autosend)

    s = sub.add_parser("policy", help="explain why a job can or cannot be sent")
    s.add_argument("id", type=int)
    s.add_argument("--manual", action="store_true")
    s.set_defaults(fn=cmd_policy)

    s = sub.add_parser("smtp-test", help="verify SMTP credentials, send nothing")
    s.set_defaults(fn=cmd_smtptest)

    s = sub.add_parser("followups", help="applications awaiting a nudge")
    s.set_defaults(fn=cmd_followups)

    s = sub.add_parser("reply", help="record an employer's response")
    s.add_argument("id", type=int)
    s.add_argument("--status", required=True,
                   choices=["interview", "offer", "rejected", "ghosted"])
    s.set_defaults(fn=cmd_reply)

    s = sub.add_parser("stats")
    s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("watch", help="live Telegram watcher")
    s.add_argument("--backfill", type=int, default=0)
    s.set_defaults(fn=cmd_watch)

    s = sub.add_parser("agent", help="talk to the AI agent about your queue")
    s.add_argument("message", nargs="*", help="one-shot question (omit for interactive)")
    s.add_argument("--act", action="store_true",
                   help="enable approve/reject tools (approve still never sends)")
    s.add_argument("--max-turns", type=int, default=None,
                   help="max tool-loop iterations (default AGENT_MAX_TURNS)")
    s.set_defaults(fn=cmd_agent)

    s = sub.add_parser("triage", help="agent reviews every undecided job")
    s.add_argument("--act", action="store_true",
                   help="record decisions (approve generates .eml, never sends)")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_triage)

    s = sub.add_parser("llm-test", help="check LLM provider connectivity")
    s.set_defaults(fn=cmd_llmtest)

    args = ap.parse_args(argv)

    # Startup validation: warn about missing identity before any send path.
    from .config import SENDER_EMAIL, SENDER_NAME
    if not SENDER_EMAIL and args.fn in (cmd_send, cmd_autosend, cmd_approve):
        print("warning: SENDER_EMAIL is not set — generated .eml files will have "
              "an empty From address. Set SENDER_NAME and SENDER_EMAIL in .env.",
              file=sys.stderr)

    con = connect()
    try:
        args.fn(args, con)
    except (ValueError, KeyError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
