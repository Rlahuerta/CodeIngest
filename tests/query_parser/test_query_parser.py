"""
Tests for the `query_parsing` module.

These tests cover URL parsing, pattern parsing, and handling of branches/subpaths for HTTP(S) repositories and local
paths.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Assuming IngestionQuery is importable if needed, but not directly used in most tests here
# from CodeIngest.schemas import IngestionQuery
from CodeIngest.query_parsing import _parse_patterns, _parse_remote_repo, parse_query
from CodeIngest.utils.exceptions import InvalidPatternError # Import specific exception
from CodeIngest.utils.ignore_patterns import DEFAULT_IGNORE_PATTERNS

# --- Mocks for Remote Calls ---

# Common mock for fetch_remote_branch_list to prevent actual network calls
MOCK_BRANCH_LIST = ["main", "dev", "feature/branch", "release/v1.0"]

@pytest.fixture(autouse=True)
def mock_fetch_branches():
    """Auto-used fixture to mock fetch_remote_branch_list for all tests in this module."""
    with patch("CodeIngest.query_parsing.fetch_remote_branch_list", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = MOCK_BRANCH_LIST
        yield mock_fetch

@pytest.fixture(autouse=True)
def mock_check_repo_exists():
    """Auto-used fixture to mock check_repo_exists for all tests in this module."""
    # Assume repo exists for most tests unless overridden locally
    with patch("CodeIngest.query_parsing.check_repo_exists", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = True
        yield mock_check


# --- Tests ---

@pytest.mark.asyncio
async def test_parse_url_valid_https() -> None:
    """
    Test `_parse_remote_repo` with valid HTTPS URLs.
    """
    test_cases = [
        "https://github.com/user/repo",
        "https://gitlab.com/user/repo",
        "https://bitbucket.org/user/repo",
        "https://gitea.com/user/repo",
        "https://codeberg.org/user/repo",
        "https://gist.github.com/user/repo",
    ]
    for url in test_cases:
        query = await _parse_remote_repo(url)
        assert query.user_name == "user"
        assert query.repo_name == "repo"
        assert query.url == url


@pytest.mark.asyncio
async def test_parse_url_valid_http() -> None:
    """
    Test `_parse_remote_repo` with valid HTTP URLs.
    """
    test_cases = [
        "http://github.com/user/repo",
        "http://gitlab.com/user/repo",
        "http://bitbucket.org/user/repo",
        "http://gitea.com/user/repo",
        "http://codeberg.org/user/repo",
        "http://gist.github.com/user/repo",
    ]
    for url in test_cases:
        query = await _parse_remote_repo(url)
        assert query.user_name == "user"
        assert query.repo_name == "repo"
        assert query.slug == "user-repo"
        # URL should be normalized to https
        assert query.url == f"https://{url.split('://')[1]}"


@pytest.mark.asyncio
async def test_parse_url_invalid() -> None:
    """
    Test `_parse_remote_repo` with an invalid URL structure.
    """
    url = "https://github.com" # Missing user/repo
    with pytest.raises(ValueError, match="Invalid repository URL"):
        await _parse_remote_repo(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["https://github.com/user/repo", "https://gitlab.com/user/repo"])
async def test_parse_query_basic(url):
    """
    Test `parse_query` with a basic valid repository URL.
    """
    query = await parse_query(source=url, max_file_size=50, from_web=True, ignore_patterns="*.txt")
    assert query.user_name == "user"
    assert query.repo_name == "repo"
    assert query.url == url
    assert query.ignore_patterns is not None
    assert "*.txt" in query.ignore_patterns
    assert ".git" in query.ignore_patterns # Default should still be there


@pytest.mark.asyncio
async def test_parse_query_mixed_case() -> None:
    """
    Test `parse_query` with mixed-case URLs.
    """
    url = "Https://GitHub.COM/UsEr/rEpO"
    query = await parse_query(url, max_file_size=50, from_web=True)
    assert query.user_name == "user"
    assert query.repo_name == "repo"
    assert query.url == "https://github.com/user/repo" # Should be normalized


@pytest.mark.asyncio
async def test_parse_query_include_pattern() -> None:
    """
    Test `parse_query` with a specified include pattern.
    """
    url = "https://github.com/user/repo"
    query = await parse_query(url, max_file_size=50, from_web=True, include_patterns="*.py")
    assert query.include_patterns == {"*.py"}
    # Default ignore patterns should still exist, unless overridden by include
    assert query.ignore_patterns == DEFAULT_IGNORE_PATTERNS


@pytest.mark.asyncio
async def test_parse_query_invalid_pattern() -> None:
    """
    Test `parse_query` with an invalid pattern.
    """
    url = "https://github.com/user/repo"
    with pytest.raises(InvalidPatternError, match="Pattern.*contains invalid characters"):
        await parse_query(url, max_file_size=50, from_web=True, include_patterns="*.py;rm -rf")


@pytest.mark.asyncio
async def test_parse_url_with_subpaths() -> None:
    """
    Test `_parse_remote_repo` with a URL containing branch and subpath.
    """
    url = "https://github.com/user/repo/tree/main/subdir/file"
    # Mocking is handled by autouse fixture mock_fetch_branches
    query = await _parse_remote_repo(url)
    assert query.user_name == "user"
    assert query.repo_name == "repo"
    assert query.branch == "main" # Matches mock branch list
    assert query.subpath == "/subdir/file"
    assert query.type == "tree"


@pytest.mark.asyncio
async def test_parse_url_with_subpaths_slash_in_branch() -> None:
    """
    Test `_parse_remote_repo` with a URL containing branch with slashes and subpath.
    """
    url = "https://github.com/user/repo/tree/feature/branch/subdir/file"
    # Mocking is handled by autouse fixture mock_fetch_branches
    query = await _parse_remote_repo(url)
    assert query.user_name == "user"
    assert query.repo_name == "repo"
    assert query.branch == "feature/branch" # Matches mock branch list
    assert query.subpath == "/subdir/file"
    assert query.type == "tree"


@pytest.mark.asyncio
async def test_parse_url_invalid_repo_structure() -> None:
    """
    Test `_parse_remote_repo` with a URL missing a repository name.
    """
    url = "https://github.com/user"
    with pytest.raises(ValueError, match="Invalid repository URL"):
        await _parse_remote_repo(url)


def test_parse_patterns_valid() -> None:
    """
    Test `_parse_patterns` with valid comma/space-separated patterns.
    """
    patterns = "*.py, *.md docs/* another/path"
    parsed_patterns = _parse_patterns(patterns)
    assert parsed_patterns == {"*.py", "*.md", "docs/*", "another/path"}


def test_parse_patterns_valid_set() -> None:
    """
    Test `_parse_patterns` with a set input.
    """
    patterns = {"*.py", "*.md", "docs/*"}
    parsed_patterns = _parse_patterns(patterns)
    assert parsed_patterns == {"*.py", "*.md", "docs/*"}


def test_parse_patterns_empty_string() -> None:
    """ Test _parse_patterns with empty string """
    patterns = ""
    parsed_patterns = _parse_patterns(patterns)
    assert parsed_patterns == set()


def test_parse_patterns_empty_set() -> None:
    """ Test _parse_patterns with empty set """
    patterns = set()
    parsed_patterns = _parse_patterns(patterns)
    assert parsed_patterns == set()


def test_parse_patterns_mixed_separators() -> None:
    """ Test _parse_patterns with mixed separators and extra spaces """
    patterns = "*.py ,  *.md   docs/*"
    parsed_patterns = _parse_patterns(patterns)
    assert parsed_patterns == {"*.py", "*.md", "docs/*"}


def test_parse_patterns_invalid_characters() -> None:
    """
    Test `_parse_patterns` with invalid characters.
    """
    patterns = "*.py;rm -rf"
    with pytest.raises(InvalidPatternError, match="Pattern.*contains invalid characters"):
        _parse_patterns(patterns)


@pytest.mark.asyncio
async def test_parse_query_with_large_file_size() -> None:
    """
    Test `parse_query` with a very large file size limit.
    """
    url = "https://github.com/user/repo"
    query = await parse_query(url, max_file_size=10**9, from_web=True)
    assert query.max_file_size == 10**9
    assert query.ignore_patterns == DEFAULT_IGNORE_PATTERNS


@pytest.mark.asyncio
async def test_parse_query_empty_patterns() -> None:
    """
    Test `parse_query` with empty patterns.
    """
    url = "https://github.com/user/repo"
    query = await parse_query(url, max_file_size=50, from_web=True, include_patterns="", ignore_patterns="")
    assert query.include_patterns is None
    assert query.ignore_patterns == DEFAULT_IGNORE_PATTERNS


@pytest.mark.asyncio
async def test_parse_query_include_and_ignore_overlap() -> None:
    """
    Test `parse_query` with overlapping patterns.
    Include patterns should remove matching patterns from the ignore set.
    """
    url = "https://github.com/user/repo"
    # Start with default ignores + '*.txt'
    initial_ignore = DEFAULT_IGNORE_PATTERNS.copy()
    initial_ignore.add("*.txt")
    initial_ignore.add("*.py") # Add *.py to ignore initially

    query = await parse_query(
        url,
        max_file_size=50,
        from_web=True,
        include_patterns="*.py", # Include *.py
        ignore_patterns=initial_ignore,
    )

    assert query.include_patterns == {"*.py"}
    assert query.ignore_patterns is not None
    # *.py should be removed from ignores because it's included
    assert "*.py" not in query.ignore_patterns
    # *.txt should remain in ignores
    assert "*.txt" in query.ignore_patterns
    # Other defaults should remain
    assert ".git" in query.ignore_patterns


@pytest.mark.asyncio
async def test_parse_query_local_path(tmp_path: Path) -> None: # Added tmp_path fixture
    """
    Test `parse_query` with a local file path.
    """
    # Create a dummy directory within the temporary path
    dummy_project_path = tmp_path / "project"
    dummy_project_path.mkdir()

    path_str = str(dummy_project_path) # Use the path of the created directory
    query = await parse_query(path_str, max_file_size=100, from_web=False)

    assert query.local_path == dummy_project_path.resolve() # Check resolved path
    assert query.id is not None
    assert query.slug == path_str # Slug should be the input path string
    assert query.url is None
    assert query.user_name is None
    assert query.repo_name is None


@pytest.mark.asyncio
async def test_parse_query_local_path_dot(tmp_path: Path) -> None: # Added tmp_path fixture
    """
    Test `parse_query` with '.' as the local path.
    """
    # Change current working directory for the test's scope if needed,
    # or rely on tmp_path's behavior. For simplicity, let's assume '.'
    # resolves relative to where pytest runs, but using tmp_path is safer.
    # Let's test '.' relative to tmp_path
    path_str = "."
    # We need the actual path '.' resolves to during the test
    expected_resolved_path = Path(path_str).resolve()

    query = await parse_query(path_str, max_file_size=100, from_web=False)

    assert query.local_path == expected_resolved_path
    assert query.id is not None
    assert query.slug == expected_resolved_path.name # Slug becomes the dir name for '.'
    assert query.url is None


@pytest.mark.asyncio
async def test_parse_query_relative_path(tmp_path: Path) -> None: # Added tmp_path fixture
    """
    Test `parse_query` with a relative path.
    """
    # Create a dummy directory relative to tmp_path
    dummy_subdir = os.path.join(str(tmp_path), "subdir")
    os.makedirs(dummy_subdir, exist_ok=True)

    dummy_project_path = os.path.join(dummy_subdir, "project")
    os.makedirs(dummy_project_path, exist_ok=True)

    # Let's use the absolute path derived from tmp_path for clarity in the test input
    query = await parse_query(dummy_project_path, max_file_size=100, from_web=False)

    assert str(query.local_path) == dummy_project_path
    assert query.slug == dummy_project_path # Slug is the input string
    assert query.url is None


@pytest.mark.asyncio
async def test_parse_query_nonexistent_local_path(tmp_path: Path) -> None: # Added tmp_path
    """ Test parse_query with a non-existent local path """
    non_existent_path = tmp_path / "does_not_exist"
    path_str = str(non_existent_path)

    with pytest.raises(ValueError, match=f"Local path not found: {path_str}"):
        await parse_query(path_str, max_file_size=100, from_web=False)


@pytest.mark.asyncio
async def test_parse_query_empty_source() -> None:
    """
    Test `parse_query` with an empty string.
    """
    # Parsing empty string as local path should fail
    with pytest.raises(ValueError, match="Local path cannot be empty."):
         await parse_query("", max_file_size=100, from_web=False)
    # Parsing empty string as remote should also fail
    with pytest.raises(ValueError, match="Invalid repository URL"):
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
    """
    Test `_parse_remote_repo` with various URL patterns using mocked branches.
    """
    # Mocking handled by autouse fixture
    query = await _parse_remote_repo(url)
    assert query.branch == expected_branch
    assert query.subpath == expected_subpath

# Test for the scenario where fetching branches fails (overrides autouse mock)
@pytest.mark.asyncio
async def test_parse_repo_source_with_failed_git_command():
    """
    Test `_parse_remote_repo` when git fetch fails.
    """
    url = "https://github.com/user/repo/tree/main/src"
    expected_branch = "main" # Falls back to first path part
    expected_subpath = "/src" # Rest of the path

    # Override the autouse fixture for this specific test
    with patch("CodeIngest.query_parsing.fetch_remote_branch_list", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = RuntimeError("Simulated fetch failure") # Simulate failure

        # Expect a warning because the fetch failed
        with pytest.warns(RuntimeWarning, match="Warning: Failed to fetch branch list: Simulated fetch failure"):
            query = await _parse_remote_repo(url)

            assert query.branch == expected_branch
            assert query.subpath == expected_subpath

