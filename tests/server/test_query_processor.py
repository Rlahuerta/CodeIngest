import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path
import zipfile # For BadZipFile
import io # For BytesIO in upcoming tests if needed
import json # Added import

from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse
from starlette.templating import _TemplateResponse as TemplateResponse
from starlette.datastructures import FormData

from src.server.query_processor import process_query, sanitize_filename_part
from CodeIngest.schemas import IngestionQuery
from CodeIngest.utils.exceptions import GitError, InvalidPatternError
from CodeIngest.config import TMP_BASE_PATH
from src.server.server_utils import log_slider_to_size
# from src.CodeIngest.output_formatters import TreeDataItem

# Minimal mock for Request object
def mock_request(scope_type="http"):
    mock_req = MagicMock(spec=Request)
    mock_req.scope = {"type": scope_type}
    mock_req.url = MagicMock()
    mock_req.url.path = "/"
    return mock_req

def test_sanitize_filename_part_empty():
    assert sanitize_filename_part("") == ""

@pytest.mark.asyncio
async def test_process_query_url_path_missing_input_text():
    req = mock_request()
    response = await process_query(
        request=req,
        source_type="url_path",
        input_text=None,
        zip_file=None,
        slider_position=243,
        is_index=True
    )
    assert response.status_code == 400
    assert isinstance(response, TemplateResponse)
    assert "Please provide a URL or local path." in response.body.decode()

@pytest.mark.asyncio
async def test_process_query_zip_file_missing_path():
    req = mock_request()
    response = await process_query(
        request=req,
        source_type="zip_file",
        input_text=None,
        zip_file=None,
        slider_position=243,
        is_index=True
    )
    assert response.status_code == 400
    assert isinstance(response, TemplateResponse)
    assert "Uploaded ZIP file path is missing or invalid." in response.body.decode()

@pytest.mark.asyncio
async def test_process_query_zip_file_path_not_a_file():
    req = mock_request()
    with patch("src.server.query_processor.Path") as mock_path_constructor:
        mock_path_instance = MagicMock()
        mock_path_instance.is_file.return_value = False
        mock_path_constructor.return_value = mock_path_instance

        response = await process_query(
            request=req,
            source_type="zip_file",
            input_text="/path/to/a_directory",
            zip_file=None,
            slider_position=243,
            is_index=True
        )
    assert response.status_code == 400
    assert isinstance(response, TemplateResponse)
    assert "Uploaded ZIP file path is missing or invalid." in response.body.decode()
    mock_path_constructor.assert_called_with("/path/to/a_directory")
    mock_path_instance.is_file.assert_called_once()

@pytest.mark.asyncio
async def test_process_query_invalid_source_type():
    req = mock_request()
    response = await process_query(
        request=req,
        source_type="weird_type",
        input_text="anything",
        zip_file=None,
        slider_position=243,
        is_index=True
    )
    assert response.status_code == 400
    assert isinstance(response, TemplateResponse)
    assert "Invalid source type specified." in response.body.decode()

