"""Build traceable SQL inventory, relationships and catalogs without an LLM.

Consumes the unmodified source_extractor/sources_manifest.json and
sql_ast_parsers/ast_manifest.json formats of the CMU Capstone project.
No database connection or SQL execution takes place in this module.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from lib.artifact_io import write_json
from lib.manifest_paths import resolve_manifest_path

EventCallback = Callable[[dict[str, Any]], None]

DML_TYPES = {
    "insert_statement": "INSERT",
    "update_statement": "UPDATE",
    "delete_statement": "DELETE",
    "merge_statement": "MERGE",
}
RELATION_NAMES = {
    "INSERT": "INSERTS_INTO", "UPDATE": "UPDATES",
    "DELETE": "DELETES_FROM", "MERGE": "MERGES_INTO",
    "SELECT": "READS",
}
EXEC_TYPES = {
    "execute_statement", "execute_script_segment", "execute_procedure_statement",
    "exec_statement", "execute_segment", "execute_script_statement",
    "execute_command",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _walk(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for child in node.get("children", []):
        yield from _walk(child)


def _find(node: dict[str, Any], node_type: str) -> list[dict[str, Any]]:
    return [child for child in _walk(node) if child.get("type") == node_type]


def _direct(node: dict[str, Any], node_type: str) -> list[dict[str, Any]]:
    return [child for child in node.get("children", []) if child.get("type") == node_type]


def _first(node: dict[str, Any], node_type: str) -> dict[str, Any] | None:
    return next(iter(_find(node, node_type)), None)


def _text(node: dict[str, Any]) -> str:
    children = node.get("children")
    if children:
        return "".join(_text(child) for child in children)
    return str(node.get("text", ""))


def _compact(node: dict[str, Any]) -> str:
    return " ".join(_text(node).split())


def _name(node: dict[str, Any] | None) -> str | None:
    return re.sub(r"\s+", "", _text(node)).strip() if node is not None else None


def _source(file: str, line: int | None, ast_file: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"file": file, "line": line}
    if ast_file is not None:
        result["ast_file"] = ast_file
    return result


def _load_object(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(obj, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return obj


def _condition(expression: str, kind: str, node: dict[str, Any], file: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "expression": expression,
        "source": _source(file, node.get("line")),
    }


def _param_list(proc: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse procedure parameters from the parameter-list segment children."""
    parameters: list[dict[str, Any]] = []
    entry: dict[str, Any] | None = None
    for child in _direct(proc, "procedure_parameter_list"):
        for item in child.get("children", []):
            kind = item.get("type")
            value = _text(item).strip()
            if kind == "raw" and value.startswith("@"):
                if entry is not None:
                    parameters.append(entry)
                entry = {"name": value, "data_type": None, "line": item.get("line")}
            elif kind == "data_type" and entry is not None and entry["data_type"] is None:
                entry["data_type"] = _compact(item)
    if entry is not None:
        parameters.append(entry)
    return parameters


def _extract_column(node: dict[str, Any], file: str) -> dict[str, Any]:
    children = node.get("children", [])
    first_identifier = next((item for item in children if item.get("type") in {"identifier", "raw", "quoted_identifier"}), None)
    dtype = next((item for item in children if item.get("type") == "data_type"), None)
    sql = _compact(node)
    return {
        "name": _name(first_identifier),
        "data_type": _compact(dtype) if dtype else None,
        "nullable": (False if re.search(r"\bNOT\s+NULL\b", sql, re.I)
                     else True if re.search(r"\bNULL\b", sql, re.I) else None),
        "primary_key": bool(re.search(r"\bPRIMARY\s+KEY\b", sql, re.I)),
        "definition": sql,
        "source": _source(file, node.get("line")),
    }


def _column_names_from_bracket(node: dict[str, Any] | None) -> list[str]:
    return [_name(child) for child in _find(node, "column_reference")] if node else []


def _fk_constraint(node: dict[str, Any], table: str, file: str) -> dict[str, Any] | None:
    if not re.search(r"\bFOREIGN\s+KEY\b", _compact(node), re.I):
        return None
    ref = _first(node, "references_constraint_grammar")
    ref_table = _first(ref, "table_reference") if ref else None
    if ref is None or ref_table is None:
        return None
    local_bracket = next(iter(_direct(node, "bracketed")), None)
    remote_bracket = next(iter(_direct(ref, "bracketed")), None)
    named = next(iter(_direct(node, "object_reference")), None)
    return {
        "name": _name(named),
        "columns": _column_names_from_bracket(local_bracket),
        "references_table": _name(ref_table),
        "references_columns": _column_names_from_bracket(remote_bracket),
        "source": _source(file, node.get("line")),
    }


