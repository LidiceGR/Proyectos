"""Create logical SQL workflow research tasks from deterministic inventory data.

Version 0.2 preserves ordered statement evidence and complete source slices.
It neither infers business rules nor executes an LLM or SQL operations.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from lib.artifact_io import write_json
from lib.manifest_paths import resolve_manifest_path

from .selector import select_workflow_units

EventCallback = Callable[[dict[str, Any]], None]
AST_NODE_TYPES: dict[str, set[str]] = {
    "INSERT": {"insert_statement"},
    "UPDATE": {"update_statement"},
    "DELETE": {"delete_statement"},
    "MERGE": {"merge_statement"},
    "THROW": {"throw_statement"},
    "RAISERROR": {"raiserror_statement", "raise_error_statement"},
    "RETURN": {"return_segment", "return_statement"},
    "SELECT": {"select_statement"},
    "TRANSACTION": {"transaction_statement"},
    "SET": {"set_statement", "set_clause", "set_clause_element"},
    "CALL": {"execute_statement", "execute_script_segment", "execute_procedure_statement", "exec_statement", "execute_segment", "execute_script_statement", "execute_command"},
}


def _load_document(path: Path, required_keys: set[str]) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(doc, dict) or not required_keys.issubset(doc):
        raise ValueError(f"Invalid metadata document: {path}")
    return doc


def _walk_ast(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for child in node.get("children", []):
        if isinstance(child, dict):
            yield from _walk_ast(child)


def _node_text(node: dict[str, Any]) -> str:
    children = node.get("children")
    if isinstance(children, list):
        return "".join(_node_text(child) for child in children if isinstance(child, dict))
    return str(node.get("text", ""))


def _last_line(node: dict[str, Any]) -> int | None:
    ends = []
    for leaf in _walk_ast(node):
        if "children" in leaf:
            continue
        line = leaf.get("line")
        if isinstance(line, int):
            ends.append(line + str(leaf.get("text", "")).count("\n"))
    return max(ends, default=node.get("line"))


def _locate_fragment(tree: dict[str, Any], operation: str, line: int) -> dict[str, Any] | None:
    allowed = AST_NODE_TYPES.get(operation, set())
    found = [n for n in _walk_ast(tree) if n.get("type") in allowed and n.get("line") == line]
    return min(found, key=lambda n: len(_node_text(n))) if found else None


def _related_objects(owner_type: str, owner_name: str, relationships: list[dict[str, Any]],
                     defined_tables: set[str]) -> list[dict[str, Any]]:
    related: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in relationships:
        parent = item.get("from") or {}
        if (str(parent.get("type", "")).casefold() != owner_type.casefold()
                or str(parent.get("name", "")).casefold() != owner_name.casefold()):
            continue
        target = item.get("to") or {}
        name, kind = str(target.get("name", "")), str(target.get("type", ""))
        relation = str(item.get("type", ""))
        identifier = (kind.casefold(), name.casefold(), relation)
        if not name or identifier in seen:
            continue
        seen.add(identifier)
        record: dict[str, Any] = {"relation": relation, "type": kind, "name": name}
        if kind.casefold() == "table":
            if name.casefold() in {"inserted", "deleted"}:
                record["sql_server_pseudotable"] = True
            else:
                record["definition_in_inventory"] = name.casefold() in defined_tables
        related.append(record)
    return sorted(related, key=lambda x: (x["relation"], x["type"], x["name"].casefold()))


def _task_identifier(source_file: str, obj_name: str, unit_type: str, source_line: int,
                     transaction_name: str | None) -> str:
    key = f"workflow-v2|{source_file.casefold()}|{obj_name.casefold()}|{unit_type}|{source_line}|{(transaction_name or '').casefold()}"
    return "TASK-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12].upper()


def _find_body_start(tree: dict[str, Any], object_type: str, source_line: int) -> int:
    """Find source-backed executable-body location; fall back to object start."""
    body_types = {"procedure_statement"} if object_type.casefold() == "procedure" else {"trigger_statement", "begin_end_block"}
    candidates = [n.get("line") for n in _walk_ast(tree)
                  if n.get("type") in body_types and isinstance(n.get("line"), int)
                  and n["line"] >= source_line]
    return min(candidates) if candidates else source_line


def _source_slice(lines: list[str], start: int, end: int) -> str:
    if not (1 <= start <= end <= len(lines)):
        raise ValueError(f"Invalid source line range {start}..{end} for file with {len(lines)} lines")
    return "\n".join(lines[start - 1:end])


def initialize_tasks(
    inventory_file: str | Path,
    relationships_file: str | Path,
    ast_directory: str | Path,
    destination: str | Path,
    on_event: EventCallback | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create pending source-backed workflow tasks without classifying business rules."""
    inventory_path = Path(inventory_file).expanduser().resolve()
    relationships_path = Path(relationships_file).expanduser().resolve()
    ast_root = Path(ast_directory).expanduser().resolve()
    run_root = ast_root.parent
    target = Path(destination).expanduser().resolve()
    output = target / "research_tasks.json"
    if output.exists() and not overwrite:
        raise FileExistsError(
            f"Research task file already exists: {output}. "
            "Use --overwrite only when you intend to reset the initial queue."
        )
    inventory = _load_document(inventory_path, {"tables", "procedures", "triggers"})
    relationship_doc = _load_document(relationships_path, {"relationships"})
    if not all(isinstance(inventory[key], list) for key in ("tables", "procedures", "triggers")):
        raise ValueError("Inventory sections must be arrays")
    relationships = relationship_doc["relationships"]
    if not isinstance(relationships, list):
        raise ValueError("Relationships must be an array")
    defined_tables = {str(item.get("name", "")).casefold() for item in inventory["tables"]}

    tasks: list[dict[str, Any]] = []
    ast_cache: dict[Path, dict[str, Any]] = {}
    seen_ids: set[str] = set()
    by_scope: Counter = Counter()
    operation_counts: Counter = Counter()
    objects = [*inventory["procedures"], *inventory["triggers"]]
    objects.sort(key=lambda item: (
        str((item.get("source") or {}).get("file", "")).casefold(),
        int((item.get("source") or {}).get("line") or 0),
        str(item.get("name", "")).casefold(),
    ))

    for obj in objects:
        owner_name = obj.get("name")
        owner_type = obj.get("object_type")
        source = obj.get("source") or {}
        source_file, ast_file = source.get("file"), source.get("ast_file")
        if not all(isinstance(field, str) and field.strip() for field in (
            owner_name, owner_type, source_file, ast_file
        )):
            raise ValueError(f"Object has missing identity or source references: {obj!r}")
        ast_path = resolve_manifest_path(ast_root, ast_file)
        if ast_path not in ast_cache:
            ast_doc = _load_document(ast_path, {"tree", "source_file", "status"})
            if ast_doc.get("status") != "ok" or not isinstance(ast_doc["tree"], dict):
                raise ValueError(f"Cannot initialize tasks from an invalid AST: {ast_path}")
            if str(ast_doc["source_file"]).replace("\\", "/") != source_file.replace("\\", "/"):
                raise ValueError(f"AST source file mismatch: {ast_path}")
            ast_cache[ast_path] = ast_doc["tree"]
        tree = ast_cache[ast_path]
        sql_path = resolve_manifest_path(run_root, source_file)
        sql_lines = sql_path.read_text(encoding="utf-8-sig").splitlines()
        source_line = int(source.get("line") or 1)
        body_start = _find_body_start(tree, owner_type, source_line)
        operations = obj.get("operations", [])
        if not isinstance(operations, list):
            raise ValueError(f"Operations must be an array in {owner_name}")
        if any(not isinstance(op, dict) or not isinstance((op.get("source") or {}).get("line"), int)
               for op in operations):
            raise ValueError(f"Invalid operation location in {owner_name}")
        units = select_workflow_units(operations)
        related = _related_objects(owner_type, owner_name, relationships, defined_tables)
        context: dict[str, Any] = {"parameters": copy.deepcopy(obj.get("parameters", []))}
        if owner_type == "trigger":
            context["trigger_table"] = obj.get("table")
            context["trigger_events"] = copy.deepcopy(obj.get("events", []))

        for index, unit in enumerate(units):
            steps = []
            for op in unit["operations"]:
                kind = str(op.get("operation", "")).upper()
                line = int((op.get("source") or {}).get("line"))
                if line < 1 or line > len(sql_lines):
                    raise ValueError(f"Invalid source line {line} for {owner_name}")
                node = _locate_fragment(tree, kind, line)
                end_line = _last_line(node) if node else line
                if not isinstance(end_line, int):
                    end_line = line
                step = {
                    "operation": kind,
                    "source_line": line,
                    "target_table": op.get("target_table"),
                    "called_object": op.get("called_object"),
                    "read_tables": copy.deepcopy(op.get("tables", [])),
                    "condition_context": copy.deepcopy(op.get("condition_context", [])),
                    "where_conditions": copy.deepcopy(op.get("where_conditions", [])),
                    "effects": copy.deepcopy(op.get("effects", {})),
                    "evidence": {
                        "source_file": source_file,
                        "start_line": line,
                        "end_line": end_line,
                        "ast_node_type": node.get("type") if node else None,
                    },
                    "sql": _source_slice(sql_lines, line, min(end_line, len(sql_lines))),
                }
                steps.append(step)
                operation_counts[kind] += 1
            if len(units) == 1:
                start_line = max(source_line, body_start)
                last_op_line = max((item["evidence"]["end_line"] for item in steps), default=start_line)
                # Read AST-backed procedure/trigger body instead of terminating at
                # the last selected DML. This retains final output and END clauses.
                body_end = max((n for n in (_last_line(node) for node in _walk_ast(tree)
                    if node.get("type") in {"create_procedure_statement", "create_trigger_statement", "create_trigger"}
                    and isinstance(node.get("line"), int) and node["line"] == source_line)
                    if isinstance(n, int)), default=last_op_line)
                end_line = min(max(last_op_line, body_end), len(sql_lines))
            else:
                start_line = min((step["source_line"] for step in steps), default=source_line)
                end_line = max((step["evidence"]["end_line"] for step in steps), default=start_line)
                # Keep the initial declaration block for the first flow unit.
                if index == 0:
                    start_line = min(start_line, body_start)
            # Preserve controlling IF predicates even if the inventoried steps
            # start inside the associated conditional block.
            guard_lines = [c.get("source", {}).get("line") for step in steps
                           for c in step["condition_context"] if isinstance(c, dict)]
            guard_lines = [x for x in guard_lines if isinstance(x, int) and x >= source_line]
            if guard_lines:
                start_line = min(start_line, min(guard_lines))
            if start_line > end_line:
                raise ValueError(f"Invalid workflow range for {owner_name}")
            scope_type = unit["type"]
            unit_name = unit.get("name")
            scope_start = unit.get("transaction_start_line", start_line)
            scope_end = unit.get("transaction_end_line", end_line)
            task_id = _task_identifier(source_file, owner_name, scope_type, scope_start, unit_name)
            if task_id in seen_ids:
                raise ValueError(f"Duplicate task identity at {source_file}:{scope_start}")
            seen_ids.add(task_id)
            by_scope[scope_type] += 1
            task = {
                "task_id": task_id,
                "task_type": "analyze_sql_workflow",
                "status": "pending",
                "target": {"object_type": owner_type, "object_name": owner_name},
                "scope": {
                    "type": scope_type,
                    "name": unit_name,
                    "start_line": scope_start,
                    "end_line": scope_end,
                    "segmentation_note": unit.get("warning"),
                },
                "fragment": {
                    "operation": "WORKFLOW",
                    "sql": _source_slice(sql_lines, start_line, end_line),
                    "steps": steps,
                    "operation_count": len(steps),
                },
                "object_context": copy.deepcopy(context),
                "related_objects": copy.deepcopy(related),
                "evidence": {
                    "source_file": source_file,
                    "ast_file": "ast_files/" + ast_file.replace("\\", "/"),
                    "start_line": start_line,
                    "end_line": end_line,
                    "ast_node_type": "logical_workflow",
                },
                "parent_task_id": None,
            }
            tasks.append(task)
            if on_event:
                on_event({
                    "event": "task_created", "task_id": task_id, "object_name": owner_name,
                    "kind": "WORKFLOW", "scope": scope_type, "steps": len(steps),
                })

    doc = {
        "schema_version": "2.0",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "note": "Source-backed workflow tasks only; no business rule is inferred or confirmed.",
        "source_artifacts": {
            "inventory": inventory_path.name,
            "relationships": relationships_path.name,
            "ast_directory": ast_root.name,
        },
        "summary": {
            "total_tasks": len(tasks),
            "objects_examined": len(objects),
            "by_scope": dict(sorted(by_scope.items())),
            "operations_preserved": dict(sorted(operation_counts.items())),
        },
        "tasks": tasks,
    }
    write_json(output, doc)
    if on_event:
        on_event({"event": "tasks_initialized", "total_tasks": len(tasks), "destination": str(output)})
    return doc
