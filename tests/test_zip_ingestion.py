# tests/test_zip_ingestion.py
"""Tests for ZIP file ingestion."""

import zipfile
import pytest
import shutil
from pathlib import Path
from typing import Optional, Dict, Any # Added imports
from CodeIngest.query_parsing import parse_query
from CodeIngest.ingestion import ingest_query
from CodeIngest.schemas import IngestionQuery, FileSystemNodeType # Added FileSystemNodeType

# (Fixture sample_query remains the same)
@pytest.fixture
def sample_query() -> IngestionQuery:
    from CodeIngest.utils.ignore_patterns import DEFAULT_IGNORE_PATTERNS
    default_ignores = DEFAULT_IGNORE_PATTERNS.copy()
    if ".git" not in default_ignores: default_ignores.add(".git")
    return IngestionQuery(
        user_name=None, repo_name=None, url=None, subpath="/",
        local_path=Path("/tmp/placeholder"), slug="placeholder_slug", id="placeholder_id", branch=None,
        max_file_size=1_000_000, ignore_patterns=default_ignores, include_patterns=None,
        original_zip_path=None, temp_extract_path=None,
    )


@pytest.fixture
def temp_zip_file(tmp_path: Path) -> Path:
    source_dir = tmp_path / "zip_source"; source_dir.mkdir()
    (source_dir / "empty_file.txt").touch()
    (source_dir / "file1.txt").write_text("Hello Zip")
    (source_dir / "file2.py").write_text("print('Zip Hello')")
    sub_dir = source_dir / "subdir"; sub_dir.mkdir()
    (sub_dir / "sub_file.txt").write_text("Hello from zip subdir")
    zip_path = tmp_path / "test_repo.zip"
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for file_path in source_dir.rglob('*'):
            arcname = file_path.relative_to(source_dir); zipf.write(file_path, arcname=arcname)
    return zip_path


