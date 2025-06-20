# tests/test_cli.py
"""Tests for the CodeIngest cli."""

import os
import json # Added import
import pytest
from pathlib import Path
from click.testing import CliRunner
from typing import Optional, Dict, Any # Added typing imports

from CodeIngest.cli import main
from CodeIngest.config import MAX_FILE_SIZE, OUTPUT_FILE_NAME


def test_cli_with_default_options():
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("dummy_file_for_cli.txt").touch() # Create a file to ingest
        result = runner.invoke(main, ["."])

        print(f"CLI Output (default):\n{result.output}")
        assert result.exit_code == 0, f"CLI exited with error: {result.output}"

        # Check for essential output parts
        assert "Summary:" in result.output
        assert "Files analyzed:" in result.output
        assert "Output written to:" in result.output # Check the message is present

        # Find the output filename and check existence
        output_line = next((line for line in result.output.splitlines() if "Output written to:" in line), None)
        assert output_line is not None, "'Output written to:' message not found in CLI output."
        try:
            output_filename = output_line.split(":", 1)[1].strip()
            assert Path(output_filename).exists(), f"Expected output file '{output_filename}' was not created."
            # Cleanup
            if Path(output_filename).exists():
                 os.remove(output_filename)
        except IndexError:
            pytest.fail(f"Could not parse output filename from line: {output_line}")


def test_cli_with_options():
    runner = CliRunner()
    output_filename = "custom_cli_digest.txt" # Use a distinct name

    with runner.isolated_filesystem():
        # Create structure matching include/exclude
        Path("src").mkdir()
        Path("src/included.py").write_text("print('included')")
        Path("tests").mkdir()
        Path("tests/excluded_test.py").write_text("print('excluded')")
        Path("root_excluded.log").touch()

        result = runner.invoke(
            main,
            [
                ".",
                "--output", output_filename,
                "--max-size", str(MAX_FILE_SIZE),
                "--exclude-pattern", "tests/",
                "--exclude-pattern", "*.log", # Add another exclude
                "--include-pattern", "src/", # Include only src dir content
            ],
        )
        print(f"CLI Output (with options):\n{result.output}")
        assert result.exit_code == 0, f"CLI exited with error: {result.output}"

        assert "Summary:" in result.output
        # Should analyze the file in src/
        assert "Files analyzed: 1" in result.output or "Files analyzed: 2" in result.output # Allow for dir node count variation
        assert f"Output written to: {output_filename}" in result.output
        assert os.path.exists(output_filename), f"Output file '{output_filename}' was not created."

        # Check content reflects filtering
        with open(output_filename, "r", encoding='utf-8') as f:
            content = f.read()
            assert "src/included.py" in content
            assert "tests/excluded_test.py" not in content
            assert "root_excluded.log" not in content

        os.remove(output_filename)


def test_cli_json_output():
    runner = CliRunner()
    output_filename = "data.json"

    with runner.isolated_filesystem() as fs:
        fs_path = Path(fs)
        source_dir = fs_path / "test_repo"
        source_dir.mkdir()
        (source_dir / "sample.py").write_text("print('hello')")

        result = runner.invoke(
            main,
            [str(source_dir), "--format", "json", "-o", output_filename] # Output to current dir (fs)
        )

        assert result.exit_code == 0, f"CLI exited with error: {result.output}"

        json_output_path = fs_path / output_filename
        assert json_output_path.exists(), "JSON output file was not created."

        with open(json_output_path, "r", encoding='utf-8') as f:
            content_str = f.read()
            try:
                data = json.loads(content_str)
            except json.JSONDecodeError as e:
                pytest.fail(f"Failed to decode JSON output: {e}\nContent:\n{content_str}")

        assert "summary" in data
        assert "metadata" in data
        assert "tree" in data
        assert "query" in data
        assert "content" not in data # Verify top-level 'content' is removed

        # Metadata checks
        metadata = data["metadata"]
        assert "repository_url" in metadata # Will be None for local paths
        assert metadata.get("repository_url") is None # Explicitly check for None for local path
        assert "branch" in metadata
        assert metadata.get("branch") is None # Explicitly check for None as not a git repo
        assert "commit" in metadata
        assert metadata.get("commit") is None # Explicitly check for None
        assert "number_of_tokens" in metadata and isinstance(metadata["number_of_tokens"], int) and metadata["number_of_tokens"] >= 0
        assert "number_of_files" in metadata and metadata["number_of_files"] == 1
        assert "directory_structure_text" in metadata and isinstance(metadata["directory_structure_text"], str)
        assert source_dir.name in metadata["directory_structure_text"] # e.g. "test_repo"
        assert "sample.py" in metadata["directory_structure_text"]

        # Tree checks
        assert isinstance(data["tree"], dict) # Changed from list to dict
        assert data["tree"] is not None

        # Find node using helper. data["tree"] is the root node.
        # sample.py is a direct child of source_dir (test_repo)
        # Its path in the nested tree will be "sample.py" relative to the root "."
        sample_py_node = find_node_in_nested_tree(data["tree"], "sample.py")
        assert sample_py_node is not None, "sample.py node not found in tree"
        assert sample_py_node["type"] == "FILE"
        assert "file_content" in sample_py_node
        assert sample_py_node["file_content"] == "print('hello')"
        # 'path' key is already verified by find_node_in_nested_tree matching "sample.py"

        # Query checks
        assert data["query"]["local_path"] == str(source_dir)
        # assert data["query"]["output_format"] == "json" # output_format is not in IngestionQuery schema

        # Check console output
        # The output path in the message should be the one specified by -o
        assert f"Output written to: {output_filename}" in result.output
        assert "Summary:" not in result.output # Summary should be in the file, not console for JSON file output