@pytest.mark.asyncio
@patch("src.server.query_processor.os.makedirs")
@patch("src.server.query_processor.open", new_callable=MagicMock)
@patch("src.server.query_processor.ingest_async", new_callable=AsyncMock)
async def test_process_query_success_url_path(mock_ingest_async, mock_open, mock_makedirs):
    req = mock_request()
    mock_query_id = "test-ingest-id"
    mock_repo_slug = "successful-repo"
    mock_repo_url = "https://github.com/testowner/successful-repo"
    mock_query_obj = MagicMock(spec=IngestionQuery)
    mock_query_obj.id = mock_query_id
    mock_query_obj.slug = mock_repo_slug
    mock_query_obj.url = mock_repo_url
    mock_query_obj.branch = "main"
    mock_query_obj.commit = None
    mock_summary = "Summary: URL Ingestion successful."
    # mock_tree_data is used for the old assertion, will be replaced by mock_tree_data_with_content
    mock_content_str = "File content for success." # This is concatenated_content
    mock_file_content_for_txt_test = "File content for success."

    mock_nested_tree_root_for_txt_test = {
        "name": mock_repo_slug + "/",
        "path": ".",
        "type": "DIRECTORY",
        "children": [
            {
                "name": "file.py",
                "path": "file.py",
                "type": "FILE",
                "file_content": mock_file_content_for_txt_test
            }
        ]
    }
    mock_dir_structure_text = "file.py" # This is based on the single file in the mock tree
    mock_num_tokens = 5
    mock_num_files = 1  # From mock_nested_tree_root_for_txt_test

    mock_ingestion_result = {
        "summary_str": mock_summary,
        "tree_data": mock_nested_tree_root_for_txt_test,
        "directory_structure_text": mock_dir_structure_text,
        "num_tokens": mock_num_tokens,
        "num_files": mock_num_files,
        "concatenated_content": mock_content_str,
        "query_obj": mock_query_obj
    }
    mock_ingest_async.return_value = mock_ingestion_result

    mock_file_handle = MagicMock()
    mock_open.return_value.__enter__.return_value = mock_file_handle

    response = await process_query(
        request=req,
        source_type="url_path",
        input_text=mock_repo_url,
        zip_file=None,
        slider_position=243,
        pattern_type="exclude",
        pattern="",
        branch_or_tag="main",
        is_index=True
    )
    assert response.status_code == 200
    assert isinstance(response, TemplateResponse)
    assert response.template.name == "index.jinja"
    context = response.context
    assert context["result"] is True
    assert context["summary"] == mock_summary
    assert context["tree_data"] == mock_nested_tree_root_for_txt_test
    assert context["content"].startswith(mock_content_str[:100])
    assert context["ingest_id"] == mock_query_id
    assert context["encoded_download_filename"] is not None
    assert mock_repo_slug in context["encoded_download_filename"]
    assert context["base_repo_url"] == mock_repo_url
    assert context["repo_ref"] == "main"
    expected_digest_dir = TMP_BASE_PATH / mock_query_id
    mock_makedirs.assert_called_once_with(expected_digest_dir, exist_ok=True)
    # Default download_format is 'txt', so digest.txt is expected
    mock_open.assert_called_once_with(expected_digest_dir / "digest.txt", "w", encoding="utf-8")

    # Construct the expected content string using values from mock_ingestion_result
    expected_file_content = f"Directory structure:\n{mock_dir_structure_text}\n\n{mock_content_str}"
    mock_file_handle.write.assert_called_once_with(expected_file_content)

    mock_ingest_async.assert_called_once()
    call_kwargs = mock_ingest_async.call_args.kwargs
    assert call_kwargs.get("source") == mock_repo_url
    assert call_kwargs.get("branch") == "main"
    expected_max_file_size = log_slider_to_size(243)
    assert call_kwargs.get("max_file_size") == expected_max_file_size

@pytest.mark.asyncio
@patch("src.server.query_processor.os.makedirs")
@patch("src.server.query_processor.open", new_callable=MagicMock)
@patch("src.server.query_processor.ingest_async", new_callable=AsyncMock)
async def test_process_query_success_url_path_include_pattern(mock_ingest_async, mock_open, mock_makedirs):
    req = mock_request()
    mock_query_obj = MagicMock(spec=IngestionQuery, id="test-id-include", slug="include-repo", url="http://example.com/include", branch="dev", commit=None)
    # mock_query_obj.repo_name = "include-repo" # Not strictly needed for this test's assertions
    # mock_query_obj.user_name = "testuser"   # Not strictly needed for this test's assertions

    minimal_mock_ingestion_result_include = {
        "summary_str": "Include Summary",
        "tree_data": {"name": mock_query_obj.slug + "/", "path": ".", "type": "DIRECTORY", "children": []},
        "directory_structure_text": "",
        "num_tokens": 0, "num_files": 0, "concatenated_content": "", "query_obj": mock_query_obj
    }
    mock_ingest_async.return_value = minimal_mock_ingestion_result_include
    mock_open.return_value.__enter__.return_value = MagicMock() # mock_open is for file writing, not directly asserted here

    await process_query(
        request=req,
        source_type="url_path",
        input_text="http://example.com/include",
        zip_file=None,
        slider_position=243,
        pattern_type="include",
        pattern="*.py",
        branch_or_tag="dev",
        is_index=True
    )
    mock_ingest_async.assert_called_once()
    call_kwargs = mock_ingest_async.call_args.kwargs
    assert call_kwargs.get("include_patterns") == "*.py"
    assert call_kwargs.get("exclude_patterns") is None

