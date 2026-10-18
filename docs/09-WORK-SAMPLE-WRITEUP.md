# RetailOps Analyst Agent: work sample write-up

Facts only. Anything not built or not measured is labelled as such. Numbers come from files in `evals/reports/`; code facts from the repository at the commit shown by `git log`.

## What it does and who it is for

RetailOps is a multi-agent assistant for retail data and operations teams (analytics engineers, data engineers, operations analysts). It answers metric questions with validated read-only SQL, answers definition and runbook questions with citations or an explicit "I don't know", runs named data-quality checks, and investigates anomalies by combining statistical scanners with the data-quality suite and labelling each shift as a probable data artefact or a probable real business change. All data is synthetic.

## How it is built

- **Stack:** LangGraph (state graphs, `Send` fan-out, `Command` handoff, SQLite checkpointer), LangChain (chat model factory, structured output, tools), LangSmith (metadata and tags wired; dataset sync and `evaluate` runner written, never run), DuckDB, sqlglot, Pydantic v2, pytest, ruff, mypy, uv. Pinned in `pyproject.toml` and `uv.lock` (langgraph 1.2.16, langchain 1.4.6, langsmith 0.14.11, duckdb 1.5.6, sqlglot 30.23.0). Python 3.12.
- **Not built:** FastAPI service, Streamlit UI, Docker image, cloud deployment, verifier, action proposer, human approval, audit-log writer. No public demo exists.
- **Architecture:** a supervisor with five validated intents routes to four specialists (SQL Analyst, Knowledge, Data Quality, Investigation) or a decline. Diagram: see `README.md` (Mermaid; not yet rendered or checked on GitHub).
- **Tools that exist:** `get_schema`, `run_sql` (guarded, read-only, row-capped, timeout), `run_dq_check` (7 named checks), cancellation and demand scanners, `search_knowledge`, `ask_knowledge` (agents-as-tools, budgeted), `get_calendar_context` (Nager.Date API with retry, cache and offline fallback). `propose_action`, `execute_action` and `record_feedback` do not exist yet.
- **Coordination patterns implemented:** supervisor routing; handoff via `Command` (SQL Analyst to Data Quality when result rows look corrupted); agents-as-tools (`ask_knowledge`); parallel fan-out with `Send` and a reducer-based merge in the investigation graph.
- **Memory and RAG:** thread memory through a LangGraph SQLite checkpointer, with follow-up questions resolved by the supervisor into a standalone question (tested with scripted models). RAG over 10 knowledge documents (47 heading-aware chunks, TF-IDF index) with citation validation and layered abstention. The documents were AI-drafted and await the author's review.
- **Safety implemented:** parser-based SQL guard, read-only connection with external access disabled, untrusted-data spotlighting, seeded injection fixtures, bounded retries and budgets, `.env` gitignored.
- **Code size:** about 3,800 lines in `src/`, 2,600 in `tests/`, 1,900 in `evals/` (Python, including blank lines).

## One key design decision and its tradeoff

I compared (by design) a single agent holding every tool (E0) against a supervisor with specialists (E1), both scored by the same harness on the same 31 gold questions.

- **Result:** not yet measured. Both systems are built and their plumbing is tested with scripted models, but no real-model run has completed (the first attempt stopped at an API billing error), so I make no claim about which is more accurate, cheaper or slower.
- **What is measured:** the parallel fan-out in the investigation graph runs the tool workers concurrently: p50 0.71 s sequential vs 0.55 s parallel (1.29x), 10 repeats, model calls excluded. The data-quality suite dominates the wall time, which caps the gain.
- **Tradeoff built in:** investigation workers are deterministic tools, not model agents. Detection is measurable without a model and cannot be hallucinated; flexibility is lower than free-form SQL.
- **When the simpler design may be better:** unknown until E0 and E1 are measured. Do not state a conclusion before then.

## How it was tested and evaluated

| Experiment | What changed | SQL exec accuracy | Anomaly recall | Groundedness | Safety pass | p50 latency | Cost/req |
|---|---|---|---|---|---|---|---|
| E0 | Single-agent baseline | not yet measured | not yet measured | not yet measured | not yet measured | not yet measured | not computed |
| E1 | Multi-agent | not yet measured | not yet measured | not yet measured | not yet measured | not yet measured | not computed |
| Detectors only (no model) | 7 DQ checks + 2 scanners | n/a | 7 / 7, also on seeds 1-5 | n/a | n/a | 0.49 s | n/a |

- **Dataset sizes:** `sql_gold` 31; `rag_gold` 36 (28 answerable, 8 not); `routing_smoke` 18; anomaly manifest 7 anomalies + 2 decoys. The `anomaly_gold`, `safety`, `trajectory` and `multi_turn` datasets from the evaluation plan are not built.
- **Repeats:** none for model runs. Detector results were repeated across 6 seeds.
- **Offline tests:** 223 automated tests (guard, data generator and anomaly detectability, retrieval, graph orchestration, evaluators, harness). They use scripted model doubles and prove control flow, not model quality.
- **Retrieval:** hit@5 1.00, MRR 0.946, abstention on unanswerable questions 0.25 (poor; layered abstention is the mitigation and is unmeasured).
- **Limitations:** small synthetic data with a clean baseline; detector thresholds chosen knowing the injected anomalies; gold and retrieval questions written by the same party as the data and documents; rule-based groundedness checks numbers only; no LLM judge exists, so no calibration (kappa); scanner window edges approximate to about 2 days; CI not yet run on GitHub.

## Demo / deployment

Runs locally (`make setup`, `make data`, `make test`). No public demo or deployment link. No LangSmith trace has been produced, so there is no trace link.

## What I personally built

**Author to complete.** The repository was produced with Claude Code under the author's direction. Replace this paragraph with an accurate account of what you specified, reviewed, tested, changed or redesigned yourself, and which parts you can explain from memory. Suggested list to annotate (delete what you did not do):

- `src/retailops/guards/sql_guard.py` and `tests/unit/test_sql_guard.py`: SQL validation rules and tests
- `src/retailops/data/generate.py`: seeded generator with injected anomalies and decoys
- `src/retailops/graph.py`, `agents/investigation.py`: routing, handoff, fan-out and merge
- `src/retailops/tools/dq.py`, `tools/metrics.py`: checks and scanners
- `evals/`: datasets, evaluators, experiment runner, reports
- `knowledge/*.md`: **drafted by AI; the author must review before presenting as their own**

## Roadmap (clearly not built)

Real-model runs and the E0/E1 comparison; routing and abstention measured with a model; LangSmith traces and experiment views; verifier (E2); action proposer, interrupt-based approval, audit log and the unreachable-`execute_action` test (M6); FastAPI, streaming, UI, Docker (M7); cloud deployment, rate limit, budget cap (M8); ablations, model tiering, judge calibration, repeats with ranges (M9); three debugging case studies from real traces (M10, none exist yet).