def _table_definition(node: dict[str, Any], file: str, ast_file: str) -> dict[str, Any] | None:
    tableref = next(iter(_direct(node, "table_reference")), None)
    if tableref is None:
        return None
    name = _name(tableref)
    foreign_keys = []
    for constraint in _find(node, "table_constraint"):
        fk = _fk_constraint(constraint, name, file)
        if fk:
            foreign_keys.append(fk)
    return {
        "name": name,
        "columns": [_extract_column(c, file) for c in _find(node, "column_definition")],
        "foreign_keys": foreign_keys,
        "source": _source(file, node.get("line"), ast_file),
    }


def _split_top_level_items(node: dict[str, Any]) -> list[str]:
    """Split one AST bracketed list on *direct* commas (not nested commas)."""
    result: list[str] = []
    current: list[str] = []
    for child in node.get("children", []):
        if child.get("type") in {"indent", "dedent"}:
            continue
        if child.get("type") == "symbol" and child.get("text") in {"(", ")"}:
            continue
        if child.get("type") == "symbol" and child.get("text") == ",":
            value = " ".join("".join(current).split())
            result.append(value)
            current = []
        else:
            current.append(_text(child))
    value = " ".join("".join(current).split())
    if value or current:
        result.append(value)
    return result


def _effects(node: dict[str, Any], op_type: str) -> dict[str, Any]:
    """Return source-based data effects, without inferring business semantics."""
    if op_type == "UPDATE":
        assignments = []
        for set_clause in _find(node, "set_clause"):
            ref = _first(set_clause, "column_reference")
            op = _first(set_clause, "assignment_operator")
            value = next(iter(_direct(set_clause, "expression")), None)
            assignments.append({
                "column": _name(ref),
                "operator": _compact(op) if op else None,
                "expression": _compact(value) if value is not None else None,
                "line": set_clause.get("line"),
            })
        return {"assignments": assignments}
    if op_type == "INSERT":
        bracket = next(iter(_direct(node, "bracketed")), None)
        columns = _column_names_from_bracket(bracket)
        values = next(iter(_direct(node, "values_clause")), None)
        result: dict[str, Any] = {"columns": columns, "values_expression": _compact(values) if values else None}
        if values:
            rows = _direct(values, "bracketed")
            if len(rows) == 1:
                expressions = _split_top_level_items(rows[0])
                if columns and len(expressions) == len(columns):
                    result["column_values"] = [
                        {"column": col, "expression": expr} for col, expr in zip(columns, expressions)
                    ]
        return result
    return {"sql": _compact(node)}


def _select_effects(node: dict[str, Any]) -> dict[str, Any]:
    """Recognize SQL Server SELECT @variable = expression without guessing intent."""
    assignments = []
    for clause in _find(node, "select_clause_element"):
        expression = next(iter(_direct(clause, "expression")), None)
        if expression is None:
            continue
        children = [c for c in expression.get("children", [])
                    if c.get("type") not in {"whitespace", "newline", "indent", "dedent"}]
        if len(children) >= 3 and children[0].get("type") == "raw" and _text(children[0]).startswith("@") \
                and children[1].get("type") in {"comparison_operator", "assignment_operator"} \
                and _compact(children[1]) == "=":
            assignments.append({"variable": _text(children[0]),
                                "expression": " ".join("".join(_text(c) for c in children[2:]).split()),
                                "line": clause.get("line")})
    return {"variable_assignments": assignments} if assignments else {}


def _extract_events(trigger: dict[str, Any]) -> list[str]:
    """Identify DML events in the trigger header before AS."""
    events: list[str] = []
    after_on = False
    for child in trigger.get("children", []):
        if child.get("type") != "keyword":
            continue
        token = _text(child).upper()
        if token == "AS":
            break
        if token in {"AFTER", "INSTEAD", "INSTEAD OF", "FOR"}:
            after_on = True
        if after_on and token in {"INSERT", "UPDATE", "DELETE"}:
            events.append(token)
    return list(dict.fromkeys(events))


