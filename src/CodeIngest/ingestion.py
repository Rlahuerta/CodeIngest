"""Functions to ingest and analyze a codebase directory or single file."""

import sys  # Import sys for stderr
import warnings
from pathlib import Path
from typing import Tuple

# Import config values
from CodeIngest.config import (
    MAX_DIRECTORY_DEPTH,
    MAX_FILES,
    MAX_TOTAL_SIZE_BYTES,
)

# Keep format_node
from CodeIngest.output_formatters import format_node
from CodeIngest.query_parsing import IngestionQuery

# Import the utility functions correctly
from CodeIngest.schemas import FileSystemNode, FileSystemNodeType, FileSystemStats
from CodeIngest.utils.ingestion_utils import _should_exclude, _should_include

try:
    import tomllib  # type: ignore[import]
except ImportError:
    # For Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]


def ingest_query(query: IngestionQuery) -> Tuple[str, str, str]:
    """
    Run the ingestion process for a parsed query.

    This is the main entry point for analyzing a codebase directory or single file. It processes the query
    parameters, reads the file or directory content (in chunks for large files), and generates a summary,
    directory structure, and file content, along with token estimations.

    Parameters
    ----------
    query : IngestionQuery
        The parsed query object containing information about the repository and query parameters.

    Returns
    -------
    Tuple[str, str, str]
        A tuple containing the summary, directory structure, and file contents.

    Raises
    ------
    ValueError
        If the path cannot be found, is not a file, or the file has no content.
    """
    # Determine the effective path to start ingestion from
    if query.local_path.is_file():
        path = query.local_path
        base_path_for_rel = query.local_path.parent
    elif query.subpath and query.subpath != "/":
        path = (query.local_path / query.subpath.strip("/")).resolve()
        base_path_for_rel = query.local_path
    else:
        path = query.local_path
        base_path_for_rel = query.local_path

    # Apply .gitingest file configuration from the effective path's directory
    apply_gitingest_file(path.parent if path.is_file() else path, query)

    if not path.exists():
        source_ref = query.url if query.url else query.slug
        raise ValueError(f"Target path for '{source_ref}' cannot be found: {path}")

    # Handle single file ingestion
    if path.is_file():
        # Check if the single file should be excluded/included
        if query.ignore_patterns and _should_exclude(path, base_path_for_rel, query.ignore_patterns):
            raise ValueError(f"File '{path.name}' is excluded by ignore patterns.")
        if query.include_patterns and not _should_include(path, base_path_for_rel, query.include_patterns):
            raise ValueError(f"File '{path.name}' does not match include patterns.")

        relative_path_str = str(path.relative_to(base_path_for_rel))

        file_node = FileSystemNode(
            name=path.name,
            type=FileSystemNodeType.FILE,
            size=path.stat().st_size,
            file_count=1,
            path_str=relative_path_str,
            path=path,
        )

        # --- FIX: Explicitly call format_node for single files ---
        return format_node(file_node, query)

    # Handle directory ingestion
    if path.is_dir():
        root_node = FileSystemNode(
            name=path.name,
            type=FileSystemNodeType.DIRECTORY,
            path_str=str(path.relative_to(base_path_for_rel)),
            path=path,
        )

        stats = FileSystemStats()

        _process_node(
            node=root_node,
            query=query,
            stats=stats,
            base_path_for_rel=base_path_for_rel,
        )

        return format_node(root_node, query)

    # If it's neither a file nor a directory
    raise ValueError(f"Path is neither a file nor a directory: {path}")


