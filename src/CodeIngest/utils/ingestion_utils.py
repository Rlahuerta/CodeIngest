# src/CodeIngest/utils/ingestion_utils.py
"""Utility functions for the ingestion process."""

# --- Import pathspec ---
import pathspec
# --- End import ---

from pathlib import Path
from typing import Set, Optional # Import Optional
import sys # Import sys for stderr
import os # Import os for scandir
import warnings # Import warnings


def _get_relative_path_string(path: Path, base_path: Path) -> Optional[str]:
    """Safely get the relative path string."""
    try:
        # Ensure paths are resolved for accurate comparison
        resolved_path = path.resolve()
        resolved_base = base_path.resolve()
        # Check if the path is actually within the base path
        # Allow path == base_path for the root directory itself
        if resolved_path != resolved_base and resolved_base not in resolved_path.parents :
             # If path is not within base_path (e.g., symlink pointing outside),
             # we cannot get a meaningful relative path for gitignore-style matching.
             # Return None to indicate this.
             return None
        rel_path = resolved_path.relative_to(resolved_base)
        # Use forward slashes for consistency, as pathspec expects Unix-style paths
        return rel_path.as_posix()
    except ValueError:
        # This might happen if paths are on different drives (Windows) or other issues
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
    # --- Updated Logic ---
    if include_patterns is None:
        # If None, default is to include everything (exclusion logic will handle ignores)
        return True
    if not include_patterns: # Checks for empty set {}
        # If an empty set is explicitly passed, nothing should be included
        return False
    # --- End Updated Logic ---

    rel_path_str = _get_relative_path_string(path, base_path)

    # If we couldn't get a relative path (e.g., outside base), it cannot match patterns anchored to base
    if rel_path_str is None:
         # Check if any pattern matches the filename directly (non-anchored patterns)
         # Use GitIgnorePattern for standard .gitignore syntax
         spec = pathspec.PathSpec.from_lines(pathspec.GitIgnorePattern, list(include_patterns))
         # pathspec matches against the full path string representation
         return spec.match_file(path.name)


    # Create a PathSpec object from the include patterns
    # Use GitIgnorePattern for standard .gitignore syntax
    spec = pathspec.PathSpec.from_lines(pathspec.GitIgnorePattern, list(include_patterns))

    # Check if the relative path matches any pattern in the spec
    # pathspec expects paths relative to the root where the patterns apply (base_path here)
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
    # If no ignore patterns are specified, nothing is excluded
    if not ignore_patterns:
        return False

    rel_path_str = _get_relative_path_string(path, base_path)

    # If we couldn't get a relative path (e.g., outside base), check filename only
    if rel_path_str is None:
         # Use GitIgnorePattern for standard .gitignore syntax
         spec = pathspec.PathSpec.from_lines(pathspec.GitIgnorePattern, list(ignore_patterns))
         # Match against filename for non-anchored patterns
         return spec.match_file(path.name)


    # Create a PathSpec object from the ignore patterns
    # Use GitIgnorePattern for standard .gitignore syntax
    spec = pathspec.PathSpec.from_lines(pathspec.GitIgnorePattern, list(ignore_patterns))

    # Check if the relative path matches any pattern in the spec
    return spec.match_file(rel_path_str)
