# src/CodeIngest/utils/ingestion_utils.py
"""Utility functions for the ingestion process."""

from fnmatch import fnmatch
from pathlib import Path
from typing import Set
import sys # Import sys for stderr
import os # Import os for path normalization

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
    # (DEBUG PRINTING can remain or be removed)
    # if not _include_debug_header_printed: print("\n--- [DEBUG INCLUDE START] ---", file=sys.stderr); _include_debug_header_printed = True

    try:
        # Ensure paths are resolved for accurate comparison
        rel_path = path.resolve().relative_to(base_path.resolve())
    except ValueError:
        # print(f"[DEBUG INCLUDE] Path '{path}' not relative to base '{base_path}'. Assuming not included.", file=sys.stderr)
        return False # Path outside base cannot match relative patterns

    # Convert to string using POSIX separator for consistent matching
    rel_str = rel_path.as_posix()
    filename = path.name # Get just the filename

    # print(f"[DEBUG INCLUDE] Checking: RelPath='{rel_str}', Filename='{filename}' against patterns: {include_patterns}", file=sys.stderr)

    # Check if the pattern matches the full relative path OR just the filename
    for pattern in include_patterns:
        if not pattern: continue

        # Normalize pattern slashes just in case
        normalized_pattern = pattern.replace(os.sep, '/')

        # Match against relative path (POSIX style)
        match_rel = fnmatch(rel_str, normalized_pattern)

        # Match against filename only
        match_file = fnmatch(filename, normalized_pattern)

        # Match against directory components (if pattern looks like a directory)
        match_dir = False
        if "/" in normalized_pattern or "*" not in normalized_pattern: # Heuristic for dir pattern
             # Check if relative path starts with the pattern (as directory)
             if rel_str.startswith(normalized_pattern.rstrip('/') + '/'):
                 match_dir = True


        if match_rel or match_file or match_dir:
            # print(f"[DEBUG INCLUDE] Match Found! Pattern='{normalized_pattern}' matched RelPath='{rel_str}' ({match_rel}) or Filename='{filename}' ({match_file}) or Dir ({match_dir}). Including.", file=sys.stderr)
            return True # Match found

    # print(f"[DEBUG INCLUDE] No Match: RelPath='{rel_str}', Filename='{filename}' did not match any include patterns. Excluding.", file=sys.stderr)
    return False # No include pattern matched


def _should_exclude(path: Path, base_path: Path, ignore_patterns: Set[str]) -> bool:
    """
    Determine if the given file or directory path matches any of the ignore patterns.

    Checks against relative path, filename, and parent directory components.

    Parameters
    ----------
    path : Path
        The absolute path of the file or directory to check.
    base_path : Path
        The base directory from which the relative path is calculated.
    ignore_patterns : Set[str]
        A set of patterns to check.

    Returns
    -------
    bool
        `True` if the path should be excluded, `False` otherwise.
    """
    global _exclude_debug_header_printed
    # (DEBUG PRINTING can remain or be removed)
    # if not _exclude_debug_header_printed: print("\n--- [DEBUG EXCLUDE START] ---", file=sys.stderr); _exclude_debug_header_printed = True

    try:
        # Ensure paths are resolved
        resolved_path = path.resolve()
        resolved_base = base_path.resolve()
        rel_path = resolved_path.relative_to(resolved_base)
    except ValueError:
        # print(f"[DEBUG EXCLUDE] Path '{path}' not relative to base '{base_path}'. Assuming not excluded.", file=sys.stderr)
        return False # Path outside base shouldn't be implicitly excluded.

    # Use POSIX style for matching consistency
    rel_str = rel_path.as_posix()
    filename = path.name

    # print(f"[DEBUG EXCLUDE] Checking: RelPath='{rel_str}', Filename='{filename}' against patterns: {ignore_patterns}", file=sys.stderr)

    for pattern in ignore_patterns:
        if not pattern: continue

        # Normalize pattern slashes
        normalized_pattern = pattern.replace(os.sep, '/')

        # 1. Match full relative path
        if fnmatch(rel_str, normalized_pattern):
            # print(f"[DEBUG EXCLUDE] Match RelPath: '{rel_str}' matches '{normalized_pattern}'. Excluding.", file=sys.stderr)
            return True

        # 2. Match filename only
        if fnmatch(filename, normalized_pattern):
            # print(f"[DEBUG EXCLUDE] Match Filename: '{filename}' matches '{normalized_pattern}'. Excluding.", file=sys.stderr)
            return True

        # 3. Match if item is *inside* an explicitly ignored directory pattern
        #    (e.g., pattern is "node_modules/" or "build")
        #    This requires checking parent components.
        if "/" in normalized_pattern.rstrip('/'): # Pattern likely specifies a path segment
             # Check if the relative path starts with the pattern
             if rel_str.startswith(normalized_pattern.rstrip('/') + '/'):
                 # print(f"[DEBUG EXCLUDE] Match Dir Prefix: '{rel_str}' starts with '{normalized_pattern}'. Excluding.", file=sys.stderr)
                 return True
        # Check simple directory name patterns against parent parts
        elif "*" not in normalized_pattern and "?" not in normalized_pattern:
             dir_pattern_name = normalized_pattern.rstrip('/')
             for parent_part in rel_path.parent.parts:
                 if parent_part == dir_pattern_name:
                     # print(f"[DEBUG EXCLUDE] Match Parent Dir: '{parent_part}' matches dir pattern '{dir_pattern_name}'. Excluding.", file=sys.stderr)
                     return True


    # print(f"[DEBUG EXCLUDE] No Match: Path='{rel_str}', Filename='{filename}'. Including.", file=sys.stderr)
    return False # No ignore pattern matched