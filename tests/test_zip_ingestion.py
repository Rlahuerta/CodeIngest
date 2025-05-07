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
    if query.ignore_patterns: query.ignore_patterns.discard("*.py")
    extracted_path = query.local_path
    try:
        summary, tree_data, content = ingest_query(query)
        assert f"Source: {temp_zip_file.stem}" in summary
        assert "Files analyzed: 4" in summary
        assert isinstance(tree_data, list)
        assert any(item['path_str'] == 'file1.txt' for item in tree_data)
        assert any(item['path_str'] == 'file2.py' for item in tree_data)
        assert any(item['path_str'] == 'subdir/sub_file.txt' for item in tree_data)
        assert "FILE: file1.txt" in content; assert "Hello Zip" in content
        assert "FILE: file2.py" in content; assert "print('Zip Hello')" in content
        assert "FILE: subdir/sub_file.txt" in content; assert "Hello from zip subdir" in content
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
    assert "*.log" in query.ignore_patterns
    try:
        summary, tree_data, content = ingest_query(query)
        assert f"Source: {zip_path.stem}" in summary; assert "Files analyzed: 2" in summary
        # ...
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