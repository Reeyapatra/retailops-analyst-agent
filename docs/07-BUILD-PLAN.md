# 07 – Build Plan

Time estimates assume you drive Claude Code and review everything. Work in small commits; run tests after each milestone.

## Submission slice (target: submit the application tonight, Oct 18)
Aim for M0–M4 in a minimal form. If time runs short, cut scope from the bottom of this list, never the evaluation numbers or the README honesty.

| Milestone | Deliverable | Acceptance criteria | Est. |
|---|---|---|---|
| M0 Setup | Repo, env, CI skeleton, Makefile, CLAUDE.md, docs committed | `make test` and `make lint` pass on an empty-but-structured repo | 1 h |
| M1 Data | Generator, DuckDB load, anomaly manifest, schema catalog | `make data` is deterministic; manifest lists anomalies; row counts sane; unit tests on generator | 2 h |
| M2 SQL agent | Schema tool, SQL guard, `run_sql`, text-to-SQL with retry, evidence output | Guard unit tests (destructive, multi-statement, non-allowlisted tables) all rejected; 20 gold questions answered; traced in LangSmith | 3 h |
| M3 Knowledge + supervisor | Knowledge docs, ingest, retriever with citations, supervisor routing, thread memory | Routing correct on a 15-question smoke set; follow-up question works; abstains on unknown | 3 h |
| M4 Baseline eval | `sql_gold` (at least 30 questions), single-agent baseline E0 vs multi-agent E1, results table | Real numbers in `evals/reports/`; failure analysis written | 2 h |
| Write-up | README, diagram, work-sample write-up (Doc 08) | Public repo link ready to paste into the application | 1 h |

## After submission (before the take-home and interview)
| Milestone | Deliverable | Acceptance criteria |
|---|---|---|
| M5 Parallel investigation | Data quality subgraph, fan-out with `Send`, merge reducer, specialist-to-specialist handoff, knowledge agent as a tool, external calendar API tool with fallback | Investigates injected anomalies; recall and false-alarm numbers reported (E3) |
| M6 Verifier + actions + HITL | Verifier, action proposer, interrupt approval, audit log | Test proves no action without approval; groundedness change measured (E2) |
| M7 API + UI | FastAPI, streaming, approval endpoint, Streamlit UI | Full demo flow works locally in Docker |
| M8 Deploy | Cloud Run (or fallback), budget cap, rate limit, CI deploy | Public URL works; smoke test passes in CI |
| M9 Rigor | Judge calibration, ablations E4, model tiering E5, 3 repeats | Report with ranges; limitations section |
| M10 Teaching assets | 3 debugging case studies, 15-minute walkthrough outline | Each case links a real trace |

## Learning checkpoints (you must be able to explain these live)
After each milestone, close the editor and explain it out loud for two minutes:
1. Why this state schema, and what each reducer does.
2. How the SQL guard works and one thing it cannot catch.
3. How routing decisions are made and what happens when classification is wrong.
4. How `Send` fan-out merges results and why order is not guaranteed.
5. How the interrupt/resume mechanism uses the checkpointer.
6. Why the judge can be wrong and how you calibrated it.
7. Where the multi-agent design lost to the baseline, if it did.
If you cannot explain a part, ask Claude Code to walk you through it before moving on. The interview includes walking through your work and leading a coaching exercise.

## Working agreements with Claude Code
- One milestone per session; start by reading `CLAUDE.md` and the relevant doc.
- Tests first for guards and evaluators.
- After each milestone: run tests, run lint, commit with a clear message, update the README status table.
- Never mark a milestone done without running its acceptance checks.
- Keep a `docs/DECISIONS.md` log: date, decision, alternatives, reason. These become interview answers.

## Risk register
| Risk | Mitigation |
|---|---|
| Time: not enough for everything tonight | Submission slice only; honest "roadmap" section |
| API cost during evals | Cheaper models for dev, cached responses for smoke tests, full evals sparingly |
| LangGraph/LangSmith API changes | Pin versions; read current docs before coding each component |
| Flaky evals | Temperature 0, repeats, report ranges |
| Over-claiming | Only report measured results; label planned work as planned |
