# Capstone Batch Research v0.1

## Purpose

Add a deterministic, sequential `batch` research mode to the existing Capstone main coordinator **without introducing a ReAct loop**. The pipeline still collects SQL sources, parses ASTs, builds metadata, and initializes structural tasks before launching research. The batch runner selects every task marked `pending` and runs **one LLM analysis per task**.

This package is a **patch** for the previously delivered `capstone_app` and `research_agent` components, not a replacement project. All existing SQL extractors, parsers, inventory code, task initializers, and the current prompt are left unchanged.

## Files to add or update

```text
capstone_app/
    config.py                  UPDATED
    coordinator.py             UPDATED
    __main__.py                UPDATED
research_agent/
    __main__.py                UPDATED
    batch.py                   NEW
config/
    capstone_pipeline.batch.example.json  NEW
tests/
    test_capstone_batch.py      NEW
    test_research_batch.py      NEW
README_Batch_Research.md       NEW
```

**Important:** This patch does not include `prompts/analyze_sql_fragment.txt`. Preserve your current prompt v3, which will be used by `research_agent.agent.render_prompt` unchanged. Keep the existing `capstone_app/__init__.py`, `research_agent/__init__.py`, `research_agent/agent.py`, `research_agent/validator.py`, `llm/local_qwen.py`, and `lib/artifact_io.py`.

## Configuration

```json
{
  "run_id": "run_005",
  "workspace": "workspace",
  "source_config": "config/mock_req_dummy_system1.json",
  "rag": {"initialize": false},
  "research": {
    "mode": "batch",
    "model_id": "Qwen/Qwen3-0.6B",
    "task_id": null,
    "max_tasks": 0
  }
}
```

`research.mode` supports `none`, `preview`, `single`, and `batch`.

- `max_tasks = 0`: analyze all pending tasks.
- `max_tasks = 2`: analyze only the first two pending tasks, preserving their order in `research_tasks.json`.
- `task_id`: must be `null` in batch mode; targeted runs remain available using `single`.
- `rag.initialize`: remains `false`. RAG is NOT YET IMPLEMENTED.

The example source configuration path reflects the source file shown in the user's verified `run_004` execution log. Change it if your local configuration filename differs. The example uses `run_005`: choose a new unused `run_id` if that directory already exists.

## Step 1: Preview the new coordinator plan

From the root of your existing `Proyectos` repository:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m capstone_app --config config\capstone_pipeline.batch.example.json --dry-run
```

This command does not create workspace artifacts or load Qwen.

## Step 2: Run preparation and batch research

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m capstone_app --config config\capstone_pipeline.batch.example.json
```

The coordinator runs the same four preparation stages and then invokes the research agent **once** in batch mode. It passes all selected tasks to the same Python process, which instantiates **one `LocalQwen` object**. Model weights are loaded lazily on the first inference, not once per task.

## Output artifacts

```text
workspace/run_005/
    sources_manifest.json
    ast_files/ast_manifest.json
    metadata/inventory.json
    metadata/relationships.json
    metadata/catalogs.json
    metadata/extraction_issues.json
    research/research_tasks.json
    research/agent_runs/
        TASK-<id>_<timestamp>.json    (one per analyzed task)
        batch_summary.json            (batch overview)
    pipeline_status.json
```

The summary contains:

- The number of pending, selected, processed, and not-selected tasks.
- Counts for `valid_json`, `invalid_response`, and `execution_error`.
- A list of task IDs, outcome statuses, saved result paths, and validation errors.
- `status: completed` if all selected tasks are processed with valid JSON, or `completed_with_errors` if any attempt failed.

**`valid_json` is only a format check.** It does not mean that a candidate business rule is accurate, complete, or approved.

Each individual result retains the original model output, validated decision (when available), prompt, source evidence, elapsed time, and execution device. The batch runner **does not update task statuses** in `research_tasks.json`, execute proposed tool actions, or approve findings. Every task remains `pending` in the source JSON until a future workflow manages its lifecycle explicitly.

## Failure behavior

- A task-level invalid response or inference exception is recorded as that task's result; later tasks are still attempted.
- `batch_summary.json` is checkpointed after every attempt, preserving progress if a later task fails.
- If one or more tasks are invalid or error, the batch command returns exit code `1` *after* processing the selected tasks. The coordinator records `research_batch` as a failed stage while retaining the generated artifacts.
- Existing `batch_summary.json` files are never overwritten by the batch runner. Use a fresh output directory for a new batch attempt. The main coordinator always requires a new run ID.
- Invalid task documents (such as duplicate IDs) are rejected before analysis, rather than silently skipping tasks.
- A model-loading failure may produce errors for multiple tasks; the batch does not claim successful research when the model is unavailable.

## Run batch research directly on existing tasks

The following command **skips all preparation** and evaluates the pending tasks in the given file:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m research_agent --tasks workspace\run_004\research\research_tasks.json --batch --max-tasks 0
```

Choose a fresh `--output-directory` if you already used the default `agent_runs` directory for a previous batch. This direct command does not affect `pipeline_status.json` for the source run.

## Tests

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m pytest tests\test_capstone_batch.py tests\test_research_batch.py -q
```

This patch was tested in an integrated copy of the previously delivered `capstone_app` and `research_agent`, including all their original tests: **33 passed**. A separate smoke test used the three real structural tasks from the supplied `run_001` sample with a fake LLM; it produced three individual results and a batch summary without changing the original task file. These tests **do not** prove the quality of real Qwen inference, which must be evaluated in the user's `cmu-llm` environment.

## Future extensions

The initial batch forms a baseline for later comparison with iterative investigation. Pending components include evidence sufficiency checks, actual `lookup_catalog_value` / source inspection tools, optional MCP adapters, RAG indexing and retrieval, research task lifecycle management, and the full ReAct cycle.
