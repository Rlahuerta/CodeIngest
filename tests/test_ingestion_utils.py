"""Tests for the ingestion utility functions."""

import pytest
import os
import platform
import locale # Import the locale module
from pathlib import Path
from unittest.mock import patch, MagicMock, call # Import MagicMock and call

from CodeIngest.utils.ingestion_utils import _should_include, _should_exclude
from CodeIngest.utils.file_utils import is_text_file, get_preferred_encodings # Import functions to test

# Helper to create dummy paths for testing
@pytest.fixture
def base_path(tmp_path: Path) -> Path:
    """Create a base temporary directory."""
    p = tmp_path / "repo_root"
    p.mkdir()
    # Create some structure
    (p / "src").mkdir()
    (p / "src" / "module.py").touch()
    (p / "docs").mkdir()
    (p / "docs" / "index.md").touch()
    (p / "README.md").touch()
    (p / ".hiddenfile").touch()
    (p / ".hiddendir").mkdir()
    (p / ".hiddendir" / "inside.txt").touch()
    return p

# --- Tests for _should_exclude ---

def test_exclude_exact_match(base_path: Path):
    patterns = {"README.md"}
    assert _should_exclude(base_path / "README.md", base_path, patterns) is True
    assert _should_exclude(base_path / "src" / "module.py", base_path, patterns) is False


def test_exclude_wildcard_filename(base_path: Path):
    patterns = {"*.md"}
    assert _should_exclude(base_path / "README.md", base_path, patterns) is True
    assert _should_exclude(base_path / "docs" / "index.md", base_path, patterns) is True
    assert _should_exclude(base_path / "src" / "module.py", base_path, patterns) is False


def test_exclude_directory_pattern(base_path: Path):
    patterns = {"docs/"} # Note: patterns usually don't need trailing slash with fnmatch logic
    # Our current logic checks filename OR path, so "docs/" won't match "docs" filename.
    # To exclude a dir, use "docs" or "docs/*"
    patterns_effective = {"docs", "docs/*"}
    assert _should_exclude(base_path / "docs", base_path, patterns_effective) is True
    assert _should_exclude(base_path / "docs" / "index.md", base_path, patterns_effective) is True
    assert _should_exclude(base_path / "src", base_path, patterns_effective) is False


def test_exclude_path_pattern(base_path: Path):
    patterns = {"src/module.py"}
    assert _should_exclude(base_path / "src" / "module.py", base_path, patterns) is True
    assert _should_exclude(base_path / "src", base_path, patterns) is False


def test_exclude_hidden_files(base_path: Path):
    patterns = {".*"} # Exclude all hidden files/dirs starting with dot
    assert _should_exclude(base_path / ".hiddenfile", base_path, patterns) is True
    assert _should_exclude(base_path / ".hiddendir", base_path, patterns) is True
    assert _should_exclude(base_path / ".hiddendir" / "inside.txt", base_path, patterns) is True
    assert _should_exclude(base_path / "README.md", base_path, patterns) is False


def test_exclude_no_match(base_path: Path):
    patterns = {"build/", "*.log"}
    assert _should_exclude(base_path / "README.md", base_path, patterns) is False
    assert _should_exclude(base_path / "src" / "module.py", base_path, patterns) is False


def test_exclude_empty_patterns(base_path: Path):
    patterns = set()
    assert _should_exclude(base_path / "README.md", base_path, patterns) is False


def test_exclude_pattern_with_base_path_itself(base_path: Path):
    # This case is tricky, relative path of base_path to itself is '.'
    patterns = {"."}
    assert _should_exclude(base_path, base_path, patterns) is True # Matches '.' relative path

# --- Tests for _should_include ---

def test_include_exact_match(base_path: Path):
    patterns = {"README.md"}
    assert _should_include(base_path / "README.md", base_path, patterns) is True
    assert _should_include(base_path / "src" / "module.py", base_path, patterns) is False


def test_include_wildcard_filename(base_path: Path):
    patterns = {"*.py"}
    assert _should_include(base_path / "src" / "module.py", base_path, patterns) is True
    assert _should_include(base_path / "README.md", base_path, patterns) is False


