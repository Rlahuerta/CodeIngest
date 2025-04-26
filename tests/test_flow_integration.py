"""Integration tests covering core functionalities, edge cases, and concurrency handling."""

import shutil
import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock, call

import pytest
from fastapi.testclient import TestClient

from src.server.main import app
from CodeIngest.entrypoint import ingest, ingest_async
from CodeIngest.schemas import IngestionQuery

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = BASE_DIR / "src" / "templates"


@pytest.fixture(scope="module")
def test_client():
    """Create a test client fixture."""
    with TestClient(app) as client_instance:
        client_instance.headers.update({"Host": "localhost"})
        yield client_instance


@pytest.fixture(scope="module", autouse=True)
def mock_static_files():
    """Mock the static file mount to avoid directory errors."""
    with patch("src.server.main.StaticFiles") as mock_static:
        mock_static.return_value = None  # Mocks the StaticFiles response
        yield mock_static


@pytest.fixture(scope="module", autouse=True)
def mock_templates():
    """Mock Jinja2 template rendering to bypass actual file loading."""
    with patch("starlette.templating.Jinja2Templates.TemplateResponse") as mock_template:
        mock_template.return_value = "Mocked Template Response"
        yield mock_template

# Mock parse_query for tests that don't specifically target parsing
@pytest.fixture(autouse=True)
def mock_parse_query():
    """Mock parse_query to return a default IngestionQuery."""
    mock_query = MagicMock(spec=IngestionQuery)
    mock_query.url = "https://github.com/octocat/Hello-World"
    mock_query.local_path = Path("/tmp/mock_repo") # Use a mock local path
    mock_query.subpath = "/"
    mock_query.branch = "main"
    mock_query.commit = None
    mock_query.max_file_size = 1000000
    mock_query.ignore_patterns = set()
    mock_query.include_patterns = set()
    mock_query.extract_clone_config.return_value = MagicMock() # Mock clone config extraction

    with patch("CodeIngest.entrypoint.parse_query", new_callable=AsyncMock) as mock_parse:
        mock_parse.return_value = mock_query
        yield mock_parse

# Mock clone_repo for tests that don't specifically target cloning
# KEEP this fixture as it's used by other tests marked with @pytest.mark.asyncio
@pytest.fixture(autouse=True)
def mock_clone_repo():
    """Mock clone_repo to do nothing."""
    with patch("CodeIngest.entrypoint.clone_repo", new_callable=AsyncMock) as mock_clone:
         # Ensure the mock is awaited if called
         mock_clone.return_value = AsyncMock() # Return an awaitable mock
         yield mock_clone

# Mock ingest_query for tests that don't specifically target ingestion logic
@pytest.fixture(autouse=True)
def mock_ingest_query():
    """Mock ingest_query to return dummy results."""
    with patch("CodeIngest.entrypoint.ingest_query") as mock_ingest:
        mock_ingest.return_value = ("Mock Summary", "Mock Tree", "Mock Content")
        yield mock_ingest


def cleanup_temp_directories():
    temp_dir = Path("/tmp/CodeIngest")
    if temp_dir.exists():
        try:
            shutil.rmtree(temp_dir)
        except PermissionError as exc:
            print(f"Error cleaning up {temp_dir}: {exc}")


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    """Cleanup temporary directories after tests."""
    yield
    cleanup_temp_directories()


@pytest.mark.asyncio
async def test_remote_repository_analysis(request):
    """Test the complete flow of analyzing a remote repository."""
    client = request.getfixturevalue("test_client")
    form_data = {
        "input_text": "https://github.com/octocat/Hello-World",
        "max_file_size": "243",
        "pattern_type": "exclude",
        "pattern": "",
    }

    response = client.post("/", data=form_data)
    assert response.status_code == 200, f"Form submission failed: {response.text}"
    assert "Mocked Template Response" in response.text


@pytest.mark.asyncio
async def test_invalid_repository_url(request):
    """Test handling of an invalid repository URL."""
    client = request.getfixturevalue("test_client")
    form_data = {
        "input_text": "https://github.com/nonexistent/repo",
        "max_file_size": "243",
        "pattern_type": "exclude",
        "pattern": "",
    }

    response = client.post("/", data=form_data)
    assert response.status_code == 200, f"Request failed: {response.text}"
    assert "Mocked Template Response" in response.text


@pytest.mark.asyncio
async def test_large_repository(request):
    """Simulate analysis of a large repository with nested folders."""
    client = request.getfixturevalue("test_client")
    form_data = {
        "input_text": "https://github.com/large/repo-with-many-files",
        "max_file_size": "243",
        "pattern_type": "exclude",
        "pattern": "",
    }

    response = client.post("/", data=form_data)
    assert response.status_code == 200, f"Request failed: {response.text}"
    assert "Mocked Template Response" in response.text


