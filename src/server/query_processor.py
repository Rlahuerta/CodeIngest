# src/server/query_processor.py
"""Process a query by parsing input, cloning a repository, and generating a summary."""

import os
import shutil
import re # Import re for sanitization
import uuid # Import uuid
import logging # Import logging
from functools import partial
from pathlib import Path
from typing import Optional, Set # Import Set

# --- FastAPI / Starlette Imports ---
from fastapi import Form, Request, UploadFile, HTTPException, BackgroundTasks # Add UploadFile, HTTPException, BackgroundTasks
from starlette.templating import _TemplateResponse
from urllib.parse import quote # Import quote for URL encoding query param

# --- Core CodeIngest imports ---
from CodeIngest.entrypoint import ingest_async
from CodeIngest.query_parsing import IngestionQuery, parse_query, _parse_patterns # Import _parse_patterns
from CodeIngest.config import TMP_BASE_PATH

# --- Server specific imports ---
from server.server_config import EXAMPLE_REPOS, MAX_DISPLAY_SIZE, templates
from server.server_utils import Colors, log_slider_to_size

# --- Configure logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Define a path for raw zip uploads ---
# Ensure this path exists and is writable within your Docker container
# Consider using a volume mount for this in production
RAW_UPLOADS_PATH = Path("/tmp/codeingest_raw_uploads")
RAW_UPLOADS_PATH.mkdir(parents=True, exist_ok=True)


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

async def cleanup_temp_file(temp_file_path: Path):
    """Safely removes a temporary file and its parent directory if empty."""
    parent_dir = temp_file_path.parent
    try:
        if temp_file_path.exists() and temp_file_path.is_file():
            temp_file_path.unlink()
            logger.info(f"Cleaned up temporary upload file: {temp_file_path}")
        # Attempt to remove parent directory if it's empty
        if parent_dir.exists() and parent_dir.is_dir() and not any(parent_dir.iterdir()):
             # Check if it's within the expected raw uploads path to be safe
             if parent_dir.is_relative_to(RAW_UPLOADS_PATH):
                 parent_dir.rmdir()
                 logger.info(f"Cleaned up empty temporary upload directory: {parent_dir}")
             else:
                  logger.warning(f"Skipping cleanup of parent directory {parent_dir} as it's outside expected path.")
    except Exception as e:
        logger.error(f"Error during cleanup of {temp_file_path} or parent {parent_dir}: {e}", exc_info=True)


