# tests/test_zip_ingestion.py
"""Tests for ZIP file ingestion."""

import zipfile
import pytest
import shutil
from pathlib import Path
from CodeIngest.query_parsing import parse_query
from CodeIngest.ingestion import ingest_query
from CodeIngest.schemas import IngestionQuery

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
        assert "Files analyzed: 4" in result["summary_str"] # From original node.file_count
        assert result["num_files"] == 4 # Actual files in tree (no symlinks in this zip)

        assert isinstance(result["tree_data_with_embedded_content"], list)
        assert any(item['path_str'] == 'file1.txt' for item in result["tree_data_with_embedded_content"])
        assert any(item['path_str'] == 'file2.py' for item in result["tree_data_with_embedded_content"])
        assert any(item['path_str'] == 'subdir/sub_file.txt' for item in result["tree_data_with_embedded_content"])

        # Check concatenated content
        concatenated_content = result["concatenated_content_for_txt"]
        assert "FILE: file1.txt" in concatenated_content; assert "Hello Zip" in concatenated_content
        assert "FILE: file2.py" in concatenated_content; assert "print('Zip Hello')" in concatenated_content
        assert "FILE: subdir/sub_file.txt" in concatenated_content; assert "Hello from zip subdir" in concatenated_content

        # Check embedded content
        file1_node = next(item for item in result["tree_data_with_embedded_content"] if item["name"] == "file1.txt")
        assert file1_node["file_content"] == "Hello Zip"
        file2_node = next(item for item in result["tree_data_with_embedded_content"] if item["name"] == "file2.py")
        assert file2_node["file_content"] == "print('Zip Hello')"
        sub_file_node = next(item for item in result["tree_data_with_embedded_content"] if item["name"] == "sub_file.txt" and "subdir" in item["path_str"])
        assert sub_file_node["file_content"] == "Hello from zip subdir"

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

        assert isinstance(result["tree_data_with_embedded_content"], list)
        assert any(item['path_str'] == 'file.txt' for item in result["tree_data_with_embedded_content"])
        assert not any(item['path_str'] == 'file.log' for item in result["tree_data_with_embedded_content"])

        file_txt_node = next(item for item in result["tree_data_with_embedded_content"] if item["name"] == "file.txt")
        assert file_txt_node["file_content"] == "Include me"

        concatenated_content = result["concatenated_content_for_txt"]
        assert "FILE: file.txt" in concatenated_content
        assert "Include me" in concatenated_content
        assert "Exclude me" not in concatenated_content
        assert "file.log" not in concatenated_content # Check it's not even listed as a file
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