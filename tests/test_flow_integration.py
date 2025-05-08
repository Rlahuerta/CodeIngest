# tests/test_flow_integration.py
"""Integration tests covering core functionalities, edge cases, and concurrency handling."""

import shutil
import asyncio
# import inspect # No longer used
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock  # Removed call as it's not used

import pytest
from fastapi.testclient import TestClient

# Need ingest_async for one test case
from CodeIngest.entrypoint import ingest, ingest_async  # Restore ingest_async import
from CodeIngest.schemas import IngestionQuery
from src.server.main import app  # Import app from src

BASE_DIR = Path(__file__).resolve().parent.parent
# Corrected TEMPLATE_DIR path
TEMPLATE_DIR = BASE_DIR / "src" / "server" / "templates"


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
def mock_parse_query_in_entrypoint():  # Renamed to be more specific
    """Mock parse_query used by CodeIngest.entrypoint to return a default IngestionQuery."""
    mock_query = MagicMock(spec=IngestionQuery)
    mock_query.url = "https://github.com/octocat/Hello-World"
    mock_query.local_path = Path("/tmp/CodeIngest/mock_id/mock_repo")  # Use a mock local path with ID
    mock_query.id = "mock_id"  # Ensure mock_query has an ID
    mock_query.slug = "octocat-Hello-World"  # Added slug for query_processor
    mock_query.repo_name = "Hello-World"  # Added repo_name for query_processor
    mock_query.subpath = "/"
    mock_query.branch = "main"
    mock_query.commit = None
    mock_query.max_file_size = 1000000
    mock_query.ignore_patterns = set()
    mock_query.include_patterns = set()
    mock_query.extract_clone_config.return_value = MagicMock()

    # This mock targets parse_query as used by the entrypoint module (ingest_async)
    with patch("CodeIngest.entrypoint.parse_query", new_callable=AsyncMock) as mock_parse:
        mock_parse.return_value = mock_query
        yield mock_parse


@pytest.fixture(autouse=True)
def mock_parse_query_in_processor():  # New fixture for processor
    """Mock parse_query used by server.query_processor (if it were still used directly)."""
    # This fixture might not be strictly necessary if query_processor no longer calls parse_query
    # but kept for safety or if other tests indirectly cause its invocation.
    mock_query_proc = MagicMock(spec=IngestionQuery)
    mock_query_proc.url = "https://github.com/octocat/Hello-World"
    mock_query_proc.local_path = Path("/tmp/CodeIngest/mock_id_proc/mock_repo_proc")
    mock_query_proc.id = "mock_id_proc"
    mock_query_proc.slug = "octocat-Hello-World-proc"  # Added
    mock_query_proc.repo_name = "Hello-World-proc"  # Added
    mock_query_proc.subpath = "/"
    # ... other fields as necessary ...
    with patch("server.query_processor.parse_query", new_callable=AsyncMock, create=True) as mock_parse_proc:
        # Create=True allows mocking a non-existent attribute if it was removed
        mock_parse_proc.return_value = mock_query_proc
        yield mock_parse_proc


# Mock clone_repo for tests that don't specifically target cloning
@pytest.fixture(autouse=True)
def mock_clone_repo():
    """Mock clone_repo to do nothing."""
    with patch("CodeIngest.entrypoint.clone_repo", new_callable=AsyncMock) as mock_clone:
        mock_clone.return_value = AsyncMock()
        yield mock_clone


# Mock ingest_query for tests that don't specifically target ingestion logic
@pytest.fixture(autouse=True)
def mock_ingest_query_in_entrypoint():  # Renamed for specificity
    """Mock ingest_query used by CodeIngest.entrypoint to return dummy results."""
    # This mock targets ingest_query as used by the entrypoint module (ingest_async)
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
        "branch_or_tag": "main",
        "source_type": "url_path",  # Corrected: Use url_path to match endpoint logic
    }

    response = client.post("/", data=form_data)
    assert response.status_code == 200, f"Form submission failed: {response.text}"
    assert "Mocked Template Response" in response.text


