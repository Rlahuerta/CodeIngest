"""Tests for the ingestion utility functions."""

from pathlib import Path
import pytest

from CodeIngest.utils.ingestion_utils import _should_include, _should_exclude

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
    assert _should_exclude(base_path / "src", base_path, patterns) is False # Dir itself doesn't match

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

