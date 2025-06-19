"""Utility functions for the server."""

import asyncio
import math
import shutil
import time
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from CodeIngest.config import TMP_BASE_PATH
from server.server_config import DELETE_REPO_AFTER

# Initialize a rate limiter
limiter = Limiter(key_func=get_remote_address)

logger = logging.getLogger(__name__)


async def rate_limit_exception_handler(request: Request, exc: Exception) -> Response:
    """
    Custom exception handler for rate-limiting errors.

    Parameters
    ----------
    request : Request
        The incoming HTTP request.
    exc : Exception
        The exception raised, expected to be RateLimitExceeded.

    Returns
    -------
    Response
        A response indicating that the rate limit has been exceeded.

    Raises
    ------
    exc
        If the exception is not a RateLimitExceeded error, it is re-raised.
    """
    if isinstance(exc, RateLimitExceeded):
        # Delegate to the default rate limit handler
        return _rate_limit_exceeded_handler(request, exc)
    # Re-raise other exceptions
    raise exc


@asynccontextmanager
async def lifespan(_: FastAPI):
    """
    Lifecycle manager for handling startup and shutdown events for the FastAPI application.

    Parameters
    ----------
    _ : FastAPI
        The FastAPI application instance (unused).

    Yields
    -------
    None
        Yields control back to the FastAPI application while the background task runs.
    """
    task = asyncio.create_task(_remove_old_repositories())

    yield
    # Cancel the background task on shutdown
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _remove_old_repositories():
    """
    Periodically remove old repository folders.

    Background task that runs periodically to clean up old repository directories.

    This task:
    - Scans the TMP_BASE_PATH directory every 60 seconds
    - Removes directories older than DELETE_REPO_AFTER seconds
    - Before deletion, logs repository URLs to history.txt if a matching .txt file exists
    - Handles errors gracefully if deletion fails

    The repository URL is extracted from the first .txt file in each directory,
    assuming the filename format: "owner-repository.txt"
    """
    while True:
        try:
            if not TMP_BASE_PATH.exists():
                await asyncio.sleep(60)
                continue

            current_time = time.time()

            for folder in TMP_BASE_PATH.iterdir():
                # Skip if folder is not old enough
                if current_time - folder.stat().st_ctime <= DELETE_REPO_AFTER:
                    continue

                await _process_folder(folder)

        except Exception as exc:
            logger.error("Error in repository cleanup task: %s", exc, exc_info=True)

        await asyncio.sleep(60)


async def _process_folder(folder: Path) -> None:
    """
    Process a single folder for deletion and logging.

    Parameters
    ----------
    folder : Path
        The path to the folder to be processed.
    """
    # Try to log repository URL before deletion
    try:
        txt_files = [f for f in folder.iterdir() if f.suffix == ".txt"]
        if txt_files: # If there are .txt files
            filename_stem = txt_files[0].stem # Use stem from the first .txt file found
            original_filename = txt_files[0].name # For logging
            if "-" in filename_stem:
                owner, repo = filename_stem.split("-", 1)
                repo_url = f"{owner}/{repo}"
                with open("history.txt", mode="a", encoding="utf-8") as history:
                    history.write(f"{repo_url}\n")
            else:
                # Log if filename doesn't contain a hyphen
                logger.warning(
                    "Could not parse repository name from filename '%s' in folder %s. Expected 'owner-repo.txt' format.",
                    original_filename,
                    folder
                )
        # If no .txt files, this block is skipped, no warning needed for that specifically
    except Exception as exc: # Catches errors from iterdir, file access, open, write
        logger.warning("Error logging repository URL for %s: %s", folder, exc)

    # Delete the folder
    try:
        shutil.rmtree(folder)
    except Exception as exc:
        logger.error("Error deleting folder %s: %s", folder, exc, exc_info=True)


def log_slider_to_size(position: int) -> int:
    """
    Convert a slider position to a file size in bytes using a logarithmic scale.

    Parameters
    ----------
    position : int
        Slider position ranging from 0 to 500.

    Returns
    -------
    int
        File size in bytes corresponding to the slider position.
    """
    maxp = 500
    minv = math.log(1)
    maxv = math.log(102_400)
    return round(math.exp(minv + (maxv - minv) * pow(position / maxp, 1.5))) * 1024
