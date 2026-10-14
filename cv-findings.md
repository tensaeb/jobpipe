# CV findings — updated for new CV (October 2026)

Files updated this session:

| File | What changed |
|---|---|
| `profile.yaml` | OneTap role removed; CTO at Nodelala PLC added; Angular/Flutter/Dart/Spring Boot moved to `listed`; GitHub URL populated; experience totals updated |
| `cv-findings.md` | This file — rewritten to reflect new CV state |
| `matcher.py` | Aliases added: Angular variants, Spring Boot variants, FastAPI, Prisma confirmed |

---

## What changed from the old CV to the new one

| Item | Old CV | New CV |
|---|---|---|
| OneTap Technologies | Present (Sep 2023 – May 2024) | **Removed** |
| Nodelala PLC (CTO) | Absent | **Added — currently employed** |
| Employment gap | 10-month gap (Dec 2025 → Oct 2026) | **Resolved** — CTO role is ongoing |
| GitHub URL | Missing | `https://github.com/tensaeb` — now in profile |
| Angular | `absent` (hard negative) | Moved to `listed` (competencies list) |
| Flutter | `absent` (hard negative) | Moved to `listed` (competencies list) |
| Dart | `absent` (hard negative) | Moved to `listed` (competencies list) |
| Spring Boot | Not in file | Added as `listed` |

The `PROFILE_GAP` flag (no GitHub) that appeared on every job will no longer fire. The `EMPLOYMENT_GAP` critical item is removed — the CTO role is ongoing.

---

## Evidence-tier scoring

`profile.yaml` tiers every skill by the strength of evidence behind it. The matcher weights matches accordingly so a post that aligns only with the competencies list scores lower than one aligned with the experience bullets.

| Tier | Meaning | Weight |
|---|---|---|
| `named` | Named explicitly in an employment bullet | 1.00 |
| `inferred` | Role scope strongly implies it; technology unnamed | 0.70 |
| `project` | Portfolio project only | 0.60 |
| `listed` | Competencies list only — no narrative backing | 0.35 |

`listed` is not a lie — it is an unevidenced claim. A technical screen will probe it. The matcher must not treat it as production depth.

---

## Key strengths (new CV)

**Full-stack web development** — 4+ years across web platforms. React, Node.js, Python, Django are on the competencies list. The CTO and Team Leader roles provide strong inferred evidence for frontend and backend depth even without explicit technology names in bullets.

**Leadership and management** — CTO at Nodelala PLC (Sep 2024 – present) and Team Leader at Beta Tech Hub. Both roles evidence team management, code review, sprint planning, and engineering standards. This is the strongest single upgrade from the old CV.

**Property management platforms** — RBAC, financial tracking, and automated notifications in BETA_01; full-stack property platform in NODELALA_01; cross-platform architecture as CTO.

**E-commerce** — Global Trade House and Efoyita (B2B/B2C) built at Beta Tech Hub.

**Government / public sector** — EFDP Ministry of Transport: large-scale logistics platform, responsive public site, multi-role admin dashboard with RBAC.

**Education tech** — Sek'ela SIMS (Student/School Information Management System) at Line Addis; only role with explicit testing and code-review evidence.

**Mobile / cross-platform** — iOS and Android deployment overseeing releases as CTO (CTO_03). React Native, Flutter, Dart now on competencies list.

**Backend systems** — Moogter.com backend: database model design and client-server APIs for a social platform.

---

## Still-valid warnings

### BLOCK_SENDING — BETA_DATES conflict (critical, unresolved)

The Beta Tech Hub dates conflict remains unresolved and the matcher still appends a `BLOCKED` flag to every job until it is fixed.

| | CV as written | If the letter is right |
|---|---|---|
| Beta Tech Hub tenure | 16 months | 6.5 months |
| Impact on total | Moderate | "4+ years" headline may be unsupportable |

Resolve this manually before any application is sent. No software change resolves it.

### UNNAMED_STACK (critical)

30+ technologies are claimed in Technical Competencies. Very few are named in any experience bullet. React, Next.js, and Node.js — the three skills the target channel asks for most — appear nowhere outside the competencies list.

The matcher will score these at tier `listed` (weight 0.35). A post requiring React + Node.js will produce a shallow match with low evidence depth, flagged as `SHALLOW_EVIDENCE`.

**Highest-ROI CV edit still available:** name the stack in experience bullets. No new claims required — just make explicit what you actually used.

Example rewrites:
- `EFDP_02` → "Developed a responsive public-facing site and multi-role admin dashboard **in React and Next.js** with RBAC..."
- `BETA_01` → "Led end-to-end delivery of a Property Management System **built on [stack]**..."
- `NODELALA_01` → "...full-stack property platform across web **(Next.js)** and mobile **(React Native)**..."

### NO_METRICS (moderate)

Zero quantification in the entire CV — no team size, user counts, latency numbers, or revenue figures. The CTO and Team Leader roles make this especially visible: "Managed and mentored the developer team" vs "Managed a team of N engineers" is free signal left on the table.

### ML_THIN (moderate)

The entire Data & AI competencies line rests on one Streamlit regression project (`ML_01`). TensorFlow and OpenCV have no narrative evidence. AI/ML roles should not be targeted on this CV as written.

---

## Best-fit role targets

Strong fit (APPLY range likely):
- Full-stack engineering roles (React + Node.js + Python stacks)
- Tech Lead / Engineering Manager (CTO credential is strong; team leadership evidenced at two roles)
- Property / real-estate tech platforms
- Frontend roles (React / Next.js)
- Backend roles (Node.js / Django / Python / FastAPI)

Medium fit (REVIEW range; evidence depth improves if stack is named in bullets):
- EdTech / school information systems
- Government / public sector digital platforms
- E-commerce platforms
- Mobile development (React Native / Flutter listed; iOS/Android deployment inferred from CTO role)

Avoid:
- AI / ML engineer roles — thin evidence; one project does not support a senior hire
- Roles requiring Kubernetes, Terraform, Jenkins, Go, Rust, PHP, .NET, Ruby, Swift, Kotlin — all hard negatives

---

## Highest-ROI next actions (in order)

1. **Resolve Beta Tech Hub dates.** Decide what the accurate record is, update the CV, and remove the `BLOCK_SENDING` flag. *(today, by hand)*
2. **Name the stack in experience bullets.** One afternoon of edits moves the majority of target roles from REVIEW/SKIP into APPLY.
3. **Add metrics.** At minimum: team size at Beta Tech Hub and Nodelala PLC, rough scale of platforms (users, records, transactions).
4. Re-run `rescore.py` against the updated `profile.yaml` to confirm the lift is real.
5. Cut updated static CV variants from the rewritten master.
