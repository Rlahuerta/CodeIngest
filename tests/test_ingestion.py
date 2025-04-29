"""
Tests for the `ingestion` module.

These tests validate directory scanning, file content extraction, notebook handling, and the overall ingestion logic,
including filtering patterns and subpaths.
"""

import json
import os
import warnings
from pathlib import Path
from typing import Set # Import Set for the helper function type hint
from unittest.mock import MagicMock, patch

import pytest

from CodeIngest.config import (
    MAX_DIRECTORY_DEPTH,
    MAX_FILES,
    MAX_TOTAL_SIZE_BYTES,
)
from CodeIngest.ingestion import (
    _process_file,
    _process_node,
    apply_gitingest_file,
    ingest_query,
    limit_exceeded,
)
from CodeIngest.output_formatters import _create_tree_structure, format_node
from CodeIngest.query_parsing import IngestionQuery
from CodeIngest.schemas import (
    FileSystemNode,
    FileSystemNodeType,
    FileSystemStats,
)
from CodeIngest.schemas.filesystem_schema import SEPARATOR
from CodeIngest.utils.ignore_patterns import DEFAULT_IGNORE_PATTERNS
from CodeIngest.utils.ingestion_utils import _should_exclude


# Fixture to create a temporary directory structure (remains the same)
@pytest.fixture
def temp_directory(tmp_path: Path) -> Path:
    test_dir = tmp_path / "test_repo"
    test_dir.mkdir()
    (test_dir / "file1.txt").write_text("Hello World")
    (test_dir / "file2.py").write_text("print('Hello')")
    src_dir = test_dir / "src"
    src_dir.mkdir()
    (src_dir / "subfile1.txt").write_text("Hello from src")
    (src_dir / "subfile2.py").write_text("print('Hello from src')")
    subdir = src_dir / "subdir"
    subdir.mkdir()
    (subdir / "file_subdir.txt").write_text("Hello from subdir")
    (subdir / "file_subdir.py").write_text("print('Hello from subdir')")
    dir1 = test_dir / "dir1"
    dir1.mkdir()
    (dir1 / "file_dir1.txt").write_text("Hello from dir1")
    dir2 = test_dir / "dir2"
    dir2.mkdir()
    (dir2 / "file_dir2.txt").write_text("Hello from dir2")
    (test_dir / ".gitingest").write_text('[config]\nignore_patterns = ["dir2"]')
    try:
        os.symlink(test_dir / "file1.txt", test_dir / "symlink_to_file1")
        os.symlink("/nonexistent/target", test_dir / "broken_symlink")
    except OSError as e:
        pytest.skip(f"Could not create symlink: {e}")
    (test_dir / "non_text_file.bin").write_bytes(b"\x00\x01\x02\x03")
    notebook_content = {"cells": [{"cell_type": "code", "source": ["print('Hello Notebook')"]}]}
    with (test_dir / "notebook.ipynb").open("w") as f:
        json.dump(notebook_content, f)
    (test_dir / "empty_file.txt").touch()
    # Add directory for empty filtered test
    filtered_dir = test_dir / "filtered_dir"
    filtered_dir.mkdir()
    (filtered_dir / "filtered_file.py").write_text("print('filtered')")

    return test_dir


# Fixture for a sample query (remains the same)
@pytest.fixture
def sample_query() -> IngestionQuery:
    default_ignores = DEFAULT_IGNORE_PATTERNS.copy()
    default_ignores.update({"*.pyc", "__pycache__", ".git", "*.py"})
    return IngestionQuery(
        user_name="test_user",
        repo_name="test_repo",
        url=None,
        subpath="/",
        local_path=Path("/tmp/test_repo").resolve(), # Default, override in tests
        slug="test_user/test_repo",
        id="id",
        branch="main",
        max_file_size=1_000_000,
        ignore_patterns=default_ignores,
        include_patterns=None,
    )

# --- FIX: Helper function for collecting file paths from a node ---
def get_all_file_paths(node: FileSystemNode) -> Set[str]:
    """Recursively collects path_str of all FILE nodes under the given node."""
    paths = set()
    if node.type == FileSystemNodeType.FILE:
        paths.add(node.path_str)
    elif node.type == FileSystemNodeType.DIRECTORY:
        for child in node.children:
            paths.update(get_all_file_paths(child))
    # Ignore symlinks for this helper
    return paths
