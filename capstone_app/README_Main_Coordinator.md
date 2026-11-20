# Capstone Main Coordinator v0.1

## Purpose

This is a **programmatic coordinator**, not an LLM agent. It runs the existing deterministic source preparation pipeline using their public CLI interfaces, without changing the established modules. The research agent starts only when explicitly requested.

## New files

```text
capstone_app/
    __init__.py
    __main__.py
    config.py
    coordinator.py
rag_tools/
    __init__.py
    initializer.py
    retriever.py
config/
    capstone_pipeline.example.json
tests/
    test_capstone_app.py
README_Main_Coordinator.md
```

Copy these files into the **root of the existing `Proyectos` repository**. Do not replace `source_extractor`, `sql_ast_parsers`, `inventory_builder`, `task_initializer`, or `research_agent`.

## Workflow

1. `source_extractor/extractor.py --config ... --destination ...`
2. `python -m sql_ast_parsers --manifest ... --destination ...`
3. `python -m inventory_builder --ast-manifest ... --source-manifest ... --destination ...`
4. `python -m task_initializer --inventory ... --relationships ... --ast-directory ... --destination ...`
5. Optional RAG initialization: **NOT YET IMPLEMENTED**.
6. Optional research-agent invocation: `none`, `preview`, or `single` (only one task; not a ReAct loop).

All phases run through the **same Python interpreter** that launched `capstone_app`. The coordinator uses subprocess execution because the current `source_extractor/extractor.py` imports its sibling `utils` as an unqualified module. This avoids modifying the user's working code. Each phase has an existing, independently testable CLI.

## First test — no workspace modifications

From the project root, in the existing Conda environment:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m capstone_app --config config\capstone_pipeline.example.json --dry-run
```

The dry run prints commands and expected outputs without creating `run_003`.

## Run deterministic preparation only

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m capstone_app --config config\capstone_pipeline.example.json
```

Default configuration: `run_003`, `rag.initialize=false`, `research.mode=none`. This generates:

```text
workspace/run_003/
    sources_manifest.json
    sql_sources/
    catalogs/
    ast_files/ast_manifest.json
    metadata/inventory.json
    metadata/relationships.json
    metadata/catalogs.json
    metadata/extraction_issues.json
    research/research_tasks.json
    pipeline_status.json
```

Only paths supported by the source configuration will be populated; the number of files depends on the selected system. Every stage is checked for successful process exit and expected files. Failures stop the pipeline, and `pipeline_status.json` records the failed phase when a run has started.

## Start research only when ready

Change `research.mode` from `none` to `preview` (only prompt preview) or `single` (one Qwen run) before starting a **new** run. Alternatively use `--research-mode preview` or `--research-mode single` to override the JSON mode. An optional `research.task_id` selects a specific task; when null, the existing agent selects its first pending task. The current research agent saves a separate analysis artifact and does not update task statuses or iterate through ReAct actions.

## RAG extension point

Set `rag.initialize=true` in the configuration, or pass `--initialize-rag`. The placeholder `rag_tools.initializer.initialize_rag_index` explicitly raises `NotImplementedError` **before extraction starts** while the capability is unavailable, so the application cannot silently claim RAG is available. When implemented, the initialization stage belongs **after** source preparation and **before** research. The `rag_tools.retriever.retrieve_evidence` placeholder reserves the future research tool. Keep `false` until indexing and retrieval are developed.

## Safety and repeatability

- Each run requires a **new run ID**. Existing `workspace/run_###` directories are never overwritten.
- `--dry-run` previews the exact stages without writing files.
- Source and RAG configurations are kept separate from model instructions.
- The pipeline does not infer business rules and does not confirm any model-produced finding.
- `pipeline_status.json` records phase outcomes, not LLM reasoning.
- Source manifests with issues and incomplete AST manifests stop the default pipeline. No partial results are silently accepted.

## Tests

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m pytest tests\test_capstone_app.py -q
```

## MCP boundary (future work)

Not all deterministic pipeline stages need an MCP interface. The main coordinator may use standard Python subprocess calls. Useful agent-facing MCP tools later include `lookup_catalog_value`, `inspect_related_object`, and `inspect_source_file`; they should wrap independently testable Python functions. If exposing preparation through MCP is later valuable, wrapper tools should delegate to existing code rather than duplicate it. The MCP SDK evolves, so choose a specific version when implementing the adapter; **no MCP server is included here**.

## Known limitations

- Full source extraction requires the same config and dependencies that currently work in the user's repository. ODBC mode still depends on the existing extractor implementation.
- The example configuration points to the previously used `config/mock_req_dummy_sistema1_sources.json` file. Adjust it if your current configuration has another name.
- RAG, automatic task state updates, an iterative ReAct cycle, and independent evidence verification are not implemented in v0.1.
- Phase order is currently fixed and intentionally simple; controlled resume and partial AST support may be added later.
