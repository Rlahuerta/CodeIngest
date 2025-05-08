# src/CodeIngest/output_formatters.py
"""Functions to ingest and analyze a codebase directory or single file."""

from typing import Optional, Tuple, List, Dict, Any
import os
from pathlib import Path # Import Path

import tiktoken

from CodeIngest.query_parsing import IngestionQuery
from CodeIngest.schemas import FileSystemNode, FileSystemNodeType

TreeDataItem = Dict[str, Any]

def format_node(node: FileSystemNode, query: IngestionQuery) -> Tuple[str, List[TreeDataItem], str]:
    """
    Generate a summary, structured tree data, and file contents for a given file system node.
    # ... (docstring remains the same)
    """
    is_single_file = node.type == FileSystemNodeType.FILE
    summary = _create_summary_prefix(query, single_file=is_single_file)

    if node.type == FileSystemNodeType.DIRECTORY:
        summary += f"Files analyzed: {node.file_count}\n"
    elif node.type == FileSystemNodeType.FILE:
        summary += f"File: {node.path_str}\n"
        try:
            node_content = node.content
            summary += f"Lines: {len(node_content.splitlines()):,}\n"
        except (ValueError, AttributeError):
             summary += "Lines: N/A\n"

    # --- Pass the effective repository root path for relative path calculation ---
    # If it's a zip, the root is the extracted path. If local, it's the original local_path.
    # If remote, it's the base clone path *before* subpath application.
    # query.local_path *should* represent this base path.
    repo_root_path_for_links = query.local_path
    tree_data: List[TreeDataItem] = _create_tree_data(node, repo_root_path=repo_root_path_for_links, parent_prefix="")
    # --- END CHANGE ---


    content = _gather_file_contents(node)

    # (Token estimation remains the same)
    tree_paths_for_token = "\n".join([ item['full_relative_path'] for item in tree_data if item.get('type') != 'DIRECTORY' and item['depth'] > 0 ]) # Use full path for tokens
    text_for_token_estimation = content if is_single_file else (tree_paths_for_token + content)
    token_estimate = _format_token_count(text_for_token_estimation)
    if token_estimate: summary += f"\nEstimated tokens: {token_estimate}"

    return summary, tree_data, content


# (_create_summary_prefix remains the same)
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
    Includes the correctly formatted prefix string and full relative path.
    """
    tree_list: List[TreeDataItem] = []
    prefix = parent_prefix + ("└── " if is_last_sibling else "├── ") if depth > 0 else parent_prefix

    # --- Construct Display Name (remains the same) ---
    display_name = node.name; node_type_str = node.type.name; link_target = ""; is_root_node = depth == 0
    if node.type == FileSystemNodeType.DIRECTORY:
        if not is_root_node or node.name != '.': display_name += "/"
        elif is_root_node and node.name == '.': display_name = node.path.name + "/"
    elif node.type == FileSystemNodeType.SYMLINK:
        try: link_target = node.path.readlink().as_posix(); display_name += f" -> {link_target}"
        except OSError: display_name += " -> [Broken Link]"; link_target = "[Broken Link]"
    if is_root_node and node.name == '.': display_name = node.path.name + ("/" if node.type == FileSystemNodeType.DIRECTORY else "")


    # --- Calculate FULL Relative Path from Repo Root ---
    try:
        # Calculate path relative to the *repo root* passed down
        full_relative_path = node.path.relative_to(repo_root_path).as_posix()
         # Handle root case where relative path might be '.'
        if full_relative_path == '.':
             full_relative_path = "" # Represent root as empty string for linking? Or use name? Let's use name.
             full_relative_path = node.path.name # Use actual dir name for root link base if needed
    except ValueError:
         # Should not happen if repo_root_path is correct, but fallback
         full_relative_path = node.path_str.replace(os.sep, '/')
    except Exception: # Catch other potential errors
        full_relative_path = node.path_str.replace(os.sep, '/') # Fallback

    # --- Add Node to List ---
    tree_list.append({
        "name": display_name,
        "type": node_type_str,
        "path_str": node.path_str.replace(os.sep, '/'), # Keep original path_str if needed elsewhere
        "full_relative_path": full_relative_path, # Store the full path for links
        "depth": depth,
        "link_target": link_target,
        "prefix": prefix,
        "is_last": is_last_sibling,
    })

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
    if total_tokens >= 1_000_000: return f"{total_tokens / 1_000_000:.1f}M"
    if total_tokens >= 1_000: return f"{total_tokens / 1_000:.1f}k"
    return str(total_tokens)