"""Collect SQL Server source artifacts

The mock backend discovers and copies local SQL scripts and catalog JSON files.
The ODBC backend reads SQL Server metadata and explicitly allowlisted catalogs.

The public ``collect_sources`` function accepts an optional event callback, so a
future FastMCP adapter can forward progress messages to its MCP client.

Execute

& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python source_extractor\extractor.py --config config\mock_req_dummy_sistema1_sources.json --destination workspace\run_002

"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from collections import Counter
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping

import  utils 




# Lightweight declaration recognition, not a substitute for a T-SQL AST parser.
_DECLARATION = re.compile(
    r"\b(?:CREATE\s+(?:OR\s+ALTER\s+)?|ALTER\s+)"
    r"(?P<kind>PROC(?:EDURE)?|TRIGGER|TABLE|SCHEMA)\s+"
    r"(?P<name>(?:\[[^\]]+\]|[\w#]+)(?:\s*\.\s*(?:\[[^\]]+\]|[\w#]+))?)",
    re.IGNORECASE,
)
_OBJECT_KIND = {
    "PROC": "procedure",
    "PROCEDURE": "procedure",
    "TRIGGER": "trigger",
    "TABLE": "table",
    "SCHEMA": "schema",
}




def _read_sql(path: Path) -> str:
    """Decode common SQL Server script encodings without rewriting the file."""
    payload = path.read_bytes()
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        return payload.decode("utf-16")
    if payload.startswith(b"\xef\xbb\xbf"):
        return payload.decode("utf-8-sig")
    for encoding in ("utf-8", "cp1252"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("latin-1")


def _mask_comments_and_literals(sql: str) -> str:
    """Replace SQL comments and string literals with spaces, preserving lines."""
    output: list[str] = []
    position, depth, state = 0, 0, "normal"
    while position < len(sql):
        char = sql[position]
        next_char = sql[position + 1] if position + 1 < len(sql) else ""
        if state == "normal":
            if char == "-" and next_char == "-":
                state = "line_comment"
                output.extend("  ")
                position += 2
                continue
            if char == "/" and next_char == "*":
                state, depth = "block_comment", 1
                output.extend("  ")
                position += 2
                continue
            if char == "'":
                state = "literal"
                output.append(" ")
                position += 1
                continue
            output.append(char)
        elif state == "line_comment":
            output.append("\n" if char == "\n" else " ")
            if char == "\n":
                state = "normal"
        elif state == "block_comment":
            if char == "/" and next_char == "*":
                depth += 1
                output.extend("  ")
                position += 2
                continue
            if char == "*" and next_char == "/":
                depth -= 1
                output.extend("  ")
                position += 2
                if depth == 0:
                    state = "normal"
                continue
            output.append("\n" if char == "\n" else " ")
        else:
            if char == "'" and next_char == "'":
                output.extend("  ")
                position += 2
                continue
            output.append("\n" if char == "\n" else " ")
            if char == "'":
                state = "normal"
        position += 1
    return "".join(output)


def classify_sql(content: str) -> dict[str, Any]:
    """Detect declared SQL object types and source lines heuristically.

    Returns a ``file_type`` of ``schema``, ``procedure``, ``trigger``, ``mixed``
    or ``unclassified`` and the individual object declarations.
    """
    masked = _mask_comments_and_literals(content)
    objects = []
    for match in _DECLARATION.finditer(masked):
        object_kind = _OBJECT_KIND[match.group("kind").upper()]
        objects.append({
            "type": object_kind,
            "name": re.sub(r"\s+", "", match.group("name")),
            "line": masked.count("\n", 0, match.start()) + 1,
        })
    detected_types = sorted({item["type"] for item in objects})
    file_types = {"schema" if value in ("table", "schema") else value for value in detected_types}
    file_type = next(iter(file_types)) if len(file_types) == 1 else ("mixed" if file_types else "unclassified")
    return {"file_type": file_type, "detected_types": detected_types, "objects": objects}


def validate_catalog(document: Any, source_name: str) -> dict[str, Any]:
    """Validate a catalog document while preserving its original code values."""
    if not isinstance(document, dict) or document.get("type") != "catalog":
        raise ValueError(f"{source_name}: expected type='catalog'")
    for key in ("name", "table", "key_column", "description_column"):
        if not isinstance(document.get(key), str) or not document[key].strip():
            raise ValueError(f"{source_name}: missing non-empty '{key}'")
    values = document.get("values")
    if not isinstance(values, list):
        raise ValueError(f"{source_name}: 'values' must be an array")
    seen: set[str] = set()
    for index, row in enumerate(values):
        if not isinstance(row, dict) or "code" not in row or "description" not in row:
            raise ValueError(f"{source_name}: values[{index}] requires code and description")
        code = row["code"]
        if code is None or isinstance(code, (dict, list, bool)):
            raise ValueError(f"{source_name}: invalid code in values[{index}]")
        if not isinstance(row["description"], str):
            raise ValueError(f"{source_name}: invalid description in values[{index}]")
        code_key = json.dumps(code, ensure_ascii=False, sort_keys=True)
        if code_key in seen:
            raise ValueError(f"{source_name}: duplicate code {code!r}")
        seen.add(code_key)
    return document


def _serialize(value: Any) -> str:
    """Convert supported SQL Server scalar values for JSON serialization."""
    if isinstance(value, (date, datetime, time, Decimal)):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _write_json(path: Path, content: Any) -> None:
    """Write a UTF-8 JSON file with stable indentation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2, ensure_ascii=False, default=_serialize) + "\n", encoding="utf-8")


