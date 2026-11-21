# Capstone Research Agent v0.4 — Bounded ReAct with Source-Backed Tools

## Purpose

This update evolves the existing v0.3 single-pass Research Agent into an optional, bounded ReAct research mode. It does **not** modify the v0.2 SQL segmentation algorithm, initial research tasks, source extraction, AST parsing or the existing v0.3 `batch`, `single` and `preview` modes.

The coordinator still prepares sources, AST, inventory and initial tasks deterministically. When `research.mode = "react"`, the Research Agent performs iterative analysis with read-only tool actions. Each action produces a recorded observation that is included in the next model prompt.

All source content and tool observations remain evidence, not executable instructions. Every rule remains **tentative**.

## Install

Extract `Capstone_Research_Agent_v04_ReAct_Run011_English.zip` **into the root of the existing project**, preserving directories and agreeing to overwrite the modified files. In particular, `capstone_app` and `research_agent` should remain sibling packages, with `prompts/` and `config/` beside them. This is a **patch**, not a complete standalone project.

New files:

- `research_agent/tools.py`: read-only catalog lookup and graph-based object inspection.
- `research_agent/derived_tasks.py`: source-grounded child task creation, deduplication and links to initial tasks.
- `research_agent/react.py`: ReAct controller, action limits, trace, derived queue and summary.
- `prompts/react_sql_research.txt`: instruction template for each ReAct reasoning step.
- `config/capstone_pipeline.react.system1.run_011.json`: example `run_011` configuration.
- `tests/test_react_agent_v04.py`: tests based on the existing system1 metadata and mocked model replies.
- `Capstone_Workspace_Explorer_v05_ReAct.html`: optional offline viewer with ReAct trace display.

Modified files:

- `capstone_app/config.py` and `capstone_app/coordinator.py`: register the new `react` research mode and limits.
- `research_agent/__main__.py`: add the `--react` command-line entry point.
- `research_agent/validator.py`: add the validated `enqueue_research_task` controller action.

**Legacy:** v0.3 `batch`, `single` and `preview` remain supported. The v0.3 evidence checker remains unchanged and is not a semantic verification agent.

## Run 011 (system1)

From the project root in Windows PowerShell:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m capstone_app --config config\capstone_pipeline.react.system1.run_011.json
```

`run_011` must not exist beforehand. Existing run directories are intentionally never overwritten.

Configuration:

```json
{
  "run_id": "run_011",
  "workspace": "workspace",
  "source_config": "config/mock_req_dummy_system1.json",
  "rag": {"initialize": false},
  "research": {
    "mode": "react",
    "model_id": "Qwen/Qwen3-0.6B",
    "task_id": null,
    "max_tasks": 0,
    "max_actions": 3,
    "max_derived_tasks": 4,
    "max_depth": 1
  }
}
```

### How the loop works

1. Load one existing `analyze_sql_workflow` research task; send the workflow SQL and compact ordered steps to Qwen.
2. Qwen returns a seven-field provisional decision. It may request `lookup_catalog_value` or `inspect_related_object`.
3. Validate requested action and arguments, then run the corresponding **read-only** tool using artifacts from the current workspace run. Add the observation to the trace.
4. Resend the original task **plus prior observations** to Qwen. The model may request another action or finalize.
5. When Qwen prematurely finalizes while the controller sees an **exact catalog-key match or a DML-trigger dependency**, the controller may execute a deterministic `evidence_gap_policy` action and ask Qwen again. Such actions are explicitly labeled as **policy-driven**, not model-driven.
6. Once no additional action is needed or the maximum action count is reached, perform the existing v0.3 evidence-coverage check and save the final decision. If the model still requests an action after the action budget is exhausted, mark the research as incomplete rather than claiming success.

Limits apply per task. Model generation runs on the same local Qwen instance across a batch; the model is not reloaded for each task.

### Tool 1 — lookup_catalog_value

Input: `{"name":"lookup_catalog_value","arguments":{"column":"OperationType","value":2}}`.

Looks up an exact key-column/value match in `workspace/run_011/metadata/catalogs.json`. Returns `found`, `ambiguous` or `not_found`, matching codes, descriptions and catalog source filenames. For system1, metadata defines `OperationType = 2` as `Internal adjustment` in `catalogs/operation_types.catalog.json`.

The tool does not invent meanings, does not treat every numeric threshold as a catalog key and does not query a live database.

### Tool 2 — inspect_related_object

Input: `{"name":"inspect_related_object","arguments":{"object_name":"dbo.Operations"}}`.

Finds incoming and outgoing edges in `relationships.json` and object details in `inventory.json`, including provenance and trigger event information. When the parent task modifies a table and the graph shows a relevant `TRIGGERS_ON` event, the controller proposes a related trigger investigation.

### Controller action — enqueue_research_task

The model can also request:

```json
{
  "name": "enqueue_research_task",
  "arguments": {
    "object_name": "dbo.trg_Operations_ValidateAmount",
    "research_question": "Does this trigger constrain operation registration?",
    "reason": "The parent INSERT modifies the table on which this trigger is defined."
  }
}
```

This is a **controller action**, not a third external retrieval tool. It can only create a task for an inventory-backed procedure or trigger related to the parent through recorded relationships and an available local SQL file. It cannot invent an object or read arbitrary paths.

If the target already has initial or previously derived tasks, the result is `linked_existing` with the existing task IDs; **no duplicate task is created**. In normal system1, the trigger is already an initial task, so the expected result is a link. When a genuinely new source-backed related object is discovered, the controller writes a child into `derived_tasks.json`, records the `parent_task_id`, question and source, and processes it up to the configured depth.

Derived tasks are intentionally separate from the initial `research_tasks.json`. For a previously unsegmented object, the first derived version uses its full source and inventory operations rather than rerunning the v0.2 transaction segmentation. This is an explicit limitation of v0.4.

## Output files

```text
workspace/run_011/
  metadata/
    inventory.json
    relationships.json
    catalogs.json
  research/
    research_tasks.json                 # unchanged initial tasks
    derived_tasks.json                  # new source-backed tasks (may be empty)
    agent_runs/
      TASK-..._<timestamp>.json        # decision, full ReAct trace and observations
      react_summary.json                # counts and result references