async def process_query(
    request: Request,
    # --- Updated parameters ---
    source_type: str,
    input_text: Optional[str],
    zip_file: Optional[UploadFile],
    # --- End updated parameters ---
    slider_position: int,
    pattern_type: str = "exclude",
    pattern: str = "",
    branch_or_tag: str = "",
    is_index: bool = False,
) -> _TemplateResponse:
    """
    Process a query (URL/path or ZIP upload), generate summary, save digest, and prepare response.
    """
    # --- Initialize variables ---
    query: Optional[IngestionQuery] = None
    temp_digest_dir: Optional[Path] = None
    repo_cloned_or_extracted = False # Flag for cleanup logic
    source_to_process: Optional[str] = None # Path to the item to be ingested (URL, local path, or *saved zip path*)
    temp_zip_save_path: Optional[Path] = None # Path where uploaded zip is temporarily saved
    background_tasks = BackgroundTasks() # Initialize background tasks
    include_patterns: Optional[Set[str]] = None
    exclude_patterns: Optional[Set[str]] = None

    # --- Parse include/exclude patterns early for use in ingest_async ---
    try:
        if pattern_type == "include":
            include_patterns = _parse_patterns(pattern) if pattern else None
            # If include patterns are provided, exclude patterns are typically ignored or start empty
            # Depending on desired logic, you might want to start with default excludes and remove included ones
            exclude_patterns = None # Or start with defaults and filter later
        elif pattern_type == "exclude":
            exclude_patterns = _parse_patterns(pattern) if pattern else None
            include_patterns = None
        else:
            # Fallback or error for invalid pattern_type
            logger.warning(f"Invalid pattern type received: {pattern_type}. Defaulting to exclude.")
            exclude_patterns = _parse_patterns(pattern) if pattern else None
            include_patterns = None
    except Exception as e: # Catch potential errors from _parse_patterns
         logger.error(f"Error parsing patterns: {e}", exc_info=True)
         # Handle error appropriately, maybe raise HTTPException or set default patterns
         raise HTTPException(status_code=400, detail=f"Invalid pattern format: {e}")


    # --- Determine source and prepare for ingestion ---
    if source_type == "url_path":
        if not input_text:
            raise HTTPException(status_code=400, detail="URL or Local Path is required.")
        source_to_process = input_text
        display_source = input_text # For context and logging
    elif source_type == "zip_file":
        if not zip_file:
            raise HTTPException(status_code=400, detail="ZIP file is required.")
        if not zip_file.filename or not zip_file.filename.lower().endswith('.zip'):
            raise HTTPException(status_code=400, detail="Invalid file type. Only ZIP files are accepted.")

        display_source = zip_file.filename # For context and logging

        # Create a unique temp dir for this specific upload
        upload_id = str(uuid.uuid4())
        temp_save_dir = RAW_UPLOADS_PATH / upload_id
        temp_save_dir.mkdir(parents=True, exist_ok=True)
        # Sanitize filename before saving
        safe_filename = sanitize_filename_part(zip_file.filename)
        if not safe_filename: # Handle case where filename becomes empty after sanitization
            safe_filename = "upload.zip"
        temp_zip_save_path = temp_save_dir / safe_filename


        try:
            logger.info(f"Saving uploaded file '{zip_file.filename}' to '{temp_zip_save_path}'")
            # Save the uploaded file temporarily
            with open(temp_zip_save_path, "wb") as buffer:
                shutil.copyfileobj(zip_file.file, buffer)
            logger.info(f"File saved successfully.")
            source_to_process = str(temp_zip_save_path) # Ingest will process this path
            # --- Schedule cleanup for the *saved zip file* ---
            # Pass the actual path to the saved file
            background_tasks.add_task(cleanup_temp_file, temp_zip_save_path)
        except Exception as e:
             logger.error(f"Failed to save uploaded file '{zip_file.filename}': {e}", exc_info=True)
             # Clean up if save failed
             if temp_zip_save_path and temp_zip_save_path.exists():
                 # Use await here if cleanup_temp_file becomes async
                 await cleanup_temp_file(temp_zip_save_path)
             raise HTTPException(status_code=500, detail=f"Failed to save uploaded file: {e}")
        finally:
            await zip_file.close() # Ensure file handle is closed

    else:
        raise HTTPException(status_code=400, detail=f"Invalid source_type: {source_type}")

    # --- Prepare context for template rendering ---
    template = "index.jinja" if is_index else "git.jinja"
    template_response = partial(templates.TemplateResponse, name=template)
    max_file_size = log_slider_to_size(slider_position)

    # Initial context (repo_url might be empty if zip upload)
    context = {
        "request": request,
        "repo_url": input_text if source_type == 'url_path' else "", # Pre-fill URL only if it was the source
        "examples": EXAMPLE_REPOS if is_index else [],
        "default_file_size": slider_position,
        "pattern_type": pattern_type,
        "pattern": pattern,
        "branch_or_tag": branch_or_tag,
        "source_type": source_type, # Pass source_type to template
        "result": False, # Default result status
        "error_message": None, # Default error message
    }

    # --- Start Ingestion Process ---
    try:
        if not source_to_process:
             # This should ideally not happen due to checks above, but as a safeguard
             raise ValueError("Source for processing could not be determined.")

        # --- Call the core ingest function ---
        # ingest_async now handles URL, local path, AND local zip path
        # Pass the parsed include/exclude patterns
        summary, tree, content = await ingest_async(
            source=source_to_process, # Pass URL, local path, or path to saved zip
            max_file_size=max_file_size,
            include_patterns=include_patterns, # Pass parsed patterns
            exclude_patterns=exclude_patterns, # Pass parsed patterns
            branch=branch_or_tag if branch_or_tag else None,
            output=None # Don't write output file from here
        )

        # --- Parse Query Again (for ID, URL info, slug/repo_name etc.) ---
        # This is needed to get the ID generated during ingestion (for clone/extract temp dirs)
        # and the final slug, repo name etc. after parsing
        # Pass the same patterns again for consistency, though parse_query might recalculate defaults
        query = await parse_query(
            source=source_to_process, # Use the same source path
            max_file_size=max_file_size,
            from_web=False, # Let parse_query determine type based on source_to_process
            include_patterns=include_patterns, # Pass patterns
            ignore_patterns=exclude_patterns,  # Pass patterns
        )

        # Determine if a temporary directory was created by ingest_async (for clone or zip extract)
        # Note: ingest_async's finally block handles cleanup of *these* dirs
        repo_cloned_or_extracted = bool(query.url or query.temp_extract_path)

        # --- Create Temp Dir and Save *Final* Digest for Download ---
        # This uses the ID generated by parse_query (related to clone/extract temp dir)
        if not query.id:
             # Handle case where query ID might be missing after parsing (e.g., error)
             logger.error("Query ID missing after parsing, cannot save digest for download.")
             raise ValueError("Failed to obtain a valid query ID for saving the digest.")

        temp_digest_dir = TMP_BASE_PATH / query.id
        temp_digest_dir.mkdir(parents=True, exist_ok=True) # Ensure digest dir exists

        internal_filename = "digest.txt"
        digest_path = temp_digest_dir / internal_filename
        try:
            with open(digest_path, "w", encoding="utf-8") as f:
                f.write(tree + "\n" + content)
            logger.info(f"Digest content saved to {digest_path}")
            # --- Schedule cleanup for the *digest file's directory* ---
            # This seems handled by server_utils._remove_old_repositories, so maybe not needed here?
            # background_tasks.add_task(cleanup_temp_directory, temp_digest_dir) # Re-evaluate if needed
        except OSError as e:
             logger.error(f"Error writing digest file {digest_path}: {e}", exc_info=True)
             # Invalidate ID in context if saving failed, so download link isn't shown
             context["ingest_id"] = None # Set context ID to None
             query.id = None # Also update query object if used later

        # --- Determine Download Filename ---
        filename_parts = []
        project_name_part = query.repo_name if query.url else query.slug
        sanitized_project_name = sanitize_filename_part(project_name_part)
        if sanitized_project_name:
             filename_parts.append(sanitized_project_name)
        else:
             filename_parts.append("digest") # Fallback

        # Add branch/tag/commit if it's a remote repo and a ref was provided/parsed
        ref_part = branch_or_tag if branch_or_tag else query.branch if query.branch else query.commit if query.commit else None
        if query.url and ref_part:
            sanitized_ref = sanitize_filename_part(ref_part)
            if sanitized_ref:
                 filename_parts.append(sanitized_ref)

        download_filename = "_".join(filename_parts) + ".txt"
        encoded_download_filename = quote(download_filename)

        # --- Success Context Update ---
        if len(content) > MAX_DISPLAY_SIZE:
            content = (
                f"(Files content cropped to {int(MAX_DISPLAY_SIZE / 1_000)}k characters. "
                f"Download full ingest to see more)\n" + content[:MAX_DISPLAY_SIZE]
            )

        _print_success(
            url_or_path=display_source, # Log the original source identifier
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
                "ingest_id": query.id, # Use potentially invalidated ID
                "is_local_path": not query.url, # True if source was local path or zip
                "encoded_download_filename": encoded_download_filename if query.id else None, # Only if ID is valid
                # Update repo_url in context if it was parsed from URL
                "repo_url": query.url if query.url else context.get("repo_url"),
            }
        )

    except Exception as exc:
        # --- Error Handling ---
        _print_error(display_source, exc, max_file_size, pattern_type, pattern, branch_or_tag)

        # Determine specific error message
        error_message = f"Error processing '{display_source}': {exc}" # Default
        if isinstance(exc, HTTPException):
             error_message = exc.detail # Use detail from HTTP exceptions
        elif "Repository not found" in str(exc) or "404" in str(exc) or "405" in str(exc):
             error_message = (
                f"Error: Could not access '{display_source}'. Please ensure the URL is correct and public, "
                f"or that the branch/tag/commit '{branch_or_tag}' exists (if specified), "
                "or that the local path/zip exists and is accessible."
             )
        elif "Local path not found" in str(exc) or "Target path" in str(exc):
             error_message = f"Error: Source path not found or inaccessible: {display_source}"
        elif isinstance(exc, ValueError) and "invalid characters" in str(exc):
             error_message = f"Error: Invalid pattern provided. {exc}"
        elif "timed out" in str(exc).lower():
             error_message = f"Error: Operation timed out processing '{display_source}'. The repository/zip might be too large or the network connection slow."
        elif "BadZipFile" in str(exc) or "Invalid or corrupted ZIP file" in str(exc):
             error_message = f"Error: The uploaded file '{display_source}' is invalid or corrupted."
        elif "Failed to extract ZIP file" in str(exc):
             error_message = f"Error: Failed to extract the contents of '{display_source}'."


        context["error_message"] = error_message
        context["result"] = False
        # Ensure the template still gets the necessary context even on error
        context["repo_url"] = input_text if source_type == 'url_path' else ""
        context["summary"] = None
        context["tree"] = None
        context["content"] = None
        context["ingest_id"] = None
        context["encoded_download_filename"] = None


    # --- Return response ---
    # Execute background tasks (like cleanup) after response is sent
    response = template_response(context=context)
    response.background = background_tasks
    return response


