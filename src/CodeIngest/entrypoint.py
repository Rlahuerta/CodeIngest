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
# Removed import of TreeDataItem as it's no longer used here

async def ingest_async(
    source: str,
    max_file_size: int = 10 * 1024 * 1024,  # 10 MB
    include_patterns: Optional[Union[str, Set[str]]] = None,
    exclude_patterns: Optional[Union[str, Set[str]]] = None,
    branch: Optional[str] = None
) -> Dict[str, Any]: # output parameter removed
    """
    Main entry point for ingesting a source and processing its contents.
    Returns a dictionary with structured data.
    The 'output' parameter was removed as this function no longer handles file writing directly.
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
        # ingest_query now returns a dictionary
        formatted_data_dict = ingest_query(query)

        # --- Output file writing is REMOVED from this function ---
        # The 'output' parameter is effectively ignored by this function's direct logic now.
        # Callers (like cli.py) will handle file writing based on the returned dictionary.

        return {
            "summary_str": formatted_data_dict["summary_str"],
            "tree_data": formatted_data_dict["tree_data_with_embedded_content"],
            "directory_structure_text": formatted_data_dict["directory_structure_text_str"],
            "num_tokens": formatted_data_dict["num_tokens"],
            "num_files": formatted_data_dict["num_files"],
            "concatenated_content": formatted_data_dict["concatenated_content_for_txt"],
            "query_obj": query  # query object itself, not its model_dump
        }

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
    branch: Optional[str] = None
) -> Dict[str, Any]: # output parameter removed
    """
    Synchronous version of ingest_async.
    Returns a dictionary with structured data.
    The 'output' parameter was removed as this function no longer handles file writing directly.
    """
    # (Implementation remains the same)
    try: loop = asyncio.get_running_loop()
    except RuntimeError: loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    return loop.run_until_complete( ingest_async( source=source,
                                                  max_file_size=max_file_size,
                                                  include_patterns=include_patterns,
                                                  exclude_patterns=exclude_patterns,
                                                  branch=branch ) ) # output argument removed from call