@pytest.mark.asyncio
@patch("src.server.query_processor.ingest_async", new_callable=AsyncMock)
async def test_process_query_ingest_async_returns_no_query_id(mock_ingest_async):
    req = mock_request()
    mock_query_obj_no_id = MagicMock(spec=IngestionQuery, id=None, slug="no-id-repo", url="http://example.com/no-id", branch="main")

    mock_ingestion_result_no_id = {
        "summary_str": "Summary No ID",
        "tree_data": {"name": mock_query_obj_no_id.slug + "/", "path": ".", "type": "DIRECTORY", "children": []},
        "directory_structure_text": "",
        "num_tokens": 0, "num_files": 0, "concatenated_content": "", "query_obj": mock_query_obj_no_id
    }
    mock_ingest_async.return_value = mock_ingestion_result_no_id

    response_no_id = await process_query(
        request=req, source_type="url_path", input_text="http://example.com/no-id",
        zip_file=None,
        slider_position=243,
        pattern_type="exclude",
        pattern="",
        branch_or_tag="",
        is_index=True
    )
    assert response_no_id.status_code == 200
    assert isinstance(response_no_id, TemplateResponse)
    assert "Ingestion ID missing" in response_no_id.context["error_message"]

    mock_ingest_async.reset_mock() # Reset for the next scenario in the same test

    mock_ingestion_result_none_obj = {
        "summary_str": "Summary None Obj",
        "tree_data": {}, # Empty dict if query_obj is None and nested_tree_root becomes None
        "directory_structure_text": "",
        "num_tokens": 0, "num_files": 0, "concatenated_content": "", "query_obj": None
    }
    mock_ingest_async.return_value = mock_ingestion_result_none_obj

    response_none_obj = await process_query(
        request=req, source_type="url_path", input_text="http://example.com/none-obj",
        zip_file=None,
        slider_position=243,
        pattern_type="exclude",
        pattern="",
        branch_or_tag="",
        is_index=True
    )
    assert response_none_obj.status_code == 200
    assert isinstance(response_none_obj, TemplateResponse)
    assert "Ingestion ID missing" in response_none_obj.context["error_message"]

@pytest.mark.asyncio
async def test_process_query_invalid_pattern_type_direct_call():
    req = mock_request()
    with pytest.raises(ValueError, match="Invalid pattern type: unknown_type"):
        await process_query(
            request=req,
            source_type="url_path",
            input_text="http://example.com/repo",
            zip_file=None,
            slider_position=243,
            pattern_type="unknown_type",
            pattern="*.py",
            branch_or_tag="",
            is_index=True
        )

@pytest.mark.asyncio
@patch("src.server.query_processor.os.makedirs")
@patch("src.server.query_processor.open", new_callable=MagicMock)
@patch("src.server.query_processor.ingest_async", new_callable=AsyncMock)
async def test_process_query_success_local_path_is_local_true(mock_ingest_async, mock_open, mock_makedirs):
    req = mock_request()
    mock_query_obj = MagicMock(spec=IngestionQuery, id="local-id", slug="local-folder", url=None, branch=None, commit=None)
    mock_local_file_content = "Local content"
    mock_nested_tree_for_local_path = {
        "name": mock_query_obj.slug + "/",
        "path": ".", "type": "DIRECTORY",
        "children": [{
            "name": "file.local", "path": "file.local", "type": "FILE",
            "file_content": mock_local_file_content
        }]
    }
    mock_ingestion_result_local = {
        "summary_str": "Summary local",
        "tree_data": mock_nested_tree_for_local_path,
        "directory_structure_text": "file.local", # This would be generated by _generate_text_tree_lines_recursive
        "num_tokens": 3,
        "num_files": 1,
        "concatenated_content": mock_local_file_content, # If only one file
        "query_obj": mock_query_obj
    }
    mock_ingest_async.return_value = mock_ingestion_result_local
    mock_open.return_value.__enter__.return_value = MagicMock() # Mock for file writing

    response = await process_query(
        request=req,
        source_type="url_path",
        input_text="/path/to/local_folder",
        zip_file=None,
        slider_position=243,
        pattern_type="exclude",
        pattern="",
        branch_or_tag="",
        is_index=True
    )
    assert response.status_code == 200
    assert isinstance(response, TemplateResponse)
    assert response.context.get("is_local_path") is True
    assert response.context.get("base_repo_url") is None
    assert response.context.get("repo_url") == "/path/to/local_folder"

