# tests/test_ingestion.py
"""Tests for the `ingestion` module."""

import os
import pytest
import warnings
import zipfile
import logging
from pathlib import Path
from unittest.mock import patch, MagicMock
from typing import Optional, Dict, Any, List # Added List for create_mock_fs_node if used, Dict, Any, Optional

from CodeIngest.ingestion import ingest_query, apply_gitingest_file, _process_node, _process_file, limit_exceeded
from CodeIngest.query_parsing import IngestionQuery
from CodeIngest.schemas import FileSystemNode, FileSystemNodeType, FileSystemStats
from CodeIngest.config import MAX_DIRECTORY_DEPTH, MAX_FILES, MAX_TOTAL_SIZE_BYTES
from CodeIngest.utils.ignore_patterns import DEFAULT_IGNORE_PATTERNS
from CodeIngest.utils.ingestion_utils import _should_exclude

@pytest.fixture
def temp_directory(tmp_path: Path) -> Path:
    test_dir = tmp_path / "test_repo_source"; test_dir.mkdir()
    (test_dir / "empty_file.txt").touch()
    (test_dir / "file1.txt").write_text("Hello World")
    (test_dir / "file2.py").write_text("print('Hello')")
    src_dir = test_dir / "src"; src_dir.mkdir()
    (src_dir / "subfile1.txt").write_text("Hello from src")
    (src_dir / "subfile2.py").write_text("print('Hello from src')")
    subdir = src_dir / "subdir"; subdir.mkdir()
    (subdir / "file_subdir.txt").write_text("Hello from subdir")
    (subdir / "file_subdir.py").write_text("print('Hello from subdir')")
    dir1 = test_dir / "dir1"; dir1.mkdir()
    (dir1 / "file_dir1.txt").write_text("Hello from dir1")
    dir2 = test_dir / "dir2"; dir2.mkdir()
    (dir2 / "file_dir2.txt").write_text("Hello from dir2")
    (test_dir / ".gitingest").write_text("[config]\nignore_patterns = [\"dir2\"]")
    (test_dir / ".hiddenfile").write_text("Hidden Content")
    hidden_dir = test_dir / ".hiddendir"; hidden_dir.mkdir()
    (hidden_dir / "hidden_in_dir.txt").write_text("Hidden Dir Content")
    try: os.symlink(test_dir / "file1.txt", test_dir / "symlink_to_file1")
    except OSError as e: pytest.skip(f"Could not create symlink: {e}")
    return test_dir

@pytest.fixture
def sample_query() -> IngestionQuery:
    default_ignores = DEFAULT_IGNORE_PATTERNS.copy()
    if ".git" not in default_ignores: default_ignores.add(".git")
    return IngestionQuery(
        user_name=None, repo_name=None, url=None, subpath="/",
        local_path=Path("/tmp/placeholder"), slug="placeholder_slug", id="placeholder_id", branch=None,
        max_file_size=1_000_000, ignore_patterns=default_ignores, include_patterns=None,
        original_zip_path=None, temp_extract_path=None,
    )


