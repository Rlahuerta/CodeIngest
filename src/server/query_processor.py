# src/server/query_processor.py
"""Process a query by parsing input, cloning a repository, and generating a summary."""

import os
import re
from functools import partial
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import Request, UploadFile
from starlette.templating import _TemplateResponse
from urllib.parse import quote
import zipfile # Ensure zipfile is imported
import shutil  # Ensure shutil is imported

# --- Core CodeIngest imports ---
from CodeIngest.entrypoint import ingest_async
from CodeIngest.config import TMP_BASE_PATH
from CodeIngest.output_formatters import TreeDataItem

# --- Server specific imports ---
from server.server_config import EXAMPLE_REPOS, MAX_DISPLAY_SIZE, templates
from server.server_utils import Colors, log_slider_to_size

# Define paths for zip handling
RAW_UPLOADS_PATH = TMP_BASE_PATH / "uploads"
EXTRACTED_ZIPS_PATH = TMP_BASE_PATH / "extracted"
RAW_UPLOADS_PATH.mkdir(parents=True, exist_ok=True)
EXTRACTED_ZIPS_PATH.mkdir(parents=True, exist_ok=True)

def sanitize_filename_part(part: str) -> str:
    """Removes or replaces characters unsafe for filenames."""
    if not part: return ""
    part = re.sub(r'[\\/*?:"<>|]+', '', part)
    part = re.sub(r'[^a-zA-Z0-9._-]+', '_', part)
    part = part.strip('._')
    return part[:50]


