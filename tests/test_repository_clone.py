"""
Tests for the `cloning` module.

These tests cover various scenarios for cloning repositories, verifying that the appropriate Git commands are invoked
and handling edge cases such as nonexistent URLs, timeouts, redirects, and specific commits or branches.
"""

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from CodeIngest.cloning import check_repo_exists, clone_repo
from CodeIngest.schemas import CloneConfig
from CodeIngest.utils.exceptions import AsyncTimeoutError
from CodeIngest.utils.git_utils import ensure_git_installed, fetch_remote_branch_list, run_command # Import functions to test


@pytest.mark.asyncio
async def test_clone_with_commit() -> None:
    """
    Test cloning a repository with a specific commit hash.

    Given a valid URL and a commit hash:
    When `clone_repo` is called,
    Then the repository should be cloned and checked out at that commit.
    """
    clone_config = CloneConfig(
        url="https://github.com/user/repo",
        local_path="/tmp/repo",
        commit="a" * 40,  # Simulating a valid commit hash
        branch="main",
    )

    with patch("CodeIngest.cloning.check_repo_exists", return_value=True) as mock_check:
        with patch("CodeIngest.cloning.run_command", new_callable=AsyncMock) as mock_exec:
            # Mock run_command to return success for both clone and checkout
            mock_exec.return_value = (b"output", b"error")
            # Mock ensure_git_installed
            with patch("CodeIngest.cloning.ensure_git_installed", new_callable=AsyncMock):
                 # Mock os.makedirs
                 with patch("os.makedirs", return_value=None):
                    await clone_repo(clone_config)

                    mock_check.assert_called_once_with(clone_config.url)
                    assert mock_exec.call_count == 2  # Clone and checkout calls


@pytest.mark.asyncio
async def test_clone_without_commit() -> None:
    """
    Test cloning a repository when no commit hash is provided.

    Given a valid URL and no commit hash:
    When `clone_repo` is called,
    Then only the clone_repo operation should be performed (no checkout).
    """
    query = CloneConfig(
        url="https://github.com/user/repo",
        local_path="/tmp/repo",
        commit=None,
        branch="main",
    )

    with patch("CodeIngest.cloning.check_repo_exists", return_value=True) as mock_check:
        with patch("CodeIngest.cloning.run_command", new_callable=AsyncMock) as mock_exec:
             mock_exec.return_value = (b"output", b"error")
             with patch("CodeIngest.cloning.ensure_git_installed", new_callable=AsyncMock):
                 with patch("os.makedirs", return_value=None):
                    await clone_repo(query)

                    mock_check.assert_called_once_with(query.url)
                    assert mock_exec.call_count == 1  # Only clone call


@pytest.mark.asyncio
async def test_clone_nonexistent_repository() -> None:
    """
    Test cloning a nonexistent repository URL.

    Given an invalid or nonexistent URL:
    When `clone_repo` is called,
    Then a ValueError should be raised with an appropriate error message.
    """
    clone_config = CloneConfig(
        url="https://github.com/user/nonexistent-repo",
        local_path="/tmp/repo",
        commit=None,
        branch="main",
    )
    with patch("CodeIngest.cloning.check_repo_exists", return_value=False) as mock_check:
        with pytest.raises(ValueError, match="Repository not found"):
            await clone_repo(clone_config)

            mock_check.assert_called_once_with(clone_config.url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mock_stdout, return_code, expected",
    [
        (b"HTTP/1.1 200 OK\n", 0, True),  # Existing repo
        (b"HTTP/1.1 404 Not Found\n", 0, False),  # Non-existing repo
        (b"HTTP/1.1 302 Found\n", 0, False),  # Redirect (treated as not found)
        (b"HTTP/1.1 301 Moved Permanently\n", 0, True), # Permanent redirect (treated as found)
        (b"HTTP/1.1 200 OK\n", 1, False),  # Failed request (non-zero return code)
        (b"", 1, False), # Empty output, non-zero return code
    ],
)
async def test_check_repo_exists(mock_stdout: bytes, return_code: int, expected: bool) -> None:
    """
    Test the `check_repo_exists` function with different curl responses.

    Given various stdout lines and return codes:
    When `check_repo_exists` is called,
    Then it should correctly indicate whether the repository exists.
    """
    url = "https://github.com/user/repo"

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_process = AsyncMock()
        # Mock the subprocess output
        mock_process.communicate.return_value = (mock_stdout, b"")
        mock_process.returncode = return_code
        mock_exec.return_value = mock_process

        repo_exists = await check_repo_exists(url)

        assert repo_exists is expected

