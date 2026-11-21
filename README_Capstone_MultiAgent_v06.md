# Carnegie IA Capstone — Integrated Multi-Agent Research v0.6

This release builds on the original project code. **It is not a separate demo service.** The existing extractor, SQLFluff AST parser, inventory, logical-workflow task initializer, and Qwen Research Agent remain the initial pipeline.

## 1. What is included

1. The **programmatic Coordinator** (`capstone_app/`) orchestrates source extraction, AST parsing, inventory, initial tasks, optional local retrieval indexing, the SQL Research Agent, and the Evidence Verifier.
2. The **Resource Acquisition role** (`resource_acquisition/agent.py`) is a deterministic *allowlisted tool broker*, not a separate LLM. It exposes run-local catalog lookup, dependency inspection, source excerpts, related findings and retrieval. Database access still occurs only in the pre-existing source extractor.
3. The **SQL Research Agent** (`research_agent/react_v05.py`) separates REASON/ACT/OBSERVE decisions from FINAL candidate synthesis. It tracks model actions, policy actions, source references, and derived tasks.
4. The **Evidence Verification Agent** (`evidence_verifier/agent.py`) uses a distinct verifier prompt and critical evaluation schema, backed by deterministic checks (`evidence_verifier/deterministic.py`). In this configuration it may use the same local Qwen model weights as the Researcher, so correlated model errors remain possible.
5. The **Multi-Agent Coordinator** (`evidence_verifier/orchestrator.py`) routes missing evidence back to the SQL Researcher, executes justified read-only requests, regenerates a revised candidate and verifies it again. It also processes source-backed derived investigations requested by verification. Limits prevent uncontrolled loops.
6. **Local retrieval** (`rag_tools/initializer.py`) indexes source-backed, AST-aligned SQL snippets and ranks them using BM25. **This is not semantic embedding RAG** and does not claim embedding support.
7. **Evaluation infrastructure** (`evaluation/compare_to_reference.py`) can score classification, literal condition recall, required effect-term recall, and escalation sensitivity *only after an expert-reviewed reference set is supplied*. A template is included; no accuracy numbers have been invented.
8. Two user interfaces: `Capstone_Artifact_Explorer_v07.html` for complete artifact navigation, and `Capstone_Business_Rule_Review_Demo_v01.html` for simulated human review.

## 2. New system1 validation scenario

The original `system1` SQL and catalog samples remain, with three added SQL files:

| New file | Purpose |
|---|---|
| `03_operation_checks_schema.sql` | Defines a synthetic operation-check table with its own Status field. |
| `04_HasPendingOperationCheck.sql` | Exposes an output parameter telling the caller whether a matching operation has a pending check. |
| `05_RegisterOperationAfterCheck.sql` | Calls `HasPendingOperationCheck` and then calls `RegisterOperation` **only if** the returned flag is zero. |

This tests a rule distributed across procedure calls, output parameters, an `IF` decision, an additional table and a separately implemented registration rule. **The new check is fictional test data, not a real business policy.**

The catalog samples now declare `applies_to: ["dbo.Operations"]` to avoid erroneously applying the `Operations.Status` meanings to `OperationChecks.Status` merely because their column names match. Older catalogs without scope metadata retain legacy lookup behavior; catalog scope must be confirmed in a real system.

Static `EXEC` statements become ordered `CALL` operations plus `CALLS` graph edges. The inventory first uses its parsed AST; a conservative line-based fallback preserves literal static calls if SQLFluff's dialect nodes do not expose them. It does not claim to resolve dynamic SQL or all expression-level dependencies.

## 3. Run locally on Windows

Extract this package into the root of your existing `D:\Proyectos\CMUAgent\Proyectos` folder, merging the corresponding directories. **Use a new run ID each time** to preserve previous results. Your Conda environment must already have Python 3.11, SQLFluff 3.3.1, `transformers`, PyTorch and the local model available or downloadable.