async def process_query(
    request: Request,
    source_type: str,
    input_text: Optional[str], # For url_path OR path to the saved ZIP file
    zip_file: Optional[UploadFile], # Original UploadFile object for metadata
    slider_position: int,
    pattern_type: str = "exclude",
    pattern: str = "",
    branch_or_tag: str = "",
    is_index: bool = False,
) -> _TemplateResponse:
    """
    Process a query (from URL/path or ZIP), generate summary, save digest, and prepare response.
    """
    source_for_ingest: Optional[str] = None
    effective_input_display = ""
    original_filename_for_slug = None # For ZIPs, to create a nice slug for display

    # Determine the actual source for ingestion based on source_type
    if source_type == "url_path":
        if not input_text:
            return templates.TemplateResponse(
                "index.jinja" if is_index else "git.jinja",
                {"request": request, "error_message": "Please provide a URL or local path.",
                 "repo_url": input_text, "examples": EXAMPLE_REPOS if is_index else [],
                 "default_file_size": slider_position, "pattern_type": pattern_type,
                 "pattern": pattern, "branch_or_tag": branch_or_tag, "source_type": source_type},
                status_code=400
            )
        source_for_ingest = input_text
        effective_input_display = input_text
    elif source_type == "zip_file":
        # 'input_text' should be the path to the saved zip file, set by the router
        if not input_text or not Path(input_text).is_file():
            return templates.TemplateResponse(
                "index.jinja" if is_index else "git.jinja",
                {"request": request, "error_message": "Uploaded ZIP file path is missing or invalid.",
                 "repo_url": None, "examples": EXAMPLE_REPOS if is_index else [],
                 "default_file_size": slider_position, "pattern_type": pattern_type,
                 "pattern": pattern, "branch_or_tag": branch_or_tag, "source_type": source_type},
                status_code=400
            )
        source_for_ingest = input_text # This is the path to the saved zip
        original_filename_for_slug = zip_file.filename if zip_file else Path(input_text).name
        effective_input_display = f"ZIP: {original_filename_for_slug}"
        branch_or_tag = "" # Clear branch/tag for ZIPs as it's not applicable
    else:
         return templates.TemplateResponse(
             "index.jinja" if is_index else "git.jinja",
             {"request": request, "error_message": "Invalid source type specified.",
              "repo_url": input_text, "examples": EXAMPLE_REPOS if is_index else [],
              "default_file_size": slider_position, "pattern_type": pattern_type,
              "pattern": pattern, "branch_or_tag": branch_or_tag, "source_type": source_type},
             status_code=400
         )

    if pattern_type == "include":
        include_patterns = pattern
        exclude_patterns = None
    elif pattern_type == "exclude":
        exclude_patterns = pattern
        include_patterns = None
    else:
        # This should ideally not be reached if pattern_type comes from a select element
        raise ValueError(f"Invalid pattern type: {pattern_type}")

    template = "index.jinja" if is_index else "git.jinja"
    max_file_size = log_slider_to_size(slider_position)

    # Initialize context for the template
    context = {
        "request": request,
        "repo_url": input_text if source_type == "url_path" else original_filename_for_slug, # Display original input or zip name
        "examples": EXAMPLE_REPOS if is_index else [],
        "default_file_size": slider_position,
        "pattern_type": pattern_type,
        "pattern": pattern,
        "branch_or_tag": branch_or_tag, # Display user's original branch input
        "result": False, "error_message": None, "summary": None,
        "tree_data": None, "content": None, "ingest_id": None,
        "is_local_path": False, "encoded_download_filename": None,
        "base_repo_url": None, "repo_ref": None,
    }
    query_obj_from_ingest = None

    try:
        # Call the core ingest function
        summary, tree_data, content_str, query_obj_from_ingest = await ingest_async(
            source=source_for_ingest, # This is now the correct full path to URL, local dir, or saved ZIP
            max_file_size=max_file_size,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            branch=branch_or_tag if source_type == 'url_path' and branch_or_tag else None,
            output=None
        )

        if not query_obj_from_ingest or not query_obj_from_ingest.id:
            _print_error(effective_input_display, Exception("Ingestion succeeded but query ID was not returned."), max_file_size, pattern_type, pattern, branch_or_tag)
            context["error_message"] = "An unexpected error occurred: Ingestion ID missing."
            return templates.TemplateResponse(template, context=context)

        ingest_id_for_download = query_obj_from_ingest.id
        temp_digest_dir = TMP_BASE_PATH / ingest_id_for_download
        os.makedirs(temp_digest_dir, exist_ok=True)
        internal_filename = "digest.txt"
        digest_path = temp_digest_dir / internal_filename

        try:
            with open(digest_path, "w", encoding="utf-8") as f:
                 f.write("Directory structure:\n")
                 for item in tree_data:
                     f.write(f"{item['prefix']}{item['name']}\n")
                 f.write("\n" + content_str)
        except OSError as e:
            print(f"Error writing digest file {digest_path}: {e}")
            ingest_id_for_download = None # Invalidate if save failed
            context["error_message"] = f"Error saving digest: {e}"


        # Determine Download Filename
        filename_parts = []
        project_name_part = query_obj_from_ingest.slug # Slug from IngestionQuery (e.g., zip filename stem or repo name)
        sanitized_project_name = sanitize_filename_part(project_name_part)
        filename_parts.append(sanitized_project_name or "digest")

        # For remote repos, add branch/tag/commit if provided
        ref_for_filename = branch_or_tag if (source_type == 'url_path' and branch_or_tag) else query_obj_from_ingest.branch
        if source_type == 'url_path' and query_obj_from_ingest.url: # It's a remote repo
            if ref_for_filename:
                sanitized_ref = sanitize_filename_part(ref_for_filename)
                if sanitized_ref: filename_parts.append(sanitized_ref)
            elif query_obj_from_ingest.commit: # Fallback to commit if no specific ref for filename
                sanitized_commit = sanitize_filename_part(query_obj_from_ingest.commit[:7])
                if sanitized_commit: filename_parts.append(sanitized_commit)

        download_filename = "_".join(filename_parts) + ".txt"
        encoded_download_filename = quote(download_filename)

        # Prepare content for display (cropping if too large)
        content_to_display = content_str[:MAX_DISPLAY_SIZE] + ("\n(Files content cropped to first characters...)" if len(content_str) > MAX_DISPLAY_SIZE else "")

        _print_success(
            url_or_path=effective_input_display, max_file_size=max_file_size,
            pattern_type=pattern_type, pattern=pattern, summary=summary,
            branch_or_tag=branch_or_tag # Log the user's original input for ref
        )

        context.update({
            "result": True, "summary": summary, "tree_data": tree_data,
            "content": content_to_display, "ingest_id": ingest_id_for_download,
            "is_local_path": not query_obj_from_ingest.url and source_type != 'zip_file', # True if local dir/file
            "encoded_download_filename": encoded_download_filename if ingest_id_for_download else None,
            "base_repo_url": query_obj_from_ingest.url if query_obj_from_ingest.url else None,
            "repo_ref": query_obj_from_ingest.branch or query_obj_from_ingest.commit or 'main',
        })
        return templates.TemplateResponse(template, context=context)

    except Exception as exc:
        _print_error(effective_input_display, exc, max_file_size, pattern_type, pattern, branch_or_tag)
        context["error_message"] = f"Error processing '{effective_input_display}': {exc}"
        str_exc = str(exc).lower()
        if "repository not found" in str_exc or "404" in str_exc or "405" in str_exc:
            context["error_message"] = (f"Error: Could not access '{effective_input_display}'. Ensure URL/path is correct, public, and branch/tag/commit exists.")
        elif "local path not found" in str_exc:
             context["error_message"] = f"Error: Local path not found: {effective_input_display}"
        elif isinstance(exc, ValueError) and "invalid characters" in str_exc:
             context["error_message"] = f"Error: Invalid pattern provided. {exc}"
        elif "timed out" in str_exc:
             context["error_message"] = f"Error: Operation timed out processing '{effective_input_display}'."
        elif "zipfile.badzipfile" in str_exc or "invalid zip file" in str_exc :
             context["error_message"] = f"Error: The uploaded file '{effective_input_display}' is not a valid ZIP file."
        return templates.TemplateResponse(template, context=context)


# Logging functions
def _print_query(url_or_path: str, max_file_size: int, pattern_type: str, pattern: str, branch_or_tag: str = "") -> None:
    print(f"{Colors.WHITE}{url_or_path:<50}{Colors.END}", end="")
    if branch_or_tag: print(f" | {Colors.CYAN}Ref: {branch_or_tag}{Colors.END}", end="")
    default_log_size = log_slider_to_size(243)
    if max_file_size != default_log_size: print(f" | {Colors.YELLOW}Size: {int(max_file_size/1024)}kb{Colors.END}", end="")
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
    except Exception: estimated_tokens = "N/A"
    print(f"{Colors.GREEN}INFO{Colors.END}: {Colors.GREEN}<- Process OK    {Colors.END}", end="")
    _print_query(url_or_path, max_file_size, pattern_type, pattern, branch_or_tag)
    print(f" | {Colors.PURPLE}Tokens: {estimated_tokens}{Colors.END}")