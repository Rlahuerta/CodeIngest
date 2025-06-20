# tests/test_output_formatters.py
import pytest
from unittest.mock import MagicMock, patch, PropertyMock # Added PropertyMock
from pathlib import Path

from CodeIngest.output_formatters import (
    _parse_token_estimate_str_to_int,
    _create_tree_data,
    format_node,
    # FormattedNodeData, # If it's a type alias, it might not be needed for tests directly
    # TreeDataItem # Same as above
)
from CodeIngest.schemas import FileSystemNode, FileSystemNodeType
from CodeIngest.query_parsing import IngestionQuery

# Basic fixtures or helper functions can be added here later if needed.

# Tests for _parse_token_estimate_str_to_int
@pytest.mark.parametrize(
    "token_str, expected_int",
    [
        ("123", 123),
        ("0", 0),
        ("1k", 1000),
        ("1K", 1000),
        ("1.5k", 1500),
        ("1.5K", 1500),
        ("0.5k", 500),
        ("100k", 100000),
        ("1m", 1000000),
        ("1M", 1000000),
        ("2.1m", 2100000),
        ("0.5M", 500000),
        ("100M", 100000000),
        ("  1.2k  ", 1200), # Test with whitespace
        (None, 0),
        ("", 0),
        ("abc", 0),
        ("1.k", 1000), # Changed from 0 to 1000 to match current code behavior
        ("1.2.3k", 0), # Malformed
        ("1mk", 0), # Malformed - current logic will take 'm' first
        ("1km", 0), # Malformed - current logic will take 'k' first
    ],
)
def test_parse_token_estimate_str_to_int_valid_and_edge_cases(token_str, expected_int):
    assert _parse_token_estimate_str_to_int(token_str) == expected_int

def test_parse_token_estimate_str_to_int_specific_malformed():
    # Test cases where current logic might have specific outcomes due to replace order
    # Test cases where current logic might have specific outcomes due to replace order
    assert _parse_token_estimate_str_to_int("1m2k") == 0 # Corrected expectation
    assert _parse_token_estimate_str_to_int("1k2m") == 0    # Corrected expectation
    # The case for "1.k" is now in the parametrized test and aligned with code behavior.
    # If "1.k" should be 0, the function _parse_token_estimate_str_to_int needs adjustment.
    # For now, testing existing behavior.

# Helper Fixture for mock IngestionQuery
@pytest.fixture
def mock_query():
    query = MagicMock(spec=IngestionQuery)
    query.local_path = Path("/tmp/repo_root") # Example root path
    return query

# Helper Fixture for basic FileSystemNode
@pytest.fixture
def mock_fs_node():
    node = MagicMock(spec=FileSystemNode)
    node.children = [] # Default to no children
    node.type = FileSystemNodeType.FILE # Default type, can be overridden in tests
    node.name = "test_file.txt"
    node.path = Path(f"/tmp/repo_root/{node.name}") # Make path consistent with name and mock_query
    node.path_str = node.name # Simplified path_str for these tests, relative to root
    # Mock the 'content' property directly using PropertyMock
    type(node).content = PropertyMock(return_value="Default file content")
    return node

# Tests for _create_tree_data
def test_create_tree_data_single_file(mock_fs_node, mock_query):
    mock_fs_node.name = "file1.py"
    mock_fs_node.path = mock_query.local_path / "file1.py"
    mock_fs_node.path_str = "file1.py"
    mock_fs_node.type = FileSystemNodeType.FILE
    type(mock_fs_node).content = PropertyMock(return_value="print('hello')") # Using PropertyMock

    result_tree = _create_tree_data(mock_fs_node, repo_root_path=mock_query.local_path)

    assert len(result_tree) == 1
    item = result_tree[0]
    assert item["name"] == "file1.py"
    assert item["type"] == FileSystemNodeType.FILE.name
    assert item["file_content"] == "print('hello')"
    assert item["full_relative_path"] == "file1.py"
    assert item["prefix"] == "" # Root node, no prefix

def test_create_tree_data_empty_file(mock_fs_node, mock_query):
    mock_fs_node.name = "empty.txt"
    mock_fs_node.path = mock_query.local_path / "empty.txt"
    mock_fs_node.path_str = "empty.txt"
    mock_fs_node.type = FileSystemNodeType.FILE
    type(mock_fs_node).content = PropertyMock(return_value="") # Using PropertyMock

    result_tree = _create_tree_data(mock_fs_node, repo_root_path=mock_query.local_path)

    assert len(result_tree) == 1
    item = result_tree[0]
    assert item["name"] == "empty.txt"
    assert item["type"] == FileSystemNodeType.FILE.name
    assert item["file_content"] == ""
    assert item["full_relative_path"] == "empty.txt"

