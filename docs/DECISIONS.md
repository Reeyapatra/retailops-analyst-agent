# Decisions log

| Date | Decision | Alternatives | Reason |
|---|---|---|---|
| 2026-10-18 | Python 3.12 via uv (`requires-python >=3.11,<3.14`) | System Python 3.14 | 3.14 is too new for the ML/vector dependencies planned for M3; 3.12 is widely supported. |
| 2026-10-18 | Anthropic as default provider via `langchain-anthropic`, model names from env vars | OpenAI | User choice; the model factory stays provider-agnostic (Doc 02 §6). |
| 2026-10-18 | GNU Make (ezwinports) installed on Windows | PowerShell task wrapper | Keeps the Makefile the kit requires. |
| 2026-10-18 | Core dependencies pinned with `==` to versions found on PyPI; `uv.lock` pins the rest | Ranges only | Reproducibility (CLAUDE.md rule 1). |
| 2026-10-18 | `chromadb`/`fastembed` deferred to M3 as an extra | Install in M0 | Not needed until RAG; keeps M0 fast and lets M3 verify the choice. |
| 2026-10-18 | Makefile targets for later milestones exit 1 with a "not implemented" message | Empty no-op targets | Avoids presenting stubs as working (CLAUDE.md rule 7). |
