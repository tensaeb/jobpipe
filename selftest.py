"""Behavioural tests for the pipeline. Run: python3 selftest.py"""
import json, os, tempfile, sys

# Hermetic: ignore whatever is in the shell or .env so results are stable.
for k in list(os.environ):
    if k.startswith(("SMTP_", "AUTO_APPLY", "TELEGRAM_", "LLM_", "DRY_RUN",
                     "ALLOW_PERSONAL", "REQUIRE_CV", "DAILY_SEND", "BLOCKED_DOM",
                     "PROFILE", "CV_DIR", "DB_PATH", "OUTBOX")):
        del os.environ[k]
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")
os.environ["OUTBOX"] = tempfile.mkdtemp()
os.environ["CV_DIR"] = tempfile.mkdtemp()  # deliberately empty
os.environ["LLM_ENABLED"] = "off"          # prove regex-only works

from jobpipe.db import connect, jl, now, ul
from jobpipe.pipeline import approve, ingest_post, process_pending, reject
from jobpipe.dedup import body_hash, normalize_body
from jobpipe.extract import regex_extract, injection_signals

ok = fail = 0
def check(name, cond):
    global ok, fail
    if cond: ok += 1; print(f"  PASS  {name}")
    else:    fail += 1; print(f"  FAIL  {name}")

posts = [json.loads(l) for l in open("samples/posts.jsonl")]
con = connect()
stats = {}
for r in posts:
    _, st = ingest_post(con, r["source"], r["body"], r["id"], r.get("url"))
    stats[st] = stats.get(st, 0) + 1
jobs = process_pending(con, alert=False)

print("\n[dedup]")
check("near-duplicate repost detected at ingest", stats.get("duplicate_near") == 1)
check("duplicate never reaches extraction", len(jobs) == 8)
check("re-ingesting the same message_id is a no-op",
      ingest_post(con, posts[0]["source"], posts[0]["body"], posts[0]["id"])[1] == "seen")
check("re-processing is idempotent", process_pending(con, alert=False) == [])
check("normalization strips channel furniture",
      "join us" not in normalize_body("Hiring dev. Join us 👉 @chan #react"))

print("\n[extraction, regex-only]")
j = regex_extract(posts[0]["body"])
check("title",    j.title == "Frontend Developer")
check("email",    j.application_email == "careers@wagergeeks.com")
check("company",  j.company == "WagerGeeks")
check("years",    j.min_years == 2.0)
check("remote",   j.remote is True)
check("urgency",  j.urgency == "immediate")
check("required skills found", {"React", "Next.js", "TypeScript"} <= set(j.required_skills))
check("preferred split off required", "React" not in j.preferred_skills)
check("materials", {"CV", "GitHub", "Portfolio"} <= set(j.required_materials))
check("salary absent stays None", j.salary_raw is None)
j2 = regex_extract(posts[1]["body"])
check("salary captured verbatim", j2.salary_raw and "1,600" in j2.salary_raw)

print("\n[scoring]")
rows = {r["id"]: r for r in con.execute("SELECT * FROM jobs").fetchall()}
angular = [r for r in rows.values() if r["title"] == "Frontend Developer" and r["company"] is None]
check("Angular role rejected via hard negative", angular and angular[0]["bucket"] == "SKIP")
check("conflict resolved — APPLY bucket now possible (no BLOCKED conflicts)",
      not any("BLOCKED:" in f for r in rows.values() for f in ul(r["flags_json"])))
check("conflict resolved — CTO part-time + Beta full-time can coexist",
      not any("BLOCKED: BETA_DATES" in f for r in rows.values()
              for f in ul(r["flags_json"])))
check("evidence depth recorded", all(r["evidence_depth"] is not None for r in rows.values()))

print("\n[adversarial]")
inj = [r for r in rows.values() if r["status"] == "QUARANTINED"]
check("injection post quarantined", len(inj) == 1)
check("attacker address NOT stored as recipient",
      inj and inj[0]["application_email"] is None)
