# src/CodeIngest/cli.py
"""Command-line interface for the Gitingest package."""

# pylint: disable=no-value-for-parameter

import asyncio
import os # <--- ENSURED IMPORT IS PRESENT
from pathlib import Path
from typing import Optional, Tuple

import click

from CodeIngest.config import MAX_FILE_SIZE, OUTPUT_FILE_NAME
from CodeIngest.entrypoint import ingest_async


@click.command()
@click.argument("source", type=str, default=".")
@click.option("--output", "-o", default=None, help="Output file path (default: <repo_name>.txt in current directory)")
@click.option("--max-size", "-s", default=MAX_FILE_SIZE, help="Maximum file size to process in bytes")
@click.option("--exclude-pattern", "-e", multiple=True, help="Patterns to exclude")
@click.option("--include-pattern", "-i", multiple=True, help="Patterns to include")
@click.option("--branch", "-b", default=None, help="Branch to clone and ingest")
def main(
    source: str,
    output: Optional[str],
    max_size: int,
    exclude_pattern: Tuple[str, ...],
    include_pattern: Tuple[str, ...],
    branch: Optional[str],
):
    """
     Main entry point for the CLI.
     # ... (rest of docstring)
    """
    asyncio.run(_async_main(source, output, max_size, exclude_pattern, include_pattern, branch))


async def _async_main(
    source: str,
    output: Optional[str],
    max_size: int,
    exclude_pattern: Tuple[str, ...],
    include_pattern: Tuple[str, ...],
    branch: Optional[str],
) -> None:
    """
    Analyze a directory or repository and create a text dump of its contents.
     # ... (rest of docstring)
    """
    try:
        exclude_patterns_set = set(exclude_pattern)
        include_patterns_set = set(include_pattern)

        # Determine output filename if not provided
        if not output:
            output_filename_candidate = OUTPUT_FILE_NAME # Default fallback
            name_part = ""

            # If source is current directory, use its name
            if source == ".":
                name_part = Path(".").resolve().name
            # Else, if it's a path or URL, extract the last part
            elif "/" in source or "\\" in source:
                name_part = source.split('/')[-1].split('\\')[-1]
                if name_part.endswith(".git"):
                    name_part = name_part[:-4]

            # If a valid name_part was derived (not empty or just "."), sanitize and use it
            if name_part and name_part != ".":
                sanitized_name = "".join(c if c.isalnum() or c in ['_', '.', '-'] else '_' for c in name_part)
                if branch:
                    sanitized_branch = "".join(c if c.isalnum() or c in ['_', '.', '-'] else '_' for c in branch)
                    output_filename_candidate = f"{sanitized_name}_{sanitized_branch}.txt"
                else:
                    output_filename_candidate = f"{sanitized_name}.txt"

            output = output_filename_candidate


        # Corrected unpacking: ingest_async now returns 4 values
        summary, _, _, _ = await ingest_async(
            source,
            max_file_size=max_size,
            include_patterns=include_patterns_set,
            exclude_patterns=exclude_patterns_set,
            branch=branch,
            output=output
        )

        click.echo(f"Analysis complete! Output written to: {output}")
        click.echo("\nSummary:")
        click.echo(summary)

    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        # Potentially print traceback for debugging if needed
        # import traceback
        # click.echo(traceback.format_exc(), err=True)
        raise click.Abort()


if __name__ == "__main__":
    main()