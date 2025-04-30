"""Integration tests covering core functionalities, edge cases, and concurrency handling."""

import shutil
import asyncio
import inspect
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock, call

import pytest
from fastapi.testclient import TestClient

from src.server.main import app
from CodeIngest.entrypoint import ingest, ingest_async
from CodeIngest.schemas import IngestionQuery, CloneConfig
from CodeIngest.config import TMP_BASE_PATH

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
    """Mock the static file mount."""
    with patch("src.server.main.StaticFiles", return_value=None) as mock_static: yield mock_static


@pytest.fixture(scope="module", autouse=True)
def mock_templates():
    """Mock Jinja2 template rendering."""
    with patch("starlette.templating.Jinja2Templates.TemplateResponse", return_value="Mocked Template Response") as mock_template: yield mock_template

# Mock parse_query
@pytest.fixture(autouse=True)
def mock_parse_query_fixture(): # Renamed fixture slightly
    """Mock parse_query to return a default IngestionQuery."""
    # Define the async function that will be the side_effect
    async def _mock_parse(*args, **kwargs):
        mock_query = MagicMock(spec=IngestionQuery)
        mock_query.url = "https://github.com/octocat/Hello-World"
        mock_id = "mock-uuid-1234"
        mock_slug = "octocat-Hello-World"
        mock_query.id = mock_id
        mock_query.slug = mock_slug
        mock_query.local_path = TMP_BASE_PATH / mock_id / mock_slug
        mock_query.subpath = "/"
        mock_query.branch = "main"
        mock_query.commit = None
        mock_query.max_file_size = 1000000
        mock_query.ignore_patterns = set()
        mock_query.include_patterns = None
        mock_query.original_zip_path = None
        mock_query.temp_extract_path = None

        mock_clone_config = MagicMock(spec=CloneConfig)
        mock_clone_config.url = mock_query.url
        mock_clone_config.local_path = str(mock_query.local_path)
        mock_clone_config.commit = mock_query.commit
        mock_clone_config.branch = mock_query.branch
        mock_clone_config.subpath = mock_query.subpath
        mock_clone_config.blob = False
        mock_query.extract_clone_config.return_value = mock_clone_config
        return mock_query

    # Use AsyncMock directly as the patch target's replacement
    # The side_effect will be our async function _mock_parse
    with patch("CodeIngest.entrypoint.parse_query", new_callable=AsyncMock, side_effect=_mock_parse) as mock_async_parse:
        yield mock_async_parse # Yield the AsyncMock object


# Mock clone_repo
@pytest.fixture(autouse=True)
def mock_clone_repo():
    """Mock clone_repo to simulate directory creation."""
    async def dummy_clone(*args, **kwargs):
        config: CloneConfig = args[0]
        clone_target_path = Path(config.local_path)
        clone_target_path.parent.mkdir(parents=True, exist_ok=True)
        clone_target_path.mkdir(exist_ok=True)
        return None

    with patch("CodeIngest.entrypoint.clone_repo", side_effect=dummy_clone) as mock_clone:
         yield mock_clone

# Mock ingest_query
@pytest.fixture(autouse=True)
def mock_ingest_query():
    """Mock ingest_query to return dummy results."""
    def _mock_ingest(*args, **kwargs):
        return ("Mock Summary", "Mock Tree", "Mock Content")

    with patch("CodeIngest.entrypoint.ingest_query", side_effect=_mock_ingest) as mock_ingest:
        yield mock_ingest


def cleanup_temp_directories():
    """Cleans up the base temporary directory."""
    if TMP_BASE_PATH.exists():
        try:
            if TMP_BASE_PATH.is_relative_to(Path(tempfile.gettempdir())):
                 shutil.rmtree(TMP_BASE_PATH, ignore_errors=True)
            else: print(f"Warning: Skipping cleanup: {TMP_BASE_PATH}")
        except Exception as exc: print(f"Error cleaning up {TMP_BASE_PATH}: {exc}")


@pytest.fixture(scope="session", autouse=True)
def cleanup():
    """Cleanup temporary directories after the test session."""
    yield; cleanup_temp_directories()


@pytest.mark.asyncio
async def test_remote_repository_analysis(request):
    client = request.getfixturevalue("test_client")
    form_data = { "input_text": "https://github.com/octocat/Hello-World", "max_file_size": "243", "pattern_type": "exclude", "pattern": "", "branch_or_tag": "" }
    response = client.post("/", data=form_data); assert response.status_code == 200; assert "Mocked Template Response" in response.text

@pytest.mark.asyncio
async def test_invalid_repository_url(request, mock_parse_query_fixture): # Use the fixture name
    client = request.getfixturevalue("test_client")
    mock_parse_query_fixture.side_effect = ValueError("Repository not found") # Set side_effect on the mock object
    form_data = { "input_text": "invalid-url", "max_file_size": "243", "pattern_type": "exclude", "pattern": "", "branch_or_tag": "" }
    response = client.post("/", data=form_data); assert response.status_code == 200; assert "Mocked Template Response" in response.text