def test_ingest_query_directory(temp_directory: Path, sample_query: IngestionQuery) -> None:
    """Test `ingest_query` with a directory source."""
    sample_query.local_path = temp_directory
    sample_query.slug = temp_directory.name
    sample_query.ignore_patterns.discard("*.py") # Include python files
    sample_query.ignore_patterns.add(".hiddendir") # Exclude hidden dir for this specific test

    result = ingest_query(sample_query)

    assert f"Source: {temp_directory.name}" in result["summary_str"]
    # The "Files analyzed: X" in summary_str is based on node.file_count before symlink filtering.
    # result["num_files"] is after symlink filtering in _create_tree_data.
    # Symlinks are now filtered by _create_tree_data, so they won't be in tree_data or num_files.
    # The original fixture created 11 non-symlink files + 1 symlink.
    # .gitingest ignores "dir2" (1 file). .hiddendir is ignored by this test (1 file).
    # So, 11 - 1 (dir2) - 1 (.hiddendir) = 9 files should be in the tree.
    # The summary string "Files analyzed" count might be higher as it's from initial scan.
    assert "Files analyzed: 11" in result["summary_str"] # This reflects pre-filter count if node.file_count was used
    # Count includes: empty_file.txt, file1.txt, file2.py, src/subfile1.txt, src/subfile2.py,
    # src/subdir/file_subdir.txt, src/subdir/file_subdir.py, dir1/file_dir1.txt, .gitingest, .hiddenfile
    # Total = 10. (dir2 and .hiddendir contents are ignored by patterns)
    assert result["num_files"] == 10 # Actual files in the tree_data after symlink filtering, including .gitingest

    nested_tree_root = result["tree_data_with_embedded_content"]
    assert isinstance(nested_tree_root, dict) # Root of a nested tree is a dict
    assert nested_tree_root["name"] == temp_directory.name + "/"
    assert nested_tree_root["path"] == "."
    assert nested_tree_root["type"] == "DIRECTORY"

    # Use helper to find nodes
    assert find_node_in_nested_tree(nested_tree_root, ".gitingest") is not None
    file1_node_found = find_node_in_nested_tree(nested_tree_root, "file1.txt")
    assert file1_node_found is not None
    assert file1_node_found["file_content"] == "Hello World"

    src_subfile2_node = find_node_in_nested_tree(nested_tree_root, "src/subfile2.py")
    assert src_subfile2_node is not None
    assert src_subfile2_node["file_content"] == "print('Hello from src')"

    hidden_node_found = find_node_in_nested_tree(nested_tree_root, ".hiddenfile")
    assert hidden_node_found is not None
    assert hidden_node_found["file_content"] == "Hidden Content"

    assert find_node_in_nested_tree(nested_tree_root, "dir2/file_dir2.txt") is None
    assert find_node_in_nested_tree(nested_tree_root, ".hiddendir/hidden_in_dir.txt") is None
    assert find_node_in_nested_tree(nested_tree_root, "symlink_to_file1") is None


    # concatenated_content_for_txt should still contain content from non-symlink files
    assert "FILE: file1.txt" in result["concatenated_content_for_txt"]
    assert "Hello World" in result["concatenated_content_for_txt"]
    assert "FILE: src/subfile2.py" in result["concatenated_content_for_txt"]
    assert "print('Hello from src')" in result["concatenated_content_for_txt"]
    assert "FILE: .hiddenfile" in result["concatenated_content_for_txt"]
    assert "Hidden Content" in result["concatenated_content_for_txt"]
    assert "FILE: dir2/file_dir2.txt" not in result["concatenated_content_for_txt"]
    assert "FILE: .hiddendir/hidden_in_dir.txt" not in result["concatenated_content_for_txt"]
    # Symlink content is read by _gather_file_contents if it points to a text file.
    # However, symlinks themselves are filtered from tree_data by _create_tree_data.
    # _gather_file_contents includes a placeholder for symlinks.
    assert "SYMLINK: symlink_to_file1 ->" in result["concatenated_content_for_txt"] # _gather_file_contents includes symlink placeholders

    # Already checked specific embedded content above with find_node_in_nested_tree


def test_ingest_query_single_file(temp_directory: Path, sample_query: IngestionQuery) -> None:
    """Test `ingest_query` with a single file source."""
    file_path = temp_directory / "file1.txt"
    sample_query.local_path = file_path
    sample_query.slug = file_path.stem

    result = ingest_query(sample_query)

    assert f"Source: {file_path.stem}" in result["summary_str"]
    assert f"File: {file_path.name}" in result["summary_str"]
    assert "Lines: 1" in result["summary_str"]
    assert result["num_files"] == 1

    nested_tree_root = result["tree_data_with_embedded_content"]
    assert isinstance(nested_tree_root, dict)
    assert nested_tree_root['name'] == 'file1.txt'
    assert nested_tree_root['path'] == 'file1.txt' # For single file, path is filename
    assert nested_tree_root['type'] == "FILE"
    assert nested_tree_root['file_content'] == "Hello World"

    assert "FILE: file1.txt" in result["concatenated_content_for_txt"]
    assert "Hello World" in result["concatenated_content_for_txt"]


