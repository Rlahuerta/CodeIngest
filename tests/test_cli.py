# tests/test_cli.py
"""Tests for the CodeIngest cli."""

import os
import pytest
from pathlib import Path
from click.testing import CliRunner

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

