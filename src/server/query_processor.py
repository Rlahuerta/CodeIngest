# src/server/query_processor.py
"""Process a query by parsing input, cloning a repository, and generating a summary."""

import os
import re # Import re for sanitization
from functools import partial
from pathlib import Path
from typing import Optional, List, Dict, Any # Added List, Dict, Any

from fastapi import Request, UploadFile
from starlette.templating import _TemplateResponse
from urllib.parse import quote

# --- Core CodeIngest imports ---
from CodeIngest.entrypoint import ingest_async
from CodeIngest.config import TMP_BASE_PATH
from CodeIngest.output_formatters import TreeDataItem # Import type alias

# --- Server specific imports ---
from server.server_config import EXAMPLE_REPOS, MAX_DISPLAY_SIZE, templates
from server.server_utils import Colors, log_slider_to_size

# Define paths for zip handling (if needed later)
RAW_UPLOADS_PATH = TMP_BASE_PATH / "uploads"
EXTRACTED_ZIPS_PATH = TMP_BASE_PATH / "extracted"
RAW_UPLOADS_PATH.mkdir(parents=True, exist_ok=True)
EXTRACTED_ZIPS_PATH.mkdir(parents=True, exist_ok=True)

def sanitize_filename_part(part: str) -> str:
    """Removes or replaces characters unsafe for filenames."""
    # (Implementation remains the same)
    if not part: return ""
    part = re.sub(r'[\\/*?:"<>|]+', '', part)
    part = re.sub(r'[^a-zA-Z0-9._-]+', '_', part)
    part = part.strip('._')
    return part[:50]


