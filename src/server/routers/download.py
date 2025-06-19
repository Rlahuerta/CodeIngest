"""This module contains the FastAPI router for downloading a digest file."""

from pathlib import Path
from typing import Optional # Import Optional
from fastapi import APIRouter, HTTPException, Query # Import Query
from fastapi.responses import FileResponse

from CodeIngest.config import TMP_BASE_PATH
from server.server_utils import limiter # Added import

router = APIRouter()


@router.get("/download/{digest_id}")
@limiter.limit("30/minute") # Added rate limit decorator
async def download_ingest(
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
    # Use the provided filename if it's valid, otherwise default
    # Basic validation: ensure it's not empty and looks like a .txt file
    if filename and filename.lower().endswith(".txt") and len(filename) > 4:
         effective_download_filename = filename
    else:
         effective_download_filename = "digest.txt" # Default download name

    # Use FileResponse to efficiently send the file
    return FileResponse(
        path=digest_file_path,
        media_type="text/plain",
        # --- Use the determined filename for download prompt ---
        filename=effective_download_filename
    )