def apply_gitingest_file(path: Path, query: IngestionQuery) -> None:
    """
    Apply the .gitingest file to the query object.

    This function reads the .gitingest file in the specified path and updates the query object with the ignore
    patterns found in the file.

    Parameters
    ----------
    path : Path
        The path of the directory containing the potential .gitingest file.
    query : IngestionQuery
        The parsed query object containing information about the repository and query parameters.
        It should have an attribute `ignore_patterns` which is either None or a set of strings.
    """
    path_gitingest = path / ".gitingest"

    if not path_gitingest.is_file():
        return

    try:
        with path_gitingest.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        warnings.warn(f"Invalid TOML in {path_gitingest}: {exc}", UserWarning)
        return
    except OSError as exc:
        warnings.warn(f"Could not read {path_gitingest}: {exc}", UserWarning)
        return

    config_section = data.get("config", {})
    ignore_patterns = config_section.get("ignore_patterns")

    if not ignore_patterns:
        return

    if isinstance(ignore_patterns, str):
        ignore_patterns = [ignore_patterns]

    if not isinstance(ignore_patterns, (list, set)):
        warnings.warn(
            f"Expected a list/set for 'ignore_patterns', got {type(ignore_patterns)} in {path_gitingest}. Skipping.",
            UserWarning,
        )
        return

    valid_patterns = {pattern for pattern in ignore_patterns if isinstance(pattern, str)}
    invalid_patterns = set(ignore_patterns) - valid_patterns

    if invalid_patterns:
        warnings.warn(f"Ignoring non-string patterns {invalid_patterns} from {path_gitingest}.", UserWarning)

    if not valid_patterns:
        return

    if query.ignore_patterns is None:
        query.ignore_patterns = valid_patterns
    else:
        query.ignore_patterns.update(valid_patterns)


_process_node_debug_header_printed = False


def _process_node(
    node: FileSystemNode,
    query: IngestionQuery,
    stats: FileSystemStats,
    base_path_for_rel: Path,
) -> None:
    """
    Recursively process a directory item, applying include/exclude patterns.

    Parameters
    ----------
    node : FileSystemNode
        The current directory node being processed.
    query : IngestionQuery
        The parsed query object containing filtering patterns and limits.
    stats : FileSystemStats
        Statistics tracking object for the total file count and size.
    base_path_for_rel : Path
        The base path to calculate relative paths from (repo root or user-provided dir).
    """
    global _process_node_debug_header_printed
    if not _process_node_debug_header_printed:
        print("\n--- [DEBUG PROCESS_NODE START] ---", file=sys.stderr)
        _process_node_debug_header_printed = True

    if limit_exceeded(stats, node.depth):
        print(f"[DEBUG PROCESS_NODE] Depth limit exceeded for {node.path}. Skipping.", file=sys.stderr)
        return

    print(f"[DEBUG PROCESS_NODE] Processing directory: {node.path}", file=sys.stderr)
    print(f"[DEBUG PROCESS_NODE] Current depth: {node.depth}", file=sys.stderr)
    print(f"[DEBUG PROCESS_NODE] Include patterns: {query.include_patterns}", file=sys.stderr)
    print(f"[DEBUG PROCESS_NODE] Ignore patterns: {query.ignore_patterns}", file=sys.stderr)

    try:
        if not node.path.is_dir():
            warnings.warn(f"Attempted to iterate non-directory: {node.path}", UserWarning)
            print(f"[DEBUG PROCESS_NODE] Path is not a directory: {node.path}. Skipping.", file=sys.stderr)
            return
        iterator = list(node.path.iterdir())
        print(f"[DEBUG PROCESS_NODE] Found {len(iterator)} items in {node.path}", file=sys.stderr)
    except OSError as e:
        warnings.warn(f"Cannot access directory {node.path}: {e}", UserWarning)
        print(f"[DEBUG PROCESS_NODE] Cannot access directory {node.path}: {e}. Skipping.", file=sys.stderr)
        return

    for sub_path in iterator:
        print(f"[DEBUG PROCESS_NODE] Checking item: {sub_path}", file=sys.stderr)

        if query.ignore_patterns and _should_exclude(sub_path, base_path_for_rel, query.ignore_patterns):
            print(f"[DEBUG PROCESS_NODE] Item excluded by ignore patterns: {sub_path}. Skipping.", file=sys.stderr)
            continue

        if sub_path.is_symlink():
            print(f"[DEBUG PROCESS_NODE] Processing symlink: {sub_path}", file=sys.stderr)
            if query.include_patterns and not _should_include(sub_path, base_path_for_rel, query.include_patterns):
                print(f"[DEBUG PROCESS_NODE] Symlink excluded by include patterns: {sub_path}. Skipping.", file=sys.stderr)
                continue
            _process_symlink(path=sub_path, parent_node=node, stats=stats, local_path=base_path_for_rel)
            print(f"[DEBUG PROCESS_NODE] Symlink processed: {sub_path}", file=sys.stderr)

        elif sub_path.is_file():
            print(f"[DEBUG PROCESS_NODE] Processing file: {sub_path}", file=sys.stderr)
            if query.include_patterns and not _should_include(sub_path, base_path_for_rel, query.include_patterns):
                print(f"[DEBUG PROCESS_NODE] File excluded by include patterns: {sub_path}. Skipping.", file=sys.stderr)
                continue
            _process_file(
                path=sub_path,
                parent_node=node,
                stats=stats,
                local_path=base_path_for_rel,
                max_file_size=query.max_file_size,
            )
            print(f"[DEBUG PROCESS_NODE] File processed: {sub_path}", file=sys.stderr)

        elif sub_path.is_dir():
            print(f"[DEBUG PROCESS_NODE] Processing directory: {sub_path}", file=sys.stderr)
            child_directory_node = FileSystemNode(
                name=sub_path.name,
                type=FileSystemNodeType.DIRECTORY,
                path_str=str(sub_path.relative_to(base_path_for_rel)),
                path=sub_path,
                depth=node.depth + 1,
            )

            _process_node(
                node=child_directory_node,
                query=query,
                stats=stats,
                base_path_for_rel=base_path_for_rel,
            )

            if child_directory_node.children or child_directory_node.file_count > 0:
                node.children.append(child_directory_node)
                node.size += child_directory_node.size
                node.file_count += child_directory_node.file_count
                node.dir_count += 1 + child_directory_node.dir_count
                print(f"[DEBUG PROCESS_NODE] Added directory to parent: {sub_path}", file=sys.stderr)
            else:
                print(
                    f"[DEBUG PROCESS_NODE] Directory has no included content or skipped by limits: {sub_path}. Skipping adding to parent.",
                    file=sys.stderr,
                )
        else:
            warnings.warn(f"Skipping unknown file type: {sub_path}", UserWarning)
            print(f"[DEBUG PROCESS_NODE] Skipping unknown file type: {sub_path}", file=sys.stderr)

    node.sort_children()
    print(f"[DEBUG PROCESS_NODE] Finished processing directory: {node.path}", file=sys.stderr)


