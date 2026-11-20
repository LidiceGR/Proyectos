"""Select source-backed logical execution units without inferring business rules.

Version 0.2 groups related statements before model analysis. A matching
BEGIN TRAN / COMMIT can define a unit; compact nontransactional objects retain
the entire execution flow, including initialization, conditions and output.
"""

from __future__ import annotations

import re
from typing import Any

DATA_CHANGES = frozenset({"INSERT", "UPDATE", "DELETE", "MERGE"})
CONTROL_OUTCOMES = frozenset({"THROW", "RAISERROR", "RETURN"})

_TRANSACTION_RE = re.compile(
    r"^\s*(begin\s+tran(?:saction)?|commit(?:\s+tran(?:saction)?)?|rollback(?:\s+tran(?:saction)?)?)\b(?:\s+([\w\[\]#@]+))?",
    re.IGNORECASE,
)


def select_candidates(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Legacy v0.1 selector retained for compatibility with existing callers."""
    selected: list[dict[str, Any]] = []
    for operation in operations:
        kind = str(operation.get("operation", "")).upper()
        effects = operation.get("effects") or {}
        guarded = bool(operation.get("condition_context"))
        if kind in DATA_CHANGES or kind in {"THROW", "RAISERROR"}:
            selected.append(operation)
        elif kind == "RETURN" and guarded:
            selected.append(operation)
        elif kind == "SELECT" and guarded and effects.get("variable_assignments"):
            selected.append(operation)
    return selected


def _transaction_directive(operation: dict[str, Any]) -> tuple[str, str | None] | None:
    if str(operation.get("operation", "")).upper() != "TRANSACTION":
        return None
    statement = str((operation.get("effects") or {}).get("statement", ""))
    match = _TRANSACTION_RE.match(statement)
    if not match:
        return None
    return match.group(1).split()[0].upper(), match.group(2)


def select_workflow_units(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select conservative logical units, preserving original operation order.

    Matched transaction spans can form separate units if they do not overlap.
    Remaining operations are assigned to an adjacent unit, not turned into
    standalone RETURN or SELECT tasks. Ambiguous/unmatched transactions are
    grouped as one whole-object flow rather than splitting unsafely.

    The returned units contain *references* to the input operation dictionaries.
    The initializer is responsible for copying and validating those records.
    """
    ordered = sorted(operations, key=lambda x: int((x.get("source") or {}).get("line") or 0))
    if not ordered:
        return [{"type": "procedure_flow", "name": None, "operations": [], "warning": "No inventoried operations; inspect source as one unit."}]

    open_stack: list[tuple[int, str | None]] = []
    matched: list[tuple[int, int, str | None]] = []
    invalid = False
    for index, operation in enumerate(ordered):
        directive = _transaction_directive(operation)
        if directive is None:
            continue
        action, name = directive
        if action == "BEGIN":
            open_stack.append((index, name))
        elif action == "COMMIT":
            if not open_stack:
                invalid = True
                continue
            start_index, start_name = open_stack.pop()
            if name and start_name and name.casefold() != start_name.casefold():
                invalid = True
            matched.append((start_index, index, start_name or name))
        # ROLLBACK is a conditional control path, not a successful end boundary.

    if open_stack or invalid:
        return [{
            "type": "procedure_flow", "name": None, "operations": ordered,
            "warning": "Transaction boundaries are not unambiguously matched; the full object was retained.",
        }]
    if not matched:
        return [{"type": "procedure_flow", "name": None, "operations": ordered, "warning": None}]
    if len(matched) == 1:
        return [{
            "type": "transaction", "name": matched[0][2], "operations": ordered,
            "transaction_start_line": (ordered[matched[0][0]].get("source") or {}).get("line"),
            "transaction_end_line": (ordered[matched[0][1]].get("source") or {}).get("line"),
            "warning": None,
        }]

    # Nested/overlapping transaction spans require a complete-flow analysis.
    spans = sorted(matched)
    if any(left[1] >= right[0] for left, right in zip(spans, spans[1:])):
        return [{
            "type": "procedure_flow", "name": None, "operations": ordered,
            "warning": "Overlapping transaction scopes; the full object was retained.",
        }]

    # The split is safe only when the surrounding code has no material data
    # change or branching condition outside the matched transaction spans.
    included_indices = {idx for start, end, _ in spans for idx in range(start, end + 1)}
    outside = [(i, ordered[i]) for i in range(len(ordered)) if i not in included_indices]
    for _, op in outside:
        kind = str(op.get("operation", "")).upper()
        if kind in DATA_CHANGES or kind in {"THROW", "RAISERROR"} or op.get("condition_context"):
            return [{
                "type": "procedure_flow", "name": None, "operations": ordered,
                "warning": "Cross-transaction behavior may exist; the full object was retained.",
            }]

    groups: list[list[dict[str, Any]]] = [ordered[start:end + 1] for start, end, _ in spans]
    for index, op in outside:
        # Attach setup/epilogue statements to the nearest transaction, never
        # create a fragment-only task from a control statement.
        best = min(range(len(spans)), key=lambda ix: min(
            abs(index - spans[ix][0]), abs(index - spans[ix][1])
        ))
        groups[best].append(op)
    units = []
    for (start, end, name), group in zip(spans, groups):
        units.append({
            "type": "transaction", "name": name,
            "operations": sorted(group, key=lambda x: int((x.get("source") or {}).get("line") or 0)),
            "transaction_start_line": (ordered[start].get("source") or {}).get("line"),
            "transaction_end_line": (ordered[end].get("source") or {}).get("line"),
            "warning": "Adjacent setup/return statements were assigned to the nearest transaction.",
        })
    return units