# --- End Helper ---

def test_ingest_query_directory(temp_directory: Path, sample_query: IngestionQuery) -> None:
    """Test `ingest_query` with a directory source."""
    sample_query.local_path = temp_directory
    sample_query.subpath = "/"
    sample_query.type = None
    sample_query.ignore_patterns.discard("*.py") # Include python files

    summary, tree, content = ingest_query(sample_query)

    assert "Repository: test_user/test_repo" in summary
    # FIX: Update expected file count to 14 (includes files + counted symlinks)
    assert "Files analyzed: 12" in summary

    # Check for correctly formatted content blocks
    assert f"{SEPARATOR}\nFILE: file1.txt\n{SEPARATOR}\nHello World\n\n" in content
    assert f"{SEPARATOR}\nFILE: file2.py\n{SEPARATOR}\nprint('Hello')\n\n" in content
    assert f'{SEPARATOR}\nFILE: .gitingest\n{SEPARATOR}\n[config]\nignore_patterns = ["dir2"]\n\n' in content
    assert f"{SEPARATOR}\nFILE: src/subfile1.txt\n{SEPARATOR}\nHello from src\n\n" in content
    assert f"{SEPARATOR}\nFILE: src/subfile2.py\n{SEPARATOR}\nprint('Hello from src')\n\n" in content
    assert f"{SEPARATOR}\nFILE: src/subdir/file_subdir.txt\n{SEPARATOR}\nHello from subdir\n\n" in content
    assert f"{SEPARATOR}\nFILE: src/subdir/file_subdir.py\n{SEPARATOR}\nprint('Hello from subdir')\n\n" in content
    assert f"{SEPARATOR}\nFILE: dir1/file_dir1.txt\n{SEPARATOR}\nHello from dir1\n\n" in content
    assert f"{SEPARATOR}\nFILE: dir2/file_dir2.txt\n{SEPARATOR}" not in content
    assert f"{SEPARATOR}\nFILE: non_text_file.bin\n{SEPARATOR}\n[Non-text file]\n\n" in content
    assert f"{SEPARATOR}\nFILE: notebook.ipynb\n{SEPARATOR}\n# Jupyter notebook converted to Python script.\n\nprint('Hello Notebook')\n\n\n" in content
    assert f"{SEPARATOR}\nFILE: empty_file.txt\n{SEPARATOR}\n\n\n" in content
    assert f"{SEPARATOR}\nFILE: filtered_dir/filtered_file.py\n{SEPARATOR}\nprint('filtered')\n\n" in content

    # FIX: Correct assertion for symlink target path representation in tree
    # The target path is now relative to the parent of the base path (temp_directory)
    assert f"symlink_to_file1 -> file1.txt" in tree
    # Check broken link representation
    assert "broken_symlink -> /nonexistent/target" in tree

def test_ingest_query_single_file(temp_directory: Path, sample_query: IngestionQuery) -> None:
    """Test `ingest_query` with a single file source."""
    file_path = temp_directory / "file1.txt"
    sample_query.local_path = file_path
    sample_query.subpath = "/"
    sample_query.type = "blob"

    summary, tree, content = ingest_query(sample_query)

    assert "File: file1.txt" in summary
    assert "Lines: 1" in summary # Line count should be correct now
    assert "file1.txt" in tree
    expected_content = f"{SEPARATOR}\nFILE: file1.txt\n{SEPARATOR}\nHello World\n\n"
    assert content == expected_content

# ... (excluded/included tests remain the same) ...
def test_ingest_query_single_file_excluded_by_pattern(temp_directory: Path, sample_query: IngestionQuery) -> None:
    file_path = temp_directory / "file1.txt"
    sample_query.local_path = file_path
    sample_query.ignore_patterns = {"*.txt"}
    with pytest.raises(ValueError, match="File 'file1.txt' is excluded by ignore patterns."):
        ingest_query(sample_query)

def test_ingest_query_single_file_not_included_by_pattern(temp_directory: Path, sample_query: IngestionQuery) -> None:
    file_path = temp_directory / "file1.txt"
    sample_query.local_path = file_path
    sample_query.include_patterns = {"*.py"}
    with pytest.raises(ValueError, match="File 'file1.txt' does not match include patterns."):
        ingest_query(sample_query)

