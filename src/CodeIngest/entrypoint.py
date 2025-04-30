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

    This function analyzes a source (URL, local path, or local zip file), clones the corresponding
    repository or extracts the zip file (if applicable), and processes its files according to
    the specified query parameters. It returns a summary, a tree-like structure of the files,
    and the content of the files. The results can optionally be written to an output file.

    Parameters
    ----------
    source : str
        The source to analyze: URL (Git repository), local directory path, or local .zip file path.
    max_file_size : int
        Maximum allowed file size for file ingestion. Files larger than this size are ignored,
        by default 10*1024*1024 (10 MB).
    include_patterns : Union[str, Set[str]], optional
        Pattern or set of patterns specifying which files to include. If `None`, all files are included.
    exclude_patterns : Union[str, Set[str]], optional
        Pattern or set of patterns specifying which files to exclude. If `None`, no files are excluded.
    branch : str, optional
        The branch to clone and ingest (only applicable for URL sources). If `None`, the default branch is used.
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
        If the source path does not exist, is invalid (e.g., bad zip), or other parsing/ingestion errors occur.
"""
    repo_cloned = False
    zip_extracted = False # Flag to track if zip was extracted
    query: Optional[IngestionQuery] = None # Initialize query to None

    try:
        # Parse the source (URL, local path, or zip file) into a query object
        # parse_query now handles zip detection and extraction internally
        query = await parse_query(
            source=source,
            max_file_size=max_file_size,
            from_web=False, # Assume not from web initially; parse_query handles URLs/zips
            include_patterns=include_patterns,
            ignore_patterns=exclude_patterns,
        )

        zip_extracted = query.temp_extract_path is not None # Check if extraction happened

        # --- Conditional Cloning (Only for remote URLs) ---
        if query.url:
            selected_branch = branch if branch else query.branch
            query.branch = selected_branch

            clone_config = query.extract_clone_config()
            clone_coroutine = clone_repo(clone_config)

            if inspect.iscoroutine(clone_coroutine):
                if asyncio.get_event_loop().is_running():
                    await clone_coroutine
                else:
                    asyncio.run(clone_coroutine)
            else:
                raise TypeError("clone_repo did not return a coroutine as expected.")

            repo_cloned = True # Mark that a temporary clone was made

        # --- Ingestion ---
        # Ingest from local_path (which points to original dir, temp clone, or temp extract)
        summary, tree, content = ingest_query(query)

        # --- Output ---
        if output is not None:
            # Determine output filename based on slug
            output_filename = f"{query.slug}.txt" if output == OUTPUT_FILE_NAME else output
            output_path = Path(output_filename)
            # Create parent directory if necessary
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(tree + "\n" + content)
            print(f"Output written to: {output_path}") # Inform user of actual path

        return summary, tree, content

    finally:
        # --- Cleanup ---
        # Clean up the temporary directory ONLY if a remote repository was cloned
        if repo_cloned and query and query.local_path.is_relative_to(TMP_BASE_PATH):
            print(f"Cleaning up cloned repo temp dir: {query.local_path.parent}") # Optional logging
            shutil.rmtree(query.local_path.parent, ignore_errors=True) # Remove the parent ID folder

        # --- Added: Clean up the temporary directory if a zip file was extracted ---
        if zip_extracted and query and query.temp_extract_path and query.temp_extract_path.is_relative_to(TMP_BASE_PATH):
             # Ensure temp_extract_path exists before removal
            if query.temp_extract_path.exists():
                 print(f"Cleaning up zip extraction temp dir: {query.temp_extract_path.parent}") # Optional logging
                 # Remove the parent ID folder containing the extracted files
                 shutil.rmtree(query.temp_extract_path.parent, ignore_errors=True)
        # --- End Added ---


def ingest(
    source: str,
    max_file_size: int = 10 * 1024 * 1024,  # 10 MB
    include_patterns: Optional[Union[str, Set[str]]] = None,
    exclude_patterns: Optional[Union[str, Set[str]]] = None,
    branch: Optional[str] = None,
    output: Optional[str] = None,
) -> tuple[str, str, str] | None:
    """
    Synchronous version of ingest_async.

    Analyzes a source (URL, local path, or local zip file), clones/extracts if necessary,
    and processes its files according to parameters. Returns summary, tree, and content.

    Parameters
    ----------
    source : str
        Source URL, local directory path, or local .zip file path.
    max_file_size : int
        Maximum allowed file size (default 10 MB).
    include_patterns : Union[str, Set[str]], optional
        Patterns for files to include.
    exclude_patterns : Union[str, Set[str]], optional
        Patterns for files to exclude.
    branch : str, optional
        Branch to clone (URL source only).
    output : str, optional
        File path for output. If None, not written. If 'digest.txt', uses slug.

    Returns
    -------
    Tuple[str, str, str]
        Summary string, tree string, file content string.

    See Also
    --------
    ingest_async : The asynchronous version of this function.
"""
    # ... (implementation remains the same) ...
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError: # No running event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # Ensure loop is closed if we created it
        try:
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
        finally:
            loop.close()
    else:
         # If a loop is running, use ensure_future or create_task depending on context
        # For simplicity using run_until_complete here assuming it's okay in the context.
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

