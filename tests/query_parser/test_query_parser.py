"""
Tests for the `query_parsing` module.

These tests cover URL parsing, pattern parsing, and handling of branches/subpaths for HTTP(S) repositories and local
paths.
"""

import os
import pytest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Assuming IngestionQuery is importable if needed, but not directly used in most tests here
from CodeIngest.ingestion import ingest_query
from CodeIngest.query_parsing import _parse_patterns, _parse_remote_repo, parse_query
from CodeIngest.utils.exceptions import InvalidPatternError
from CodeIngest.utils.ignore_patterns import DEFAULT_IGNORE_PATTERNS

# --- Mocks for Remote Calls ---

# Common mock for fetch_remote_branch_list to prevent actual network calls
MOCK_BRANCH_LIST = ["main", "dev", "feature/branch", "release/v1.0"]
@pytest.fixture(autouse=True)
def mock_fetch_branches():
    with patch("CodeIngest.query_parsing.fetch_remote_branch_list", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = MOCK_BRANCH_LIST; yield mock_fetch
@pytest.fixture(autouse=True)
def mock_check_repo_exists():
    with patch("CodeIngest.query_parsing.check_repo_exists", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = True; yield mock_check


# --- Tests ---
@pytest.mark.asyncio
async def test_parse_url_valid_https() -> None:
    test_cases = [ "https://github.com/user/repo", "https://gitlab.com/user/repo", # ... (rest are ok)
                   "https://gist.github.com/user/repo" ]
    for url in test_cases:
        query = await _parse_remote_repo(url)
        assert query.user_name == "user"; assert query.repo_name == "repo"; assert query.url == url

@pytest.mark.asyncio
async def test_parse_url_valid_http() -> None:
    test_cases = [ "http://github.com/user/repo", "http://gitlab.com/user/repo", # ... (rest are ok)
                   "http://gist.github.com/user/repo" ]
    for url in test_cases:
        query = await _parse_remote_repo(url)
        assert query.user_name == "user"; assert query.repo_name == "repo"; assert query.slug == "user-repo"
        assert query.url == f"https://{url.split('://')[1]}"

@pytest.mark.asyncio
async def test_parse_url_invalid() -> None:
    url = "https://github.com"
    with pytest.raises(ValueError, match="Invalid repository URL"): await _parse_remote_repo(url)

@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["https://github.com/user/repo", "https://gitlab.com/user/repo"])
async def test_parse_query_basic(url):
    query = await parse_query(source=url, max_file_size=50, from_web=True, ignore_patterns="*.txt")
    assert query.user_name == "user"; assert query.repo_name == "repo"; assert query.url == url
    assert query.ignore_patterns is not None; assert "*.txt" in query.ignore_patterns
    assert ".git" in query.ignore_patterns

@pytest.mark.asyncio
async def test_parse_query_mixed_case() -> None:
    url = "Https://GitHub.COM/UsEr/rEpO"
    query = await parse_query(url, max_file_size=50, from_web=True)
    assert query.user_name == "user"; assert query.repo_name == "repo"
    assert query.url == "https://github.com/user/repo"

@pytest.mark.asyncio
async def test_parse_query_include_pattern() -> None:
    url = "https://github.com/user/repo"
    query = await parse_query(url, max_file_size=50, from_web=True, include_patterns="*.py")
    assert query.include_patterns == {"*.py"}
    # Check only default ignores REMAIN after include override logic
    assert query.ignore_patterns == DEFAULT_IGNORE_PATTERNS - {"*.py"} # Check difference

@pytest.mark.asyncio
async def test_parse_query_invalid_pattern() -> None:
    url = "https://github.com/user/repo"
    with pytest.raises(InvalidPatternError, match="Pattern.*contains invalid characters"):
        await parse_query(url, max_file_size=50, from_web=True, include_patterns="*.py;rm -rf")

@pytest.mark.asyncio
async def test_parse_url_with_subpaths() -> None:
    url = "https://github.com/user/repo/tree/main/subdir/file"
    query = await _parse_remote_repo(url)
    assert query.user_name == "user"; assert query.repo_name == "repo"
    assert query.branch == "main"; assert query.subpath == "/subdir/file"; assert query.type == "tree"

@pytest.mark.asyncio
async def test_parse_url_with_subpaths_slash_in_branch() -> None:
    url = "https://github.com/user/repo/tree/feature/branch/subdir/file"
    query = await _parse_remote_repo(url)
    assert query.user_name == "user"; assert query.repo_name == "repo"
    assert query.branch == "feature/branch"; assert query.subpath == "/subdir/file"; assert query.type == "tree"

@pytest.mark.asyncio
async def test_parse_url_invalid_repo_structure() -> None:
    url = "https://github.com/user"
    with pytest.raises(ValueError, match="Invalid repository URL"): await _parse_remote_repo(url)

def test_parse_patterns_valid() -> None:
    patterns = "*.py, *.md docs/* another/path"; parsed = _parse_patterns(patterns)
    assert parsed == {"*.py", "*.md", "docs/*", "another/path"}

def test_parse_patterns_valid_set() -> None:
    patterns = {"*.py", "*.md", "docs/*"}; parsed = _parse_patterns(patterns)
    assert parsed == {"*.py", "*.md", "docs/*"}

def test_parse_patterns_empty_string() -> None:
    assert _parse_patterns("") == set()

def test_parse_patterns_empty_set() -> None:
    assert _parse_patterns(set()) == set()

def test_parse_patterns_mixed_separators() -> None:
    patterns = "*.py ,  *.md   docs/*"; parsed = _parse_patterns(patterns)
    assert parsed == {"*.py", "*.md", "docs/*"}

def test_parse_patterns_invalid_characters() -> None:
    with pytest.raises(InvalidPatternError): _parse_patterns("*.py;rm -rf")

@pytest.mark.asyncio
async def test_parse_query_with_large_file_size() -> None:
    url = "https://github.com/user/repo"
    query = await parse_query(url, max_file_size=10**9, from_web=True)
    assert query.max_file_size == 10**9; assert query.ignore_patterns == DEFAULT_IGNORE_PATTERNS

@pytest.mark.asyncio
async def test_parse_query_empty_patterns() -> None:
    url = "https://github.com/user/repo"
    query = await parse_query(url, max_file_size=50, from_web=True, include_patterns="", ignore_patterns="")
    assert query.include_patterns is None; assert query.ignore_patterns == DEFAULT_IGNORE_PATTERNS

@pytest.mark.asyncio
async def test_parse_query_include_and_ignore_overlap() -> None:
    url = "https://github.com/user/repo"; initial_ignore = DEFAULT_IGNORE_PATTERNS.copy()
    initial_ignore.add("*.txt"); initial_ignore.add("*.py")
    query = await parse_query(url, max_file_size=50, from_web=True, include_patterns="*.py", ignore_patterns=initial_ignore)
    assert query.include_patterns == {"*.py"}; assert query.ignore_patterns is not None
    assert "*.py" not in query.ignore_patterns; assert "*.txt" in query.ignore_patterns
    assert ".git" in query.ignore_patterns

@pytest.mark.asyncio
async def test_parse_query_local_path(tmp_path: Path) -> None:
    dummy_project_path = tmp_path / "project"; dummy_project_path.mkdir()
    path_str = str(dummy_project_path)
    query = await parse_query(path_str, max_file_size=100, from_web=False)
    assert query.local_path == dummy_project_path.resolve(); assert query.id is not None
    assert query.slug == "project"; assert query.url is None; assert query.type == 'local'

@pytest.mark.asyncio
async def test_parse_query_local_path_dot(tmp_path: Path) -> None:
    expected_resolved_path = Path(".").resolve() # Resolve relative to actual CWD
    # Create a file inside the current dir for the test scope
    (tmp_path / "dummy_in_dot.txt").touch()
    query = await parse_query(".", max_file_size=100, from_web=False)
    assert query.local_path == expected_resolved_path; assert query.id is not None
    assert query.slug == expected_resolved_path.name; assert query.url is None; assert query.type == 'local'

@pytest.mark.asyncio
async def test_parse_query_relative_path(tmp_path: Path) -> None:
    # Create structure relative to tmp_path
    sub_dir = tmp_path / "subdir"; sub_dir.mkdir()
    project_dir = sub_dir / "project"; project_dir.mkdir()
    # Use a relative path string for input
    relative_path_str = os.path.join("subdir", "project")
    # Change CWD for the duration of the test to tmp_path
    original_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        query = await parse_query(relative_path_str, max_file_size=100, from_web=False)
        assert query.local_path == project_dir.resolve()
        assert query.slug == "project" # Slug should be the last component
        assert query.url is None; assert query.type == 'local'
    finally:
        os.chdir(original_cwd)


@pytest.mark.asyncio
async def test_parse_query_nonexistent_local_path(tmp_path: Path) -> None:
    """ Test parse_query raises error for a non-existent local path """
    non_existent_path = tmp_path / "does_not_exist"
    path_str = str(non_existent_path)
    # The refined parse_query should now raise the error directly
    with pytest.raises(ValueError, match=f"Local path not found: {path_str}"):
        await parse_query(path_str, max_file_size=100, from_web=False)


@pytest.mark.asyncio
async def test_parse_query_empty_source() -> None:
    """ Test `parse_query` with an empty string """
    # Should raise the initial check error regardless of from_web
    with pytest.raises(ValueError, match="Input source cannot be empty."):
         await parse_query("", max_file_size=100, from_web=False)
    with pytest.raises(ValueError, match="Input source cannot be empty."):
         await parse_query("", max_file_size=100, from_web=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url, expected_branch, expected_commit",
    [
        ("https://github.com/user/repo/tree/main", "main", None), # Branch from mock list
        ("https://github.com/user/repo/tree/dev", "dev", None),   # Branch from mock list
        (
            "https://github.com/user/repo/tree/abcd1234abcd1234abcd1234abcd1234abcd1234", # Commit hash
            None,
            "abcd1234abcd1234abcd1234abcd1234abcd1234",
        ),
         ("https://github.com/user/repo/tree/not_a_branch", "not_a_branch", None), # Not in mock list, treated as branch/tag
    ],
)
async def test_parse_url_branch_and_commit_distinction(url: str, expected_branch: str, expected_commit: str) -> None:
    """
    Test `_parse_remote_repo` distinguishing branch vs. commit hash.
    Uses the auto-mocked fetch_remote_branch_list.
    """
    query = await _parse_remote_repo(url)
    assert query.branch == expected_branch
    assert query.commit == expected_commit


@pytest.mark.asyncio
async def test_parse_query_uuid_uniqueness(tmp_path: Path) -> None: # Added tmp_path
    """
    Test `parse_query` for unique UUID generation for local paths.
    """
    dummy_project_path = tmp_path / "project_for_uuid"
    dummy_project_path.mkdir()
    path_str = str(dummy_project_path)

    query_1 = await parse_query(path_str, max_file_size=100, from_web=False)
    query_2 = await parse_query(path_str, max_file_size=100, from_web=False)

    assert query_1.id != query_2.id


@pytest.mark.asyncio
async def test_parse_url_with_query_and_fragment() -> None:
    """
    Test `_parse_remote_repo` with query parameters and a fragment.
    """
    url = "https://github.com/user/repo?arg=value#fragment"
    query = await _parse_remote_repo(url)
    assert query.user_name == "user"
    assert query.repo_name == "repo"
    assert query.url == "https://github.com/user/repo"  # URL should be cleaned


@pytest.mark.asyncio
async def test_parse_url_unsupported_host(mock_check_repo_exists) -> None: # Use mock explicitly
    """
    Test `_parse_remote_repo` with an unsupported host.
    """
    url = "https://unsupported.com/user/repo"
    # Need to mock check_repo_exists to return False for the unsupported host guess
    mock_check_repo_exists.return_value = False
    with pytest.raises(ValueError, match="Unknown domain 'unsupported.com' in URL"):
        await _parse_remote_repo(url)


@pytest.mark.asyncio
async def test_parse_query_with_branch_in_blob() -> None:
    """
    Test `parse_query` when a branch is specified in a blob path.
    """
    url = "https://github.com/pandas-dev/pandas/blob/main/.github/ISSUE_TEMPLATE/bug_report.md"
    # Mocking handled by autouse fixture
    query = await parse_query(url, max_file_size=10**9, from_web=True)

    assert query.user_name == "pandas-dev"
    assert query.repo_name == "pandas"
    assert query.url == "https://github.com/pandas-dev/pandas"
    assert query.slug == "pandas-dev-pandas"
    assert query.id is not None
    assert query.subpath == "/.github/ISSUE_TEMPLATE/bug_report.md"
    assert query.branch == "main" # Matches mock branch list
    assert query.commit is None
    assert query.type == "blob"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url, expected_branch, expected_subpath",
    [
        ("https://github.com/user/repo/tree/main/src", "main", "/src"),
        ("https://github.com/user/repo/tree/dev", "dev", "/"), # Branch only
        ("https://github.com/user/repo/tree/feature/branch/docs", "feature/branch", "/docs"), # Branch w/ slash
        ("https://github.com/user/repo/tree/release/v1.0", "release/v1.0", "/"), # Branch w/ dot
        ("https://github.com/user/repo/tree/nonexistent-branch/src", "nonexistent-branch", "/src"), # Not in mock list
        ("https://github.com/user/repo", None, "/"),  # No branch/commit/tag part
        ("https://github.com/user/repo/blob/main/file.txt", "main", "/file.txt"), # Blob path
    ],
)
async def test_parse_repo_source_with_various_url_patterns(url, expected_branch, expected_subpath):
    query = await _parse_remote_repo(url)
    assert query.branch == expected_branch; assert query.subpath == expected_subpath

@pytest.mark.asyncio
async def test_parse_repo_source_with_failed_git_command():
    url = "https://github.com/user/repo/tree/main/src"; expected_branch = "main"; expected_subpath = "/src"
    with patch("CodeIngest.query_parsing.fetch_remote_branch_list", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = RuntimeError("Simulated fetch failure")
        with pytest.warns(RuntimeWarning, match="Warning: Failed to fetch branch list"):
            query = await _parse_remote_repo(url)
            assert query.branch == expected_branch; assert query.subpath == expected_subpath
