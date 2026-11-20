"""Select investigation-worthy SQL operations using structural evidence only.

This module does not infer business meaning, classify rules, or call an LLM.
"""

from __future__ import annotations

from typing import Any

DATA_CHANGES = frozenset({"INSERT", "UPDATE", "DELETE", "MERGE"})
CONTROL_OUTCOMES = frozenset({"THROW", "RAISERROR", "RETURN"})


def select_candidates(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return operations that warrant investigation without interpreting them.

    Conditional SELECT assignments are candidates; ordinary reads, and
    transaction-control statements, remain context rather than separate tasks.
    A RETURN is selected only when guarded by a captured condition.

    Args:
        operations: Operations extracted by inventory_builder.

    Returns:
        Candidate operations in source order, preserving their input fields.
    """
    candidates: list[dict[str, Any]] = []
    for operation in operations:
        kind = str(operation.get("operation", "")).upper()
        guarded = bool(operation.get("condition_context"))
        effects = operation.get("effects") or {}
        if kind in DATA_CHANGES or kind in {"THROW", "RAISERROR"}:
            candidates.append(operation)
        elif kind == "RETURN" and guarded:
            candidates.append(operation)
        elif kind == "SELECT" and guarded and effects.get("variable_assignments"):
            candidates.append(operation)
    return candidates
