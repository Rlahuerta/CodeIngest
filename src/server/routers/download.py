"""This module contains the FastAPI router for downloading a digest file."""

import re # Added import for regex
from pathlib import Path
from typing import Optional # Import Optional
from fastapi import APIRouter, HTTPException, Query, Request # Import Request
from fastapi.responses import FileResponse

from CodeIngest.config import TMP_BASE_PATH
from server.server_utils import limiter # Added import

router = APIRouter()


@router.get("/download/{digest_id}")
@limiter.limit("30/minute") # Added rate limit decorator
async def download_ingest(
    request: Request, # Added request parameter
    digest_id: str,
    # --- Add filename query parameter ---
    filename: Optional[str] = Query(None, description="Desired filename for the download.")
) -> FileResponse:
    """
    Download the 'digest.txt' file associated with a given digest ID,
    allowing the client to suggest a download filename via query parameter.

    Searches for 'digest.txt' within the temporary directory corresponding
    to the digest ID.

    Parameters
    ----------
    request : Request
        The FastAPI Request object, used by the rate limiter.
    digest_id : str
        The unique identifier for the digest, corresponding to a directory
        under TMP_BASE_PATH.
    filename : str, optional
        The desired filename for the downloaded file, passed as a query parameter.
        Defaults to 'digest.txt' if not provided or invalid.

    Returns
    -------
    FileResponse
        A FastAPI FileResponse object streaming the content of 'digest.txt'.
        The file is sent with the media type 'text/plain' and prompts a download
        using the provided or default filename.

    Raises
    ------
    HTTPException
        If the digest directory or the 'digest.txt' file within it is not found.
    """
    # Construct the path to the *actual* saved file
    directory = TMP_BASE_PATH / digest_id
    internal_filename = "digest.txt" # The file is always saved with this name
    digest_file_path = directory / internal_filename

    # Check if the directory and the file exist
    if not directory.is_dir() or not digest_file_path.is_file():
        raise HTTPException(status_code=404, detail="Digest file not found.")

    # --- Determine the filename for the Content-Disposition header ---
    effective_download_filename = "digest.txt" # Default download name
    if filename:
        # Normalize and basic sanitize
        normalized_filename = filename.strip()
        if normalized_filename.lower().endswith(".txt") and len(normalized_filename) > 4:
            base_name = normalized_filename[:-4] # Remove .txt extension
            # Ensure base_name is not empty and contains no path traversal or unsafe characters.
            # A simple check is that the basename derived from Path is the same as the cleaned base_name.
            # Also, limit length and check for allowed characters.
            # This regex allows alphanumeric, underscore, hyphen, dot (dot is tricky as it's also extension sep)
            # For simplicity, we'll allow dots in the base_name part here.
            # More robust would be to disallow dots in base_name or have a stricter regex.
            if base_name and Path(base_name).name == base_name and re.match(r"^[a-zA-Z0-9_.-]+$", base_name):
                effective_download_filename = base_name + ".txt"
            # If validation fails, it falls back to "digest.txt" set initially.

    # Use FileResponse to efficiently send the file
    return FileResponse(
        path=digest_file_path,
        media_type="text/plain",
        # --- Use the determined filename for download prompt ---
        filename=effective_download_filename
    )