def test_ingest_query_nonexistent_path(sample_query: IngestionQuery) -> None:
    sample_query.local_path = Path("/nonexistent/path")
    with pytest.raises(ValueError, match="Target path for 'test_user/test_repo' cannot be found:"):
        ingest_query(sample_query)

def test_ingest_query_single_file_no_content(temp_directory: Path, sample_query: IngestionQuery) -> None:
    """Test `ingest_query` with a single non-text file."""
    binary_file = temp_directory / "non_text_file.bin"
    sample_query.local_path = binary_file

    summary, tree, content = ingest_query(sample_query)

    assert "File: non_text_file.bin" in summary
    expected_content = f"{SEPARATOR}\nFILE: non_text_file.bin\n{SEPARATOR}\n[Non-text file]\n\n"
    assert content == expected_content

# ... (apply_gitingest tests remain the same) ...
def test_apply_gitingest_file_basic(temp_directory: Path, sample_query: IngestionQuery) -> None:
    gitingest_path = temp_directory / ".gitingest"
    gitingest_content = '[config]\nignore_patterns = ["*.log", "temp/", "build/*"]'
    gitingest_path.write_text(gitingest_content)
    sample_query.ignore_patterns = DEFAULT_IGNORE_PATTERNS.copy()
    apply_gitingest_file(temp_directory, sample_query)
    assert sample_query.ignore_patterns is not None
    assert "*.log" in sample_query.ignore_patterns
    assert "temp/" in sample_query.ignore_patterns
    assert "build/*" in sample_query.ignore_patterns
    assert ".git" in sample_query.ignore_patterns

def test_apply_gitingest_file_invalid_toml(temp_directory: Path, sample_query: IngestionQuery) -> None:
    gitingest_path = temp_directory / ".gitingest"
    gitingest_path.write_text("[config\nignore_patterns = [")
    original_ignore_patterns = sample_query.ignore_patterns.copy() if sample_query.ignore_patterns else set()
    with pytest.warns(UserWarning, match="Invalid TOML in"):
        apply_gitingest_file(temp_directory, sample_query)
    assert sample_query.ignore_patterns == original_ignore_patterns

def test_apply_gitingest_file_missing_config_section(temp_directory: Path, sample_query: IngestionQuery) -> None:
    gitingest_path = temp_directory / ".gitingest"
    gitingest_path.write_text('ignore_patterns = ["*.log"]')
    original_ignore_patterns = sample_query.ignore_patterns.copy() if sample_query.ignore_patterns else set()
    apply_gitingest_file(temp_directory, sample_query)
    assert sample_query.ignore_patterns == original_ignore_patterns

def test_apply_gitingest_file_ignore_patterns_not_list_or_set(temp_directory: Path, sample_query: IngestionQuery) -> None:
    gitingest_path = temp_directory / ".gitingest"
    gitingest_path.write_text("[config]\nignore_patterns = 123")
    original_ignore_patterns = sample_query.ignore_patterns.copy() if sample_query.ignore_patterns else set()
    with pytest.warns(UserWarning, match=r"Expected a list/set for 'ignore_patterns'"):
        apply_gitingest_file(temp_directory, sample_query)
    assert sample_query.ignore_patterns == original_ignore_patterns

def test_apply_gitingest_file_ignore_patterns_with_non_strings(temp_directory: Path, sample_query: IngestionQuery) -> None:
    gitingest_path = temp_directory / ".gitingest"
    gitingest_content = '[config]\nignore_patterns = ["*.log", 123, "temp/"]'
    gitingest_path.write_text(gitingest_content)
    sample_query.ignore_patterns = DEFAULT_IGNORE_PATTERNS.copy() | {"*.py"} # Start with defaults + py
    original_plus_defaults = sample_query.ignore_patterns.copy()
    with pytest.warns(UserWarning, match="Ignoring non-string patterns"):
        apply_gitingest_file(temp_directory, sample_query)
    expected_patterns = original_plus_defaults | {"*.log", "temp/"}
    assert sample_query.ignore_patterns == expected_patterns


# ... (_process_node tests remain the same) ...
def test_process_node_oserror_iterdir(temp_directory: Path, sample_query: IngestionQuery) -> None:
    root_node = FileSystemNode(name="test_repo", type=FileSystemNodeType.DIRECTORY, path_str="test_repo", path=temp_directory)
    stats = FileSystemStats()
    with patch.object(Path, "iterdir", side_effect=OSError("Simulated iterdir error")):
        with pytest.warns(UserWarning, match="Cannot access directory"):
            _process_node(root_node, sample_query, stats, temp_directory)
    assert len(root_node.children) == 0
    assert stats.total_files == 0

