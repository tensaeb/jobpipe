"""Re-score the roles from the original plan's section 2 table, using the
real CV fact bank and evidence tiers. Prints a corrected comparison."""

from jobpipe.matcher import Matcher, Job, cv_variant

# Reconstructed from the plan's section-2 descriptions of the visible posts.
JOBS = [
    Job("wagergeeks", "Frontend Developer (React + Next.js)", "WagerGeeks",
        required=["React", "Next.js", "TypeScript", "Tailwind", "REST APIs", "Git"],
        preferred=["AI tools", "UI/UX"], min_years=2),
    Job("redesigned", "Full Stack Developer", "Redesigned Agency",
        required=["React", "Next.js", "Node.js", "REST APIs", "PostgreSQL"],
        preferred=["AI integrations", "Docker"], min_years=3,
        application_email="business@redesigned.agency"),
    Job("zaimah", "Full Stack Engineer", "Zaimah",
        required=["React", "Next.js", "Node.js", "Express", "MySQL", "Prisma"],
        preferred=["e-commerce"], min_years=3),
    Job("telecom", "Freelance Full Stack (telecom/e-commerce)", None,
        required=["Next.js", "Node.js", "NestJS", "PostgreSQL", "Docker"],
        preferred=["AWS", "e-commerce"], min_years=3),
    Job("bankon24", "Full Stack Developer (AI-first)", "Bankon24",
        required=["Full-stack development", "REST APIs", "PostgreSQL", "Docker", "CI/CD"],
        preferred=["fintech"], min_years=3,
        salary_raw="$1,600–$2,050/month", salary_confidence="medium"),
    Job("codebase", "Junior Full Stack Developer", "Codebase Solutions",
        required=["JavaScript", "React", "Node.js", "SQL"],
        preferred=["Git"], min_years=1,
        salary_raw="$400–$650/month", salary_confidence="medium"),
    Job("rn_freelance", "React Native Developer (freelance)", None,
        required=["React Native", "JavaScript", "REST APIs"],
        preferred=["Redux Toolkit"], min_years=2),
    Job("codingcops", "AI/ML Senior Software Engineer", "CodingCops",
        required=["Python", "TensorFlow", "Scikit-learn", "Pandas", "NumPy"],
        preferred=["FastAPI", "Docker"], min_years=5),
    Job("svelte", "Svelte Frontend Developer", None,
        required=["Svelte", "JavaScript", "Jenkins", "Kubernetes"],
        preferred=["Docker"], min_years=3),
    Job("angular", "Angular Frontend Developer", None,
        required=["Angular", "TypeScript", "RxJS"], min_years=3),
    Job("qa", "Manual QA Engineer", None,
        required=["Unit testing", "Jira", "test cases"], min_years=2),
    Job("rn_senior", "Senior React Native Developer", None,
        required=["React Native", "Mobile development", "JavaScript"], min_years=6),
]

PLAN_SCORES = {
    "wagergeeks": 93, "redesigned": 91, "zaimah": 90, "telecom": 89,
    "bankon24": 87, "codebase": 84, "rn_freelance": 68, "codingcops": 75,
    "svelte": 35, "angular": 30, "qa": 25, "rn_senior": 35,
}

m = Matcher("profile.yaml")
results = sorted((m.match(j) for j in JOBS), key=lambda r: -r.score)

print(f"{'role':<42}{'plan':>6}{'real':>6}{'Δ':>6}  {'bucket':<14}{'depth':>6}")
print("-" * 86)
for r in results:
    plan = PLAN_SCORES[r.job.id]
    print(f"{r.job.title[:41]:<42}{plan:>6}{r.score:>6}{r.score-plan:>+6}  "
          f"{r.bucket:<14}{r.evidence_depth:>6.2f}")

print("\n\nDETAIL — top candidates and notable corrections\n" + "=" * 86)
for r in results:
    if r.job.id not in ("wagergeeks", "redesigned", "codingcops", "codebase", "bankon24"):
        continue
    print(f"\n{r.job.title} @ {r.job.company or '—'}   [{r.bucket}]  score {r.score}")
    print(f"  CV variant : {cv_variant(r)}")
    print(f"  matched    : " + ", ".join(f"{s}({t})" for s, t, _ in r.required_hits) or "  matched    : none")
    if r.required_misses:
        print(f"  missing    : " + ", ".join(r.required_misses))
    if r.blockers:
        print(f"  BLOCKERS   : " + "; ".join(r.blockers))
    for f in r.flags:
        print(f"  flag       : {f}")