@pytest.mark.asyncio
@patch("src.server.query_processor.ingest_async", new_callable=AsyncMock)
async def test_process_query_handles_git_error_from_ingest(mock_ingest_async):
    req = mock_request()
    mock_ingest_async.side_effect = GitError("Test Git clone failed")
    response = await process_query(
        request=req, source_type="url_path", input_text="http://example.com/git-error",
        zip_file=None, slider_position=243, pattern_type="exclude", pattern="", branch_or_tag="", is_index=True
    )
    assert response.status_code == 500
    assert isinstance(response, TemplateResponse)
    assert "Git operation failed: Test Git clone failed" in response.context["error_message"]

@pytest.mark.asyncio
@patch("src.server.query_processor.Path")
@patch("src.server.query_processor.ingest_async", new_callable=AsyncMock)
async def test_process_query_handles_bad_zip_file_error_from_ingest(mock_ingest_async, mock_path_constructor):
    req = mock_request()
    mock_path_instance = MagicMock()
    mock_path_instance.is_file.return_value = True
    mock_path_constructor.return_value = mock_path_instance
    mock_ingest_async.side_effect = zipfile.BadZipFile("Test bad zip")
    mock_zip_file_obj = MagicMock(spec=UploadFile)
    mock_zip_file_obj.filename = "bad.zip"
    response = await process_query(
        request=req, source_type="zip_file",
        input_text="/path/to/bad.zip",
        zip_file=mock_zip_file_obj,
        slider_position=243, pattern_type="exclude", pattern="", branch_or_tag="", is_index=True
    )
    assert response.status_code == 400
    assert isinstance(response, TemplateResponse)
    assert "not a valid ZIP file or is corrupted. Details: Test bad zip" in response.context["error_message"]
    assert "bad.zip" in response.context["error_message"]

@pytest.mark.asyncio
@patch("src.server.query_processor.ingest_async", new_callable=AsyncMock)
async def test_process_query_handles_invalid_pattern_error_from_ingest(mock_ingest_async):
    req = mock_request()
    mock_ingest_async.side_effect = InvalidPatternError("*.bad!!")
    response = await process_query(
        request=req, source_type="url_path", input_text="http://example.com/pattern-error",
        zip_file=None, slider_position=243, pattern_type="exclude", pattern="*.bad!!", branch_or_tag="", is_index=True
    )
    assert response.status_code == 400
    assert isinstance(response, TemplateResponse)
    assert "Invalid include/exclude pattern provided: Pattern '*.bad!!' contains invalid characters" in response.context["error_message"]

@pytest.mark.asyncio
@patch("src.server.query_processor.ingest_async", new_callable=AsyncMock)
async def test_process_query_handles_generic_exception_from_ingest(mock_ingest_async):
    req = mock_request()
    mock_ingest_async.side_effect = Exception("Something else broke")
    response = await process_query(
        request=req, source_type="url_path", input_text="http://example.com/generic-error",
        zip_file=None, slider_position=243, pattern_type="exclude", pattern="", branch_or_tag="", is_index=True
    )
    assert response.status_code == 500
    assert isinstance(response, TemplateResponse)
    assert "An unexpected error occurred while processing" in response.context["error_message"]