def _parse_exec(sql: str) -> tuple[str | None, str]:
    match = re.match(r"(?is)^exec(?:ute)?\s+(.+)$", sql)
    if match is None:
        return None, "unrecognized"
    rest = re.sub(r"(?is)^@\w+\s*=\s*", "", match.group(1).strip())
    if not rest or rest[0] in "(@'\"":
        return None, "dynamic"
    identifier = r"(?:\[[^\]]+\]|[A-Za-z_#][\w#]*)"
    name_match = re.match(rf"^({identifier}(?:\s*\.\s*{identifier})*)", rest)
    if not name_match:
        return None, "unresolved"
    return re.sub(r"\s+", "", name_match.group(1)), "static"


def _routine(node: dict[str, Any], kind: str, file: str, ast_file: str,
             relationships: list[dict[str, Any]], issues: list[dict[str, Any]]) -> dict[str, Any] | None:
    ref_type = "trigger_reference" if kind == "trigger" else "object_reference"
    ref = next(iter(_direct(node, ref_type)), None)
    name = _name(ref)
    if not name:
        issues.append({"code": "OBJECT_NAME_MISSING", "severity": "warning", "source_file": file, "line": node.get("line")})
        return None
    table = None
    events: list[str] = []
    if kind == "trigger":
        table_ref = next(iter(_direct(node, "table_reference")), None)
        table = _name(table_ref)
        events = _extract_events(node)
        if table:
            relationships.append({
                "from": {"type": kind, "name": name}, "to": {"type": "table", "name": table},
                "type": "TRIGGERS_ON", "events": events, "source": _source(file, table_ref.get("line")),
            })
    entity = {"name": name, "object_type": kind,
              "source": _source(file, node.get("line"), ast_file),
              "parameters": _param_list(node) if kind == "procedure" else [],
              "operations": [], "conditions": [], "calls": []}
    if kind == "trigger":
        entity["table"] = table
        entity["events"] = events

    def record_table_relation(table_name: str, relation: str, line: int | None,
                              context: list[dict[str, Any]]) -> None:
        relationships.append({
            "from": {"type": kind, "name": name},
            "to": {"type": "table", "name": table_name},
            "type": relation, "source": _source(file, line),
            "condition_context": list(context),
        })

    def visit(segment: dict[str, Any], context: list[dict[str, Any]]) -> None:
        typ = segment.get("type", "")
        if typ == "if_then_statement":
            cond = next(iter(_direct(segment, "if_clause")), None)
            if cond:
                expression = re.sub(r"(?is)^IF\s+", "", _compact(cond)).strip()
                entry = _condition(expression, "IF", cond, file)
                entity["conditions"].append(entry)
                # Conditions can contain SELECTs (for example IF EXISTS).
                for child in cond.get("children", []):
                    visit(child, context)
                for child in segment.get("children", []):
                    ctype = child.get("type")
                    if ctype == "if_clause":
                        continue
                    if ctype in {"else_clause", "elif_clause"}:
                        alternative = _condition(f"ALTERNATIVE_TO_IF_AT_LINE_{cond.get('line')}", "ELSE_BRANCH", child, file)
                        visit(child, context + [alternative])
                    else:
                        visit(child, context + [entry])
                return

        if typ in DML_TYPES:
            op_type = DML_TYPES[typ]
            target = next(iter(_direct(segment, "table_reference")), None)
            target_name = _name(target)
            wheres = [_condition(re.sub(r"(?is)^WHERE\s+", "", _compact(w)).strip(), "WHERE", w, file)
                      for w in _direct(segment, "where_clause")]
            entity["conditions"].extend(wheres)
            op = {"operation": op_type, "target_table": target_name,
                  "source": _source(file, segment.get("line")),
                  "condition_context": list(context),
                  "where_conditions": wheres, "effects": _effects(segment, op_type)}
            entity["operations"].append(op)
            if target_name:
                record_table_relation(target_name, RELATION_NAMES[op_type], segment.get("line"), context + wheres)
            else:
                issues.append({"code": "DML_TARGET_UNRESOLVED", "severity": "warning", "source_file": file,
                               "line": segment.get("line"), "object": name})
            # Read subqueries nested in DML expressions, without reprocessing DML.
            for child in segment.get("children", []):
                if child.get("type") not in {"table_reference", "where_clause", "set_clause_list", "values_clause"}:
                    visit(child, context + wheres)
                else:
                    for sub in _find(child, "select_statement"):
                        visit(sub, context + wheres)
            return

        if typ == "select_statement":
            tables = sorted({_name(ref) for ref in _find(segment, "table_reference") if _name(ref)}, key=str.casefold)
            wheres = [_condition(re.sub(r"(?is)^WHERE\s+", "", _compact(w)).strip(), "WHERE", w, file)
                      for w in _direct(segment, "where_clause")]
            entity["conditions"].extend(wheres)
            entity["operations"].append({
                "operation": "SELECT", "tables": tables,
                "source": _source(file, segment.get("line")), "condition_context": list(context),
                "where_conditions": wheres, "effects": _select_effects(segment),
            })
            for table_name in tables:
                record_table_relation(table_name, "READS", segment.get("line"), context + wheres)
            return

        if typ in {"throw_statement", "raiserror_statement", "return_segment", "transaction_statement"}:
            entity["operations"].append({
                "operation": {"throw_statement": "THROW", "raiserror_statement": "RAISERROR",
                              "return_segment": "RETURN", "transaction_statement": "TRANSACTION"}[typ],
                "source": _source(file, segment.get("line")),
                "condition_context": list(context), "effects": {"statement": _compact(segment)},
            })
            return

        if typ in EXEC_TYPES or (typ == "statement" and re.match(r"(?is)^EXEC(?:UTE)?\s+", _compact(segment))
                                      and not any(c.get("type") in EXEC_TYPES for c in _walk(segment) if c is not segment)):
            sql = _compact(segment)
            called, resolution = _parse_exec(sql)
            entity["calls"].append({"procedure": called, "resolution": resolution,
                                    "expression": sql, "source": _source(file, segment.get("line")),
                                    "condition_context": list(context)})
            # Preserve static procedure invocation as an ordered workflow step.
            # This is essential for tracing business rules split across procedures.
            entity["operations"].append({
                "operation": "CALL", "called_object": called,
                "resolution": resolution, "source": _source(file, segment.get("line")),
                "condition_context": list(context),
                "effects": {"statement": sql, "called_object": called},
            })
            if called:
                relationships.append({
                    "from": {"type": kind, "name": name},
                    "to": {"type": "procedure", "name": called},
                    "type": "CALLS", "source": _source(file, segment.get("line")),
                    "condition_context": list(context),
                })
            else:
                issues.append({"code": "DYNAMIC_OR_UNRESOLVED_EXEC", "severity": "warning", "source_file": file,
                               "line": segment.get("line"), "object": name})
            return

        for child in segment.get("children", []):
            visit(child, context)

    # Only visit executable bodies, not declaration and header segments.
    if kind == "procedure":
        bodies = _direct(node, "procedure_statement")
    else:
        bodies = [child for child in node.get("children", []) if child.get("type") == "statement"]
    for body in bodies:
        visit(body, [])
    return entity