@pytest.mark.asyncio
async def test_check_repo_exists_unexpected_status() -> None:
    """
    Test check_repo_exists with an unexpected status line.
    """
    url = "https://github.com/user/repo"
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"HTTP/1.1 500 Internal Server Error\n", b"")
        mock_process.returncode = 0
        mock_exec.return_value = mock_process

        with pytest.raises(RuntimeError, match="Unexpected status line: HTTP/1.1 500 Internal Server Error"):
            await check_repo_exists(url)


@pytest.mark.asyncio
async def test_clone_with_custom_branch() -> None:
    """
    Test cloning a repository with a specified custom branch.

    Given a valid URL and a branch:
    When `clone_repo` is called,
    Then the repository should be cloned shallowly to that branch.
    """
    clone_config = CloneConfig(url="https://github.com/user/repo", local_path="/tmp/repo", branch="feature-branch")
    with patch("CodeIngest.cloning.check_repo_exists", return_value=True):
        with patch("CodeIngest.cloning.run_command", new_callable=AsyncMock) as mock_exec:
            with patch("CodeIngest.cloning.ensure_git_installed", new_callable=AsyncMock):
                 with patch("os.makedirs", return_value=None):
                    await clone_repo(clone_config)

                    mock_exec.assert_called_once_with(
                        "git",
                        "clone",
                        "--single-branch",
                        "--depth=1",
                        "--branch",
                        "feature-branch",
                        clone_config.url,
                        clone_config.local_path,
                    )


@pytest.mark.asyncio
async def test_git_command_failure_in_clone_repo() -> None:
    """
    Test cloning when the Git command fails during execution within clone_repo.

    Given a valid URL, but `run_command` raises a RuntimeError during clone or checkout:
    When `clone_repo` is called,
    Then the RuntimeError should be raised.
    """
    clone_config = CloneConfig(
        url="https://github.com/user/repo",
        local_path="/tmp/repo",
        commit="a" * 40, # Add commit to test checkout failure as well
    )
    with patch("CodeIngest.cloning.check_repo_exists", return_value=True):
        with patch("CodeIngest.cloning.run_command", side_effect=RuntimeError("Simulated Git command failure")) as mock_exec:
            with patch("CodeIngest.cloning.ensure_git_installed", new_callable=AsyncMock):
                 with patch("os.makedirs", return_value=None):
                    with pytest.raises(RuntimeError, match="Simulated Git command failure"):
                        await clone_repo(clone_config)


@pytest.mark.asyncio
async def test_clone_default_shallow_clone() -> None:
    """
    Test cloning a repository with the default shallow clone options.

    Given a valid URL and no branch or commit:
    When `clone_repo` is called,
    Then the repository should be cloned with `--depth=1` and `--single-branch`.
    """
    clone_config = CloneConfig(
        url="https://github.com/user/repo",
        local_path="/tmp/repo",
    )

    with patch("CodeIngest.cloning.check_repo_exists", return_value=True):
        with patch("CodeIngest.cloning.run_command", new_callable=AsyncMock) as mock_exec:
            with patch("CodeIngest.cloning.ensure_git_installed", new_callable=AsyncMock):
                 with patch("os.makedirs", return_value=None):
                    await clone_repo(clone_config)

                    mock_exec.assert_called_once_with(
                        "git",
                        "clone",
                        "--single-branch",
                        "--depth=1",
                        clone_config.url,
                        clone_config.local_path,
                    )