async def process_query(
    request: Request,
    source_type: str,
    input_text: Optional[str],
    zip_file: Optional[UploadFile],
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
    # (Input validation remains the same)
    source_for_ingest: Optional[str] = None
    effective_input_display = "" # Initialize
    if source_type == "url_path":
        if not input_text:
            # Handle error
            return templates.TemplateResponse("index.jinja" if is_index else "git.jinja", {"request": request, "error_message": "Please provide a URL or local path.", "repo_url": input_text, "examples": EXAMPLE_REPOS if is_index else [], "default_file_size": slider_position, "pattern_type": pattern_type, "pattern": pattern, "branch_or_tag": branch_or_tag}, status_code=400)
        source_for_ingest = input_text
        effective_input_display = input_text
    elif source_type == "zip_file":
        if not zip_file or not zip_file.filename:
            # Handle error
            return templates.TemplateResponse("index.jinja" if is_index else "git.jinja", {"request": request, "error_message": "Please upload a ZIP file.", "repo_url": input_text, "examples": EXAMPLE_REPOS if is_index else [], "default_file_size": slider_position, "pattern_type": pattern_type, "pattern": pattern, "branch_or_tag": branch_or_tag}, status_code=400)
        # Placeholder logic for zip
        source_for_ingest = zip_file.filename # Replace with actual extraction path
        effective_input_display = f"ZIP: {zip_file.filename}"
        branch_or_tag = "" # Clear branch/tag for zip uploads
    else:
         # Handle error
         return templates.TemplateResponse("index.jinja" if is_index else "git.jinja", {"request": request, "error_message": "Invalid source type specified.", "repo_url": input_text, "examples": EXAMPLE_REPOS if is_index else [], "default_file_size": slider_position, "pattern_type": pattern_type, "pattern": pattern, "branch_or_tag": branch_or_tag}, status_code=400)


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

    context = {
        "request": request,
        "repo_url": input_text or "",
        "examples": EXAMPLE_REPOS if is_index else [],
        "default_file_size": slider_position,
        "pattern_type": pattern_type,
        "pattern": pattern,
        "branch_or_tag": branch_or_tag,
        "result": False,
        "error_message": None,
        "summary": None,
        "tree_data": None, # MODIFIED: Changed tree to tree_data
        "content": None,
        "ingest_id": None,
        "is_local_path": False,
        "encoded_download_filename": None,
        "base_repo_url": None, # Added for links
        "repo_ref": None,      # Added for links (branch/commit)
    }

    query_obj_from_ingest = None

    try:
        # --- Call the core ingest function ---
        # MODIFIED: Unpack tree_data (List) instead of tree (str)
        summary, tree_data, content, query_obj_from_ingest = await ingest_async(
            source=source_for_ingest,
            max_file_size=max_file_size,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            branch=branch_or_tag if source_type == 'url_path' and branch_or_tag else None,
            output=None
        )

        # --- Create Temp Dir and Save Digest ---
        if not query_obj_from_ingest or not query_obj_from_ingest.id:
            _print_error(effective_input_display, Exception("Ingestion succeeded but query ID was not returned."), max_file_size, pattern_type, pattern, branch_or_tag)
            context["error_message"] = "An unexpected error occurred: Ingestion ID missing."
            return template_response(context=context)

        ingest_id_for_download = query_obj_from_ingest.id
        temp_digest_dir = TMP_BASE_PATH / ingest_id_for_download
        os.makedirs(temp_digest_dir, exist_ok=True)

        internal_filename = "digest.txt"
        digest_path = temp_digest_dir / internal_filename
        try:
            # Save structured data or simplified text to digest?
            # For simplicity, save simplified text version to digest.txt
            with open(digest_path, "w", encoding="utf-8") as f:
                 # Recreate simple text tree for file output
                 text_tree_for_file = "Directory structure:\n"
                 for item in tree_data:
                     indent = "    " * item['depth']
                     prefix = "└── " # Simplified prefix
                     text_tree_for_file += f"{indent}{prefix}{item['name']}\n"
                 f.write(text_tree_for_file)
                 f.write("\n" + content)

        except OSError as e:
            print(f"Error writing digest file {digest_path}: {e}")
            ingest_id_for_download = None
            context["error_message"] = f"Error saving digest: {e}"


        # --- Determine Download Filename ---
        # (Logic remains the same)
        filename_parts = []
        project_name_part = query_obj_from_ingest.repo_name if query_obj_from_ingest.url else query_obj_from_ingest.slug
        sanitized_project_name = sanitize_filename_part(project_name_part)
        if sanitized_project_name: filename_parts.append(sanitized_project_name)
        else: filename_parts.append("digest")
        ref_for_filename = branch_or_tag if branch_or_tag else query_obj_from_ingest.branch
        if source_type != 'zip_file' and query_obj_from_ingest.url and ref_for_filename:
            sanitized_ref = sanitize_filename_part(ref_for_filename)
            if sanitized_ref: filename_parts.append(sanitized_ref)
        elif source_type != 'zip_file' and query_obj_from_ingest.url and query_obj_from_ingest.commit and not branch_or_tag:
            sanitized_commit = sanitize_filename_part(query_obj_from_ingest.commit[:7])
            if sanitized_commit: filename_parts.append(sanitized_commit)
        download_filename = "_".join(filename_parts) + ".txt"
        encoded_download_filename = quote(download_filename)


        # --- Update context for success ---
        display_path = query_obj_from_ingest.url if query_obj_from_ingest.url else str(query_obj_from_ingest.local_path)
        if len(content) > MAX_DISPLAY_SIZE:
            content_to_display = (f"(Files content cropped...)\n" + content[:MAX_DISPLAY_SIZE])
        else:
            content_to_display = content

        _print_success(
            url_or_path=effective_input_display,
            max_file_size=max_file_size, pattern_type=pattern_type, pattern=pattern,
            summary=summary, branch_or_tag=branch_or_tag
        )

        context.update(
            {
                "result": True,
                "summary": summary,
                "tree_data": tree_data, # MODIFIED: Pass structured tree data
                "content": content_to_display,
                "ingest_id": ingest_id_for_download,
                "is_local_path": not query_obj_from_ingest.url and source_type != 'zip_file',
                "encoded_download_filename": encoded_download_filename if ingest_id_for_download else None,
                # ADDED: Pass info needed for links
                "base_repo_url": query_obj_from_ingest.url if query_obj_from_ingest.url else None,
                "repo_ref": query_obj_from_ingest.branch or query_obj_from_ingest.commit or 'main',
            }
        )
        return template_response(context=context)

    except Exception as exc:
        # (Error handling remains the same)
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
        elif "zipfile.badzipfile" in str_exc:
             context["error_message"] = f"Error: The uploaded file '{effective_input_display}' is not a valid ZIP file."

        return template_response(context=context)


# (Logging functions _print_query, _print_error, _print_success remain unchanged)
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