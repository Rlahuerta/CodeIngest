"""Process a query by parsing input, cloning a repository, and generating a summary."""

import os
import shutil
from functools import partial
from pathlib import Path
from typing import Optional

from fastapi import Form, Request # Import Form if needed, though handled by router
from starlette.templating import _TemplateResponse

# --- Core CodeIngest imports ---
from CodeIngest.entrypoint import ingest_async # Import ingest_async
from CodeIngest.query_parsing import IngestionQuery, parse_query
from CodeIngest.config import TMP_BASE_PATH

# --- Server specific imports ---
from server.server_config import EXAMPLE_REPOS, MAX_DISPLAY_SIZE, templates
from server.server_utils import Colors, log_slider_to_size


async def process_query(
    request: Request,
    input_text: str,
    slider_position: int,
    pattern_type: str = "exclude",
    pattern: str = "",
    # --- Added branch_or_tag parameter ---
    branch_or_tag: str = "",
    is_index: bool = False,
) -> _TemplateResponse:
    """
    Process a query by parsing input, potentially cloning a repository, and generating a summary.

    Handle user input (URL or local path), process Git repository data or local directory, save
    the result to a temporary file, and prepare a response for rendering a template with the
    processed results or an error message.

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
    branch_or_tag : str
        Specific branch, tag, or commit hash to use (optional).
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
    # Determine include/exclude patterns based on input
    if pattern_type == "include":
        include_patterns = pattern
        exclude_patterns = None
    elif pattern_type == "exclude":
        exclude_patterns = pattern
        include_patterns = None
    else:
        raise ValueError(f"Invalid pattern type: {pattern_type}")

    # Select template and prepare response function
    template = "index.jinja" if is_index else "git.jinja"
    template_response = partial(templates.TemplateResponse, name=template)
    # Convert slider position to actual byte size
    max_file_size = log_slider_to_size(slider_position)

    query: Optional[IngestionQuery] = None # Initialize query

    # Prepare initial context for the template (passed back on success or error)
    context = {
        "request": request,
        "repo_url": input_text, # Keep original input for display
        "examples": EXAMPLE_REPOS if is_index else [],
        "default_file_size": slider_position,
        "pattern_type": pattern_type,
        "pattern": pattern,
        "branch_or_tag": branch_or_tag, # Pass back to template
    }

    try:
        # --- Call the core ingest function ---
        # Pass the branch_or_tag directly to the 'branch' parameter of ingest_async
        # ingest_async handles the logic of cloning (if URL) or direct processing (if local)
        # and prioritizes the passed 'branch' over any parsed from the URL.
        summary, tree, content = await ingest_async(
            source=input_text,
            max_file_size=max_file_size,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            # --- Pass branch_or_tag to ingest_async ---
            branch=branch_or_tag if branch_or_tag else None,
            output=None # We handle saving the digest separately
        )

        # --- Parse Query Again (for ID and display path) ---
        # We parse again *after* successful ingestion to get the generated ID
        # and the canonical path/URL for display and download link generation.
        # This avoids issues if parsing failed initially but ingest_async handled it.
        # It's slightly inefficient but ensures we have the ID from the successful run.
        query = await parse_query(
            source=input_text,
            max_file_size=max_file_size, # Max size doesn't affect ID/slug parsing
            from_web=False, # Let it detect
            include_patterns=include_patterns, # Pass patterns for consistency if needed
            ignore_patterns=exclude_patterns,
        )
        # We need the query object mainly for its generated ID and potentially the slug/URL

        # --- Save Digest File ---
        # Always save the digest to a file for the download button
        temp_digest_dir = TMP_BASE_PATH / query.id
        os.makedirs(temp_digest_dir, exist_ok=True)
        digest_filename = "digest.txt"
        digest_path = temp_digest_dir / digest_filename
        try:
            with open(digest_path, "w", encoding="utf-8") as f:
                f.write(tree + "\n" + content)
        except OSError as e:
             print(f"Error writing digest file {digest_path}: {e}")
             query.id = None # Invalidate ID if file couldn't be written


    except Exception as exc:
        # Log the error with context
        url_or_path = input_text # Use original input for error message
        # --- Pass branch_or_tag to error logging ---
        _print_error(url_or_path, exc, max_file_size, pattern_type, pattern, branch_or_tag)

        context["error_message"] = f"Error processing '{url_or_path}': {exc}"
        # Improve error message for common issues
        if "Repository not found" in str(exc) or "404" in str(exc) or "405" in str(exc):
             context["error_message"] = (
                f"Error: Could not access '{url_or_path}'. Please ensure the URL is correct and public, "
                # --- Add branch/tag context to error message ---
                f"or that the branch/tag/commit '{branch_or_tag}' exists (if specified), "
                "or that the local path exists and is accessible."
             )
        elif "Local path not found" in str(exc):
             context["error_message"] = f"Error: Local path not found: {input_text}"
        elif isinstance(exc, ValueError) and "invalid characters" in str(exc):
             context["error_message"] = f"Error: Invalid pattern provided. {exc}"
        elif "timed out" in str(exc).lower():
             context["error_message"] = f"Error: Operation timed out processing '{url_or_path}'. The repository might be too large or the network connection slow."
        # Add more specific error handling as needed

        # Set result to False explicitly on error to avoid showing results section
        context["result"] = False
        return template_response(context=context)

    # --- Success ---
    # Determine display path after successful processing using the parsed query
    display_path = query.url if query.url else str(query.local_path)
    if len(content) > MAX_DISPLAY_SIZE:
        content = (
            f"(Files content cropped to {int(MAX_DISPLAY_SIZE / 1_000)}k characters. "
            f"Download full ingest to see more)\n" + content[:MAX_DISPLAY_SIZE]
        )

    # --- Pass branch_or_tag to success logging ---
    _print_success(
        url_or_path=display_path,
        max_file_size=max_file_size,
        pattern_type=pattern_type,
        pattern=pattern,
        branch_or_tag=branch_or_tag,
        summary=summary,
    )

    # Update context for successful response
    context.update(
        {
            "result": True,
            "summary": summary,
            "tree": tree,
            "content": content,
            "ingest_id": query.id, # Pass the ID for the download link
            "is_local_path": not query.url # Flag for template logic (optional)
            # branch_or_tag is already in context from the start
        }
    )

    return template_response(context=context)


# --- Updated logging functions to include branch_or_tag ---
def _print_query(url_or_path: str, max_file_size: int, pattern_type: str, pattern: str, branch_or_tag: str = "") -> None:
    """
    Print a formatted summary of the query details.
    """
    print(f"{Colors.WHITE}{url_or_path:<50}{Colors.END}", end="")
    if branch_or_tag:
        print(f" | {Colors.CYAN}Ref: {branch_or_tag}{Colors.END}", end="") # Log branch/tag/commit
    if int(max_file_size / 1024) != 50: # Assuming 50k is default size threshold
        print(f" | {Colors.YELLOW}Size: {int(max_file_size/1024)}kb{Colors.END}", end="")
    if pattern: # Only print if pattern is not empty
        ptype = "Include" if pattern_type == "include" else "Exclude"
        print(f" | {Colors.YELLOW}{ptype}: '{pattern}'{Colors.END}", end="")


def _print_error(url_or_path: str, e: Exception, max_file_size: int, pattern_type: str, pattern: str, branch_or_tag: str = "") -> None:
    """
    Print a formatted error message.
    """
    print(f"{Colors.BROWN}WARN{Colors.END}: {Colors.RED}<- Process Failed {Colors.END}", end="")
    _print_query(url_or_path, max_file_size, pattern_type, pattern, branch_or_tag)
    print(f" | {Colors.RED}{type(e).__name__}: {e}{Colors.END}")


def _print_success(url_or_path: str, max_file_size: int, pattern_type: str, pattern: str, summary: str, branch_or_tag: str = "") -> None:
    """
    Print a formatted success message.
    """
    try:
        token_line = next((line for line in summary.splitlines() if "Estimated tokens:" in line), None)
        estimated_tokens = token_line.split(":", 1)[1].strip() if token_line else "N/A"
    except Exception:
        estimated_tokens = "N/A"

    print(f"{Colors.GREEN}INFO{Colors.END}: {Colors.GREEN}<- Process OK    {Colors.END}", end="")
    _print_query(url_or_path, max_file_size, pattern_type, pattern, branch_or_tag)
    print(f" | {Colors.PURPLE}Tokens: {estimated_tokens}{Colors.END}")