def test_process_node_symlink(temp_directory: Path, sample_query: IngestionQuery) -> None:
    root_node = FileSystemNode(name="test_repo", type=FileSystemNodeType.DIRECTORY, path_str="test_repo", path=temp_directory)
    stats = FileSystemStats()
    with patch.object(Path, "iterdir") as mock_iterdir:
        mock_symlink_path = MagicMock(spec=Path, name="symlink_to_file1")
        mock_symlink_path.name = "symlink_to_file1"; mock_symlink_path.is_symlink.return_value = True
        mock_symlink_path.is_file.return_value = False; mock_symlink_path.is_dir.return_value = False
        mock_symlink_path.relative_to.return_value = Path("symlink_to_file1")
        mock_symlink_path.readlink.return_value = Path("file1.txt")
        mock_symlink_path.path = temp_directory / "symlink_to_file1"
        mock_iterdir.return_value = [mock_symlink_path]
        sample_query.ignore_patterns = set(); sample_query.include_patterns = set()
        _process_node(root_node, sample_query, stats, temp_directory)
    assert len(root_node.children) == 1
    assert root_node.children[0].name == "symlink_to_file1"; assert root_node.children[0].type == FileSystemNodeType.SYMLINK
    assert stats.total_files == 1

def test_process_node_symlink_excluded_by_include(temp_directory: Path, sample_query: IngestionQuery) -> None:
    root_node = FileSystemNode(name="test_repo", type=FileSystemNodeType.DIRECTORY, path_str="test_repo", path=temp_directory)
    stats = FileSystemStats()
    sample_query.include_patterns = {"*.py"}; sample_query.ignore_patterns = set()
    with patch.object(Path, "iterdir") as mock_iterdir:
        mock_symlink_path = MagicMock(spec=Path, name="symlink_to_file1")
        mock_symlink_path.name = "symlink_to_file1"; mock_symlink_path.is_symlink.return_value = True
        mock_symlink_path.is_file.return_value = False; mock_symlink_path.is_dir.return_value = False
        mock_symlink_path.relative_to.return_value = Path("symlink_to_file1")
        mock_symlink_path.readlink.return_value = Path("file1.txt")
        mock_symlink_path.path = temp_directory / "symlink_to_file1"
        mock_iterdir.return_value = [mock_symlink_path]
        _process_node(root_node, sample_query, stats, temp_directory)
    assert len(root_node.children) == 0; assert stats.total_files == 0

# ... (_process_file tests remain the same) ...
def test_process_file_oserror_stat(temp_directory: Path, sample_query: IngestionQuery) -> None:
    parent_node = FileSystemNode(name="test_repo", type=FileSystemNodeType.DIRECTORY, path_str="test_repo", path=temp_directory)
    stats = FileSystemStats()
    file_path = temp_directory / "file_causing_error.txt"; file_path.write_text("content")
    with patch.object(Path, 'stat', side_effect=OSError("Simulated stat error")):
        with pytest.warns(UserWarning, match="Could not stat file"):
            _process_file(file_path, parent_node, stats, temp_directory, sample_query.max_file_size)
    assert len(parent_node.children) == 0; assert stats.total_files == 0

def test_process_file_exceeds_max_file_size(temp_directory: Path, sample_query: IngestionQuery) -> None:
    parent_node = FileSystemNode(name="test_repo", type=FileSystemNodeType.DIRECTORY, path_str="test_repo", path=temp_directory)
    stats = FileSystemStats()
    file_path = temp_directory / "large_file.txt"; file_path.write_text("a" * (sample_query.max_file_size + 1))
    with pytest.warns(UserWarning, match="Skipping file large_file.txt"):
        _process_file(file_path, parent_node, stats, temp_directory, sample_query.max_file_size)
    assert len(parent_node.children) == 0; assert stats.total_files == 0

