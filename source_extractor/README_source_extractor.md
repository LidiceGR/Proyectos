# Capstone CMU — Source Collector (v0.2)

`extractor.py` is a **deterministic input acquisition tool**. 
It collects source evidence before AST parsing, inventory construction, RAG indexing, or agent-driven investigation. 


## 1. Purpose and execution modes

| Capability | `mock` | `odbc` |
| --- | --- | --- |
| Discover schema/table declarations | Inspect every `.sql` file in a directory tree | Query SQL Server table/column metadata |
| Discover stored procedures | Recognize `CREATE` / `ALTER` declarations | Read available procedure definitions |
| Discover DML triggers | Recognize `CREATE` / `ALTER` declarations | Read available DML trigger definitions |
| Retrieve catalog descriptions | Validate and copy `.catalog.json` files | Query **explicitly allowlisted** catalog tables |
| Export artifacts | Copy files without modifying originals | Export SQL definitions and JSON metadata |
| Report progress | Optional event callback | Optional event callback |
| Return file inventory | `output_srcs_manifest_[System_name].json` | `output_srcs_manifest_[System_name].json` |

The mock's recognition of declarations is heuristic; an AST parser must validate the actual SQL syntax. Catalog values provide additional evidence but do not constitute verified business rules by themselves.

## 2. Run the mock example on Windows

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python source_extractor\extractor.py --config config\mock_req_dummy_sistema1_sources.json --destination workspace\run_001
```

The destination must be **new or empty**. To repeat the example, use `workspace\run_002`.

Expected output structure:

```text
workspace/run_001/
├── sql_sources/
│   ├── schema/01_operations_schema.sql
│   ├── procedures/01_ApproveOperation.sql
│   ├── procedures/02_RegisterOperation.sql
│   └── triggers/01_ValidateAmount.sql
├── catalogs/
│   ├── operation_types.catalog.json
│   └── operation_statuses.catalog.json
└── sources_manifest.json
```

## 3. Catalog JSON format

`examples/operations/catalogs/operation_types.catalog.json`:

```json
{
  "type": "catalog",
  "name": "Operation types",
  "table": "dbo.OperationTypes",
  "key_column": "OperationType",
  "description_column": "Description",
  "values": [
    {"code": 1, "description": "External payment"},
    {"code": 2, "description": "Internal adjustment"},
    {"code": 3, "description": "Account-to-account transfer"}
  ]
}
```

Catalog files are picked up when located inside a `catalogs/` directory or named `*.catalog.json`. The validator checks required fields, rows, and duplicate codes. Invalid catalogs are reported without stopping valid files. In a later RAG scenario, the catalog value `OperationType = 2` supplies the business meaning **Internal adjustment**; the original stored procedure does not supply that description by itself.

## 4. ODBC configuration (later phase)

`config/odbc.example.json`:

```json
{
  "mode": "odbc",
  "odbc": {
    "dsn": "LEGACY_READ_ONLY",
    "database": "DemoOperations",
    "schemas": ["dbo"],
    "connection_timeout_seconds": 15,
    "catalogs": [
      {
        "schema": "dbo",
        "table": "OperationTypes",
        "key_column": "OperationType",
        "description_column": "Description",
        "max_rows": 500
      }
    ]
  }
}
```

The actual example configuration includes both operation-type and operation-status catalogs. The ODBC backend requires `pyodbc`, a configured **SQL Server ODBC DSN**, suitable certificates, and a database account with **read-only permissions**. This implementation has been tested with a simulated driver, **not** against an actual SQL Server instance.

- It reads table/column metadata using `sys.tables` and `sys.columns`, and stored procedure/trigger definitions using `sys.sql_modules`.
- ODBC schema metadata is exported to `schema/tables.json`; this is **not** equivalent to a full `CREATE TABLE` script and does not include all keys, constraints, or indexes.
- Catalog values are exported only for tables explicitly listed under `odbc.catalogs` and within configured schemas. No automatic catalog guessing or broad table-data export occurs.
- It does not execute extracted SQL statements. `ApplicationIntent=ReadOnly` does not replace database-side read-only permissions.
- Integrated authentication is used by default. Optional `username_env` and `password_env` entries reference environment variables rather than storing secrets in the configuration file.
- Encrypted or inaccessible SQL module definitions are reported as issues.

## 7. Manifest contract

The tool returns a JSON-compatible Python dictionary and writes `sources_manifest.json`. Each entry identifies the actual artifact to send to AST, RAG, or inventory processing later:

```json
{
  "mode": "mock",
  "status": "ok",
  "files": [
    {
      "file_type": "procedure",
      "objects": [{"type": "procedure", "name": "dbo.ApproveOperation", "line": 2}],
      "format": "sql",
      "relative_path": "sql_sources/procedures/01_ApproveOperation.sql",
      "absolute_path": "<machine-specific path>",
      "source": "procedures/01_ApproveOperation.sql",
      "size_bytes": 310,
      "sha256": "<computed SHA-256 digest>",
      "suggested_processors": ["ast", "rag"]
    }
  ],
  "issues": [],
  "summary": {"files": 6, "by_type": {"catalog": 2, "procedure": 2, "schema": 1, "trigger": 1}, "issues": 0}
}
```

This is an **illustrative fragment**, not the literal manifest. Actual file sizes, SHA-256 hashes, paths, and timestamps are generated at runtime. SQL artifacts are tagged for `ast` and `rag`; catalogs are tagged for `rag`; ODBC schema metadata is tagged for `inventory` and `rag`. These tags describe planned consumers; no AST or RAG processing runs inside this tool.

## 8. Python API and future FastMCP boundary

```python
from source_collector import collect_sources