def _process_symlink(path: Path, parent_node: FileSystemNode, stats: FileSystemStats, local_path: Path) -> None:
    """
    Process a symlink node. Currently just adds it to the tree.

    Parameters
    ----------
    path : Path
        The full path of the symlink.
    parent_node : FileSystemNode
        The parent directory node.
    stats : FileSystemStats
        Statistics tracking object (symlinks currently don't add to size).
    local_path : Path
        The base path for calculating the relative path string.
    """
    print(f"[DEBUG PROCESS_SYMLINK] Processing symlink: {path}", file=sys.stderr)
    try:
        child = FileSystemNode(
            name=path.name,
            type=FileSystemNodeType.SYMLINK,
            path_str=str(path.relative_to(local_path)),
            path=path,
            depth=parent_node.depth + 1,
        )
        stats.total_files += 1
        parent_node.children.append(child)
        parent_node.file_count += 1
        print(f"[DEBUG PROCESS_SYMLINK] Added symlink node: {path.name}", file=sys.stderr)
    except Exception as e:
        warnings.warn(f"Failed to process symlink {path}: {e}", UserWarning)
        print(f"[DEBUG PROCESS_SYMLINK] Failed to process symlink {path}: {e}", file=sys.stderr)


def _process_file(path: Path, parent_node: FileSystemNode, stats: FileSystemStats, local_path: Path, max_file_size: int) -> None:
    """
    Process a file node, checking size limits and adding it to the parent.

    Parameters
    ----------
    path : Path
        The full path of the file.
    parent_node : FileSystemNode
        The parent directory node.
    stats : FileSystemStats
        Statistics tracking object for total file count and size.
    local_path : Path
        The base path for calculating the relative path string.
    max_file_size : int
        The maximum allowed size for individual files.
    """
    print(f"[DEBUG PROCESS_FILE] Processing file: {path}", file=sys.stderr)
    try:
        file_size = path.stat().st_size
        print(f"[DEBUG PROCESS_FILE] File size: {file_size}", file=sys.stderr)
    except OSError as e:
        warnings.warn(f"Could not stat file {path}: {e}", UserWarning)
        print(f"[DEBUG PROCESS_FILE] Could not stat file {path}: {e}. Skipping.", file=sys.stderr)
        return

    if file_size > max_file_size:
        warnings.warn(f"Skipping file {path.name} ({file_size} bytes): exceeds max file size ({max_file_size} bytes).", UserWarning)
        print(f"[DEBUG PROCESS_FILE] File exceeds max file size. Skipping: {path}", file=sys.stderr)
        return

    if stats.total_size + file_size > MAX_TOTAL_SIZE_BYTES:
        warnings.warn(f"Skipping file {path.name}: adding it would exceed total size limit.", UserWarning)
        print(f"[DEBUG PROCESS_FILE] Adding file would exceed total size limit. Skipping: {path}", file=sys.stderr)
        stats.total_files += 1
        if stats.total_files >= MAX_FILES:
            print(f"Maximum file limit ({MAX_FILES}) reached while checking size.")
            print(f"[DEBUG PROCESS_FILE] Maximum file limit ({MAX_FILES}) reached while checking size.", file=sys.stderr)
        return

    if stats.total_files >= MAX_FILES:
        if stats.total_files == MAX_FILES:
            print(f"Maximum file limit ({MAX_FILES}) reached. Skipping further files.")
            print(f"[DEBUG PROCESS_FILE] Maximum file limit ({MAX_FILES}) reached. Skipping further files.", file=sys.stderr)
        stats.total_files += 1
        print(f"[DEBUG PROCESS_FILE] Total file count limit reached. Skipping: {path}", file=sys.stderr)
        return

    stats.total_files += 1
    stats.total_size += file_size
    print(f"[DEBUG PROCESS_FILE] File passed limits. Total files: {stats.total_files}, Total size: {stats.total_size}", file=sys.stderr)

    child = FileSystemNode(
        name=path.name,
        type=FileSystemNodeType.FILE,
        size=file_size,
        file_count=1,
        path_str=str(path.relative_to(local_path)),
        path=path,
        depth=parent_node.depth + 1,
    )

    parent_node.children.append(child)
    parent_node.size += file_size
    parent_node.file_count += 1
    print(f"[DEBUG PROCESS_FILE] Added file node to parent: {path.name}", file=sys.stderr)


def limit_exceeded(stats: FileSystemStats, depth: int) -> bool:
    """
    Check if any traversal limits (depth, file count, total size) have been exceeded.

    Parameters
    ----------
    stats : FileSystemStats
        Statistics tracking object.
    depth : int
        The current depth of directory traversal.

    Returns
    -------
    bool
        True if any limit has been exceeded, False otherwise.
    """
    if depth > MAX_DIRECTORY_DEPTH:
        if depth == MAX_DIRECTORY_DEPTH + 1:
            print(f"Maximum depth limit ({MAX_DIRECTORY_DEPTH}) reached. Stopping recursion deeper.")
            print(f"[DEBUG LIMIT_EXCEEDED] Depth limit reached ({MAX_DIRECTORY_DEPTH}).", file=sys.stderr)
        return True

    if stats.total_files >= MAX_FILES:
        print(f"[DEBUG LIMIT_EXCEEDED] File count limit reached ({MAX_FILES}).", file=sys.stderr)
        return True

    if stats.total_size >= MAX_TOTAL_SIZE_BYTES:
        print(f"[DEBUG LIMIT_EXCEEDED] Total size limit reached ({MAX_TOTAL_SIZE_BYTES}).", file=sys.stderr)
        return True

    return False # No limits exceeded