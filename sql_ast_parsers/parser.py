"""Parse collected SQL sources into SQLFluff segment trees.

Input: the source collector's ``sources_manifest.json`` into de workspace\run_### folder

Output: one ``*.sql.ast.json`` file per eligible SQL source, and
``ast_manifest.json`` describing the outcome for each file.

The parser does not execute SQL, infer business rules, or call an LLM.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable

from lib.artifact_io import read_sql_text, write_json

EventCallback = Callable[[dict[str, Any]], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _node_position(node: Any) -> dict[str, int]:
    """Read the original SQL line and column, when SQLFluff provides them."""
    marker = getattr(node, "pos_marker", None)
    if marker is None:
        return {}
    try:
        line, column = marker.source_position()
        return {"line": int(line), "column": int(column)}
    except (AttributeError, TypeError, ValueError):
        return {}


def _serialize_node(node: Any, unknown_regions: list[dict[str, Any]]) -> dict[str, Any]:
    """Serialize a SQLFluff segment tree, keeping source positions."""
    node_type = str(getattr(node, "type", type(node).__name__))
    entry: dict[str, Any] = {"type": node_type, **_node_position(node)}
    children = getattr(node, "segments", ())
    if node_type in {"unparsable", "unlexable"}:
        unknown_regions.append({
            "type": node_type,
            **_node_position(node),
            "fragment": str(getattr(node, "raw", ""))[:250],
        })
    if children:
        entry["children"] = [_serialize_node(child, unknown_regions) for child in children]
    else:
        entry["text"] = str(getattr(node, "raw", ""))
    return entry


def _parse_violation(issue: Any) -> dict[str, Any]:
    """Normalize SQLFluff syntax and lexing violations."""
    try:
        code = str(issue.rule_code())
    except (AttributeError, TypeError):
        code = type(issue).__name__
    try:
        description = str(issue.desc())
    except (AttributeError, TypeError):
        description = str(issue)
    return {
        "code": code,
        "line": getattr(issue, "line_no", None),
        "column": getattr(issue, "line_pos", None),
        "message": description,
    }


def _parse_one(source: Path, source_relative: str, file_type: str,
               objects: list[Any], linter: Any) -> dict[str, Any]:
    """Create one syntactic AST artifact without interpreting business meaning."""
    artifact: dict[str, Any] = {
        "schema_version": "1.0",
        "source_file": source_relative,
        "source_type": file_type,
        "source_objects": objects,
        "parser": "SQLFluff (tsql)",
        "tree_type": "syntactic_segment_tree",
        "status": "error",
        "parse_errors": [],
        "unparsed_regions": [],
        "tree": None,
    }
    try:
        parsed = linter.parse_string(read_sql_text(source), fname=str(source))
        if parsed.tree is None:
            artifact["parse_errors"] = [_parse_violation(e) for e in parsed.violations]
            if not artifact["parse_errors"]:
                artifact["parse_errors"] = [{"code": "NO_TREE", "message": "Parser returned no syntax tree"}]
            return artifact
        unknown_regions: list[dict[str, Any]] = []
        artifact["tree"] = _serialize_node(parsed.tree, unknown_regions)
        artifact["unparsed_regions"] = unknown_regions
        # SQLFluff layout/style lint violations are not parse failures.
        violations = [_parse_violation(e) for e in parsed.violations]
        artifact["parse_errors"] = [
            e for e in violations if e["code"] in {"PRS", "LXR", "SQLParseError", "SQLLexError"}
        ]
        artifact["status"] = "partial" if unknown_regions or artifact["parse_errors"] else "ok"
    except Exception as exc:
        artifact["parse_errors"] = [{"code": type(exc).__name__, "message": str(exc)}]
    return artifact


def _safe_source_path(manifest_dir: Path, relative: str) -> Path:
    """Resolve a manifest file path without allowing traversal outside its run."""
    portable = PurePosixPath(relative.replace("\\", "/"))
    windows_path = PureWindowsPath(relative)
    if (not relative.strip() or portable.is_absolute() or windows_path.is_absolute()
            or windows_path.drive or ".." in portable.parts):
        raise ValueError(f"Unsafe relative path in source manifest: {relative!r}")
    path = manifest_dir.joinpath(*portable.parts).resolve()
    if not path.is_relative_to(manifest_dir):
        raise ValueError(f"Source file escapes its run directory: {relative!r}")
    return path


def _eligible(entry: dict[str, Any]) -> bool:
    """Use source metadata to include SQL, never catalog or schema JSON. File must be SQL.
    suggested_processors must have "ast" value
    """
    relative = entry.get("relative_path", "")
    if not isinstance(relative, str) or not relative.lower().endswith(".sql"):
        return False
    processors = entry.get("suggested_processors")
    if processors is not None:
        return isinstance(processors, list) and "ast" in processors
    return entry.get("file_type") not in {"catalog", "metadata"}


def _new_linter() -> tuple[Any, str]:
    try:
        import sqlfluff
        from sqlfluff.core import FluffConfig, Linter
    except ImportError as exc:
        raise RuntimeError(
            'SQLFluff is required. Install it in cmu-llm: python -m pip install "sqlfluff==3.3.1"'
        ) from exc
    return Linter(config=FluffConfig(overrides={"dialect": "tsql", "templater": "raw"})), sqlfluff.__version__


def parse_sql_sources(manifest_path: str | Path, destination: str | Path, *,
                      max_files: int = 0, on_event: EventCallback | None = None,
                      linter: Any = None) -> dict[str, Any]:
    """Parse SQL files explicitly listed by a source collector manifest.

    Args:
        manifest_path: Path of ``sources_manifest.json`` for the current run.
        destination: Output directory, typically ``workspace/run_001/ast_files``.
        max_files: Optional limit for controlled testing; 0 parses all SQL files.
        on_event: Optional progress callback; suitable for a future MCP adapter.
        linter: Optional injected SQLFluff-compatible linter for unit testing.

    Returns:
        The JSON-serializable AST manifest. Each source has an individual
        ``ok``, ``partial`` or ``error`` result. Parsing continues after errors.

    Raises:
        ValueError: Invalid input manifest, unsafe paths, or invalid arguments.
        RuntimeError: SQLFluff is unavailable (unless a linter is injected).
    """
    if max_files < 0:
        raise ValueError("max_files cannot be negative")
    
    manifest_file = Path(manifest_path).expanduser().resolve()
    run_directory = manifest_file.parent
    document = json.loads(manifest_file.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict) or not isinstance(document.get("files"), list):
        raise ValueError("Source manifest must contain a 'files' array")

    output = Path(destination).expanduser().resolve()
    if output == manifest_file or output == run_directory or output in run_directory.parents:
        raise ValueError("AST destination must be a separate directory within or outside the run")

    # filter the files to select only the stored procedures
    entries: list[dict[str, Any]] = []
    for entry in document["files"]:
        if not isinstance(entry, dict):
            raise ValueError("Each 'files' entry must be a JSON object")
        if _eligible(entry):
            entries.append(entry)

    entries.sort(key=lambda item: item["relative_path"].casefold())
    if max_files:
        entries = entries[:max_files]

    # Do not import SQLFluff for empty manifests or when a fake linter is injected.
    if linter is None and entries:
        linter, sqlfluff_version = _new_linter()
    else:
        sqlfluff_version = "injected-test-linter" if linter is not None else "not-required"

    output.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    results: list[dict[str, Any]] = []
    for index, entry in enumerate(entries, 1):
        relative = entry["relative_path"]
        path = _safe_source_path(run_directory, relative)
        artifact_relative = PurePosixPath(relative.replace("\\", "/") + ".ast.json")
        ast_file = output.joinpath(*artifact_relative.parts)
        file_type = str(entry.get("file_type", "unknown"))
        try:
            if not path.is_file():
                raise FileNotFoundError(f"Source file does not exist: {relative}")
            expected_sha = entry.get("sha256")
            if isinstance(expected_sha, str) and expected_sha:
                actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
                if actual_sha.lower() != expected_sha.lower():
                    raise ValueError(f"Source checksum mismatch: {relative}")
            artifact = _parse_one(path, relative, file_type,
                                  entry.get("objects", []), linter)
        except (OSError, ValueError) as exc:
            artifact = {
                "schema_version": "1.0", "source_file": relative,
                "source_type": file_type, "source_objects": entry.get("objects", []),
                "parser": "SQLFluff (tsql)", "tree_type": "syntactic_segment_tree",
                "status": "error", "parse_errors": [
                    {"code": type(exc).__name__, "message": str(exc)}],
                "unparsed_regions": [], "tree": None,
            }
        write_json(ast_file, artifact)
        status = artifact["status"]
        counts[status] += 1
        record = {
            "source_file": relative,
            "source_type": file_type,
            "ast_file": ast_file.relative_to(output).as_posix(),
            "status": status,
            "parse_errors": len(artifact["parse_errors"]),
            "unparsed_regions": len(artifact["unparsed_regions"]),
        }
        results.append(record)
        event = {"event": "sql_parsed", "current": index, "total": len(entries), **record}
        if on_event is not None:
            on_event(event)

    summary = {
        "files_selected": len(entries),
        "ok": counts["ok"], "partial": counts["partial"], "error": counts["error"],
        "source_files_ignored": len(document["files"]) - len(entries),
    }
    ast_manifest = {
        "schema_version": "1.0", "timestamp_utc": _utc_now(),
        "source_manifest": str(manifest_file),
        "destination": str(output),
        "parser": f"SQLFluff {sqlfluff_version} (tsql)",
        "status": "error" if counts["error"] else ("partial" if counts["partial"] else "ok"),
        "note": "Syntactic evidence only; these AST segments are not verified business rules.",
        "summary": summary, "files": results,
    }
    write_json(output / "ast_manifest.json", ast_manifest)
    if on_event is not None:
        on_event({"event": "ast_completed", "status": ast_manifest["status"], **summary})
    return ast_manifest