config = {"mode": "mock", "source_directory": "examples/operations"}

def report_event(event: dict) -> None:
    print(event)

manifest = collect_sources(config, "workspace/run_001", on_event=report_event)
print(manifest["files"])
```

Function signature:

```python
def collect_sources(config: Mapping[str, Any], destination: str | Path,
                    on_event: EventCallback | None = None) -> dict[str, Any]: ...
```

The callback emits event dictionaries, for example `{"event": "file_collected", "source": "...", "file_type": "procedure"}`. A future FastMCP wrapper can forward those events to client logging, while returning the manifest as structured tool output.

**Security note:** a later MCP wrapper should resolve a previously approved configuration ID and restrict destination paths to an approved workspace. Do not allow an LLM to supply unrestricted filesystem paths, raw connection strings, or credentials.

## 9. Automated tests

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m unittest discover -s tests -v
```

Tests cover classification, comment/string masking, line numbers, mixed scripts, mock copying, catalog validation, destination safety, event callbacks, and simulated ODBC extraction. They do not require model inference or network access.

## 10. Limitations and downstream integration

- The mock uses declaration heuristics, not a SQL AST. Dynamic SQL and unusual T-SQL syntax may require additional handling.
- The ODBC implementation currently collects **table metadata**, stored procedures, and table-level DML triggers; other SQL Server objects and DDL triggers are not yet covered.
- The collector does not identify real catalog tables automatically; ODBC catalogs require an explicit allowlist.
- Missing objects are not evidence that a business rule does not exist; collection only reflects accessible source material.
- **Interface change from v0.1:** this English-language version uses `collect_sources`, `mode`, `source_directory`, `files`, `file_type`, `sources_manifest.json`, and other English field names. Existing Spanish-language AST/inventory scripts must be adapted at their boundaries or provided with a conversion adapter before integration. No integration is claimed in this release.

## 11. Technical references

- SQL Server catalog views: https://learn.microsoft.com/en-us/sql/relational-databases/system-catalog-views/object-catalog-views-transact-sql
- SQL module definitions: https://learn.microsoft.com/en-us/sql/relational-databases/system-catalog-views/sys-sql-modules-transact-sql