```

Each result contains `trace` events:

- `kind: "reason"`: prompt, raw response and parsed decision from Qwen.
- `kind: "act_observe"`: the validated action, execution origin (`model` or `evidence_gap_policy`) and actual tool observation.
- `related_task_links`: `created` or `linked_existing` investigation references.

The summary reports valid/incomplete/invalid tasks, candidate rules, policy/model actions, tool calls and child tasks. A `valid_json` status means the existing automated checks passed; it is not business approval. If a finding is marked `evidence_incomplete` or `research_incomplete`, the pipeline may report a failed research stage while preserving the individual JSON outputs for inspection.

## Comparison with run_009

- `run_009` / Research Agent v0.3: one Qwen call per workflow, no tool execution.
- `run_011` / Research Agent v0.4: multiple model calls when needed, actual lookup/graph tool observations, trace and optional child tasks.
- Inspect whether descriptions like `Internal adjustment` occur **only after an actual catalog observation**. Also inspect whether related trigger investigations are linked rather than duplicated.
- Accuracy of business interpretations still needs independent verification; the Evidence Verifier is planned for the next stage.

## Tests and limitations

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m unittest discover -s tests -p "test_*v0*.py" -q
```

The patch was tested with 24 local automated tests (14 existing v0.3 and 10 new ReAct tests), including source-backed catalog lookups, trigger link/deduplication, generated child tasks, bounded tool actions, coordinator dry-run and legacy batch tests. The new test fixtures are bundled in `tests/fixtures/react_v04_system1/`, so testing does not require retaining old workspace runs. Integration tests use **simulated Qwen output**, not a real Qwen3-0.6B inference run. Actual run_011 model output must be evaluated after you run it.

Important limitations:

- ReAct is bounded (not an unconstrained autonomous agent); no MCP server, RAG, live SQL, SQL execution or semantic Evidence Verifier is included.
- Small language models may ignore tool instructions, produce invalid JSON or repeat an action. Deterministic gap actions increase evidence coverage but cannot guarantee accurate synthesis.
- Current follow-ups support known procedures/triggers connected by source-backed relationships. They do not create a full dependency graph of arbitrary Java code or SQL dynamic strings.
- Current verification still checks mostly structural completeness, not unsupported semantic claims. Human review is still required.
- Run v0.4 using the same Windows environment as previous runs; optional HTML viewing does not require a local web server.