check("injection signals detected", len(injection_signals(posts[8]["body"])) >= 2)
try:
    approve(con, inj[0]["id"]); check("approve refuses quarantined job", False)
except ValueError:
    check("approve refuses quarantined job", True)

print("\n[application + feedback loop]")
target = [r for r in rows.values() if r["status"] == "NEW"][0]
res = approve(con, target["id"])
check("eml written", os.path.exists(res["eml"]))
check("recipient set from post", res["to"] is not None)
check("CV variant chosen deterministically", res["cv"].endswith(".docx"))
check("no blocking conflict on approve",
      not any(b.startswith("BLOCKED:") for b in res["blocked"]))
check("no fabricated metrics in body",
      not any(w in res["body"] for w in ("%", "users", "increased", "reduced")))
other = [r for r in rows.values() if r["status"] == "NEW"][1]
reject(con, other["id"], "stack mismatch")
n = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
check("decisions logged for training signal", n == 2)
feat = con.execute("SELECT features FROM decisions LIMIT 1").fetchone()[0]
check("decision captures features", "evidence_depth" in feat)

print("\n[cv routing]")
from jobpipe.matcher import Job as MJ, cv_variant
from jobpipe.score import score as _score
from jobpipe.models import ParsedJob
def variant(title, skills):
    return _score(ParsedJob(title=title, required_skills=skills))[1]
check("ML skills beat a generic title",
      variant("Senior Software Engineer", ["Python","TensorFlow","Pandas","NumPy"]) == "cv_ai_ml.docx")
check("MERN routes to full-stack",
      variant(None, ["MongoDB","Express","React","Node.js"]) == "cv_fullstack.docx")
check("mobile routed",
      variant("React Native Developer", ["React Native"]) == "cv_mobile.docx")
check("backend routed",
      variant("Backend Engineer", ["Django","PostgreSQL","REST APIs"]) == "cv_backend.docx")

print("\n[send policy]")
from jobpipe import policy
sent_job = [r for r in rows.values() if r["status"] == "NEW"][0]["id"]
reasons, _ = policy.check(con, sent_job, manual=True)
check("no longer blocks on resolved CV conflict",
      not any("BETA_DATES" in r for r in reasons))
# SMTP block test — if real SMTP creds are present, no SMTP block expected.
from jobpipe import config as _cfg
if _cfg.SMTP_USER and _cfg.SMTP_PASS:
    check("SMTP configured — no SMTP block (expected with real creds)",
          not any("SMTP_USER/SMTP_PASS" in r for r in reasons))
else:
    check("blocks without SMTP config (no creds)", any("SMTP" in r for r in reasons))
qid = inj[0]["id"]
r2, _ = policy.check(con, qid, manual=True)
check("quarantined job blocked even in manual mode",
      any("QUARANTINED" in x for x in r2))
check("no-recipient job blocked", any("recipient" in x for x in r2))
r3, _ = policy.check(con, 999999, manual=True)
check("unknown job id handled", r3 and "does not exist" in r3[0])

print("\n[llm providers]")
from jobpipe.config import LLM_PROVIDERS, resolve_llm
r = resolve_llm({})
check("defaults unchanged: ollama local + qwen3:8b",
      r["provider"] == "ollama" and "localhost:11434" in r["base_url"]
      and r["model"] == "qwen3:8b" and r["api_key"] == "ollama")
check("free providers registered",
      {"gemini", "groq", "openrouter", "cerebras", "mistral", "ollama"} <= set(LLM_PROVIDERS))
r = resolve_llm({"LLM_PROVIDER": "gemini"})
check("gemini preset resolves base_url + model",
      r["base_url"].startswith("https://generativelanguage.googleapis.com/")
      and r["model"].startswith("gemini-"))
