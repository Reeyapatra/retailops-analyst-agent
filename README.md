# RetailOps Analyst Agent

Multi-agent retail operations assistant built with LangGraph, traced and evaluated with LangSmith. All data is synthetic. Work sample in progress; see the status table for what actually exists.

## Status

| Feature | Status |
|---|---|
| M0 Setup (layout, tooling, CI skeleton) | Done and verified locally (make test, make lint). CI not yet run on GitHub |
| M1 Data (generator, DuckDB, manifest, catalog) | Done and verified locally (make data, 25 tests) |
| M2 SQL agent | Not started |
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
