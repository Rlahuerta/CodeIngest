"""Functions to format the output of the ingestion process."""

import os
import warnings
from typing import Iterator, List, Optional, Tuple # Added List

import tiktoken

from CodeIngest.query_parsing import IngestionQuery
from CodeIngest.schemas import FileSystemNode, FileSystemNodeType
from CodeIngest.schemas.filesystem_schema import SEPARATOR
from server.server_config import MAX_DISPLAY_SIZE


def format_node(node: FileSystemNode, query: IngestionQuery) -> Tuple[str, str, str]:
    """
    Generate a summary, directory structure, and file contents for a given file system node.

    Parameters
    ----------
    node : FileSystemNode
        The file system node to be summarized.
    query : IngestionQuery
        The parsed query object containing information about the repository and query parameters.

    Returns
    -------
    Tuple[str, str, str]
        A tuple containing the summary, directory structure, and file contents.
    """
    is_single_file = node.type == FileSystemNodeType.FILE
    summary = _create_summary_prefix(query, single_file=is_single_file)

    tree = "Directory structure:\n" + _create_tree_structure(query, node)

    # --- Assemble Formatted Content ---
    content_parts = []
    total_content_size = 0
    cropped = False
    total_files_processed = 0 # For directory summary

    # Use a queue for breadth-first or depth-first traversal to gather file nodes
    nodes_to_process = [node]
    all_file_chunks = {} # Store chunks to avoid reading twice

    while nodes_to_process:
        current_node = nodes_to_process.pop(0) # Use pop(0) for BFS-like order

        if current_node.type == FileSystemNodeType.FILE:
            # --- Read chunks ONCE ---
            try:
                # Store chunks associated with the node's path_str
                all_file_chunks[current_node.path_str] = list(current_node.read_chunks())
                total_files_processed += 1 # Count files successfully read (even if error message)
            except Exception as e:
                warnings.warn(f"Error reading chunks for {current_node.path_str}: {e}")
                all_file_chunks[current_node.path_str] = [f"Error reading file content: {e}"]
                total_files_processed += 1 # Count files attempted

        elif current_node.type == FileSystemNodeType.DIRECTORY:
            # Add children to the queue (ensure sorted for consistent processing order)
            current_node.sort_children()
            # Prepend children to maintain processing order (like DFS) or append for BFS
            nodes_to_process = current_node.children + nodes_to_process # DFS-like order

    # --- Now build the content string and calculate lines ---
    line_count = 0
    for path_str, chunks in all_file_chunks.items():
        header = f"{SEPARATOR}\nFILE: {path_str}\n{SEPARATOR}\n"
        content_parts.append(header)
        total_content_size += len(header)
        if total_content_size > MAX_DISPLAY_SIZE:
            cropped = True
            break

        # Join the stored chunks
        file_content_str = "".join(chunks)
        content_parts.append(file_content_str)
        total_content_size += len(file_content_str)

        # --- Calculate lines for this file from stored content ---
        if not file_content_str.startswith("Error:") and file_content_str != "[Non-text file]":
            current_file_lines = file_content_str.count('\n')
            if file_content_str and not file_content_str.endswith('\n'):
                current_file_lines += 1
            # Only add to total line count if it's the single file being processed
            if is_single_file:
                line_count = current_file_lines

        # Add trailing newlines
        content_parts.append("\n\n")
        total_content_size += 2

        if total_content_size > MAX_DISPLAY_SIZE:
            cropped = True
            break

    # --- Finalize Summary ---
    if node.type == FileSystemNodeType.DIRECTORY:
        # Use the count of files whose chunks were successfully gathered
        summary += f"Files analyzed: {total_files_processed}\n"
    elif node.type == FileSystemNodeType.FILE:
        summary += f"File: {node.path_str}\n"
        summary += f"Lines: {line_count:,}\n" if line_count > 0 else "Lines: N/A\n" # Add calculated lines

    content = "".join(content_parts)

    # Add cropping message and truncate if needed
    if cropped:
        item_type = "File" if is_single_file else "Files"
        crop_message = (
            f"({item_type} content cropped to {int(MAX_DISPLAY_SIZE / 1000)}k characters. "
            f"Download full ingest to see more)\n"
        )
        content = crop_message + content[:MAX_DISPLAY_SIZE - len(crop_message)]

    token_estimate = _format_token_count(content)
    if token_estimate:
        summary += f"\nEstimated tokens: {token_estimate}"

    return summary, tree, content