@pytest.mark.asyncio
@patch("src.server.query_processor.os.makedirs")
@patch("src.server.query_processor.open", new_callable=MagicMock)
@patch("src.server.query_processor.ingest_async", new_callable=AsyncMock)
async def test_process_query_digest_write_os_error(mock_ingest_async, mock_open, mock_makedirs):
    req = mock_request()
    mock_query_obj = MagicMock(spec=IngestionQuery, id="os-error-id", slug="os-error-repo", url="http://example.com/os-error", branch="main", commit=None)
    mock_os_error_file_content = "Content"
    mock_nested_tree_for_os_error = {
        "name": mock_query_obj.slug + "/",
        "path": ".", "type": "DIRECTORY",
        "children": [{
            "name": "f.py", "path": "f.py", "type": "FILE",
            "file_content": mock_os_error_file_content
        }]
    }
    mock_ingestion_result_os_error = {
        "summary_str": "Summary OS Error",
        "tree_data": mock_nested_tree_for_os_error,
        "directory_structure_text": "f.py", # This would be generated
        "num_tokens": 1,
        "num_files": 1,
        "concatenated_content": mock_os_error_file_content,
        "query_obj": mock_query_obj
    }
    mock_ingest_async.return_value = mock_ingestion_result_os_error
    mock_open.side_effect = OSError("Disk is full or something") # Simulate error during file write

    response = await process_query(
        request=req, source_type="url_path", input_text="http://example.com/os-error",
        zip_file=None, # Added for consistency
        slider_position=243,
        pattern_type="exclude", # Added for consistency
        pattern="", # Added for consistency
        branch_or_tag="", # Added for consistency
        is_index=True
    )
    assert response.status_code == 200
    assert isinstance(response, TemplateResponse)
    assert response.context.get("ingest_id") is None
    assert "Error saving digest: Disk is full or something" in response.context.get("error_message")
    assert response.context.get("encoded_download_filename") is None

@pytest.mark.asyncio
@patch("src.server.query_processor.os.makedirs")
@patch("src.server.query_processor.open", new_callable=MagicMock)
@patch("src.server.query_processor.ingest_async", new_callable=AsyncMock)
async def test_process_query_filename_gen_with_commit_hash(mock_ingest_async, mock_open, mock_makedirs):
    req = mock_request()
    commit_hash = "abcdef1234567890"
    mock_query_obj = MagicMock(spec=IngestionQuery, id="commit-id", slug="commit-repo", url="http://example.com/commit-repo", branch=None, commit=commit_hash)

    mock_ingestion_result_commit = {
        "summary_str": "Summary Commit Hash",
        "tree_data": {"name": mock_query_obj.slug + "/", "path": ".", "type": "DIRECTORY", "children": []},
        "directory_structure_text": "",
        "num_tokens": 0, "num_files": 0, "concatenated_content": "", "query_obj": mock_query_obj
    }
    mock_ingest_async.return_value = mock_ingestion_result_commit
    mock_open.return_value.__enter__.return_value = MagicMock() # Mock for file writing

    response = await process_query(
        request=req,
        source_type="url_path",
        input_text="http://example.com/commit-repo",
        zip_file=None, # Added for consistency
        branch_or_tag="",
        slider_position=243,
        pattern_type="exclude", # Added for consistency
        pattern="", # Added for consistency
        is_index=True
    )
    assert response.status_code == 200
    assert isinstance(response, TemplateResponse)
    assert response.context.get("result") is True
    assert response.context.get("ingest_id") == "commit-id"
    assert commit_hash[:7] in response.context.get("encoded_download_filename")
    assert "commit-repo_" + commit_hash[:7] + ".txt" in response.context.get("encoded_download_filename")

# TODO: More tests:
# - Digest file saving error when tree_data is empty (partially covered by os_error test if content is empty)
# - Successful ZIP file processing (covering actual file save and passing path to process_query) - needs separate test
# - Test specific MAX_DISPLAY_SIZE cropping for content (covered by success_url_path and success_json_download via context["content"])

