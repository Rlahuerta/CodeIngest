"""Utility functions for the ingestion process."""

from fnmatch import fnmatch
from pathlib import Path
from typing import Set


def _should_include(path: Path, base_path: Path, include_patterns: Set[str]) -> bool:
    """
    Determine if the given file or directory path matches any of the include patterns.

    Checks if the pattern matches either the full relative path or just the filename.

    Parameters
    ----------
    path : Path
        The absolute path of the file or directory to check.
    base_path : Path
        The base directory from which the relative path is calculated.
    include_patterns : Set[str]
        A set of patterns to check against the relative path and filename.

    Returns
    -------
    bool
        `True` if the path or filename matches any include patterns, `False` otherwise.
    """
    try:
        rel_path = path.relative_to(base_path)
    except ValueError:
        # If path is not under base_path at all
        return False

    rel_str = str(rel_path)
    filename = path.name # Get just the filename

    # Check if the pattern matches the full relative path OR just the filename
    for pattern in include_patterns:
        if fnmatch(rel_str, pattern) or fnmatch(filename, pattern):
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
    try:
        rel_path = path.relative_to(base_path)
    except ValueError:
        # If path is not under base_path at all, treat as excluded for safety?
        # Or False? Let's stick to False - if it's outside, it won't be iterated anyway.
        return False # Changed from True - path outside base shouldn't be implicitly excluded here.

    rel_str = str(rel_path)
    filename = path.name # Get just the filename

    # Check if the pattern matches the full relative path OR just the filename
    for pattern in ignore_patterns:
        # Ensure pattern is not empty before matching
        if pattern and (fnmatch(rel_str, pattern) or fnmatch(filename, pattern)):
            return True # Match found
    return False # No ignore pattern matched
