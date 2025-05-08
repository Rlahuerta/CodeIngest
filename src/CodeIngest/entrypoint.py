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
) -> Tuple[str, List[TreeDataItem], str, IngestionQuery]:
    """
    Main entry point for ingesting a source and processing its contents.
    # ... (docstring remains the same)
    """
    repo_cloned = False
    query: Optional[IngestionQuery] = None

    try:
        # (Parsing and Cloning logic remains the same)
        query = await parse_query( source=source, max_file_size=max_file_size, from_web=False, include_patterns=include_patterns, ignore_patterns=exclude_patterns )
        if query.url:
            selected_branch = branch if branch else query.branch
            query.branch = selected_branch
            clone_config = query.extract_clone_config()
            clone_coroutine = clone_repo(clone_config)
            if inspect.iscoroutine(clone_coroutine): await clone_coroutine
            else: raise TypeError("clone_repo did not return a coroutine as expected.")
            repo_cloned = True

        # --- Ingestion ---
        summary, tree_data, content = ingest_query(query)

        # --- Output ---
        if output is not None:
            # --- CORRECTED: Write output file using correct prefixes ---
            try:
                with open(output, "w", encoding="utf-8") as f:
                     f.write("Directory structure:\n")
                     for item in tree_data:
                         # Use the pre-calculated prefix directly
                         f.write(f"{item['prefix']}{item['name']}\n")
                     f.write("\n" + content) # Append the actual file content
            except OSError as e:
                 # Handle potential file writing errors
                 print(f"Warning: Could not write output file '{output}': {e}")
            # --- END CORRECTION ---

        return summary, tree_data, content, query

    finally:
        # (Cleanup logic remains the same)
        if repo_cloned and query and query.local_path.is_relative_to(TMP_BASE_PATH):
            if query.local_path.exists():
                shutil.rmtree(query.local_path, ignore_errors=True)


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
    # ... (docstring remains the same)
    """
    # (Implementation remains the same)
    try: loop = asyncio.get_running_loop()
    except RuntimeError: loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    return loop.run_until_complete( ingest_async( source=source,
                                                  max_file_size=max_file_size,
                                                  include_patterns=include_patterns,
                                                  exclude_patterns=exclude_patterns,
                                                  branch=branch, output=output, ) )
