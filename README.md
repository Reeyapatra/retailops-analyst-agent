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
| Parallel investigation, data-quality subgraph, verifier, approvals, API/UI | Not started (M5-M7) |
| M4 Baseline evaluation | Not started |
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
