# src/CodeIngest/query_parsing.py
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
    include_patterns: Optional[Union[str, Set[str], Set[str]]] = None, # Allow Set[str] type hint
    ignore_patterns: Optional[Union[str, Set[str], Set[str]]] = None,  # Allow Set[str] type hint
) -> IngestionQuery:
    """
    Parse the input source (URL, local path, or local zip file) to extract relevant details for the query.

    Parameters
    ----------
    source : str
        The source URL, local directory path, or local .zip file path to parse.
    max_file_size : int
        The maximum file size in bytes to include.
    from_web : bool
        Flag indicating whether the source is a web URL.
    include_patterns : Union[str, Set[str]], optional
        Patterns to include.
    ignore_patterns : Union[str, Set[str]], optional
        Patterns to ignore. If provided, these are *added* to the defaults.
        An empty string "" means use defaults. An empty set {} means ignore nothing.

    Returns
    -------
    IngestionQuery
        A dataclass object containing the parsed details.
    """

    is_remote_url = from_web or urlparse(source).scheme in ("https", "http") or any(h in source for h in KNOWN_GIT_HOSTS)
    is_local_zip = not is_remote_url and source.lower().endswith(".zip")

    if is_remote_url:
        query = await _parse_remote_repo(source)
    else:
        query = _parse_local_dir_path(source) # Handles local dirs, files, and zips

    # --- Process Patterns ---
    final_ignore_patterns = DEFAULT_IGNORE_PATTERNS.copy()
    final_include_patterns = None # Default: include all unless excluded

    # Parse custom patterns if provided
    parsed_custom_includes = _parse_patterns(include_patterns) # Handles None, str, set

    # --- Updated Ignore Pattern Logic ---
    if ignore_patterns == set(): # Explicit empty set means ignore nothing
        final_ignore_patterns = set()
    elif ignore_patterns is not None and ignore_patterns != "": # User provided non-empty ignores
        parsed_custom_ignores = _parse_patterns(ignore_patterns)
        final_ignore_patterns.update(parsed_custom_ignores) # Add custom to defaults
    # If ignore_patterns is None or "", defaults are kept.
    # --- End Updated Ignore Pattern Logic ---

    # Set final include patterns
    if parsed_custom_includes:
        final_include_patterns = parsed_custom_includes

    # Filter ignore patterns based on include patterns (if includes were specified)
    if final_include_patterns:
        # Remove any ignore pattern that is exactly matched by an include pattern
        final_ignore_patterns = {
            ignore for ignore in final_ignore_patterns
            if ignore not in final_include_patterns
        }

    # Update the query object
    query.max_file_size = max_file_size
    query.ignore_patterns = final_ignore_patterns
    query.include_patterns = final_include_patterns # Store None if empty/not provided

    return query


async def _parse_remote_repo(source: str) -> IngestionQuery:
    """
    Parse a repository URL into a structured query dictionary.
    """
    source = unquote(source)
    parsed_url = urlparse(source)

    if parsed_url.scheme:
        _validate_url_scheme(parsed_url.scheme)
        _validate_host(parsed_url.netloc.lower())
    else:
        tmp_host = source.split("/")[0].lower()
        if "." in tmp_host:
            _validate_host(tmp_host)
        else:
            user_name_guess, repo_name_guess = _get_user_and_repo_from_path(source)
            host = await try_domains_for_user_and_repo(user_name_guess, repo_name_guess)
            source = f"{host}/{source}"
        source = "https://" + source
        parsed_url = urlparse(source)

    host = parsed_url.netloc.lower()
    user_name, repo_name = _get_user_and_repo_from_path(parsed_url.path)

    _id = str(uuid.uuid4())
    slug = f"{user_name}-{repo_name}"
    local_path = TMP_BASE_PATH / _id / slug
    url = f"https://{host}/{user_name}/{repo_name}"

    parsed = IngestionQuery(
        user_name=user_name,
        repo_name=repo_name,
        url=url,
        local_path=local_path,
        slug=slug,
        id=_id,
        subpath="/",
        type=None,
        branch=None,
        commit=None,
    )

    remaining_parts = parsed_url.path.strip("/").split("/")[2:]
    if not remaining_parts: return parsed

    possible_type = remaining_parts.pop(0)
    if not remaining_parts:
        if possible_type in ("issues", "pull"): return parsed
        parsed.type = possible_type
        return parsed

    if remaining_parts and possible_type in ("issues", "pull"): return parsed
    parsed.type = possible_type

    commit_or_branch = remaining_parts[0]
    if _is_valid_git_commit_hash(commit_or_branch):
        parsed.commit = commit_or_branch
        remaining_parts.pop(0)
    else:
        parsed.branch = await _configure_branch_and_subpath(remaining_parts, url)

    if remaining_parts:
        parsed.subpath = "/" + "/".join(remaining_parts)

    return parsed


