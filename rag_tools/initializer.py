"""Explicit placeholder for future RAG index initialization."""

from __future__ import annotations

from pathlib import Path


def rag_is_implemented() -> bool:
    """Report whether the indexing capability is available in this version."""
    return False


def initialize_rag_index(run_directory: str | Path) -> None:
    """Initialize a retrieval index for one run (not implemented yet)."""
    raise NotImplementedError(
        "RAG initialization is NOT YET IMPLEMENTED. "
        "Set rag.initialize to false until the index builder is available."
    )