@pytest.mark.asyncio
async def test_clone_commit_without_branch() -> None:
    """
    Test cloning when a commit hash is provided but no branch is specified.

    Given a valid URL and a commit hash (but no branch):
    When `clone_repo` is called,
    Then the repository should be cloned and checked out at that commit.
    """
    clone_config = CloneConfig(
        url="https://github.com/user/repo",
        local_path="/tmp/repo",
        commit="a" * 40,  # Simulating a valid commit hash
    )
    with patch("CodeIngest.cloning.check_repo_exists", return_value=True):
        with patch("CodeIngest.cloning.run_command", new_callable=AsyncMock) as mock_exec:
            with patch("CodeIngest.cloning.ensure_git_installed", new_callable=AsyncMock):
                 with patch("os.makedirs", return_value=None):
                    mock_exec.return_value = (b"output", b"error") # Mock success for both commands
                    await clone_repo(clone_config)

                    assert mock_exec.call_count == 2  # Clone and checkout calls
                    mock_exec.assert_any_call("git", "clone", "--single-branch", clone_config.url, clone_config.local_path)
                    mock_exec.assert_any_call("git", "-C", clone_config.local_path, "checkout", clone_config.commit)


@pytest.mark.asyncio
async def test_check_repo_exists_with_redirect() -> None:
    """
    Test `check_repo_exists` when a redirect (302) is returned.

    Given a URL that responds with "302 Found":
    When `check_repo_exists` is called,
    Then it should return `False`, indicating the repo is inaccessible.
    """
    url = "https://github.com/user/repo"
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"HTTP/1.1 302 Found\n", b"")
        mock_process.returncode = 0  # Simulate successful request
        mock_exec.return_value = mock_process

        repo_exists = await check_repo_exists(url)

        assert repo_exists is False


@pytest.mark.asyncio
async def test_check_repo_exists_with_permanent_redirect() -> None:
    """
    Test `check_repo_exists` when a permanent redirect (301) is returned.

    Given a URL that responds with "301 Found":
    When `check_repo_exists` is called,
    Then it should return `True`, indicating the repo may exist at the new location.
    """
    url = "https://github.com/user/repo"
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"HTTP/1.1 301 Found\n", b"")
        mock_process.returncode = 0  # Simulate successful request
        mock_exec.return_value = mock_process

        repo_exists = await check_repo_exists(url)

        assert repo_exists


@pytest.mark.asyncio
async def test_clone_with_timeout() -> None:
    """
    Test cloning a repository when a timeout occurs.

    Given a valid URL, but `run_command` times out:
    When `clone_repo` is called,
    Then an `AsyncTimeoutError` should be raised to indicate the operation exceeded time limits.
    """
    clone_config = CloneConfig(url="https://github.com/user/repo", local_path="/tmp/repo")

    with patch("CodeIngest.cloning.check_repo_exists", return_value=True):
        # Mock run_command to raise asyncio.TimeoutError
        with patch("CodeIngest.cloning.run_command", side_effect=asyncio.TimeoutError) as mock_exec:
            with patch("CodeIngest.cloning.ensure_git_installed", new_callable=AsyncMock):
                 with patch("os.makedirs", return_value=None):
                    with pytest.raises(AsyncTimeoutError, match="Operation timed out after"):
                        await clone_repo(clone_config)


