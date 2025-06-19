"""Utility functions for interacting with Git repositories."""

import asyncio
import logging
from typing import List, Tuple

from CodeIngest.utils.exceptions import GitError # Assuming RepositoryNotFoundError is not directly raised here


logger = logging.getLogger(__name__)


async def run_command(*args: str) -> Tuple[bytes, bytes]:
    """
    Execute a shell command asynchronously and return (stdout, stderr) bytes.

    Parameters
    ----------
    *args : str
        The command and its arguments to execute.

    Returns
    -------
    Tuple[bytes, bytes]
        A tuple containing the stdout and stderr of the command.

    Raises
    ------
    RuntimeError
        If command exits with a non-zero status.
    """
    # Execute the requested command
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        error_message = stderr.decode().strip()
        # Log the command and error before raising
        logger.error("Command '%s' failed with error: %s", ' '.join(args), error_message)
        raise GitError(f"Command '{' '.join(args)}' failed with error: {error_message}")

    return stdout, stderr


async def ensure_git_installed() -> None:
    """
    Ensure Git is installed and accessible on the system.

    Raises
    ------
    RuntimeError
        If Git is not installed or not accessible.
    """
    try:
        await run_command("git", "--version")
    except GitError as exc: # Catch the more specific GitError
        logger.error("Git installation check failed.", exc_info=True)
        raise GitError("Git is not installed or not accessible. Please install Git first.") from exc


async def check_repo_exists(url: str) -> bool:
    """
    Check if a Git repository exists at the provided URL.

    Parameters
    ----------
    url : str
        The URL of the Git repository to check.
    Returns
    -------
    bool
        True if the repository exists, False otherwise.

    Raises
    ------
    RuntimeError
        If the curl command returns an unexpected status code.
    """
    proc = await asyncio.create_subprocess_exec(
        "curl",
        "-I",
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()

    if proc.returncode != 0:
        # Log curl command failure, e.g., network issue or invalid URL format before HTTP check
        logger.warning("curl command failed for URL %s. Return code: %s, Error: %s", url, proc.returncode, _.decode().strip())
        return False

    response = stdout.decode()
    if not response:
        logger.warning("Empty response from curl -I %s", url)
        return False

    status_line = response.splitlines()[0].strip()
    parts = status_line.split(" ")

    if len(parts) < 2:
        logger.warning("Unexpected status line format from 'curl -I %s': %s", url, status_line)
        return False # Or raise GitError if this should be a harder fail

    status_code_str = parts[1]

    if status_code_str in ("200", "301", "302"): # 302 often redirects to actual repo page
        logger.debug("Repository check for %s returned status %s. Assuming exists.", url, status_code_str)
        return True
    elif status_code_str == "404":
        logger.debug("Repository check for %s returned status 404. Assuming does not exist.", url)
        return False
    elif status_code_str in ("401", "403"):
        logger.warning("Repository check for %s returned status %s. Repository may exist but is private/inaccessible.", url, status_code_str)
        return False # Treat as not accessible for cloning purposes
    else:
        # For other status codes, log as a warning and treat as not existing or problematic.
        logger.warning("Repository check for %s returned unexpected HTTP status %s. Status line: %s", url, status_code_str, status_line)
        return False


async def fetch_remote_branch_list(url: str) -> List[str]:
    """
    Fetch the list of branches from a remote Git repository.
    Parameters
    ----------
    url : str
        The URL of the Git repository to fetch branches from.
    Returns
    -------
    List[str]
        A list of branch names available in the remote repository.
    """
    fetch_branches_command = ["git", "ls-remote", "--heads", url]
    await ensure_git_installed()
    stdout, _ = await run_command(*fetch_branches_command)
    stdout_decoded = stdout.decode()

    return [
        line.split("refs/heads/", 1)[1]
        for line in stdout_decoded.splitlines()
        if line.strip() and "refs/heads/" in line
    ]
