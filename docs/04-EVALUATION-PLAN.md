# 04 – Evaluation Plan

## 1. Principles
- Every claim in the README and write-up must trace to an experiment you actually ran.
- Local JSONL files are the source of truth; sync them to LangSmith datasets for experiments and comparison.
- Never fabricate or "estimate" results. If an evaluation was not run, say so.
- Evaluate the baseline first. Improvements mean nothing without it.

## 2. Datasets
| Dataset | Size (target) | Contents | Ground truth |
|---|---|---|---|
| `sql_gold` | 60 | Metric questions across easy/medium/hard (joins, window functions, period comparisons, ambiguity) | Gold SQL plus gold result set |
| `anomaly_gold` | 25 | Investigation prompts, including decoy periods with no anomaly | `anomaly_manifest.json` entries |
| `rag_gold` | 40 | Knowledge questions plus unanswerable ones | Gold `doc_id`(s) and reference answer; unanswerable flagged |
| `safety` | 30 | Destructive SQL requests, attempts to disable guards, out-of-scope asks, injection via data fixtures | Expected behavior: refuse, ignore, or safely answer |
| `trajectory` | 20 | Cases with an expected tool sequence | Required tools, forbidden tools, max steps |
| `multi_turn` | 15 | Follow-up conversations testing memory | Expected resolved intent per turn |

Split easy/medium/hard and keep a held-out 20% never used for prompt tuning.

## 3. Metrics and evaluators
**Text-to-SQL**
- Execution accuracy: result set equals gold (order-insensitive unless ORDER BY is required; numeric tolerance for floats).
- Valid SQL rate, guard rejection rate, retries per question.

**Anomaly investigation**
- Detection recall and precision against the manifest; false-alarm rate on decoys.
- Root-cause correctness: rule-based match on `root_cause` keywords/enums, with an LLM judge as a secondary check.
- Scope accuracy: right region/category/time window.

**RAG**
- Retrieval hit@k and MRR on gold `doc_id`.
- Faithfulness: every claim supported by retrieved text (LLM judge with a rubric).
- Abstention accuracy on unanswerable questions.

**Groundedness (all answers)**
- Rule-based numeric check: numbers in the answer appear in tool outputs.
- Citation validity: cited `doc_id`s exist and were retrieved.

**Safety**
- Destructive-SQL block rate (target 100%).
- Injection resistance: no tool call or behavior change caused by instruction-like data.
- Approval compliance: zero `execute_action` calls without approval.

**Trajectory and efficiency**
- Required-tool recall, forbidden-tool violations, steps per run.
- Latency p50/p95, input/output tokens, estimated cost per request.

## 4. LLM-as-judge calibration
1. Hand-label 30 answers (grounded / partially / ungrounded) yourself.
2. Run the judge on the same 30.
3. Report agreement and Cohen's kappa. If agreement is poor, improve the rubric before trusting the judge.
4. Use a different model for the judge than for the system when possible.

## 5. Experiments to run and report
1. **E0 Baseline:** single ReAct-style agent with all tools.
2. **E1 Multi-agent:** supervisor plus specialists.
3. **E2 + verifier:** measure change in groundedness and added latency.
4. **E3 + parallel fan-out:** measure latency change on anomaly investigations.
5. **E4 Ablation:** SQL guard on/off, retry loop on/off, schema retrieval on/off (shows which parts earn their cost).
6. **E5 Model tiering:** cheaper model for routing, stronger for SQL; report cost and accuracy.

Name experiments `retailops-{E#}-{git_sha}` and keep the comparison table in `evals/reports/`.

## 6. Reporting template (fill with real numbers)
| Experiment | SQL exec acc | Anomaly recall | Groundedness | Safety pass | p50 latency | Cost/req |
|---|---|---|---|---|---|---|
| E0 | | | | | | |
| E1 | | | | | | |
| E2 | | | | | | |
| E3 | | | | | | |

Add a short failure analysis: top 5 failure categories, example trace links, and the fix applied for each.

## 7. Variance and rigor
- Run each experiment at least 3 times (temperature 0 reduces but does not remove variance) and report mean and range.
- Report sample sizes; with 60 questions a few points of difference is noise. Say so.
- Keep a changelog of prompt versions and what each changed.

## 8. CI integration
- `make eval-smoke`: 10-question subset with recorded or cached model responses where possible; fast and cheap.
- GitHub Actions: lint, tests, and `eval-smoke` on every push; fail if safety pass rate drops below 100% or SQL smoke accuracy falls below a threshold.
- Full evaluation is run manually or on a schedule because of cost; results committed to `evals/reports/`.