def test_ingest_query_single_file_excluded_by_pattern(temp_directory: Path, sample_query: IngestionQuery) -> None:
    """Test `ingest_query` with a single file source that is excluded."""
    file_path = temp_directory / "file1.txt"
    sample_query.local_path = file_path
    sample_query.ignore_patterns = {"*.txt"}
    with pytest.raises(ValueError, match="File 'file1.txt' is excluded by ignore patterns."):
        ingest_query(sample_query)


def test_ingest_query_single_file_not_included_by_pattern(temp_directory: Path, sample_query: IngestionQuery) -> None:
    """Test `ingest_query` with a single file source not matching include patterns."""
    file_path = temp_directory / "file1.txt"
    sample_query.local_path = file_path
    sample_query.include_patterns = {"*.py"}
    with pytest.raises(ValueError, match="File 'file1.txt' does not match include patterns."):
        ingest_query(sample_query)


def test_ingest_query_nonexistent_path(sample_query: IngestionQuery) -> None:
    """Test `ingest_query` with a non-existent local path."""
    sample_query.local_path = Path("/nonexistent/path/that/fails")
    sample_query.slug = "nonexistent/path/that/fails"
    with pytest.raises(ValueError, match="Target path for 'nonexistent/path/that/fails' cannot be found:"):
        ingest_query(sample_query)


def test_ingest_query_single_file_no_content(temp_directory: Path, sample_query: IngestionQuery, caplog: pytest.LogCaptureFixture) -> None:
    """Test `ingest_query` with a single non-text file."""
    binary_file = temp_directory / "non_text_file.bin"
    binary_file.write_bytes(b'\x00\x01\x02\x03')
    sample_query.local_path = binary_file
    sample_query.slug = binary_file.stem

    result = ingest_query(sample_query)
    # Check if the specific log message exists (already asserted by FileSystemNode.content property test, not directly here)
    # This test focuses on what ingest_query returns.

    assert f"Source: {binary_file.stem}" in result["summary_str"]
    assert f"File: {binary_file.name}" in result["summary_str"]
    assert "Lines: 1" in result["summary_str"] # Line count for placeholder like "[Non-text file]"
    assert result["num_files"] == 1

    nested_tree_root = result["tree_data_with_embedded_content"]
    assert isinstance(nested_tree_root, dict)
    assert nested_tree_root['name'] == 'non_text_file.bin'
    assert nested_tree_root['type'] == "FILE"
    assert nested_tree_root['file_content'] == "[Non-text file]"

    assert "FILE: non_text_file.bin" in result["concatenated_content_for_txt"]
    assert "[Non-text file]" in result["concatenated_content_for_txt"]


# --- Tests for apply_gitingest_file (remain the same) ---
def test_apply_gitingest_file_basic(temp_directory: Path, sample_query: IngestionQuery) -> None:
    gitingest_path = temp_directory / ".gitingest"; gitingest_path.write_text('[config]\nignore_patterns = ["*.log", "temp/", "build/*"]')
    initial_ignore_count = len(sample_query.ignore_patterns)
    apply_gitingest_file(temp_directory, sample_query)
    assert "*.log" in sample_query.ignore_patterns; assert "temp/" in sample_query.ignore_patterns
    assert "build/*" in sample_query.ignore_patterns; assert ".git" in sample_query.ignore_patterns
    assert len(sample_query.ignore_patterns) > initial_ignore_count

def test_apply_gitingest_file_invalid_toml(temp_directory: Path, sample_query: IngestionQuery, caplog: pytest.LogCaptureFixture) -> None:
    gitingest_path = temp_directory / ".gitingest"; gitingest_path.write_text("[config\nignore_patterns = [")
    original_ignores = sample_query.ignore_patterns.copy()
    apply_gitingest_file(temp_directory, sample_query)
    assert any(
        "Invalid TOML in" in record.message and "Expected ']'" in record.message and record.levelname == "WARNING"
        for record in caplog.records
    ), "Expected warning for invalid TOML not found or incorrect level."
    assert sample_query.ignore_patterns == original_ignores

