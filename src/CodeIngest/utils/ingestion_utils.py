"""Utility functions for the ingestion process."""

from fnmatch import fnmatch
from pathlib import Path
from typing import Set
import sys # Import sys for stderr

# Flag to print header only once
_exclude_debug_header_printed = False
_include_debug_header_printed = False # Add header for include debug

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
    global _include_debug_header_printed
    if not _include_debug_header_printed:
        print("\n--- [DEBUG INCLUDE START] ---", file=sys.stderr)
        _include_debug_header_printed = True

    try:
        rel_path = path.relative_to(base_path)
    except ValueError:
        # If path is not under base_path at all
        print(f"[DEBUG INCLUDE] Path '{path}' not relative to base '{base_path}'. Assuming not included.", file=sys.stderr)
        return False

    rel_str = str(rel_path)
    filename = path.name # Get just the filename

    print(f"[DEBUG INCLUDE] Checking: Path='{rel_str}', Filename='{filename}' against patterns: {include_patterns}", file=sys.stderr)

    # Check if the pattern matches the full relative path OR just the filename
    for pattern in include_patterns:
        if not pattern: # Skip empty patterns
            continue

        match_rel = fnmatch(rel_str, pattern)
        match_file = fnmatch(filename, pattern)

        if match_rel or match_file:
            print(f"[DEBUG INCLUDE] Match Found! Pattern='{pattern}' matched Path='{rel_str}' ({match_rel}) or Filename='{filename}' ({match_file}). Including.", file=sys.stderr)
            return True # Match found

    print(f"[DEBUG INCLUDE] No Match: Path='{rel_str}', Filename='{filename}' did not match any include patterns. Excluding.", file=sys.stderr)
    return False # No include pattern matched


def _should_exclude(path: Path, base_path: Path, ignore_patterns: Set[str]) -> bool:
    """
    Determine if the given file or directory path matches any of the ignore patterns.

    Checks if the pattern matches either the full relative path or just the filename.
    Includes debugging print statements.

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
    global _exclude_debug_header_printed
    if not _exclude_debug_header_printed:
        print("\n--- [DEBUG EXCLUDE START] ---", file=sys.stderr)
        _exclude_debug_header_printed = True

    try:
        rel_path = path.relative_to(base_path)
    except ValueError:
        print(f"[DEBUG EXCLUDE] Path '{path}' not relative to base '{base_path}'. Assuming not excluded.", file=sys.stderr)
        return False # Path outside base shouldn't be implicitly excluded here.

    rel_str = str(rel_path)
    filename = path.name # Get just the filename

    # --- Debug Print ---
    # Print check for every item
    print(f"[DEBUG EXCLUDE] Checking: Path='{rel_str}', Filename='{filename}' against patterns: {ignore_patterns}", file=sys.stderr)
    # --- End Debug Print ---

    # Check if the pattern matches the full relative path OR just the filename
    for pattern in ignore_patterns:
        # Ensure pattern is not empty before matching
        if not pattern:
            continue

        match_rel = fnmatch(rel_str, pattern)
        match_file = fnmatch(filename, pattern)

        if match_rel or match_file:
            # --- Debug Print ---
            print(f"[DEBUG EXCLUDE] Match Found! Pattern='{pattern}' matched Path='{rel_str}' ({match_rel}) or Filename='{filename}' ({match_file}). Excluding.", file=sys.stderr)
            # --- End Debug Print ---
            return True # Match found

    # --- Debug Print ---
    # If no pattern matched, print for the interesting paths
    print(f"[DEBUG EXCLUDE] No Match: Path='{rel_str}', Filename='{filename}'. Including.", file=sys.stderr)
    # --- End Debug Print ---

    return False # No ignore pattern matched