# --- Logging functions remain the same ---
def _print_query(url_or_path: str, max_file_size: int, pattern_type: str, pattern: str, branch_or_tag: str = "") -> None:
    # Limit length of displayed path/URL for cleaner logs
    display_path = url_or_path if len(url_or_path) < 70 else url_or_path[:67] + "..."
    print(f"{Colors.WHITE}{display_path:<70}{Colors.END}", end="")
    if branch_or_tag:
        print(f" | {Colors.CYAN}Ref: {branch_or_tag}{Colors.END}", end="")
    if int(max_file_size / 1024) != 50: # Assuming 50kb is the default display value equivalent
        print(f" | {Colors.YELLOW}Size: {int(max_file_size/1024)}kb{Colors.END}", end="")
    if pattern:
        ptype = "Include" if pattern_type == "include" else "Exclude"
        print(f" | {Colors.YELLOW}{ptype}: '{pattern}'{Colors.END}", end="")

def _print_error(url_or_path: str, e: Exception, max_file_size: int, pattern_type: str, pattern: str, branch_or_tag: str = "") -> None:
    print(f"{Colors.BROWN}WARN{Colors.END}: {Colors.RED}<- Process Failed {Colors.END}", end="")
    _print_query(url_or_path, max_file_size, pattern_type, pattern, branch_or_tag)
    # Print exception type and message, limit length
    error_str = f"{type(e).__name__}: {e}"
    print(f" | {Colors.RED}{error_str[:200]}{Colors.END}") # Limit error message length in log

def _print_success(url_or_path: str, max_file_size: int, pattern_type: str, pattern: str, summary: str, branch_or_tag: str = "") -> None:
    try:
        token_line = next((line for line in summary.splitlines() if "Estimated tokens:" in line), None)
        estimated_tokens = token_line.split(":", 1)[1].strip() if token_line else "N/A"
    except Exception:
        estimated_tokens = "N/A"
    print(f"{Colors.GREEN}INFO{Colors.END}: {Colors.GREEN}<- Process OK    {Colors.END}", end="")
    _print_query(url_or_path, max_file_size, pattern_type, pattern, branch_or_tag)
    print(f" | {Colors.PURPLE}Tokens: {estimated_tokens}{Colors.END}")

