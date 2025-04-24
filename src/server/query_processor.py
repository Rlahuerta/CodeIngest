"""Process a query by parsing input, cloning a repository, and generating a summary."""

import shutil # Added for potential cleanup if needed later
from functools import partial
# from pathlib import Path # Added for path operations if needed later

from fastapi import Request
from starlette.templating import _TemplateResponse

from CodeIngest.cloning import clone_repo
from CodeIngest.ingestion import ingest_query
from CodeIngest.query_parsing import IngestionQuery, parse_query
from CodeIngest.config import TMP_BASE_PATH # Import TMP_BASE_PATH for cleanup logic
from server.server_config import EXAMPLE_REPOS, MAX_DISPLAY_SIZE, templates
from server.server_utils import Colors, log_slider_to_size


async def process_query(
    request: Request,
    input_text: str,
    slider_position: int,
    pattern_type: str = "exclude",
    pattern: str = "",
    is_index: bool = False,
) -> _TemplateResponse:
    """
    Process a query by parsing input, potentially cloning a repository, and generating a summary.

    Handle user input (URL or local path), process Git repository data or local directory, and prepare
    a response for rendering a template with the processed results or an error message.

    Parameters
    ----------
    request : Request
        The HTTP request object.
    input_text : str
        Input text provided by the user, typically a Git repository URL/slug or a local path.
    slider_position : int
        Position of the slider, representing the maximum file size in the query.
    pattern_type : str
        Type of pattern to use, either "include" or "exclude" (default is "exclude").
    pattern : str
        Pattern to include or exclude in the query, depending on the pattern type.
    is_index : bool
        Flag indicating whether the request is for the index page (default is False).

    Returns
    -------
    _TemplateResponse
        Rendered template response containing the processed results or an error message.

    Raises
    ------
    ValueError
        If an invalid pattern type is provided or if the local path is invalid/inaccessible.
    """
    if pattern_type == "include":
        include_patterns = pattern
        exclude_patterns = None
    elif pattern_type == "exclude":
        exclude_patterns = pattern
        include_patterns = None
    else:
        raise ValueError(f"Invalid pattern type: {pattern_type}")

    template = "index.jinja" if is_index else "git.jinja"
    template_response = partial(templates.TemplateResponse, name=template)
    max_file_size = log_slider_to_size(slider_position)
    repo_cloned = False # Track if we cloned a repo temporarily
    query: Optional[IngestionQuery] = None # Initialize query

    context = {
        "request": request,
        "repo_url": input_text, # Keep original input for display
        "examples": EXAMPLE_REPOS if is_index else [],
        "default_file_size": slider_position,
        "pattern_type": pattern_type,
        "pattern": pattern,
    }

    try:
        # SECURITY NOTE: Allowing arbitrary input_text to be parsed as a local path
        # is highly insecure in a web context. Ensure this runs in a controlled environment.
        query = await parse_query(
            source=input_text,
            max_file_size=max_file_size,
            from_web=False, # Let parse_query determine if it's a URL or local path
            include_patterns=include_patterns,
            ignore_patterns=exclude_patterns,
        )

        # --- Conditional Cloning ---
        if query.url:
            # It's a remote URL, proceed with cloning
            clone_config = query.extract_clone_config()
            await clone_repo(clone_config)
            repo_cloned = True # Mark that we created a temporary clone
            # Ingest from the temporary clone path
            summary, tree, content = ingest_query(query)
            # Optionally save the digest file in the temp clone dir
            digest_path = query.local_path.parent / f"{query.slug}.txt"
            with open(digest_path, "w", encoding="utf-8") as f:
                 f.write(tree + "\n" + content)

        else:
            # It's a local path, ingest directly
            # SECURITY: query.local_path comes directly from user input here.
            # No temporary directory is used or cleaned up.
            summary, tree, content = ingest_query(query)
            # Local paths don't have a temporary digest file created by default here.
            # The 'ingest_id' might be less meaningful as it doesn't map to a temp downloadable file.
            # We'll still pass the query.id, but download won't work unless ingest() created a file.


    except Exception as exc:
        # Log the error with context
        url_or_path = query.url if query and query.url else input_text # Use input_text if query failed early
        _print_error(url_or_path, exc, max_file_size, pattern_type, pattern)

        context["error_message"] = f"Error processing '{url_or_path}': {exc}"
        # Improve error message for common issues
        if "Repository not found" in str(exc) or "404" in str(exc) or "405" in str(exc):
             context["error_message"] = (
                f"Error: Could not access '{url_or_path}'. Please ensure the URL is correct and public, "
                "or that the local path exists and is accessible."
             )
        elif "Local path not found" in str(exc):
             context["error_message"] = f"Error: Local path not found: {input_text}"
        elif isinstance(exc, ValueError) and "invalid characters" in str(exc):
             context["error_message"] = f"Error: Invalid pattern provided. {exc}"
        # Add more specific error handling as needed

        return template_response(context=context)

    finally:
         # --- Cleanup ---
         # Clean up the temporary directory ONLY if a remote repository was cloned
         if repo_cloned and query and query.local_path.is_relative_to(TMP_BASE_PATH):
             shutil.rmtree(query.local_path.parent, ignore_errors=True) # Remove the parent ID folder


    # --- Success ---
    display_path = query.url if query.url else str(query.local_path) # Show URL or local path
    if len(content) > MAX_DISPLAY_SIZE:
        content = (
            f"(Files content cropped to {int(MAX_DISPLAY_SIZE / 1_000)}k characters. "
            f"Download full ingest if available, or run CLI for full content)\n" + content[:MAX_DISPLAY_SIZE]
        )

    _print_success(
        url_or_path=display_path,
        max_file_size=max_file_size,
        pattern_type=pattern_type,
        pattern=pattern,
        summary=summary,
    )

    context.update(
        {
            "result": True,
            "summary": summary,
            "tree": tree,
            "content": content,
            "ingest_id": query.id if repo_cloned else None, # Only provide ID if a downloadable file exists
            "is_local_path": not query.url # Add flag for template logic
        }
    )

    return template_response(context=context)