def _augment_static_calls_from_source(entity: dict[str, Any], sql: str, file: str,
                                      relationships: list[dict[str, Any]]) -> None:
    """Safeguard for SQLFluff dialect differences around static EXEC statements.

    The AST remains primary. This conservative fallback records only literal
    EXEC names on their own source line. It never executes the statement.
    """
    known_lines = {entry['source']['line'] for entry in entity.get('calls', [])
                   if isinstance(entry.get('source'), dict) and isinstance(entry['source'].get('line'), int)}
    for line, text in enumerate(sql.splitlines(), 1):
        if line in known_lines:
            continue
        if not re.match(r'(?i)^\s*EXEC(?:UTE)?\s+', text):
            continue
        called, resolution = _parse_exec(text.strip())
        if called is None or resolution != 'static':
            continue
        source = _source(file, line)
        entity['calls'].append({'procedure': called, 'resolution': 'static_source_fallback',
                                'expression': text.strip(), 'source': source,
                                'condition_context': []})
        entity['operations'].append({'operation':'CALL', 'called_object':called,
                                     'resolution':'static_source_fallback', 'source':source,
                                     'condition_context':[],
                                     'effects':{'statement':text.strip(), 'called_object':called}})
        relationships.append({'from':{'type':entity['object_type'],'name':entity['name']},
                              'to':{'type':'procedure','name':called},
                              'type':'CALLS','source':source,'condition_context':[]})
    # Preserve simple variable assignments even if the AST's SET dialect node
    # is not recognized; these values control calls and final decisions.
    set_lines = {((op.get('source') or {}).get('line')) for op in entity['operations']
                 if op.get('operation') == 'SET'}
    for line, text in enumerate(sql.splitlines(), 1):
        if line in set_lines:
            continue
        m = re.match(r'(?i)^\s*SET\s+(@\w+)\s*=\s*(.+?)\s*;?\s*$', text)
        if m:
            entity['operations'].append({'operation':'SET', 'source':_source(file,line),
                'condition_context':[],
                'effects':{'variable_assignments':[{'variable':m.group(1),
                    'expression':m.group(2).rstrip(';').strip(),'line':line}]}})
    entity['operations'].sort(key=lambda op:(op.get('source') or {}).get('line') or 0)


