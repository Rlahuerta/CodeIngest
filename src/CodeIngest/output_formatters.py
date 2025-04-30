"""Functions to format the output of the ingestion process."""

import os
import warnings
from typing import Iterator, List, Optional, Tuple

import tiktoken

from CodeIngest.query_parsing import IngestionQuery
from CodeIngest.schemas import FileSystemNode, FileSystemNodeType
from CodeIngest.schemas.filesystem_schema import SEPARATOR

MAX_DISPLAY_SIZE: int = 300_000


def format_node(node: FileSystemNode, query: IngestionQuery) -> Tuple[str, str, str]:
    """
    Generate a summary, directory structure, and file contents for a given file system node.

    Parameters
    ----------
    node : FileSystemNode
        The file system node to be summarized (can be the root of a directory or a single file).
    query : IngestionQuery
        The parsed query object containing information about the source and parameters.

    Returns
    -------
    Tuple[str, str, str]
        A tuple containing the summary, directory structure, and file contents.
"""
    # Determine if the *initial* input source represented a single file
    is_single_file_input = (query.local_path == node.path and node.type == FileSystemNodeType.FILE)

    summary = _create_summary_prefix(query, single_file=is_single_file_input)

    # --- Tree Structure ---
    if is_single_file_input:
        tree = f"File: {node.name}\n"
    else:
        tree = "Directory structure:\n" + _create_tree_structure(query, node)

    # --- Assemble Formatted Content ---
    content_parts = []
    total_content_size = 0
    cropped = False
    total_files_processed = 0
    line_count = 0

    nodes_to_process: List[FileSystemNode] = [node]
    all_file_chunks: dict[str, List[str]] = {}
    processed_paths: set[Path] = set() # Use Path object for tracking

    while nodes_to_process:
        current_node = nodes_to_process.pop(0)

        if current_node.type == FileSystemNodeType.FILE:
            if current_node.path in processed_paths: continue
            processed_paths.add(current_node.path)

            try:
                file_content_list = list(current_node.read_chunks())
                all_file_chunks[current_node.path_str] = file_content_list

                first_chunk = file_content_list[0] if file_content_list else ""
                if not first_chunk.startswith("Error:") and first_chunk != "[Non-text file]":
                    file_content_str_for_lines = "".join(file_content_list)
                    current_file_lines = file_content_str_for_lines.count('\n')
                    if file_content_str_for_lines and not file_content_str_for_lines.endswith('\n'):
                        current_file_lines += 1
                    # Add to total line count only if it's the single file input
                    if is_single_file_input and current_node.path == query.local_path:
                        line_count = current_file_lines
                total_files_processed += 1
            except Exception as e:
                warnings.warn(f"Error reading chunks for {current_node.path_str}: {e}")
                all_file_chunks[current_node.path_str] = [f"Error reading file content: {e}"]
                total_files_processed += 1

        elif current_node.type == FileSystemNodeType.DIRECTORY:
            current_node.sort_children()
            nodes_to_process = current_node.children + nodes_to_process # DFS

    # --- Build the final content string ---
    sorted_file_paths = sorted(all_file_chunks.keys())

    for path_str in sorted_file_paths:
        chunks = all_file_chunks[path_str]
        header = f"{SEPARATOR}\nFILE: {path_str}\n{SEPARATOR}\n"
        content_parts.append(header)
        total_content_size += len(header)
        if total_content_size > MAX_DISPLAY_SIZE: cropped = True; break

        file_content_str = "".join(chunks)
        content_parts.append(file_content_str)
        total_content_size += len(file_content_str)

        content_parts.append("\n\n")
        total_content_size += 2

        if total_content_size > MAX_DISPLAY_SIZE: cropped = True; break

    # --- Finalize Summary ---
    if is_single_file_input:
        summary += f"Lines: {line_count:,}\n" if line_count > 0 else "Lines: N/A\n"
    else: # Directory or Zip input
        summary += f"Files analyzed: {total_files_processed}\n"

    content = "".join(content_parts)

    if cropped:
        item_type = "File" if is_single_file_input else "Files"
        crop_message = (
            f"\n({item_type} content cropped to {int(MAX_DISPLAY_SIZE / 1000)}k characters. "
            f"Download full ingest to see more)\n"
        )
        content = crop_message + content[:MAX_DISPLAY_SIZE - len(crop_message)]

    token_estimate = _format_token_count(content)
    if token_estimate:
        summary += f"\nEstimated tokens: {token_estimate}"

    return summary, tree, content


