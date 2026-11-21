"""Bounded ReAct controller with recorded ACT/OBSERVE steps and derived tasks.

The model proposes actions; the controller also closes objectively detectable
catalog/trigger gaps as *policy actions*, always labeled separately from model
choices. No SQL executes and no source/initial task artifact is modified.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Any, Callable

from .agent import SYSTEM_PROMPT, PROJECT_ROOT
from .batch import _read_pending, _validate_selected_task, _write_checkpoint
from .context_builder import compact_workflow_context
from .derived_tasks import discover_affected_triggers, register_investigation
from .evidence_checker import check_evidence
from .tools import ResearchTools, _identifier, _short_name
from .validator import extract_json_object, validate_decision

REACT_PROMPT_FILE = PROJECT_ROOT / "prompts" / "react_sql_research.txt"
REACT_SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
    "No tools are available in this step.",
    "You can REQUEST read-only tools. The controller runs them and returns observations."
)
RETRIEVAL_ACTIONS = {"lookup_catalog_value", "inspect_related_object"}
ENQUEUE_ACTION = "enqueue_research_task"
MAX_FORMAT_RETRIES = 1  # Additional model calls per reasoning step, not ACT/OBSERVE actions.


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _action_key(action: dict[str, Any]) -> str:
    return json.dumps(action, sort_keys=True, ensure_ascii=False)


def _source_policy_actions(task: dict[str, Any], tools: ResearchTools) -> list[dict[str, Any]]:
    """Offer lookups for actual categorical equalities backed by extracted catalogs."""
    fragments = task.get("fragment", {})
    steps = fragments.get("steps") if isinstance(fragments.get("steps"), list) else [fragments]
    candidates: list[dict[str, Any]] = []
    available = {_short_name(str(c.get("key_column", ""))) for c in tools.catalogs}
    predicate = re.compile(r"(?i)(?<!\w)(@?[\w\[\].]+)\s*=\s*(?:'([^']+)'|\b(\d+)\b)")
    for step in steps:
        pieces = [str(c.get("expression", ""))
                  for category in ("condition_context", "where_conditions")
                  for c in step.get(category, []) if isinstance(c, dict)]
        effects = step.get("effects") or {}
        for assign in [*(effects.get("assignments") or []), *(effects.get("column_values") or [])]:
            if isinstance(assign, dict):
                pieces.append(f"{assign.get('column', '')} = {assign.get('expression', '')}")
        for piece in pieces:
            for match in predicate.finditer(piece):
                column = match.group(1).replace("[", "").replace("]", "")
                if _short_name(column) not in available:
                    continue
                known_tables = [str(step.get("target_table") or ""),
                                *(str(t) for t in step.get("read_tables", []))]
                known_tables = [t for t in known_tables if t]
                # Do not interpret OperationChecks.Status using the unrelated
                # Operations.Status catalog merely because names coincide.
                candidate_catalogs = [c for c in tools.catalogs if
                    _short_name(str(c.get("key_column", ""))) == _short_name(column)]
                explicit_scopes = [c for c in candidate_catalogs if c.get("applies_to")]
                if known_tables and explicit_scopes and not any(
                    _identifier(str(t)) == _identifier(str(scope))
                    for t in known_tables for c in explicit_scopes
                    for scope in c.get("applies_to", [])
                ):
                    continue
                value: Any = match.group(2) if match.group(2) is not None else int(match.group(3))
                candidates.append({"name": "lookup_catalog_value", "arguments": {"column": column, "value": value}})
    # The graph lets us safely discover triggers that are relevant to this DML.
    for step in steps:
        target = step.get("target_table")
        operation = str(step.get("operation", "")).upper()
        if not target or operation not in {"INSERT", "UPDATE", "DELETE"}:
            continue
        if any(edge.get("type") == "TRIGGERS_ON"
               and _identifier(str((edge.get("to") or {}).get("name", ""))) == _identifier(str(target))
               and operation in [str(e).upper() for e in edge.get("events", [])]
               for edge in tools.relationships):
            candidates.append({"name": "inspect_related_object", "arguments": {"object_name": str(target)}})
    unique: dict[str, dict[str, Any]] = {}
    for action in candidates:
        unique.setdefault(_action_key(action), action)
    return list(unique.values())


def render_react_prompt(task: dict[str, Any], observations: list[dict[str, Any]],
                        actions_remaining: int, *, pending_hint: list[dict[str, Any]] | None = None,
                        template_path: str | Path | None = None) -> str:
    """Render each model turn using original source, accumulated tool observations."""
    payload = compact_workflow_context(task) if task.get("task_type") == "analyze_sql_workflow" else {
        "task_id": task["task_id"], "task_type": task.get("task_type"),
        "target": task["target"], "fragment": task["fragment"], "evidence": task["evidence"],
        "related_objects": task.get("related_objects", []),
    }
    if task.get("research_question"):
        payload["research_question"] = task["research_question"]
    template = Template(Path(template_path or REACT_PROMPT_FILE).read_text(encoding="utf-8"))
    return template.substitute(
        task_json=json.dumps(payload, ensure_ascii=False, indent=2),
        observations_json=json.dumps(observations, ensure_ascii=False, indent=2),
        remaining_actions=str(actions_remaining),
        research_hints_json=json.dumps(pending_hint or [], ensure_ascii=False),
    )



def render_format_repair_prompt(original_prompt: str, raw_response: str,
                                validation_errors: list[str]) -> str:
    """Ask Qwen to fix its output contract without changing the source evidence."""
    error_lines = "\n".join(f"- {error}" for error in validation_errors)
    return (
        original_prompt
        + "\n\nFORMAT REPAIR REQUIRED. The prior response is NOT accepted.\n"
        + "Validation errors:\n" + error_lines + "\n"
        + "PRIOR RESPONSE (untrusted, possibly incomplete):\n"
        + raw_response[:9000] + "\n\n"
        + "Return one CORRECTED JSON object with exactly the seven required keys. "
        + "Do NOT copy conditions objects from ordered_steps; conditions must be "
        + "a JSON array of strings such as [\"Amount <= 0\"]. "
        + "The effect field MUST be a plain string, never an object and never a "
        + "placeholder. If classification is candidate_business_rule and "
        + "requested_action is null, candidate_rule MUST contain a tentative "
        + "nonempty rule supported by the source. Do not invent meaning. "
        + "Preserve any valid requested_action, if present. Do not explain the repair. "
        + "JSON only.\n"
    )

def _validate_action(action: dict[str, Any]) -> str | None:
    name = action.get("name")
    args = action.get("arguments")
    if not isinstance(args, dict):
        return "Tool arguments must be an object"
    if name == "lookup_catalog_value":
        if set(args) != {"column", "value"}:
            return "Catalog lookup requires exactly column and value"
        if not isinstance(args["column"], str) or not args["column"].strip():
            return "Catalog column must be a nonempty string"
        if not isinstance(args["value"], (str, int, float)) or isinstance(args["value"], bool):
            return "Catalog value must be a literal string or number"
    elif name == "inspect_related_object":
        if set(args) != {"object_name"} or not isinstance(args["object_name"], str) or not args["object_name"].strip():
            return "Object inspection requires exactly object_name"
    elif name == ENQUEUE_ACTION:
        if set(args) != {"object_name", "research_question", "reason"}:
            return "Task creation requires object_name, research_question and reason"
        if any(not isinstance(args[k], str) or not args[k].strip() or len(args[k]) > 500 for k in args):
            return "Task creation arguments must be nonempty bounded strings"
    else:
        return "Unsupported ReAct action: " + str(name)
    return None


def investigate_task(
    task: dict[str, Any], tools: ResearchTools, generate: Callable[[str, str], str],
    *, model_id: str, known_tasks: list[dict[str, Any]], derived: list[dict[str, Any]],
    max_actions: int = 3, max_derived: int = 4,
    on_derived_change: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Run a bounded ReAct loop; retain every model turn and tool observation."""
    if max_actions < 0:
        raise ValueError("max_actions cannot be negative")
    start = time.perf_counter()
    result: dict[str, Any] = {
        "schema_version": "1.1", "generated_utc": _now(), "task_id": task["task_id"],
        "model_id": model_id, "stage": "react_research_with_tools",
        "task_evidence": task["evidence"], "system_prompt": REACT_SYSTEM_PROMPT,
        "user_prompt": None, "raw_model_output": None, "decision": None,
        "validation_errors": [], "evidence_check": None, "status": "execution_error",
        "trace": [], "observations": [], "tool_actions_executed": 0,
        "actions_executed": 0, "model_actions_executed": 0,
        "policy_actions_executed": 0, "queue_actions_executed": 0,
        "related_task_links": [], "format_repair_attempts": 0,
        "format_repairs_successful": 0,
        "note": "Candidate findings are not verified business rules.",
    }
    policy = _source_policy_actions(task, tools)
    executed: set[str] = set()
    for turn in range(max_actions + 1):
        remaining = max_actions - len(executed)
        prompt = render_react_prompt(task, result["observations"], remaining,
                                     pending_hint=[a for a in policy if _action_key(a) not in executed])
        result["user_prompt"] = prompt
        decision: dict[str, Any] | None = None
        errors: list[str] = []
        raw = ""
        hard_failure = False
        for attempt in range(MAX_FORMAT_RETRIES + 1):
            current_prompt = (
                prompt if attempt == 0
                else render_format_repair_prompt(prompt, raw, errors)
            )
            if attempt:
                result["format_repair_attempts"] += 1
                print(f"[react_format_repair] task_id={task['task_id']} turn={turn+1} "
                      f"attempt={attempt}", flush=True)
            try:
                raw = generate(current_prompt, REACT_SYSTEM_PROMPT)
                if not isinstance(raw, str):
                    raise TypeError("Model output must be a string")
                result["raw_model_output"] = raw
                decision = extract_json_object(raw)
                errors = validate_decision(decision, require_observed_behavior=True)
            except ValueError as exc:
                decision = None
                errors = [f"ValueError: {exc}"]
            except Exception as exc:
                decision = None
                errors = [f"{type(exc).__name__}: {exc}"]
                hard_failure = True
            result["decision"] = decision
            result["validation_errors"] = errors
            result["trace"].append({
                "turn": turn + 1, "kind": "reason", "attempt": attempt + 1,
                "origin": "format_repair" if attempt else "model",
                "prompt": current_prompt, "raw_model_output": raw,
                "decision": decision, "validation_errors": list(errors),
            })
            if not errors:
                if attempt:
                    result["format_repairs_successful"] += 1
                break
            if hard_failure:
                break
        if errors or decision is None:
            result["status"] = "execution_error" if hard_failure else "invalid_response"
            break
        action = decision.get("requested_action")
        origin = "model"
        if action is None and remaining > 0:
            # A small LLM may prematurely conclude despite known source-backed
            # catalog/trigger gaps. Record these as policy actions, not model actions.
            action = next((a for a in policy if _action_key(a) not in executed), None)
            origin = "evidence_gap_policy"
        if action is None:
            check = check_evidence(task, decision)
            result["evidence_check"] = check
            result["status"] = "valid_json" if check["status"] == "passed" else "evidence_incomplete"
            break
        if remaining <= 0:
            result["status"] = "research_incomplete"
            result["validation_errors"].append("ReAct action budget was exhausted before a final answer")
            break
        issue = _validate_action(action)
        if issue:
            result["status"] = "research_incomplete"
            result["validation_errors"].append(issue)
            break
        key = _action_key(action)
        if key in executed:
            result["status"] = "research_incomplete"
            result["validation_errors"].append("Repeated action was rejected to prevent loops")
            break
        executed.add(key)
        result["actions_executed"] += 1
        result["model_actions_executed" if origin == "model" else "policy_actions_executed"] += 1
        if action["name"] == ENQUEUE_ACTION:
            result["queue_actions_executed"] += 1
        else:
            result["tool_actions_executed"] += 1
        print(f"[react_act] task_id={task['task_id']} turn={turn+1} origin={origin} action={action['name']}", flush=True)
        try:
            if action["name"] == ENQUEUE_ACTION:
                args = action["arguments"]
                observation = register_investigation(tools, task, args["object_name"],
                    args["research_question"], args["reason"], known_tasks, derived, max_derived)
            else:
                observation = tools.execute(action)
                if action["name"] == "inspect_related_object" and observation.get("status") == "found":
                    observation["dependent_investigations"] = discover_affected_triggers(
                        tools, task, action["arguments"]["object_name"], known_tasks, derived, max_derived
                    )
            if on_derived_change is not None:
                on_derived_change()
        except Exception as exc:
            observation = {"status": "tool_error", "error": f"{type(exc).__name__}: {exc}"}
        if isinstance(observation, dict) and observation.get("status") in {"created", "linked_existing"}:
            result["related_task_links"].append(observation)
        for found in observation.get("dependent_investigations", []):
            if found.get("status") in {"created", "linked_existing"}:
                result["related_task_links"].append(found)
        print(f"[react_observe] task_id={task['task_id']} action={action['name']} status={observation.get('status')} links={len(result['related_task_links'])}", flush=True)
        event = {"turn": turn + 1, "kind": "act_observe", "origin": origin,
                 "action": action, "observation": observation}
        result["trace"].append(event)
        result["observations"].append({"action": action, "origin": origin, "observation": observation})
    result["elapsed_seconds"] = round(time.perf_counter() - start, 3)
    return result


