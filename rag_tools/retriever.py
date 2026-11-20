"""Reserved agent-facing tool for future RAG retrieval."""

from __future__ import annotations

from typing import Any


def retrieve_evidence(query: str, run_directory: str) -> list[dict[str, Any]]:
    """Retrieve grounded evidence from a run-local index (not implemented yet)."""
    raise NotImplementedError("RAG retrieval is NOT YET IMPLEMENTED")
