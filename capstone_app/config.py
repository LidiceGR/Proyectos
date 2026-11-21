"""Parse and validate the Capstone pipeline configuration."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_ALLOWED_RESEARCH = frozenset({"none", "preview", "single", "batch", "react", "multiagent"})


@dataclass(frozen=True)
class PipelineConfig:
    """Validated immutable configuration for one new workspace run."""

    run_id: str
    workspace: Path
    source_config: Path
    initialize_rag: bool
    research_mode: str
    model_id: str
    task_id: str | None
    max_tasks: int = 0
    max_actions: int = 3
    max_derived_tasks: int = 4
    max_depth: int = 1
    react_strategy: str = "v04"
    policy_fallback: bool = False
    max_verification_rounds: int = 2
    max_feedback_actions: int = 3
    allow_incomplete_batch: bool = False

    @property
    def run_directory(self) -> Path:
        return self.workspace / self.run_id


def _require_string(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' must be a non-empty string")
    return value.strip()


def load_config(config_file: Path, project_root: Path) -> PipelineConfig:
    """Resolve project-root-relative paths and validate the pipeline options."""
    if not config_file.is_file():
        raise FileNotFoundError(f"Pipeline configuration was not found: {config_file}")
    document = json.loads(config_file.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict):
        raise ValueError("Pipeline configuration must be a JSON object")
    run_id = _require_string(document.get("run_id"), "run_id")
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("'run_id' must contain only letters, digits, underscores, or hyphens")
    workspace_name = _require_string(document.get("workspace"), "workspace")
    source_name = _require_string(document.get("source_config"), "source_config")
    workspace = (project_root / workspace_name).resolve()
    source_config = (project_root / source_name).resolve()
    if not source_config.is_file():
        raise FileNotFoundError(f"Source extractor configuration was not found: {source_config}")
    # A run directory must not overlap the application root or its input source files.
    if workspace == project_root or workspace in project_root.parents:
        raise ValueError("Workspace must not be the project root or one of its parent folders")
    if source_config == workspace or workspace in source_config.parents:
        raise ValueError("Source configuration must be outside the workspace")

    rag = document.get("rag", {})
    if not isinstance(rag, dict) or not isinstance(rag.get("initialize", False), bool):
        raise ValueError("'rag.initialize' must be a boolean")
    research = document.get("research", {})
    if not isinstance(research, dict):
        raise ValueError("'research' must be an object")
    mode = research.get("mode", "none")
    if mode not in _ALLOWED_RESEARCH:
        raise ValueError("'research.mode' must be one of: none, preview, single, batch, react")
    model_id = _require_string(research.get("model_id", "Qwen/Qwen3-0.6B"), "research.model_id")
    task_id = research.get("task_id")
    if task_id is not None and (not isinstance(task_id, str) or not task_id.strip()):
        raise ValueError("'research.task_id' must be null or a non-empty string")
    max_tasks = research.get("max_tasks", 0)
    if type(max_tasks) is not int or max_tasks < 0:
        raise ValueError("'research.max_tasks' must be an integer greater than or equal to zero")
    if mode in {"batch", "react", "multiagent"} and task_id is not None:
        raise ValueError("'research.task_id' must be null when research.mode is batch, react or multiagent")
    limits = {}
    for key, default in (("max_actions", 3), ("max_derived_tasks", 4), ("max_depth", 1), ("max_verification_rounds", 2), ("max_feedback_actions", 3)):
        val = research.get(key, default)
        if type(val) is not int or val < 0:
            raise ValueError(f"research.{key} must be a nonnegative integer")
        limits[key] = val
    strategy = research.get("react_strategy", "v04")
    if strategy not in {"v04", "v05"}:
        raise ValueError("research.react_strategy must be v04 or v05")
    allow_incomplete_batch = research.get("allow_incomplete_batch", False)
    if type(allow_incomplete_batch) is not bool:
        raise ValueError("research.allow_incomplete_batch must be a boolean")
    if allow_incomplete_batch and mode != "batch":
        raise ValueError("research.allow_incomplete_batch is only supported for batch mode")
    fallback = research.get("policy_fallback", False)
    if type(fallback) is not bool:
        raise ValueError("research.policy_fallback must be boolean")
    return PipelineConfig(
        run_id=run_id,
        workspace=workspace,
        source_config=source_config,
        initialize_rag=rag.get("initialize", False),
        research_mode=mode,
        model_id=model_id,
        task_id=task_id,
        max_tasks=max_tasks,
        react_strategy=strategy,
        policy_fallback=fallback,
        allow_incomplete_batch=allow_incomplete_batch,
        **limits,
    )


def apply_overrides(
    config: PipelineConfig,
    *,
    run_id: str | None = None,
    initialize_rag: bool | None = None,
    research_mode: str | None = None,
) -> PipelineConfig:
    """Apply optional CLI overrides without mutating the JSON document."""
    if run_id is not None:
        if not _RUN_ID.fullmatch(run_id):
            raise ValueError("Invalid --run-id")
        config = replace(config, run_id=run_id)
    if initialize_rag is not None:
        config = replace(config, initialize_rag=initialize_rag)
    if research_mode is not None:
        if research_mode not in _ALLOWED_RESEARCH:
            raise ValueError("Invalid --research-mode")
        config = replace(config, research_mode=research_mode)
    if config.research_mode in {"batch", "react", "multiagent"} and config.task_id is not None:
        raise ValueError("Batch/React mode requires research.task_id to be null")
    return config