def test_apply_gitingest_file_missing_config_section(temp_directory: Path, sample_query: IngestionQuery) -> None:
    gitingest_path = temp_directory / ".gitingest"; gitingest_path.write_text("ignore_patterns = [\"*.log\"]")
    original_ignores = sample_query.ignore_patterns.copy(); apply_gitingest_file(temp_directory, sample_query)
    assert sample_query.ignore_patterns == original_ignores

def test_apply_gitingest_file_ignore_patterns_not_list_or_set(temp_directory: Path, sample_query: IngestionQuery, caplog: pytest.LogCaptureFixture) -> None:
    """Test `apply_gitingest_file` when ignore_patterns is not list/set."""
    gitingest_path = temp_directory / ".gitingest"; gitingest_path.write_text("[config]\nignore_patterns = 123")
    original_ignores = sample_query.ignore_patterns.copy()
    apply_gitingest_file(temp_directory, sample_query)
    assert any(
        "Expected list/set for 'ignore_patterns', got <class 'int'>" in record.message and record.levelname == "WARNING"
        for record in caplog.records
    ), "Expected warning for non-list/set ignore_patterns not found or incorrect level."
    assert sample_query.ignore_patterns == original_ignores

def test_apply_gitingest_file_ignore_patterns_with_non_strings(temp_directory: Path, sample_query: IngestionQuery, caplog: pytest.LogCaptureFixture) -> None:
    gitingest_path = temp_directory / ".gitingest"; gitingest_path.write_text('[config]\nignore_patterns = ["*.log", 123, "temp/"]')
    original_ignores = sample_query.ignore_patterns.copy()
    apply_gitingest_file(temp_directory, sample_query)
    assert any(
        "Ignoring non-string patterns" in record.message and "{123}" in record.message and record.levelname == "WARNING"
        for record in caplog.records
    ), "Expected warning for non-string patterns not found or incorrect level."
    assert "*.log" in sample_query.ignore_patterns; assert "temp/" in sample_query.ignore_patterns
    assert 123 not in sample_query.ignore_patterns

# --- Tests for _process_node and _process_file using FileSystemStats flags ---

@pytest.mark.filterwarnings("ignore:coroutine 'AsyncMockMixin._execute_mock_call' was never awaited")
def test_process_node_oserror_iterdir(temp_directory: Path, sample_query: IngestionQuery, caplog: pytest.LogCaptureFixture) -> None:
    root_node = FileSystemNode(name="test_repo", type=FileSystemNodeType.DIRECTORY, path_str=".", path=temp_directory)
    stats = FileSystemStats()
    with patch.object(Path, "iterdir", side_effect=OSError("Permission denied")):
        _process_node(root_node, sample_query, stats, temp_directory)
    assert any(
        r"Cannot access directory contents" in record.message and "Permission denied" in record.message and record.levelname == "WARNING"
        for record in caplog.records
    ), "Expected warning for iterdir OSError not found or incorrect level."
    assert len(root_node.children) == 0; assert stats.total_files == 0

def test_process_node_symlink(temp_directory: Path, sample_query: IngestionQuery) -> None:
    root_node = FileSystemNode(name="test_repo", type=FileSystemNodeType.DIRECTORY, path_str=".", path=temp_directory)
    stats = FileSystemStats(); sample_query.ignore_patterns = set(); sample_query.include_patterns = None # Reset patterns for this test
    _process_node(root_node, sample_query, stats, temp_directory) # Process all files
    symlink_node = next((child for child in root_node.children if child.name == "symlink_to_file1"), None)
    assert symlink_node is not None; assert symlink_node.type == FileSystemNodeType.SYMLINK
    assert symlink_node.path_str == "symlink_to_file1"; assert stats.total_files > 0 # Check if any file was processed, including symlink

