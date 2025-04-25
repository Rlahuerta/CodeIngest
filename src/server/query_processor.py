"""Process a query by parsing input, cloning a repository, and generating a summary."""

import os
import shutil
import re # Import re for sanitization
from functools import partial
from pathlib import Path
from typing import Optional

from fastapi import Form, Request
from starlette.templating import _TemplateResponse
from urllib.parse import quote # Import quote for URL encoding query param

# --- Core CodeIngest imports ---
from CodeIngest.entrypoint import ingest_async
from CodeIngest.query_parsing import IngestionQuery, parse_query
from CodeIngest.config import TMP_BASE_PATH

# --- Server specific imports ---
from server.server_config import EXAMPLE_REPOS, MAX_DISPLAY_SIZE, templates
from server.server_utils import Colors, log_slider_to_size

def sanitize_filename_part(part: str) -> str:
    """Removes or replaces characters unsafe for filenames."""
    if not part: # Handle empty input
        return ""
    # Remove potentially problematic characters like slashes, backslashes, colons etc.
    part = re.sub(r'[\\/*?:"<>|]+', '', part)
    # Replace sequences of non-alphanumeric (excluding ., -, _) with a single underscore
    part = re.sub(r'[^a-zA-Z0-9._-]+', '_', part)
    # Avoid leading/trailing dots or underscores
    part = part.strip('._')
    # Limit length to avoid excessively long filenames
    return part[:50]