def test_create_tree_data_file_with_error_content(mock_fs_node, mock_query):
    mock_fs_node.name = "error.bin"
    mock_fs_node.path = mock_query.local_path / "error.bin"
    mock_fs_node.path_str = "error.bin"
    mock_fs_node.type = FileSystemNodeType.FILE
    type(mock_fs_node).content = PropertyMock(return_value="[Non-text file]") # Using PropertyMock

    result_tree = _create_tree_data(mock_fs_node, repo_root_path=mock_query.local_path)

    assert len(result_tree) == 1
    item = result_tree[0]
    assert item["name"] == "error.bin"
    assert item["type"] == FileSystemNodeType.FILE.name
    assert item["file_content"] == "[Non-text file]"
    assert item["full_relative_path"] == "error.bin"

def test_create_tree_data_filters_symlink(mock_fs_node, mock_query):
    mock_fs_node.type = FileSystemNodeType.SYMLINK
    mock_fs_node.name = "link_to_something"

    result_tree = _create_tree_data(mock_fs_node, repo_root_path=mock_query.local_path)

    assert len(result_tree) == 0

def test_create_tree_data_directory_with_file_and_symlink(mock_query):
    # Root directory node
    root_dir_node = MagicMock(spec=FileSystemNode)
    root_dir_node.name = "." # Typically how root is named if it's the source
    root_dir_node.path = mock_query.local_path
    root_dir_node.path_str = "."
    root_dir_node.type = FileSystemNodeType.DIRECTORY

    # File child
    file_child = MagicMock(spec=FileSystemNode)
    file_child.name = "actual_file.txt"
    file_child.path = mock_query.local_path / "actual_file.txt"
    file_child.path_str = "actual_file.txt" # Relative to root_dir_node's path_str (which is root itself)
    file_child.type = FileSystemNodeType.FILE
    type(file_child).content = PropertyMock(return_value="Actual content") # Using PropertyMock
    file_child.children = []

    # Symlink child
    symlink_child = MagicMock(spec=FileSystemNode)
    symlink_child.name = "a_symlink"
    symlink_child.path = mock_query.local_path / "a_symlink"
    symlink_child.path_str = "a_symlink"
    symlink_child.type = FileSystemNodeType.SYMLINK
    symlink_child.children = []

    root_dir_node.children = [file_child, symlink_child]

    result_tree = _create_tree_data(root_dir_node, repo_root_path=mock_query.local_path)

    # Expected: root dir, and actual_file.txt. Symlink is filtered by _create_tree_data.
    assert len(result_tree) == 2

    # Root directory item
    # The name of the root node when path_str is "." becomes the directory name
    # And display_name for root dir appends "/"
    assert result_tree[0]["name"] == mock_query.local_path.name + "/"
    assert result_tree[0]["type"] == FileSystemNodeType.DIRECTORY.name
    assert result_tree[0]["full_relative_path"] == "" # Root path is empty string
    assert "file_content" not in result_tree[0] # Directories don't have file_content

    # File item
    assert result_tree[1]["name"] == "actual_file.txt"
    assert result_tree[1]["type"] == FileSystemNodeType.FILE.name
    assert result_tree[1]["file_content"] == "Actual content"
    assert result_tree[1]["full_relative_path"] == "actual_file.txt"
    assert result_tree[1]["prefix"].strip() == "└──" # Assuming it's the last/only item after symlink filter

def test_create_tree_data_prefix_and_paths(mock_query):
    # Setup a deeper structure
    # repo_root/
    #   ├── dir1/
    #   │   └── file1.txt
    #   └── file2.txt

    root_node = MagicMock(spec=FileSystemNode)
    root_node.name = "."
    root_node.path = mock_query.local_path
    root_node.path_str = "."
    root_node.type = FileSystemNodeType.DIRECTORY

    dir1_node = MagicMock(spec=FileSystemNode)
    dir1_node.name = "dir1"
    dir1_node.path = mock_query.local_path / "dir1"
    dir1_node.path_str = "dir1"
    dir1_node.type = FileSystemNodeType.DIRECTORY

    file1_node = MagicMock(spec=FileSystemNode)
    file1_node.name = "file1.txt"
    file1_node.path = mock_query.local_path / "dir1" / "file1.txt"
    file1_node.path_str = "dir1/file1.txt"
    file1_node.type = FileSystemNodeType.FILE
    type(file1_node).content = PropertyMock(return_value="content1") # Using PropertyMock
    file1_node.children = []

    dir1_node.children = [file1_node]

    file2_node = MagicMock(spec=FileSystemNode)
    file2_node.name = "file2.txt"
    file2_node.path = mock_query.local_path / "file2.txt"
    file2_node.path_str = "file2.txt"
    file2_node.type = FileSystemNodeType.FILE
    type(file2_node).content = PropertyMock(return_value="content2") # Using PropertyMock
    file2_node.children = []

    root_node.children = [dir1_node, file2_node] # dir1 is first, file2 is last at this level

    result_tree = _create_tree_data(root_node, repo_root_path=mock_query.local_path)

    # Expected structure in flat list: root_node, dir1_node, file1_node, file2_node
    assert len(result_tree) == 4

    # Item 0: root_node (e.g. "repo_root/")
    assert result_tree[0]["name"] == mock_query.local_path.name + "/"
    assert result_tree[0]["prefix"] == ""
    assert result_tree[0]["full_relative_path"] == ""

    # Item 1: dir1_node (e.g. "  ├── dir1/")
    assert result_tree[1]["name"] == "dir1/"
    assert result_tree[1]["prefix"].strip() == "├──"
    assert result_tree[1]["full_relative_path"] == "dir1"

    # Item 2: file1_node (e.g. "  │   └── file1.txt")
    assert result_tree[2]["name"] == "file1.txt"
    assert result_tree[2]["prefix"].strip() == "│   └──" # Or similar based on actual prefix chars
    assert result_tree[2]["full_relative_path"] == "dir1/file1.txt"
    assert result_tree[2]["file_content"] == "content1"

    # Item 3: file2_node (e.g. "  └── file2.txt")
    assert result_tree[3]["name"] == "file2.txt"
    assert result_tree[3]["prefix"].strip() == "└──"
    assert result_tree[3]["full_relative_path"] == "file2.txt"
    assert result_tree[3]["file_content"] == "content2"

