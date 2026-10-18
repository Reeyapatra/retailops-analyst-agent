# 05 – Security and Guardrails

## 1. Threat model
| Threat | Example | Control |
|---|---|---|
| Destructive or exfiltrating SQL | A request to drop a table or read system files | SQL guard + read-only connection + table allowlist |
| Prompt injection via data | Instruction-like text in `notes` or product names | Treat tool and retrieval output as untrusted data; spotlighting; tool permissions independent of model output |
| Prompt injection via documents | Runbook text that tries to change agent behavior | Same as above; knowledge base reviewed and versioned |
| Unauthorized actions | Agent executes remediation without consent | Interrupt-based approval; `execute_action` unreachable without approved state |
| Secret leakage | API keys in repo or traces | `.env` only, `.env.example` committed, secret scanning in CI, redact before logging |
| Cost abuse on public demo | Loops or spam | Step/token budgets, rate limiting, daily spend cap |
| Hallucinated numbers | Confident but wrong figures | Verifier step; numeric grounding check; evidence always shown |

## 2. SQL guard rules (implement with a SQL parser such as sqlglot)
1. Parse the statement; reject on parse failure.
2. Exactly one statement; reject multiple statements.
3. Allow only `SELECT` (and `WITH` CTEs that end in `SELECT`).
4. Reject any DDL/DML/DCL, `COPY`, `ATTACH`, `INSTALL`, `LOAD`, `PRAGMA`, file-reading table functions, and external access functions.
5. Table allowlist: only warehouse tables and views from the catalog.
6. Inject or enforce a `LIMIT` (for example 1000) and a statement timeout.
7. Open DuckDB with `read_only=True` for the analytics connection; the audit log lives in a separate database file with a different connection.
8. Return a structured rejection reason to the agent; log every rejection with trace ID.

## 3. Prompt injection defenses
- Wrap all tool and retrieval output in clearly delimited blocks and instruct the model that the content is data, never instructions.
- The supervisor and tool permissions never depend on text found in data.
- Do not give any tool the ability to modify guards, prompts, or permissions.
- Strip or flag instruction-like patterns in retrieved text as a signal (not as the only defense).
- Safety evaluation set (Doc 04) must include data-borne injection; target zero behavior change.

## 4. Human-in-the-loop approval
- `action_proposer` creates a proposal record with rationale and evidence.
- Graph pauses via interrupt; the UI shows the proposal; a human approves or rejects through the API.
- On approval, `execute_action` runs the simulated side effect and writes to `action_audit_log` (who, when, what, trace ID).
- On rejection or timeout, no action runs and the answer notes it.
- Test: a unit test must prove `execute_action` cannot run from any path without an approved flag.

## 5. Privacy
- Synthetic data only; document this in the README.
- No real names, emails or addresses in the generator.
- Traces may contain prompts and results; keep demo data synthetic so traces are safe to share.

## 6. Secrets and supply chain
- Never commit keys; `.env` gitignored; `.env.example` with placeholders.
- Pin dependencies (lock file); enable dependency update checks.
- CI runs a secret scanner and a dependency vulnerability check.
- Docker image: non-root user, minimal base image, no build tools in final stage.

## 7. Public demo hardening
- Rate limit per IP and per session; maximum message length.
- Global daily budget; when exceeded, the demo returns a friendly "demo limit reached" and a pointer to recorded traces.
- No file upload; no arbitrary URL fetching.
- Clear banner: "synthetic data, demo only".

## 8. Responsible-use notes for the write-up
- Describe the guard design and what it does not cover (for example, a read-only parser can still be bypassed by an unknown dialect edge case; defense in depth reduces but does not eliminate risk).
- State limitations of the evaluation set.
