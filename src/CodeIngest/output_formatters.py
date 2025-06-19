# src/CodeIngest/output_formatters.py
"""Functions to ingest and analyze a codebase directory or single file."""

from typing import Optional, Tuple, List, Dict, Any
import os
from pathlib import Path # Import Path

import tiktoken

from CodeIngest.query_parsing import IngestionQuery
from CodeIngest.schemas import FileSystemNode, FileSystemNodeType

TreeDataItem = Dict[str, Any]

# New return type for format_node
FormattedNodeData = Dict[str, Any]


def _parse_token_estimate_str_to_int(token_str: Optional[str]) -> int:
    if not token_str:
        return 0
    token_str_lower = token_str.lower() # Use a different variable name
    multiplier = 1
    if 'k' in token_str_lower:
        multiplier = 1000
        token_str_val = token_str_lower.replace('k', '').strip()
    elif 'm' in token_str_lower:
        multiplier = 1_000_000
        token_str_val = token_str_lower.replace('m', '').strip()
    else:
        token_str_val = token_str_lower.strip()
    try:
        return int(float(token_str_val) * multiplier)
    except ValueError:
        # Fallback for plain numbers if any or handle error
        try:
            return int(token_str_val)
        except ValueError:
            return 0

def format_node(node: FileSystemNode, query: IngestionQuery) -> FormattedNodeData: # Changed return type
    """
    Generate a structured dictionary containing summary, tree data with embedded content,
    directory structure text, token count, file count, and concatenated content.
    """
    is_single_file = node.type == FileSystemNodeType.FILE
    summary = _create_summary_prefix(query, single_file=is_single_file)

    # Original summary part for file count/lines - this might be slightly different now
    # as num_files will be derived from tree_data post-symlink filtering.
    # The summary_str will retain this original, potentially broader, file count.
    if node.type == FileSystemNodeType.DIRECTORY:
        summary += f"Files analyzed: {node.file_count}\n" # This is pre-filtering count
    elif node.type == FileSystemNodeType.FILE:
        summary += f"File: {node.path_str}\n"
        try:
            node_content_for_summary = node.content # Access content for line count
            summary += f"Lines: {len(node_content_for_summary.splitlines()):,}\n"
        except (ValueError, AttributeError):
             summary += "Lines: N/A\n"

    repo_root_path_for_links = query.local_path
    # tree_data now potentially has file_content embedded, and symlinks are filtered out
    tree_data: List[TreeDataItem] = _create_tree_data(node, repo_root_path=repo_root_path_for_links, parent_prefix="")

    # Create directory_structure_text_str from the (filtered) tree_data
    dir_struct_lines = []
    for item in tree_data:
        dir_struct_lines.append(f"{item['prefix']}{item['name']}")
    directory_structure_text_str = "\n".join(dir_struct_lines)

    # Calculate num_files_in_tree from the generated (and filtered) tree_data
    num_files_in_tree = sum(1 for item in tree_data if item['type'] == FileSystemNodeType.FILE.name)

    # Concatenated content for token estimation and TXT output
    concatenated_content_str = _gather_file_contents(node) # Gathers content from non-symlink files

    # Token estimation based on concatenated content (as before for summary)
    # Note: text_for_token_estimation used tree_paths_for_token before, which is not directly available here.
    # For simplicity, using concatenated_content_str for token estimation.
    # If tree_paths were crucial, _create_tree_data would need to return them or they'd be rebuilt.
    # The CLI version used `tree_paths_for_token + content` if not single file.
    # Here, `concatenated_content_str` should be roughly equivalent to `content` from old model.
    # For directory, _gather_file_contents recursively gets all file content.
    # For single file, it's just that file's content.
    # This should be fine for token estimation for the summary.
    token_estimate_str = _format_token_count(concatenated_content_str)
    if token_estimate_str:
        summary += f"\nEstimated tokens: {token_estimate_str}" # Append to summary string

    # Parse token estimate for structured data
    parsed_num_tokens = _parse_token_estimate_str_to_int(token_estimate_str)

    return {
        "summary_str": summary,
        "tree_data_with_embedded_content": tree_data,
        "directory_structure_text_str": directory_structure_text_str,
        "num_tokens": parsed_num_tokens,
        "num_files": num_files_in_tree,
        "concatenated_content_for_txt": concatenated_content_str
    }

# _create_summary_prefix (remains the same)
def _create_summary_prefix(query: IngestionQuery, single_file: bool = False) -> str:
    parts = []; # ... (implementation remains) ...
    if query.user_name and query.repo_name: parts.append(f"Repository: {query.user_name}/{query.repo_name}")
    else: parts.append(f"Source: {query.slug}")
    if query.commit: parts.append(f"Commit: {query.commit}")
    elif query.branch and query.branch not in ("main", "master"): parts.append(f"Branch: {query.branch}")
    if query.subpath and query.subpath != "/" and not single_file: parts.append(f"Subpath: {query.subpath}")
    return "\n".join(parts) + "\n"