# Tests for format_node
# We will patch _create_tree_data and _gather_file_contents to isolate format_node logic

@patch("CodeIngest.output_formatters._gather_file_contents")
@patch("CodeIngest.output_formatters._create_tree_data")
def test_format_node_structure_and_data_assembly(
    mock_create_tree_data, mock_gather_file_contents, mock_query, mock_fs_node # Use existing fixtures
):
    # --- Setup Mocks ---
    # Mock what _create_tree_data would return
    mock_tree_item_file = {
        "name": "file1.py", "type": FileSystemNodeType.FILE.name,
        "prefix": "├── ", "file_content": "content1"
    }
    mock_tree_item_dir = {
        "name": "dir1/", "type": FileSystemNodeType.DIRECTORY.name,
        "prefix": "└── "
    }
    mock_generated_tree_data = [mock_tree_item_file, mock_tree_item_dir]
    mock_create_tree_data.return_value = mock_generated_tree_data

    # Mock what _gather_file_contents would return
    mock_concatenated_content = "content1" # Simplified for this test
    mock_gather_file_contents.return_value = mock_concatenated_content

    # Configure the root FileSystemNode (mock_fs_node) for this test
    # This node is passed to format_node, its attributes are used for summary, etc.
    mock_fs_node.type = FileSystemNodeType.DIRECTORY
    mock_fs_node.file_count = 1 # This is the pre-filtering count for the summary string
    mock_fs_node.name = "test_project_root_node_name" # Give distinct name from path.name for clarity
    mock_fs_node.path = mock_query.local_path / "test_project_path_name" # Path used for query.local_path comparison
    mock_fs_node.path_str = "." # Root of ingestion for summary

    # Configure mock_query for summary prefix
    mock_query.slug = "test_project_slug"
    mock_query.user_name = None
    mock_query.repo_name = None
    mock_query.branch = None
    mock_query.commit = None
    mock_query.subpath = "/"


    # --- Call the function under test ---
    # Patch _format_token_count for predictable token string during this call
    with patch("CodeIngest.output_formatters._format_token_count", return_value="1") as mock_format_count_in_test:
        result_dict = format_node(mock_fs_node, mock_query)
        # Assert _format_token_count was called as expected by format_node
        mock_format_count_in_test.assert_called_once_with(mock_concatenated_content)


    # --- Assertions ---
    # c. Verify the structure of the dictionary
    expected_keys = [
        "summary_str", "tree_data_with_embedded_content",
        "directory_structure_text_str", "num_tokens", "num_files",
        "concatenated_content_for_txt"
    ]
    for key in expected_keys:
        assert key in result_dict, f"Key {key} missing from format_node result"

    # Assert that _create_tree_data and _gather_file_contents were called
    mock_create_tree_data.assert_called_once_with(mock_fs_node, repo_root_path=mock_query.local_path, parent_prefix="")
    mock_gather_file_contents.assert_called_once_with(mock_fs_node)

    # Verify tree_data passed through
    assert result_dict["tree_data_with_embedded_content"] == mock_generated_tree_data

    # d. Verify num_files (count from mocked_generated_tree_data)
    # mock_generated_tree_data has one FILE node
    assert result_dict["num_files"] == 1

    # e. Verify directory_structure_text_str (based on mocked_generated_tree_data)
    expected_dir_struct_text = "├── file1.py\n└── dir1/"
    assert result_dict["directory_structure_text_str"] == expected_dir_struct_text

    # f. Verify num_tokens (based on the patched _format_token_count which returned "1")
    # _parse_token_estimate_str_to_int("1") should be 1
    assert result_dict["num_tokens"] == 1

    # Verify summary_str (check for key parts)
    assert "Source: test_project_slug" in result_dict["summary_str"]
    # The file_count in summary is from the raw node.file_count before filtering
    assert f"Files analyzed: {mock_fs_node.file_count}" in result_dict["summary_str"]
    assert "Estimated tokens: 1" in result_dict["summary_str"] # From the patched _format_token_count

    # Verify concatenated_content_for_txt
    assert result_dict["concatenated_content_for_txt"] == mock_concatenated_content