def test_process_node_symlink_excluded_by_include(temp_directory: Path, sample_query: IngestionQuery) -> None:
    root_node = FileSystemNode(name="test_repo", type=FileSystemNodeType.DIRECTORY, path_str=".", path=temp_directory)
    stats = FileSystemStats(); sample_query.include_patterns = {"*.py"}; sample_query.ignore_patterns = set()
    _process_node(root_node, sample_query, stats, temp_directory)
    symlink_node = next((child for child in root_node.children if child.name == "symlink_to_file1"), None)
    assert symlink_node is None

def test_process_file_oserror_stat(temp_directory: Path, sample_query: IngestionQuery, caplog: pytest.LogCaptureFixture) -> None:
    parent_node = FileSystemNode(name=".", type=FileSystemNodeType.DIRECTORY, path_str=".", path=temp_directory)
    stats = FileSystemStats(); file_path = temp_directory / "stat_error.txt"; file_path.touch()
    with patch.object(Path, 'stat', side_effect=OSError("Stat failed")):
        _process_file(file_path, parent_node, stats, temp_directory, sample_query.max_file_size)
    assert any(
        "Could not stat file" in record.message and "Stat failed" in record.message and record.levelname == "WARNING"
        for record in caplog.records
    ), "Expected warning for stat OSError not found or incorrect level."
    assert len(parent_node.children) == 0; assert stats.total_files == 0

def test_process_file_exceeds_max_file_size(temp_directory: Path, sample_query: IngestionQuery, caplog: pytest.LogCaptureFixture) -> None:
    parent_node = FileSystemNode(name=".", type=FileSystemNodeType.DIRECTORY, path_str=".", path=temp_directory)
    stats = FileSystemStats(); file_path = temp_directory / "large.bin"; file_path.write_text("a" * (sample_query.max_file_size + 10))
    # Ensure the logger for the module under test is set to capture INFO
    caplog.set_level(logging.INFO, logger="CodeIngest.ingestion")
    _process_file(file_path, parent_node, stats, temp_directory, sample_query.max_file_size)
    assert any(
        "Skipping file large.bin" in record.message and "exceeds max file size" in record.message and record.levelname == "INFO"
        for record in caplog.records
    ), "Expected info log for max file size exceeded not found or incorrect level."
    assert len(parent_node.children) == 0; assert stats.total_files == 0

def test_process_file_exceeds_total_size_limit(temp_directory: Path, sample_query: IngestionQuery, caplog: pytest.LogCaptureFixture) -> None:
    parent_node = FileSystemNode(name=".", type=FileSystemNodeType.DIRECTORY, path_str=".", path=temp_directory)
    stats = FileSystemStats(); stats.total_size = MAX_TOTAL_SIZE_BYTES - 5
    file_path = temp_directory / "pushover.txt"; file_path.write_text("This is more than 5 bytes")
    caplog.set_level(logging.INFO, logger="CodeIngest.ingestion")
    _process_file(file_path, parent_node, stats, temp_directory, sample_query.max_file_size)
    assert any(
        "Total size limit" in record.message and "reached" in record.message and record.levelname == "INFO"
        for record in caplog.records
    ), "Expected info log for total size limit reached not found or incorrect level."
    assert len(parent_node.children) == 0; assert stats.total_size == MAX_TOTAL_SIZE_BYTES - 5
    assert stats.total_size_limit_reached is True

def test_process_file_exceeds_total_file_limit(temp_directory: Path, sample_query: IngestionQuery, caplog: pytest.LogCaptureFixture) -> None:
    parent_node = FileSystemNode(name=".", type=FileSystemNodeType.DIRECTORY, path_str=".", path=temp_directory)
    stats = FileSystemStats(); stats.total_files = MAX_FILES - 1
    file_ok = temp_directory / "ok.txt"; file_ok.touch()
    file_bad = temp_directory / "bad.txt"; file_bad.touch()
    caplog.set_level(logging.INFO, logger="CodeIngest.ingestion")
    _process_file(file_ok, parent_node, stats, temp_directory, sample_query.max_file_size) # This one should pass
    # Clear previous logs from "ok.txt" processing if any, to only check "bad.txt" effect
    caplog.clear()
    _process_file(file_bad, parent_node, stats, temp_directory, sample_query.max_file_size) # This one should trigger limit
    assert any(
        "Maximum file limit" in record.message and "reached" in record.message and record.levelname == "INFO"
        for record in caplog.records
    ), "Expected info log for max file limit reached not found or incorrect level."
    assert len(parent_node.children) == 1; assert parent_node.children[0].name == "ok.txt"
    assert stats.total_files == MAX_FILES # Count stopped at limit
    assert stats.total_file_limit_reached is True

