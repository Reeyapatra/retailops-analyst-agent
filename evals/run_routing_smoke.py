"""Score supervisor routing on the smoke set with a REAL model (needs MODEL_SUPERVISOR + key).

Only the supervisor is called; specialists are not run, so this costs a few cents. Earlier turns
in a case get a placeholder assistant reply so follow-ups have context.

    uv run python -m evals.run_routing_smoke
Nothing here has been run without a key; report only numbers from an actual run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from retailops.agents.supervisor import route
from retailops.models import get_chat_model

DATASET = Path(__file__).parent / "datasets" / "routing_smoke.jsonl"
REPORTS = Path(__file__).parent / "reports"


def main() -> int:
    cases: list[dict[str, Any]] = [
        json.loads(x) for x in DATASET.read_text("utf-8").splitlines() if x
    ]
    llm = get_chat_model("supervisor")
    results = []
    for case in cases:
        history: list[str] = []
        decision = None
        for turn in case["turns"]:
            decision, _ = route(llm, turn, "\n".join(history), scenario_id=f"routing:{case['id']}")
            history += [f"User: {turn}", "Assistant: (answered)"]
        assert decision is not None
        ok = decision.intent == case["expected_intent"]
        results.append(
            {
                "id": case["id"],
                "expected": case["expected_intent"],
                "got": decision.intent,
                "correct": ok,
                "standalone": decision.standalone_question,
            }
        )
        print(
            f"{case['id']} {'OK ' if ok else 'BAD'} expected={case['expected_intent']} "
            f"got={decision.intent}"
        )
    n_ok = sum(r["correct"] for r in results)
    print(f"\nrouting accuracy: {n_ok}/{len(results)}")
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "routing_smoke.json").write_text(
        json.dumps({"n": len(results), "correct": n_ok, "results": results}, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
