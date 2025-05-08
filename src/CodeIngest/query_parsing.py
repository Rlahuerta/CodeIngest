# src/CodeIngest/query_parsing.py
"""This module contains functions to parse and validate input sources and patterns."""

import re
import uuid
import warnings
import os
import shutil
import zipfile
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
    # ... (docstring) ...
    query: IngestionQuery
    temp_extract_path: Optional[Path] = None
    original_zip_path: Optional[Path] = None

    if not source: raise ValueError("Input source cannot be empty.")

    source_path = Path(source)
    source_lower = source.lower()
    source_type = None # file, dir, zip, remote, unknown

    # --- Refined Source Type Detection ---

    # 1. Check if it's potentially a ZIP (by extension)
    is_potential_zip = source_lower.endswith(".zip")

    # 2. Check Remote URL criteria first (more specific than just local existence)
    parsed_source_url = urlparse(source)
    has_scheme = parsed_source_url.scheme in ("https", "http")
    has_known_host_domain = False
    if parsed_source_url.netloc:
        host_domain = parsed_source_url.netloc.lower()
        if host_domain in KNOWN_GIT_HOSTS: has_known_host_domain = True
    elif not has_scheme and not is_potential_zip: # Check string only if no scheme and not looking like zip
         for host in KNOWN_GIT_HOSTS:
             if source_lower.startswith(host + '/') or f'//{host}/' in source_lower:
                 has_known_host_domain = True; break

    is_remote_url = has_scheme or has_known_host_domain

    # 3. If not remote, check if it exists locally (could be zip, dir, or file)
    is_local = False
    if not is_remote_url:
        if source_path.exists():
            is_local = True
        else:
            # Doesn't exist locally. Could it be a remote slug?
            is_likely_slug = ("/" in source and "." not in source.split("/")[0] and not os.path.isabs(source))
            if is_likely_slug:
                is_remote_url = True # Treat as remote slug
            else:
                # Doesn't exist locally, doesn't look like a remote slug -> Path Not Found
                raise ValueError(f"Local path not found: {source}")

    # --- Processing Logic based on determined type ---
    if is_local:
        if is_potential_zip and source_path.is_file():
            # It exists locally and ends with .zip - TRY to treat as zip
            try:
                with zipfile.ZipFile(source_path, 'r') as zf_test: _ = zf_test.testzip()
                # --- Handle Valid ZIP ---
                source_type = "zip"
                zip_path = source_path
                unique_id = str(uuid.uuid4())
                base_extract_dir = TMP_BASE_PATH / "extracted_zips"; base_extract_dir.mkdir(parents=True, exist_ok=True)
                temp_extract_path = base_extract_dir / unique_id; temp_extract_path.mkdir()
                try:
                    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                        for member in zip_ref.namelist():
                            if member.startswith('/') or '..' in member: shutil.rmtree(temp_extract_path, ignore_errors=True); raise ValueError(f"ZIP unsafe path: {member}")
                        zip_ref.extractall(temp_extract_path)
                    local_path_for_query = temp_extract_path; slug = zip_path.stem; original_zip_path = zip_path.resolve()
                    query = IngestionQuery(local_path=local_path_for_query, slug=slug, id=unique_id, original_zip_path=original_zip_path, temp_extract_path=temp_extract_path, user_name=None, repo_name=None, url=None, subpath="/", type="zip", branch=None, commit=None)
                except Exception as e: # Catch extraction errors
                    if temp_extract_path and temp_extract_path.exists(): shutil.rmtree(temp_extract_path, ignore_errors=True)
                    raise ValueError(f"Error processing ZIP file '{source}': {e}") from e
            except zipfile.BadZipFile as e:
                 # If it exists, ends with .zip, but is invalid -> raise BadZipFile
                 raise zipfile.BadZipFile(f"Invalid ZIP file: {source}") from e
            except Exception as e: # Catch other potential errors opening file
                 raise ValueError(f"Error accessing local path '{source}': {e}") from e
        elif source_path.is_dir() or source_path.is_file():
             # --- Handle Local Dir/File ---
             source_type = "local"
             try: query = _parse_local_dir_path(source)
             except Exception as e: raise ValueError(f"Error parsing local path '{source}': {e}") from e
        else:
             # Exists but is not file/dir (e.g., broken symlink)
             raise ValueError(f"Local path exists but is not a file or directory: {source}")

    elif is_remote_url:
        # --- Handle Remote URL ---
        source_type = "remote"
        try: query = await _parse_remote_repo(source)
        except ValueError as e: raise e
        except Exception as e: raise ValueError(f"Error parsing remote source '{source}': {e}") from e

    else:
        # Should be unreachable due to prior existence check or slug check
        raise ValueError(f"Unable to determine source type for: {source}")


    # --- Process Patterns & Update Query ---
    ignore_patterns_set = DEFAULT_IGNORE_PATTERNS.copy()
    if ignore_patterns: ignore_patterns_set.update(_parse_patterns(ignore_patterns))
    parsed_include = None
    if include_patterns:
        parsed_include = _parse_patterns(include_patterns)
        if ignore_patterns_set: ignore_patterns_set -= parsed_include
        else: ignore_patterns_set = DEFAULT_IGNORE_PATTERNS - parsed_include
    query.max_file_size = max_file_size
    query.ignore_patterns = ignore_patterns_set
    query.include_patterns = parsed_include
    # Set type explicitly if it's local (and not zip)
    if source_type == 'local' and query.type is None: query.type = 'local'

    return query