def _register_file(manifest: dict[str, Any], destination: Path, path: Path,
                   file_type: str, source: str, objects: list[dict[str, Any]],
                   *, processors: list[str] | None = None) -> None:
    """Add a provenance-rich file record to the output manifest."""
    payload = path.read_bytes()
    extension = path.suffix.lower().lstrip(".")
    manifest["files"].append({
        "file_type": file_type,
        "objects": objects,
        "format": extension,
        "relative_path": path.relative_to(destination).as_posix(),
        "absolute_path": str(path.resolve()),
        "source": source,
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "suggested_processors": processors if processors is not None else (["ast", "rag"] if extension == "sql" else ["rag"]),
    })


def _validate_mock_paths(source: Path, destination: Path) -> None:
    """Prevent missing inputs and recursive or destructive copy locations."""
    if not source.is_dir():
        raise ValueError(f"Mock source directory does not exist: {source}")
    source, destination = source.resolve(), destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Source and destination directories must not overlap")


def _collect_mock(config: Mapping[str, Any], destination: Path,
                  manifest: dict[str, Any], onEventCallbackFunction: utils.EventCallback | None) -> None:
    """Discover and copy SQL declarations and catalog JSON files from a folder."""
    
    source_text = config.get("source_directory")
    
    if not isinstance(source_text, str) or not source_text.strip():
        raise ValueError("Mock mode requires a 'source_directory'")
    
    source = Path(source_text).expanduser().resolve()
    
    _validate_mock_paths(source, destination)
  

    sql_files = sorted((item for item in source.rglob("*") if item.is_file() and item.suffix.lower() == ".sql"),
                       key=lambda item: item.as_posix().casefold())
    catalog_files = sorted((item for item in source.rglob("*") if item.is_file() and item.suffix.lower() == ".json"
                            and ("catalogs" in [part.casefold() for part in item.relative_to(source).parts[:-1]]
                                 or item.name.casefold().endswith(".catalog.json"))),
                           key=lambda item: item.as_posix().casefold())
    utils._emitEvent(onEventCallbackFunction, "mock_started", directory=str(source), sql_count=len(sql_files), catalog_count=len(catalog_files))

    for path in sql_files:
        relative = path.relative_to(source)
        if not path.resolve().is_relative_to(source):
            manifest["issues"].append({"source": relative.as_posix(), "reason": "Symbolic link escapes the source directory"})
            continue
        try:
            classification = classify_sql(_read_sql(path))
            if classification["file_type"] == "unclassified":
                manifest["issues"].append({"source": relative.as_posix(), "reason": "No supported CREATE/ALTER declaration found"})
                utils._emitEvent(onEventCallbackFunction, "sql_unclassified", source=relative.as_posix())
                continue
            output = destination / "sql_sources" / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, output)
            _register_file(manifest, destination, output, classification["file_type"],
                           relative.as_posix(), classification["objects"])
            utils._emitEvent(onEventCallbackFunction, "file_collected", source=relative.as_posix(), file_type=classification["file_type"])
        except (OSError, UnicodeError) as exc:
            manifest["issues"].append({"source": relative.as_posix(), "reason": str(exc)})

    for path in catalog_files:
        relative = path.relative_to(source)
        if not path.resolve().is_relative_to(source):
            manifest["issues"].append({"source": relative.as_posix(), "reason": "Symbolic link escapes the source directory"})
            continue
        try:
            document = validate_catalog(json.loads(path.read_text(encoding="utf-8-sig")), relative.as_posix())
            subpath = Path(*relative.parts[1:]) if relative.parts[0].casefold() == "catalogs" else relative
            output = destination / "catalogs" / subpath
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, output)
            _register_file(manifest, destination, output, "catalog", relative.as_posix(),
                           [{"type": "catalog", "name": document["name"], "record_count": len(document["values"])}])
            utils._emitEvent(onEventCallbackFunction, "catalog_collected", name=document["name"], record_count=len(document["values"]))
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            manifest["issues"].append({"source": relative.as_posix(), "reason": str(exc)})
            utils._emitEvent(onEventCallbackFunction, "catalog_invalid", source=relative.as_posix(), reason=str(exc))