def run_react_batch(
    tasks_path: str | Path, generate: Callable[[str, str], str], *, model_id: str,
    max_tasks: int = 0, max_actions: int = 3, max_derived_tasks: int = 4,
    max_depth: int = 1, output_directory: str | Path | None = None,
    device_provider: Callable[[], str | None] | None = None,
) -> dict[str, Any]:
    """Research initial and discovered tasks; preserve source queue and checkpoints."""
    if any(type(v) is not int or v < 0 for v in (max_tasks, max_actions, max_derived_tasks, max_depth)):
        raise ValueError("ReAct limits must be nonnegative integers")
    path = Path(tasks_path).resolve()
    initial = _read_pending(path)
    selected = initial if max_tasks == 0 else initial[:max_tasks]
    # Include also nonpending tasks in deduplication.
    known = json.loads(path.read_text(encoding="utf-8-sig"))["tasks"]
    tools = ResearchTools.from_tasks_file(path)
    destination = Path(output_directory).resolve() if output_directory else path.parent / "agent_runs"
    summary_path = destination / "react_summary.json"
    derived_path = path.parent / "derived_tasks.json"
    if summary_path.exists() or derived_path.exists():
        raise FileExistsError("Existing ReAct artifacts cannot be overwritten; use a new run_id")
    derived: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "schema_version": "1.1", "mode": "react_bounded_with_tools", "started_utc": _now(),
        "finished_utc": None, "status": "running", "model_id": model_id,
        "tasks_file": str(path), "derived_tasks_file": str(derived_path),
        "settings": {"max_actions_per_task": max_actions, "max_derived_tasks": max_derived_tasks,
                     "max_depth": max_depth, "max_initial_tasks": max_tasks},
        "counts": {"total_pending": len(initial), "selected": len(selected), "processed": 0,
                   "initial_processed": 0, "derived_created": 0, "derived_processed": 0,
                   "valid_json": 0, "evidence_incomplete": 0, "research_incomplete": 0,
                   "invalid_response": 0, "execution_error": 0,
                   "candidate_rules_proposed": 0, "tool_actions_executed": 0,
                   "queue_actions_executed": 0, "model_actions_executed": 0,
                   "policy_actions_executed": 0,
                   "format_repair_attempts": 0, "format_repairs_successful": 0,
                   "not_selected": len(initial) - len(selected)},
        "results": [], "note": "Original research_tasks.json is unchanged. Derived tasks are separate and source-backed.",
    }

    def save_derived() -> None:
        _write_checkpoint(derived_path, {"schema_version": "1.0", "generated_utc": _now(),
                                         "source_tasks": str(path), "tasks": derived})
        summary["counts"]["derived_created"] = len(derived)
        _write_checkpoint(summary_path, summary)

    save_derived()
    queue: list[tuple[dict[str, Any], int]] = [(t, 0) for t in selected]
    queued: set[str] = {t["task_id"] for t in selected}
    print(f"[react_started] initial={len(selected)} max_actions={max_actions} max_derived={max_derived_tasks}", flush=True)
    i = 0
    while i < len(queue):
        task, depth = queue[i]
        i += 1
        print(f"[react_task_started] task_id={task['task_id']} depth={depth}", flush=True)
        try:
            _validate_selected_task(task)
            result = investigate_task(task, tools, generate, model_id=model_id,
                                      known_tasks=known, derived=derived, max_actions=max_actions,
                                      max_derived=max_derived_tasks if depth < max_depth else len(derived),
                                      on_derived_change=save_derived)
        except Exception as exc:
            result = {"schema_version": "1.1", "generated_utc": _now(), "task_id": task["task_id"],
                      "stage": "react_research_with_tools", "status": "execution_error", "decision": None,
                      "validation_errors": [f"{type(exc).__name__}: {exc}"], "trace": [],
                      "observations": [], "tool_actions_executed": 0,
                      "queue_actions_executed": 0, "model_actions_executed": 0,
                      "policy_actions_executed": 0, "elapsed_seconds": 0.0,
                      "evidence_check": None}
        result["device"] = device_provider() if device_provider else None
        result["research_depth"] = depth
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        result_file = destination / f"{task['task_id']}_{stamp}.json"
        destination.mkdir(parents=True, exist_ok=True)
        with result_file.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        status = result["status"]
        summary["counts"]["processed"] += 1
        summary["counts"]["initial_processed" if depth == 0 else "derived_processed"] += 1
        summary["counts"][status] += 1
        for counter in ("tool_actions_executed", "queue_actions_executed",
                        "model_actions_executed", "policy_actions_executed",
                        "format_repair_attempts", "format_repairs_successful"):
            summary["counts"][counter] += result.get(counter, 0)
        decision = result.get("decision") or {}
        if status == "valid_json" and decision.get("classification") == "candidate_business_rule" and decision.get("candidate_rule"):
            summary["counts"]["candidate_rules_proposed"] += 1
        summary["results"].append({"task_id": task["task_id"], "depth": depth, "status": status,
                                   "result_file": str(result_file), "classification": decision.get("classification"),
                                   "candidate_rule_proposed": bool(decision.get("candidate_rule")),
                                   "tool_actions_executed": result.get("tool_actions_executed", 0),
                                   "format_repair_attempts": result.get("format_repair_attempts", 0),
                                   "evidence_status": (result.get("evidence_check") or {}).get("status"),
                                   "evidence_issues": (result.get("evidence_check") or {}).get("issues", []),
                                   "validation_errors": result.get("validation_errors", []),
                                   "related_task_links": result.get("related_task_links", [])})
        if depth < max_depth:
            for child in derived:
                if child["task_id"] not in queued and child.get("parent_task_id") == task["task_id"]:
                    queue.append((child, depth + 1))
                    queued.add(child["task_id"])
        save_derived()
        print(f"[react_task_completed] task_id={task['task_id']} status={status} actions={result.get('tool_actions_executed', 0)}", flush=True)
    summary["finished_utc"] = _now()
    failed = sum(summary["counts"][name] for name in ("evidence_incomplete", "research_incomplete", "invalid_response", "execution_error"))
    summary["status"] = "completed_with_errors" if failed else "completed"
    save_derived()
    print(f"[react_completed] status={summary['status']} processed={summary['counts']['processed']} tools={summary['counts']['tool_actions_executed']} derived={len(derived)}", flush=True)
    print(f"[react_summary_file] path={summary_path}", flush=True)
    return summary