@pytest.mark.asyncio
async def test_ingest_query_zip_basic(temp_zip_file: Path, sample_query: IngestionQuery) -> None:
    """Test basic ingestion from a valid zip file."""
    source_path_str = str(temp_zip_file)
    query = await parse_query(source=source_path_str, max_file_size=sample_query.max_file_size, from_web=False)
    if query.ignore_patterns: query.ignore_patterns.discard("*.py") # Allow .py files
    # extracted_path = query.local_path # Not used directly in assertions after refactor
    try:
        result = ingest_query(query)
        assert f"Source: {temp_zip_file.stem}" in result["summary_str"]
        # Zip contains: empty_file.txt, file1.txt, file2.py, subdir/sub_file.txt = 4 files
        assert "Files analyzed: 4" in result["summary_str"]
        assert result["num_files"] == 4

        nested_tree_root = result["tree_data_with_embedded_content"]
        assert isinstance(nested_tree_root, dict)
        # Root name is the name of the temporary extraction directory
        assert nested_tree_root["name"] == query.local_path.name + "/"
        assert nested_tree_root["path"] == "."
        assert nested_tree_root["type"] == "DIRECTORY"

        # Use helper to find nodes
        file1_node_found = find_node_in_nested_tree(nested_tree_root, "file1.txt")
        assert file1_node_found is not None
        assert file1_node_found["file_content"] == "Hello Zip"

        file2_node_found = find_node_in_nested_tree(nested_tree_root, "file2.py")
        assert file2_node_found is not None
        assert file2_node_found["file_content"] == "print('Zip Hello')"

        subdir_file_node_found = find_node_in_nested_tree(nested_tree_root, "subdir/sub_file.txt")
        assert subdir_file_node_found is not None
        assert subdir_file_node_found["file_content"] == "Hello from zip subdir"

        empty_file_node_found = find_node_in_nested_tree(nested_tree_root, "empty_file.txt")
        assert empty_file_node_found is not None
        # Assuming empty file content is an empty string
        assert empty_file_node_found.get("file_content", "") == ""


        # Check concatenated content
        concatenated_content = result["concatenated_content_for_txt"]
        assert "FILE: file1.txt" in concatenated_content; assert "Hello Zip" in concatenated_content
        assert "FILE: file2.py" in concatenated_content; assert "print('Zip Hello')" in concatenated_content
        assert "FILE: subdir/sub_file.txt" in concatenated_content; assert "Hello from zip subdir" in concatenated_content

    finally:
        if query.temp_extract_path and query.temp_extract_path.exists():
             shutil.rmtree(query.temp_extract_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_ingest_query_zip_with_gitingest(tmp_path: Path, sample_query: IngestionQuery) -> None:
    source_dir = tmp_path / "zip_gitingest_src"; source_dir.mkdir()
    (source_dir / "file.txt").write_text("Include me"); (source_dir / "file.log").write_text("Exclude me")
    (source_dir / ".gitingest").write_text('[config]\nignore_patterns = ["*.log"]')
    zip_path = tmp_path / "repo_with_gitingest.zip"
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for file_path in source_dir.rglob('*'): arcname = file_path.relative_to(source_dir); zipf.write(file_path, arcname=arcname)
    query = await parse_query(source=str(zip_path), max_file_size=sample_query.max_file_size, from_web=False)
    assert "*.log" in query.ignore_patterns # Ensure .gitingest was processed by parse_query
    try:
        result = ingest_query(query)
        # Zip contains: file.txt, file.log, .gitingest. .gitingest itself is usually not counted or included.
        # .log is ignored. So, 1 file (file.txt) should be in the tree.
        assert f"Source: {zip_path.stem}" in result["summary_str"]
        assert "Files analyzed: 2" in result["summary_str"] # file.txt, file.log (before ignore by ingest_query)
                                                            # or 3 if .gitingest is counted by initial scan.
                                                            # This depends on how parse_query sets up the initial FileSystemNode
                                                            # For now, let's assume summary reflects the files found by ingest_query before its internal filtering.
                                                            # The important part is num_files.
        # Expecting file.txt and .gitingest
        assert result["num_files"] == 2

        nested_tree_root = result["tree_data_with_embedded_content"]
        assert isinstance(nested_tree_root, dict)
        assert nested_tree_root["name"] == query.local_path.name + "/"
        assert nested_tree_root["path"] == "."
        assert nested_tree_root["type"] == "DIRECTORY"

        file_txt_node_found = find_node_in_nested_tree(nested_tree_root, "file.txt")
        assert file_txt_node_found is not None
        assert file_txt_node_found["file_content"] == "Include me"

        gitingest_node_found = find_node_in_nested_tree(nested_tree_root, ".gitingest")
        assert gitingest_node_found is not None
        assert gitingest_node_found["file_content"] == '[config]\nignore_patterns = ["*.log"]'

        assert find_node_in_nested_tree(nested_tree_root, "file.log") is None

        concatenated_content = result["concatenated_content_for_txt"]
        assert "FILE: file.txt" in concatenated_content
        assert "Include me" in concatenated_content
        assert "FILE: .gitingest" in concatenated_content
        assert '[config]\nignore_patterns = ["*.log"]' in concatenated_content
        assert "Exclude me" not in concatenated_content
        assert "file.log" not in concatenated_content
    finally:
        if query.temp_extract_path and query.temp_extract_path.exists(): shutil.rmtree(query.temp_extract_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_ingest_query_zip_nonexistent(sample_query: IngestionQuery) -> None:
    """Test parse_query fails correctly for a non-existent zip path."""
    non_existent_zip = "/path/to/non_existent.zip"
    with pytest.raises(ValueError, match="Local path not found"):
        await parse_query(source=non_existent_zip, max_file_size=sample_query.max_file_size, from_web=False)


@pytest.mark.asyncio
async def test_ingest_query_zip_invalid(tmp_path: Path, sample_query: IngestionQuery) -> None:
    """Test parse_query fails correctly for an invalid zip file."""
    invalid_zip_path = tmp_path / "corrupt.zip"
    invalid_zip_path.write_text("This is not a valid zip archive content.")
    with pytest.raises(zipfile.BadZipFile):
        await parse_query(source=str(invalid_zip_path), max_file_size=sample_query.max_file_size, from_web=False)

# Helper function to find a node in the new nested tree structure
def find_node_in_nested_tree(node: Optional[Dict[str, Any]], target_path: str) -> Optional[Dict[str, Any]]:
    if not node:
        return None
    if node.get("path") == target_path:
        return node

    if node.get("type") == "DIRECTORY" and "children" in node and isinstance(node["children"], list):
        for child in node["children"]:
            found = find_node_in_nested_tree(child, target_path)
            if found:
                return found
    return None