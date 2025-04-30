"""This module contains functions to parse and validate input sources and patterns."""

import re
import uuid
import shutil
import warnings
import zipfile # Add zipfile import
import tempfile # Add tempfile import

from pathlib import Path
from typing import List, Optional, Set, Union
from urllib.parse import unquote, urlparse

from CodeIngest.config import TMP_BASE_PATH
from CodeIngest.schemas import IngestionQuery
from CodeIngest.utils.exceptions import InvalidPatternError
from CodeIngest.utils.git_utils import check_repo_exists, fetch_remote_branch_list
from CodeIngest.utils.ignore_patterns import DEFAULT_IGNORE_PATTERNS
from CodeIngest.utils.query_parser_utils import (
    KNOWN_GIT_HOSTS,
    _get_user_and_repo_from_path,
    _is_valid_git_commit_hash,
    _is_valid_pattern,
    _normalize_pattern,
    _validate_host,
    _validate_url_scheme,
)


async def parse_query(
    source: str,
    max_file_size: int,
    from_web: bool,
    include_patterns: Optional[Union[str, Set[str]]] = None,
    ignore_patterns: Optional[Union[str, Set[str]]] = None,
) -> IngestionQuery:
    """
    Parse the input source (URL, local path, or local zip file) to extract relevant details for the query.

    This function parses the input source to extract details such as the username, repository name,
    commit hash, branch name, and other relevant information. It handles remote Git URLs, local directories,
    and local .zip files containing repositories. It also processes the include and ignore
    patterns to filter the files and directories to include or exclude from the query.

    Parameters
    ----------
    source : str
        The source URL, local directory path, or local .zip file path to parse.
    max_file_size : int
        The maximum file size in bytes to include.
    from_web : bool
        Flag indicating whether the source is a web URL.
    include_patterns : Union[str, Set[str]], optional
        Patterns to include, by default None. Can be a set of strings or a single string.
    ignore_patterns : Union[str, Set[str]], optional
        Patterns to ignore, by default None. Can be a set of strings or a single string.

    Returns
    -------
    IngestionQuery
        A dataclass object containing the parsed details of the repository or file path.
        If the source was a zip file, `local_path` will point to the temporary extraction directory,
        and `original_zip_path` and `temp_extract_path` will store the original zip path and temp dir path respectively.
"""

    # Determine the parsing method based on the source type
    # Treat as remote if it looks like a URL or from_web is true.
    # Otherwise, check if it ends with .zip or assume it's a local dir/file path.
    is_remote_url = from_web or urlparse(source).scheme in ("https", "http") or any(h in source for h in KNOWN_GIT_HOSTS)
    is_local_zip = not is_remote_url and source.lower().endswith(".zip")

    if is_remote_url:
        query = await _parse_remote_repo(source)
    else: # Local path (directory, zip file, or single file)
        query = _parse_local_dir_path(source) # This now handles zip files

    # Combine default ignore patterns + custom patterns
    ignore_patterns_set = DEFAULT_IGNORE_PATTERNS.copy()
    if ignore_patterns:
        ignore_patterns_set.update(_parse_patterns(ignore_patterns))

    # Process include patterns and override ignore patterns accordingly
    if include_patterns:
        parsed_include = _parse_patterns(include_patterns)
        ignore_patterns_set = set(ignore_patterns_set) - set(parsed_include)
    else:
        parsed_include = None

    # Update the query object with processed patterns and max size
    query.max_file_size = max_file_size
    query.ignore_patterns = ignore_patterns_set
    query.include_patterns = parsed_include

    return query


