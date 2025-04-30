"""Functions to ingest and analyze a codebase directory or single file."""

import sys
import warnings
import os
from pathlib import Path
from typing import Tuple

from CodeIngest.config import (
    MAX_DIRECTORY_DEPTH,
    MAX_FILES,
    MAX_TOTAL_SIZE_BYTES,
)
from CodeIngest.output_formatters import format_node
from CodeIngest.query_parsing import IngestionQuery
from CodeIngest.schemas import FileSystemNode, FileSystemNodeType, FileSystemStats
from CodeIngest.utils.ingestion_utils import _should_exclude, _should_include

try:
    import tomllib
except ImportError:
    import tomli as tomllib


def ingest_query(query: IngestionQuery) -> Tuple[str, str, str]:
    """Run the ingestion process for a parsed query."""
    path = query.local_path
    base_path_for_rel = query.local_path

    apply_gitingest_file(path, query)

    if not path.exists():
        source_ref = query.url or (str(query.original_zip_path) if query.original_zip_path else query.slug)
        raise ValueError(f"Target path for '{source_ref}' cannot be found or accessed: {path}")

    if path.is_file():
        if query.ignore_patterns and _should_exclude(path, path.parent, query.ignore_patterns):
            raise ValueError(f"File '{path.name}' is excluded by ignore patterns.")
        if query.include_patterns is not None and not _should_include(path, path.parent, query.include_patterns):
            raise ValueError(f"File '{path.name}' does not match include patterns.")

        file_node = FileSystemNode(
            name=path.name, type=FileSystemNodeType.FILE, size=path.stat().st_size,
            file_count=1, path_str=path.name, path=path,
        )
        return format_node(file_node, query)

    elif path.is_dir():
        root_node = FileSystemNode(name=path.name, type=FileSystemNodeType.DIRECTORY, path_str=".", path=path)
        stats = FileSystemStats()
        _process_node(node=root_node, query=query, stats=stats, base_path_for_rel=base_path_for_rel)

        if not root_node.children and root_node.file_count == 0:
             warnings.warn(f"Directory '{path.name}' is empty or fully excluded.", UserWarning)
             summary = format_node(root_node, query)[0]
             summary += "Files analyzed: 0\n\nEstimated tokens: 0"
             return summary, "Directory structure:\n(empty or excluded)", ""

        return format_node(root_node, query)

    raise ValueError(f"Path is neither a file nor a directory: {path}")


def apply_gitingest_file(path: Path, query: IngestionQuery) -> None:
    """Apply .gitingest file configuration."""
    if not path.is_dir(): return
    path_gitingest = path / ".gitingest"
    if not path_gitingest.is_file(): return

    try:
        with path_gitingest.open("rb") as f: data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        warnings.warn(f"Error reading {path_gitingest}: {exc}", UserWarning); return

    ignore_patterns = data.get("config", {}).get("ignore_patterns")
    if not ignore_patterns: return

    if isinstance(ignore_patterns, str): ignore_patterns = [ignore_patterns]
    if not isinstance(ignore_patterns, (list, set)):
        warnings.warn(f"Invalid 'ignore_patterns' type in {path_gitingest}. Expected list or set.", UserWarning); return

    valid_patterns = {p for p in ignore_patterns if isinstance(p, str)}
    if invalid := set(ignore_patterns) - valid_patterns:
        warnings.warn(f"Ignoring non-string patterns in {path_gitingest}: {invalid}", UserWarning)
    if not valid_patterns: return

    if query.ignore_patterns is None: query.ignore_patterns = set()
    query.ignore_patterns.update(valid_patterns)


