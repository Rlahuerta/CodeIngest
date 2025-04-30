# src/CodeIngest/utils/ingestion_utils.py
"""Utility functions for the ingestion process."""

from fnmatch import fnmatch
from pathlib import Path
from typing import Set
import sys # Import sys for stderr
import os # Import os for scandir

def _should_include(path: Path, base_path: Path, include_patterns: Set[str]) -> bool:
    """
    Determine if the given file or directory path matches any of the include patterns.

    Checks if the pattern matches either the full relative path or just the filename.
    If the `include_patterns` set is empty, it returns False (as the intention
    is typically that *only* matching items are included when patterns are provided).

    Parameters
    ----------
    path : Path
        The absolute path of the file or directory to check.
    base_path : Path
        The base directory from which the relative path is calculated.
    include_patterns : Set[str]
        A set of patterns to check against the relative path and filename. If empty,
        no path will match.

    Returns
    -------
    bool
        `True` if the path or filename matches any include patterns, `False` otherwise.
"""
    # --- FIX: Return False if include_patterns is specifically an empty set ---
    # This means the user provided include patterns, but none matched this item.
    # Note: The calling function (_process_node) should handle the case where
    # query.include_patterns is None (meaning include everything not ignored).
    if not include_patterns: # Handles None and empty set cases implicitly if called directly
         # However, the test specifically passes an empty set, expecting False.
         # If the intent is "if include patterns are specified, only matching items pass",
         # then an empty set means nothing matches.
         return False
    # --- End FIX ---

    try:
        # Ensure paths are resolved for accurate comparison
        rel_path = path.resolve().relative_to(base_path.resolve())
    except ValueError:
        # Path not relative to base. Check filename only.
        filename = path.name
        for pattern in include_patterns:
            if fnmatch(filename, pattern):
                 return True
        return False


    rel_str = str(rel_path)
    filename = path.name # Get just the filename

    # Check if the pattern matches the full relative path OR just the filename
    for pattern in include_patterns:
        if not pattern: # Skip empty patterns
            continue

        # Normalize pattern: remove trailing slash for directory matching if present
        normalized_pattern = pattern.rstrip('/')

        # Check 1: Exact match on relative path string
        match_rel = fnmatch(rel_str, pattern)

        # Check 2: Match on filename only
        match_file = fnmatch(filename, pattern)

        # Check 3: Directory match - does pattern match the directory name itself?
        is_dir = getattr(path, '_is_dir', None)
        if is_dir is None: is_dir = path.is_dir()
        match_dir_name = is_dir and fnmatch(filename, normalized_pattern)

        # Check 4: Directory content match - pattern like "dir/*" and path is "dir"
        match_dir_contents = is_dir and pattern.endswith('/*') and fnmatch(rel_str, pattern[:-2])

        if match_rel or match_file or match_dir_name or match_dir_contents:
            return True # Match found

    return False # No include pattern matched


def _should_exclude(path: Path, base_path: Path, ignore_patterns: Set[str]) -> bool:
    """
    Determine if the given file or directory path matches any of the ignore patterns.

    Checks if the pattern matches either the full relative path or just the filename.

    Parameters
    ----------
    path : Path
        The absolute path of the file or directory to check.
    base_path : Path
        The base directory from which the relative path is calculated.
    ignore_patterns : Set[str]
        A set of patterns to check against the relative path and filename.

    Returns
    -------
    bool
        `True` if the path or filename matches any ignore patterns, `False` otherwise.
"""
    if not ignore_patterns:
        return False

    try:
        # Ensure paths are resolved for accurate comparison
        rel_path = path.resolve().relative_to(base_path.resolve())
    except ValueError:
        # Path not relative to base. Check filename only.
        filename = path.name
        for pattern in ignore_patterns:
            if fnmatch(filename, pattern):
                return True
        return False # Path outside base and filename doesn't match exclude patterns.

    rel_str = str(rel_path)
    filename = path.name # Get just the filename

    # Check if the pattern matches the full relative path OR just the filename
    for pattern in ignore_patterns:
        # Ensure pattern is not empty before matching
        if not pattern:
            continue

        # Normalize pattern: remove trailing slash if present for directory matching
        normalized_pattern = pattern.rstrip('/')

        # Check 1: Match relative path string (e.g., "src/utils.py" matches "src/utils.py")
        match_rel = fnmatch(rel_str, pattern)

        # Check 2: Match filename only (e.g., "utils.py" matches "*.py")
        match_file = fnmatch(filename, pattern)

        # Check 3: Directory match - pattern matches directory name itself (e.g., "src" matches "src")
        is_dir = getattr(path, '_is_dir', None)
        if is_dir is None: is_dir = path.is_dir()
        match_dir_name = is_dir and fnmatch(filename, normalized_pattern)

        if match_rel or match_file or match_dir_name:
            return True # Match found

    return False # No ignore pattern matched
