# src/server/query_processor.py
"""Process a query by parsing input, cloning a repository, and generating a summary."""

import os
import re # Import re for sanitization
from functools import partial
from pathlib import Path
from typing import Optional # Import Optional

from fastapi import Request, UploadFile # Import UploadFile
from starlette.templating import _TemplateResponse
from urllib.parse import quote # Import quote for URL encoding query param

# --- Core CodeIngest imports ---
from CodeIngest.entrypoint import ingest_async
from CodeIngest.config import TMP_BASE_PATH, MAX_FILE_SIZE as DEFAULT_MAX_FILE_SIZE # Import default size

# --- Server specific imports ---
from server.server_config import EXAMPLE_REPOS, MAX_DISPLAY_SIZE, templates
from server.server_utils import Colors, log_slider_to_size

# --- Added: Define paths for zip handling (if needed later) ---
# You might need to adjust this path based on your deployment
RAW_UPLOADS_PATH = TMP_BASE_PATH / "uploads"
EXTRACTED_ZIPS_PATH = TMP_BASE_PATH / "extracted"
# Ensure these directories exist
RAW_UPLOADS_PATH.mkdir(parents=True, exist_ok=True)
EXTRACTED_ZIPS_PATH.mkdir(parents=True, exist_ok=True)
# --- End Added ---

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
    # Updated signature: Add source_type, make input_text optional, add zip_file
    source_type: str,
    input_text: Optional[str],
    zip_file: Optional[UploadFile],
    # End updated signature
    slider_position: int,
    pattern_type: str = "exclude",
    pattern: str = "",
    branch_or_tag: str = "",
    is_index: bool = False,
) -> _TemplateResponse:
    """
    Process a query (from URL/path or ZIP), generate summary, save digest, and prepare response.
    Includes dynamic download filename based on project and branch/tag.
    """
    # --- Input Validation & Source Determination ---
    source_for_ingest: Optional[str] = None
    if source_type == "url_path":
        if not input_text:
             # Handle error: URL/path source type requires input_text
            return templates.TemplateResponse(
                "index.jinja" if is_index else "git.jinja",
                {
                    "request": request,
                    "error_message": "Please provide a URL or local path.",
                    # Pass back other form values
                    "repo_url": input_text,
                    "examples": EXAMPLE_REPOS if is_index else [],
                    "default_file_size": slider_position,
                    "pattern_type": pattern_type,
                    "pattern": pattern,
                    "branch_or_tag": branch_or_tag,
                },
                status_code=400
            )
        source_for_ingest = input_text
        effective_input_display = input_text # For context and logging
    elif source_type == "zip_file":
        if not zip_file or not zip_file.filename:
            # Handle error: zip_file source type requires a file
             return templates.TemplateResponse(
                "index.jinja" if is_index else "git.jinja",
                 {
                    "request": request,
                    "error_message": "Please upload a ZIP file.",
                    # Pass back other form values
                    "repo_url": input_text,
                    "examples": EXAMPLE_REPOS if is_index else [],
                    "default_file_size": slider_position,
                    "pattern_type": pattern_type,
                    "pattern": pattern,
                    "branch_or_tag": branch_or_tag,
                 },
                status_code=400
            )

        # --- Basic Zip Handling (Placeholder) ---
        # NOTE: Requires actual implementation for saving & extracting zip
        # For now, we'll just use the filename as a placeholder source
        # In a real scenario, save the zip, extract it to a unique temp dir,
        # and set source_for_ingest to the path of the extracted directory.
        # Example (conceptual, needs zipfile library, async file ops):
        # temp_zip_save_path = RAW_UPLOADS_PATH / zip_file.filename
        # with open(temp_zip_save_path, "wb") as buffer:
        #     shutil.copyfileobj(zip_file.file, buffer)
        # extracted_path = EXTRACTED_ZIPS_PATH / Path(zip_file.filename).stem
        # with zipfile.ZipFile(temp_zip_save_path, 'r') as zip_ref:
        #     zip_ref.extractall(extracted_path)
        # source_for_ingest = str(extracted_path)

        # Placeholder: Using filename, ingest will likely fail unless it's a valid path
        source_for_ingest = zip_file.filename
        effective_input_display = f"ZIP: {zip_file.filename}" # For context/logging
        # ZIP files don't have branches/tags in the same way Git repos do
        branch_or_tag = "" # Clear branch/tag for zip uploads
        # --- End Basic Zip Handling ---

    else:
        # Handle error: Invalid source_type
         return templates.TemplateResponse(
             "index.jinja" if is_index else "git.jinja",
            {
                "request": request,
                "error_message": "Invalid source type specified.",
                # Pass back other form values
                 "repo_url": input_text,
                 "examples": EXAMPLE_REPOS if is_index else [],
                 "default_file_size": slider_position,
                 "pattern_type": pattern_type,
                 "pattern": pattern,
                 "branch_or_tag": branch_or_tag,
            },
            status_code=400
         )

    # --- End Input Validation & Source Determination ---


    if pattern_type == "include":
        include_patterns = pattern
        exclude_patterns = None
    elif pattern_type == "exclude":
        exclude_patterns = pattern
        include_patterns = None
    else:
        # This case might be redundant if pattern_type comes from select, but good practice
        raise ValueError(f"Invalid pattern type: {pattern_type}")

    template = "index.jinja" if is_index else "git.jinja"
    template_response = partial(templates.TemplateResponse, name=template)
    max_file_size = log_slider_to_size(slider_position)

    context = {
        "request": request,
        "repo_url": input_text or "", # Use input_text for display even if zip
        "examples": EXAMPLE_REPOS if is_index else [],
        "default_file_size": slider_position,
        "pattern_type": pattern_type,
        "pattern": pattern,
        "branch_or_tag": branch_or_tag, # Display user's original branch input
        "result": False, # Default to no result
        "error_message": None,
        "summary": None,
        "tree": None,
        "content": None,
        "ingest_id": None,
        "is_local_path": False, # Will be updated later
        "encoded_download_filename": None,
    }

    query_obj_from_ingest = None # Initialize

    try:
        # --- Call the core ingest function ---
        summary, tree, content, query_obj_from_ingest = await ingest_async(
            source=source_for_ingest, # Use the determined source
            max_file_size=max_file_size,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            # Pass branch_or_tag only if it's relevant (not zip)
            branch=branch_or_tag if source_type == 'url_path' and branch_or_tag else None,
            output=None  # Server handles output saving
        )

        # --- Create Temp Dir and Save Digest ---
        if not query_obj_from_ingest or not query_obj_from_ingest.id:
            _print_error(effective_input_display, Exception("Ingestion succeeded but query ID was not returned."), max_file_size, pattern_type, pattern, branch_or_tag)
            context["error_message"] = "An unexpected error occurred: Ingestion ID missing."
            return template_response(context=context)

        ingest_id_for_download = query_obj_from_ingest.id
        temp_digest_dir = TMP_BASE_PATH / ingest_id_for_download
        os.makedirs(temp_digest_dir, exist_ok=True) # Server is responsible for this ID-based dir

        internal_filename = "digest.txt"
        digest_path = temp_digest_dir / internal_filename
        try:
            with open(digest_path, "w", encoding="utf-8") as f:
                f.write(tree + "\n" + content)
        except OSError as e:
            print(f"Error writing digest file {digest_path}: {e}")
            ingest_id_for_download = None # Invalidate ID for download if saving failed
            context["error_message"] = f"Error saving digest: {e}"

        # --- Determine Download Filename ---
        filename_parts = []
        project_name_part = query_obj_from_ingest.repo_name if query_obj_from_ingest.url else query_obj_from_ingest.slug
        sanitized_project_name = sanitize_filename_part(project_name_part)

        if sanitized_project_name:
            filename_parts.append(sanitized_project_name)
        else:
            filename_parts.append("digest") # Fallback

        ref_for_filename = branch_or_tag if branch_or_tag else query_obj_from_ingest.branch

        # Only add ref if it's not a zip upload and ref exists
        if source_type != 'zip_file' and query_obj_from_ingest.url and ref_for_filename:
            sanitized_ref = sanitize_filename_part(ref_for_filename)
            if sanitized_ref:
                filename_parts.append(sanitized_ref)
        elif source_type != 'zip_file' and query_obj_from_ingest.url and query_obj_from_ingest.commit and not branch_or_tag:
            sanitized_commit = sanitize_filename_part(query_obj_from_ingest.commit[:7]) # Short commit
            if sanitized_commit:
                filename_parts.append(sanitized_commit)

        download_filename = "_".join(filename_parts) + ".txt"
        encoded_download_filename = quote(download_filename)

        # --- Update context for success ---
        # Determine display path (URL or local path, not zip path)
        display_path = query_obj_from_ingest.url if query_obj_from_ingest.url else str(query_obj_from_ingest.local_path)
        if len(content) > MAX_DISPLAY_SIZE:
            content_to_display = (
                f"(Files content cropped to {int(MAX_DISPLAY_SIZE / 1_000)}k characters. "
                f"Download full ingest to see more)\n" + content[:MAX_DISPLAY_SIZE]
            )
        else:
            content_to_display = content

        _print_success(
            url_or_path=effective_input_display, # Use effective input for logging
            max_file_size=max_file_size,
            pattern_type=pattern_type,
            pattern=pattern,
            summary=summary,
            branch_or_tag=branch_or_tag, # Log the user's input ref
        )

        context.update(
            {
                "result": True,
                "summary": summary,
                "tree": tree,
                "content": content_to_display,
                "ingest_id": ingest_id_for_download, # Use the consistent ID
                "is_local_path": not query_obj_from_ingest.url and source_type != 'zip_file', # Check it's not URL and not zip
                "encoded_download_filename": encoded_download_filename if ingest_id_for_download else None # Only provide if save succeeded
            }
        )
        return template_response(context=context)

    except Exception as exc:
        # url_or_path = input_text # Original input text - use effective_input_display
        _print_error(effective_input_display, exc, max_file_size, pattern_type, pattern, branch_or_tag)
        context["error_message"] = f"Error processing '{effective_input_display}': {exc}"
        # --- More specific error messages ---
        str_exc = str(exc).lower()
        if "repository not found" in str_exc or "404" in str_exc or "405" in str_exc:
             context["error_message"] = (
                f"Error: Could not access '{effective_input_display}'. Please ensure the URL is correct and public, "
                f"or that the branch/tag/commit '{branch_or_tag}' exists (if specified), "
                "or that the local path exists and is accessible."
             )
        elif "local path not found" in str_exc:
             context["error_message"] = f"Error: Local path not found: {effective_input_display}"
        elif isinstance(exc, ValueError) and "invalid characters" in str_exc:
             context["error_message"] = f"Error: Invalid pattern provided. {exc}"
        elif "timed out" in str_exc:
             context["error_message"] = f"Error: Operation timed out processing '{effective_input_display}'. The repository/ZIP might be too large or the network connection slow."
        elif "zipfile.badzipfile" in str_exc: # Example for zip error
             context["error_message"] = f"Error: The uploaded file '{effective_input_display}' is not a valid ZIP file."
        # --- End specific error messages ---

        # context["result"] is already False
        return template_response(context=context)


# Logging functions remain the same
def _print_query(url_or_path: str, max_file_size: int, pattern_type: str, pattern: str, branch_or_tag: str = "") -> None:
    print(f"{Colors.WHITE}{url_or_path:<50}{Colors.END}", end="")
    if branch_or_tag:
        print(f" | {Colors.CYAN}Ref: {branch_or_tag}{Colors.END}", end="")
    # Compare against the default slider value's log size equivalent if needed
    default_log_size = log_slider_to_size(243) # Assuming 243 is the default slider pos
    if max_file_size != default_log_size:
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