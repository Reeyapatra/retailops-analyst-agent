# 08 – Work Sample Write-up Template (for the application form)

The form asks for: what the agent does and who it is for; how it is built; one key design decision and its tradeoff; how it was tested or evaluated; a demo or deployment link; and, if it was a team project, what you personally built. Fill every bracket with real facts. Delete anything you did not build or measure. A public repo README that covers these is sufficient.

---

## RetailOps Analyst Agent

**What it does and who it is for**
RetailOps is a multi-agent assistant for retail data and operations teams. It answers metric questions with validated read-only SQL, investigates revenue and cancellation anomalies, runs data quality checks, cites runbooks and metric definitions, and proposes remediation actions that require human approval. All data is synthetic.

**How it is built**
- Framework: LangGraph orchestration, LangChain components, LangSmith tracing and evaluation, DuckDB, FastAPI, Streamlit, Docker, [cloud platform].
- Architecture: [single or multi-agent: supervisor with SQL, data quality and knowledge specialists]. [Diagram image link]
- Tools: [list the tools that actually exist].
- Memory and RAG: [checkpointer-backed thread memory; RAG over N knowledge documents with citation and abstention].
- Safety: [SQL guard rules implemented; read-only connection; approval before actions; injection fixtures].

**One key design decision and its tradeoff**
I compared a single agent with all tools against a supervisor with specialists. [Fill after running E0 vs E1: what improved, what got worse, cost and latency difference, and the situations where the simpler design is better.]

**How I tested and evaluated it**
| Experiment | What changed | SQL exec accuracy | Anomaly recall | Groundedness | Safety pass | p50 latency | Cost/req |
|---|---|---|---|---|---|---|---|
| E0 | Single-agent baseline | | | | | | |
| E1 | Multi-agent | | | | | | |
Dataset sizes: [fill]. Repeats: [fill]. Limitations: [for example, small synthetic dataset, judge calibration agreement of X on 30 labels].

**Demo / deployment**
[URL or "runs locally with `make dev`; demo link planned"]. [LangSmith public trace link if available.]

**What I personally built**
List files, features and components. Example format:
- `src/retailops/guards/sql_guard.py`: SQL validation rules and tests
- `src/retailops/graph.py`: top-level graph, routing and fan-out
- `evals/`: datasets, evaluators, experiment runner
- [add or remove to match reality; state clearly which parts were drafted with AI assistance and what you reviewed, tested or redesigned yourself]

**Roadmap (clearly marked as not yet built)**
[Items from the build plan that remain.]

---

## Debugging case study template (build three from real traces)
**Case N: [short title]**
- Symptom (what the user saw)
- Trace link and the step where it went wrong
- Root cause
- Fix (code or prompt change) and the experiment showing the change helped
- How I would coach a student team to find this themselves (questions to ask, not answers to give)

Suggested cases: a hallucinated column that the retry loop fixed; a routing mistake between knowledge and SQL; a data-borne injection attempt the agent ignored; a verifier catch of an ungrounded number.

## Interview preparation
Prepare short answers for:
1. Walk me through the graph from request to final answer.
2. Why a supervisor? When would you not use one?
3. How did you evaluate beyond "it looks right"?
4. What did your evaluation not cover?
5. How does human approval work technically?
6. Show a failure and how you diagnosed it in the trace.
7. A student team's agent loops forever. How do you coach them?
8. A student's RAG answers are fluent but wrong. What do you ask first?
9. How would you give feedback on a teammate's weak evaluation design?
10. Coaching exercise: pick one concept (for example, tool-calling or evals) and be ready to lead a 10-minute guided activity with a check for understanding.