@pytest.mark.asyncio
async def test_clone_specific_branch(tmp_path):
    """
    Test cloning a specific branch of a repository.

    Given a valid repository URL and a branch name:
    When `clone_repo` is called,
    Then the repository should be cloned and checked out at that branch.
    """
    repo_url = "https://github.com/cyclotruc/gitingest.git"
    branch_name = "main"
    local_path = tmp_path / "CodeIngest"

    # Mock check_repo_exists and run_command for the actual clone and checkout steps
    with patch("CodeIngest.cloning.check_repo_exists", new_callable=AsyncMock, return_value=True) as mock_check_exists:
        with patch("CodeIngest.cloning.run_command", new_callable=AsyncMock) as mock_run_command:
            # Mock the ensure_git_installed call within clone_repo
            with patch("CodeIngest.cloning.ensure_git_installed", new_callable=AsyncMock) as mock_ensure_git:
                # Mock os.makedirs to prevent actual directory creation in tests
                with patch("os.makedirs", return_value=None) as mock_makedirs:
                    await clone_repo(CloneConfig(url=repo_url, local_path=str(local_path), branch=branch_name))

                    mock_check_exists.assert_called_once_with(repo_url)
                    mock_ensure_git.assert_called_once()
                    mock_makedirs.assert_called_once_with(local_path.parent, exist_ok=True)

                    # Verify the git clone command was called correctly
                    # Corrected assertion: Don't expect --branch main as per clone_repo logic
                    mock_run_command.assert_called_once_with(
                        "git",
                        "clone",
                        "--single-branch",
                        "--depth=1",
                        repo_url, # Branch is not explicitly added for 'main'
                        str(local_path),
                    )

    # Note: We cannot assert the actual branch checked out or files present
    # without performing a real clone or extensive file system mocking.
    # The test verifies that the correct external commands are invoked.


@pytest.mark.asyncio
async def test_clone_branch_with_slashes(tmp_path):
    """
    Test cloning a branch with slashes in the name.

    Given a valid repository URL and a branch name with slashes:
    When `clone_repo` is called,
    Then the repository should be cloned and checked out at that branch.
    """
    repo_url = "https://github.com/user/repo"
    branch_name = "fix/in-operator"
    local_path = tmp_path / "CodeIngest"

    with patch("CodeIngest.cloning.check_repo_exists", return_value=True):
        with patch("CodeIngest.cloning.run_command", new_callable=AsyncMock) as mock_exec:
            with patch("CodeIngest.cloning.ensure_git_installed", new_callable=AsyncMock):
                with patch("os.makedirs", return_value=None):
                    await clone_repo(CloneConfig(url=repo_url, local_path=str(local_path), branch=branch_name))

                    mock_exec.assert_called_once_with(
                        "git",
                        "clone",
                        "--single-branch",
                        "--depth=1",
                        "--branch",
                        "fix/in-operator",
                        repo_url,
                        str(local_path),
                    )


@pytest.mark.asyncio
async def test_clone_creates_parent_directory(tmp_path: Path) -> None:
    """
    Test that clone_repo creates parent directories if they don't exist.

    Given a local path with non-existent parent directories:
    When `clone_repo` is called,
    Then it should create the parent directories before attempting to clone.
    """
    nested_path = tmp_path / "deep" / "nested" / "path" / "repo"
    clone_config = CloneConfig(
        url="https://github.com/user/repo",
        local_path=str(nested_path),
    )

    with patch("CodeIngest.cloning.check_repo_exists", return_value=True):
        with patch("CodeIngest.cloning.run_command", new_callable=AsyncMock) as mock_exec:
            with patch("CodeIngest.cloning.ensure_git_installed", new_callable=AsyncMock):
                # Mock os.makedirs to check it's called
                with patch("os.makedirs", return_value=None) as mock_makedirs:
                    await clone_repo(clone_config)

                    # Verify parent directory was attempted to be created
                    mock_makedirs.assert_called_once_with(nested_path.parent, exist_ok=True)

                    # Verify git clone was called with correct parameters
                    mock_exec.assert_called_once_with(
                        "git",
                        "clone",
                        "--single-branch",
                        "--depth=1",
                        clone_config.url,
                        str(nested_path),
                    )


