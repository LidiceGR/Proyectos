"""Compare the observable output of two baseline research runs.

This comparison measures model reporting and evidence coverage only. It is NOT
an independent validation of business rules or a ReAct benchmark.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def summarize(run_directory: Path) -> dict[str, int]:
    """Summarize individual results in a workspace run's agent_runs folder."""
    destination = run_directory / "research" / "agent_runs"
    if not destination.is_dir():
        raise FileNotFoundError(f"Research results directory was not found: {destination}")
    files = sorted(destination.glob("TASK-*.json"))
    if not files:
        raise ValueError(f"No individual research results were found in {destination}")
    counts = {
        "tasks": 0,
        "reported_status_valid_json": 0,
        "evidence_incomplete": 0,
        "invalid_response": 0,
        "execution_error": 0,
        "observed_behavior_reported": 0,
        "conditions_reported": 0,
        "effect_reported": 0,
        "candidate_rules_reported": 0,
        "missing_information_reported": 0,
        "actions_proposed": 0,
    }
    for file in files:
        result: dict[str, Any] = json.loads(file.read_text(encoding="utf-8-sig"))
        decision = result.get("decision") or {}
        status = result.get("status")
        counts["tasks"] += 1
        if status == "valid_json":
            counts["reported_status_valid_json"] += 1
        elif status in counts:
            counts[status] += 1
        for output_key, metric_key in (
            ("observed_behavior", "observed_behavior_reported"),
            ("conditions", "conditions_reported"),
            ("effect", "effect_reported"),
            ("candidate_rule", "candidate_rules_reported"),
            ("missing_information", "missing_information_reported"),
            ("requested_action", "actions_proposed"),
        ):
            value = decision.get(output_key)
            if isinstance(value, str):
                present = bool(value.strip())
            else:
                present = bool(value)
            if present:
                counts[metric_key] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare outputs of two Capstone research runs")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    args = parser.parse_args()
    before = summarize(args.baseline)
    after = summarize(args.current)
    print("[research_comparison] Earlier runs validated JSON only; v0.3 also applies evidence coverage checks. Rule correctness is NOT evaluated.")
    print(f"{'Metric':34} {'Baseline':>9} {'Current':>9}")
    print("-" * 55)
    for key in before:
        print(f"{key:34} {before[key]:>9} {after[key]:>9}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
