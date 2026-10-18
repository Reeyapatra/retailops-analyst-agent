# RetailOps Analyst Agent

Multi-agent retail operations assistant built with LangGraph, traced and evaluated with LangSmith. All data is synthetic. Work sample in progress; see the status table for what actually exists.

## Status

| Feature | Status |
|---|---|
| M0 Setup (layout, tooling, CI skeleton) | Done and verified locally (make test, make lint). CI not yet run on GitHub |
| M1 Data (generator, DuckDB, manifest, catalog) | Done and verified locally (make data, 25 tests) |
| M2 SQL guard, run_sql, get_schema | Done and verified (129 tests, 80 for the guard) |
| M2 text-to-SQL subgraph (generate, guard, execute, check, retry, summarize) | Control flow verified with a scripted model only; **not yet run with a real model** |
| M2 gold set (31 questions, gold SQL and results) | Done and verified (gold SQL reproduces from the seed) |
| M2 execution accuracy of the agent on gold questions | Not yet measured (needs MODEL_SQL and an API key) |
| M2 LangSmith tracing | Metadata/tags wired in code; no trace has been produced yet |
| M3 Knowledge base (10 docs, AI-drafted) | Drafted; **pending your review** (front matter says `draft-pending-author-review`) |
| M3 Chunking, TF-IDF index, retriever with abstention threshold | Done and verified (tests; retrieval measured, see below) |
| M3 Knowledge agent (citations validated, abstains) | Control flow verified with a scripted model only; not run with a real model |
| M3 Supervisor (structured routing, safe fallback) and top-level graph | Control flow verified with a scripted model only; not run with a real model |
| M3 Thread memory (SQLite checkpointer) and follow-ups | Verified with scripted models (history reaches supervisor, thread isolation) |
| M3 Routing accuracy on the 18-case smoke set | Not yet measured (needs MODEL_SUPERVISOR and an API key) |
| M3 Dense embeddings (fastembed, `--extra rag`) | Code written, **never run**; downloads a model on first use |
| M5 Data-quality checks (7 named, parameter-validated) and metric scanners (cancellation spike, demand drop) | Done and verified; scored against the manifest (see below) |
| M5 Data Quality agent (validated check plan, fallback to all checks) | Control flow verified with a scripted model; not run with a real model |
| M5 Parallel investigation with `Send` fan-out, merge reducer, cause classification | Verified with a scripted model; concurrency tested; narration quality not measured |
| M5 Handoff SQL Analyst -> Data Quality via `Command` | Done and verified (scripted model, real SQL and checks) |
| M5 Agents-as-tools: `ask_knowledge` with call budget | Done and verified (scripted models) |
| M5 Calendar API tool (timeout, retry, cache, fallback) | Done and verified with a mocked transport; endpoint schema confirmed by one live call on 2026-10-18 |
| M5 Anomaly *investigation* quality with a real model (E3) | Not yet measured |
| Verifier, action proposer, human approval, audit log (M6); API and UI (M7); cloud deploy (M8) | Not started |
| M4 Evaluators (execution accuracy, numeric groundedness, citation validity) and failure classification | Done and verified with unit tests |
| M4 E0 baseline agent and E1 multi-agent targets, experiment runner, report generator, LangSmith dataset sync | Built; plumbing verified with scripted models. LangSmith `evaluate` path never run |
| M4 E0 vs E1 results, failure analysis | **Not yet measured** (needs API key). `evals/reports/results.md` shows every cell as not yet measured |
| Evaluation results | Not yet measured |

## Quickstart

```
make setup
make data
make test
make lint
```

Docs: `docs/01-PRD.md` to `docs/08-WORK-SAMPLE-WRITEUP-TEMPLATE.md`. Decisions: `docs/DECISIONS.md`.

## Measured so far (no model involved)

Retrieval on `evals/datasets/rag_gold.jsonl` (28 answerable + 8 unanswerable questions written by the author of the knowledge base, TF-IDF index, 47 chunks, k=5, threshold 0.12). Source: `evals/reports/rag_retrieval.json`.

| Split | hit@5 | MRR | Answered when answerable | Abstained when unanswerable |
|---|---|---|---|---|
| All (n=28 / 8) | 1.00 | 0.946 | 0.964 | 0.25 |
| Dev (21 / 6) | 1.00 | 0.952 | 0.952 | 0.33 |
| Held-out (7 / 2) | 1.00 | 0.929 | 1.00 | 0.00 |

Reading this honestly: the questions use the documents' own vocabulary and the sets are small, so hit@5 is optimistic. The relevance score cannot separate unanswerable questions (they score up to 0.27 because words like "region" match) from answerable ones (as low as 0.11), so retrieval-level abstention is weak. The knowledge agent therefore also lets the model decline (`answerable=false`) and drops answers without valid citations; how well that works is not yet measured.

## Running the real experiments (needs your keys)

1. `copy .env.example .env`, then set `ANTHROPIC_API_KEY`, confirm the three `MODEL_*` names are valid for your account, and optionally `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT=retailops-eval`.
2. `make data` then `make ingest`.
3. Cheap checks first: `uv run python -m evals.run_sql_gold --limit 3` and `uv run python -m evals.run_routing_smoke`.
4. Full: `make eval-full` (E0 and E1, 3 repeats each over 31 questions, then the results table). Add `--langsmith` to the `run_eval` commands to sync the dataset and log experiments named `retailops-E#-<git sha>`.

Expect roughly 31 x 3 x 2 system runs; check provider pricing first. Results are written to `evals/reports/runs/` and summarised in `evals/reports/results.md`; the failure analysis must be written from those runs, not before.

## Measured offline: anomaly detectors (no language model)

`python -m evals.run_detectors --sweep-seeds 1,2,3,4,5 --write-report` scores the deterministic tools (7 DQ checks + 2 statistical scanners) against the ground-truth manifest. Source: `evals/reports/anomaly_detectors.json`.

| Dataset | Anomalies found | Scope correct | Cause class correct (data vs business) | Precision | Decoy false alarms |
|---|---|---|---|---|---|
| Seed 42 (the default data) | 7 / 7 | 7 / 7 | 7 / 7 | 8 findings, all explained | 0 / 2 |
| Seeds 1-5 (same settings, regenerated data) | 7 / 7 each | all | all | all explained | 0 each |

How to read this: the data is synthetic with a clean baseline, so a perfect score shows the tools work on this generator, not that they would on messy production data (real data needs seasonality-aware baselines and noisy thresholds). Window edges from the rolling scanners are approximate (about 2 days). The scanners' thresholds were chosen with knowledge of the injected magnitudes; the multi-seed sweep checks seed overfitting only. This measures the tools, not an agent's investigation, which is not yet measured.

Investigation worker latency, parallel `Send` fan-out vs the same workers in sequence (10 repeats, model calls scripted so they are excluded): p50 0.71 s sequential vs 0.55 s parallel (1.29x), p95 0.86 s vs 0.61 s. The gain is modest because the data-quality suite dominates and the other workers are short. Source: `evals/reports/parallel_latency.json`. End-to-end latency with a real model is not yet measured.
