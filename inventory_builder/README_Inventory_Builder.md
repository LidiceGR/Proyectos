# Inventory Builder — CMU Capstone (v0.1)

## Purpose

`inventory_builder` converts **existing SQLFluff AST JSON artifacts** and source-collector catalogs into structured, traceable metadata. This is a deterministic preparation step: it does **not** invoke an LLM, execute SQL, query SQL Server, or confirm business rules.

It is designed to fit the current project, without replacing or modifying `source_extractor` or `sql_ast_parsers`.

## Placement in the project

Copy the following **new** files into the repository root:

```text
Proyectos/
├── lib/
│   ├── artifact_io.py             # Existing file; keep unchanged
│   └── manifest_paths.py         # NEW: shared path-validation helper
├── inventory_builder/
│   ├── __init__.py
│   ├── __main__.py
│   └── builder.py
├── tests/
│   └── test_inventory_builder.py
├── workspace/
│   └── run_001/
│       ├── sources_manifest.json
│       ├── sql_sources/
│       ├── catalogs/
│       ├── ast_files/
│       │   └── ast_manifest.json
│       └── metadata/              # Created by this module
└── README_Inventory_Builder.md
```

The optional `sample_results/` folder in this package contains outputs generated from the uploaded `workspace/run_001` fixture for inspection; it does not need to be copied into the project.

## Input contract

The module requires both existing manifests:

- **Source manifest:** `workspace/run_001/sources_manifest.json`, with `files[]`, `relative_path`, `file_type`, and (when present) `sha256`.
- **AST manifest:** `workspace/run_001/ast_files/ast_manifest.json`, with `files[]`, `source_file`, and `ast_file`.

All file references are resolved relative to the corresponding manifest directory. The builder checks that paths remain inside their designated run directory. If a source checksum is present, the current SQL or catalog is verified against it before analysis.

The builder reads the AST JSON created by SQLFluff 3.3.1 but does not require SQLFluff to be installed in order to process previously generated AST files. It uses the project's existing `lib.artifact_io.write_json` function.

## Output contract

The requested `--destination` folder receives four JSON documents:

| Artifact | Contents |
| --- | --- |
| `inventory.json` | Tables, columns, explicit foreign keys, procedures, parameters, triggers, operations, conditions, effects, and source locations. |
| `relationships.json` | Source-backed relationships: `FOREIGN_KEY`, `TRIGGERS_ON`, `READS`, `UPDATES`, `INSERTS_INTO`, `DELETES_FROM`, `MERGES_INTO`, and `CALLS`. |
| `catalogs.json` | Catalog names, tables, key/description columns, complete catalog values, and original source filenames. |
| `extraction_issues.json` | Files skipped, invalid JSON/checksums, partial ASTs, and unsupported or unresolved references. |

Each extracted operation retains its original SQL filename and line. For example, the `dbo.ApproveOperation` UPDATE contains its `WHERE` condition and `Status = 'APPROVED'` assignment. The `dbo.RegisterOperation` INSERT retains the governing `IF @OperationType = 2 AND @Amount > 0` and the inserted column-value expressions. The validation trigger retains the `IF EXISTS (...)` condition and the `THROW` statement.

Catalog values remain **source data**, separate from the AST-based inventory. The builder does not silently interpret `@OperationType = 2` as *Internal adjustment*: connecting parameter values to an identified catalog and forming a rule is part of subsequent investigation and verification.

## Run in Windows PowerShell

From the repository root (`D:\Proyectos\CMUAgent\proyectos`):

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m inventory_builder --ast-manifest workspace\run_001\ast_files\ast_manifest.json --source-manifest workspace\run_001\sources_manifest.json --destination workspace\run_001\metadata
```

Expected summary for the uploaded example dataset (`run_001`):

```text
[inventory_completed] status=ok tables=3 procedures=2 triggers=1 catalogs=2 relationships=6 issues=0
Metadata directory: ...\workspace\run_001\metadata
```

After this command succeeds, inspect `workspace/run_001/metadata/inventory.json` and `catalogs.json`.

To analyze `run_002`, use the same command with all three `run_001` occurrences changed to `run_002`.

## Run automated tests

Requires `pytest` (included in the SQLFluff installation from this project):

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m pytest -q tests\test_inventory_builder.py
```

Integration tests use the two `workspace/run_001` and `workspace/run_002` datasets. If those directories are not present, the tests that depend on them are skipped.

## Python API and future MCP adapter

```python
from inventory_builder import build_inventory

manifest = build_inventory(
    ast_manifest_path="workspace/run_001/ast_files/ast_manifest.json",
    source_manifest_path="workspace/run_001/sources_manifest.json",
    destination="workspace/run_001/metadata",
    on_event=lambda event: print(event),
)
```

`on_event` is a structured callback suitable for relaying progress through a future FastMCP adapter. The builder itself has no MCP dependency.

## Known limitations

- Objects currently recognized: SQLFluff `create_table_statement`, `create_procedure_statement`, and `create_trigger`. Other DDL forms (including some `ALTER` variants), views, and complex procedural constructs require separate support.
- A `status: ok` AST indicates syntactic parsing, **not** complete semantic understanding. Conditional expressions are preserved as source expressions; they are not yet normalized into formal predicate trees.
- Multiple DML patterns (dynamic SQL, `MERGE`, complex `INSERT ... SELECT`, nested updates, and aliases) are not fully resolved. When a DML target cannot be identified it is recorded in `extraction_issues.json`.
- Trigger pseudo-tables such as `inserted` are preserved as textual table references. They are not treated as persistent schema tables.
- Catalog mappings are not inferred from similarly named SQL parameters or columns. Catalog rows must originate from a catalog JSON listed in the source manifest.
- There is no task generation yet. The next planned component, `task_initializer`, will read these artifacts and generate **candidate research tasks**, not confirmed business rules.
- A changed source file causes a checksum mismatch and that artifact is skipped until `sql_ast_parsers` regenerates the AST.

## Next step

After verifying the actual metadata output, implement `task_initializer` using stable references to conditions and effects in `inventory.json`, table relationships in `relationships.json`, and codes in `catalogs.json`. Then use the local Qwen model to investigate those tasks with traceable evidence.
