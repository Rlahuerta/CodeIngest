"""Functions to ingest and analyze a codebase directory or single file."""

import warnings
from pathlib import Path
from typing import Tuple
import sys # Import sys for stderr

from CodeIngest.config import MAX_DIRECTORY_DEPTH, MAX_FILES, MAX_TOTAL_SIZE_BYTES
from CodeIngest.output_formatters import format_node
from CodeIngest.query_parsing import IngestionQuery
from CodeIngest.schemas import FileSystemNode, FileSystemNodeType, FileSystemStats
# Import the utility functions correctly
from CodeIngest.utils.ingestion_utils import _should_exclude, _should_include

try:
    import tomllib  # type: ignore[import]
except ImportError:
    # For Python < 3.11
    import tomli as tomllib # type: ignore[no-redef]


def ingest_query(query: IngestionQuery) -> Tuple[str, str, str]:
    """
    Run the ingestion process for a parsed query.

    This is the main entry point for analyzing a codebase directory or single file. It processes the query
    parameters, reads the file or directory content, and generates a summary, directory structure, and file content,
    along with token estimations.

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
    # Ensure subpath is handled correctly, potentially relative to local_path
    # If local_path is already the target file/dir, subpath might just be '/'
    # If local_path is the repo root, subpath specifies the target within it.

    # Determine the effective path to start ingestion from
    if query.local_path.is_file():
         # If the resolved local path is already a file, use it directly
         path = query.local_path
         # Adjust base path for relative calculations if needed (might be the parent dir)
         base_path_for_rel = query.local_path.parent
    elif query.subpath and query.subpath != "/":
         # If there's a subpath, resolve it relative to the local_path (which should be a dir)
         path = (query.local_path / query.subpath.strip("/")).resolve()
         base_path_for_rel = query.local_path # Use the original local_path for relative paths
    else:
         # Otherwise, start from the local_path itself (likely a directory)
         path = query.local_path
         base_path_for_rel = query.local_path

    # Apply .gitingest file configuration from the effective path's directory
    apply_gitingest_file(path.parent if path.is_file() else path, query)

    if not path.exists():
        # Use the original slug/source for the error message for clarity
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

        # Check content after creating node
        try:
            _ = file_node.content # Access content to trigger read
        except Exception as e:
             raise ValueError(f"Could not read content of file {file_node.name}: {e}") from e

        if not file_node.content or file_node.content == "[Non-text file]" or "Error reading file" in file_node.content or "Unable to decode" in file_node.content:
             # Optionally, decide if you want to raise error or just return empty content for non-text/unreadable
              warnings.warn(f"File {file_node.name} has no readable text content.", UserWarning)
             # return format_node(file_node, query) # Return with warning message in content
             # OR raise error:
             # raise ValueError(f"File {file_node.name} has no readable text content.")


        return format_node(file_node, query)

    # Handle directory ingestion
    if path.is_dir():
        root_node = FileSystemNode(
            name=path.name, # Use the target directory name
            type=FileSystemNodeType.DIRECTORY,
            # Path string relative to the original base path (repo root or user-provided dir)
            path_str=str(path.relative_to(base_path_for_rel)),
            path=path,
        )

        stats = FileSystemStats()

        _process_node(
            node=root_node,
            query=query,
            stats=stats,
            # Pass the base path used for relative calculations
            base_path_for_rel=base_path_for_rel
        )

        return format_node(root_node, query)

    # If it's neither a file nor a directory (shouldn't happen with initial check)
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
    # Look for .gitingest in the provided directory path
    path_gitingest = path / ".gitingest" # Corrected filename

    if not path_gitingest.is_file():
        return # No config file found

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
        return # No ignore_patterns defined in the file

    # Ensure it's a list or set
    if isinstance(ignore_patterns, str):
        ignore_patterns = [ignore_patterns] # Convert single string to list

    if not isinstance(ignore_patterns, (list, set)):
        warnings.warn(
            f"Expected a list/set for 'ignore_patterns', got {type(ignore_patterns)} in {path_gitingest}. Skipping.",
            UserWarning,
        )
        return

    # Filter out non-string entries and duplicates
    valid_patterns = {pattern for pattern in ignore_patterns if isinstance(pattern, str)}
    invalid_patterns = set(ignore_patterns) - valid_patterns

    if invalid_patterns:
        warnings.warn(f"Ignoring non-string patterns {invalid_patterns} from {path_gitingest}.", UserWarning)

    if not valid_patterns:
        return # No valid patterns found

    # Merge with existing patterns
    if query.ignore_patterns is None:
        query.ignore_patterns = valid_patterns
    else:
        # Add the patterns from the file to the existing set
        query.ignore_patterns.update(valid_patterns)

    return


# Flag to print header only once for _process_node debug
_process_node_debug_header_printed = False

def _process_node(
    node: FileSystemNode,
    query: IngestionQuery,
    stats: FileSystemStats,
    base_path_for_rel: Path, # Added base path for consistent relative path calculations
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

    print(f"[DEBUG PROCESS_NODE] Processing directory: {node.path}", file=sys.stderr)
    print(f"[DEBUG PROCESS_NODE] Current depth: {node.depth}", file=sys.stderr)
    print(f"[DEBUG PROCESS_NODE] Include patterns: {query.include_patterns}", file=sys.stderr)
    print(f"[DEBUG PROCESS_NODE] Ignore patterns: {query.ignore_patterns}", file=sys.stderr)


    if limit_exceeded(stats, node.depth):
        print(f"[DEBUG PROCESS_NODE] Limit exceeded for {node.path}. Skipping.", file=sys.stderr)
        return

    # Iterate through items in the current directory node's path
    try:
        # Check if path exists and is a directory before iterating
        if not node.path.is_dir():
             warnings.warn(f"Attempted to iterate non-directory: {node.path}", UserWarning)
             print(f"[DEBUG PROCESS_NODE] Path is not a directory: {node.path}. Skipping.", file=sys.stderr)
             return
        iterator = list(node.path.iterdir()) # Convert to list to iterate multiple times if needed for debug
        print(f"[DEBUG PROCESS_NODE] Found {len(iterator)} items in {node.path}", file=sys.stderr)
    except OSError as e:
         warnings.warn(f"Cannot access directory {node.path}: {e}", UserWarning)
         print(f"[DEBUG PROCESS_NODE] Cannot access directory {node.path}: {e}. Skipping.", file=sys.stderr)
         return # Skip this directory if not accessible

    for sub_path in iterator:
        print(f"[DEBUG PROCESS_NODE] Checking item: {sub_path}", file=sys.stderr)

        # --- Exclusion Check (Applied first to both files and dirs) ---
        # Use the consistent base_path_for_rel for checking patterns
        if query.ignore_patterns and _should_exclude(sub_path, base_path_for_rel, query.ignore_patterns):
            print(f"[DEBUG PROCESS_NODE] Item excluded by ignore patterns: {sub_path}. Skipping.", file=sys.stderr)
            continue # Skip if excluded by ignore patterns

        # --- Process based on type ---
        if sub_path.is_symlink():
            print(f"[DEBUG PROCESS_NODE] Processing symlink: {sub_path}", file=sys.stderr)
            # Apply include check only if include patterns exist
            if query.include_patterns and not _should_include(sub_path, base_path_for_rel, query.include_patterns):
                print(f"[DEBUG PROCESS_NODE] Symlink excluded by include patterns: {sub_path}. Skipping.", file=sys.stderr)
                continue # Skip symlink if include patterns exist and it doesn't match
            _process_symlink(path=sub_path, parent_node=node, stats=stats, local_path=base_path_for_rel)
            print(f"[DEBUG PROCESS_NODE] Symlink processed: {sub_path}", file=sys.stderr)


        elif sub_path.is_file():
            print(f"[DEBUG PROCESS_NODE] Processing file: {sub_path}", file=sys.stderr)
            # Apply include check only if include patterns exist
            if query.include_patterns and not _should_include(sub_path, base_path_for_rel, query.include_patterns):
                print(f"[DEBUG PROCESS_NODE] File excluded by include patterns: {sub_path}. Skipping.", file=sys.stderr)
                continue # Skip file if include patterns exist and it doesn't match

            # If not skipped by exclude or include, process the file
            _process_file(path=sub_path, parent_node=node, stats=stats, local_path=base_path_for_rel, max_file_size=query.max_file_size)
            print(f"[DEBUG PROCESS_NODE] File processed: {sub_path}", file=sys.stderr)


        elif sub_path.is_dir():
            print(f"[DEBUG PROCESS_NODE] Processing directory: {sub_path}", file=sys.stderr)
            # No include check here for the directory itself.
            # We always recurse into non-excluded directories.
            # The include check will happen for files *within* this directory during recursion.
            child_directory_node = FileSystemNode(
                name=sub_path.name,
                type=FileSystemNodeType.DIRECTORY,
                # Calculate relative path string based on the consistent base
                path_str=str(sub_path.relative_to(base_path_for_rel)),
                path=sub_path,
                depth=node.depth + 1,
            )

            # Recurse into the subdirectory
            _process_node(
                node=child_directory_node,
                query=query,
                stats=stats,
                base_path_for_rel=base_path_for_rel # Pass base path down
            )

            # Add child directory to parent's children *only if* it contains included content
            if child_directory_node.children or child_directory_node.file_count > 0:
                node.children.append(child_directory_node)
                # Aggregate stats from the child directory that had content
                node.size += child_directory_node.size
                node.file_count += child_directory_node.file_count
                node.dir_count += 1 + child_directory_node.dir_count
                print(f"[DEBUG PROCESS_NODE] Added directory to parent: {sub_path}", file=sys.stderr)
            else:
                 print(f"[DEBUG PROCESS_NODE] Directory has no included content: {sub_path}. Skipping adding to parent.", file=sys.stderr)
        else:
            # Handle other potential file types if necessary, or warn
            warnings.warn(f"Skipping unknown file type: {sub_path}", UserWarning)
            print(f"[DEBUG PROCESS_NODE] Skipping unknown file type: {sub_path}", file=sys.stderr)


    # Sort children after processing all items in the current directory
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
    # Basic symlink handling: add to tree, count as a file-like entry for limits.
    # Does not follow the link or add its target's size/content by default.
    try:
        child = FileSystemNode(
            name=path.name,
            type=FileSystemNodeType.SYMLINK,
            path_str=str(path.relative_to(local_path)),
            path=path,
            depth=parent_node.depth + 1,
        )
        # Increment file count for limit checking, but not size
        stats.total_files += 1
        parent_node.children.append(child)
        parent_node.file_count += 1 # Count symlink as a file entry in the parent
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
        return # Skip file if cannot get stats

    # Check individual file size limit
    if file_size > max_file_size:
         warnings.warn(f"Skipping file {path.name} ({file_size} bytes): exceeds max file size ({max_file_size} bytes).", UserWarning)
         print(f"[DEBUG PROCESS_FILE] File exceeds max file size. Skipping: {path}", file=sys.stderr)
         return

    # Check overall total size limit
    if stats.total_size + file_size > MAX_TOTAL_SIZE_BYTES:
        warnings.warn(f"Skipping file {path.name}: adding it would exceed total size limit.", UserWarning)
        print(f"[DEBUG PROCESS_FILE] Adding file would exceed total size limit. Skipping: {path}", file=sys.stderr)
        stats.total_files += 1 # Increment count even if skipped due to size for limit tracking
        if stats.total_files >= MAX_FILES:
             print(f"Maximum file limit ({MAX_FILES}) reached while checking size.")
             print(f"[DEBUG PROCESS_FILE] Maximum file limit ({MAX_FILES}) reached while checking size.", file=sys.stderr)
        return # Stop processing this file

    # Check total file count limit
    if stats.total_files >= MAX_FILES:
        # Check if we already printed the warning
        if stats.total_files == MAX_FILES: # Print only once when limit is first hit
             print(f"Maximum file limit ({MAX_FILES}) reached. Skipping further files.")
             print(f"[DEBUG PROCESS_FILE] Maximum file limit ({MAX_FILES}) reached. Skipping further files.", file=sys.stderr)
        stats.total_files += 1 # Increment anyway to know how many were skipped
        print(f"[DEBUG PROCESS_FILE] Total file count limit reached. Skipping: {path}", file=sys.stderr)
        return # Stop processing this file

    # --- If limits are okay, process the file ---
    stats.total_files += 1
    stats.total_size += file_size
    print(f"[DEBUG PROCESS_FILE] File passed limits. Total files: {stats.total_files}, Total size: {stats.total_size}", file=sys.stderr)


    child = FileSystemNode(
        name=path.name,
        type=FileSystemNodeType.FILE,
        size=file_size,
        file_count=1, # A file node represents 1 file
        path_str=str(path.relative_to(local_path)),
        path=path,
        depth=parent_node.depth + 1,
    )

    # Add the valid file node to the parent's children
    parent_node.children.append(child)
    # Aggregate stats up to the parent
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
        # Avoid printing repeatedly deep in recursion
        if depth == MAX_DIRECTORY_DEPTH + 1:
            print(f"Maximum depth limit ({MAX_DIRECTORY_DEPTH}) reached. Stopping recursion deeper.")
            print(f"[DEBUG LIMIT_EXCEEDED] Depth limit reached ({MAX_DIRECTORY_DEPTH}).", file=sys.stderr)
        return True

    # Check file count limit (Use >= MAX_FILES because we check *before* processing the potential MAX_FILES+1 item)
    if stats.total_files >= MAX_FILES:
        # Print only once when the limit is first hit during traversal
        # This check might be redundant if _process_file also handles it, but good for directories
        # We need a way to track if the warning was already printed. Add a flag to stats?
        # For now, rely on the print in _process_file.
        print(f"[DEBUG LIMIT_EXCEEDED] File count limit reached ({MAX_FILES}).", file=sys.stderr)
        return True

    # Check total size limit
    if stats.total_size >= MAX_TOTAL_SIZE_BYTES:
        # Similar printing logic needed if we want to avoid spamming the console.
        # Relying on print in _process_file for now.
        print(f"[DEBUG LIMIT_EXCEEDED] Total size limit reached ({MAX_TOTAL_SIZE_BYTES}).", file=sys.stderr)
        return True

    return False # No limits exceeded