@pytest.mark.asyncio
@patch("src.server.query_processor.os.makedirs")
@patch("src.server.query_processor.open", new_callable=MagicMock)
@patch("src.server.query_processor.ingest_async", new_callable=AsyncMock)
async def test_process_query_success_json_download(mock_ingest_async, mock_open, mock_makedirs):
    req = mock_request()
    mock_query_id = "test-json-id"
    mock_repo_slug = "json-repo"
    mock_repo_url = "https://github.com/testowner/json-repo"

    mock_query_obj = MagicMock(spec=IngestionQuery)
    mock_query_obj.id = mock_query_id
    mock_query_obj.slug = mock_repo_slug
    mock_query_obj.url = mock_repo_url
    mock_query_obj.branch = "main"
    mock_query_obj.commit = "abc123xyz" # Example commit

    # Define the dictionary that model_dump(mode='json') is expected to return
    # This will be used by both the SUT and the test's assertion construction.
    expected_query_dump_content = {"url": mock_repo_url, "branch": "main", "commit": "abc123xyz", "local_path": str(TMP_BASE_PATH / mock_query_id / mock_repo_slug)} # Example with a more realistic path
    mock_query_obj.model_dump.return_value = expected_query_dump_content


    mock_summary_str = "JSON Summary"
    mock_file_content = "print('hello json')"
    # Note: tree_data in ingestion_result is tree_data_with_embedded_content
    mock_file_content_for_json_test = "print('hello json from test')" # Distinct content
    mock_nested_tree_root_for_json_test = {
        "name": mock_repo_slug + "/",
        "path": ".",
        "type": "DIRECTORY",
        "children": [
            {
                "name": "file.py",
                "path": "file.py",
                "type": "FILE",
                "file_content": mock_file_content_for_json_test
            }
        ]
    }
    mock_dir_text_val = f"{mock_repo_slug}/\n└── file.py" # More realistic dir structure text
    mock_tokens_val = 10 # Example
    mock_files_val = 1   # From mock_nested_tree_root_for_json_test
    mock_concat_content_val = mock_file_content_for_json_test

    mock_ingestion_result = {
        "summary_str": mock_summary_str,
        "tree_data": mock_nested_tree_root_for_json_test,
        "directory_structure_text": mock_dir_text_val,
        "num_tokens": mock_tokens_val,
        "num_files": mock_files_val,
        "concatenated_content": mock_concat_content_val,
        "query_obj": mock_query_obj
    }
    mock_ingest_async.return_value = mock_ingestion_result

    mock_file_handle = MagicMock()
    mock_open.return_value.__enter__.return_value = mock_file_handle

    response = await process_query(
        request=req,
        source_type="url_path",
        input_text=mock_repo_url,
        zip_file=None,
        slider_position=243,
        branch_or_tag="main",
        download_format="json", # Specify JSON download
        is_index=True
    )

    assert response.status_code == 200
    context = response.context
    assert context["result"] is True
    assert context["summary"] == mock_summary_str
    assert context["tree_data"] == mock_nested_tree_root_for_json_test
    assert context["encoded_download_filename"].endswith(".json")

    expected_digest_dir = TMP_BASE_PATH / mock_query_id
    mock_makedirs.assert_called_once_with(expected_digest_dir, exist_ok=True)
    mock_open.assert_called_once_with(expected_digest_dir / "digest.json", "w", encoding="utf-8")

    # Expected JSON structure for the saved file
    expected_metadata_obj = {
        "repository_url": mock_repo_url,
        "branch": "main",
        "commit": "abc123xyz",
        "number_of_tokens": mock_tokens_val,
        "number_of_files": mock_files_val,
        "directory_structure_text": mock_dir_text_val
    }
    expected_data_to_save = {
        "summary": mock_summary_str,
        "metadata": expected_metadata_obj,
        "tree": mock_nested_tree_root_for_json_test,
        "query": expected_query_dump_content
    }

    # Capture what was written to file
    written_content_str = mock_file_handle.write.call_args[0][0]
    written_data = json.loads(written_content_str)

    assert written_data == expected_data_to_save
    mock_query_obj.model_dump.assert_called_once_with(mode='json')
