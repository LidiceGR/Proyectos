"""Parse and validate untrusted model responses without executing tools."""

from __future__ import annotations

import json
import re
from typing import Any

CLASSIFICATIONS = {
    "candidate_business_rule", "technical_logic", "ambiguous"
}
ALLOWED_ACTIONS = {
    "lookup_catalog_value": {"column", "value"},
    "inspect_related_object": {"object_name"},
    "inspect_source_file": {"source_file"},
    "enqueue_research_task": {"object_name", "research_question", "reason"},
}


def extract_json_object(text: str) -> dict[str, Any]:
    """Find and decode the first JSON object while retaining raw text elsewhere."""
    clean = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)
    decoder = json.JSONDecoder()
    for index, char in enumerate(clean):
        if char != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(clean[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            return candidate
    raise ValueError("No valid JSON object was found in the model output")


def validate_decision(
    value: dict[str, Any], *, require_observed_behavior: bool = False
) -> list[str]:
    """Validate response structure and action names, not factual correctness."""
    errors: list[str] = []
    if require_observed_behavior:
        expected = {
            "observed_behavior", "conditions", "effect", "classification",
            "candidate_rule", "missing_information", "requested_action",
        }
        missing = expected - set(value)
        extra = set(value) - expected
        if missing:
            errors.append(f"missing output fields: {', '.join(sorted(missing))}")
        if extra:
            errors.append(f"unexpected output fields: {', '.join(sorted(extra))}")
        if not isinstance(value.get("observed_behavior"), str) or not value.get("observed_behavior", "").strip():
            errors.append("observed_behavior must be a nonempty string")
    if not isinstance(value.get("classification"), str) or value.get("classification") not in CLASSIFICATIONS:
        errors.append("classification is missing or not allowed")

    rule = value.get("candidate_rule")
    if rule is not None and not isinstance(rule, str):
        errors.append("candidate_rule must be a string or null")

    if require_observed_behavior:
        classification = value.get("classification")
        if classification == "candidate_business_rule" and value.get("requested_action") is None and not (isinstance(rule, str) and rule.strip()):
            errors.append("candidate_business_rule requires a nonempty candidate_rule")
        if classification in {"technical_logic", "ambiguous"} and rule is not None:
            errors.append("candidate_rule must be null for technical_logic or ambiguous")

    effect = value.get("effect")
    if effect is not None and not isinstance(effect, str):
        errors.append("effect must be a string or null")

    if require_observed_behavior:
        for field in ("observed_behavior", "effect", "candidate_rule"):
            text = value.get(field)
            if isinstance(text, str) and re.fullmatch(r"\s*<[^>]+>\s*", text):
                errors.append(f"{field} contains an unfilled output placeholder")

    for field in ("conditions", "missing_information"):
        items = value.get(field)
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
            errors.append(f"{field} must be a list of strings")

    action = value.get("requested_action")
    if action is not None:
        if not isinstance(action, dict):
            errors.append("requested_action must be an object or null")
        else:
            name = action.get("name")
            if not isinstance(name, str) or name not in ALLOWED_ACTIONS:
                errors.append("requested_action.name is not allowed")
            args = action.get("arguments")
            if not isinstance(args, dict):
                errors.append("requested_action.arguments must be an object")
            elif isinstance(name, str) and name in ALLOWED_ACTIONS:
                required = ALLOWED_ACTIONS[name]
                if not required.issubset(args):
                    errors.append(f"requested_action.arguments missing: {', '.join(sorted(required - set(args)))}")
                if name == "lookup_catalog_value":
                    if not isinstance(args.get("column"), str) or not args.get("column"):
                        errors.append("lookup_catalog_value.column must be a non-empty string")
                    if not isinstance(args.get("value"), (str, int, float)) or isinstance(args.get("value"), bool):
                        errors.append("lookup_catalog_value.value must be a string or number")
                if name == "inspect_related_object" and not isinstance(args.get("object_name"), str):
                    errors.append("inspect_related_object.object_name must be a string")
                if name == "inspect_source_file" and not isinstance(args.get("source_file"), str):
                    errors.append("inspect_source_file.source_file must be a string")
                if name == "enqueue_research_task":
                    for field in ("object_name", "research_question", "reason"):
                        if not isinstance(args.get(field), str) or not args[field].strip():
                            errors.append(f"enqueue_research_task.{field} must be a nonempty string")
    return errors