@pytest.mark.asyncio
async def test_invalid_repository_url(request, mock_parse_query_in_entrypoint,
                                      mock_ingest_query_in_entrypoint):  # Pass specific mocks
    """Test handling of an invalid repository URL."""
    client = request.getfixturevalue("test_client")

    # Configure mock_ingest_async to raise ValueError for this test case
    # We mock ingest_async now because process_query calls it directly
    with patch("server.query_processor.ingest_async",
               side_effect=ValueError("Repository not found")) as mock_ingest_async_call:
        form_data = {
            "input_text": "https://github.com/nonexistent/repo",
            "max_file_size": "243",
            "pattern_type": "exclude",
            "pattern": "",
            "branch_or_tag": "",
            "source_type": "url_path",  # Corrected: Use url_path
        }

        response = client.post("/", data=form_data)
        assert response.status_code == 200, f"Request failed: {response.text}"
        assert "Mocked Template Response" in response.text
        mock_ingest_async_call.assert_called_once()  # Verify ingest_async was called
        # Check if the arguments passed to ingest_async match what the form data implies
        call_args, call_kwargs = mock_ingest_async_call.call_args
        assert call_kwargs.get('source') == "https://github.com/nonexistent/repo"


@pytest.mark.asyncio
async def test_large_repository(request):
    """Simulate analysis of a large repository with nested folders."""
    client = request.getfixturevalue("test_client")
    form_data = {
        "input_text": "https://github.com/large/repo-with-many-files",
        "max_file_size": "243",
        "pattern_type": "exclude",
        "pattern": "",
        "branch_or_tag": "",
        "source_type": "url_path",  # Corrected: Use url_path
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
            "branch_or_tag": "main",
            "source_type": "url_path",  # Corrected: Use url_path
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
        "input_text": "https://github.com/octocat/Hello-World",  # This is a small repo
        "max_file_size": "10",  # Simulate a very small max_file_size via slider
        "pattern_type": "exclude",
        "pattern": "",
        "branch_or_tag": "main",
        "source_type": "url_path",  # Corrected: Use url_path
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
        "branch_or_tag": "main",
        "source_type": "url_path",  # Corrected: Use url_path
    }

    response = client.post("/", data=form_data)
    assert response.status_code == 200, f"Request failed: {response.text}"
    assert "Mocked Template Response" in response.text


@pytest.mark.asyncio
async def test_ingest_async_clone_repo_not_coroutine(mock_parse_query_in_entrypoint, mock_ingest_query_in_entrypoint):
    """
    Test ingest_async when clone_repo does not return a coroutine.
    Covers src/CodeIngest/entrypoint.py lines 90-92.
    """
    with patch("CodeIngest.entrypoint.clone_repo", return_value=MagicMock()) as mock_clone, \
            patch("inspect.iscoroutine", return_value=False) as mock_iscoroutine:

        with pytest.raises(TypeError, match="clone_repo did not return a coroutine as expected."):
            # Ensure the event loop is running for create_task
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            await loop.create_task(ingest_async("https://github.com/user/repo"))  # Use create_task within loop

        mock_clone.assert_called_once()
        mock_iscoroutine.assert_called()
        mock_ingest_query_in_entrypoint.assert_not_called()


@pytest.mark.filterwarnings("ignore::RuntimeWarning:unittest.mock")
def test_ingest_sync_no_running_loop(mock_parse_query_in_entrypoint, mock_clone_repo, mock_ingest_query_in_entrypoint):
    """
    Test synchronous ingest when no asyncio event loop is running.
    Covers src/CodeIngest/entrypoint.py lines 162-182.
    """
    # Expected return from ingest_async includes the query object
    expected_ingest_async_return = ("Mock Summary", "Mock Tree", "Mock Content",
                                    mock_parse_query_in_entrypoint.return_value)

    with patch("asyncio.get_running_loop", side_effect=RuntimeError("No running loop")) as mock_get_loop:
        mock_loop = MagicMock(spec=asyncio.AbstractEventLoop)
        # Adjust mock to simulate the new return tuple of ingest_async
        mock_loop.run_until_complete.return_value = expected_ingest_async_return

        with patch("asyncio.new_event_loop", return_value=mock_loop) as mock_new_loop:
            with patch("asyncio.set_event_loop") as mock_set_loop:
                summary, tree, content, query_obj = ingest("https://github.com/user/repo")  # Unpack 4 values

                mock_get_loop.assert_called_once()
                mock_new_loop.assert_called_once()
                mock_set_loop.assert_called_once_with(mock_loop)
                mock_loop.run_until_complete.assert_called_once()

                assert summary == "Mock Summary"
                assert tree == "Mock Tree"
                assert content == "Mock Content"
                assert query_obj == mock_parse_query_in_entrypoint.return_value  # Check query object too