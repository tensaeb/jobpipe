# CV ingested — fact bank built, and the §2 fit table does not survive contact with it

Files produced:

| File | What it is |
|---|---|
| `profile.yaml` | Canonical fact bank. Every claim traceable to a CV line; 51 skills tiered by evidence strength |
| `matcher.py` | Deterministic matcher. No LLM, three buckets, flags-not-rejects |
| `rescore.py` | Re-runs the plan's §2 role list against the real CV |

---

## 1. The thing nobody flagged: you have a 10-month employment gap

Your CV's most recent role, Beta Tech Hub, ends **December 2025**. Today is **October 2026**.

Nothing in the CV addresses the intervening 10 months. This is the first thing any reviewer notices, it will be the first question in any screen, and it is not mentioned anywhere in the 45-section plan or its fit analysis. It outranks every architectural decision in that document.

And it compounds with the date conflict from §16. If the employment letter (Oct 10 2024 – Apr 22 2025) is the accurate record rather than the CV (Sept 2024 – Dec 2025):

| | CV as written | If the letter is right |
|---|---|---|
| Beta Tech Hub tenure | 16 months | 6.5 months |
| Total experience | 48 months (**"4+ years"** ✓) | ~38 months (**3.2 years** ✗) |
| Employment gap | 10 months | **18 months** |

The "4+ years" headline on your CV is load-bearing for most of the roles in that channel, and under one of the two documents you hold, it isn't true. `profile.yaml` marks this `action: BLOCK_SENDING`, so the matcher appends a `BLOCKED` flag to every single job until you resolve it. Resolve it by hand today.

---

## 2. The §2 fit scores were measuring your skills *list*, not your experience

Here's the finding that invalidates the original table. Your CV names **30+ technologies** in Technical Competencies. Your experience bullets name **five**: Python, JavaScript, SQL, HTML, CSS.

**React, Next.js, and Node.js — the three technologies this channel asks for most — appear nowhere outside the competencies list.** No bullet says you shipped anything with them. The EFDP Ministry of Transport work says "Frontend Developer... responsive public-facing site and a multi-role admin dashboard" without naming a single framework.

So `profile.yaml` tiers every skill by evidence strength — `named` (1.00) / `inferred` (0.70) / `project` (0.60) / `listed` (0.35) — and the matcher weights matches accordingly. Rescoring the plan's own role list:

```
role                                        plan  real     Δ  bucket    depth
-----------------------------------------------------------------------------
Junior Full Stack Developer                   84    68   -16  APPLY      0.74
React Native Developer (freelance)            68    60    -8  REVIEW     0.83
AI/ML Senior Software Engineer                75    53   -22  REVIEW     0.60
Full Stack Developer (AI-first)               87    49   -38  REVIEW     0.57
Manual QA Engineer                            25    48   +23  REVIEW     1.00
Senior React Native Developer                 35    47   +12  REVIEW     0.82
Frontend Developer (React + Next.js)          93    42   -51  REVIEW     0.29
Full Stack Developer (Redesigned)             91    40   -51  SKIP       0.33
Svelte Frontend Developer                     35    38    +3  SKIP       1.00
Freelance Full Stack (telecom)                89    36   -53  SKIP       0.00
Full Stack Engineer (Zaimah)                  90    34   -56  SKIP       0.00
Angular Frontend Developer                    30    17   -13  SKIP       0.00
```

`depth` = share of the match backed by real narrative evidence rather than the competencies list.

The four roles the plan called "Very High priority" (93, 91, 90, 89) have depths of **0.29, 0.33, 0.00, 0.00**. Zaimah and the telecom freelance role match your CV on *literally zero* evidenced skills — every requirement hits a bare list entry. The plan ranked them top-four.

---

## 3. But the matcher isn't the fix — your CV is the bottleneck

Before you conclude you're unqualified: I re-ran everything against a simulated CV where the **only** change is naming the stack you already claim inside the bullets you already have. No new claims, no new jobs, no invented experience — just "Built a responsive public-facing site" → "Built a responsive public-facing site in React and Next.js."