def _parse_local_dir_path(path_str: str) -> IngestionQuery:
    # Existence check moved to parse_query
    try: path_obj = Path(path_str).resolve(strict=False)
    except Exception as e: raise ValueError(f"Error resolving local path '{path_str}': {e}") from e
    if path_str == ".": slug = Path.cwd().name
    else: slug = Path(path_str).name
    return IngestionQuery(
        local_path=path_obj, slug=slug, id=str(uuid.uuid4()),
        user_name=None, repo_name=None, url=None, subpath="/", type=None,
        branch=None, commit=None, ignore_patterns=None, include_patterns=None,
        original_zip_path=None, temp_extract_path=None
    )


async def _parse_remote_repo(source: str) -> IngestionQuery:
    """Parse a repository URL into a structured query dictionary."""
    source = unquote(source)
    parsed_url = urlparse(source)
    host = None
    path_part_for_user_repo = "" # Initialize

    if parsed_url.scheme:
        _validate_url_scheme(parsed_url.scheme)
        if not parsed_url.netloc: raise ValueError(f"Invalid URL: Missing host in {source}")
        host = parsed_url.netloc.lower()
        _validate_host(host)
        path_part_for_user_repo = parsed_url.path # Assign path here
    else:
        parts = source.split('/', 1)
        if "." in parts[0]: # github.com/user/repo
            host = parts[0].lower()
            _validate_host(host)
            path_part_for_user_repo = parts[1] if len(parts) > 1 else ""
            source_with_scheme = "https://" + source # Add scheme for consistency
        else: # user/repo slug
            user_name_guess, repo_name_guess = _get_user_and_repo_from_path(source)
            host = await try_domains_for_user_and_repo(user_name_guess, repo_name_guess)
            path_part_for_user_repo = source # The original source is the path
            source_with_scheme = f"https://{host}/{source}" # Add scheme and host

        parsed_url = urlparse(source_with_scheme) # Reparse if needed
        # Ensure path part is taken from the potentially updated parsed_url
        path_part_for_user_repo = parsed_url.path


    # Extract user/repo from the path part identified
    user_name, repo_name = _get_user_and_repo_from_path(path_part_for_user_repo)

    _id = str(uuid.uuid4())
    slug = f"{user_name}-{repo_name}"
    local_path = TMP_BASE_PATH / _id / slug
    url = f"https://{host}/{user_name}/{repo_name}"

    parsed = IngestionQuery(
        user_name=user_name, repo_name=repo_name, url=url,
        local_path=local_path, slug=slug, id=_id, subpath="/",
        type=None, branch=None, commit=None, ignore_patterns=None,
        include_patterns=None, original_zip_path=None, temp_extract_path=None
    )

    # (Rest of _parse_remote_repo remains the same...)
    remaining_parts = path_part_for_user_repo.strip("/").split("/")[2:]
    if not remaining_parts: return parsed
    possible_type = remaining_parts.pop(0)
    if possible_type in ("issues", "pull"): return parsed
    parsed.type = possible_type
    if not remaining_parts: return parsed
    commit_or_branch = remaining_parts[0]
    if _is_valid_git_commit_hash(commit_or_branch):
        parsed.commit = commit_or_branch; remaining_parts.pop(0)
    else:
        parsed.branch = await _configure_branch_and_subpath(remaining_parts, url)
    if remaining_parts: parsed.subpath = "/" + "/".join(remaining_parts)
    return parsed


async def _configure_branch_and_subpath(remaining_parts: List[str], url: str) -> Optional[str]:
    try: branches: List[str] = await fetch_remote_branch_list(url)
    except Exception as exc:
        warnings.warn(f"Warning: Failed to fetch branch list: {exc}", RuntimeWarning)
        if remaining_parts: return remaining_parts.pop(0)
        return None
    branch_candidate = []; matched_branch = None; parts_consumed = 0
    for i in range(len(remaining_parts)):
        branch_candidate.append(remaining_parts[i])
        branch_name = "/".join(branch_candidate)
        if branch_name in branches: matched_branch = branch_name; parts_consumed = i + 1
    if matched_branch: del remaining_parts[:parts_consumed]; return matched_branch
    if remaining_parts: return remaining_parts.pop(0)
    return None


def _parse_patterns(pattern: Union[str, Set[str]]) -> Set[str]:
    patterns_input = pattern if isinstance(pattern, set) else {pattern}
    parsed_patterns: Set[str] = set()
    for p in patterns_input: split_patterns = {part for part in re.split(r'[,\s]+', p) if part}; parsed_patterns.update(split_patterns)
    parsed_patterns = {p.replace("\\", "/") for p in parsed_patterns}
    validated_patterns = set()
    for p in parsed_patterns:
        if not _is_valid_pattern(p): raise InvalidPatternError(p)
        validated_patterns.add(_normalize_pattern(p))
    return validated_patterns


async def try_domains_for_user_and_repo(user_name: str, repo_name: str) -> str:
    for domain in KNOWN_GIT_HOSTS:
        candidate = f"https://{domain}/{user_name}/{repo_name}"
        if await check_repo_exists(candidate): return domain
    raise ValueError(f"Could not find a valid repository host for '{user_name}/{repo_name}'.")