def _collect_odbc(config: Mapping[str, Any], destination: Path,
                  manifest: dict[str, Any], callback: utils.EventCallback | None) -> None:
    """Read SQL Server metadata and explicitly allowlisted catalog contents.
       Not yet implemented"""
    raise NotImplementedError("ODBC extraction is not implemented yet.")


def collect_sources(config: Mapping[str, Any], destination: str | Path,
                    onEventCallbackFunction: utils.EventCallback | None = None) -> dict[str, Any]:
    """Collect source artifacts and return a JSON-serializable manifest.

    Args:
        config: Mapping with ``mode`` (``mock`` or ``odbc``) and backend details.
        destination: New or empty destination directory for one extraction run.
        on_event: Optional callback receiving structured progress event dictionaries.

    Returns:
        Manifest with a file list, types, provenance, SHA-256 hashes, issues,
        summary, and proposed downstream processors.

    Raises:
        ValueError: Invalid inputs, unsafe paths, or non-empty destination.
        RuntimeError: Missing ODBC dependency or connection-related problems.

    The function is suitable for wrapping with FastMCP in a later phase.
    It does not execute extracted SQL or invoke an LLM.
    """
    # TODO: quitar información de FAstMCP cuando este terminado y verificar documentación

    mode = config.get("mode")
    if mode not in ("mock", "odbc"):
        raise ValueError("mode must be 'mock' or 'odbc'")
    
    output = Path(destination).expanduser().resolve()
  
    if mode == "mock":
        source = config.get("source_directory")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("Mock mode requires 'source_directory'")
        _validate_mock_paths(Path(source).expanduser().resolve(), output)

    output.mkdir(parents=True, exist_ok=True)
    manifestObj: dict[str, Any] = {
        "mode": mode,
        "system_name":config.get("system_name"),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "destination": str(output),
        "files": [],
        "issues": [],
       }
    try:

        if mode == "mock":
            _collect_mock(config, output, manifestObj, onEventCallbackFunction)
        else:
            _collect_odbc(config, output, manifestObj, onEventCallbackFunction)

    except Exception as exc:
        manifestObj["issues"].append({"reason": f"Extraction failed: {type(exc).__name__}: {exc}"})
        manifestObj["status"] = "error"
        _write_json(output / "sources_manifest.json", manifestObj)
        utils._emitEvent(onEventCallbackFunction, "error", reason=f"{type(exc).__name__}: {exc}")
        raise

    manifestObj["files"].sort(key=lambda item: item["relative_path"].casefold())
    counts = dict(sorted(Counter(item["file_type"] for item in manifestObj["files"]).items()))
    manifestObj["summary"] = {"files": len(manifestObj["files"]), "by_type": counts, "issues": len(manifestObj["issues"])}
    manifestObj["status"] = "partial" if manifestObj["issues"] else "ok"
    _write_json(output / "sources_manifest.json", manifestObj)
    utils._emitEvent(onEventCallbackFunction, "completed", status=manifestObj["status"], **manifestObj["summary"])
    return manifestObj


def main() -> int:
    """Run the source collector from the command line."""
    parser = argparse.ArgumentParser(description="Collect SQL source artifacts and catalogs ")
    parser.add_argument("--config", required=True, type=Path, help="JSON configuration file")
    parser.add_argument("--destination", required=True, type=Path, help="New or empty output directory")
    arguments = parser.parse_args()
    try:
        config = json.loads(arguments.config.read_text(encoding="utf-8-sig"))
        if config.get("mode") == "mock":
            desc = config.get("description")
            source = Path(config.get("source_directory", ""))
            if not source.is_absolute():
                config["source_directory"] = str((arguments.config.parent / source).resolve())

        result = collect_sources(config, arguments.destination, onEventCallbackFunction=utils._print_event)

        # Output file definition

        print("\nManifest:", Path(result["destination"]) / "sources_manifest.json")
        # TODO: Verificar si esto tiene validez cuando este el agente terminado 


        return 0 if result["status"] == "ok" else 1
    
    except (ValueError, TypeError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
