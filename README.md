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
| M3 Knowledge + supervisor | Not started |
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
