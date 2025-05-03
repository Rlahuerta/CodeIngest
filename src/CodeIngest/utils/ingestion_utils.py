# src/CodeIngest/utils/ingestion_utils.py
"""Utility functions for the ingestion process."""

import pathspec
# --- Import the correct pattern class ---
from pathspec.patterns import GitWildMatchPattern
# --- End import ---

from pathlib import Path
from typing import Set, Optional
import sys
import os
import warnings


def _get_relative_path_string(path: Path, base_path: Path) -> Optional[str]:
    """Safely get the relative path string."""
    try:
        resolved_path = path.resolve()
        resolved_base = base_path.resolve()
        if resolved_path != resolved_base and resolved_base not in resolved_path.parents :
             return None
        rel_path = resolved_path.relative_to(resolved_base)
        return rel_path.as_posix()
    except ValueError:
        warnings.warn(f"Could not determine relative path for {path} against base {base_path}", UserWarning)
        return None
    except OSError as e:
        warnings.warn(f"OS error resolving path {path} or base {base_path}: {e}", UserWarning)
        return None


def _should_include(path: Path, base_path: Path, include_patterns: Optional[Set[str]]) -> bool:
    """
    Determine if the given file or directory path matches any of the include patterns
    using pathspec for .gitignore style matching.

    Parameters
    ----------
    path : Path
        The absolute path of the file or directory to check.
    base_path : Path
        The base directory from which the relative path is calculated.
    include_patterns : Set[str], optional
        A set of gitignore-style patterns to check against the relative path.
        If None, all paths are considered included.
        If an empty set {}, no paths are considered included.

    Returns
    -------
    bool
        `True` if the path matches any include patterns (or if patterns are None),
        `False` otherwise (including if patterns is an empty set {}).
    """
    if include_patterns is None:
        return True
    if not include_patterns: # Checks for empty set {}
        return False

    rel_path_str = _get_relative_path_string(path, base_path)

    # --- Use Correct Class Name ---
    spec = pathspec.PathSpec.from_lines(GitWildMatchPattern, list(include_patterns))
    # --- End Use ---

    if rel_path_str is None:
         # Match against filename only if relative path couldn't be determined
         return spec.match_file(path.name)

    # Match against the relative path
    return spec.match_file(rel_path_str)


def _should_exclude(path: Path, base_path: Path, ignore_patterns: Optional[Set[str]]) -> bool:
    """
    Determine if the given file or directory path matches any of the ignore patterns
    using pathspec for .gitignore style matching.

    Parameters
    ----------
    path : Path
        The absolute path of the file or directory to check.
    base_path : Path
        The base directory from which the relative path is calculated.
    ignore_patterns : Set[str], optional
        A set of gitignore-style patterns to check against the relative path.
        If None or empty, no paths are excluded.

    Returns
    -------
    bool
        `True` if the path matches any ignore patterns, `False` otherwise.
    """
    if not ignore_patterns:
        return False

    rel_path_str = _get_relative_path_string(path, base_path)

    # --- Use Correct Class Name ---
    spec = pathspec.PathSpec.from_lines(GitWildMatchPattern, list(ignore_patterns))
    # --- End Use ---

    if rel_path_str is None:
         # Match against filename only if relative path couldn't be determined
         return spec.match_file(path.name)

    # Match against the relative path
    return spec.match_file(rel_path_str)
