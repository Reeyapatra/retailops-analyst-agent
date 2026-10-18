# 06 – Deployment and Operations

## 1. Local development
- Python 3.11 or newer; dependency manager `uv` (or pip-tools) with a committed lock file.
- `make setup` (create env, install), `make data` (generate synthetic data and load DuckDB), `make ingest` (build vector store), `make dev` (API + UI), `make test`, `make lint`, `make eval-smoke`, `make eval-full`.
- `.env.example` documents every variable.

## 2. Environment variables
| Variable | Purpose |
|---|---|
| `LLM_PROVIDER`, `MODEL_SUPERVISOR`, `MODEL_SQL`, `MODEL_JUDGE` | Model selection |
| provider API key variables | Credentials (never committed) |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | Tracing (confirm current variable names in LangSmith docs) |
| `DUCKDB_PATH`, `AUDIT_DB_PATH`, `VECTORSTORE_PATH`, `CHECKPOINT_DB_PATH` | Data locations |
| `MAX_STEPS`, `MAX_TOKENS_PER_RUN`, `DAILY_BUDGET_USD` | Cost and loop controls |
| `RATE_LIMIT_PER_MIN` | Demo protection |
| `APP_ENV` | dev, eval, prod |

## 3. Containers
- Multi-stage Dockerfile: builder installs dependencies; final image is slim, non-root, with a health check.
- Data baked in or mounted: for the demo, generate the DuckDB file and vector store during build so the container starts instantly and deterministically.
- `docker-compose.yml`: services `api` and `ui`, shared volume for checkpoint DB, env file for secrets.
- Image tags include the git SHA.

## 4. API surface (FastAPI)
- `POST /chat` (streams steps and tokens; accepts `thread_id`, `message`)
- `POST /approve` (accepts `proposal_id`, `decision`, `thread_id`; resumes the paused graph)
- `GET /threads/{id}` (history)
- `GET /health` and `GET /ready`
- Request/response schemas with Pydantic; correlation ID on every request; structured JSON logs.

## 5. Cloud deployment
Primary suggestion: **Google Cloud Run** (container-native, scales to zero, simple secrets). Fallback for speed: Render or Fly.io.
Steps:
1. Build and push the image to the registry.
2. Create the service with environment variables and secrets from the secret manager.
3. Set concurrency, memory, request timeout (agent runs can be tens of seconds), and max instances (cost cap).
4. Persist the checkpoint database: use a managed Postgres or a mounted volume; note that scale-to-zero containers have ephemeral disks, so do not rely on local SQLite for approvals in cloud. Document the choice.
5. Put the UI on its own service or the same container behind a path.
6. Record the public URL in the README. Confirm the budget cap and rate limit are active.

## 6. CI/CD (GitHub Actions)
- On every push: install, lint (ruff), type check (mypy on core modules), unit and integration tests, `make eval-smoke`, secret scan.
- On tag or main: build image, push, deploy to staging, run smoke test against staging, then promote.
- Cache dependencies; keep workflow under 10 minutes.

## 7. Observability and operations
- LangSmith for traces and experiment comparison; link trace IDs in API responses.
- Metrics to log per request: latency, tokens, cost, tools used, guard rejections, approval outcomes.
- Basic alerting: error rate spike, budget at 80%, repeated guard rejections from one client.
- Runbook for the project itself (`docs/OPERATIONS.md`): how to rotate keys, rebuild data, roll back an image.

## 8. README structure (hiring-team friendly)
1. One-paragraph summary and demo link
2. Architecture diagram
3. Key design decision and tradeoff (with measured results)
4. Evaluation results table and how to reproduce
5. Safety design and limitations
6. Quickstart (3 commands)
7. Project structure
8. What I personally built (see Doc 08)
9. Roadmap