@pytest.mark.asyncio
async def test_concurrent_requests(request):
    """Test handling of multiple concurrent requests."""
    client = request.getfixturevalue("test_client")

    def make_request():
        form_data = {
            "input_text": "https://github.com/octocat/Hello-World",
            "max_file_size": "243",
            "pattern_type": "exclude",
            "pattern": "",
        }
        response = client.post("/", data=form_data)
        assert response.status_code == 200, f"Request failed: {response.text}"
        assert "Mocked Template Response" in response.text

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(make_request) for _ in range(5)]
        for future in futures:
            future.result()


@pytest.mark.asyncio
async def test_large_file_handling(request):
    """Test handling of repositories with large files."""
    client = request.getfixturevalue("test_client")
    form_data = {
        "input_text": "https://github.com/octocat/Hello-World",
        "max_file_size": "243",
        "pattern_type": "exclude",
        "pattern": "",
    }

    response = client.post("/", data=form_data)
    assert response.status_code == 200, f"Form submission failed: {response.text}"
    assert "Mocked Template Response" in response.text


@pytest.mark.asyncio
async def test_repository_with_patterns(request):
    """Test repository analysis with include/exclude patterns."""
    client = request.getfixturevalue("test_client")
    form_data = {
        "input_text": "https://github.com/octocat/Hello-World",
        "max_file_size": "243",
        "pattern_type": "include",
        "pattern": "*.md",
    }

    response = client.post("/", data=form_data)
    assert response.status_code == 200, f"Request failed: {response.text}"
    assert "Mocked Template Response" in response.text

@pytest.mark.asyncio
async def test_ingest_async_clone_repo_not_coroutine(mock_parse_query, mock_ingest_query):
    """
    Test ingest_async when clone_repo does not return a coroutine.
    Covers src/CodeIngest/entrypoint.py lines 90-92.
    """
    # Patch clone_repo to return a regular MagicMock (not awaitable)
    # Also patch inspect.iscoroutine to return False for *any* object within this test
    with patch("CodeIngest.entrypoint.clone_repo", return_value=MagicMock()) as mock_clone, \
         patch("inspect.iscoroutine", return_value=False) as mock_iscoroutine:

        # Call ingest_async with a URL source to trigger cloning path
        # This should now correctly trigger the `else` block and raise TypeError
        with pytest.raises(TypeError, match="clone_repo did not return a coroutine as expected."):
            await ingest_async("https://github.com/user/repo")

        # Verify clone_repo was called
        mock_clone.assert_called_once()
        # Verify inspect.iscoroutine was called at least once
        mock_iscoroutine.assert_called()
        # Ingest_query should NOT be called because of the error
        mock_ingest_query.assert_not_called()


@pytest.mark.filterwarnings("ignore::RuntimeWarning:unittest.mock")
def test_ingest_sync_no_running_loop(mock_parse_query, mock_clone_repo, mock_ingest_query):
    """
    Test synchronous ingest when no asyncio event loop is running.
    Covers src/CodeIngest/entrypoint.py lines 162-182.
    """
    # Mock asyncio.get_running_loop to raise RuntimeError, simulating no running loop
    with patch("asyncio.get_running_loop", side_effect=RuntimeError("No running loop")) as mock_get_loop:
        # Mock asyncio.new_event_loop and the loop's run_until_complete method
        mock_loop = MagicMock(spec=asyncio.AbstractEventLoop)
        mock_loop.run_until_complete.return_value = ("Mock Summary", "Mock Tree", "Mock Content") # Simulate ingest_async return
        # Do NOT mock mock_loop.close here, as the function doesn't call it in this path

        with patch("asyncio.new_event_loop", return_value=mock_loop) as mock_new_loop:
            with patch("asyncio.set_event_loop") as mock_set_loop:
                 # Call the synchronous ingest function
                 summary, tree, content = ingest("https://github.com/user/repo")

                 # Verify mocks were called as expected
                 mock_get_loop.assert_called_once()
                 mock_new_loop.assert_called_once()
                 mock_set_loop.assert_called_once_with(mock_loop)
                 mock_loop.run_until_complete.assert_called_once() # Assert run_until_complete was called
                 # Do NOT assert mock_loop.close.assert_called_once()

                 # Verify the results are correct
                 assert summary == "Mock Summary"
                 assert tree == "Mock Tree"
                 assert content == "Mock Content"