First, inspect without running:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m capstone_app --config config\capstone_pipeline.multiagent.system1.run_014.json --dry-run
```

Then run the complete scenario:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m capstone_app --config config\capstone_pipeline.multiagent.system1.run_014.json
```

`run_015` uses `system2` with the same multi-agent implementation:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m capstone_app --config config\capstone_pipeline.multiagent.system2.run_015.json
```

These are **configuration files and commands, not claims of completed real-Qwen executions**.

## 4. Important output files

```text
workspace/run_014/
├── sources_manifest.json
├── sql_sources/                 # Original collected SQL
├── catalogs/                    # Catalog source artifacts
├── ast_files/                   # SQLFluff AST + ast_manifest.json
├── metadata/
│   ├── inventory.json
│   ├── relationships.json       # Includes source-backed CALLS edges
│   ├── catalogs.json
│   └── extraction_issues.json
├── rag/index.json              # BM25 index, when initialize=true
├── research/
│   ├── research_tasks.json      # Initial workflow tasks
│   ├── derived_tasks.json       # ReAct + verifier-derived tasks
│   ├── agent_runs/
│   │   ├── TASK-...json         # REASON/ACT/OBSERVE/FINAL traces
│   │   └── react_summary.json
│   ├── verifications/
│   │   └── TASK-...json         # Verifier, feedback loops and evidence gates
│   ├── findings.json            # Technically evidenced CANDIDATES only
│   ├── pending.json             # Outstanding investigations / escalation
│   ├── human_corrections.json   # Empty placeholder; no backend yet
│   ├── evaluation_metrics.json  # Operational counters, NOT truth accuracy
│   └── multiagent_summary.json
└── pipeline_status.json
```

## 5. How the verifier works

After the Research Agent finishes, the coordinator provides its proposed rule, original SQL, tool observations, static dependencies and line references to the verification stage.

- **Deterministic gates:** check for source conditions and effects, output aliases, available but unqueried catalog literals, called procedures without reviewed findings, and source-backed column coverage warnings. They can veto an optimistic LLM `accepted` response.
- **Verifier LLM:** evaluates unsupported assertions, missing guards, contradictions, unresolved dependencies, and whether the conclusion is attributable to a specific source. Its output is structured as `accepted`, `needs_research`, or `insufficient_evidence`.
- **Controlled feedback:** missing evidence produces validated, read-only tool calls to the acquisition broker and a new Researcher final synthesis. If a newly related source-backed procedure/trigger has not been investigated, the verifier may request a derived task; the coordinator applies deduplication and a depth/size limit.
- **Stop:** after configured verification rounds, remaining concerns become pending human-review items, **never silently approved**.

The verifier is a model-based critic, **not a proof engine**. An `accepted` verifier response alone cannot certify business semantics. A finding's `technically_evidenced` status is explicitly distinct from human approval.

## 6. Human-review demonstration

Open `Capstone_Business_Rule_Review_Demo_v01.html` in Edge or Chrome. Without files it displays **clearly labeled illustrative rules**. Select a completed run folder to load its `research/findings.json` and `research/pending.json`. The specialist can:

- Select a candidate and inspect conditions, effects, source file/lines and dependencies.
- Click **Approve** to change the label **within the open page only**.
- Type a comment; the disabled **Reinvestigar (próximamente)** button then appears.

**No action persists, updates `human_corrections.json`, connects to a database or triggers an agent.** That is intentionally deferred.

Open `Capstone_Artifact_Explorer_v07.html` to navigate every loaded `.sql`, `.json`, `.md`, or `.txt` artifact. It is complementary to the earlier process-oriented Explorer v0.6. Browser local-file restrictions are handled by the directory-picker input (Chromium-based browser).

## 7. Mapping to Carnegie course deliveries 5 and 6

| Course objective | Concrete implementation | Status |
|---|---|---|
| Centralized, cooperative coordination | `capstone_app/coordinator.py` plus `evidence_verifier/orchestrator.py` | Implemented |
| Resource acquisition / controlled access | `source_extractor/` and `resource_acquisition/agent.py` allowlist | Implemented as deterministic service; not a resource LLM |
| Research Agent with ReAct and source-grounded tools | `research_agent/react_v05.py`, `tools.py`, `prompts/react_v05_decide.txt` | Implemented; real model quality must be tested |
| Evidence Verification Agent | `evidence_verifier/agent.py`, `prompts/verify_evidence_v06.txt` | Implemented |
| Agent-to-agent feedback and tasks | `evidence_verifier/orchestrator.py`, `derived_tasks.py` | Implemented within configurable limits |
| Structured shared state | `research_tasks.json`, `derived_tasks.json`, `agent_runs`, `verifications`, `findings.json`, `pending.json` | Implemented |
| Guardrails | Tool allowlist, no arbitrary SQL execution by agents, bounded source-path reads, limits, schema validation | Implemented baseline; requires production security review |
| Traceability and logging | Original SQL/line references, model decisions, tool observations, errors/retries, timing | Implemented |
| RAG | Local BM25 index with source-backed chunks; optional lookup tool | Lexical baseline only, not embeddings |
| Evaluation | Operational counters and expert-reference comparison tool | Infrastructure ready; no 20–30 human-labeled cases yet |
| Human validation | Read-only UI simulation; explicit pending-approval artifacts | Front-end simulation only |
| Java/application-layer analysis | Original roadmap for Java, JSP, JPA, services | **Not implemented in this SQL-focused release** |
| MCP transport | Current tools are local Python dispatch | **Not an MCP server** |

Note: Delivery 5 calls the resource-acquisition role an “agent”, while Delivery 6 describes the coordinator as one of three agents. This implementation uses **two LLM roles (Researcher and Verifier)** plus a deterministic Coordinator and Resource Acquisition broker, making their actual behavior explicit rather than mislabeling every Python module as an autonomous model.

## 8. ReAct and multi-agent demonstration for the final presentation

1. `react_v05.py::investigate_task_v05()` — model chooses `act` vs. `finish`.
2. `react_v05.py::_validate_action()` — validates allowed tool name, arguments and source permissions.
3. `resource_acquisition/agent.py::ResourceAcquisitionAgent.execute()` — dispatches a bounded read-only tool call.
4. `research_agent/tools.py` — data retrieval from catalog, inventory, SQL lines, related findings, or BM25 index.
5. `react_v05.py` trace entries `research_decision` and `act_observe` — the **real observation** is added to the subsequent model input.
6. `evidence_verifier/deterministic.py::coverage_gates()` — checks source-backed omissions and known but unconsulted catalogs/CALLS.
7. `evidence_verifier/agent.py::verify_with_model()` — a second role critiques the proposal, not merely generating another rule.
8. `evidence_verifier/orchestrator.py::run_multiagent_batch()` — validates verification requests, sends feedback to the Researcher and saves iterations.
9. `research/verifications/TASK-...json` — shows the full back-and-forth and stopping criteria.

## 9. Tests and known limitations

Run:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m pytest -q tests
```

The build was checked with **54 offline automated tests**; mocked responses verify code execution and coordination. Source extraction for the extended system1 found **7 SQL files and 2 catalogs** without issues. The full AST/SQLFluff-to-Qwen pipeline was **not** executed in this build environment because SQLFluff was unavailable and the local model weights were not loaded. Run `run_014` in your configured Conda environment before considering the new SQL scenario validated end-to-end.

The next engineering work after these tests would be an embeddings-backed RAG adapter, broader Java/SQL dependency support (dynamic SQL, constraints, nested calls, dataflow), realistic evaluation against 20–30 expert-labeled cases, stronger security hardening for production inputs, and a persistent approval/reinvestigation API.
