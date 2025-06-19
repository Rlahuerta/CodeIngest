# src/CodeIngest/ingestion.py
"""Functions to ingest and analyze a codebase directory or single file."""

import logging
from pathlib import Path
from typing import Tuple, List # Added List type hint

from CodeIngest.config import MAX_DIRECTORY_DEPTH, MAX_FILES, MAX_TOTAL_SIZE_BYTES
from CodeIngest.output_formatters import format_node, TreeDataItem # Import TreeDataItem
from CodeIngest.query_parsing import IngestionQuery
from CodeIngest.schemas import FileSystemNode, FileSystemNodeType, FileSystemStats
from CodeIngest.utils.ingestion_utils import _should_exclude, _should_include

try:
    import tomllib  # type: ignore[import]
except ImportError:
    # For Python < 3.11
    import tomli as tomllib # type: ignore[no-redef]

logger = logging.getLogger(__name__)

# MODIFIED: ingest_query returns TreeDataItem list now
def ingest_query(query: IngestionQuery) -> Tuple[str, List[TreeDataItem], str]:
    """
    Run the ingestion process for a parsed query.

    This is the main entry point for analyzing a codebase directory or single file. It processes the query
    parameters, reads the file or directory content, and generates a summary, structured tree data, and file content,
    along with token estimations.

    Parameters
    ----------
    query : IngestionQuery
        The parsed query object containing information about the repository and query parameters.

    Returns
    -------
    Tuple[str, List[TreeDataItem], str]
        A tuple containing the summary, structured tree data, and file contents.

    Raises
    ------
    ValueError
        If the path cannot be found, is not a file/directory, or the file has no content.
    """
    # Determine the effective path to start ingestion from
    if query.local_path.is_file():
         path = query.local_path
         base_path_for_rel = query.local_path.parent
    elif query.subpath and query.subpath != "/":
         # Ensure subpath resolution doesn't cause issues if local_path is already deep
         try:
             resolved_subpath = (query.local_path / query.subpath.strip("/")).resolve()
             # Check if resolved path still starts with the original local_path base
             # This helps prevent unintended directory traversal if subpath contains '..'
             if resolved_subpath.is_relative_to(query.local_path.resolve()):
                 path = resolved_subpath
                 base_path_for_rel = query.local_path # Base for relative paths remains original
             else:
                 raise ValueError(f"Invalid subpath leads outside the base directory: {query.subpath}")
         except Exception as e: # Catch FileNotFoundError or other resolution errors
             raise ValueError(f"Could not resolve subpath '{query.subpath}' within '{query.local_path}': {e}") from e
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
        if query.ignore_patterns and _should_exclude(path, base_path_for_rel, query.ignore_patterns):
             raise ValueError(f"File '{path.name}' is excluded by ignore patterns.")
        if query.include_patterns and not _should_include(path, base_path_for_rel, query.include_patterns):
             raise ValueError(f"File '{path.name}' does not match include patterns.")

        relative_path_str = path.relative_to(base_path_for_rel).as_posix()

        file_node = FileSystemNode(
            name=path.name,
            type=FileSystemNodeType.FILE,
            size=path.stat().st_size,
            file_count=1,
            path_str=relative_path_str,
            path=path,
        )

        # Trigger content read to check for errors/non-text
        _ = file_node.content
        if file_node._content_cache is not None and (file_node._content_cache == "[Non-text file]" or "Error" in file_node._content_cache): # Check cache
             logger.warning("File %s has no readable text content or encountered an error during initial read.", file_node.name)

        # format_node now returns tree_data list
        summary, tree_data, content_str = format_node(file_node, query) # Renamed to avoid conflict
        return summary, tree_data, content_str


    # Handle directory ingestion
    if path.is_dir():
         # Calculate relative path for the root node itself
         root_path_str = "." if path == base_path_for_rel else path.relative_to(base_path_for_rel).as_posix()

         root_node = FileSystemNode(
            name=path.name, # Use the target directory name
            type=FileSystemNodeType.DIRECTORY,
            path_str=root_path_str, # Use calculated relative path string
            path=path,
        )

         stats = FileSystemStats() # Fresh stats object with defaults (flags = False)

         _process_node(
            node=root_node,
            query=query,
            stats=stats,
            base_path_for_rel=base_path_for_rel
        )

         # format_node now returns tree_data list
         summary, tree_data, content_str = format_node(root_node, query) # Renamed to avoid conflict
         return summary, tree_data, content_str


    raise ValueError(f"Path is neither a file nor a directory: {path}")