def test_cli_json_default_output_filename():
    runner = CliRunner()
    with runner.isolated_filesystem() as fs:
        fs_path = Path(fs)
        source_dir_name = "my_project"
        source_dir = fs_path / source_dir_name
        source_dir.mkdir()
        (source_dir / "main.rs").write_text("fn main() {}")

        # Run without -o, but with --format json
        result = runner.invoke(main, [str(source_dir), "--format", "json"])

        assert result.exit_code == 0, f"CLI exited with error: {result.output}"

        output_line = next((line for line in result.output.splitlines() if "Output written to:" in line), None)
        assert output_line is not None, "'Output written to:' message not found."

        try:
            # The filename printed by CLI is just the name, not the full path to isolated_filesystem
            output_filename_str = output_line.split(":", 1)[1].strip()
        except IndexError:
            pytest.fail(f"Could not parse output filename from line: {output_line}")

        expected_default_filename = f"{source_dir_name}.json"
        assert output_filename_str == expected_default_filename, \
            f"Expected default filename '{expected_default_filename}', got '{output_filename_str}'"

        # The file is created in the CWD of the isolated_filesystem
        default_json_output_path = fs_path / expected_default_filename
        assert default_json_output_path.exists(), \
            f"Default JSON output file '{default_json_output_path}' was not created."

        with open(default_json_output_path, "r", encoding='utf-8') as f:
            try:
                data = json.loads(f.read())
            except json.JSONDecodeError:
                pytest.fail("Failed to decode JSON from default output file.")

        assert "summary" in data
        assert "metadata" in data
        assert "tree" in data
        assert "query" in data
        assert "content" not in data

        # Metadata checks
        metadata = data["metadata"]
        assert "repository_url" in metadata
        assert metadata.get("repository_url") is None
        assert "number_of_tokens" in metadata and isinstance(metadata["number_of_tokens"], int) and metadata["number_of_tokens"] >= 0
        assert "number_of_files" in metadata and metadata["number_of_files"] == 1
        assert "directory_structure_text" in metadata and isinstance(metadata["directory_structure_text"], str)
        assert source_dir_name in metadata["directory_structure_text"] # e.g. "my_project"
        assert "main.rs" in metadata["directory_structure_text"]

        # Tree checks
        assert isinstance(data["tree"], dict) # Changed from list to dict
        assert data["tree"] is not None

        # Find node using helper. data["tree"] is the root node.
        # main.rs is a direct child of source_dir (my_project)
        # Its path in the nested tree will be "main.rs" relative to the root "."
        main_rs_node = find_node_in_nested_tree(data["tree"], "main.rs")
        assert main_rs_node is not None, "main.rs node not found in tree"
        assert main_rs_node["type"] == "FILE"
        assert "file_content" in main_rs_node
        assert main_rs_node["file_content"] == "fn main() {}"
        # 'path' key is already verified by find_node_in_nested_tree matching "main.rs"

        # Query checks
        assert data["query"]["local_path"] == str(source_dir)
        # assert data["query"]["output_format"] == "json" # output_format is not in IngestionQuery schema

# Helper function to find a node in the new nested tree structure
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
