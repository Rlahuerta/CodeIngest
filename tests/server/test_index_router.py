import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi import UploadFile as FastAPIUploadFile, FastAPI
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.responses import HTMLResponse
from pathlib import Path
import io

from src.CodeIngest.schemas import IngestionQuery # Not used in this file directly, but good for context
from src.server.routers.index import router as index_router
from src.server.query_processor import RAW_UPLOADS_PATH

# Create an isolated app for these tests
test_app = FastAPI()
test_app.include_router(index_router)

client = TestClient(test_app) # Use the isolated app

def test_get_home():
    response = client.get("/")
    assert response.status_code == 200
    assert b"Select Source Type:" in response.content
    assert b"Max File Size:" in response.content

async def test_post_index_zip_file_missing_no_mock():
    form_data = {
        "source_type": "zip_file",
        "max_file_size": "243",
        "pattern_type": "exclude",
        "pattern": "",
        "branch_or_tag": ""
    }
    response = client.post("/", data=form_data, files={})
    assert response.status_code == 400
    assert b"Uploaded ZIP file path is missing or invalid." in response.content

@patch("src.server.routers.index.process_query")
async def test_post_index_url_path_missing_input(mock_process_query):
    mock_process_query.return_value = HTMLResponse(content="<html><body>mocked</body></html>", status_code=200)
    form_data = {
        "source_type": "url_path",
        "input_text": "",
        "max_file_size": "243",
        "pattern_type": "exclude",
        "pattern": "",
        "branch_or_tag": ""
    }
    response = client.post("/", data=form_data)
    mock_process_query.assert_called_once()
    args, kwargs = mock_process_query.call_args
    assert kwargs.get("source_type") == "url_path"
    assert kwargs.get("input_text") is None
    assert response.status_code == 200

@patch("src.server.routers.index.process_query")
async def test_post_index_invalid_source_type(mock_process_query):
    mock_process_query.return_value = HTMLResponse(content="<html><body>mocked</body></html>", status_code=200)
    form_data = {
        "source_type": "invalid_source",
        "input_text": "some_input",
        "max_file_size": "243",
        "pattern_type": "exclude",
        "pattern": "",
        "branch_or_tag": ""
    }
    response = client.post("/", data=form_data)
    mock_process_query.assert_called_once()
    args, kwargs = mock_process_query.call_args
    assert kwargs.get("source_type") == "invalid_source"
    assert kwargs.get("input_text") == "Invalid source type"
    assert response.status_code == 200

@patch("src.server.routers.index.process_query", new_callable=AsyncMock)
async def test_post_index_url_path_success(mock_process_query_instance):
    success_content = "<html><body><h1>Success</h1><p>Summary: Test success.</p><p>file.py</p><p>File content here.</p></body></html>"
    mock_process_query_instance.return_value = HTMLResponse(content=success_content, status_code=200)
    form_data = {
        "source_type": "url_path",
        "input_text": "https://github.com/testuser/test-repo",
        "max_file_size": "243",
        "pattern_type": "exclude",
        "pattern": "",
        "branch_or_tag": "main"
    }
    response = client.post("/", data=form_data)
    assert response.status_code == 200
    assert b"Summary: Test success." in response.content
    assert b"file.py" in response.content
    assert b"File content here." in response.content
    mock_process_query_instance.assert_called_once()
    args, kwargs = mock_process_query_instance.call_args
    assert kwargs.get("source_type") == "url_path"
    assert kwargs.get("input_text") == "https://github.com/testuser/test-repo"
    assert kwargs.get("branch_or_tag") == "main"

@patch("src.server.routers.index.shutil.copyfileobj")
@patch("src.server.routers.index.uuid.uuid4", return_value="test-uuid")
@patch("src.server.routers.index.process_query", new_callable=AsyncMock)
async def test_post_index_zip_file_success(mock_process_query, mock_uuid_func, mock_copyfileobj):
    mock_process_query.return_value = HTMLResponse(content="Success from ZIP", status_code=200)
    zip_content = b"dummy zip content"
    dummy_file = io.BytesIO(zip_content)
    form_data = {
        "source_type": "zip_file",
        "max_file_size": "243",
        "pattern_type": "exclude",
        "pattern": "",
        "branch_or_tag": ""
    }
    files_data = {
        "zip_file": ("test.zip", dummy_file, "application/zip")
    }
    response = client.post("/", data=form_data, files=files_data)
    assert response.status_code == 200
    assert b"Success from ZIP" in response.content
    mock_copyfileobj.assert_called_once()
    mock_process_query.assert_called_once()
    args, kwargs = mock_process_query.call_args
    assert kwargs.get("source_type") == "zip_file"
    expected_save_path = RAW_UPLOADS_PATH / "test-uuid_test.zip"
    assert kwargs.get("input_text") == str(expected_save_path)
    assert isinstance(kwargs.get("zip_file"), StarletteUploadFile)
    assert kwargs.get("zip_file").filename == "test.zip"

@patch("src.server.routers.index.shutil.copyfileobj") # Mock to raise error
@patch("src.server.routers.index.uuid.uuid4", return_value="test-uuid-error") # Mock uuid
@patch("src.server.routers.index.process_query", new_callable=AsyncMock) # Mock process_query
async def test_post_index_zip_file_save_error(mock_process_query, mock_uuid_func, mock_copyfileobj):
    mock_copyfileobj.side_effect = IOError("Disk full")
    mock_process_query.return_value = HTMLResponse(content="Error handled", status_code=200)

    zip_content = b"dummy zip content for error test"
    dummy_file = io.BytesIO(zip_content)
    form_data = {
        "source_type": "zip_file",
        "max_file_size": "243",
        "pattern_type": "exclude",
        "pattern": "",
        "branch_or_tag": ""
    }
    files_data = {
        "zip_file": ("error_test.zip", dummy_file, "application/zip")
    }
    response = client.post("/", data=form_data, files=files_data)
    assert response.status_code == 200
    mock_copyfileobj.assert_called_once()
    mock_process_query.assert_called_once()
    args, kwargs = mock_process_query.call_args
    assert kwargs.get("source_type") == "zip_file"
    assert "Error saving uploaded ZIP: Disk full" in kwargs.get("input_text")
    assert isinstance(kwargs.get("zip_file"), StarletteUploadFile)
    assert kwargs.get("zip_file").filename == "error_test.zip"
    assert kwargs.get("slider_position") == 243

# TODO:
# - Test successful url/path submission (covered by test_post_index_url_path_success)
# - Test error during ZIP save (covered by test_post_index_zip_file_save_error)