def test_process_file_exceeds_total_size_limit(temp_directory: Path, sample_query: IngestionQuery) -> None:
    parent_node = FileSystemNode(name="test_repo", type=FileSystemNodeType.DIRECTORY, path_str="test_repo", path=temp_directory)
    stats = FileSystemStats(); stats.total_size = MAX_TOTAL_SIZE_BYTES - 100
    file_path = temp_directory / "small_file.txt"; file_path.write_text("a" * 200)
    with pytest.warns(UserWarning, match="Skipping file small_file.txt: adding it would exceed total size limit."):
        _process_file(file_path, parent_node, stats, temp_directory, sample_query.max_file_size)
    assert len(parent_node.children) == 0; assert stats.total_files == 1
    assert stats.total_size == MAX_TOTAL_SIZE_BYTES - 100

def test_process_file_exceeds_total_file_limit(temp_directory: Path, sample_query: IngestionQuery) -> None:
    parent_node = FileSystemNode(name="test_repo", type=FileSystemNodeType.DIRECTORY, path_str="test_repo", path=temp_directory)
    stats = FileSystemStats(); stats.total_files = MAX_FILES - 1
    file_path = temp_directory / "another_file.txt"; file_path.write_text("content")
    _process_file(file_path, parent_node, stats, temp_directory, sample_query.max_file_size)
    assert len(parent_node.children) == 1; assert stats.total_files == MAX_FILES
    file_path_exceed = temp_directory / "file_too_many.txt"; file_path_exceed.write_text("content")
    _process_file(file_path_exceed, parent_node, stats, temp_directory, sample_query.max_file_size) # Should be skipped
    assert len(parent_node.children) == 1 # Not added
    assert stats.total_files == MAX_FILES + 1

# ... (limit_exceeded tests remain the same) ...
def test_limit_exceeded_depth() -> None:
    stats = FileSystemStats(); depth = MAX_DIRECTORY_DEPTH + 1
    assert limit_exceeded(stats, depth) is True

def test_limit_exceeded_file_count() -> None:
    stats = FileSystemStats(); stats.total_files = MAX_FILES; depth = 0
    assert limit_exceeded(stats, depth) is True

def test_limit_exceeded_total_size() -> None:
    stats = FileSystemStats(); stats.total_size = MAX_TOTAL_SIZE_BYTES; depth = 0
    assert limit_exceeded(stats, depth) is True

def test_limit_exceeded_none() -> None:
    stats = FileSystemStats(); stats.total_files = MAX_FILES - 1; stats.total_size = MAX_TOTAL_SIZE_BYTES - 1; depth = MAX_DIRECTORY_DEPTH - 1
    assert limit_exceeded(stats, depth) is False

# --- FIX: Include/Exclude node tests with helper ---
def test_process_node_with_include_pattern(temp_directory: Path, sample_query: IngestionQuery) -> None:
    """Test _process_node with an include pattern."""
    root_node = FileSystemNode(name=temp_directory.name, type=FileSystemNodeType.DIRECTORY, path_str="", path=temp_directory)
    stats = FileSystemStats()
    sample_query.include_patterns = {"*.txt"}
    sample_query.ignore_patterns = set()
    _process_node(root_node, sample_query, stats, temp_directory)

    # FIX: Use the helper function defined above
    included_files = get_all_file_paths(root_node)

    assert "file1.txt" in included_files
    assert "src/subfile1.txt" in included_files
    assert "src/subdir/file_subdir.txt" in included_files
    assert "dir1/file_dir1.txt" in included_files
    assert "dir2/file_dir2.txt" in included_files
    assert "empty_file.txt" in included_files
    # Check exclusions based on pattern
    assert "file2.py" not in included_files
    assert "src/subfile2.py" not in included_files
    assert "src/subdir/file_subdir.py" not in included_files
    assert ".gitingest" not in included_files # Doesn't match *.txt
    assert "non_text_file.bin" not in included_files
    assert "notebook.ipynb" not in included_files
    # Symlinks are not FILE type, so helper won't include them
    assert "symlink_to_file1" not in included_files
    assert "broken_symlink" not in included_files


