# src/CodeIngest/entrypoint.py
"""Main entry point for ingesting a source and processing its contents."""

import asyncio
import inspect
import shutil
from pathlib import Path
from typing import Optional, Set, Tuple, Union

from CodeIngest.cloning import clone_repo
from CodeIngest.config import TMP_BASE_PATH, OUTPUT_FILE_NAME
from CodeIngest.ingestion import ingest_query
from CodeIngest.query_parsing import IngestionQuery, parse_query
from CodeIngest.utils.ignore_patterns import DEFAULT_IGNORE_PATTERNS # Import defaults


async def ingest_async(
    source: str,
    max_file_size: int = 10 * 1024 * 1024,  # 10 MB
    include_patterns: Optional[Set[str]] = None,
    exclude_patterns: Optional[Set[str]] = None,
    branch: Optional[str] = None,
    output: Optional[str] = None,
) -> Tuple[str, str, str]:
    """
    Main entry point for ingesting a source and processing its contents.

    Parameters
    ----------
    source : str
        Source URL, local directory path, or local .zip file path.
    max_file_size : int
        Maximum allowed file size.
    include_patterns : Set[str], optional
        Set of patterns specifying which files to include. If provided, only matching files are processed.
    exclude_patterns : Set[str], optional
        Set of patterns specifying which files to exclude. If provided, these are used instead of defaults.
    branch : str, optional
        Branch to clone (URL source only).
    output : str, optional
        File path for output.

    Returns
    -------
    Tuple[str, str, str]
        Summary, tree, and content.

    Raises
    ------
    TypeError, ValueError
    """
    repo_cloned = False
    zip_extracted = False
    query: Optional[IngestionQuery] = None

    try:
        # --- Determine Final Patterns ---
        final_ignore_patterns = DEFAULT_IGNORE_PATTERNS.copy()
        final_include_patterns = None

        if exclude_patterns is not None:
            final_ignore_patterns = exclude_patterns
            final_include_patterns = include_patterns
        elif include_patterns is not None:
            final_include_patterns = include_patterns

        # --- Parse Query ---
        query = await parse_query(
            source=source,
            max_file_size=max_file_size,
            from_web=False,
            include_patterns=final_include_patterns,
            ignore_patterns=final_ignore_patterns,
        )

        zip_extracted = query.temp_extract_path is not None

        # --- Conditional Cloning ---
        if query.url:
            selected_branch = branch if branch else query.branch
            if selected_branch and selected_branch != query.branch:
                 query.branch = selected_branch

            clone_config = query.extract_clone_config()
            clone_coroutine = clone_repo(clone_config)

            # --- FIX: Always await, never call asyncio.run() here ---
            if inspect.iscoroutine(clone_coroutine):
                 await clone_coroutine # Assume we are already in a running loop
            else:
                # This case should ideally not happen if clone_repo is always async
                raise TypeError("clone_repo did not return a coroutine as expected.")
            # --- End FIX ---

            repo_cloned = True

        # --- Ingestion ---
        summary, tree, content = ingest_query(query)

        # --- Output ---
        if output is not None:
            output_filename = f"{query.slug}.txt" if output == OUTPUT_FILE_NAME else output
            output_path = Path(output_filename)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(tree + "\n" + content)
            print(f"Output written to: {output_path}")

        return summary, tree, content

    finally:
        # --- Cleanup ---
        if repo_cloned and query and query.local_path.is_relative_to(TMP_BASE_PATH):
            print(f"Cleaning up cloned repo temp dir: {query.local_path.parent}")
            shutil.rmtree(query.local_path.parent, ignore_errors=True)

        if zip_extracted and query and query.temp_extract_path and query.temp_extract_path.is_relative_to(TMP_BASE_PATH):
            if query.temp_extract_path.exists():
                 print(f"Cleaning up zip extraction temp dir: {query.temp_extract_path.parent}")
                 shutil.rmtree(query.temp_extract_path.parent, ignore_errors=True)


def ingest(
    source: str,
    max_file_size: int = 10 * 1024 * 1024,  # 10 MB
    include_patterns: Optional[Set[str]] = None,
    exclude_patterns: Optional[Set[str]] = None,
    branch: Optional[str] = None,
    output: Optional[str] = None,
) -> tuple[str, str, str] | None:
    """
    Synchronous version of ingest_async.
    """
    try:
        # Check if a loop is already running in this thread
        loop = asyncio.get_running_loop()
        # If yes, schedule ingest_async within it (e.g., using ensure_future or similar,
        # but run_until_complete might work if called from the loop's thread context,
        # though it's generally for starting/stopping loops).
        # For simplicity in this context, we'll assume run_until_complete is acceptable.
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
    except RuntimeError: # No running event loop in this thread
        # Create a new loop, run ingest_async, and close the loop
        # Note: asyncio.run() is the preferred way for this in Python 3.7+
        # Let's revert to using asyncio.run() here as it handles setup/teardown.
        try:
            return asyncio.run(
                 ingest_async(
                     source=source,
                     max_file_size=max_file_size,
                     include_patterns=include_patterns,
                     exclude_patterns=exclude_patterns,
                     branch=branch,
                     output=output,
                 )
             )
        except Exception as e:
             # Handle potential exceptions during async execution if needed
             print(f"Error during synchronous execution via asyncio.run: {e}")
             return None