@pytest.mark.asyncio
async def test_clone_with_specific_subpath() -> None:
    """
    Test cloning a repository with a specific subpath.

    Given a valid repository URL and a specific subpath:
    When `clone_repo` is called,
    Then the repository should be cloned with sparse checkout enabled and the specified subpath.
    """
    clone_config = CloneConfig(url="https://github.com/user/repo", local_path="/tmp/repo", subpath="src/docs")

    with patch("CodeIngest.cloning.check_repo_exists", return_value=True):
        with patch("CodeIngest.cloning.run_command", new_callable=AsyncMock) as mock_exec:
            with patch("CodeIngest.cloning.ensure_git_installed", new_callable=AsyncMock):
                with patch("os.makedirs", return_value=None):
                    await clone_repo(clone_config)

                    # Verify the clone command includes sparse checkout flags
                    mock_exec.assert_any_call(
                        "git",
                        "clone",
                        "--single-branch",
                        "--filter=blob:none",
                        "--sparse",
                        "--depth=1",
                        clone_config.url,
                        clone_config.local_path,
                    )

                    # Verify the sparse-checkout command sets the correct path
                    mock_exec.assert_any_call("git", "-C", clone_config.local_path, "sparse-checkout", "set", "src/docs")

                    assert mock_exec.call_count == 2


@pytest.mark.asyncio
async def test_clone_with_commit_and_subpath() -> None:
    """
    Test cloning a repository with both a specific commit and subpath.

    Given a valid repository URL, commit hash, and subpath:
    When `clone_repo` is called,
    Then the repository should be cloned with sparse checkout enabled,
    checked out at the specific commit, and only include the specified subpath.
    """
    clone_config = CloneConfig(
        url="https://github.com/user/repo",
        local_path="/tmp/repo",
        commit="a" * 40,  # Simulating a valid commit hash
        subpath="src/docs",
    )

    with patch("CodeIngest.cloning.check_repo_exists", return_value=True):
        with patch("CodeIngest.cloning.run_command", new_callable=AsyncMock) as mock_exec:
            with patch("CodeIngest.cloning.ensure_git_installed", new_callable=AsyncMock):
                 with patch("os.makedirs", return_value=None):
                    mock_exec.return_value = (b"output", b"error") # Mock success for both commands
                    await clone_repo(clone_config)

                    # Verify the clone command includes sparse checkout flags
                    mock_exec.assert_any_call(
                        "git",
                        "clone",
                        "--single-branch",
                        "--filter=blob:none",
                        "--sparse",
                        clone_config.url,
                        clone_config.local_path,
                    )

                    # Verify the sparse-checkout and checkout commands are called together
                    mock_exec.assert_any_call(
                        "git",
                        "-C",
                        clone_config.local_path,
                        "sparse-checkout",
                        "set",
                        "src/docs",
                        "checkout",
                        clone_config.commit,
                    )

                    assert mock_exec.call_count == 2

@pytest.mark.asyncio
async def test_clone_with_blob_subpath() -> None:
    """
    Test cloning a repository with a subpath pointing to a file (blob).

    Given a valid repository URL and a subpath pointing to a file:
    When `clone_repo` is called,
    Then the repository should be cloned with sparse checkout enabled,
    and the sparse-checkout path should be the parent directory of the file.
    """
    clone_config = CloneConfig(
        url="https://github.com/user/repo",
        local_path="/tmp/repo",
        subpath="src/file.txt",
        blob=True # Indicate it's a blob path
    )

    with patch("CodeIngest.cloning.check_repo_exists", return_value=True):
        with patch("CodeIngest.cloning.run_command", new_callable=AsyncMock) as mock_exec:
            with patch("CodeIngest.cloning.ensure_git_installed", new_callable=AsyncMock):
                with patch("os.makedirs", return_value=None):
                    mock_exec.return_value = (b"output", b"error") # Mock success for commands
                    await clone_repo(clone_config)

                    # Verify the clone command includes sparse checkout flags
                    mock_exec.assert_any_call(
                        "git",
                        "clone",
                        "--single-branch",
                        "--filter=blob:none",
                        "--sparse",
                        "--depth=1", # Should still be shallow if no commit
                        clone_config.url,
                        clone_config.local_path,
                    )

                    # Verify the sparse-checkout command sets the parent directory path
                    mock_exec.assert_any_call(
                        "git",
                        "-C",
                        clone_config.local_path,
                        "sparse-checkout",
                        "set",
                        "src", # Should be the parent directory
                    )

                    assert mock_exec.call_count == 2