async def process_query(
    request: Request,
    input_text: str,
    slider_position: int,
    pattern_type: str = "exclude",
    pattern: str = "",
    branch_or_tag: str = "",
    is_index: bool = False,
) -> _TemplateResponse:
    """
    Process a query, generate summary, save digest, and prepare response.
    Includes dynamic download filename based on project and branch/tag.
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
    query: Optional[IngestionQuery] = None
    temp_digest_dir: Optional[Path] = None
    repo_cloned = False

    context = {
        "request": request,
        "repo_url": input_text,
        "examples": EXAMPLE_REPOS if is_index else [],
        "default_file_size": slider_position,
        "pattern_type": pattern_type,
        "pattern": pattern,
        "branch_or_tag": branch_or_tag,
    }

    try:
        # --- Call the core ingest function ---
        summary, tree, content = await ingest_async(
            source=input_text,
            max_file_size=max_file_size,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            branch=branch_or_tag if branch_or_tag else None,
            output=None
        )

        # --- Parse Query Again (for ID, URL info, slug/repo_name) ---
        query = await parse_query(
            source=input_text,
            max_file_size=max_file_size,
            from_web=False,
            include_patterns=include_patterns,
            ignore_patterns=exclude_patterns,
        )

        repo_cloned = bool(query.url)

        # --- Create Temp Dir and Save Digest ---
        temp_digest_dir = TMP_BASE_PATH / query.id
        os.makedirs(temp_digest_dir, exist_ok=True)
        internal_filename = "digest.txt"
        digest_path = temp_digest_dir / internal_filename
        try:
            with open(digest_path, "w", encoding="utf-8") as f:
                f.write(tree + "\n" + content)
        except OSError as e:
             print(f"Error writing digest file {digest_path}: {e}")
             query.id = None

        # --- Determine Download Filename ---
        # --- FIX: Start with an empty list, don't add hardcoded prefix ---
        filename_parts = []

        # Add project name (repo_name for remote, slug for local)
        project_name_part = query.repo_name if query.url else query.slug
        # Sanitize the project name part
        sanitized_project_name = sanitize_filename_part(project_name_part)
        if sanitized_project_name: # Add only if not empty after sanitization
             filename_parts.append(sanitized_project_name)
        else: # Fallback if project name is invalid or empty
             filename_parts.append("digest")

        # Add branch/tag/commit if it's a remote repo and a ref was provided
        if query.url and branch_or_tag:
            sanitized_ref = sanitize_filename_part(branch_or_tag)
            if sanitized_ref: # Ensure it's not empty after sanitization
                 filename_parts.append(sanitized_ref)

        # Join parts with underscore and add extension
        download_filename = "_".join(filename_parts) + ".txt"

        # URL encode the final filename for use in the query parameter
        encoded_download_filename = quote(download_filename)


    except Exception as exc:
        url_or_path = input_text
        _print_error(url_or_path, exc, max_file_size, pattern_type, pattern, branch_or_tag)
        context["error_message"] = f"Error processing '{url_or_path}': {exc}"
        # Specific error messages...
        if "Repository not found" in str(exc) or "404" in str(exc) or "405" in str(exc):
             context["error_message"] = (
                f"Error: Could not access '{url_or_path}'. Please ensure the URL is correct and public, "
                f"or that the branch/tag/commit '{branch_or_tag}' exists (if specified), "
                "or that the local path exists and is accessible."
             )
        elif "Local path not found" in str(exc):
             context["error_message"] = f"Error: Local path not found: {input_text}"
        elif isinstance(exc, ValueError) and "invalid characters" in str(exc):
             context["error_message"] = f"Error: Invalid pattern provided. {exc}"
        elif "timed out" in str(exc).lower():
             context["error_message"] = f"Error: Operation timed out processing '{url_or_path}'. The repository might be too large or the network connection slow."

        context["result"] = False
        return template_response(context=context)

    finally:
         # --- Cleanup for Cloned Repos ---
         if repo_cloned and query and query.local_path.is_relative_to(TMP_BASE_PATH):
             # Check if local_path exists before trying to remove
             if query.local_path.exists():
                shutil.rmtree(query.local_path, ignore_errors=True)


    # --- Success ---
    display_path = query.url if query.url else str(query.local_path)
    if len(content) > MAX_DISPLAY_SIZE:
        content = (
            f"(Files content cropped to {int(MAX_DISPLAY_SIZE / 1_000)}k characters. "
            f"Download full ingest to see more)\n" + content[:MAX_DISPLAY_SIZE]
        )

    _print_success(
        url_or_path=display_path,
        max_file_size=max_file_size,
        pattern_type=pattern_type,
        pattern=pattern,
        branch_or_tag=branch_or_tag,
        summary=summary,
    )

    context.update(
        {
            "result": True,
            "summary": summary,
            "tree": tree,
            "content": content,
            "ingest_id": query.id,
            "is_local_path": not query.url,
            "encoded_download_filename": encoded_download_filename
        }
    )

    return template_response(context=context)


# Logging functions remain the same
def _print_query(url_or_path: str, max_file_size: int, pattern_type: str, pattern: str, branch_or_tag: str = "") -> None:
    print(f"{Colors.WHITE}{url_or_path:<50}{Colors.END}", end="")
    if branch_or_tag:
        print(f" | {Colors.CYAN}Ref: {branch_or_tag}{Colors.END}", end="")
    if int(max_file_size / 1024) != 50:
        print(f" | {Colors.YELLOW}Size: {int(max_file_size/1024)}kb{Colors.END}", end="")
    if pattern:
        ptype = "Include" if pattern_type == "include" else "Exclude"
        print(f" | {Colors.YELLOW}{ptype}: '{pattern}'{Colors.END}", end="")

def _print_error(url_or_path: str, e: Exception, max_file_size: int, pattern_type: str, pattern: str, branch_or_tag: str = "") -> None:
    print(f"{Colors.BROWN}WARN{Colors.END}: {Colors.RED}<- Process Failed {Colors.END}", end="")
    _print_query(url_or_path, max_file_size, pattern_type, pattern, branch_or_tag)
    print(f" | {Colors.RED}{type(e).__name__}: {e}{Colors.END}")

def _print_success(url_or_path: str, max_file_size: int, pattern_type: str, pattern: str, summary: str, branch_or_tag: str = "") -> None:
    try:
        token_line = next((line for line in summary.splitlines() if "Estimated tokens:" in line), None)
        estimated_tokens = token_line.split(":", 1)[1].strip() if token_line else "N/A"
    except Exception:
        estimated_tokens = "N/A"
    print(f"{Colors.GREEN}INFO{Colors.END}: {Colors.GREEN}<- Process OK    {Colors.END}", end="")
    _print_query(url_or_path, max_file_size, pattern_type, pattern, branch_or_tag)
    print(f" | {Colors.PURPLE}Tokens: {estimated_tokens}{Colors.END}")