def test_include_directory_pattern(base_path: Path):
    # Include patterns usually target files, but can match directory names
    patterns = {"docs"}
    assert _should_include(base_path / "docs", base_path, patterns) is True # Matches dir name
    # It will NOT automatically include files within unless they also match
    assert _should_include(base_path / "docs" / "index.md", base_path, patterns) is False
    # To include all files in docs, use "docs/*"
    patterns_all_in_docs = {"docs/*"}
    assert _should_include(base_path / "docs" / "index.md", base_path, patterns_all_in_docs) is True


def test_include_path_pattern(base_path: Path):
    patterns = {"src/module.py"}
    assert _should_include(base_path / "src" / "module.py", base_path, patterns) is True
    assert _should_include(base_path / "src", base_path, patterns) is False


def test_include_no_match(base_path: Path):
    patterns = {"*.java", "build/"}
    assert _should_include(base_path / "README.md", base_path, patterns) is False
    assert _should_include(base_path / "src" / "module.py", base_path, patterns) is False


def test_include_empty_patterns(base_path: Path):
    # If include patterns are empty, _should_include should likely not be called,
    # but if it were, it should return False. The calling logic handles this.
    patterns = set()
    assert _should_include(base_path / "README.md", base_path, patterns) is False

# --- Tests for is_text_file ---

def test_is_text_file_text(tmp_path: Path):
    """Test is_text_file with a standard text file."""
    text_file = tmp_path / "test.txt"
    text_file.write_text("This is a text file.")
    assert is_text_file(text_file) is True


def test_is_text_file_empty(tmp_path: Path):
    """Test is_text_file with an empty file."""
    empty_file = tmp_path / "empty.txt"
    empty_file.touch()
    assert is_text_file(empty_file) is True


def test_is_text_file_binary_null_byte(tmp_path: Path):
    """Test is_text_file with a binary file containing a null byte."""
    binary_file = tmp_path / "binary_null.bin"
    binary_file.write_bytes(b'\x00\x01\x02\x03')
    assert is_text_file(binary_file) is False


def test_is_text_file_binary_ff_byte(tmp_path: Path):
    """Test is_text_file with a binary file containing a 0xff byte."""
    binary_file = tmp_path / "binary_ff.bin"
    binary_file.write_bytes(b'\xfe\xff\xfd')
    assert is_text_file(binary_file) is False


def test_is_text_file_oserror_on_open_rb(tmp_path: Path):
    """Test is_text_file when opening the file for binary read raises OSError."""
    mock_path = MagicMock(spec=Path)
    mock_path.open.side_effect = OSError("Simulated OS error on binary read")
    assert is_text_file(mock_path) is False


def test_is_text_file_unicode_decode_error(tmp_path: Path):
    """Test is_text_file when decoding with preferred encoding fails, but fallback succeeds."""
    text_file = tmp_path / "utf8_file.txt"
    # Content that might cause issues with some non-UTF8 decoders
    text_file.write_text("你好世界", encoding="utf-8")

    # Mock get_preferred_encodings to put a failing encoding first
    with patch('CodeIngest.utils.file_utils.get_preferred_encodings', return_value=['ascii', 'utf-8']):
        # is_text_file should try 'ascii', fail with UnicodeDecodeError, then try 'utf-8' and succeed.
        assert is_text_file(text_file) is True


@patch('locale.getpreferredencoding', return_value='utf-8')
@patch('platform.system', return_value='Linux')
def test_get_preferred_encodings_linux(mock_system, mock_encoding):
    """Test get_preferred_encodings on a simulated Linux system."""
    encodings = get_preferred_encodings()
    assert encodings[0] == 'utf-8'
    assert 'utf-16' in encodings
    # Correct the assertion to expect 'latin-1'
    assert 'latin-1' in encodings
    assert 'cp1252' not in encodings # Windows-specific encoding


@patch('locale.getpreferredencoding', return_value='cp1252')
@patch('platform.system', return_value='Windows')
def test_get_preferred_encodings_windows(mock_system, mock_encoding):
    """Test get_preferred_encodings on a simulated Windows system."""
    encodings = get_preferred_encodings()
    assert encodings[0] == 'cp1252'
    assert 'utf-8' in encodings
    assert 'cp1252' in encodings # Windows-specific encoding
    assert 'iso-8859-1' in encodings # Another Windows-specific encoding