# (_gather_file_contents remains the same)
def _gather_file_contents(node: FileSystemNode) -> str:
    # ... (implementation remains) ...
    if node.type == FileSystemNodeType.FILE: return node.content_string
    elif node.type == FileSystemNodeType.SYMLINK: return node.content_string
    elif node.type == FileSystemNodeType.DIRECTORY:
        return "\n".join(_gather_file_contents(child) for child in node.children)
    return ""


# --- REVISED _create_tree_data to calculate full relative path ---
def _create_tree_data(
    node: FileSystemNode,
    repo_root_path: Path, # Add repo root path
    depth: int = 0,
    is_last_sibling: bool = True,
    parent_prefix: str = ""
) -> List[TreeDataItem]:
    """
    Recursively generate structured data representing the file tree.
    Includes the correctly formatted prefix string, full relative path, and embedded file content.
    Symlinks are excluded.
    """
    if node.type == FileSystemNodeType.SYMLINK:
        return [] # Do not include symlinks in the tree

    tree_list: List[TreeDataItem] = []
    prefix = parent_prefix + ("└── " if is_last_sibling else "├── ") if depth > 0 else parent_prefix

    # --- Construct Display Name ---
    display_name = node.name
    node_type_str = node.type.name # e.g. "FILE", "DIRECTORY"
    link_target = "" # Will remain empty for non-symlinks as symlinks are filtered out
    is_root_node = depth == 0
    if node.type == FileSystemNodeType.DIRECTORY:
        if not is_root_node or node.name != '.': display_name += "/"
        elif is_root_node and node.name == '.': display_name = node.path.name + "/"
    elif node.type == FileSystemNodeType.SYMLINK: # This block should not be reached due to early filter
        try: link_target = node.path.readlink().as_posix(); display_name += f" -> {link_target}"
        except OSError: display_name += " -> [Broken Link]"; link_target = "[Broken Link]"
    if is_root_node and node.name == '.': display_name = node.path.name + ("/" if node.type == FileSystemNodeType.DIRECTORY else "")

    # --- Calculate FULL Relative Path from Repo Root ---
    try:
        # Calculate path relative to the *repo root* passed down
        full_relative_path = node.path.relative_to(repo_root_path).as_posix()
         # Handle root case where relative path might be '.'
        if full_relative_path == '.':
             full_relative_path = "" # Root of the repo, relative path is empty for URL construction
    except ValueError:
         # Should not happen if repo_root_path is correct, but fallback
         full_relative_path = node.path_str.replace(os.sep, '/')
    except Exception: # Catch other potential errors
        full_relative_path = node.path_str.replace(os.sep, '/') # Fallback

    # --- Add Node to List ---
    item_data: TreeDataItem = { # Explicitly type item_data
        "name": display_name,
        "type": node_type_str,
        "path_str": node.path_str.replace(os.sep, '/'),
        "full_relative_path": full_relative_path,
        "depth": depth,
        "link_target": link_target, # Will be empty as symlinks are filtered
        "prefix": prefix,
        "is_last": is_last_sibling,
    }
    if node.type == FileSystemNodeType.FILE:
        item_data["file_content"] = node.content # node.content is a property

    tree_list.append(item_data)

    # --- Recurse into Children ---
    if node.type == FileSystemNodeType.DIRECTORY and node.children:
        num_children = len(node.children)
        child_indent = parent_prefix + ("    " if is_last_sibling else "│   ") if depth >= 0 else "" # Always indent children
        for i, child in enumerate(node.children):
            is_last = (i == num_children - 1)
            # Pass repo_root_path down
            tree_list.extend(_create_tree_data(
                child, repo_root_path, depth + 1, is_last_sibling=is_last, parent_prefix=child_indent
            ))

    return tree_list

# (_format_token_count remains the same)
def _format_token_count(text: str) -> Optional[str]:
    # ... (implementation) ...
    try: encoding = tiktoken.get_encoding("cl100k_base"); total_tokens = len(encoding.encode(text, disallowed_special=()))
    except Exception: return None # Simplified error handling
    if not total_tokens: return "0" # Handle case where total_tokens might be 0
    if total_tokens >= 1_000_000: return f"{total_tokens / 1_000_000:.1f}M"
    if total_tokens >= 1_000: return f"{total_tokens / 1_000:.1f}k"
    return str(total_tokens)