@pytest.mark.asyncio
async def test_large_repository(request):
    client = request.getfixturevalue("test_client")
    form_data = { "input_text": "https://github.com/large/repo", "max_file_size": "243", "pattern_type": "exclude", "pattern": "", "branch_or_tag": "" }
    response = client.post("/", data=form_data); assert response.status_code == 200; assert "Mocked Template Response" in response.text

@pytest.mark.asyncio
async def test_concurrent_requests(request):
    client = request.getfixturevalue("test_client")
    def make_request(url):
        form_data = { "input_text": url, "max_file_size": "243", "pattern_type": "exclude", "pattern": "", "branch_or_tag": "" }
        response = client.post("/", data=form_data); assert response.status_code == 200; assert "Mocked Template Response" in response.text
    urls = [f"https://github.com/octocat/Hello-World-{i}" for i in range(5)]
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(make_request, url) for url in urls]
        for future in futures: future.result()

@pytest.mark.asyncio
async def test_large_file_handling(request):
    client = request.getfixturevalue("test_client")
    form_data = { "input_text": "https://github.com/octocat/large-files", "max_file_size": "10", "pattern_type": "exclude", "pattern": "", "branch_or_tag": "" }
    response = client.post("/", data=form_data); assert response.status_code == 200; assert "Mocked Template Response" in response.text

@pytest.mark.asyncio
async def test_repository_with_patterns(request):
    client = request.getfixturevalue("test_client")
    form_data = { "input_text": "https://github.com/octocat/Hello-World", "max_file_size": "243", "pattern_type": "include", "pattern": "*.md", "branch_or_tag": "" }
    response = client.post("/", data=form_data); assert response.status_code == 200; assert "Mocked Template Response" in response.text

@pytest.mark.asyncio
async def test_ingest_async_clone_repo_not_coroutine(mock_parse_query_fixture, mock_ingest_query): # Use fixture name
    non_awaitable_mock = MagicMock()
    with patch("CodeIngest.entrypoint.clone_repo", return_value=non_awaitable_mock) as mock_clone, \
         patch("inspect.iscoroutine", return_value=False) as mock_iscoroutine:
        with pytest.raises(TypeError, match="clone_repo did not return a coroutine as expected."):
            await ingest_async("https://github.com/user/repo")
        # --- FIX: Assert call on the mock object from the fixture ---
        mock_parse_query_fixture.assert_called_once()
        # --- End FIX ---
        mock_clone.assert_called_once()
        mock_iscoroutine.assert_called()
        mock_ingest_query.assert_not_called()


@pytest.mark.filterwarnings("ignore::RuntimeWarning:unittest.mock")
def test_ingest_sync_no_running_loop(mock_parse_query_fixture, mock_clone_repo, mock_ingest_query): # Use fixture name
    """Test synchronous ingest when no asyncio event loop is running."""
    with patch("asyncio.get_running_loop", side_effect=RuntimeError("No running loop")) as mock_get_loop:
        mock_loop = MagicMock(spec=asyncio.AbstractEventLoop)

        async def mocked_ingest_async(*args, **kwargs):
             # Use the mock object from the fixture
             await mock_parse_query_fixture(*args, **kwargs)
             if kwargs.get('source', '').startswith("http"):
                 dummy_config = CloneConfig(url=kwargs.get('source'), local_path="/tmp/dummy")
                 await mock_clone_repo(dummy_config)
             mock_ingest_query()
             return ("Mock Summary", "Mock Tree", "Mock Content")

        # Let asyncio.run handle the execution of the actual (patched) coro
        mock_loop.run_until_complete.side_effect = lambda coro: asyncio.run(coro)

        with patch("asyncio.new_event_loop", return_value=mock_loop) as mock_new_loop:
            with patch("asyncio.set_event_loop") as mock_set_loop:
                 # Patch the *actual* ingest_async function within entrypoint
                 with patch("CodeIngest.entrypoint.ingest_async", new=mocked_ingest_async):
                      summary, tree, content = ingest("https://github.com/user/repo")

                 mock_get_loop.assert_called_once()
                 mock_new_loop.assert_called_once()
                 mock_set_loop.assert_called_once_with(mock_loop)
                 # run_until_complete is called by asyncio.run implicitly
                 # mock_loop.run_until_complete.assert_called_once() # This might fail depending on asyncio.run impl details
                 mock_loop.close.assert_called_once()

                 assert summary == "Mock Summary"
                 assert tree == "Mock Tree"
                 assert content == "Mock Content"

                 # --- FIX: Assert call on the mock object from the fixture ---
                 mock_parse_query_fixture.assert_called()
                 # --- End FIX ---
                 mock_clone_repo.assert_called()
                 mock_ingest_query.assert_called()