def _print_query(url_or_path: str, max_file_size: int, pattern_type: str, pattern: str) -> None:
    """
    Print a formatted summary of the query details, including the URL/path, file size,
    and pattern information, for easier debugging or logging.

    Parameters
    ----------
    url_or_path : str
        The URL or local path associated with the query.
    max_file_size : int
        The maximum file size allowed for the query, in bytes.
    pattern_type : str
        Specifies the type of pattern to use, either "include" or "exclude".
    pattern : str
        The actual pattern string to include or exclude in the query.
    """
    print(f"{Colors.WHITE}{url_or_path:<50}{Colors.END}", end="") # Increased width for paths
    if int(max_file_size / 1024) != 50: # Assuming 50k is default size threshold
        print(f" | {Colors.YELLOW}Size: {int(max_file_size/1024)}kb{Colors.END}", end="")
    if pattern: # Only print if pattern is not empty
        if pattern_type == "include":
            print(f" | {Colors.YELLOW}Include: '{pattern}'{Colors.END}", end="")
        elif pattern_type == "exclude":
            print(f" | {Colors.YELLOW}Exclude: '{pattern}'{Colors.END}", end="")


def _print_error(url_or_path: str, e: Exception, max_file_size: int, pattern_type: str, pattern: str) -> None:
    """
    Print a formatted error message including the URL/path, file size, pattern details,
    and the exception encountered, for debugging or logging purposes.

    Parameters
    ----------
    url_or_path : str
        The URL or local path associated with the query that caused the error.
    e : Exception
        The exception raised during the query or process.
    max_file_size : int
        The maximum file size allowed for the query, in bytes.
    pattern_type : str
        Specifies the type of pattern to use, either "include" or "exclude".
    pattern : str
        The actual pattern string to include or exclude in the query.
    """
    print(f"{Colors.BROWN}WARN{Colors.END}: {Colors.RED}<- Process Failed {Colors.END}", end="")
    _print_query(url_or_path, max_file_size, pattern_type, pattern)
    print(f" | {Colors.RED}{type(e).__name__}: {e}{Colors.END}")


def _print_success(url_or_path: str, max_file_size: int, pattern_type: str, pattern: str, summary: str) -> None:
    """
    Print a formatted success message, including the URL/path, file size, pattern details,
    and a summary with estimated tokens, for debugging or logging purposes.

    Parameters
    ----------
    url_or_path : str
        The URL or local path associated with the successful query.
    max_file_size : int
        The maximum file size allowed for the query, in bytes.
    pattern_type : str
        Specifies the type of pattern to use, either "include" or "exclude".
    pattern : str
        The actual pattern string to include or exclude in the query.
    summary : str
        A summary of the query result, including details like estimated tokens.
    """
    try:
        # Extract token estimate robustly
        token_line = next((line for line in summary.splitlines() if "Estimated tokens:" in line), None)
        estimated_tokens = token_line.split(":", 1)[1].strip() if token_line else "N/A"
    except Exception:
        estimated_tokens = "N/A"

    print(f"{Colors.GREEN}INFO{Colors.END}: {Colors.GREEN}<- Process OK    {Colors.END}", end="") # Aligned length
    _print_query(url_or_path, max_file_size, pattern_type, pattern)
    print(f" | {Colors.PURPLE}Tokens: {estimated_tokens}{Colors.END}")

