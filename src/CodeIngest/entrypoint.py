# src/CodeIngest/entrypoint.py
"""Main entry point for ingesting a source and processing its contents."""

import asyncio
import inspect
import shutil
from typing import Optional, Set, Tuple, Union, List, Dict, Any # Added List, Dict, Any

from CodeIngest.cloning import clone_repo
from CodeIngest.config import TMP_BASE_PATH
from CodeIngest.ingestion import ingest_query
from CodeIngest.query_parsing import IngestionQuery, parse_query
from CodeIngest.output_formatters import TreeDataItem # Import the type alias

async def ingest_async(
    source: str,
    max_file_size: int = 10 * 1024 * 1024,  # 10 MB
    include_patterns: Optional[Union[str, Set[str]]] = None,
    exclude_patterns: Optional[Union[str, Set[str]]] = None,
    branch: Optional[str] = None,
    output: Optional[str] = None,
# MODIFIED: Updated return type hint for tree_data
) -> Tuple[str, List[TreeDataItem], str, IngestionQuery]:
    """
    Main entry point for ingesting a source and processing its contents.

    This function analyzes a source (URL or local path), clones the corresponding repository (if applicable),
    and processes its files according to the specified query parameters. It returns a summary, structured
    tree data, the content of the files, and the IngestionQuery object used.
    The results can optionally be written to an output file.

    Parameters
    ----------
    source : str
        The source to analyze, which can be a URL (for a Git repository) or a local directory path.
    max_file_size : int
        Maximum allowed file size for file ingestion. Files larger than this size are ignored, by default
        10*1024*1024 (10 MB).
    include_patterns : Union[str, Set[str]], optional
        Pattern or set of patterns specifying which files to include. If `None`, all files are included.
    exclude_patterns : Union[str, Set[str]], optional
        Pattern or set of patterns specifying which files to exclude. If `None`, no files are excluded.
    branch : str, optional
        The branch to clone and ingest. If `None`, the default branch is used.
    output : str, optional
        File path where the summary and content should be written. If `None`, the results are not written to a file.

    Returns
    -------
    Tuple[str, List[TreeDataItem], str, IngestionQuery] # MODIFIED
        A tuple containing:
        - A summary string of the analyzed repository or directory.
        - A list of dictionaries representing the tree structure.
        - The content of the files in the repository or directory.
        - The IngestionQuery object used for this ingestion.

    Raises
    ------
    TypeError
        If `clone_repo` does not return a coroutine, or if the `source` is of an unsupported type.
    ValueError
        If the source path does not exist or other parsing/ingestion errors occur.
    """
    repo_cloned = False
    query: Optional[IngestionQuery] = None # Initialize query to None

    try:
        # Parse the source (URL or local path) into a query object
        query = await parse_query(
            source=source,
            max_file_size=max_file_size,
            from_web=False, # Assume not from web initially; parse_query will detect URLs
            include_patterns=include_patterns,
            ignore_patterns=exclude_patterns,
        )

        # --- Conditional Cloning ---
        if query.url:
            selected_branch = branch if branch else query.branch
            query.branch = selected_branch # Update query with the selected branch

            clone_config = query.extract_clone_config()
            clone_coroutine = clone_repo(clone_config)

            if inspect.iscoroutine(clone_coroutine):
                await clone_coroutine # Simpler await now
            else:
                raise TypeError("clone_repo did not return a coroutine as expected.")

            repo_cloned = True

        # --- Ingestion ---
        # MODIFIED: ingest_query now returns tree_data (list) instead of tree (string)
        summary, tree_data, content = ingest_query(query)

        # --- Output ---
        if output is not None:
             # Recreate simple text tree for file output if needed, or save structured?
             # For simplicity, let's just save summary and content to file.
            with open(output, "w", encoding="utf-8") as f:
                 f.write(summary + "\n\n")
                 # Recreate a basic text tree for the file output
                 text_tree_for_file = "Directory structure:\n"
                 for item in tree_data:
                     indent = "    " * item['depth']
                     prefix = "└── " # Simplified prefix for file output
                     text_tree_for_file += f"{indent}{prefix}{item['name']}\n"
                 f.write(text_tree_for_file)
                 f.write("\n" + content)


        return summary, tree_data, content, query # MODIFIED: Return tree_data list

    finally:
        # --- Cleanup ---
        if repo_cloned and query and query.local_path.is_relative_to(TMP_BASE_PATH):
            if query.local_path.exists():
                shutil.rmtree(query.local_path, ignore_errors=True)


# MODIFIED: Update return type hint for ingest function
def ingest(
    source: str,
    max_file_size: int = 10 * 1024 * 1024,  # 10 MB
    include_patterns: Optional[Union[str, Set[str]]] = None,
    exclude_patterns: Optional[Union[str, Set[str]]] = None,
    branch: Optional[str] = None,
    output: Optional[str] = None,
) -> Tuple[str, List[TreeDataItem], str, IngestionQuery]:
    """
    Synchronous version of ingest_async.

    Returns structured tree data instead of a formatted string.

    Parameters
    ----------
    source : str
        The source to analyze.
    max_file_size : int
        Maximum allowed file size.
    include_patterns : Union[str, Set[str]], optional
        Patterns to include.
    exclude_patterns : Union[str, Set[str]], optional
        Patterns to exclude.
    branch : str, optional
        Branch to clone.
    output : str, optional
        Output file path.

    Returns
    -------
    Tuple[str, List[TreeDataItem], str, IngestionQuery] # MODIFIED
        Summary, structured tree data, content, and query object.
    """
    # Run the asynchronous version within the current event loop or create a new one
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError: # No running event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(
             ingest_async(
                source=source,
                max_file_size=max_file_size,
                include_patterns=include_patterns,
                exclude_patterns=exclude_patterns,
                branch=branch,
                output=output,
            )
        )
    else:
         return loop.run_until_complete(
             ingest_async(
                source=source,
                max_file_size=max_file_size,
                include_patterns=include_patterns,
                exclude_patterns=exclude_patterns,
                branch=branch,
                output=output,
            )
        )