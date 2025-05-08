# src/server/routers/index.py
"""This module defines the FastAPI router for the home page of the application."""

from typing import Optional # Added Optional
from fastapi import APIRouter, Form, Request, File, UploadFile # Added File, UploadFile
from fastapi.responses import HTMLResponse

from server.query_processor import process_query
from server.server_config import EXAMPLE_REPOS, templates
from server.server_utils import limiter

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    """
    Render the home page with example repositories and default parameters.

    This endpoint serves the home page of the application, rendering the `index.jinja` template
    and providing it with a list of example repositories and default file size values.

    Parameters
    ----------
    request : Request
        The incoming request object, which provides context for rendering the response.

    Returns
    -------
    HTMLResponse
        An HTML response containing the rendered home page template, with example repositories
        and other default parameters such as file size.
    """
    return templates.TemplateResponse(
        "index.jinja",
        {
            "request": request,
            "examples": EXAMPLE_REPOS,
            "default_file_size": 243,
            # Initialize branch_or_tag for the template context on GET
            "branch_or_tag": "",
             # Initialize source_type (assuming URL/Path is default)
            "source_type": "url_path", # Default for initial view
        },
    )


@router.post("/", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def index_post(
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

    This endpoint handles POST requests from the home page form. It processes the user-submitted
    input (either text URL/path or a ZIP file) and invokes the `process_query`
    function to handle the query logic, returning the result as an HTML response.

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
        is_index=True, # Indicate this is the index page handler
    )