"""Benchmark-root discovery and guarded paths, including symlink/junction resolution."""
from __future__ import annotations

import os
from pathlib import Path


class PathSafetyError(ValueError):
    """A requested path escapes the benchmark root or traverses with '..'."""


def benchmark_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _contains_parent_traversal(path: Path) -> bool:
    return ".." in path.parts


def resolve_benchmark_path(path: str | os.PathLike[str], *, create_parent: bool = False) -> Path:
    raw = Path(path)
    if _contains_parent_traversal(raw):
        raise PathSafetyError(f"path traversal is not allowed: {raw}")
    root = benchmark_root().resolve()
    candidate = raw if raw.is_absolute() else root / raw
    # strict=False resolves existing symlink/junction parents and normalizes missing leaves.
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        raise PathSafetyError(f"path must resolve inside benchmark root {root}: {resolved}") from None
    if create_parent:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        # Re-resolve after mkdir to catch a raced/replaced symlink parent.
        resolved = resolved.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError:
            raise PathSafetyError(f"path parent resolved outside benchmark root: {resolved}") from None
    return resolved


def cache_path(*parts: str, create_parent: bool = False) -> Path:
    return _scoped_path(".cache", parts, create_parent=create_parent)


def output_path(*parts: str, create_parent: bool = False) -> Path:
    return _scoped_path("results", parts, create_parent=create_parent)


def _scoped_path(directory: str, parts: tuple[str, ...], *, create_parent: bool) -> Path:
    root = benchmark_root().resolve()
    scope = root / directory
    if scope.resolve(strict=False) != scope:
        raise PathSafetyError(f"{directory} directory is redirected by a symlink/junction")
    requested = scope / Path(*parts)
    resolved = resolve_benchmark_path(requested)
    try:
        resolved.relative_to(scope)
    except ValueError:
        raise PathSafetyError(f"path must remain inside benchmark/{directory}: {resolved}") from None
    if create_parent:
        resolved = resolve_benchmark_path(resolved, create_parent=True)
        try:
            resolved.relative_to(scope)
        except ValueError:
            raise PathSafetyError(f"path parent escaped benchmark/{directory}: {resolved}") from None
    return resolved