# _create_summary_prefix, _gather_file_info (now integrated), _create_tree_structure, _format_token_count remain the same
def _create_summary_prefix(query: IngestionQuery, single_file: bool = False) -> str:
    """Create summary prefix with repo/dir, branch/commit, subpath info."""
    parts = []
    if query.user_name and query.repo_name:
        parts.append(f"Repository: {query.user_name}/{query.repo_name}")
    else:
        # Use resolved absolute path for local directories for clarity
        local_display_path = str(query.local_path.resolve())
        parts.append(f"Directory: {local_display_path}")


    if query.commit:
        parts.append(f"Commit: {query.commit}")
    elif query.branch and query.branch.lower() not in ("main", "master"):
        parts.append(f"Branch: {query.branch}")

    # Only show subpath if it's not the root and not a single file scenario
    if query.subpath and query.subpath != "/" and not single_file:
         # Strip leading/trailing slashes for display
        display_subpath = query.subpath.strip('/')
        if display_subpath: # Only add if subpath is not just '/' after stripping
             parts.append(f"Subpath: {display_subpath}")


    return "\n".join(parts) + "\n"

def _create_tree_structure(query: IngestionQuery, node: FileSystemNode, prefix: str = "", is_last: bool = True) -> str:
    """Generate a tree-like string representation of the file structure."""
    # Use node's path_str for top-level if name is missing (shouldn't happen often)
    node_name = node.name if node.name else node.path_str

    tree_str = ""
    # Determine connector based on whether it's the last item in its parent's list
    # This requires the parent to have sorted children *before* calling this function
    current_prefix = "└── " if is_last else "├── "

    display_name = node_name
    if node.type == FileSystemNodeType.DIRECTORY:
        display_name += "/"
    elif node.type == FileSystemNodeType.SYMLINK:
        try:
            target_path = node.path.readlink() # Read the target path
             # Try to make target relative to the base path's parent for better context
            try:
                # Calculate base relative to the *parent* of the original query local path
                # This provides a more stable base, especially when query.local_path is deep
                base_for_rel_link = query.local_path.parent if query.local_path.is_file() else query.local_path
                # Resolve both paths fully before calculating relative path
                rel_target = target_path.resolve().relative_to(base_for_rel_link.resolve())
                display_name += f" -> {str(rel_target)}"
            except ValueError:
                 # If not relative, show absolute path but resolve symlinks in it too
                 display_name += f" -> {str(target_path.resolve())}"
            except OSError: # Handle cases where target might not exist during resolve()
                 display_name += f" -> {str(target_path)} [Target Error]"

        except OSError:
            display_name += " -> [Broken Symlink]"
        except Exception as e:
             warnings.warn(f"Error reading link target for {node.path}: {e}")
             display_name += " -> [Error reading link]"


    tree_str += f"{prefix}{current_prefix}{display_name}\n"

    if node.type == FileSystemNodeType.DIRECTORY and node.children:
        # Ensure children are sorted before iterating for consistent output
        # Sorting should happen in _process_node, but ensure it here too just in case
        # node.sort_children() # sorting is done in _process_node now
        new_prefix = prefix + ("    " if is_last else "│   ")
        processable_children = node.children # Already filtered during _process_node
        for i, child in enumerate(processable_children):
            tree_str += _create_tree_structure(query, node=child, prefix=new_prefix, is_last=i == len(processable_children) - 1)
    return tree_str


def _format_token_count(text: str) -> Optional[str]:
    """Estimate token count and format it."""
    try:
        # Use a common encoding, adjust if needed for specific models
        encoding = tiktoken.get_encoding("cl100k_base")
        tokens = encoding.encode(text, disallowed_special=())
        total_tokens = len(tokens)
    except Exception as e:
        warnings.warn(f"Could not estimate token count: {e}", UserWarning)
        return None

    if total_tokens >= 1_000_000:
        return f"{total_tokens / 1_000_000:.1f}M"
    if total_tokens >= 1_000:
        return f"{total_tokens / 1_000:.1f}k"
    return str(total_tokens)