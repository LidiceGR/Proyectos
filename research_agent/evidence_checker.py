"""Check evidence coverage without inferring or approving a business rule.

These checks do not prove semantic correctness. They detect conspicuous omissions
in model responses by comparing them with the task's structured SQL evidence.
Unresolved categorical code warnings are heuristic and do not create tool calls.
"""

from __future__ import annotations

import re
from typing import Any

_EFFECT_OPERATIONS = frozenset({"INSERT", "UPDATE", "DELETE", "MERGE", "THROW", "RAISERROR"})
_CONDITION_COLUMNS = re.compile(r"\b(status|type|category|code|estado|tipo|categoria)\b", re.IGNORECASE)


def _conditions(task: dict[str, Any]) -> list[str]:
    """Collect unique source conditions in source order."""
    fragment = task.get("fragment") or {}
    steps = fragment.get("steps") if isinstance(fragment.get("steps"), list) else [fragment]
    found: list[str] = []
    seen: set[str] = set()
    for step in steps:
        for field in ("condition_context", "where_conditions"):
            for condition in step.get(field) or []:
                expression = condition.get("expression") if isinstance(condition, dict) else None
                if isinstance(expression, str) and expression.strip():
                    key = expression.strip().casefold()
                    if key not in seen:
                        seen.add(key)
                        found.append(expression.strip())
    return found


def _unexplained_code_hints(task: dict[str, Any]) -> list[str]:
    """Identify potential categorical literals, not confirmed catalog references."""
    fragment = task.get("fragment") or {}
    steps = fragment.get("steps") if isinstance(fragment.get("steps"), list) else [fragment]
    hints: set[str] = set()
    for step in steps:
        for condition in (step.get("condition_context") or []) + (step.get("where_conditions") or []):
            expression = condition.get("expression", "") if isinstance(condition, dict) else ""
            for match in re.finditer(
                r"([@\w.]+)\s*=\s*('[^']*'|\d+)", expression, re.IGNORECASE
            ):
                name, literal = match.group(1), match.group(2)
                if _CONDITION_COLUMNS.search(name) or re.search(r"(?:type|status|code|estado|tipo|category)$", name, re.I):
                    hints.add(f"{name} = {literal}")
        effects = step.get("effects") or {}
        for assignment in (effects.get("assignments") or []) + (effects.get("column_values") or []):
            if not isinstance(assignment, dict):
                continue
            name, value = assignment.get("column", ""), assignment.get("expression", "")
            if isinstance(name, str) and isinstance(value, str) and (
                _CONDITION_COLUMNS.search(name) or re.search(r"(?:type|status|code|estado|tipo|category)$", name, re.I)
            ) and (value.startswith("'") or re.fullmatch(r"\d+", value)):
                hints.add(f"{name} = {value}")
    return sorted(hints)


def check_evidence(task: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    """Return blocking coverage issues separately from advisory uncertainty warnings."""
    fragment = task.get("fragment") or {}
    steps = fragment.get("steps") if isinstance(fragment.get("steps"), list) else [fragment]
    effects = [
        s for s in steps if s.get("operation") in _EFFECT_OPERATIONS
        or s.get("operation") in {"RETURN", "SET", "CALL"}
        or (s.get("operation") == "SELECT" and (
            (s.get("effects") or {}).get("variable_assignments")
            or (s.get("sql") or "").strip().lower().startswith("select ")
        ))
    ]
    predicates = _conditions(task)
    issues: list[str] = []
    warnings: list[str] = []
    behavior = decision.get("observed_behavior")
    effect_text = decision.get("effect")

    if effects and (not isinstance(behavior, str) or not behavior.strip()):
        issues.append("observed_behavior is required when the workflow contains observable effects")
    if effects and (not isinstance(effect_text, str) or not effect_text.strip()):
        issues.append("effect must describe observable data changes or validation effects")
    if predicates and not decision.get("conditions"):
        issues.append("conditions are empty although IF/WHERE conditions exist in the source")
    for item in decision.get("conditions") or []:
        if isinstance(item, str) and re.match(r"^\s*(?:SELECT\s+@\w+\s*=|UPDATE\s+|INSERT\s+)", item, re.I):
            issues.append("conditions contain a SQL assignment or effect rather than a predicate")
            break

    # Result-set output is distinct from a stored procedure RETURN status code.
    source_sql = fragment.get("sql") or ""
    output_matches = re.findall(
        r"(?im)^\s*select\s+(@\w+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*;?\s*$",
        source_sql,
    )
    if output_matches and isinstance(behavior, str) and isinstance(effect_text, str):
        combined = (behavior + " " + effect_text).casefold()
        if not any(variable.casefold() in combined or alias.casefold() in combined
                   for variable, alias in output_matches):
            issues.append("Final SELECT result-set output is missing from observed_behavior/effect")

    # Multiple table modifications are the clearest motivation for workflow grouping.
    targets = sorted({
        str(s["target_table"]) for s in effects
        if s.get("target_table") and s.get("operation") in {"INSERT", "UPDATE", "DELETE", "MERGE"}
    })
    if len(targets) > 1 and isinstance(behavior, str) and isinstance(effect_text, str):
        combined = (behavior + " " + effect_text).casefold()
        for target in targets:
            if target.casefold() not in combined:
                warnings.append(f"The combined effect does not explicitly reference table {target}")

    hints = _unexplained_code_hints(task)
    if hints and not decision.get("missing_information") and decision.get("requested_action") is None:
        warnings.append(
            "Potential categorical literals were not investigated: " + ", ".join(hints)
            + ". Confirm their meaning only if material to the business interpretation."
        )

    return {
        "status": "incomplete" if issues else "passed",
        "issues": issues,
        "warnings": warnings,
        "source_conditions": predicates,
        "effect_operations": [s.get("operation") for s in effects],
        "modified_targets": targets,
    }