def apply_gitingest_file(path: Path, query: IngestionQuery) -> None:
    """Apply the .gitingest file to the query object."""
    # (Implementation remains the same)
    path_gitingest = path / ".gitingest"
    if not path_gitingest.is_file(): return
    try:
        with path_gitingest.open("rb") as f: data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        logger.warning("Invalid TOML in %s: %s", path_gitingest, exc); return
    except FileNotFoundError:
        logger.debug(".gitingest file not found at %s, skipping.", path_gitingest); return
    except PermissionError:
        logger.warning("Permission denied when trying to read .gitingest file at %s.", path_gitingest); return
    except OSError as exc:
        logger.warning("An OS error occurred while reading .gitingest file at %s: %s", path_gitingest, exc); return
    config_section = data.get("config", {})
    ignore_patterns = config_section.get("ignore_patterns")
    if not ignore_patterns: return
    if isinstance(ignore_patterns, str): ignore_patterns = [ignore_patterns]
    if not isinstance(ignore_patterns, (list, set)):
        logger.warning("Expected list/set for 'ignore_patterns', got %s in %s. Skipping.", type(ignore_patterns), path_gitingest); return
    valid_patterns = {pattern for pattern in ignore_patterns if isinstance(pattern, str)}
    invalid_patterns = set(ignore_patterns) - valid_patterns
    if invalid_patterns: logger.warning("Ignoring non-string patterns %s from %s.", invalid_patterns, path_gitingest)
    if not valid_patterns: return
    if query.ignore_patterns is None: query.ignore_patterns = valid_patterns
    else: query.ignore_patterns.update(valid_patterns)


def _process_node(
    node: FileSystemNode,
    query: IngestionQuery,
    stats: FileSystemStats,
    base_path_for_rel: Path,
) -> None:
    """
    Recursively process a directory item, applying include/exclude patterns.
    """
    # (DEBUG PRINTING can remain or be removed)

    # Check limits before processing children
    if limit_exceeded(stats, node.depth):
        return

    try:
        if not node.path.is_dir():
             logger.warning("Attempted to iterate non-directory: %s", node.path)
             return
        iterator = list(node.path.iterdir())
    except OSError as e:
         logger.warning("Cannot access directory contents %s: %s", node.path, e)
         return

    for sub_path in iterator:
        # Check limits *before* processing each item to potentially stop early
        # Check depth for the next level
        if limit_exceeded(stats, node.depth + 1):
             break # Stop processing items in this directory if depth limit hit

        # Check file/size limits based on current stats
        if stats.total_file_limit_reached or stats.total_size_limit_reached:
             break # Stop if file or size limits already hit

        # Exclusion Check
        if query.ignore_patterns and _should_exclude(sub_path, base_path_for_rel, query.ignore_patterns):
            continue

        # Process based on type
        if sub_path.is_symlink():
            if query.include_patterns and not _should_include(sub_path, base_path_for_rel, query.include_patterns):
                continue
            _process_symlink(path=sub_path, parent_node=node, stats=stats, local_path=base_path_for_rel)

        elif sub_path.is_file():
            if query.include_patterns and not _should_include(sub_path, base_path_for_rel, query.include_patterns):
                continue
            _process_file(path=sub_path, parent_node=node, stats=stats, local_path=base_path_for_rel, max_file_size=query.max_file_size)

        elif sub_path.is_dir():
            # Recurse only if limits haven't been hit
            if not (stats.total_file_limit_reached or stats.total_size_limit_reached or stats.depth_limit_reached):
                child_directory_node = FileSystemNode(
                    name=sub_path.name, type=FileSystemNodeType.DIRECTORY,
                    # Use os.sep here as Path objects handle it correctly
                    path_str=sub_path.relative_to(base_path_for_rel).as_posix(),
                    path=sub_path, depth=node.depth + 1,
                )
                _process_node(node=child_directory_node, query=query, stats=stats, base_path_for_rel=base_path_for_rel)
                if child_directory_node.children or child_directory_node.file_count > 0:
                    node.children.append(child_directory_node)
                    node.size += child_directory_node.size
                    node.file_count += child_directory_node.file_count
                    node.dir_count += 1 + child_directory_node.dir_count
        else:
            logger.warning("Skipping unknown file type: %s", sub_path)

    node.sort_children()


