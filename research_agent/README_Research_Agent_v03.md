# Capstone Research Agent v0.3 — Observable Behavior and Candidate Rule Separation

## Objective

This incremental update addresses a regression observed in `run_008` (system1): three structurally valid JSON responses, zero proposed business rules, and two responses with no reported effect. Version 0.3 **does not change SQL source extraction, AST parsing, the inventory builder, or the task initializer v0.2**.

The agent still analyzes each pending workflow once. It does **not** implement ReAct, execute proposed actions, verify the truth of business rules, or require a human-approved rule.

## Changes

| File | Responsibility |
|---|---|
| `research_agent/context_builder.py` | Condenses an existing workflow task into ordered, source-backed steps; includes the workflow SQL only once. |
| `prompts/analyze_sql_workflow.txt` | Prompts for a complete `observed_behavior` **before** classification and a potential `candidate_rule`. Does not treat every SQL statement as technical-only logic. |
| `research_agent/agent.py` | Uses the compact workflow context and records the evidence-coverage assessment. Original fragment tasks retain their previous prompt. |
| `research_agent/validator.py` | Validates the seven-field v0.3 workflow response contract; legacy fragment responses retain the six-field contract. |
| `research_agent/evidence_checker.py` | Detects missing observable behavior, omitted conditions/effects, assignments mislabeled as conditions, and omitted final SELECT outputs. Unknown categorical codes create warnings, not invented labels or automatic tool calls. |
| `research_agent/batch.py` | Records `evidence_incomplete` separately from invalid JSON and counts successful candidate-rule proposals. Saves results per task and preserves `research_tasks.json`. |
| `research_agent/compare_runs.py` | Compares reporting metrics across two completed runs without claiming semantic accuracy. |
| `Capstone_Workspace_Explorer_v04.html` | **Optional** viewer update for the new `observed_behavior`, evidence warnings, and incomplete statuses. Does not replace v03. |

The previous workflow prompt is provided unchanged as `prompts/analyze_sql_workflow_v02.txt` for reproducibility. The active prompt remains `prompts/analyze_sql_workflow.txt`.

## Workflow output fields

```json
{
  "observed_behavior": "Source-backed description of what the entire SQL workflow does",
  "conditions": ["Actual IF/WHERE predicate"],
  "effect": "Combined observable effect",
  "classification": "candidate_business_rule",
  "candidate_rule": "A tentative, source-backed interpretation or null",
  "missing_information": [],
  "requested_action": null
}
```

`observed_behavior` describes the code, not a confirmed business requirement. `candidate_rule` is tentative and may be `null`. The model is responsible for both fields; the checker does not create business-rule text.

**Status behavior:** `valid_json` means the response follows the schema **and** passes baseline evidence-coverage checks for workflow tasks. `evidence_incomplete` means the response passed schema checks but omitted at least one required source-backed element. `invalid_response` means the model returned an invalid format or schema. `execution_error` indicates a failed call or task processing error. Evidence warnings are nonblocking. These statuses do not imply that a rule is semantically correct or verified.

`candidate_rules_proposed` counts nonempty candidate rules from responses with `valid_json` status only. This is a model-reporting metric, not business-rule identification accuracy.

## Run 009: original system1

The included configuration `config/capstone_pipeline.batch.system1.run_009.json` runs the deterministic pipeline and then sequentially analyzes **all pending** workflow tasks from the original `system1` mock sources with Qwen. RAG remains disabled.

Place this patch at the project root, preserving the existing directory structure. Use a new `run_009` directory; the main coordinator refuses to overwrite an existing run.

### Inspect the plan first

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m capstone_app --config config\capstone_pipeline.batch.system1.run_009.json --dry-run
```

### Run the complete pipeline

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m capstone_app --config config\capstone_pipeline.batch.system1.run_009.json
```

Outputs:

- `workspace/run_009/research/research_tasks.json`: unchanged v0.2 workflow task contract.
- `workspace/run_009/research/agent_runs/TASK-*.json`: model output, source prompt, proposed rule (if any), validation errors and `evidence_check`.
- `workspace/run_009/research/agent_runs/batch_summary.json`: totals, incomplete evidence counts, proposed-rule counts.
- `workspace/run_009/pipeline_status.json`: coordinator stage status.

A batch with an invalid model response or incomplete evidence will still save the individual results and the summary. The coordinator may report a failed research stage in that case; **do not delete the files**. They are useful experimental evidence.

### Compare run_008 and run_009

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m research_agent.compare_runs --baseline workspace\run_008 --current workspace\run_009
```

The comparison prints how often the model reports observable behavior, conditions, effects, candidate rules, missing information, and requested actions. **Earlier runs only checked JSON schema, while v0.3 also checks evidence coverage**, so the `valid_json` status has evolved. Neither the comparison nor a valid response establishes rule correctness.

### Offline tests (Qwen not loaded)

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m unittest discover -s tests -p test_research_agent_v03.py -v
```

## Known limitations

1. Qwen3-0.6B can still return incomplete interpretations or classify an observable rule as technical logic. The prompt cannot guarantee correctness.
2. The evidence checker validates **coverage and structural consistency**, not full SQL semantics or factual business meaning. Some heuristic warnings may be false positives.
3. No catalog lookup, related-object inspection, retrieval-augmented generation, or ReAct loop runs in this version. Actions are proposals only.
4. Complex SQL, deeply nested control flow, dynamic SQL, and data effects outside the provided AST/inventory require future improvements.
5. The model is loaded once per batch. The default maximum generated tokens for CLI runs is 512; CPU execution time can vary.

## Traceability

Every individual model output keeps the original task evidence, prompt, raw generated text, validation results and timing. `research_tasks.json` is never changed by a research pass. Historical `run_008` artifacts remain intact for comparison.
