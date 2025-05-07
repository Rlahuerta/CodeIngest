# src/CodeIngest/output_formatters.py
"""Functions to ingest and analyze a codebase directory or single file."""

from typing import Optional, Tuple, List, Dict, Any # Added List, Dict, Any
import os # Keep os import

import tiktoken

from CodeIngest.query_parsing import IngestionQuery
from CodeIngest.schemas import FileSystemNode, FileSystemNodeType

# Define a type alias for clarity
TreeDataItem = Dict[str, Any]

# MODIFIED: format_node signature and return type
def format_node(node: FileSystemNode, query: IngestionQuery) -> Tuple[str, List[TreeDataItem], str]:
    """
    Generate a summary, structured tree data, and file contents for a given file system node.

    If the node represents a directory, the function will recursively process its contents.

    Parameters
    ----------
    node : FileSystemNode
        The file system node to be summarized.
    query : IngestionQuery
        The parsed query object containing information about the repository and query parameters.

    Returns
    -------
    Tuple[str, List[TreeDataItem], str]
        A tuple containing the summary, a list representing the directory structure data,
        and the file contents.
    """
    is_single_file = node.type == FileSystemNodeType.FILE
    summary = _create_summary_prefix(query, single_file=is_single_file)

    if node.type == FileSystemNodeType.DIRECTORY:
        summary += f"Files analyzed: {node.file_count}\n"
    elif node.type == FileSystemNodeType.FILE:
        summary += f"File: {node.path_str}\n" # Use path_str for single file summary
        summary += f"Lines: {len(node.content.splitlines()):,}\n"

    # MODIFIED: Call the new structure generator
    tree_data: List[TreeDataItem] = _create_tree_data(node, base_path_str=query.slug)

    content = _gather_file_contents(node)

    # Estimate tokens based on the content string and a simple representation of the tree
    # For token estimation, we'll just use the paths from the tree_data
    tree_paths_for_token = "\n".join([item['path_str'] for item in tree_data])
    token_estimate = _format_token_count(tree_paths_for_token + content)
    if token_estimate:
        summary += f"\nEstimated tokens: {token_estimate}"

    return summary, tree_data, content


def _create_summary_prefix(query: IngestionQuery, single_file: bool = False) -> str:
    """
    Create a prefix string for summarizing a repository or local directory.

    Includes repository name (if provided), commit/branch details, and subpath if relevant.

    Parameters
    ----------
    query : IngestionQuery
        The parsed query object containing information about the repository and query parameters.
    single_file : bool
        A flag indicating whether the summary is for a single file, by default False.

    Returns
    -------
    str
        A summary prefix string containing repository, commit, branch, and subpath details.
    """
    parts = []

    if query.user_name and query.repo_name: # Check both exist
        parts.append(f"Repository: {query.user_name}/{query.repo_name}")
    else:
        # Local scenario or incomplete remote info
        parts.append(f"Source: {query.slug}") # Use slug as fallback

    if query.commit:
        parts.append(f"Commit: {query.commit}")
    elif query.branch and query.branch not in ("main", "master"): # Only show non-default branches
        parts.append(f"Branch: {query.branch}")

    # Only show subpath if it's not the root and not a single file view
    if query.subpath and query.subpath != "/" and not single_file:
        parts.append(f"Subpath: {query.subpath}")

    return "\n".join(parts) + "\n"


def _gather_file_contents(node: FileSystemNode) -> str:
    """
    Recursively gather contents of all files under the given node.

    This function recursively processes a directory node and gathers the contents of all files
    under that node. It returns the concatenated content of all files as a single string.

    Parameters
    ----------
    node : FileSystemNode
        The current directory or file node being processed.

    Returns
    -------
    str
        The concatenated content of all files under the given node.
    """
    # Base case: If it's a file, return its formatted content string
    if node.type == FileSystemNodeType.FILE:
        return node.content_string
    # Base case: If it's a symlink, return its representation (no content traversal)
    elif node.type == FileSystemNodeType.SYMLINK:
         return node.content_string # Symlink content string includes link info
    # Recursive case: If it's a directory, gather content from children
    elif node.type == FileSystemNodeType.DIRECTORY:
        # Join contents gathered from each child node
        return "\n".join(_gather_file_contents(child) for child in node.children)
    # Fallback for unexpected types
    return ""


# NEW function to create structured tree data
def _create_tree_data(node: FileSystemNode, base_path_str: str, depth: int = 0) -> List[TreeDataItem]:
    """
    Recursively generate structured data representing the file tree.

    Parameters
    ----------
    node : FileSystemNode
        The current directory or file node being processed.
    base_path_str : str
        The string representation of the base path (slug) for display.
    depth : int
        The current depth in the tree structure.

    Returns
    -------
    List[TreeDataItem]
        A list of dictionaries, where each dictionary represents a node in the tree.
    """
    tree_list: List[TreeDataItem] = []

    # Determine display name and node type string
    display_name = node.name
    node_type_str = node.type.name # e.g., "FILE", "DIRECTORY", "SYMLINK"
    link_target = ""
    if node.type == FileSystemNodeType.DIRECTORY:
        display_name += "/"
    elif node.type == FileSystemNodeType.SYMLINK:
        try:
            link_target = node.path.readlink().as_posix() # Get target path as string
            display_name += f" -> {link_target}"
        except OSError:
             display_name += " -> [Broken Link]"
             link_target = "[Broken Link]"

    # Use node.path_str which should be the relative path
    # Handle the root node case where path_str might be empty or '.'
    relative_path = node.path_str if node.path_str and node.path_str != '.' else (node.name or base_path_str)

    # Add current node to the list
    tree_list.append({
        "name": display_name,
        "type": node_type_str,
        "path_str": relative_path.replace(os.sep, '/'), # Ensure POSIX paths
        "depth": depth,
        "link_target": link_target, # Add link target info
    })

    # Recursively process children if it's a directory
    if node.type == FileSystemNodeType.DIRECTORY and node.children:
        # Children are already sorted by FileSystemNode.sort_children()
        for child in node.children:
            tree_list.extend(_create_tree_data(child, base_path_str, depth + 1))

    return tree_list


# This function remains mostly the same, just estimating tokens differently
def _format_token_count(text: str) -> Optional[str]:
    """
    Return a human-readable string representing the token count of the given text.

    E.g., '120' -> '120', '1200' -> '1.2k', '1200000' -> '1.2M'.

    Parameters
    ----------
    text : str
        The text string for which the token count is to be estimated.

    Returns
    -------
    str, optional
        The formatted number of tokens as a string (e.g., '1.2k', '1.2M'), or `None` if an error occurs.
    """
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
        total_tokens = len(encoding.encode(text, disallowed_special=()))
    except (ValueError, UnicodeEncodeError, tiktoken.registry.InvalidEncodingError) as exc: # Added InvalidEncodingError
        print(f"Warning: Could not estimate token count. Tokenizer error: {exc}")
        return None
    except Exception as exc: # Catch other potential errors during encoding
         print(f"Warning: An unexpected error occurred during token estimation: {exc}")
         return None


    if total_tokens >= 1_000_000:
        return f"{total_tokens / 1_000_000:.1f}M"

    if total_tokens >= 1_000:
        return f"{total_tokens / 1_000:.1f}k"

    return str(total_tokens)