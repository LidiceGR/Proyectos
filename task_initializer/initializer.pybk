"""Create traceable research tasks from deterministic SQL inventory metadata.

Tasks identify SQL fragments for later interpretation by an agent. They do not
predict, paraphrase, or confirm business rules, and they do not run SQL or LLMs.
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

from .selector import select_candidates

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
}


def _load_document(path: Path, required_keys: set[str]) -> dict[str, Any]:
    """Read JSON and validate the top-level object contract."""
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict) or not required_keys.issubset(document):
        raise ValueError(f"Invalid metadata document: {path}")
    return document


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
    """Find the last covered line using SQLFluff leaf locations."""
    ends = []
    for leaf in _walk_ast(node):
        if "children" in leaf:
            continue
        line = leaf.get("line")
        if isinstance(line, int):
            ends.append(line + str(leaf.get("text", "")).count("\n"))
    return max(ends, default=node.get("line"))


def _locate_fragment(
    ast_tree: dict[str, Any], operation_type: str, start_line: int
) -> dict[str, Any] | None:
    """Match an inventoried operation to its SQLFluff syntax node."""
    allowed_types = AST_NODE_TYPES.get(operation_type, set())
    matching = [
        node for node in _walk_ast(ast_tree)
        if node.get("type") in allowed_types and node.get("line") == start_line
    ]
    if not matching:
        return None
    return min(matching, key=lambda item: len(_node_text(item)))


def _related_objects(
    owner_type: str,
    owner_name: str,
    relationships: list[dict[str, Any]],
    defined_tables: set[str],
) -> list[dict[str, Any]]:
    """Keep known links and flag definitions absent from the supplied inventory."""
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
        result: dict[str, Any] = {
            "relation": relation,
            "type": kind,
            "name": name,
        }
        if kind.casefold() == "table":
            if name.casefold() in {"inserted", "deleted"}:
                result["sql_server_pseudotable"] = True
            else:
                result["definition_in_inventory"] = name.casefold() in defined_tables
        related.append(result)
    return sorted(related, key=lambda v: (v["relation"], v["type"], v["name"].casefold()))


def _task_identifier(source_file: str, name: str, kind: str, line: int, fallback: bool = False) -> str:
    """Create a stable identifier independent of task ordering or run timestamp."""
    key = f"{source_file.casefold()}|{name.casefold()}|{kind}|{line}|{fallback}"
    return "TASK-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12].upper()


def initialize_tasks(
    inventory_file: str | Path,
    relationships_file: str | Path,
    ast_directory: str | Path,
    destination: str | Path,
    on_event: EventCallback | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Generate pending fragment investigations with source-backed evidence.

    Args:
        inventory_file: The inventory_builder inventory.json path.
        relationships_file: The inventory_builder relationships.json path.
        ast_directory: The run-local ast_files folder containing AST files.
        destination: Folder where research_tasks.json will be written.
        on_event: Optional synchronous event callback, suitable for MCP adaptation.
        overwrite: Permit replacing an existing task queue explicitly. Defaults to False.

    Returns:
        The complete research_tasks.json document.

    Raises:
        ValueError: The metadata contract or a required AST node is invalid.
        FileNotFoundError: Required metadata or AST files are not available.
    """
    inventory_path = Path(inventory_file).expanduser().resolve()
    relationships_path = Path(relationships_file).expanduser().resolve()
    ast_root = Path(ast_directory).expanduser().resolve()
    target = Path(destination).expanduser().resolve()
    output_file = target / "research_tasks.json"
    if output_file.exists() and not overwrite:
        raise FileExistsError(
            f"Research task file already exists: {output_file}. "
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

    ast_cache: dict[Path, dict[str, Any]] = {}
    tasks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    summary = Counter()

    objects = [*inventory["procedures"], *inventory["triggers"]]
    objects.sort(key=lambda item: (
        str((item.get("source") or {}).get("file", "")).casefold(),
        int((item.get("source") or {}).get("line") or 0),
        str(item.get("name", "")).casefold(),
    ))

    for obj in objects:
        name = obj.get("name")
        owner_type = obj.get("object_type")
        source = obj.get("source") or {}
        source_file = source.get("file")
        ast_file = source.get("ast_file")
        if not all(isinstance(field, str) and field.strip() for field in (name, owner_type, source_file, ast_file)):
            raise ValueError(f"Object has missing identity or source references: {obj!r}")

        ast_path = resolve_manifest_path(ast_root, ast_file)
        if ast_path not in ast_cache:
            ast_doc = _load_document(ast_path, {"tree", "source_file", "status"})
            if ast_doc.get("status") != "ok":
                raise ValueError(f"Cannot initialize tasks from a non-ok AST: {ast_path}")
            if str(ast_doc["source_file"]).replace("\\", "/") != source_file.replace("\\", "/"):
                raise ValueError(f"AST source file mismatch: {ast_path}")
            if not isinstance(ast_doc["tree"], dict):
                raise ValueError(f"Missing AST tree: {ast_path}")
            ast_cache[ast_path] = ast_doc["tree"]
        tree = ast_cache[ast_path]

        dependencies = _related_objects(owner_type, name, relationships, defined_tables)
        context: dict[str, Any] = {"parameters": copy.deepcopy(obj.get("parameters", []))}
        if owner_type == "trigger":
            context["trigger_table"] = obj.get("table")
            context["trigger_events"] = copy.deepcopy(obj.get("events", []))

        candidates = select_candidates(obj.get("operations", []))
        if not candidates:
            fallback_line = int(source.get("line") or 1)
            task_id = _task_identifier(source_file, name, "OBJECT", fallback_line, True)
            task = {
                "task_id": task_id,
                "task_type": "inspect_sql_object",
                "status": "pending",
                "target": {"object_type": owner_type, "object_name": name},
                "fragment": None,
                "object_context": copy.deepcopy(context),
                "related_objects": copy.deepcopy(dependencies),
                "evidence": {
                    "source_file": source_file,
                    "ast_file": "ast_files/" + ast_file.replace("\\", "/"),
                    "start_line": fallback_line,
                    "end_line": None,
                    "ast_node_type": None,
                },
                "parent_task_id": None,
            }
            tasks.append(task)
            seen_ids.add(task_id)
            summary["inspect_sql_object"] += 1
            if on_event:
                on_event({"event": "task_created", "task_id": task_id, "object_name": name, "kind": "OBJECT"})
            continue

        for operation in candidates:
            kind = str(operation.get("operation", "")).upper()
            location = operation.get("source") or {}
            line = location.get("line")
            if not isinstance(line, int) or line <= 0:
                raise ValueError(f"Operation has no valid source line in {name}: {operation!r}")
            node = _locate_fragment(tree, kind, line)
            if node is None:
                raise ValueError(f"Missing AST node for {name} {kind} at line {line} in {ast_path}")
            task_id = _task_identifier(source_file, name, kind, line)
            if task_id in seen_ids:
                raise ValueError(f"Duplicate task identity at {source_file}:{line} ({kind})")
            seen_ids.add(task_id)
            task = {
                "task_id": task_id,
                "task_type": "analyze_sql_fragment",
                "status": "pending",
                "target": {"object_type": owner_type, "object_name": name},
                "fragment": {
                    "operation": kind,
                    "target_table": operation.get("target_table"),
                    "condition_context": copy.deepcopy(operation.get("condition_context", [])),
                    "where_conditions": copy.deepcopy(operation.get("where_conditions", [])),
                    "effects": copy.deepcopy(operation.get("effects", {})),
                    "sql": _node_text(node).strip(),
                },
                "object_context": copy.deepcopy(context),
                "related_objects": copy.deepcopy(dependencies),
                "evidence": {
                    "source_file": source_file,
                    "ast_file": "ast_files/" + ast_file.replace("\\", "/"),
                    "start_line": line,
                    "end_line": _last_line(node),
                    "ast_node_type": node.get("type"),
                },
                "parent_task_id": None,
            }
            tasks.append(task)
            summary[kind] += 1
            if on_event:
                on_event({"event": "task_created", "task_id": task_id, "object_name": name, "kind": kind})

    document = {
        "schema_version": "1.0",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "note": "Structural research tasks only; no business rule is inferred or confirmed.",
        "source_artifacts": {
            "inventory": inventory_path.name,
            "relationships": relationships_path.name,
            "ast_directory": ast_root.name,
        },
        "summary": {"total_tasks": len(tasks), "objects_examined": len(objects), "by_operation": dict(sorted(summary.items()))},
        "tasks": tasks,
    }
    write_json(output_file, document)
    if on_event:
        on_event({"event": "tasks_initialized", "total_tasks": len(tasks), "destination": str(output_file)})
    return document
