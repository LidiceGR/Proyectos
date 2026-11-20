# Research Agent v0.1 — Single Structural Task Analysis

## Purpose

This component reads **one pending task** produced by `task_initializer`, constructs a prompt from a separate English text template, and asks a local Qwen model to interpret it. It stores the original model response and validates the proposed JSON decision.

This is **not yet a ReAct loop**. It proposes a next action but **does not call tools, retrieve catalog values, perform RAG, update task states, or approve business rules**. Treat all generated rules as unverified hypotheses.

## Integration with the existing project

Merge these new paths into the root of your `Proyectos` repository:

```text
Proyectos/
├── llm/
│   ├── __init__.py
│   └── local_qwen.py
├── research_agent/
│   ├── __init__.py
│   ├── __main__.py
│   ├── agent.py
│   └── validator.py
├── prompts/
│   └── analyze_sql_fragment.txt
└── tests/
    └── test_research_agent.py
```

Existing components are **not replaced**. In particular, do not replace `llm_test/primer_llm_cargamodelo.py`; that file remains a historical experiment. The new `llm/local_qwen.py` is the English-language adapter for project modules.

The entry point uses the existing `lib.artifact_io.write_json` utility. Run commands from the repository root.

## Expected task input

`workspace/run_001/research/research_tasks.json`, with:

- `tasks`: list of task objects.
- `task_id`, `task_type=analyze_sql_fragment`, `status=pending`.
- `target`: object name and object type.
- `fragment`: SQL, operation, conditions, effects.
- `object_context`, `related_objects`, `evidence`.

No additional catalog context is supplied in this first version. Even if `metadata/catalogs.json` exists, the model **must not claim it read that file**.

## First test: preview only (no model, no changes)

From PowerShell at `D:\Proyectos\CMUAgent\proyectos`:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m research_agent --tasks workspace\run_001\research\research_tasks.json --task-id TASK-227F0C005747 --preview
```

The command prints the target object and the complete prompt prepared for Qwen. It does not load the model or write any output.

## Second test: use the local model

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m research_agent --tasks workspace\run_001\research\research_tasks.json --task-id TASK-227F0C005747
```

The model uses the local Hugging Face cache, with the same `Qwen/Qwen3-0.6B` weights previously downloaded on Windows. It uses CPU unless PyTorch detects a CUDA-capable GPU. The Windows adapter uses `truststore` for certificate validation.

Outputs are saved under:

`workspace/run_001/research/agent_runs/TASK-227F0C005747_<UTC timestamp>.json`

Each output includes task ID, evidence from the original task, prompt, raw model text, parsed decision, validation errors, duration, and model/device metadata. Individual runs never overwrite `research_tasks.json`.

## Response contract

The model is asked to return:

```json
{
  "classification": "candidate_business_rule",
  "candidate_rule": "A tentative interpretation of the SQL operation",
  "conditions": ["An observed SQL condition"],
  "effect": "An observed SQL effect",
  "missing_information": [],
  "requested_action": null
}
```

The possible classifications are `candidate_business_rule`, `technical_logic`, and `ambiguous`.

When additional information is required, `requested_action` may propose `lookup_catalog_value`, `inspect_related_object`, or `inspect_source_file`, each with defined arguments. This version **does not execute** those requests.

A `valid_json` status verifies only the structure of the generated JSON and the proposed action name. It **does not verify** the truth of the rule, completeness of SQL dependencies, or accuracy of the suggested action.

## Automated tests (no model required)

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m pytest -q tests\test_research_agent.py
```

## Planned follow-up

1. Evaluate the output from the approval `UPDATE` task.
2. Evaluate the registration `INSERT` task with missing catalog context.
3. Add deterministic lookup tools and safe agent action routing.
4. Introduce ReAct iteration and a separate evidence-verification stage.

> Security: Only submit fictitious or authorized SQL to local and external systems. Never allow model text to execute SQL or select an unrestricted ODBC connection.