def test_limit_exceeded_depth(caplog: pytest.LogCaptureFixture) -> None:
    stats = FileSystemStats(); depth = MAX_DIRECTORY_DEPTH + 1
    caplog.set_level(logging.INFO, logger="CodeIngest.ingestion")
    assert limit_exceeded(stats, depth) is True
    # This will remove the SyntaxWarning for invalid escape sequences
    expected_message = f"Max directory depth ({MAX_DIRECTORY_DEPTH}) reached."
    assert any(
        expected_message in record.message and record.levelname == "INFO"
        for record in caplog.records
    ), f"Expected info log for max depth reached ('{expected_message}') not found or incorrect level."
    assert stats.depth_limit_reached is True

def test_limit_exceeded_file_count(caplog: pytest.LogCaptureFixture) -> None:
    stats = FileSystemStats(); stats.total_file_limit_reached = True; depth = 0
    assert limit_exceeded(stats, depth) is True

def test_limit_exceeded_total_size() -> None:
    stats = FileSystemStats(); stats.total_size_limit_reached = True; depth = 0
    assert limit_exceeded(stats, depth) is True

def test_limit_exceeded_none() -> None:
    stats = FileSystemStats(); depth = 5
    assert limit_exceeded(stats, depth) is False

def test_process_node_with_include_pattern(temp_directory: Path, sample_query: IngestionQuery) -> None:
    root_node = FileSystemNode(name=temp_directory.name, type=FileSystemNodeType.DIRECTORY, path_str=".", path=temp_directory)
    stats = FileSystemStats(); sample_query.include_patterns = {"*.txt"}; sample_query.ignore_patterns = set()
    _process_node(root_node, sample_query, stats, temp_directory)
    assert any(item.name == 'file1.txt' for item in root_node.children)
    assert not any(item.name == 'file2.py' for item in root_node.children)
    hidden_dir_node = next((child for child in root_node.children if child.name == ".hiddendir"), None)
    assert hidden_dir_node is not None
    assert any(item.name == 'hidden_in_dir.txt' for item in hidden_dir_node.children)

def test_process_node_with_exclude_pattern(temp_directory: Path, sample_query: IngestionQuery) -> None:
    root_node = FileSystemNode(name=temp_directory.name, type=FileSystemNodeType.DIRECTORY, path_str=".", path=temp_directory)
    stats = FileSystemStats(); apply_gitingest_file(temp_directory, sample_query) # Applies "dir2" ignore
    sample_query.ignore_patterns.add("*.py"); assert "*.py" in sample_query.ignore_patterns
    sample_query.include_patterns = None
    _process_node(root_node, sample_query, stats, temp_directory)
    assert not any(item.name == 'dir2' for item in root_node.children)
    assert not any(item.name == 'file2.py' for item in root_node.children)
    src_node = next((child for child in root_node.children if child.name == "src"), None); assert src_node is not None
    assert not any(item.name == 'subfile2.py' for item in src_node.children)

def test_should_exclude_directory_pattern(temp_directory: Path) -> None:
    """Test _should_exclude works for directory names and files within."""
    ignore_patterns = {"dir1"} # Pattern is just the dir name
    # Test the directory itself
    assert _should_exclude(temp_directory / "dir1", temp_directory, ignore_patterns) is True
    # Test a file directly inside the directory
    assert _should_exclude(temp_directory / "dir1" / "file_dir1.txt", temp_directory, ignore_patterns) is True
    # Test a file NOT inside the directory
    assert _should_exclude(temp_directory / "file1.txt", temp_directory, ignore_patterns) is False
    # Test with a wildcard pattern
    ignore_patterns_wild = {"dir1/*"}
    assert _should_exclude(temp_directory / "dir1", temp_directory, ignore_patterns_wild) is False # Dir itself doesn't match
    assert _should_exclude(temp_directory / "dir1" / "file_dir1.txt", temp_directory, ignore_patterns_wild) is True # File matches


