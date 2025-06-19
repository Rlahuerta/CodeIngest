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
    # ... (docstring remains the same)
    query: IngestionQuery
    temp_extract_path: Optional[Path] = None
    original_zip_path: Optional[Path] = None

    if not source: raise ValueError("Input source cannot be empty.")

    source_path = Path(source)
    source_lower = source.lower()
    source_type_determined = None

    # --- Refined Source Type Detection ---

    # 1. If it ends with .zip, attempt to treat as ZIP first.
    if source_lower.endswith(".zip"):
        if not source_path.is_file():
            # If it's named .zip but isn't an existing file, it's likely a path error.
            raise ValueError(f"Local path not found: {source}")
        try:
            # Attempt to open as ZIP - this raises BadZipFile if invalid
            with zipfile.ZipFile(source_path, 'r') as zf_test:
                _ = zf_test.testzip() # Basic integrity check

            # If no error, it's a valid zip file path
            source_type_determined = "zip"
            # --- Handle ZIP Extraction ---
            unique_id = str(uuid.uuid4())
            base_extract_dir = TMP_BASE_PATH / "extracted_zips"; base_extract_dir.mkdir(parents=True, exist_ok=True)
            temp_extract_path = base_extract_dir / unique_id; temp_extract_path.mkdir()
            try:
                with zipfile.ZipFile(source_path, 'r') as zip_ref:
                    for member in zip_ref.namelist():
                        if member.startswith('/') or '..' in member:
                            shutil.rmtree(temp_extract_path, ignore_errors=True); raise ValueError(f"ZIP contains unsafe path: {member}")
                    zip_ref.extractall(temp_extract_path)
                local_path_for_query = temp_extract_path; slug = source_path.stem; original_zip_path = source_path.resolve()
                query = IngestionQuery(
                    local_path=local_path_for_query, slug=slug, id=unique_id, original_zip_path=original_zip_path, temp_extract_path=temp_extract_path,
                    user_name=None, repo_name=None, url=None, subpath="/", type="zip", branch=None, commit=None,
                )
            except zipfile.BadZipFile as e: # Should ideally be caught by the first check
                    if temp_extract_path and temp_extract_path.exists(): shutil.rmtree(temp_extract_path, ignore_errors=True)
                    raise zipfile.BadZipFile(f"Invalid ZIP file (extraction failed): {source}") from e
            except Exception as e:
                    if temp_extract_path and temp_extract_path.exists(): shutil.rmtree(temp_extract_path, ignore_errors=True)
                    raise ValueError(f"Error processing ZIP file '{source}': {e}") from e

        except zipfile.BadZipFile as e:
             # It IS an existing file ending with .zip, but invalid. Raise BadZipFile.
             raise zipfile.BadZipFile(f"Invalid ZIP file: {source}") from e
        except FileNotFoundError: # Should not happen if source_path.is_file() was true
            raise ValueError(f"Local path not found: {source}") # Should be caught by is_file
        except Exception as e: # Other errors opening/accessing the path
            raise ValueError(f"Error accessing path '{source}': {e}")


    # 2. If not identified and processed as a valid ZIP, check for Remote URL criteria
    if source_type_determined is None:
        parsed_source_url = urlparse(source)
        has_scheme = parsed_source_url.scheme in ("https", "http")
        has_known_host_domain = False
        if parsed_source_url.netloc:
            host_domain = parsed_source_url.netloc.lower()
            if host_domain in KNOWN_GIT_HOSTS: has_known_host_domain = True
        elif not has_scheme:
             for host in KNOWN_GIT_HOSTS:
                 if source_lower.startswith(host + '/') or f'//{host}/' in source_lower:
                     has_known_host_domain = True; break

        is_likely_slug_for_remote = ("/" in source and "." not in source.split("/")[0] and
                                     not os.path.isabs(source) and not Path(source).exists())

        if has_scheme or has_known_host_domain or (is_likely_slug_for_remote and from_web):
            source_type_determined = "remote"
            try: query = await _parse_remote_repo(source)
            except ValueError as e: raise e
            except Exception as e: raise ValueError(f"Error parsing remote source '{source}': {e}") from e

    # 3. If not ZIP or Remote, treat as Local Path (which might be a non-zip file or a dir)
    if source_type_determined is None:
        if not source_path.exists():
            raise ValueError(f"Local path not found: {source}")
        if not source_path.is_dir() and not source_path.is_file():
             raise ValueError(f"Local path exists but is not a file or directory: {source}")

        source_type_determined = "local" # Could be local file or dir
        try: query = _parse_local_dir_path(source)
        except Exception as e: raise ValueError(f"Error parsing local path '{source}': {e}") from e

    # --- Final Guard ---
    if source_type_determined is None or 'query' not in locals():
        # This implies it wasn't a valid zip (failed testzip), not remote, and not an existing local file/dir.
        # This primarily catches cases like a non-existent path that *doesn't* end in .zip.
        raise ValueError(f"Local path not found: {source}")


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
    if source_type_determined == 'local' and query.type is None: query.type = 'local'

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
        path_part_for_user_repo = parsed_url.path
    else:
        parts = source.split('/', 1)
        if "." in parts[0]:
            host = parts[0].lower()
            _validate_host(host)
            path_part_for_user_repo = parts[1] if len(parts) > 1 else ""
            source_with_scheme = "https://" + source
        else:
            user_name_guess, repo_name_guess = _get_user_and_repo_from_path(source)
            host = await try_domains_for_user_and_repo(user_name_guess, repo_name_guess)
            path_part_for_user_repo = source
            source_with_scheme = f"https://{host}/{source}"

        parsed_url = urlparse(source_with_scheme)
        path_part_for_user_repo = parsed_url.path


    # Extract user/repo from the path part identified
    user_name, repo_name = _get_user_and_repo_from_path(path_part_for_user_repo)

    # --- FIX: Remove .git suffix from repo_name if present ---
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]
    # --- END FIX ---

    _id = str(uuid.uuid4())
    slug = f"{user_name}-{repo_name}" # Use cleaned repo_name
    local_path = TMP_BASE_PATH / _id / slug
    # Construct the final canonical URL using cleaned repo_name
    url = f"https://{host}/{user_name}/{repo_name}" # Use cleaned repo_name

    parsed = IngestionQuery(
        user_name=user_name, repo_name=repo_name, url=url, # Use cleaned names/url
        local_path=local_path, slug=slug, id=_id, subpath="/",
        type=None, branch=None, commit=None, ignore_patterns=None,
        include_patterns=None, original_zip_path=None, temp_extract_path=None
    )

    # (Rest of _parse_remote_repo remains the same...)
    remaining_parts = path_part_for_user_repo.strip("/").split("/")[2:]
    # Remove .git from remaining parts if it exists
    if remaining_parts and remaining_parts[-1].lower() == ".git":
        remaining_parts.pop()

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