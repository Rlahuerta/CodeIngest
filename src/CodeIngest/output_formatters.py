# src/CodeIngest/output_formatters.py
"""Functions to ingest and analyze a codebase directory or single file."""

from typing import Optional, Tuple, List, Dict, Any
import os
from pathlib import Path # Import Path

import tiktoken

from CodeIngest.query_parsing import IngestionQuery
from CodeIngest.schemas import FileSystemNode, FileSystemNodeType

# New type alias for nested tree structure
NestedTreeDataItem = Dict[str, Any]
# Old TreeDataItem (flat list item) is removed.

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

def _count_files_in_nested_tree(node: Optional[NestedTreeDataItem]) -> int:
    if not node:
        return 0
    count = 1 if node.get("type") == FileSystemNodeType.FILE.name else 0
    # Check if 'children' key exists and if it's a list before iterating
    if node.get("type") == FileSystemNodeType.DIRECTORY.name and isinstance(node.get("children"), list):
        for child in node["children"]:
            count += _count_files_in_nested_tree(child)
    return count

def _generate_text_tree_lines_recursive(node: NestedTreeDataItem, parent_prefix_for_child: str, is_last_sibling: bool, lines: List[str]):
    connector = "└── " if is_last_sibling else "├── "

    # parent_prefix_for_child:
    # "ROOT_NODE_ITSELF" -> This is the root node itself. Print name, set children's base_prefix to ""
    # ""                 -> This is a direct child of the root. Print connector + name, set children's base_prefix to "    " or "│   "
    # "..."              -> This is a deeper node. Print connector + name, extend parent's base_prefix for children.

    if parent_prefix_for_child == "ROOT_NODE_ITSELF":
         lines.append(node['name'])
         current_prefix_for_children = ""
    elif parent_prefix_for_child == "":
        lines.append(f"{connector}{node['name']}")
        current_prefix_for_children = "    " if is_last_sibling else "│   "
    else:
        lines.append(f"{parent_prefix_for_child}{connector}{node['name']}")
        current_prefix_for_children = parent_prefix_for_child + ("    " if is_last_sibling else "│   ")

    if node.get("type") == FileSystemNodeType.DIRECTORY.name and isinstance(node.get("children"), list):
        children = node["children"]
        num_children = len(children)
        for i, child in enumerate(children):
            _generate_text_tree_lines_recursive(child, current_prefix_for_children, i == num_children - 1, lines)

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

    # Call the new _create_tree_data
    nested_tree_root: Optional[NestedTreeDataItem] = _create_tree_data(node, query.local_path)

    # Generate directory_structure_text_str using the helper function
    text_tree_lines: List[str] = []
    if nested_tree_root:
        _generate_text_tree_lines_recursive(nested_tree_root, "ROOT_NODE_ITSELF", True, text_tree_lines)
    directory_structure_text_str = "\n".join(text_tree_lines)

    # Calculate num_files_in_tree using the helper function
    num_files_in_tree = _count_files_in_nested_tree(nested_tree_root)

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
        "tree_data_with_embedded_content": nested_tree_root if nested_tree_root else {},
        "directory_structure_text_str": directory_structure_text_str, # Using placeholder
        "num_tokens": parsed_num_tokens,
        "num_files": num_files_in_tree, # Using placeholder
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


def _create_tree_data(
    node: FileSystemNode,
    repo_root_path: Path
) -> Optional[NestedTreeDataItem]: # Changed signature and return type
    """
    Recursively generate a nested dictionary representing the file tree.
    Symlinks are excluded.
    """
    if node.type == FileSystemNodeType.SYMLINK:
        return None # Exclude symlinks

    current_node_name = node.name
    # If the node being processed is the explicitly passed root of the repository
    if node.path == repo_root_path:
        current_node_name = node.path.name # Use the actual folder name for the root display

    if node.type == FileSystemNodeType.DIRECTORY:
        display_name = current_node_name + "/"
    else:
        display_name = current_node_name

    relative_path_str: str
    if node.path == repo_root_path:
        relative_path_str = "." # Root of the sub-tree being processed by this call
    else:
        try:
            # Ensure relative_to is only called if node.path is indeed deeper than repo_root_path
            if node.path.is_relative_to(repo_root_path): # Check if it's a subpath
                 relative_path_str = node.path.relative_to(repo_root_path).as_posix()
            else: # If not a subpath (e.g. sibling, or different root), use its name as path
                 relative_path_str = node.path.name
        except ValueError:
            # Fallback if relative_to fails for some other reason
            relative_path_str = node.path.name

    item_data: NestedTreeDataItem = { # Use NestedTreeDataItem for clarity
        "name": display_name,
        "path": relative_path_str,
        "type": node.type.name,
    }

    if node.type == FileSystemNodeType.FILE:
        item_data["file_content"] = node.content # node.content is a property

    elif node.type == FileSystemNodeType.DIRECTORY:
        children_list = []
        if node.children:
            # node.sort_children() is called by _process_node in ingestion.py
            for child_fs_node in node.children:
                child_tree_item = _create_tree_data(child_fs_node, repo_root_path)
                if child_tree_item:
                    children_list.append(child_tree_item)
        item_data["children"] = children_list

    return item_data

# (_format_token_count remains the same)
def _format_token_count(text: str) -> Optional[str]:
    # ... (implementation) ...
    try: encoding = tiktoken.get_encoding("cl100k_base"); total_tokens = len(encoding.encode(text, disallowed_special=()))
    except Exception: return None # Simplified error handling
    if not total_tokens: return "0" # Handle case where total_tokens might be 0
    if total_tokens >= 1_000_000: return f"{total_tokens / 1_000_000:.1f}M"
    if total_tokens >= 1_000: return f"{total_tokens / 1_000:.1f}k"
    return str(total_tokens)