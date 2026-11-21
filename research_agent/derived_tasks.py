"""Link existing tasks or enqueue source-backed investigations discovered by ReAct."""

from __future__ import annotations

import hashlib
from typing import Any

from .tools import ResearchTools, _identifier


def _same_object(a: str, b: str) -> bool:
    return _identifier(a) == _identifier(b)


def _linked_to_current(tools: ResearchTools, current: dict[str, Any], object_name: str) -> bool:
    """Permit only objects connected to the parent through a known table."""
    owner = str(current["target"]["object_name"])
    touched = {str(x.get("name")) for x in current.get("related_objects", [])
               if x.get("type") == "table"}
    touched.update(str(s.get("target_table")) for s in current.get("fragment", {}).get("steps", [])
                   if s.get("target_table"))
    for edge in tools.relationships:
        left, right = edge.get("from") or {}, edge.get("to") or {}
        names = [str(left.get("name", "")), str(right.get("name", ""))]
        if any(_same_object(owner, n) for n in names) and any(_same_object(object_name, n) for n in names):
            return True
        if any(_same_object(object_name, n) for n in names) and any(
            _same_object(t, n) for t in touched for n in names
        ):
            return True
    return False


def create_derived_task(tools: ResearchTools, parent: dict[str, Any], object_name: str,
                        question: str, reason: str) -> dict[str, Any] | None:
    """Build a bounded workflow from inventory operations and its original SQL."""
    target = tools.resolve_object(object_name)
    if target is None or target.get("object_type") not in {"procedure", "trigger"}:
        return None
    if not _linked_to_current(tools, parent, object_name):
        return None
    evidence = target.get("source") or {}
    relative = evidence.get("file")
    if not isinstance(relative, str) or not relative:
        return None
    source_file = tools.source_file(relative)
    sql_lines = source_file.read_text(encoding="utf-8-sig").splitlines()
    begin = evidence.get("line", 1)
    if not isinstance(begin, int) or begin < 1 or begin > len(sql_lines):
        return None
    steps = []
    for op in target.get("operations", []):
        if not isinstance(op, dict):
            continue
        line = (op.get("source") or {}).get("line")
        if not isinstance(line, int):
            continue
        steps.append({"operation": op.get("operation"), "source_line": line,
                      "target_table": op.get("target_table"),
                      "condition_context": op.get("condition_context", []),
                      "where_conditions": op.get("where_conditions", []),
                      "effects": op.get("effects", {}),
                      "sql": sql_lines[line - 1].strip() if 1 <= line <= len(sql_lines) else ""})
    if not steps:
        return None
    name = str(target["name"])
    task_id = "TASK-D-" + hashlib.sha256((parent["task_id"] + "|" + name.casefold()).encode()).hexdigest()[:12].upper()
    related = []
    for edge in tools.relationships:
        left, right = edge.get("from") or {}, edge.get("to") or {}
        if _same_object(str(left.get("name", "")), name):
            related.append({"relation": edge.get("type"), "type": right.get("type"), "name": right.get("name")})
    return {
        "task_id": task_id,
        "task_type": "analyze_sql_workflow",
        "status": "pending",
        "target": {"object_type": target["object_type"], "object_name": name},
        "scope": {"type": "derived_object_flow", "name": None, "start_line": begin,
                  "end_line": len(sql_lines), "segmentation_note": "Derived investigation; complete source from inventory, not v0.2 segmentation"},
        "fragment": {"operation": "WORKFLOW", "sql": "\n".join(sql_lines[begin - 1:]),
                     "steps": steps, "operation_count": len(steps)},
        "object_context": {"parameters": target.get("parameters", [])},
        "related_objects": related,
        "evidence": {"source_file": relative, "ast_file": "ast_files/" + str(evidence.get("ast_file") or ""),
                     "start_line": begin, "end_line": len(sql_lines), "ast_node_type": "logical_workflow"},
        "parent_task_id": parent["task_id"],
        "research_question": question,
        "creation_reason": reason,
        "origin": "react_derived",
    }


def register_investigation(tools: ResearchTools, parent: dict[str, Any], name: str,
                           question: str, reason: str, known_tasks: list[dict[str, Any]],
                           derived: list[dict[str, Any]], max_derived: int) -> dict[str, Any]:
    """Deduplicate against *all* initial tasks, including not-selected ones."""
    if not _linked_to_current(tools, parent, name):
        return {"status": "rejected", "reason": "Target is not related to the current task", "object_name": name}
    matching = [t for t in [*known_tasks, *derived]
                if _same_object(str((t.get("target") or {}).get("object_name", "")), name)]
    if matching:
        return {"status": "linked_existing", "object_name": name,
                "task_ids": [t["task_id"] for t in matching]}
    if len(derived) >= max_derived:
        return {"status": "limit_reached", "object_name": name}
    candidate = create_derived_task(tools, parent, name, question, reason)
    if candidate is None:
        return {"status": "unavailable", "object_name": name,
                "reason": "No source-backed procedure/trigger workflow could be built"}
    derived.append(candidate)
    return {"status": "created", "object_name": name, "task_id": candidate["task_id"],
            "parent_task_id": parent["task_id"]}


def discover_affected_triggers(tools: ResearchTools, parent: dict[str, Any],
                               inspected_table: str, known_tasks: list[dict[str, Any]],
                               derived: list[dict[str, Any]], max_derived: int) -> list[dict[str, Any]]:
    """Only trigger events proven relevant to the parent's DML warrant follow-up."""
    steps = parent.get("fragment", {}).get("steps", [])
    events = {str(s.get("operation", "")).upper() for s in steps
              if _same_object(str(s.get("target_table", "")), inspected_table)}
    results = []
    for edge in tools.relationships:
        left, right = edge.get("from") or {}, edge.get("to") or {}
        if (edge.get("type") != "TRIGGERS_ON"
                or not _same_object(str(right.get("name", "")), inspected_table)
                or not events.intersection({str(e).upper() for e in edge.get("events", [])})):
            continue
        name = str(left.get("name", ""))
        if _same_object(name, str(parent["target"]["object_name"])):
            continue
        result = register_investigation(tools, parent, name,
                                        f"Does {name} add constraints to the parent DML?",
                                        f"A {sorted(events)} operation on {inspected_table} can invoke this trigger",
                                        known_tasks, derived, max_derived)
        results.append(result)
    return results