def _process_node(
    node: FileSystemNode, query: IngestionQuery, stats: FileSystemStats, base_path_for_rel: Path
) -> None:
    """Recursively process directory items."""
    if limit_exceeded(stats, node.depth): return

    try:
        if not node.path.is_dir():
            warnings.warn(f"Attempted non-dir process: {node.path}", UserWarning); return
        iterator = list(node.path.iterdir())
    except OSError as e: warnings.warn(f"Cannot access directory contents {node.path}: {e}", UserWarning); return

    for sub_path in iterator: # item is now always a Path object

        if query.ignore_patterns and _should_exclude(sub_path, base_path_for_rel, query.ignore_patterns):
            continue

        try:
             is_dir = sub_path.is_dir()
             is_file = sub_path.is_file()
             is_symlink = sub_path.is_symlink()
        except OSError as e:
             warnings.warn(f"Error checking type of item {sub_path}: {e}", UserWarning); continue

        # --- Inclusion Check ---
        item_matches_include = True # Assume included unless include patterns say otherwise
        if query.include_patterns is not None:
            item_matches_include = _should_include(sub_path, base_path_for_rel, query.include_patterns)
            # If include patterns exist, skip files/symlinks that don't match
            if not is_dir and not item_matches_include:
                continue

        # --- Process based on type ---
        try:
            if is_symlink and item_matches_include:
                 _process_symlink(path=sub_path, parent_node=node, stats=stats, local_path=base_path_for_rel)
            elif is_file and item_matches_include:
                 _process_file(
                     path=sub_path, parent_node=node, stats=stats,
                     local_path=base_path_for_rel, max_file_size=query.max_file_size
                 )
            elif is_dir:
                child_node = FileSystemNode(
                    name=sub_path.name, type=FileSystemNodeType.DIRECTORY,
                    path_str=str(sub_path.relative_to(base_path_for_rel)),
                    path=sub_path, depth=node.depth + 1
                )
                # --- FIX: Always recurse if not excluded ---
                _process_node(node=child_node, query=query, stats=stats, base_path_for_rel=base_path_for_rel)

                # --- FIX: Add child directory node if it contains any processed children ---
                # This ensures directories (like .hiddendir) are added if files inside match includes.
                if child_node.children or child_node.file_count > 0:
                    node.children.append(child_node)
                    # Aggregate stats from the child node that has content
                    node.size += child_node.size
                    node.file_count += child_node.file_count
                    node.dir_count += 1 + child_node.dir_count
                # --- End FIX ---

        except OSError as e:
             warnings.warn(f"Error processing item {sub_path}: {e}", UserWarning); continue

    node.sort_children()


# _process_symlink, _process_file, limit_exceeded remain the same
def _process_symlink(path: Path, parent_node: FileSystemNode, stats: FileSystemStats, local_path: Path) -> None:
    """Process a symlink node."""
    try:
        relative_path_str = str(path.relative_to(local_path))
        child = FileSystemNode(
            name=path.name, type=FileSystemNodeType.SYMLINK, path_str=relative_path_str,
            path=path, depth=parent_node.depth + 1, size=0, file_count=0
        )
        stats.total_files += 1
        parent_node.children.append(child)
        parent_node.file_count += 1
    except Exception as e: warnings.warn(f"Failed to process symlink {path}: {e}", UserWarning)

def _process_file(path: Path, parent_node: FileSystemNode, stats: FileSystemStats, local_path: Path, max_file_size: int) -> None:
    """Process a file node, checking limits."""
    try: file_size = path.stat().st_size
    except OSError as e: warnings.warn(f"Could not stat file {path}: {e}", UserWarning); return

    if file_size > max_file_size:
        warnings.warn(f"Skipping file {path.name} ({file_size} bytes): exceeds max file size ({max_file_size} bytes).", UserWarning); return

    if stats.total_size + file_size > MAX_TOTAL_SIZE_BYTES:
        if not stats.total_size_limit_reached:
            warnings.warn(f"Total size limit ({MAX_TOTAL_SIZE_BYTES / (1024*1024):.1f} MB) reached.", UserWarning)
            stats.total_size_limit_reached = True
        return

    if stats.total_files >= MAX_FILES:
        if not stats.total_file_limit_reached:
            warnings.warn(f"Maximum file limit ({MAX_FILES}) reached.", UserWarning)
            stats.total_file_limit_reached = True
        return

    stats.total_files += 1
    stats.total_size += file_size

    child = FileSystemNode(
        name=path.name, type=FileSystemNodeType.FILE, size=file_size, file_count=1,
        path_str=str(path.relative_to(local_path)), path=path, depth=parent_node.depth + 1
    )
    parent_node.children.append(child)
    parent_node.size += file_size
    parent_node.file_count += 1

def limit_exceeded(stats: FileSystemStats, depth: int) -> bool:
    """Check if traversal limits have been exceeded."""
    if depth > MAX_DIRECTORY_DEPTH:
        if not stats.depth_limit_reached:
            warnings.warn(f"Max directory depth ({MAX_DIRECTORY_DEPTH}) reached.", UserWarning)
            stats.depth_limit_reached = True
        return True
    return stats.total_file_limit_reached or stats.total_size_limit_reached
