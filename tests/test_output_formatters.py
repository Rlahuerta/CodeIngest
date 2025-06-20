# tests/test_output_formatters.py
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path
from typing import Dict, Any, Optional, List # Ensure these are imported

from CodeIngest.output_formatters import (
    _parse_token_estimate_str_to_int,
    _create_tree_data,
    format_node,
    _count_files_in_nested_tree, # For testing format_node indirectly
    _generate_text_tree_lines_recursive # For testing format_node indirectly
)
from CodeIngest.schemas import FileSystemNode, FileSystemNodeType
from CodeIngest.query_parsing import IngestionQuery
from CodeIngest.output_formatters import NestedTreeDataItem # Assuming this alias is defined in the module

# Tests for _parse_token_estimate_str_to_int (Keep existing tests)
@pytest.mark.parametrize(
    "token_str, expected_int",
    [
        ("123", 123), ("0", 0), ("1k", 1000), ("1K", 1000), ("1.5k", 1500),
        ("1.5K", 1500), ("0.5k", 500), ("100k", 100000), ("1m", 1000000),
        ("1M", 1000000), ("2.1m", 2100000), ("0.5M", 500000), ("100M", 100000000),
        ("  1.2k  ", 1200), (None, 0), ("", 0), ("abc", 0), ("1.k", 1000),
        ("1.2.3k", 0), ("1mk", 0), ("1km", 0),
    ],
)
def test_parse_token_estimate_str_to_int_valid_and_edge_cases(token_str, expected_int):
    assert _parse_token_estimate_str_to_int(token_str) == expected_int

def test_parse_token_estimate_str_to_int_specific_malformed():
    assert _parse_token_estimate_str_to_int("1m2k") == 0
    assert _parse_token_estimate_str_to_int("1k2m") == 0

# --- Fixtures ---
@pytest.fixture
def mock_query() -> MagicMock: # Added type hint for return
    query = MagicMock(spec=IngestionQuery)
    query.local_path = Path("/tmp/repo_root")
    # For _create_summary_prefix used in format_node tests
    query.slug = "test_slug"
    query.user_name = None; query.repo_name = None; query.branch = None; query.commit = None; query.subpath = "/"
    return query

def create_mock_fs_node(name: str, node_type: FileSystemNodeType, repo_root: Path, relative_path_str: str, content: Optional[str] = None, children: Optional[List[MagicMock]] = None) -> MagicMock:
    node = MagicMock(spec=FileSystemNode)
    node.name = name
    node.type = node_type
    node.path = repo_root / relative_path_str if relative_path_str != "." else repo_root
    node.path_str = relative_path_str # path_str in FileSystemNode is relative to its parent dir for FS traversal
                                      # but for _create_tree_data, repo_root_path is the main reference.

    # Mock the 'content' property
    if content is not None:
        type(node).content = PropertyMock(return_value=content)
    else:
        type(node).content = PropertyMock(return_value="[Default mock content if not specified]")

    node.children = children if children else []
    node.file_count = 0 # Used by format_node's summary part
    if node_type == FileSystemNodeType.FILE:
        node.file_count = 1
    elif node_type == FileSystemNodeType.DIRECTORY and children:
        # Correctly sum up file_count from children for directory nodes
        current_file_count = 0
        for child in children:
            # Ensure child has file_count attribute, default to 0 if not (though create_mock_fs_node should set it)
            current_file_count += getattr(child, 'file_count', 0)
        node.file_count = current_file_count
    elif node_type == FileSystemNodeType.DIRECTORY: # Empty directory
        node.file_count = 0


    return node

# --- Tests for _create_tree_data (Rewritten for nested structure) ---

def test_create_tree_data_single_file_nested(mock_query):
    file_node = create_mock_fs_node("file1.py", FileSystemNodeType.FILE, mock_query.local_path, "file1.py", "print('hello')")

    result_tree_root = _create_tree_data(file_node, mock_query.local_path)

    assert result_tree_root is not None
    assert result_tree_root["name"] == "file1.py"
    assert result_tree_root["path"] == "file1.py"
    assert result_tree_root["type"] == "FILE"
    assert result_tree_root["file_content"] == "print('hello')"
    assert "children" not in result_tree_root

def test_create_tree_data_empty_file_nested(mock_query):
    file_node = create_mock_fs_node("empty.txt", FileSystemNodeType.FILE, mock_query.local_path, "empty.txt", "")
    result_tree_root = _create_tree_data(file_node, mock_query.local_path)

    assert result_tree_root is not None
    assert result_tree_root["file_content"] == ""

def test_create_tree_data_file_with_error_content_nested(mock_query):
    file_node = create_mock_fs_node("error.bin", FileSystemNodeType.FILE, mock_query.local_path, "error.bin", "[Non-text file]")
    result_tree_root = _create_tree_data(file_node, mock_query.local_path)

    assert result_tree_root is not None
    assert result_tree_root["file_content"] == "[Non-text file]"

def test_create_tree_data_filters_symlink_nested(mock_query):
    symlink_node = create_mock_fs_node("my_link", FileSystemNodeType.SYMLINK, mock_query.local_path, "my_link")
    result_tree_root = _create_tree_data(symlink_node, mock_query.local_path)
    assert result_tree_root is None