def _create_summary_prefix(query: IngestionQuery, single_file: bool = False) -> str:
    """Create summary prefix with repo/dir/zip, branch/commit, subpath info."""
    parts = []
    if query.url:
        # Remote repository
        parts.append(f"Repository: {query.user_name}/{query.repo_name}")
        if query.commit: parts.append(f"Commit: {query.commit}")
        elif query.branch and query.branch.lower() not in ("main", "master"):
            parts.append(f"Branch: {query.branch}")
    # --- FIX: Logic refined for local sources ---
    elif single_file:
         # If the input was determined to be a single file
         display_path = str(query.local_path.resolve()) # Show the file path
         parts.append(f"File: {display_path}")
    elif query.original_zip_path:
         # If it came from a zip file
         display_path = str(query.original_zip_path.resolve()) # Show original zip path
         parts.append(f"Zip File: {display_path}")
    else:
         # Otherwise, it's a directory input
         display_path = str(query.local_path.resolve()) # Show the directory path
         parts.append(f"Directory: {display_path}")
    # --- End FIX ---

    # Subpath only relevant for non-single-file directory/repo/zip sources
    if query.subpath and query.subpath != "/" and not single_file:
        display_subpath = query.subpath.strip('/')
        if display_subpath: parts.append(f"Subpath: {display_subpath}")

    return "\n".join(parts) + "\n"


# _create_tree_structure and _format_token_count remain the same
def _create_tree_structure(query: IngestionQuery, node: FileSystemNode, prefix: str = "", is_last: bool = True) -> str:
    """Generate a tree-like string representation of the file structure."""
    node_name = node.name if node.path_str != "." else node.name

    tree_str = ""
    current_prefix = "└── " if is_last else "├── "

    display_name = node_name
    if node.type == FileSystemNodeType.DIRECTORY and node.path_str != ".": display_name += "/"
    elif node.type == FileSystemNodeType.SYMLINK:
        try:
            target_path = node.path.readlink()
            try:
                base_for_rel_link = query.local_path.parent
                rel_target = target_path.resolve().relative_to(base_for_rel_link.resolve())
                display_name += f" -> {str(rel_target)}"
            except (ValueError, OSError): display_name += f" -> {str(target_path)}"
        except OSError: display_name += " -> [Broken Symlink]"
        except Exception as e: warnings.warn(f"Error reading link target for {node.path}: {e}"); display_name += " -> [Error reading link]"

    line_prefix = prefix if node.path_str != "." else ""
    tree_str += f"{line_prefix}{current_prefix}{display_name}\n"

    if node.type == FileSystemNodeType.DIRECTORY and node.children:
        new_prefix = prefix + ("    " if is_last else "│   ")
        if node.path_str == ".": new_prefix = ""

        processable_children = node.children
        for i, child in enumerate(processable_children):
            tree_str += _create_tree_structure(query, node=child, prefix=new_prefix, is_last=i == len(processable_children) - 1)
    return tree_str


def _format_token_count(text: str) -> Optional[str]:
    """Estimate token count and format it."""
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
        tokens = encoding.encode(text, disallowed_special=())
        total_tokens = len(tokens)
    except Exception as e: warnings.warn(f"Could not estimate token count: {e}", UserWarning); return None

    if total_tokens == 0: return "0"
    if total_tokens >= 1_000_000: return f"{total_tokens / 1_000_000:.1f}M"
    if total_tokens >= 1_000: return f"{total_tokens / 1_000:.1f}k"
    return str(total_tokens)
