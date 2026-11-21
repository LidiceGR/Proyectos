"""Run existing Capstone processors without duplicating their business logic."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from .config import PipelineConfig


StageExecutor = Callable[[Sequence[str], Path], int]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _execute(command: Sequence[str], cwd: Path) -> int:
    """Execute a module using the same Python environment as the coordinator."""
    return subprocess.run(list(command), cwd=str(cwd), check=False).returncode


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Expected stage output is missing: {path}")
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return document


class PipelineCoordinator:
    """Coordinate deterministic preparation and optional single or batch research."""

    def __init__(self, config: PipelineConfig, project_root: Path,
                 *, executor: StageExecutor = _execute):
        self.config = config
        self.project_root = project_root.resolve()
        self.executor = executor
        self.run_dir = config.run_directory
        self.status: dict[str, Any] = {
            "schema_version": "1.0",
            "run_id": config.run_id,
            "created_utc": _now(),
            "updated_utc": _now(),
            "status": "running",
            "settings": {
                "source_config": str(config.source_config),
                "initialize_rag": config.initialize_rag,
                "research_mode": config.research_mode,
            },
            "stages": [],
            "artifacts": {},
        }

    def _save(self) -> None:
        self.status["updated_utc"] = _now()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        destination = self.run_dir / "pipeline_status.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.status, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(destination)

    def _stage(self, name: str, command: list[str], expected_file: Path,
               *, validate: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        print(f"[pipeline_stage_started] stage={name}", flush=True)
        stage = {"name": name, "status": "running", "started_utc": _now()}
        self.status["stages"].append(stage)
        try:
            exit_code = self.executor(command, self.project_root)
            if exit_code != 0:
                raise RuntimeError(f"Stage '{name}' exited with code {exit_code}")
            document = _read_json(expected_file)
            if validate is not None:
                validate(document)
            stage["status"] = "ok"
            stage["finished_utc"] = _now()
            self.status["artifacts"][name] = str(expected_file)
            print(f"[pipeline_stage_completed] stage={name} status=ok", flush=True)
            self._save()
            return document
        except Exception as exc:
            stage.update({"status": "error", "finished_utc": _now(), "error": str(exc)})
            self.status.update({"status": "error", "error": str(exc)})
            self._save()
            print(f"[pipeline_stage_failed] stage={name} reason={exc}", file=sys.stderr, flush=True)
            raise

    def _research(self, mode: str, research_tasks: Path) -> None:
        """Run the unchanged single-task interface or the new batch interface."""
        name = {
            "preview": "research_preview",
            "single": "research_single",
            "batch": "research_batch",
            "react": "research_react",
            "multiagent": "research_multiagent",
        }[mode]
        command = [sys.executable, "-m", "research_agent", "--tasks", str(research_tasks)]
        if mode in {"batch", "react", "multiagent"}:
            command += ["--multiagent" if mode == "multiagent" else "--react" if mode == "react" else "--batch",
                        "--model-id", self.config.model_id,
                        "--max-tasks", str(self.config.max_tasks)]
            if mode in {"react", "multiagent"}:
                command += ["--max-actions", str(self.config.max_actions),
                            "--max-derived-tasks", str(self.config.max_derived_tasks),
                            "--max-depth", str(self.config.max_depth)]
                command += ["--react-strategy", self.config.react_strategy]
                if self.config.policy_fallback:
                    command.append("--policy-fallback")
                if mode == "multiagent":
                    command += ["--max-verification-rounds", str(self.config.max_verification_rounds),
                                "--max-feedback-actions", str(self.config.max_feedback_actions)]
        else:
            if self.config.task_id:
                command += ["--task-id", self.config.task_id]
            if mode == "preview":
                command.append("--preview")
            else:
                command += ["--model-id", self.config.model_id]

        print(f"[pipeline_stage_started] stage={name}", flush=True)
        stage = {"name": name, "status": "running", "started_utc": _now()}
        self.status["stages"].append(stage)
        try:
            exit_code = self.executor(command, self.project_root)
            completed_with_warnings = False
            if mode in {"batch", "react", "multiagent"}:
                summary_name = "multiagent_summary.json" if mode == "multiagent" else "react_summary.json" if mode == "react" else "batch_summary.json"
                summary_path = (research_tasks.parent if mode == "multiagent" else research_tasks.parent / "agent_runs") / summary_name
                if not summary_path.exists():
                    raise RuntimeError("Research batch summary is missing")
                summary = _read_json(summary_path)
                counts = summary.get("counts") or {}
                stage["summary"] = counts
                self.status["artifacts"][name] = str(summary_path)
                # An evidence-incomplete baseline is a valid OBSERVATION for an
                # ablation experiment, never a verified business rule. This
                # exception is opt-in and disallows model/JSON execution failures.
                completed_with_warnings = (
                    mode == "batch"
                    and self.config.allow_incomplete_batch
                    and summary.get("status") == "completed_with_errors"
                    and counts.get("selected", 0) > 0
                    and counts.get("processed") == counts.get("selected")
                    and counts.get("valid_json", 0) + counts.get("evidence_incomplete", 0) == counts.get("processed")
                    and counts.get("evidence_incomplete", 0) > 0
                    and counts.get("invalid_response") == 0
                    and counts.get("execution_error") == 0
                    and exit_code == 1
                )
                if summary.get("status") != "completed" and not completed_with_warnings:
                    raise RuntimeError("Research batch summary reports incomplete or failed processing")
            if exit_code != 0 and not completed_with_warnings:
                raise RuntimeError(f"Research stage exited with code {exit_code}")
            stage.update({"status": "completed_with_warnings" if completed_with_warnings else "ok",
                          "finished_utc": _now()})
            if completed_with_warnings:
                stage["warning"] = "Some baseline candidates have insufficient evidence; outputs remain unverified"
            if mode not in {"batch", "react"}:
                self.status["artifacts"][name] = (
                    str(research_tasks.parent / "agent_runs")
                    if mode == "single" else "prompt_preview_only"
                )
            self._save()
            print(f"[pipeline_stage_completed] stage={name} status={stage['status']}", flush=True)
        except Exception as exc:
            stage.update({"status": "error", "finished_utc": _now(), "error": str(exc)})
            self.status.update({"status": "error", "error": str(exc)})
            self._save()
            print(f"[pipeline_stage_failed] stage={name} reason={exc}", file=sys.stderr, flush=True)
            raise

    def _rag_stage(self) -> None:
        """Initialize the optional retrieval index after metadata preparation."""
        from rag_tools.initializer import initialize_rag_index

        stage = {"name": "rag_initialization", "status": "running", "started_utc": _now()}
        self.status["stages"].append(stage)
        print("[pipeline_stage_started] stage=rag_initialization", flush=True)
        try:
            initialize_rag_index(self.run_dir)
            stage.update({"status": "ok", "finished_utc": _now()})
            self._save()
            print("[pipeline_stage_completed] stage=rag_initialization status=ok", flush=True)
        except Exception as exc:
            stage.update({"status": "error", "finished_utc": _now(), "error": str(exc)})
            self.status.update({"status": "error", "error": str(exc)})
            self._save()
            raise

    def plan(self) -> list[tuple[str, list[str], Path | None]]:
        """Compute stage commands and output contracts, without running them."""
        run = self.run_dir
        commands: list[tuple[str, list[str], Path | None]] = [
            (
                "source_extraction",
                [sys.executable, "source_extractor/extractor.py", "--config", str(self.config.source_config), "--destination", str(run)],
                run / "sources_manifest.json",
            ),
            (
                "sql_ast_parsing",
                [sys.executable, "-m", "sql_ast_parsers", "--manifest", str(run / "sources_manifest.json"), "--destination", str(run / "ast_files")],
                run / "ast_files" / "ast_manifest.json",
            ),
            (
                "inventory_building",
                [sys.executable, "-m", "inventory_builder", "--ast-manifest", str(run / "ast_files" / "ast_manifest.json"), "--source-manifest", str(run / "sources_manifest.json"), "--destination", str(run / "metadata")],
                run / "metadata" / "inventory.json",
            ),
            (
                "task_initialization",
                [sys.executable, "-m", "task_initializer", "--inventory", str(run / "metadata" / "inventory.json"), "--relationships", str(run / "metadata" / "relationships.json"), "--ast-directory", str(run / "ast_files"), "--destination", str(run / "research")],
                run / "research" / "research_tasks.json",
            ),
        ]
        return commands

    def run(self, *, dry_run: bool = False) -> dict[str, Any]:
        """Start a fresh run or print its planned stages without side effects."""
        if self.run_dir.exists():
            raise FileExistsError(
                f"Run directory already exists: {self.run_dir}. "
                "Choose a new run_id; existing research artifacts are never overwritten."
            )
        commands = self.plan()
        if dry_run:
            print(f"[pipeline_plan] run_id={self.config.run_id} run_directory={self.run_dir}")
            for name, command, artifact in commands:
                print(f"[pipeline_planned_stage] stage={name} output={artifact}")
                print("  " + subprocess.list2cmdline(command))
            print(f"[pipeline_rag] initialize={str(self.config.initialize_rag).lower()} backend=lexical_bm25")
            print(f"[pipeline_research] mode={self.config.research_mode}")
            return {"status": "dry_run", "run_directory": str(self.run_dir)}


        try:
            for name, command, output in commands:
                assert output is not None
                if name == "source_extraction":
                    def validate_source(doc: dict[str, Any]) -> None:
                        if doc.get("status") != "ok":
                            raise ValueError("Source extraction completed with issues; check sources_manifest.json")
                        if not any(entry.get("relative_path", "").lower().endswith(".sql") for entry in doc.get("files", [])):
                            raise ValueError("Source manifest contains no SQL files")
                    validator = validate_source
                elif name == "sql_ast_parsing":
                    def validate_ast(doc: dict[str, Any]) -> None:
                        if doc.get("status") != "ok":
                            raise ValueError("AST parsing is incomplete; check ast_manifest.json")
                    validator = validate_ast
                elif name == "inventory_building":
                    def validate_inventory(doc: dict[str, Any]) -> None:
                        for key in ("tables", "procedures", "triggers"):
                            if not isinstance(doc.get(key), list):
                                raise ValueError(f"Inventory is missing the '{key}' array")
                        for filename in ("relationships.json", "catalogs.json", "extraction_issues.json"):
                            _read_json(self.run_dir / "metadata" / filename)
                    validator = validate_inventory
                elif name == "task_initialization":
                    def validate_tasks(doc: dict[str, Any]) -> None:
                        if not isinstance(doc.get("tasks"), list):
                            raise ValueError("Task initializer output has no 'tasks' array")
                    validator = validate_tasks
                else:
                    validator = None
                self._stage(name, command, output, validate=validator)
            if self.config.initialize_rag:
                self._rag_stage()
            if self.config.research_mode != "none":
                self._research(self.config.research_mode, self.run_dir / "research" / "research_tasks.json")
            self.status["status"] = ("completed_with_warnings" if any(
                item.get("status") == "completed_with_warnings" for item in self.status["stages"]
            ) else "completed")
            self.status["finished_utc"] = _now()
            self._save()
            print(f"[pipeline_completed] run_id={self.config.run_id} research_mode={self.config.research_mode} status={self.status['status']}", flush=True)
            print(f"[pipeline_status_file] path={self.run_dir / 'pipeline_status.json'}", flush=True)
            return self.status
        except Exception:
            # Stage failure was already recorded by _stage / _research.
            raise
