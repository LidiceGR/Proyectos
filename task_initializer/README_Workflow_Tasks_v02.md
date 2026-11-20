# Capstone — Logical Workflow Research Tasks v0.2

## Purpose

The v0.1 initializer selected individual UPDATE, RETURN, THROW and conditional SELECT operations as research tasks. In `run_007`, this generated five structurally valid tasks for two procedures, but individual tasks did not include the combined execution behavior needed to investigate potential cross-statement business rules.

Version 0.2 creates **source-backed logical workflow tasks**, keeping all relevant operations in order and the actual SQL source slice. The task initializer remains entirely deterministic: it does **not** infer, classify or approve business rules.

The supplied design-justification Word document discusses the five `run_007` task results, original SQL source and detailed observations.

## Package contents

Only new or changed project files are included. Extract into the **root of your current Proyectos repository**, preserving unrelated files and your existing pipeline configuration.

```text
task_initializer/
  selector.py                  UPDATED
  initializer.py               UPDATED
research_agent/
  agent.py                     UPDATED
  batch.py                     UPDATED
prompts/
  analyze_sql_workflow.txt     NEW
config/
  capstone_pipeline.batch.example.system2_v02.json  NEW
tests_v02/
  test_workflow_v02.py         NEW
sample_results_v02/
  run_001/research_tasks.json  NEW (illustrative execution output)
  run_007/research_tasks.json  NEW (illustrative execution output)
README_Workflow_Tasks_v02.md
```

No source extractor, AST parser, inventory builder, main coordinator, LLM model interface, RAG stub or MCP code is replaced. The existing `prompts/analyze_sql_fragment.txt` (prompt v3) remains unchanged for old fragment tasks.

## New workflow contract

Each task has `task_type: "analyze_sql_workflow"`, `fragment.operation: "WORKFLOW"`, a `scope`, the complete `fragment.sql` source slice, and an ordered `fragment.steps` list. Each step retains its SQL operation kind, source line, conditions, effects, source-backed SQL and AST node type where available.

```json
{
  "task_id": "TASK-...",
  "task_type": "analyze_sql_workflow",
  "status": "pending",
  "target": {"object_type": "procedure", "object_name": "..."},
  "scope": {
    "type": "transaction",
    "name": "...",
    "start_line": 13,
    "end_line": 46,
    "segmentation_note": null
  },
  "fragment": {
    "operation": "WORKFLOW",
    "sql": "...actual source SQL...",
    "steps": ["...ordered statement evidence objects..."],
    "operation_count": 11
  },
  "object_context": {"parameters": []},
  "related_objects": [],
  "evidence": {
    "source_file": "sql_sources/...sql",
    "ast_file": "ast_files/sql_sources/...sql.ast.json",
    "start_line": 11,
    "end_line": 50,
    "ast_node_type": "logical_workflow"
  },
  "parent_task_id": null
}
```

`scope.start_line` and `scope.end_line` describe an identified transaction when available; `evidence.start_line` / `end_line` describe the broader source slice sent to the model, including declarations and final return instructions. A workflow step is not a separate research task.

## How grouping works in v0.2

1. If a procedure has **one matched BEGIN TRAN / COMMIT pair**, create one transaction-scope task. Keep its surrounding declarations and return path in the same source-backed context. Conditional ROLLBACK statements do not close the transaction scope.
2. If a procedure has **no matched explicit transaction**, create one procedure-flow task, including initialization, guards, data changes and outputs.
3. Multiple clearly disjoint transaction pairs may produce separate units. Ambiguous/nested/unmatched transaction boundaries or cross-transaction data changes conservatively fall back to one complete procedure-flow task with a `segmentation_note`.
4. For triggers, preserve the IF/THROW behavior and trigger metadata in one workflow task.
5. All included operations retain their independent line-level evidence. Research task identifiers are stable for the same source identity and scope but **differ from v0.1** identifiers.

This is a conservative first implementation, **not a complete control-flow or data-flow analysis for arbitrary T-SQL**. Very large procedures may require later AST-guided decomposition at safe block boundaries. A larger evidence package can increase token usage and CPU inference time.

## Verified results using uploaded project artifacts

| Run | Source objects | v0.1 task count | v0.2 task count | v0.2 scope types |
|---|---:|---:|---:|---|
| `run_007` (dummy_system2) | 2 procedures | 5 | 2 | 1 transaction, 1 procedure flow |
| `run_001` (dummy_system1) | 2 procedures + 1 trigger | 3 | 3 | 3 procedure flows |

In `run_007`, the equipment procedure task contains **11 ordered operations**, including two updates, two error checks, conditional rollback/return paths, COMMIT and the success RETURN. The authorization task contains **5 inventoried operations**, with the actual source slice showing initialization, IF EXISTS, conditional update of `@admin`, final SELECT and RETURN.

The `tests_v02` suite has **9 passing tests** using actual parsed metadata and fake LLM outputs. No live model was run for this v0.2 package in the development environment.

## Research-agent compatibility

- `research_agent.agent.load_task` and `batch._validate_selected_task` accept both `analyze_sql_fragment` (v0.1) and `analyze_sql_workflow` (v0.2).
- A workflow task automatically uses `prompts/analyze_sql_workflow.txt`; an old fragment task still uses `prompts/analyze_sql_fragment.txt`.
- The result JSON structure remains unchanged: `classification`, `candidate_rule`, `conditions`, `effect`, `missing_information`, `requested_action`.
- Batch mode still loads the local Qwen model only once, processes pending tasks sequentially and does **not** execute requested tools or mark tasks completed.
- No RAG, ReAct, evidence verifier or MCP transport is implemented by this change.

## First Windows trial

Review the new example configuration, which uses `run_008` and `research.mode: "batch"`:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m capstone_app --config config\capstone_pipeline.batch.example.system2_v02.json --dry-run
```

Then remove `--dry-run` to perform a fresh pipeline execution. Use another unused `run_id` if `run_008` already exists. **Do not overwrite `run_007`**; it is the v0.1 experimental baseline.

To preview the first workflow prompt after preparation without loading Qwen:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m research_agent --tasks workspace\run_008\research\research_tasks.json --preview
```

The report `research_tasks.json` will show two workflow tasks. The live Qwen batch may still produce incomplete or incorrect interpretations. Comparing v0.1 and v0.2 model results is a **subsequent empirical test**, not an established outcome.