def test_ingest_query_single_file_is_directory(temp_directory: Path, sample_query: IngestionQuery) -> None:
    """Test ingest_query handles directory path even if type hints it's a file."""
    dir_path = temp_directory / "src"; sample_query.local_path = dir_path; sample_query.slug = dir_path.name
    sample_query.type = "blob"; sample_query.ignore_patterns.discard("*.py") # Allow .py files

    result = ingest_query(sample_query)

    assert f"Source: {dir_path.name}" in result["summary_str"]
    # "src" dir contains: subfile1.txt, subfile2.py, subdir/ (which has file_subdir.txt, file_subdir.py)
    # Total 4 files.
    assert "Files analyzed: 4" in result["summary_str"] # Pre-filter count from summary
    assert result["num_files"] == 4 # Actual files in tree

    nested_tree_root = result["tree_data_with_embedded_content"]
    assert isinstance(nested_tree_root, dict)
    assert nested_tree_root["name"] == dir_path.name + "/"
    assert nested_tree_root["path"] == "." # Root of ingestion is the dir itself
    assert nested_tree_root["type"] == "DIRECTORY"

    subfile1_node_found = find_node_in_nested_tree(nested_tree_root, "subfile1.txt")
    assert subfile1_node_found is not None
    assert subfile1_node_found["file_content"] == "Hello from src"

    subdir_file_node = find_node_in_nested_tree(nested_tree_root, "subdir/file_subdir.py")
    assert subdir_file_node is not None
    assert subdir_file_node["file_content"] == "print('Hello from subdir')"

    assert "FILE: subfile1.txt" in result["concatenated_content_for_txt"]
    assert "Hello from src" in result["concatenated_content_for_txt"]
    assert "print('Hello from subdir')" in result["concatenated_content_for_txt"]

def test_process_node_empty_directory_after_filtering(temp_directory: Path, sample_query: IngestionQuery) -> None:
    """Test directory isn't added if all children are filtered out."""
    root_node = FileSystemNode(name=temp_directory.name, type=FileSystemNodeType.DIRECTORY, path_str=".", path=temp_directory)
    stats = FileSystemStats(); sample_query.ignore_patterns.add("*.py"); sample_query.ignore_patterns.add("*.txt")
    assert "*.py" in sample_query.ignore_patterns; assert "*.txt" in sample_query.ignore_patterns
    _process_node(root_node, sample_query, stats, temp_directory)
    src_node = next((child for child in root_node.children if child.name == "src"), None)
    assert src_node is None # src dir node shouldn't be added

def test_filesystemnode_sort_children_not_directory() -> None:
    """Test sorting children raises error if node is not a directory."""
    file_node = FileSystemNode(name="f", type=FileSystemNodeType.FILE, path_str="f", path=Path("f"))
    with pytest.raises(ValueError, match="Cannot sort children of a non-directory node"):
        file_node.sort_children()

# --- Commented out read_chunks tests ---
# ... (tests remain commented out) ...

# Helper function to find a node in the new nested tree structure
# Added at the end to ensure it's defined before use by tests above through hoisting
def find_node_in_nested_tree(node: Optional[Dict[str, Any]], target_path: str) -> Optional[Dict[str, Any]]:
    if not node:
        return None
    # Assuming 'path' is the key for the relative path in the nested tree nodes
    if node.get("path") == target_path:
        return node

    if node.get("type") == "DIRECTORY" and "children" in node and isinstance(node["children"], list):
        for child in node["children"]:
            found = find_node_in_nested_tree(child, target_path)
            if found:
                return found
    return None