"""Tests for the ingestion utility functions."""

import pytest
import os
import platform
import locale # Import the locale module
from pathlib import Path
from unittest.mock import patch, MagicMock, call # Import MagicMock and call

from CodeIngest.utils.ingestion_utils import _should_include, _should_exclude
from CodeIngest.utils.file_utils import is_text_file, get_preferred_encodings

# Helper to create dummy paths for testing
@pytest.fixture
def base_path(tmp_path: Path) -> Path:
    """Create a base temporary directory."""
    p = tmp_path / "repo_root"
    p.mkdir()
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
    patterns_effective = {"docs", "docs/*"}
    assert _should_exclude(base_path / "docs", base_path, patterns_effective) is True
    assert _should_exclude(base_path / "docs" / "index.md", base_path, patterns_effective) is True
    assert _should_exclude(base_path / "src", base_path, patterns_effective) is False

def test_exclude_path_pattern(base_path: Path):
    patterns = {"src/module.py"}
    assert _should_exclude(base_path / "src" / "module.py", base_path, patterns) is True
    assert _should_exclude(base_path / "src", base_path, patterns) is False

def test_exclude_hidden_files(base_path: Path):
    patterns = {".*"}
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
    patterns = {"."}
    assert _should_exclude(base_path, base_path, patterns) is True

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
    patterns = {"docs"}
    assert _should_include(base_path / "docs", base_path, patterns) is True
    assert _should_include(base_path / "docs" / "index.md", base_path, patterns) is False
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
    """Test that _should_include returns False when the pattern set is empty."""
    patterns = set()
    # --- FIX: Assertion corrected based on updated logic ---
    assert _should_include(base_path / "README.md", base_path, patterns) is False
    # --- End FIX ---

# --- Tests for is_text_file ---
# ... (these tests remain the same) ...

def test_is_text_file_text(tmp_path: Path):
    text_file = tmp_path / "test.txt"; text_file.write_text("Text")
    assert is_text_file(text_file) is True

def test_is_text_file_empty(tmp_path: Path):
    empty_file = tmp_path / "empty.txt"; empty_file.touch()
    assert is_text_file(empty_file) is True

def test_is_text_file_binary_null_byte(tmp_path: Path):
    binary_file = tmp_path / "binary_null.bin"; binary_file.write_bytes(b'\x00\x01')
    assert is_text_file(binary_file) is False

def test_is_text_file_binary_ff_byte(tmp_path: Path):
    binary_file = tmp_path / "binary_ff.bin"; binary_file.write_bytes(b'\xfe\xff')
    assert is_text_file(binary_file) is False

def test_is_text_file_oserror_on_open_rb(tmp_path: Path):
    mock_path = MagicMock(spec=Path); mock_path.open.side_effect = OSError("Read error")
    assert is_text_file(mock_path) is False

def test_is_text_file_unicode_decode_error(tmp_path: Path):
    text_file = tmp_path / "utf8_file.txt"; text_file.write_text("你好", encoding="utf-8")
    with patch('CodeIngest.utils.file_utils.get_preferred_encodings', return_value=['ascii', 'utf-8']):
        assert is_text_file(text_file) is True

# --- Tests for get_preferred_encodings ---
# ... (these tests remain the same) ...

@patch('locale.getpreferredencoding', return_value='utf-8')
@patch('platform.system', return_value='Linux')
def test_get_preferred_encodings_linux(mock_system, mock_encoding):
    encodings = get_preferred_encodings()
    assert encodings[0] == 'utf-8'; assert 'latin-1' in encodings; assert 'cp1252' not in encodings

@patch('locale.getpreferredencoding', return_value='cp1252')
@patch('platform.system', return_value='Windows')
def test_get_preferred_encodings_windows(mock_system, mock_encoding):
    encodings = get_preferred_encodings()
    assert encodings[0] == 'cp1252'; assert 'utf-8' in encodings; assert 'cp1252' in encodings; assert 'iso-8859-1' in encodings

