"""Main entry point for ingesting a source and processing its contents."""

import asyncio
import inspect
import shutil
from typing import Optional, Set, Tuple, Union

from CodeIngest.cloning import clone_repo
from CodeIngest.config import TMP_BASE_PATH
from CodeIngest.ingestion import ingest_query
from CodeIngest.query_parsing import IngestionQuery, parse_query


async def ingest_async(
    source: str,
    max_file_size: int = 10 * 1024 * 1024,  # 10 MB
    include_patterns: Optional[Union[str, Set[str]]] = None,
    exclude_patterns: Optional[Union[str, Set[str]]] = None,
    branch: Optional[str] = None,
    output: Optional[str] = None,
) -> Tuple[str, str, str]:
    """
    Main entry point for ingesting a source and processing its contents.

    This function analyzes a source (URL or local path), clones the corresponding repository (if applicable),
    and processes its files according to the specified query parameters. It returns a summary, a tree-like
    structure of the files, and the content of the files. The results can optionally be written to an output file.

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
    Tuple[str, str, str]
        A tuple containing:
        - A summary string of the analyzed repository or directory.
        - A tree-like string representation of the file structure.
        - The content of the files in the repository or directory.

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
        # Only clone if the source was identified as a remote URL
        if query.url:
            # Prioritize the explicit branch argument over any branch parsed from the URL
            selected_branch = branch if branch else query.branch
            query.branch = selected_branch # Update query with the selected branch

            clone_config = query.extract_clone_config()
            # Clone the remote repository to a temporary local path
            clone_coroutine = clone_repo(clone_config)

            # Ensure clone_repo returns a coroutine and run it
            if inspect.iscoroutine(clone_coroutine):
                # Await if an event loop is running, otherwise run synchronously
                if asyncio.get_event_loop().is_running():
                    await clone_coroutine
                else:
                    asyncio.run(clone_coroutine) # Should ideally not happen in async context
            else:
                raise TypeError("clone_repo did not return a coroutine as expected.")

            repo_cloned = True # Mark that a temporary clone was made

        # --- Ingestion ---
        # Ingest the content from the local path (either the original path or the temporary clone)
        summary, tree, content = ingest_query(query)

        # --- Output ---
        # Write the results to the specified output file if requested
        if output is not None:
            with open(output, "w", encoding="utf-8") as f:
                f.write(tree + "\n" + content)

        return summary, tree, content

    finally:
        # --- Cleanup ---
        # Clean up the temporary directory ONLY if a remote repository was cloned
        # query might be None if parse_query failed early
        if repo_cloned and query and query.local_path.is_relative_to(TMP_BASE_PATH):
             # Double-check it's within the expected temp base path before removing
            shutil.rmtree(query.local_path.parent, ignore_errors=True) # Remove the parent ID folder


def ingest(
    source: str,
    max_file_size: int = 10 * 1024 * 1024,  # 10 MB
    include_patterns: Optional[Union[str, Set[str]]] = None,
    exclude_patterns: Optional[Union[str, Set[str]]] = None,
    branch: Optional[str] = None,
    output: Optional[str] = None,
) -> Tuple[str, str, str]:
    """
    Synchronous version of ingest_async.

    This function analyzes a source (URL or local path), clones the corresponding repository (if applicable),
    and processes its files according to the specified query parameters. It returns a summary, a tree-like
    structure of the files, and the content of the files. The results can optionally be written to an output file.

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
    Tuple[str, str, str]
        A tuple containing:
        - A summary string of the analyzed repository or directory.
        - A tree-like string representation of the file structure.
        - The content of the files in the repository or directory.

    See Also
    --------
    ingest_async : The asynchronous version of this function.
    """
    # Run the asynchronous version within the current event loop or create a new one
    # Check if an event loop is already running
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
        # If a loop is running, use ensure_future or create_task depending on context
        # This part might need adjustment based on how/where `ingest` is called
        # For simplicity, we'll assume run_until_complete is acceptable here too,
        # but in complex async apps, you might need `asyncio.ensure_future`.
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
