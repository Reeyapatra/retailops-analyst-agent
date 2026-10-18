# 01 – Product Requirements: RetailOps Analyst Agent

## 1. Summary
RetailOps is a multi-agent system that answers questions about retail order data, investigates anomalies, checks data quality, and proposes remediation actions that a human must approve. It is built with LangGraph, traced and evaluated in LangSmith, containerized with Docker, and deployed to the cloud.

It exists for two reasons: to be a genuinely useful operations assistant for a retail data team, and to be a rigorous, well-measured example of the agent practices taught in the Break Through Tech Agentic AI program (tool use, RAG, memory, supervisor patterns, evaluation, observability, deployment).

## 2. Users and problem
**Users:** analytics engineers, data engineers and operations analysts at an online retailer.
**Problem:** when revenue dips or cancellations spike, people jump between SQL consoles, dashboards, runbooks and chat to find the cause. Pipeline issues (duplicate or late events) quietly corrupt metrics. Answers are slow and hard to verify.
**Solution:** one conversational agent that runs checked SQL, runs data quality checks, cites runbooks and metric definitions, and never takes action without approval.

## 3. Goals
- G1: Correct answers to metric questions, verified by execution against a known dataset.
- G2: Detect injected anomalies and name the likely root cause, scored against ground truth.
- G3: Every factual claim is grounded in a tool result or a cited document.
- G4: Safe by design: read-only data access, human approval for actions, resistance to prompt injection in data.
- G5: Fully observable: every run traced; every change measured by an evaluation experiment.
- G6: Deployed with a public demo, reproducible locally in one command.

## 4. Non-goals
- No real customer data; all data is synthetic.
- No real infrastructure changes; remediation actions are simulated and audit-logged.
- Not a general chatbot; out-of-scope questions are declined politely.

## 5. Functional requirements
| ID | Requirement |
|---|---|
| FR-1 | Natural-language metric questions (revenue, orders, AOV, cancellation rate, delivery time, top products, by region and period) answered via generated, validated, read-only SQL. |
| FR-2 | SQL self-correction: on error or empty/suspicious result, retry up to N times with the error fed back. |
| FR-3 | Data quality checks as tools: duplicate events, null or invalid fields, late-arriving events, impossible event sequences, schema drift. |
| FR-4 | RAG over a knowledge base (metric definitions, event schema, runbooks, policy) with source citations and "I don't know" when unsupported. |
| FR-5 | Supervisor routes each request to the right specialist(s) and can hand off between them. |
| FR-6 | Anomaly investigation fans out to revenue, cancellation and data-quality specialists in parallel and merges findings. |
| FR-7 | Conversation memory per thread so follow-ups work ("and for the West region?"). |
| FR-8 | Action proposals (quarantine events, open ticket, notify owner) pause for explicit human approval; approve/reject is recorded in an audit log. |
| FR-9 | Verifier step checks that numbers in the final answer appear in tool outputs; flags or repairs ungrounded claims. |
| FR-10 | Every response returns: answer, evidence (SQL + rows or doc citations), confidence note, and a trace link. |
| FR-11 | Guardrails: reject destructive or out-of-allowlist SQL, ignore instructions found inside data or documents. |
| FR-12 | API and UI: chat endpoint with streaming, approval endpoint, health endpoint, and a simple UI showing steps, evidence and approvals. |
| FR-13 | Multi-agent coordination patterns, all three demonstrated: (a) supervisor routing, (b) direct specialist-to-specialist handoff (for example the SQL Analyst hands off to Data Quality when results look corrupted), (c) agents-as-tools (the SQL Analyst can call the Knowledge agent as a tool to look up a metric definition mid-query). |
| FR-14 | At least one real external API integration (for example a public holidays or calendar API) used to explain demand changes, with timeout, retry, caching and graceful fallback when the API is unavailable. |
| FR-15 | User feedback capture (thumbs up/down plus optional comment) attached to the LangSmith run, and feedback-driven additions to the evaluation set. |

## 6. Non-functional requirements
- Latency target: simple metric question p50 under 10 s; anomaly investigation under 45 s (targets, to be measured and reported honestly).
- Cost: per-request token and cost tracking; demo has a daily budget cap.
- Reproducibility: seeded data generation, pinned dependencies, single `make` commands.
- Security: no secrets in repo, read-only DB connection, rate limiting on the public demo.
- Quality: type hints, ruff clean, pytest suite, CI on every push.

## 7. JD traceability (Agentic AI Coach, Advanced track)
| JD requirement | Where it is satisfied |
|---|---|
| Agent with tool calling | SQL, data-quality, metrics, retrieval, action tools (FR-1, 3, 4, 8) |
| RAG | Knowledge agent with citations (FR-4) |
| Memory | Checkpointer-backed thread memory (FR-7) |
| LangChain + LangGraph | Entire orchestration layer |
| Subgraphs and parallel execution | Specialist subgraphs; fan-out with `Send` (FR-6) |
| Supervisor / orchestrator workflows | Supervisor routing (FR-5, FR-13a) |
| Agent handoffs | Specialist-to-specialist handoff (FR-13b) |
| Agents-as-tools | Knowledge agent exposed as a tool (FR-13c) |
| API integrations | External API tool with fallback (FR-14) plus the FastAPI service (FR-12) |
| Systematic evaluation | Doc 04: datasets, metrics, baseline comparison |
| LangSmith tracing | All runs traced; experiments compared |
| Docker and cloud deploy | Doc 06 |
| API integrations | FastAPI service, simulated ticketing/notification tools |
| Teaching and feedback | Debugging case studies and walkthrough in Doc 08 |

## 8. Scope tiers
- **Tier 1 (submission slice):** data generator + DuckDB, SQL agent with guard, knowledge agent (RAG), supervisor, thread memory, LangSmith tracing, an evaluation set with real baseline numbers, README with diagram, local Docker run.
- **Tier 2:** parallel anomaly investigation subgraphs, data quality agent, verifier, human-in-the-loop actions, UI, cloud deployment, CI eval gate.
- **Tier 3 (stretch):** dbt models over DuckDB, calibrated LLM-judge report, long-term memory store, red-team test suite for injection, cost dashboard, and a second, real public e-commerce dataset (verify its license first) to show the agent generalizes beyond synthetic data.

## 9. Success metrics (targets; report actuals only)
- SQL execution accuracy on the gold set: target 85%+ for the multi-agent system; report the baseline honestly even if lower.
- Anomaly detection recall on injected anomalies: target 80%+.
- Groundedness (verifier + judge): target 90%+.
- Guardrail pass rate on adversarial set: target 100% for destructive SQL, 95%+ for injection resistance.
- Trace coverage: 100% of runs traced.