def build_inventory(ast_manifest_path: str | Path, source_manifest_path: str | Path,
                    destination: str | Path, *, on_event: EventCallback | None = None) -> dict[str, Any]:
    """Create structural metadata from the current run's SQL AST and catalogs.

    Args:
        ast_manifest_path: The ``ast_files/ast_manifest.json`` from SQL AST Parsers.
        source_manifest_path: The ``sources_manifest.json`` from Source Extractor.
        destination: An output folder, typically ``workspace/run_001/metadata``.
        on_event: Optional structured progress callback for a future MCP adapter.

    Returns:
        A JSON-serializable summary of the generated metadata artifacts.

    The builder never confirms a business rule and never calls an LLM.
    """
    ast_path = Path(ast_manifest_path).expanduser().resolve()
    src_path = Path(source_manifest_path).expanduser().resolve()
    ast_dir = ast_path.parent
    src_dir = src_path.parent
    out_dir = Path(destination).expanduser().resolve()
    if (out_dir in {ast_dir, src_dir} or out_dir in ast_dir.parents or out_dir in src_dir.parents
            or out_dir.is_relative_to(ast_dir)):
        raise ValueError("Destination cannot replace or nest within a source or AST directory")
    ast_manifest = _load_object(ast_path)
    source_manifest = _load_object(src_path)
    if not isinstance(ast_manifest.get("files"), list) or not isinstance(source_manifest.get("files"), list):
        raise ValueError("Both manifests must contain 'files' arrays")
    source_entries = {e["relative_path"]: e for e in source_manifest["files"]
                      if isinstance(e, dict) and isinstance(e.get("relative_path"), str)}
    inventory: dict[str, Any] = {
        "schema_version": "1.0", "generated_utc": _now(),
        "note": "Structural and syntactic evidence only; no business rule is confirmed.",
        "tables": [], "procedures": [], "triggers": [],
    }
    relationships: list[dict[str, Any]] = []
    catalogs: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for index, entry in enumerate(ast_manifest["files"], 1):
        if not isinstance(entry, dict):
            issues.append({"code": "INVALID_AST_MANIFEST_ENTRY", "severity": "error"})
            continue
        file = entry.get("source_file")
        ast_file = entry.get("ast_file")
        if not isinstance(file, str) or not isinstance(ast_file, str):
            issues.append({"code": "INVALID_AST_PATH", "severity": "error", "entry": index})
            continue
        try:
            sql_path = resolve_manifest_path(src_dir, file)
            tree_path = resolve_manifest_path(ast_dir, ast_file)
            src_entry = source_entries.get(file)
            if src_entry is None:
                raise ValueError("File is missing from source manifest")
            expected = src_entry.get("sha256")
            if expected and hashlib.sha256(sql_path.read_bytes()).hexdigest().lower() != expected.lower():
                raise ValueError("Source checksum differs from its manifest")
            artifact = _load_object(tree_path)
            if artifact.get("source_file") != file:
                raise ValueError("AST source_file does not match manifest")
            if artifact.get("status") not in {"ok", "partial"} or not isinstance(artifact.get("tree"), dict):
                raise ValueError("AST is missing, invalid, or has error status")
            if artifact["status"] == "partial":
                issues.append({"code": "PARTIAL_AST", "severity": "warning", "source_file": file,
                               "details": artifact.get("parse_errors", []) + artifact.get("unparsed_regions", [])})
            node = artifact["tree"]
            for table_node in _find(node, "create_table_statement"):
                table = _table_definition(table_node, file, ast_file)
                if table:
                    inventory["tables"].append(table)
                    for fk in table["foreign_keys"]:
                        relationships.append({
                            "from": {"type": "table", "name": table["name"]},
                            "to": {"type": "table", "name": fk["references_table"]},
                            "type": "FOREIGN_KEY", "columns": fk["columns"],
                            "references_columns": fk["references_columns"],
                            "constraint_name": fk["name"], "source": fk["source"],
                        })
            for proc_node in _find(node, "create_procedure_statement"):
                result = _routine(proc_node, "procedure", file, ast_file, relationships, issues)
                if result:
                    _augment_static_calls_from_source(result, sql_path.read_text(encoding='utf-8-sig'),
                                                      file, relationships)
                    inventory["procedures"].append(result)
            for trigger_node in _find(node, "create_trigger"):
                result = _routine(trigger_node, "trigger", file, ast_file, relationships, issues)
                if result:
                    inventory["triggers"].append(result)
            if on_event:
                on_event({"event": "ast_analyzed", "current": index, "total": len(ast_manifest["files"]),
                          "source_file": file, "status": "ok"})
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            issues.append({"code": "AST_PROCESSING_FAILED", "severity": "error", "source_file": file,
                           "message": str(exc)})
            if on_event:
                on_event({"event": "ast_analyzed", "current": index, "total": len(ast_manifest["files"]),
                          "source_file": file, "status": "error"})

    for source_entry in source_manifest["files"]:
        if not isinstance(source_entry, dict) or source_entry.get("file_type") != "catalog":
            continue
        relative = source_entry.get("relative_path")
        try:
            path = resolve_manifest_path(src_dir, relative)
            checksum = source_entry.get("sha256")
            if checksum and hashlib.sha256(path.read_bytes()).hexdigest().lower() != checksum.lower():
                raise ValueError("Catalog checksum differs from source manifest")
            document = _load_object(path)
            if document.get("type") != "catalog" or not isinstance(document.get("values"), list):
                raise ValueError("Catalog must contain type='catalog' and a values array")
            catalogs.append({
                "name": document.get("name"), "table": document.get("table"),
                "key_column": document.get("key_column"),
                "description_column": document.get("description_column"),
                "values": document["values"], "source_file": relative,
                "applies_to": document.get("applies_to", []),
                "record_count": len(document["values"]),
            })
            if on_event:
                on_event({"event": "catalog_loaded", "source_file": relative,
                          "records": len(document["values"])})
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            issues.append({"code": "CATALOG_PROCESSING_FAILED", "severity": "error", "source_file": relative,
                           "message": str(exc)})

    inventory["tables"].sort(key=lambda e: e["name"].casefold())
    inventory["procedures"].sort(key=lambda e: e["name"].casefold())
    inventory["triggers"].sort(key=lambda e: e["name"].casefold())
    catalogs.sort(key=lambda e: (e.get("table") or e.get("name") or "").casefold())
    relationships.sort(key=lambda e: (e["source"].get("file", ""), e["source"].get("line") or 0,
                                      e["type"], e["to"]["name"].casefold()))
    counts = dict(sorted(Counter(item["type"] for item in relationships).items()))
    write_json(out_dir / "inventory.json", inventory)
    write_json(out_dir / "relationships.json", {
        "schema_version": "1.0", "generated_utc": inventory["generated_utc"],
        "relationship_types": counts, "relationships": relationships,
    })
    write_json(out_dir / "catalogs.json", {
        "schema_version": "1.0", "generated_utc": inventory["generated_utc"],
        "catalogs": catalogs,
    })
    write_json(out_dir / "extraction_issues.json", {
        "schema_version": "1.0", "generated_utc": inventory["generated_utc"],
        "issues": issues,
    })
    result = {
        "status": "partial" if issues else "ok",
        "destination": str(out_dir),
        "summary": {"tables": len(inventory["tables"]),
                    "procedures": len(inventory["procedures"]),
                    "triggers": len(inventory["triggers"]),
                    "catalogs": len(catalogs),
                    "relationships": len(relationships), "issues": len(issues)},
        "generated_files": ["inventory.json", "relationships.json", "catalogs.json", "extraction_issues.json"],
    }
    if on_event:
        on_event({"event": "inventory_completed", "status": result["status"], **result["summary"]})
    return result
