# src/server/routers/dynamic.py
"""This module defines the dynamic router for handling dynamic path requests."""

from typing import Optional # Add Optional
from fastapi import APIRouter, Form, Request, File, UploadFile # Add File, UploadFile
from fastapi.responses import HTMLResponse

from server.query_processor import process_query
from server.server_config import templates
from server.server_utils import limiter

router = APIRouter()


@router.get("/{full_path:path}")
async def catch_all(request: Request, full_path: str) -> HTMLResponse:
    """
    Render a page with a Git URL based on the provided path.

    This endpoint catches all GET requests with a dynamic path, constructs a Git URL
    using the `full_path` parameter, and renders the `git.jinja` template with that URL.
    It assumes the source type is URL/Path for GET requests.

    Parameters
    ----------
    request : Request
        The incoming request object, which provides context for rendering the response.
    full_path : str
        The full path extracted from the URL, which is used to build the Git URL.

    Returns
    -------
    HTMLResponse
        An HTML response containing the rendered template, with the Git URL
        and other default parameters such as loading state and file size.
    """
    # Note: Branch/tag cannot be easily pre-filled from GET request path here
    # without more complex parsing logic separate from the core parsing.
    # It will be empty on initial load via GET.
    # For GET requests, we assume source_type is 'url_path'
    return templates.TemplateResponse(
        "git.jinja",
        {
            "request": request,
            "repo_url": full_path, # Pre-fill the URL input
            "loading": True, # Start in loading state for dynamic routes
            "default_file_size": 243,
            "branch_or_tag": "", # Initialize as empty for GET request
            "pattern": "",       # Initialize pattern
            "pattern_type": "exclude", # Default pattern type
            # Add source_type context for the template's radio buttons
            "source_type": "url_path",
        },
    )


@router.post("/{full_path:path}", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def process_catch_all(
    request: Request,
    # --- Updated parameters to handle both input types ---
    source_type: str = Form(...), # 'url_path' or 'zip_file'
    input_text: Optional[str] = Form(None), # URL or local path, now optional
    zip_file: Optional[UploadFile] = File(None), # Uploaded zip file, optional
    # --- End of updated parameters ---
    max_file_size: int = Form(...),
    pattern_type: str = Form(...),
    # --- FIX: Make pattern optional with default ---
    pattern: str = Form(""), # Default to empty string if not provided
    # --- End FIX ---
    branch_or_tag: str = Form(""), # Add new form field, default to empty string
) -> HTMLResponse:
    """
    Process the form submission with user input for query parameters.

    This endpoint handles POST requests, processes the input parameters (e.g., text, file size, pattern, branch/tag),
    and calls the `process_query` function to handle the query logic, returning the result as an HTML response.

    Parameters
    ----------
    request : Request
        The incoming request object.
    source_type : str
        Indicates whether the source is 'url_path' or 'zip_file'.
    input_text : str, optional
        The input text (URL or local path), required if source_type is 'url_path'.
    zip_file : UploadFile, optional
        The uploaded ZIP file, required if source_type is 'zip_file'.
    max_file_size : int
        The maximum allowed file size slider position.
    pattern_type : str
        The type of pattern ('include' or 'exclude').
    pattern : str
        The pattern string (optional, defaults to "").
    branch_or_tag : str
        The specific branch, tag, or commit hash provided by the user (optional).

    Returns
    -------
    HTMLResponse
        The rendered results page or the form with an error message.
    """
    # --- Pass parameters to process_query ---
    # process_query will now handle the logic based on source_type
    return await process_query(
        request=request,
        source_type=source_type,
        input_text=input_text,
        zip_file=zip_file,
        slider_position=max_file_size, # Pass slider position
        pattern_type=pattern_type,
        pattern=pattern,
        branch_or_tag=branch_or_tag, # Pass the branch/tag
        is_index=False, # Indicate this is NOT the index page handler
    )
