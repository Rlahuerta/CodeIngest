"""This module contains the FastAPI router for downloading a digest file."""

import os # Ensure os is imported
import re
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from CodeIngest.config import TMP_BASE_PATH
from server.server_utils import limiter # Added import

router = APIRouter()


@router.get("/download/{digest_id}")
@limiter.limit("30/minute") # Added rate limit decorator
async def download_ingest(
    request: Request, # Added request parameter
    digest_id: str,
    filename: Optional[str] = Query(None, description="Desired filename for the download (e.g., my_repo_main.txt or my_repo_main.json).")
) -> FileResponse:
    """
    Download the digest file (TXT or JSON) associated with a given digest ID.
    The client suggests the full filename including extension, which indicates the desired format.

    Searches for 'digest.txt' or 'digest.json' within the temporary directory
    corresponding to the digest ID, based on the requested filename's extension.

    Parameters
    ----------
    request : Request
        The FastAPI Request object, used by the rate limiter.
    digest_id : str
        The unique identifier for the digest.
    filename : str, optional
        The desired filename for the downloaded file, including extension (.txt or .json).
        This determines which internal file ('digest.txt' or 'digest.json') is served
        and the Content-Type.

    Returns
    -------
    FileResponse
        A FastAPI FileResponse object streaming the content of the digest file.
        Media type is set to 'text/plain' or 'application/json'.

    Raises
    ------
    HTTPException
        If the digest directory or the determined digest file (e.g. digest.json) is not found.
    """
    directory = TMP_BASE_PATH / digest_id

    # Determine internal file to find and media type based on requested filename's extension
    requested_ext = ".json" if filename and filename.lower().endswith(".json") else ".txt"
    internal_file_to_find = "digest.json" if requested_ext == ".json" else "digest.txt"
    media_type_for_response = "application/json" if internal_file_to_find == "digest.json" else "text/plain"
    digest_file_path = directory / internal_file_to_find

    # Check if the directory and the determined file exist
    if not directory.is_dir() or not digest_file_path.is_file():
        raise HTTPException(status_code=404, detail=f"Digest file {internal_file_to_find} not found for ID {digest_id}.")

    # Determine the filename for the Content-Disposition header
    # Use the self-corrected simpler logic
    default_filename_on_error = "digest.json" if internal_file_to_find == "digest.json" else "digest.txt"
    final_download_name = default_filename_on_error

    if filename:
        p_filename = Path(filename)
        # Ensure it's a simple filename (no path components) and has a recognized suffix
        if p_filename.name == filename and p_filename.suffix.lower() in ['.txt', '.json']:
            # Check if the requested extension matches the internal file being served
            if (p_filename.suffix.lower() == ".json" and internal_file_to_find == "digest.json") or \
               (p_filename.suffix.lower() == ".txt" and internal_file_to_find == "digest.txt"):
                final_download_name = filename
            # If mismatch (e.g. requested digest.txt but internal is digest.json due to query),
            # it will use default_filename_on_error which matches internal_file_to_find.

    # Use FileResponse to efficiently send the file
    return FileResponse(
        path=digest_file_path,
        media_type=media_type_for_response,
        filename=final_download_name
    )