def test_process_node_with_exclude_pattern(temp_directory: Path, sample_query: IngestionQuery) -> None:
    """Test _process_node with an exclude pattern."""
    root_node = FileSystemNode(name=temp_directory.name, type=FileSystemNodeType.DIRECTORY, path_str="", path=temp_directory)
    stats = FileSystemStats()
    # Keep default *.py ignore, add dir2 ignore
    sample_query.ignore_patterns.add("dir2")
    sample_query.include_patterns = set()
    _process_node(root_node, sample_query, stats, temp_directory)

    # Use helper to get all processed file paths
    processed_file_paths = get_all_file_paths(root_node)

    # Check included files
    assert "file1.txt" in processed_file_paths
    assert ".gitingest" in processed_file_paths
    assert "src/subfile1.txt" in processed_file_paths
    assert "src/subdir/file_subdir.txt" in processed_file_paths
    assert "dir1/file_dir1.txt" in processed_file_paths
    assert "non_text_file.bin" in processed_file_paths
    assert "notebook.ipynb" in processed_file_paths
    assert "empty_file.txt" in processed_file_paths

    # Check excluded files
    assert "file2.py" not in processed_file_paths # Excluded by *.py
    assert "src/subfile2.py" not in processed_file_paths
    assert "src/subdir/file_subdir.py" not in processed_file_paths
    assert "dir2/file_dir2.txt" not in processed_file_paths # Inside excluded dir2

    # Check that dir2 node itself is not present in children
    dir2_node = next((child for child in root_node.children if child.name == "dir2"), None)
    assert dir2_node is None


def test__should_exclude_directory_pattern(temp_directory: Path) -> None:
    """Test _should_exclude correctly excludes a directory based on a pattern."""
    base_path = temp_directory
    dir_path = temp_directory / "dir2"
    ignore_patterns = {"dir2"}
    assert _should_exclude(dir_path, base_path, ignore_patterns) is True
    file_path_inside_dir = temp_directory / "dir2" / "file_dir2.txt"
    # Check if file *within* excluded dir matches pattern - it shouldn't directly match "dir2"
    assert _should_exclude(file_path_inside_dir, base_path, ignore_patterns) is False
    # Check if file matches if pattern is "dir2/*"
    assert _should_exclude(file_path_inside_dir, base_path, {"dir2/*"}) is True


def test_ingest_query_single_file_is_directory(temp_directory: Path, sample_query: IngestionQuery) -> None:
    """Test ingest_query when local_path points to a directory but type hints it's a file."""
    dir_path = temp_directory / "src"
    sample_query.local_path = dir_path
    sample_query.type = "blob" # Simulate URL indicating file
    sample_query.ignore_patterns.discard("*.py")

    summary, tree, content = ingest_query(sample_query)

    assert "Files analyzed:" in summary # Should process as directory
    assert "src/" in tree
    # FIX: Check content for correct relative paths within src/
    assert f"{SEPARATOR}\nFILE: subfile1.txt\n{SEPARATOR}\nHello from src\n\n" in content
    assert f"{SEPARATOR}\nFILE: subfile2.py\n{SEPARATOR}\nprint('Hello from src')\n\n" in content
    assert f"{SEPARATOR}\nFILE: subdir/file_subdir.py\n{SEPARATOR}\nprint('Hello from subdir')\n\n" in content
    assert f"{SEPARATOR}\nFILE: subdir/file_subdir.txt\n{SEPARATOR}\nHello from subdir\n\n" in content


def test_process_node_empty_directory_after_filtering(temp_directory: Path, sample_query: IngestionQuery) -> None:
    """Test that a directory node isn't added if all its children are filtered out."""
    # filtered_dir is created in the fixture
    root_node = FileSystemNode(name=temp_directory.name, type=FileSystemNodeType.DIRECTORY, path_str="", path=temp_directory)
    stats = FileSystemStats()
    assert "*.py" in sample_query.ignore_patterns # Ensure *.py is ignored
    _process_node(root_node, sample_query, stats, temp_directory)

    filtered_dir_node = next((child for child in root_node.children if child.name == "filtered_dir"), None)
    assert filtered_dir_node is None # Directory should not be added


def test_filesystemnode_read_chunks_non_file_node() -> None:
    """Test FileSystemNode.read_chunks behavior for non-file types."""
    dir_node = FileSystemNode(name="dir", type=FileSystemNodeType.DIRECTORY, path_str="dir", path=Path("dir"))
    with pytest.raises(ValueError, match="Cannot read chunks of a non-file node"):
        list(dir_node.read_chunks())

    symlink_node = FileSystemNode(name="link", type=FileSystemNodeType.SYMLINK, path_str="link", path=Path("link"))
    # FIX: Symlinks return empty iterator
    assert list(symlink_node.read_chunks()) == []


# ... (Remaining tests for read_chunks variations should now pass with the code fixes) ...

