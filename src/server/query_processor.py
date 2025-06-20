# src/server/query_processor.py
"""Process a query by parsing input, cloning a repository, and generating a summary."""

import os
import json # Added import
import re
from functools import partial
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import Request, UploadFile
from starlette.templating import _TemplateResponse
from urllib.parse import quote
import zipfile # Ensure zipfile is imported
import shutil  # Ensure shutil is imported
import logging

# --- Core CodeIngest imports ---
from CodeIngest.entrypoint import ingest_async
from CodeIngest.config import TMP_BASE_PATH
from CodeIngest.output_formatters import TreeDataItem
from CodeIngest.utils.exceptions import GitError, InvalidPatternError # Assuming IngestionError might be too broad for now

# --- Server specific imports ---
from server.server_config import EXAMPLE_REPOS, MAX_DISPLAY_SIZE, templates
from server.server_utils import log_slider_to_size

# Define paths for zip handling
RAW_UPLOADS_PATH = TMP_BASE_PATH / "uploads"
EXTRACTED_ZIPS_PATH = TMP_BASE_PATH / "extracted"
RAW_UPLOADS_PATH.mkdir(parents=True, exist_ok=True)
EXTRACTED_ZIPS_PATH.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)

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
    download_format: str = "txt", # Added download_format
    is_index: bool = False,
) -> _TemplateResponse:
    """
    Process a query (from URL/path or ZIP), generate summary, save digest (TXT or JSON), and prepare response.
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
        "base_repo_url": None, "repo_ref": None, "download_format": download_format, # Added download_format to context
    }
    query_obj_from_ingest = None

    try:
        # Call the core ingest function, which now returns a dictionary
        ingestion_result = await ingest_async(
            source=source_for_ingest,
            max_file_size=max_file_size,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            branch=branch_or_tag if source_type == 'url_path' and branch_or_tag else None
        )
        query_obj_from_ingest = ingestion_result["query_obj"] # Extract for convenience

        if not query_obj_from_ingest or not query_obj_from_ingest.id:
            logger.error(
                "Processing failed for '%s': Ingestion succeeded but query ID was not returned. Details: max_file_size=%s, pattern_type=%s, pattern='%s', branch_or_tag='%s'",
                effective_input_display, max_file_size, pattern_type, pattern, branch_or_tag,
                exc_info=True  # Include stack trace for unexpected internal errors
            )
            context["error_message"] = "An unexpected error occurred: Ingestion ID missing."
            return templates.TemplateResponse(template, context=context)

        ingest_id_for_download = query_obj_from_ingest.id
        temp_digest_dir = TMP_BASE_PATH / ingest_id_for_download
        os.makedirs(temp_digest_dir, exist_ok=True)

        file_content_to_write = ""
        actual_internal_filename = ""
        if download_format == "json":
            actual_internal_filename = "digest.json"
            # Construct data_to_save using fields from ingestion_result and the new JSON structure
            metadata_obj = {
                "repository_url": query_obj_from_ingest.url if query_obj_from_ingest else None,
                "branch": query_obj_from_ingest.branch if query_obj_from_ingest else None,
                "commit": query_obj_from_ingest.commit if query_obj_from_ingest else None,
                "number_of_tokens": ingestion_result["num_tokens"],
                "number_of_files": ingestion_result["num_files"],
                "directory_structure_text": ingestion_result["directory_structure_text"]
            }
            data_to_save = {
                "summary": ingestion_result["summary_str"],
                "metadata": metadata_obj,
                "tree": ingestion_result["tree_data"], # This is tree_data_with_embedded_content
                "query": query_obj_from_ingest.model_dump(mode='json') if query_obj_from_ingest else None
            }
            file_content_to_write = json.dumps(data_to_save, indent=2)
        else: # Default to txt
            actual_internal_filename = "digest.txt"
            # Use directory_structure_text and concatenated_content from ingestion_result
            file_content_to_write = f"Directory structure:\n{ingestion_result['directory_structure_text']}\n\n{ingestion_result['concatenated_content']}"

        digest_path = temp_digest_dir / actual_internal_filename

        try:
            with open(digest_path, "w", encoding="utf-8") as f:
                f.write(file_content_to_write)
        except OSError as e:
            logger.error("Error writing digest file %s: %s", digest_path, e, exc_info=True)
            ingest_id_for_download = None # Invalidate if save failed
            context["error_message"] = f"Error saving digest: {e}"


        # Determine Download Filename
        file_ext = ".json" if download_format == "json" else ".txt"
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

        download_filename = "_".join(filename_parts) + file_ext
        encoded_download_filename = quote(download_filename)

        # Prepare content for display (cropping if too large)
        concatenated_content_for_ui = ingestion_result["concatenated_content"]
        content_to_display = concatenated_content_for_ui[:MAX_DISPLAY_SIZE] + ("\n(Files content cropped to first characters...)" if len(concatenated_content_for_ui) > MAX_DISPLAY_SIZE else "")

        # Prepare a concise summary for logging
        summary_for_log = "N/A"
        try:
            token_line = next((line for line in ingestion_result["summary_str"].splitlines() if "Estimated tokens:" in line), None)
            if token_line:
                summary_for_log = token_line.strip()
        except Exception:
            pass # Keep N/A if parsing fails

        logger.info(
            "Processing successful for '%s'. Summary: %s. Details: max_file_size=%s, pattern_type=%s, pattern='%s', branch_or_tag='%s'",
            effective_input_display, summary_for_log, max_file_size, pattern_type, pattern, branch_or_tag
        )

        context.update({
            "result": True,
            "summary": ingestion_result["summary_str"],
            "tree_data": ingestion_result["tree_data"],
            "content": content_to_display,
            "ingest_id": ingest_id_for_download,
            "is_local_path": not query_obj_from_ingest.url and source_type != 'zip_file', # True if local dir/file
            "encoded_download_filename": encoded_download_filename if ingest_id_for_download else None,
            "base_repo_url": query_obj_from_ingest.url if query_obj_from_ingest.url else None,
            "repo_ref": query_obj_from_ingest.branch or query_obj_from_ingest.commit or 'main',
            # download_format is already added to context initialization
        })
        return templates.TemplateResponse(template, context=context)

    except GitError as e:
        logger.error("GitError occurred while processing '%s': %s. Details: max_file_size=%s, pattern_type=%s, pattern='%s', branch_or_tag='%s'",
                     effective_input_display, e, max_file_size, pattern_type, pattern, branch_or_tag, exc_info=True)
        context["error_message"] = f"Git operation failed: {e}. Ensure your Git URL is correct, the repository is accessible, and Git is installed on the server."
        return templates.TemplateResponse(template, context=context, status_code=500) # Or 400 if client error

    except zipfile.BadZipFile as e:
        logger.error("BadZipFile occurred while processing '%s': %s. Details: max_file_size=%s, pattern_type=%s, pattern='%s'",
                     effective_input_display, e, max_file_size, pattern_type, pattern, exc_info=True)
        context["error_message"] = f"The uploaded file '{original_filename_for_slug or effective_input_display}' is not a valid ZIP file or is corrupted. Details: {e}"
        return templates.TemplateResponse(template, context=context, status_code=400)

    except InvalidPatternError as e:
        logger.error("InvalidPatternError occurred for '%s': %s. Details: max_file_size=%s, pattern_type=%s, pattern='%s'",
                     effective_input_display, e, max_file_size, pattern_type, pattern, exc_info=True)
        context["error_message"] = f"Invalid include/exclude pattern provided: {e}"
        return templates.TemplateResponse(template, context=context, status_code=400)

    except ValueError as e:
        logger.warning(
            "ValueError during processing for '%s': %s. Details: max_file_size=%s, pattern_type=%s, pattern='%s', branch_or_tag='%s'",
            effective_input_display, e, max_file_size, pattern_type, pattern, branch_or_tag,
            exc_info=True # ValueErrors can sometimes have useful stack traces for debugging config issues
        )
        str_e_lower = str(e).lower()
        if "local path not found" in str_e_lower:
            context["error_message"] = f"Error: Local path not found: {effective_input_display}"
        elif "repository not found" in str_e_lower or "could not access" in str_e_lower: # Catchall for repo access issues not caught by GitError
            context["error_message"] = f"Error: Could not access '{effective_input_display}'. Ensure URL/path is correct, public, and branch/tag/commit exists."
        # "invalid characters" for patterns should ideally be caught by InvalidPatternError if parsing is robust
        else:
            context["error_message"] = f"Invalid input or configuration: {e}"
        return templates.TemplateResponse(template, context=context, status_code=400)

    except Exception as exc:
        logger.error(
            "Unexpected error processing failed for '%s': %s. Details: max_file_size=%s, pattern_type=%s, pattern='%s', branch_or_tag='%s'",
            effective_input_display, exc, max_file_size, pattern_type, pattern, branch_or_tag,
            exc_info=True
        )
        # Generic error message for unexpected issues
        context["error_message"] = f"An unexpected error occurred while processing '{effective_input_display}'. Please try again or contact support if the issue persists."
        return templates.TemplateResponse(template, context=context, status_code=500)