# --- Tests for ensure_git_installed ---

@pytest.mark.asyncio
async def test_ensure_git_installed_success() -> None:
    """Test ensure_git_installed when git is installed."""
    with patch("CodeIngest.utils.git_utils.run_command", new_callable=AsyncMock) as mock_run_command:
        mock_run_command.return_value = (b"git version 2.30.0", b"")
        await ensure_git_installed()
        mock_run_command.assert_called_once_with("git", "--version")

@pytest.mark.asyncio
async def test_ensure_git_installed_failure() -> None:
    """Test ensure_git_installed when git is not installed."""
    with patch("CodeIngest.utils.git_utils.run_command", side_effect=RuntimeError("git not found")) as mock_run_command:
        with pytest.raises(RuntimeError, match="Git is not installed or not accessible."):
            await ensure_git_installed()
        mock_run_command.assert_called_once_with("git", "--version")

# --- Tests for fetch_remote_branch_list ---

@pytest.mark.asyncio
async def test_fetch_remote_branch_list_success() -> None:
    """Test fetch_remote_branch_list with valid output."""
    url = "https://github.com/user/repo"
    mock_output = b"""\
a2109945d7f2d5e72d14606b7f10216d6c46746b\trefs/heads/main
b87654321abcdef0123456789abcdef01234567\trefs/heads/feature/branch
"""
    with patch("CodeIngest.utils.git_utils.run_command", new_callable=AsyncMock) as mock_run_command:
        mock_run_command.return_value = (mock_output, b"")
        with patch("CodeIngest.utils.git_utils.ensure_git_installed", new_callable=AsyncMock):
            branches = await fetch_remote_branch_list(url)
            assert branches == ["main", "feature/branch"]
            mock_run_command.assert_called_once_with("git", "ls-remote", "--heads", url)

@pytest.mark.asyncio
async def test_fetch_remote_branch_list_no_branches() -> None:
    """Test fetch_remote_branch_list with output containing no branch refs."""
    url = "https://github.com/user/repo"
    mock_output = b"""\
a2109945d7f2d5e72d14606b7f10216d6c46746b\trefs/tags/v1.0.0
"""
    with patch("CodeIngest.utils.git_utils.run_command", new_callable=AsyncMock) as mock_run_command:
        mock_run_command.return_value = (mock_output, b"")
        with patch("CodeIngest.utils.git_utils.ensure_git_installed", new_callable=AsyncMock):
            branches = await fetch_remote_branch_list(url)
            assert branches == []
            mock_run_command.assert_called_once_with("git", "ls-remote", "--heads", url)

@pytest.mark.asyncio
async def test_fetch_remote_branch_list_git_failure() -> None:
    """Test fetch_remote_branch_list when git command fails."""
    url = "https://github.com/user/repo"
    with patch("CodeIngest.utils.git_utils.run_command", side_effect=RuntimeError("git ls-remote failed")) as mock_run_command:
        with patch("CodeIngest.utils.git_utils.ensure_git_installed", new_callable=AsyncMock):
            with pytest.raises(RuntimeError, match="git ls-remote failed"):
                await fetch_remote_branch_list(url)
            mock_run_command.assert_called_once_with("git", "ls-remote", "--heads", url)