async def _parse_remote_repo(source: str) -> IngestionQuery:
    """
    Parse a repository URL into a structured query dictionary.

    If source is:
      - A fully qualified URL (https://gitlab.com/...), parse & verify that domain
      - A URL missing 'https://' (gitlab.com/...), add 'https://' and parse
      - A 'slug' (like 'pandas-dev/pandas'), attempt known domains until we find one that exists.

    Parameters
    ----------
    source : str
        The URL or domain-less slug to parse.

    Returns
    -------
    IngestionQuery
        A dictionary containing the parsed details of the repository.
    """
    source = unquote(source)

    # Attempt to parse
    parsed_url = urlparse(source)

    if parsed_url.scheme:
        _validate_url_scheme(parsed_url.scheme)
        _validate_host(parsed_url.netloc.lower())

    else:  # Will be of the form 'host/user/repo' or 'user/repo'
        tmp_host = source.split("/")[0].lower()
        if "." in tmp_host:
            _validate_host(tmp_host)
        else:
            # No scheme, no domain => user typed "user/repo", so we'll guess the domain.
            user_name_guess, repo_name_guess = _get_user_and_repo_from_path(source)
            host = await try_domains_for_user_and_repo(user_name_guess, repo_name_guess)
            source = f"{host}/{source}"

        source = "https://" + source
        parsed_url = urlparse(source)

    host = parsed_url.netloc.lower()
    user_name, repo_name = _get_user_and_repo_from_path(parsed_url.path)

    _id = str(uuid.uuid4())
    slug = f"{user_name}-{repo_name}"
    # For remote repos, create a temporary local path for cloning
    local_path = TMP_BASE_PATH / _id / slug
    url = f"https://{host}/{user_name}/{repo_name}"

    # Initialize query object for remote repo
    parsed = IngestionQuery(
        user_name=user_name,
        repo_name=repo_name,
        url=url,
        local_path=local_path,
        slug=slug,
        id=_id,
        # Initialize optional fields
        subpath="/",
        type=None,
        branch=None,
        commit=None,
    )

    remaining_parts = parsed_url.path.strip("/").split("/")[2:]

    if not remaining_parts:
        return parsed # Return early if no extra path parts

    possible_type = remaining_parts.pop(0)  # e.g. 'issues', 'pull', 'tree', 'blob'

    # If no extra path parts left after popping type, just return
    if not remaining_parts:
         # If it's just a type like 'issues' or 'pull', don't process further
        if possible_type in ("issues", "pull"):
             return parsed
        parsed.type = possible_type
        return parsed

    # If this is an issues page or pull requests, return early without processing subpath
    if remaining_parts and possible_type in ("issues", "pull"):
        return parsed

    parsed.type = possible_type

    # Commit or branch
    commit_or_branch = remaining_parts[0]
    if _is_valid_git_commit_hash(commit_or_branch):
        parsed.commit = commit_or_branch
        remaining_parts.pop(0)
    else:
        # If not a commit hash, try to determine if it's a branch
        # Pass the remaining parts to configure branch and potentially update subpath
        parsed.branch = await _configure_branch_and_subpath(remaining_parts, url)
        # _configure_branch_and_subpath will pop elements from remaining_parts if a branch is found

    # Subpath if anything left after processing branch/commit
    if remaining_parts:
        # Join the rest as subpath, ensuring leading slash
        parsed.subpath = "/" + "/".join(remaining_parts)

    return parsed


async def _configure_branch_and_subpath(remaining_parts: List[str], url: str) -> Optional[str]:
    """
    Configure the branch and subpath based on the remaining parts of the URL.

    It attempts to match parts of the path against known remote branches.
    If a match is found, it consumes those parts from `remaining_parts`.

    Parameters
    ----------
    remaining_parts : List[str]
        The remaining parts of the URL path (mutable list).
    url : str
        The URL of the repository.

    Returns
    -------
    str, optional
        The branch name if found, otherwise None.
    """
    try:
        # Fetch the list of branches from the remote repository
        branches: List[str] = await fetch_remote_branch_list(url)
    except Exception as exc: # Catch broader exceptions during fetch
        warnings.warn(f"Warning: Failed to fetch branch list: {exc}", RuntimeWarning)
         # Fallback: Assume the first remaining part is the branch/commit/tag
        if remaining_parts:
            return remaining_parts.pop(0)
        return None # No parts left

    # Try to match parts against known branches
    branch_candidate = []
    matched_branch = None
    parts_consumed = 0
    for i in range(len(remaining_parts)):
        branch_candidate.append(remaining_parts[i])
        branch_name = "/".join(branch_candidate)
        if branch_name in branches:
            # Found a valid branch, store it and the number of parts
            matched_branch = branch_name
            parts_consumed = i + 1
            # Continue checking in case a longer branch name matches (e.g., "feature/a" vs "feature/a/b")

    if matched_branch:
        # Remove the consumed parts from the original list
        del remaining_parts[:parts_consumed]
        return matched_branch # Return the longest matching branch found

    # If no branch matched, assume the first part was intended as branch/tag/commit (if any parts remain)
    if remaining_parts:
         return remaining_parts.pop(0)

    return None # No branch found and no parts left


def _parse_patterns(pattern: Union[str, Set[str]]) -> Set[str]:
    """
    Parse and validate file/directory patterns for inclusion or exclusion.

    Takes either a single pattern string or set of pattern strings and processes them into a normalized list.
    Patterns are split on commas and spaces, validated for allowed characters, and normalized.

    Parameters
    ----------
    pattern : Set[str] | str
        Pattern(s) to parse - either a single string or set of strings

    Returns
    -------
    Set[str]
        A set of normalized patterns.

    Raises
    ------
    InvalidPatternError
        If any pattern contains invalid characters. Only alphanumeric characters,
        dash (-), underscore (_), dot (.), forward slash (/), plus (+), and
        asterisk (*) are allowed.
    """
    patterns = pattern if isinstance(pattern, set) else {pattern}

    parsed_patterns: Set[str] = set()
    for p in patterns:
        # Split by comma or space, handling multiple separators
        split_patterns = {part for part in re.split(r'[,\s]+', p) if part}
        parsed_patterns.update(split_patterns)


    # Normalize Windows paths to Unix-style paths
    parsed_patterns = {p.replace("\\", "/") for p in parsed_patterns}

    # Validate and normalize each pattern
    validated_patterns = set()
    for p in parsed_patterns:
        if not _is_valid_pattern(p):
            raise InvalidPatternError(p)
        validated_patterns.add(_normalize_pattern(p))

    return validated_patterns