async def _configure_branch_and_subpath(remaining_parts: List[str], url: str) -> Optional[str]:
    """
    Configure the branch and subpath based on the remaining parts of the URL.
    """
    try:
        branches: List[str] = await fetch_remote_branch_list(url)
    except Exception as exc:
        warnings.warn(f"Warning: Failed to fetch branch list: {exc}", RuntimeWarning)
        if remaining_parts: return remaining_parts.pop(0)
        return None

    branch_candidate = []
    matched_branch = None
    parts_consumed = 0
    for i in range(len(remaining_parts)):
        branch_candidate.append(remaining_parts[i])
        branch_name = "/".join(branch_candidate)
        if branch_name in branches:
            matched_branch = branch_name
            parts_consumed = i + 1

    if matched_branch:
        del remaining_parts[:parts_consumed]
        return matched_branch

    if remaining_parts: return remaining_parts.pop(0)
    return None


def _parse_patterns(pattern_input: Optional[Union[str, Set[str]]]) -> Set[str]:
    """
    Parse and validate file/directory patterns for inclusion or exclusion.
    Handles None, single string, or set of strings. Returns empty set for None.
    """
    if pattern_input is None:
        return set()
    if isinstance(pattern_input, str):
        # Treat empty string as no patterns provided
        if not pattern_input.strip():
            return set()
        patterns_to_process = {pattern_input}
    elif isinstance(pattern_input, set):
        patterns_to_process = pattern_input
    else:
        warnings.warn(f"Invalid pattern type received: {type(pattern_input)}. Ignoring.", UserWarning)
        return set()

    parsed_patterns: Set[str] = set()
    for p in patterns_to_process:
        # Ensure p is a string before splitting (might be redundant with checks above)
        if not isinstance(p, str): continue
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

    If the source is a zip file containing a single root directory, the `local_path`
    in the returned query will point *inside* that root directory.
    """
    if not path_str:
        raise ValueError("Local path cannot be empty.")

    is_zip = path_str.lower().endswith(".zip")
    temp_extract_path: Optional[Path] = None
    original_zip_path_obj: Optional[Path] = None

    try:
        path_obj = Path(path_str).resolve(strict=True)

        if is_zip:
            if not path_obj.is_file(): raise ValueError(f"Specified zip path is not a file: {path_str}")
            if not zipfile.is_zipfile(path_obj): raise ValueError(f"Specified path is not a valid zip file: {path_str}")

            original_zip_path_obj = path_obj
            _id = str(uuid.uuid4())
            temp_extract_base = TMP_BASE_PATH / _id
            temp_extract_path = temp_extract_base / path_obj.stem
            temp_extract_path.mkdir(parents=True, exist_ok=True)

            print(f"Extracting zip file {path_obj} to {temp_extract_path}...")
            with zipfile.ZipFile(path_obj, 'r') as zip_ref:
                zip_ref.extractall(temp_extract_path)
            print("Extraction complete.")

            extracted_items = list(temp_extract_path.iterdir())
            if len(extracted_items) == 1 and extracted_items[0].is_dir():
                print(f"Detected single root directory '{extracted_items[0].name}' in zip. Adjusting base path.")
                ingestion_path = extracted_items[0]
            else:
                ingestion_path = temp_extract_path

            # --- Slug for zip is always the zip filename stem ---
            slug = path_obj.stem
            # --- End Slug ---

        elif path_obj.is_dir():
            ingestion_path = path_obj
            # --- Slug logic for directories (Use final dir name) ---
            slug = path_obj.name # Always use the actual directory name as slug
            # --- End Slug ---
        elif path_obj.is_file():
             ingestion_path = path_obj
             # --- Slug for single file is filename stem ---
             slug = path_obj.stem
             # --- End Slug ---
        else:
             raise ValueError(f"Local path is not a directory, zip file, or regular file: {path_str}")

    except FileNotFoundError as e:
        raise ValueError(f"Local path not found: {path_str}") from e
    except zipfile.BadZipFile as e:
        raise ValueError(f"Error opening zip file '{path_str}': {e}") from e
    except Exception as e:
        if temp_extract_path and temp_extract_path.exists():
             cleanup_target = temp_extract_path.parent if 'ingestion_path' in locals() and ingestion_path != temp_extract_path else temp_extract_path
             if cleanup_target.is_relative_to(TMP_BASE_PATH):
                 shutil.rmtree(cleanup_target.parent, ignore_errors=True)
        raise ValueError(f"Error resolving or processing local path '{path_str}': {e}") from e

    # Create IngestionQuery
    return IngestionQuery(
        user_name=None,
        repo_name=None,
        url=None,
        local_path=ingestion_path,
        slug=slug, # Use the determined slug
        id=str(uuid.uuid4()),
        original_zip_path=original_zip_path_obj,
        temp_extract_path=temp_extract_path,
        subpath="/",
        type=None,
        branch=None,
        commit=None,
    )

async def try_domains_for_user_and_repo(user_name: str, repo_name: str) -> str:
    """
    Attempt to find a valid repository host for the given user_name and repo_name.
    """
    for domain in KNOWN_GIT_HOSTS:
        candidate = f"https://{domain}/{user_name}/{repo_name}"
        if await check_repo_exists(candidate):
            return domain
    raise ValueError(f"Could not find a valid repository host for '{user_name}/{repo_name}'.")