def test_create_tree_data_directory_nested(mock_query):
    file_child = create_mock_fs_node("child.txt", FileSystemNodeType.FILE, mock_query.local_path, "parent_dir/child.txt", "child content")
    dir_node = create_mock_fs_node("parent_dir", FileSystemNodeType.DIRECTORY, mock_query.local_path, "parent_dir", children=[file_child])

    result_tree_root = _create_tree_data(dir_node, mock_query.local_path)

    assert result_tree_root is not None
    assert result_tree_root["name"] == "parent_dir/"
    assert result_tree_root["path"] == "parent_dir"
    assert result_tree_root["type"] == "DIRECTORY"
    assert "file_content" not in result_tree_root
    assert "children" in result_tree_root
    assert len(result_tree_root["children"]) == 1

    child_in_tree = result_tree_root["children"][0]
    assert child_in_tree["name"] == "child.txt"
    assert child_in_tree["path"] == "parent_dir/child.txt" # Path for children should be relative to repo_root
    assert child_in_tree["type"] == "FILE"
    assert child_in_tree["file_content"] == "child content"

def test_create_tree_data_root_node_naming_and_path(mock_query):
    # Simulate the root node as passed by ingestion.py
    # name will be the actual directory name, path will be the full path to it.
    # repo_root_path will be the same full path.
    # relative_path_str for _create_tree_data should be "."
    root_fs_node = create_mock_fs_node(mock_query.local_path.name, FileSystemNodeType.DIRECTORY, mock_query.local_path, ".")

    result_tree_root = _create_tree_data(root_fs_node, mock_query.local_path)

    assert result_tree_root is not None
    assert result_tree_root["name"] == mock_query.local_path.name + "/"
    assert result_tree_root["path"] == "."
    assert result_tree_root["type"] == "DIRECTORY"

def test_create_tree_data_directory_with_symlink_child_filtered(mock_query):
    file_child = create_mock_fs_node("good.txt", FileSystemNodeType.FILE, mock_query.local_path, "data_dir/good.txt", "good")
    symlink_child = create_mock_fs_node("bad_link", FileSystemNodeType.SYMLINK, mock_query.local_path, "data_dir/bad_link")
    dir_node = create_mock_fs_node("data_dir", FileSystemNodeType.DIRECTORY, mock_query.local_path, "data_dir", children=[file_child, symlink_child])

    result_tree_root = _create_tree_data(dir_node, mock_query.local_path)
    assert result_tree_root is not None
    assert "children" in result_tree_root
    assert len(result_tree_root["children"]) == 1
    assert result_tree_root["children"][0]["name"] == "good.txt"


# --- Updated Test for format_node ---
@patch("CodeIngest.output_formatters._gather_file_contents")
@patch("CodeIngest.output_formatters._create_tree_data")
def test_format_node_with_nested_tree(
    mock_create_tree_data_nested, mock_gather_file_contents, mock_query
):
    # --- Setup Mocks ---
    # 1. Mock what the new _create_tree_data (nested version) would return
    mock_file_node_in_tree = {
        "name": "file1.py", "path": "file1.py",
        "type": FileSystemNodeType.FILE.name, "file_content": "content of file1"
    }
    mock_dir_node_in_tree = {
        "name": "sub_dir/", "path": "sub_dir",
        "type": FileSystemNodeType.DIRECTORY.name, "children": []
    }
    mock_nested_root_name = mock_query.local_path.name + "/"
    mock_nested_root = {
        "name": mock_nested_root_name, "path": ".",
        "type": FileSystemNodeType.DIRECTORY.name,
        "children": [mock_file_node_in_tree, mock_dir_node_in_tree]
    }
    mock_create_tree_data_nested.return_value = mock_nested_root

    # 2. Mock what _gather_file_contents would return
    mock_concatenated_content = "content of file1"
    mock_gather_file_contents.return_value = mock_concatenated_content

    # 3. Configure the root FileSystemNode passed to format_node
    # This node's attributes are used for summary (like file_count) and as input to _create_tree_data
    root_fs_node_for_format = create_mock_fs_node(
        mock_query.local_path.name, FileSystemNodeType.DIRECTORY, mock_query.local_path, "."
    )
    # Set file_count on the root_fs_node for the summary string.
    # This count is the raw one from initial scan, before _create_tree_data filters symlinks etc.
    # The _count_files_in_nested_tree will calculate the one for the final JSON.
    root_fs_node_for_format.file_count = 1 # Example: summary says "1 file analyzed"

    # --- Call the function under test ---
    with patch("CodeIngest.output_formatters._format_token_count", return_value="3") as mock_format_t_count:
        result_dict = format_node(root_fs_node_for_format, mock_query)
        mock_format_t_count.assert_called_once_with(mock_concatenated_content)

    # --- Assertions ---
    expected_keys = [
        "summary_str", "tree_data_with_embedded_content",
        "directory_structure_text_str", "num_tokens", "num_files",
        "concatenated_content_for_txt"
    ]
    for key in expected_keys:
        assert key in result_dict, f"Key {key} missing from format_node result"

    mock_create_tree_data_nested.assert_called_once_with(root_fs_node_for_format, mock_query.local_path)
    mock_gather_file_contents.assert_called_once_with(root_fs_node_for_format)

    assert result_dict["tree_data_with_embedded_content"] == mock_nested_root

    # Verify num_files (uses _count_files_in_nested_tree on mock_nested_root)
    assert result_dict["num_files"] == 1 # Based on mock_nested_root having one file

    # Verify directory_structure_text_str
    expected_dir_text = f"{mock_nested_root_name}\n├── file1.py\n└── sub_dir/"
    assert result_dict["directory_structure_text_str"] == expected_dir_text.strip()

    assert result_dict["num_tokens"] == 3

    assert f"Files analyzed: {root_fs_node_for_format.file_count}" in result_dict["summary_str"]
    assert "Estimated tokens: 3" in result_dict["summary_str"]
    assert result_dict["concatenated_content_for_txt"] == mock_concatenated_content