r = resolve_llm({"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "secret"})
check("provider key env var picked up", r["api_key"] == "secret")
r = resolve_llm({"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "a", "LLM_API_KEY": "b",
                 "LLM_BASE_URL": "http://x/v1", "LLM_MODEL": "m1"})
check("explicit overrides beat presets",
      r["api_key"] == "b" and r["base_url"] == "http://x/v1" and r["model"] == "m1")
r = resolve_llm({"LLM_PROVIDER": "does-not-exist"})
check("unknown provider degrades to custom", r["provider"] == "does-not-exist")

print("\n[agent]")
from jobpipe import agent as agent_mod
from jobpipe import llm as llm_mod
from jobpipe.llm import AssistantReply, ToolCall

# Two synthetic posts give the agent tests their own jobs to work with.
p1 = ("Skyward Labs is hiring a Remote Backend Engineer, full-time. Requirements: "
      "Python, Django, PostgreSQL, REST APIs, 3+ years experience. Salary: $2500 per "
      "month. Send your CV to hiring@skywardlabs.test. Ref backend-9911.")
p2 = ("Vireo Studio wants a Freelance Full-Stack Developer, contract, remote. Must "
      "know JavaScript, Python, React, SQL and REST APIs, 4 years experience. Pay is "
      "$60 per hour. Apply with your CV and portfolio at apply@vireostudio.test. "
      "Ref front-7722.")
sid1, _ = ingest_post(con, "selftest-agent", p1)
sid2, _ = ingest_post(con, "selftest-agent", p2)
new_ids = process_pending(con, alert=False)
check("synthetic posts become jobs", len(new_ids) == 2)
syn = {r["id"]: r for r in con.execute(
    "SELECT * FROM jobs WHERE post_id IN (?,?)", (sid1, sid2)).fetchall()}
check("synthetic jobs are NEW",
      len(syn) == 2 and all(r["status"] == "NEW" for r in syn.values()))
s1, s2 = sorted(syn)

script = [AssistantReply(tool_calls=[ToolCall(id="t1", name="list_jobs",
                                              arguments={"limit": 5})]),
          AssistantReply(tool_calls=[ToolCall(id="t2", name="show_job",
                                              arguments={"job_id": s1})]),
          AssistantReply(content=f"Job {s1} is the top match — approve recommended.")]
it = iter(script)
events = []
orig_ct = llm_mod.complete_tools
llm_mod.complete_tools = lambda messages, tools, **kw: next(it)
ag = agent_mod.Agent(con, act=False)
ans = ag.ask("what should I do first?", on_event=lambda n, a, r: events.append(n))
llm_mod.complete_tools = orig_ct
check("agent loop runs tools then answers",
      ans.endswith("approve recommended.") and events == ["list_jobs", "show_job"])
check("tool results fed back into context",
      sum(1 for m in ag.messages if m.get("role") == "tool") == 2)
check("read-only agent has no decide tool",
      "decide" not in [t["function"]["name"] for t in agent_mod.Agent(con).tools()])
check("decide refused even if called read-only",
      "error" in agent_mod.Agent(con)._call("decide", {"job_id": s1, "action": "approve"}))
check("act mode exposes decide",
      "decide" in [t["function"]["name"] for t in agent_mod.Agent(con, act=True).tools()])

print("\n[agent decide guards]")
ag2 = agent_mod.Agent(con, act=True)
qjid = con.execute("SELECT id FROM jobs WHERE status='QUARANTINED'").fetchone()["id"]
res = ag2._call("decide", {"job_id": qjid, "action": "approve", "reason": "nudge"})
check("approve refuses quarantined job", "error" in res and not res.get("ok"))
res = ag2._call("decide", {"job_id": s1, "action": "approve", "reason": "good fit"})
check("approve generates materials, sends nothing",
      res.get("ok") is True and res.get("sent") is False and bool(res.get("eml")))
app = con.execute("SELECT sent_at FROM applications WHERE job_id=?", (s1,)).fetchone()
check("approved application not sent", app is not None and app["sent_at"] is None)
check("approve reports the remaining send gates",
      bool(res.get("send_still_blocked_by")))
res = ag2._call("decide", {"job_id": s1, "action": "approve"})
check("double approve blocked (idempotency)", "error" in res)
res = ag2._call("decide", {"job_id": s2, "action": "reject", "reason": "stack mismatch"})
check("agent reject logs a decision", res.get("ok") is True)
drow = con.execute("SELECT reason FROM decisions WHERE job_id=? AND action='REJECT'",
                   (s2,)).fetchone()
check("reject reason tagged as agent",
      drow is not None and drow["reason"] == "agent: stack mismatch")

never = AssistantReply(tool_calls=[ToolCall(id="x", name="pipeline_stats", arguments={})])
llm_mod.complete_tools = lambda messages, tools, **kw: never
ag3 = agent_mod.Agent(con, max_turns=3)
ans = ag3.ask("loop forever")
llm_mod.complete_tools = orig_ct
check("turn bound enforced",
      "AGENT_MAX_TURNS" in ans and
      sum(1 for m in ag3.messages if m.get("role") == "tool") == 3)
check("clear message when no LLM configured",
      "LLM not reachable" in agent_mod.Agent(con).ask("hello"))

print("\n[triage]")
n0 = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
a0 = con.execute("SELECT COUNT(*) FROM decisions WHERE reason LIKE 'agent:%'").fetchone()[0]
orig_cj = llm_mod.complete_json
llm_mod.complete_json = lambda system, user, schema, **kw: schema.model_validate(
    {"decision": "approve", "reason": "strong stack match", "confidence": 0.87})
plan = agent_mod.triage(con, limit=10, act=False)
llm_mod.complete_json = orig_cj
check("triage returns a verdict per undecided job",
      len(plan) >= 1 and all(p["decision"] == "approve" for p in plan))
check("triage without --act changes nothing",
      con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == n0)
llm_mod.complete_json = lambda system, user, schema, **kw: schema.model_validate(
    {"decision": "reject", "reason": "no evidenced match", "confidence": 0.61})
done = agent_mod.triage(con, limit=10, act=True)
llm_mod.complete_json = orig_cj
check("triage --act records every verdict",
      len(done) >= 1 and all(d["applied"] for d in done))
a1 = con.execute("SELECT COUNT(*) FROM decisions WHERE reason LIKE 'agent:%'").fetchone()[0]
check("triage decisions tagged as agent", a1 - a0 == len(done))
check("triage never touches quarantined jobs",
      all(d["job_id"] != qjid for d in done))
check("nothing was ever sent by agent or triage",
      con.execute("SELECT COUNT(*) FROM applications WHERE sent_at IS NOT NULL"
                  ).fetchone()[0] == 0)

print("\n[agent live loop via local mock server]")
try:
    import openai  # noqa: F401
except ImportError:
    print("  SKIP  openai package not installed — mock-server loop skipped")
else:
    import threading
    from http.server import ThreadingHTTPServer

    import mock_llm

    srv = ThreadingHTTPServer(("127.0.0.1", 0), mock_llm.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    saved = (llm_mod.LLM_BASE_URL, llm_mod.LLM_MODEL, llm_mod.LLM_ENABLED,
             llm_mod._client, llm_mod._probe_result)
    llm_mod.LLM_BASE_URL = f"http://127.0.0.1:{port}/v1"
    llm_mod.LLM_MODEL = "mock"
    llm_mod.LLM_ENABLED = "on"
    llm_mod._client = None
    llm_mod._probe_result = True
    try:
        ag = agent_mod.Agent(con, act=False, max_turns=6)
        ans = ag.ask("walk me through the queue")
    finally:
        (llm_mod.LLM_BASE_URL, llm_mod.LLM_MODEL, llm_mod.LLM_ENABLED,
         llm_mod._client, llm_mod._probe_result) = saved
        srv.shutdown()
        srv.server_close()
    check("real openai client completes the agent loop", "Mock agent" in ans)
    check("two tool round-trips executed against the server",
          sum(1 for m in ag.messages if m.get("role") == "tool") == 2)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
