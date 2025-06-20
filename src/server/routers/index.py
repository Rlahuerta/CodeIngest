# src/server/routers/index.py
import shutil
import uuid
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Form, Request, File, UploadFile
from fastapi.responses import HTMLResponse

from server.query_processor import process_query, RAW_UPLOADS_PATH # Import RAW_UPLOADS_PATH
from server.server_config import EXAMPLE_REPOS, templates
from server.server_utils import limiter

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse( "index.jinja", { "request": request, "examples": EXAMPLE_REPOS, "default_file_size": 243, "branch_or_tag": "", "source_type": "url_path", }, )


@router.post("/", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def index_post(
    request: Request,
    source_type: str = Form(...),
    input_text: Optional[str] = Form(None), # For URL/Path
    zip_file: Optional[UploadFile] = File(None), # For ZIP upload
    max_file_size: int = Form(...),
    pattern_type: str = Form(...),
    pattern: str = Form(""),
    branch_or_tag: str = Form(""),
    download_format: str = Form("txt"), # Added download_format
) -> HTMLResponse:

    actual_input_for_process_query = input_text # Default for url_path

    if source_type == "zip_file":
        if not zip_file or not zip_file.filename:
            # If zip_file is expected but not provided, call process_query with None for input_text
            # process_query will then handle the error message generation.
            return await process_query(request=request, source_type=source_type, input_text=None, zip_file=None,
                                       slider_position=max_file_size, pattern_type=pattern_type, pattern=pattern,
                                       branch_or_tag=branch_or_tag, download_format=download_format, is_index=True)

        # --- Save uploaded ZIP to a temporary path ---
        temp_zip_filename = f"{uuid.uuid4()}_{zip_file.filename}"
        temp_zip_save_path = RAW_UPLOADS_PATH / temp_zip_filename
        try:
            zip_file.file.seek(0) # Add this line
            with open(temp_zip_save_path, "wb") as buffer:
                shutil.copyfileobj(zip_file.file, buffer)
            # CRITICAL: actual_input_for_process_query IS NOW THE PATH TO THE SAVED ZIP
            actual_input_for_process_query = str(temp_zip_save_path)
        except Exception as e:
            # If saving fails, pass an error message via input_text to process_query
            # so it can be displayed in the template.
            # Keep zip_file object for context if needed by process_query for display.
            return await process_query(request=request, source_type=source_type,
                                       input_text=f"Error saving uploaded ZIP: {e}", # Pass error as input_text
                                       zip_file=zip_file, # Pass original zip_file
                                       slider_position=max_file_size, pattern_type=pattern_type, pattern=pattern,
                                       branch_or_tag=branch_or_tag, download_format=download_format, is_index=True)
        finally:
            await zip_file.close()
    elif source_type == "url_path":
        if not input_text: # Ensure input_text is provided for url_path
             return await process_query(request=request, source_type=source_type, input_text=None, zip_file=None,
                                       slider_position=max_file_size, pattern_type=pattern_type, pattern=pattern,
                                       branch_or_tag=branch_or_tag, download_format=download_format, is_index=True)
        # actual_input_for_process_query is already set to input_text
    else: # Invalid source_type
        return await process_query(request=request, source_type=source_type, input_text="Invalid source type", zip_file=None,
                                   slider_position=max_file_size, pattern_type=pattern_type, pattern=pattern,
                                   branch_or_tag=branch_or_tag, download_format=download_format, is_index=True)

    # Now, actual_input_for_process_query contains either:
    # 1. The URL/local path from the form.
    # 2. The full path to the temporarily saved ZIP file.
    return await process_query(
        request=request,
        source_type=source_type,
        input_text=actual_input_for_process_query, # This is the crucial part
        zip_file=zip_file, # Pass original UploadFile for metadata (e.g., original filename)
        slider_position=max_file_size,
        pattern_type=pattern_type,
        pattern=pattern,
        branch_or_tag=branch_or_tag,
        download_format=download_format, # Pass download_format
        is_index=True,
    )