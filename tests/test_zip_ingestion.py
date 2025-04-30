"""
Tests specifically for the zip file ingestion functionality.
"""

import json
import os
import warnings
import zipfile
import shutil
from pathlib import Path
from typing import Set, Iterator
from unittest.mock import MagicMock, patch, mock_open

import pytest

from CodeIngest.config import (
    MAX_DIRECTORY_DEPTH,
    MAX_FILES,
    MAX_TOTAL_SIZE_BYTES,
    TMP_BASE_PATH
)
from CodeIngest.ingestion import ingest_query # Import main function
from CodeIngest.query_parsing import IngestionQuery, parse_query # Import necessary functions/classes
from CodeIngest.schemas import (
    FileSystemNode,
    FileSystemNodeType,
    FileSystemStats,
)
from CodeIngest.schemas.filesystem_schema import SEPARATOR
from CodeIngest.utils.ignore_patterns import DEFAULT_IGNORE_PATTERNS


# --- Fixtures ---

@pytest.fixture
def temp_directory_structure(tmp_path: Path) -> Path:
    """Creates a standard directory structure for testing."""
    test_dir = tmp_path / "test_repo_source"
    test_dir.mkdir()
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
    (test_dir / ".hiddenfile").write_text("Hidden file content")
    hidden_dir = test_dir / ".hiddendir"; hidden_dir.mkdir()
    (hidden_dir / "inside.txt").write_text("Hidden content")
    (test_dir / "non_text_file.bin").write_bytes(b"\x00\x01\x02\x03")
    notebook_content = {"cells": [{"cell_type": "code", "source": ["print('Hello Notebook')"]}]}
    with (test_dir / "notebook.ipynb").open("w") as f: json.dump(notebook_content, f)
    (test_dir / "empty_file.txt").touch()
    return test_dir

@pytest.fixture
def temp_zip_file(temp_directory_structure: Path, tmp_path: Path) -> Path:
    """Creates a zip file containing the temp_directory_structure."""
    zip_path = tmp_path / "test_repo.zip"
    source_dir = temp_directory_structure

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_path in source_dir.rglob('*'):
            arcname = file_path.relative_to(source_dir)
            zipf.write(file_path, arcname=arcname)
    return zip_path

@pytest.fixture
def sample_query() -> IngestionQuery:
    """Provides a default query object, assuming local source initially."""
    default_ignores = DEFAULT_IGNORE_PATTERNS.copy()
    default_ignores.add("*.py")
    return IngestionQuery(
        user_name=None, repo_name=None, url=None, subpath="/",
        local_path=Path("/tmp/placeholder").resolve(),
        slug="placeholder_slug",
        id="id", branch=None, commit=None, max_file_size=1_000_000,
        ignore_patterns=default_ignores, include_patterns=None,
        original_zip_path=None, temp_extract_path=None,
    )

# --- Zip Ingestion Tests ---

@pytest.mark.asyncio
async def test_ingest_query_zip_basic(temp_zip_file: Path, sample_query: IngestionQuery) -> None:
    """Test basic ingestion from a valid zip file."""
    source_path_str = str(temp_zip_file)
    query = await parse_query(source=source_path_str, max_file_size=sample_query.max_file_size, from_web=False)
    if query.ignore_patterns:
        query.ignore_patterns.discard("*.py") # Allow .py files for this test

    extracted_path = query.local_path

    try:
        summary, tree, content = ingest_query(query)

        assert f"Zip File: {str(temp_zip_file.resolve())}" in summary
        # --- FIX: Correct expected file count ---
        assert "Files analyzed: 13" in summary
        # --- End FIX ---
        assert f"{SEPARATOR}\nFILE: file1.txt\n{SEPARATOR}\nHello World\n\n" in content
        assert f"{SEPARATOR}\nFILE: file2.py\n{SEPARATOR}\nprint('Hello')\n\n" in content
        assert f"{SEPARATOR}\nFILE: .hiddendir/inside.txt\n{SEPARATOR}\nHidden content\n\n" in content
        assert "file1.txt" in tree
        assert ".hiddendir/" in tree
        assert "inside.txt" in tree

    finally:
        if query.temp_extract_path and query.temp_extract_path.exists():
            shutil.rmtree(query.temp_extract_path.parent, ignore_errors=True)


@pytest.mark.asyncio
async def test_ingest_query_zip_with_gitingest(tmp_path: Path, sample_query: IngestionQuery) -> None:
    """Test zip ingestion respects .gitingest file inside the zip."""
    source_dir = tmp_path / "zip_gitingest_src"
    source_dir.mkdir()
    (source_dir / "file.txt").write_text("Include me")
    (source_dir / "file.log").write_text("Exclude me")
    (source_dir / ".gitingest").write_text('[config]\nignore_patterns = ["*.log"]')

    zip_path = tmp_path / "repo_with_gitingest.zip"
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for file_path in source_dir.rglob('*'):
            arcname = file_path.relative_to(source_dir)
            zipf.write(file_path, arcname=arcname)

    query = await parse_query(source=str(zip_path), max_file_size=sample_query.max_file_size, from_web=False)
    query.ignore_patterns = set() # Start fresh, let apply_gitingest_file handle it

    try:
        summary, tree, content = ingest_query(query)

        assert f"Zip File: {str(zip_path.resolve())}" in summary
        assert "Files analyzed: 2" in summary # file.txt, .gitingest
        assert "file.txt" in tree
        assert ".gitingest" in tree
        assert "file.log" not in tree
        assert f"{SEPARATOR}\nFILE: file.txt\n{SEPARATOR}\nInclude me\n\n" in content
        assert f"{SEPARATOR}\nFILE: .gitingest\n{SEPARATOR}" in content
        assert "Exclude me" not in content

    finally:
        if query.temp_extract_path and query.temp_extract_path.exists():
            shutil.rmtree(query.temp_extract_path.parent, ignore_errors=True)


@pytest.mark.asyncio
async def test_ingest_query_zip_nonexistent(sample_query: IngestionQuery) -> None:
    """Test parse_query with a non-existent zip file path."""
    zip_path_str = "/nonexistent/path/to/archive.zip"
    with pytest.raises(ValueError, match=r"Local path not found: /nonexistent/path/to/archive.zip"):
        await parse_query(source=zip_path_str, max_file_size=sample_query.max_file_size, from_web=False)


@pytest.mark.asyncio
async def test_ingest_query_zip_invalid(tmp_path: Path, sample_query: IngestionQuery) -> None:
    """Test parse_query with an invalid (non-zip) file ending in .zip."""
    # --- FIX: Create an invalid file *with* a .zip extension ---
    invalid_zip_path = tmp_path / "invalid_archive.zip"
    invalid_zip_path.write_text("This is not actual zip content.")
    # --- End FIX ---
    # parse_query should now raise ValueError for invalid zip files
    with pytest.raises(ValueError, match=r"Specified path is not a valid zip file:"):
        await parse_query(source=str(invalid_zip_path), max_file_size=sample_query.max_file_size, from_web=False)