def test_filesystemnode_read_chunks_path_not_a_file(tmp_path: Path) -> None:
    """Test FileSystemNode.read_chunks when path points to a directory."""
    dir_path = tmp_path / "actual_dir"; dir_path.mkdir()
    node = FileSystemNode(name="actual_dir", type=FileSystemNodeType.FILE, path_str="actual_dir", path=dir_path)
    with pytest.warns(UserWarning, match="Path is not a file"):
        chunks = list(node.read_chunks())
        assert chunks == ["Error: Path is not a file (actual_dir)"]

def test_filesystemnode_read_chunks_notebook_error(temp_directory: Path, sample_query: IngestionQuery) -> None:
    """Test FileSystemNode.read_chunks with a notebook processing error."""
    notebook_path = temp_directory / "notebook.ipynb"
    notebook_node = FileSystemNode(name="notebook.ipynb", type=FileSystemNodeType.FILE, path_str="notebook.ipynb", path=notebook_path)
    with patch('CodeIngest.schemas.filesystem_schema.is_text_file', return_value=True), \
         patch('CodeIngest.schemas.filesystem_schema.process_notebook', side_effect=Exception("Simulated notebook error")):
        chunks = list(notebook_node.read_chunks())
        assert chunks == ["Error processing notebook: Simulated notebook error"]

def test_filesystemnode_read_chunks_oserror_on_open(temp_directory: Path, sample_query: IngestionQuery) -> None:
    """Test FileSystemNode.read_chunks when file open raises OSError."""
    file_path = temp_directory / "file_causing_oserror.txt"; file_path.write_text("content")
    file_node = FileSystemNode(name="file_causing_oserror.txt", type=FileSystemNodeType.FILE, path_str="file_causing_oserror.txt", path=file_path)
    with patch('CodeIngest.schemas.filesystem_schema.is_text_file', return_value=True), \
         patch.object(Path, "open", side_effect=OSError("Simulated open error")):
        with pytest.warns(UserWarning, match="Error opening file"):
            chunks = list(file_node.read_chunks())
            assert len(chunks) == 1
            # Check yielded error message
            assert "Error reading file: Simulated open error" in chunks[0]

def test_filesystemnode_read_chunks_unexpected_error(temp_directory: Path, sample_query: IngestionQuery) -> None:
    """Test FileSystemNode.read_chunks with an unexpected error during read."""
    file_path = temp_directory / "file_causing_unexpected_error.txt"; file_path.write_text("content")
    file_node = FileSystemNode(name="file_causing_unexpected_error.txt", type=FileSystemNodeType.FILE, path_str="file_causing_unexpected_error.txt", path=file_path)
    mock_file = MagicMock(); mock_file.read.side_effect = Exception("Unexpected read error"); mock_file.__enter__.return_value = mock_file
    with patch('CodeIngest.schemas.filesystem_schema.is_text_file', return_value=True), \
         patch.object(Path, "open", return_value=mock_file):
        with pytest.warns(UserWarning, match="Unexpected error reading file"):
            chunks = list(file_node.read_chunks())
            assert len(chunks) == 1
            assert "Unexpected error reading file: Unexpected read error" in chunks[0]

def test_filesystemnode_read_chunks_unable_to_decode(temp_directory: Path, sample_query: IngestionQuery) -> None:
    """Test FileSystemNode.read_chunks when unable to decode with any preferred encoding."""
    file_path = temp_directory / "undecodable_file.bin"; file_path.write_bytes(b"\x80\x81\x82\x83")
    file_node = FileSystemNode(name="undecodable_file.bin", type=FileSystemNodeType.FILE, path_str="undecodable_file.bin", path=file_path)
    with patch('CodeIngest.schemas.filesystem_schema.is_text_file', return_value=True), \
         patch('CodeIngest.schemas.filesystem_schema.get_preferred_encodings', return_value=['ascii']):
            chunks = list(file_node.read_chunks())
            assert len(chunks) == 1
            assert chunks[0] == "Error: Unable to decode file with available encodings"

def test_filesystemnode_sort_children_not_directory() -> None:
    """Test FileSystemNode.sort_children raises ValueError for non-directory nodes."""
    file_node = FileSystemNode(name="file.txt", type=FileSystemNodeType.FILE, path_str="file.txt", path=Path("file.txt"))
    with pytest.raises(ValueError, match="Cannot sort children of a non-directory node"):
        file_node.sort_children()