import pytest
import math
from unittest.mock import MagicMock, patch, mock_open as unittest_mock_open # Added mock_open
from pathlib import Path # Added Path

from fastapi import Request, HTTPException
from slowapi.errors import RateLimitExceeded
from slowapi.wrappers import Limit as SlowAPILimit
from starlette.responses import Response

from src.server.server_utils import log_slider_to_size, rate_limit_exception_handler, _process_folder # Imported _process_folder

# Tests for log_slider_to_size
def test_log_slider_to_size_min_position():
    assert log_slider_to_size(0) == 1 * 1024

def test_log_slider_to_size_max_position():
    assert log_slider_to_size(500) == 102400 * 1024

def test_log_slider_to_size_mid_position():
    expected_kb_at_243 = round(math.exp(math.log(1) + (math.log(102400) - math.log(1)) * pow(243 / 500, 1.5)))
    assert log_slider_to_size(243) == expected_kb_at_243 * 1024

def test_log_slider_to_size_another_position():
    expected_kb_at_100 = round(math.exp(math.log(1) + (math.log(102400) - math.log(1)) * pow(100 / 500, 1.5)))
    assert log_slider_to_size(100) == expected_kb_at_100 * 1024

# Tests for rate_limit_exception_handler
@pytest.mark.asyncio
async def test_rate_limit_exception_handler_handles_rate_limit_exceeded():
    mock_req = MagicMock(spec=Request)
    mock_limit_obj = MagicMock(spec=SlowAPILimit)
    mock_limit_obj.limit_str = "10/minute"
    mock_limit_obj.key = "test_key"
    mock_limit_obj.error_message = "Rate limit exceeded from mock_limit_obj"
    rate_limit_exc = RateLimitExceeded(limit=mock_limit_obj)
    rate_limit_exc.detail = "Rate limit exceeded by test"

    with patch("src.server.server_utils._rate_limit_exceeded_handler", return_value=Response(status_code=429)) as mock_internal_handler:
        response = await rate_limit_exception_handler(mock_req, rate_limit_exc)
        mock_internal_handler.assert_called_once_with(mock_req, rate_limit_exc)
        assert response.status_code == 429

@pytest.mark.asyncio
async def test_rate_limit_exception_handler_reraises_other_exceptions():
    mock_req = MagicMock(spec=Request)
    other_exc = ValueError("Some other error")

    with pytest.raises(ValueError, match="Some other error"):
        await rate_limit_exception_handler(mock_req, other_exc)

# Tests for _process_folder
@pytest.mark.asyncio
@patch("src.server.server_utils.shutil.rmtree")
@patch("src.server.server_utils.open", new_callable=unittest_mock_open) # Mocks builtin open
async def test_process_folder_success_logs_and_deletes(mock_open_builtin, mock_rmtree):
    mock_folder = MagicMock(spec=Path)
    mock_folder.name = "test_folder_to_delete"

    mock_txt_file = MagicMock(spec=Path)
    mock_txt_file.suffix = ".txt"
    mock_txt_file.stem = "owner-repo"
    mock_folder.iterdir.return_value = [mock_txt_file]

    await _process_folder(mock_folder)

    mock_open_builtin.assert_called_once_with("history.txt", mode="a", encoding="utf-8")
    mock_file_handle = mock_open_builtin()
    mock_file_handle.write.assert_called_once_with("owner/repo\n")

    mock_rmtree.assert_called_once_with(mock_folder)

@pytest.mark.asyncio
@patch("src.server.server_utils.shutil.rmtree")
@patch("src.server.server_utils.open", new_callable=unittest_mock_open)
@patch("src.server.server_utils.logger.warning")
async def test_process_folder_unparsable_txt_name(mock_logger_warning, mock_open_builtin, mock_rmtree):
    mock_folder = MagicMock(spec=Path, name="folder_unparsable")
    mock_txt_file = MagicMock(spec=Path, suffix=".txt", stem="unparsable")
    mock_txt_file.name = "unparsable.txt" # Set .name attribute for the log message
    mock_folder.iterdir.return_value = [mock_txt_file]

    await _process_folder(mock_folder)

    mock_open_builtin.assert_not_called()
    mock_rmtree.assert_called_once_with(mock_folder)
    # Check for the new specific warning message
    assert any(
        call.args[0] == "Could not parse repository name from filename '%s' in folder %s. Expected 'owner-repo.txt' format." and
        call.args[1] == "unparsable.txt"
        for call in mock_logger_warning.call_args_list
    ), "Expected warning log for unparsable filename not found or format incorrect."

@pytest.mark.asyncio
@patch("src.server.server_utils.shutil.rmtree")
@patch("src.server.server_utils.open", new_callable=unittest_mock_open)
async def test_process_folder_no_txt_files(mock_open_builtin, mock_rmtree):
    mock_folder = MagicMock(spec=Path, name="folder_no_txt")
    mock_other_file = MagicMock(spec=Path, suffix=".md")
    mock_folder.iterdir.return_value = [mock_other_file]

    await _process_folder(mock_folder)
    mock_open_builtin.assert_not_called()
    mock_rmtree.assert_called_once_with(mock_folder)

@pytest.mark.asyncio
@patch("src.server.server_utils.shutil.rmtree")
@patch("src.server.server_utils.open", new_callable=unittest_mock_open)
@patch("src.server.server_utils.logger.warning")
async def test_process_folder_history_write_os_error(mock_logger_warning, mock_open_builtin, mock_rmtree):
    mock_folder = MagicMock(spec=Path, name="folder_history_error")
    mock_txt_file = MagicMock(spec=Path, suffix=".txt", stem="owner-repo")
    mock_folder.iterdir.return_value = [mock_txt_file]
    mock_open_builtin.side_effect = OSError("Cannot write history")

    await _process_folder(mock_folder)

    mock_open_builtin.assert_called_once_with("history.txt", mode="a", encoding="utf-8")
    mock_rmtree.assert_called_once_with(mock_folder)
    # Assert based on the actual log call structure: logger.warning(message_format, folder_arg, exc_arg)
    assert any(
        call.args[0] == "Error logging repository URL for %s: %s" and
        "Cannot write history" in str(call.args[2]) # Exception is the third arg (index 2)
        for call in mock_logger_warning.call_args_list
    )

@pytest.mark.asyncio
@patch("src.server.server_utils.shutil.rmtree")
@patch("src.server.server_utils.open", new_callable=unittest_mock_open)
@patch("src.server.server_utils.logger.error")
async def test_process_folder_rmtree_os_error(mock_logger_error, mock_open_builtin, mock_rmtree):
    mock_folder = MagicMock(spec=Path, name="folder_rmtree_error")
    mock_txt_file = MagicMock(spec=Path, suffix=".txt", stem="owner-repo-rmfail")
    mock_folder.iterdir.return_value = [mock_txt_file]
    mock_rmtree.side_effect = OSError("Cannot delete folder")

    await _process_folder(mock_folder)

    mock_open_builtin.assert_called_once_with("history.txt", mode="a", encoding="utf-8")
    mock_rmtree.assert_called_once_with(mock_folder)
    # Assert based on the actual log call structure: logger.error(message_format, folder_arg, exc_arg, exc_info=True)
    assert any(
        call.args[0] == "Error deleting folder %s: %s" and
        "Cannot delete folder" in str(call.args[2]) # Exception is the third arg (index 2)
        for call in mock_logger_error.call_args_list
    )

# TODO: Tests for _remove_old_repositories (more complex)