def _process_symlink(path: Path, parent_node: FileSystemNode, stats: FileSystemStats, local_path: Path) -> None:
    """Process a symlink node."""
    try:
        child = FileSystemNode(
            name=path.name, type=FileSystemNodeType.SYMLINK,
            # Use os.sep here as Path objects handle it correctly
            path_str=path.relative_to(local_path).as_posix(),
            path=path, depth=parent_node.depth + 1,
        )
        stats.total_files += 1 # Count towards file limit
        parent_node.children.append(child)
        parent_node.file_count += 1 # Count as file entry in parent stats
    except Exception as e: logger.warning("Failed to process symlink %s: %s", path, e)


def _process_file(path: Path, parent_node: FileSystemNode, stats: FileSystemStats, local_path: Path, max_file_size: int) -> None:
    """Process a file node, checking limits."""
    # Optimization: Check global limits first
    if stats.total_file_limit_reached or stats.total_size_limit_reached:
        return

    try: file_size = path.stat().st_size
    except OSError as e: logger.warning("Could not stat file %s: %s", path, e); return

    if file_size > max_file_size:
        logger.info("Skipping file %s (%s bytes): exceeds max file size (%s bytes).", path.name, file_size, max_file_size); return

    # Check total size limit *before* adding current file's size
    if stats.total_size >= MAX_TOTAL_SIZE_BYTES or (stats.total_size + file_size > MAX_TOTAL_SIZE_BYTES) :
        if not stats.total_size_limit_reached:
            logger.info("Total size limit (%.1f MB) reached.", MAX_TOTAL_SIZE_BYTES / (1024*1024))
            stats.total_size_limit_reached = True
        return # Stop processing this file

    # Check total file count limit *before* incrementing
    if stats.total_files >= MAX_FILES:
        if not stats.total_file_limit_reached:
            logger.info("Maximum file limit (%s) reached.", MAX_FILES)
            stats.total_file_limit_reached = True
        return # Stop processing this file

    # --- If limits are okay, process the file ---
    stats.total_files += 1
    stats.total_size += file_size

    child = FileSystemNode(
        name=path.name, type=FileSystemNodeType.FILE, size=file_size, file_count=1,
        # Use os.sep here as Path objects handle it correctly
        path_str=path.relative_to(local_path).as_posix(),
        path=path, depth=parent_node.depth + 1,
    )

    parent_node.children.append(child)
    parent_node.size += file_size
    parent_node.file_count += 1


# MODIFIED: Use boolean flags from stats object
def limit_exceeded(stats: FileSystemStats, depth: int) -> bool:
    """Check if traversal limits have been exceeded."""
    if depth > MAX_DIRECTORY_DEPTH:
        if not stats.depth_limit_reached: # Check flag before warning
            logger.info("Max directory depth (%s) reached.", MAX_DIRECTORY_DEPTH)
            stats.depth_limit_reached = True # Set flag
        return True
    # Check the boolean flags set by _process_file
    # These flags indicate if the limit was *ever* reached during the process.
    return stats.total_file_limit_reached or stats.total_size_limit_reached