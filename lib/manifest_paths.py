"""Shared validation for artifact paths supplied by run-local manifests.

Future processors, including SQL AST Parsers, can import this helper to use
the same path traversal protection without duplicating the implementation.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


def resolve_manifest_path(base_directory: str | Path, relative_path: str) -> Path:
    """Resolve a manifest-relative path and reject escaping or absolute paths.

    Both Unix and Windows separators are accepted for portability.
    Symlink targets are resolved before checking containment.

    Args:
        base_directory: The trusted directory containing the referenced files.
        relative_path: Relative path read from a manifest.

    Returns:
        A resolved pathlib.Path inside the trusted directory.

    Raises:
        ValueError: An absolute, empty, traversal, or escaping path is supplied.
    """
    base = Path(base_directory).expanduser().resolve()
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("Manifest path must be a nonempty string")
    portable = PurePosixPath(relative_path.replace("\\", "/"))
    win = PureWindowsPath(relative_path)
    if portable.is_absolute() or win.is_absolute() or win.drive or ".." in portable.parts:
        raise ValueError(f"Unsafe path in manifest: {relative_path!r}")
    resolved = base.joinpath(*portable.parts).resolve()
    if not resolved.is_relative_to(base):
        raise ValueError(f"Path escapes its manifest directory: {relative_path!r}")
    return resolved