```
role                                   plan   now  fixed   bucket now -> fixed
-------------------------------------------------------------------------------
Junior Full Stack Developer              84    68    100   APPLY      -> APPLY
Full Stack Developer (Redesigned)        91    40     88   SKIP       -> APPLY
Frontend Developer (React + Next.js)     93    42     87   REVIEW     -> APPLY
Freelance Full Stack (telecom)           89    36     79   SKIP       -> APPLY
React Native Developer (freelance)       68    60     78   REVIEW     -> APPLY
Full Stack Engineer (Zaimah)             90    34     70   SKIP       -> APPLY
Full Stack Developer (AI-first)          87    49     68   REVIEW     -> APPLY
AI/ML Senior Software Engineer           75    53     58   REVIEW     -> REVIEW
```

**One afternoon of CV editing moves seven roles into APPLY.** That is a larger improvement to your actual outcomes than every component in the 45-section plan combined, and it costs nothing. Do this before you write a line of the system.

Concretely, rewrite along these lines — the facts are unchanged, only the stack is made explicit:

- `EFDP_02` → "Developed a responsive public-facing site and multi-role admin dashboard **in React and Next.js** with role-based access control..."
- `BETA_01` → "Led end-to-end delivery of a Property Management System **built on [stack]**, giving landlords..."
- `BETA_03` → "Built and supported the e-commerce platforms Global Trade House and Efoyita **using [stack]**..."
- `NODELALA_01` → "...full-stack property platform across web **(Next.js)** and mobile **(React Native)**..."

Fill the brackets with what you actually used. If a technology in your competencies list never fills a bracket anywhere, consider cutting it — an unevidenced claim is a liability in a technical screen, not an asset.

---

## 4. Drop AI/ML from your target roles

`target_roles` in §44 includes **AI Engineer** and **ML Engineer**. The entire evidential basis is `ML_01`: one Streamlit regression app on Ethiopian car sales. **TensorFlow and OpenCV have zero supporting evidence anywhere in the CV** — they appear only in the competencies list.

The plan scored CodingCops AI/ML at 70–80 and called it "High priority." Real score: 53, and that's generous. Applying to AI/ML roles on this CV wastes the scarcest resource in a job search — your application throughput and your credibility with recruiters who may also hold full-stack roles.

Your genuine differentiators, which the plan's fact bank **omitted entirely**, are:

- **EFDP / Ethiopian Ministry of Transport** — large-scale government platform, RBAC, multi-role admin. In the Addis market this is a serious credential and it wasn't in the plan's `profile.yaml` at all.
- **Moogter.com backend** — database modelling and client-server API design for a social platform.
- **Sek'ela SIMS** — education-sector system, plus the only testing/code-review evidence you have.

The plan's YAML listed only Beta Tech Hub, NoDelala and the ML project — it dropped **three of your four employers** and your strongest project. Fixed in `profile.yaml`.

Also correcting two specifics: the Bankon24 score of 87 was credited to "banking-related experience," which rests entirely on `ONETAP_03` — *"Supported Tsedey Bank's USSD initiative and mobile app design."* That's design-adjacent support work, not fintech engineering. And Svelte at 35 was too generous: Svelte, Jenkins and Kubernetes are all hard negatives, now in `absent:` so the matcher rejects confidently instead of hedging.

---

## 5. Two 30-second fixes worth more than a sprint of code

1. **No GitHub URL on your CV.** The channel repeatedly asks for "CV + GitHub + portfolio." You have `tensae.vercel.app` and nothing else. Every application missing it is silently weakened. `profile.yaml` emits `PROFILE_GAP` on every job until you add it.
2. **No metrics anywhere.** Not one number in the whole document — no team size (you were a *Team Leader*), no user counts, no data volumes. "Coordinated sprint planning" vs "Led a team of 5 across 2-week sprints" is free signal you're leaving on the table.

---

## What I'd do next, in order

1. Resolve the Beta Tech Hub dates. Decide what the gap narrative is. *(today, by hand)*
2. Add GitHub. Name the stack in every bullet. Add 3–5 numbers. *(one afternoon)*
3. Re-run `rescore.py` against the updated `profile.yaml` to confirm the lift is real.
4. Cut four static CV variants from the rewritten master.
5. *Then* build the watcher → dedup → extract → alert pipeline from the review.

Steps 1–2 will do more for your response rate than steps 4–5. The system is worth building, but it's an amplifier — and right now it would be amplifying a CV that scores 0.00 evidence-depth against the jobs it's aimed at.

Say the word and I'll build the pipeline, or do a full line-by-line rewrite of the CV first.