def _parse_local_dir_path(path_str: str) -> IngestionQuery:
    """
    Parse the given local file path (directory or zip file) into a structured query dictionary.

    Parameters
    ----------
    path_str : str
        The file path to parse. Can be a directory or a .zip file.

    Returns
    -------
    IngestionQuery
        A dictionary containing the parsed details of the file path.

    Raises
    ------
    ValueError
        If the provided path is empty, does not exist, or is not a directory/zip file.
"""
    if not path_str:
        raise ValueError("Local path cannot be empty.")

    is_zip = path_str.lower().endswith(".zip")
    temp_extract_path: Optional[Path] = None # To store path if extracted from zip

    try:
        # Resolve the path to an absolute path and check existence
        path_obj = Path(path_str).resolve(strict=True)

        # --- Added: Handle zip file extraction ---
        if is_zip:
            if not path_obj.is_file():
                 raise ValueError(f"Specified zip path is not a file: {path_str}")
            if not zipfile.is_zipfile(path_obj):
                raise ValueError(f"Specified path is not a valid zip file: {path_str}")

            # Create a unique temporary directory for extraction
            _id = str(uuid.uuid4()) # Generate unique ID for temp dir
            temp_extract_path = TMP_BASE_PATH / _id / path_obj.stem # Use zip filename stem
            temp_extract_path.mkdir(parents=True, exist_ok=True)

            print(f"Extracting zip file {path_obj} to {temp_extract_path}...") # Optional: Add logging/print
            with zipfile.ZipFile(path_obj, 'r') as zip_ref:
                zip_ref.extractall(temp_extract_path)
            print("Extraction complete.") # Optional: Add logging/print

            # Use the *extracted* path for further processing
            ingestion_path = temp_extract_path
            slug = path_obj.stem # Use zip file name (without ext) as slug
        elif path_obj.is_dir():
            # Original logic for directories
            ingestion_path = path_obj
            if path_str == ".":
                slug = path_obj.name
            else:
                slug = path_str.rstrip("/\\")
                if not Path(slug).name and path_obj.name:
                    slug = path_obj.name
        elif path_obj.is_file():
             # It's a single file, not a directory or zip
             ingestion_path = path_obj
             slug = path_obj.stem # Use file name stem as slug
        else:
             raise ValueError(f"Local path is not a directory, zip file, or regular file: {path_str}")


    except FileNotFoundError as e:
        raise ValueError(f"Local path not found: {path_str}") from e
    except zipfile.BadZipFile as e:
        raise ValueError(f"Error opening zip file '{path_str}': {e}") from e
    except Exception as e:
        # Clean up temp dir if extraction failed mid-way
        if temp_extract_path and temp_extract_path.exists():
             shutil.rmtree(temp_extract_path.parent, ignore_errors=True) # Remove parent ID folder
        raise ValueError(f"Error resolving or processing local path '{path_str}': {e}") from e


    # Create IngestionQuery for local path
    return IngestionQuery(
        user_name=None,
        repo_name=None,
        url=None,
        local_path=ingestion_path, # Use the potentially extracted path
        slug=slug,
        id=str(uuid.uuid4()), # Generate a unique ID (distinct from temp dir ID if zip)
        # --- Added: Store original zip path if applicable ---
        original_zip_path=path_obj if is_zip else None,
        temp_extract_path=temp_extract_path if is_zip else None, # Store temp path if extracted
        # --- End Added ---
        subpath="/",
        type=None,
        branch=None,
        commit=None,
    )

async def try_domains_for_user_and_repo(user_name: str, repo_name: str) -> str:
    """
    Attempt to find a valid repository host for the given user_name and repo_name.

    Parameters
    ----------
    user_name : str
        The username or owner of the repository.
    repo_name : str
        The name of the repository.

    Returns
    -------
    str
        The domain of the valid repository host.

    Raises
    ------
    ValueError
        If no valid repository host is found for the given user_name and repo_name.
    """
    for domain in KNOWN_GIT_HOSTS:
        candidate = f"https://{domain}/{user_name}/{repo_name}"
        if await check_repo_exists(candidate):
            return domain
    raise ValueError(f"Could not find a valid repository host for '{user_name}/{repo_name}'.")

