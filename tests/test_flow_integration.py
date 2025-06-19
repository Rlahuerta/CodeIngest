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
from fastapi import FastAPI # Added for test-specific app
from slowapi import Limiter # Added for test-specific limiter
from slowapi.util import get_remote_address # Added for test-specific limiter

# Need ingest_async for one test case
from CodeIngest.entrypoint import ingest, ingest_async  # Restore ingest_async import
from CodeIngest.schemas import IngestionQuery
from src.server.main import app as main_app  # Import app from src, renamed to avoid conflict
from src.server.routers.index import router as index_router # Import for test-specific app

BASE_DIR = Path(__file__).resolve().parent.parent
# Corrected TEMPLATE_DIR path
TEMPLATE_DIR = BASE_DIR / "src" / "server" / "templates"


@pytest.fixture(scope="module")
def test_client():
    """Create a test client fixture using the main app."""
    with TestClient(main_app) as client_instance:
        client_instance.headers.update({"Host": "localhost"})
        yield client_instance

# Fixture for a TestClient with a high rate limit for specific tests
@pytest.fixture(scope="function")
def high_rate_limit_client():
    """Provides a TestClient with a high rate limit for the index router."""
    test_specific_app = FastAPI()

    # Create a new limiter with high limits for this app instance
    # Patching 'src.server.routers.index.limiter' to affect only the router instance used by test_specific_app
    # This is tricky because the limiter is imported at the module level in routers.index
    # A more robust way would be dependency injection for the limiter in the router.
    # For now, we'll patch it where it's used if direct patching of the imported name works.
    # This test demonstrates a common challenge. A direct patch might not work if the module
    # has already imported 'limiter'.

    # The router itself needs to be configured with this new limiter.
    # One way: create a new router instance and apply a new limiter to it.
    # This doesn't work because the decorator @limiter.limit uses the imported limiter.

    # Alternative: Temporarily patch the global limiter from server_utils for these tests
    # This is simpler if we accept modifying shared state for specific tests.
    # For true isolation, the app/router would need to allow injecting the limiter.

    # Let's try patching the limiter instance that index_router will use.
    # This requires index_router to be re-imported or the patch to be applied before it's imported by the test_app.
    # This is complex. For now, let's assume we create a new app and apply a NEW router instance
    # if we could configure that router instance with a new limiter.
    # Since we can't easily reconfigure the imported router's limiter, we'll create a new app
    # and rely on the fact that the test client might create a new context for the limiter.
    # This might not actually bypass the original limiter effectively without more invasive patching.

    # Given the constraints, the most straightforward (though not perfectly isolated) way
    # is to patch the limiter that the 'index_router' (imported from src.server.routers.index) uses.

    # This will use the main_app's limiter settings unless we get more sophisticated with patching
    # or app creation. For now, we'll just use a new TestClient instance.
    # The rate limit failures indicate that the state of the global limiter is the issue.

    # The simplest solution for testing is often to disable the limiter for those tests.
    # We can do this by patching the limiter's 'enabled' attribute or its check method.

    # Let's create a new app and include the index_router, then create a TestClient for it.
    # This won't solve the shared limiter state problem directly without patching.
    # The issue is that the limiter is global to the app.

    # The tests are failing because the global limiter instance used by the main 'app'
    # (and thus by the 'test_client' fixture) is accumulating hits across tests.
    # The 'function' scope for test_client doesn't reset the limiter's internal state because
    # the limiter itself is tied to the app, which is module-scoped effectively.

    # The best way here is to patch the limiter's 'enabled' state for the duration of these tests.
    # This requires the 'test_client' fixture to be function-scoped for this to be effective per-test.
    # The provided 'test_client' fixture is module-scoped. Let's make it function-scoped for these tests.

    # Re-defining a client specifically for these tests with a fresh app instance
    # that gets a fresh limiter state (if limiter is re-initialized or reset effectively).
    # The global limiter is in server.server_utils. We'll patch its 'enabled' state.

    with patch("server.server_utils.limiter.enabled", False): # Disable limiter for these tests
        with TestClient(main_app) as client:
            client.headers.update({"Host": "localhost"})
            yield client


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
async def test_concurrent_requests(high_rate_limit_client: TestClient): # Use the new client
    """Test handling of multiple concurrent requests."""
    client = high_rate_limit_client # Use the client with limiter disabled

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
async def test_large_file_handling(high_rate_limit_client: TestClient): # Use the new client
    """Test handling of repositories with large files."""
    client = high_rate_limit_client # Use the client with limiter disabled
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
async def test_repository_with_patterns(high_rate_limit_client: TestClient): # Use the new client
    """Test repository analysis with include/exclude patterns."""
    client = high_rate_limit_client # Use the client with limiter disabled
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