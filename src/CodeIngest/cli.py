"""Command-line interface for the CodeIngest package."""

# pylint: disable=no-value-for-parameter

import asyncio
from typing import Optional, Tuple
from pathlib import Path # Import Path

import click

from CodeIngest.config import MAX_FILE_SIZE, OUTPUT_FILE_NAME
from CodeIngest.entrypoint import ingest_async


@click.command()
@click.argument("source", type=str, default=".")
@click.option("--output", "-o", default=None, help="Output file path (default: <slug>.txt in current directory)")
@click.option("--max-size", "-s", default=MAX_FILE_SIZE, help="Maximum file size to process in bytes")
@click.option("--exclude-pattern", "-e", multiple=True, help="Patterns to exclude")
@click.option("--include-pattern", "-i", multiple=True, help="Patterns to include")
@click.option("--branch", "-b", default=None, help="Branch/Tag/Commit to checkout (URL source only)")
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

    Analyzes a directory, Git repository URL, or local .zip file
    and creates a text dump of its contents.

    SOURCE: Path to the local directory, .zip file, or a remote Git URL.
            Defaults to the current directory ('.').
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
    Analyze a directory, repository URL, or zip file and create a text dump.

    This command analyzes the contents of a specified source (directory, URL, or .zip file),
    applies custom include and exclude patterns, and generates a text summary of the
    analysis which is then written to an output file.

    Parameters
    ----------
    source : str
        The source directory, repository URL, or .zip file path to analyze.
    output : str, optional
        The path where the output file will be written. If None or equal to
        OUTPUT_FILE_NAME ('digest.txt'), uses the source slug
        (e.g., repo_name.txt, directory_name.txt, zip_name.txt)
        in the current directory. Otherwise, uses the specified path.
    max_size : int
        The maximum file size to process, in bytes. Files larger than this size will be ignored.
    exclude_pattern : Tuple[str, ...]
        A tuple of patterns to exclude during the analysis. Files matching these patterns will be ignored.
    include_pattern : Tuple[str, ...]
        A tuple of patterns to include during the analysis. Only files matching these patterns will be processed.
    branch : str, optional
        The branch, tag, or commit hash to checkout (optional, only applies to URL sources).

    Raises
    ------
    Abort
        If there is an error during the execution of the command.
"""
    try:
        # Combine default and custom ignore patterns
        exclude_patterns_set = set(exclude_pattern)
        include_patterns_set = set(include_pattern)

        # Let ingest_async handle default output naming based on slug
        # Pass output if provided, else pass the default sentinel name which ingest_async handles
        summary, _, _ = await ingest_async(
            source,
            max_size,
            include_patterns_set,
            exclude_patterns_set,
            branch,
            output=output if output else OUTPUT_FILE_NAME
        )

        # Output message is now printed by ingest_async if file is written
        click.echo("\nSummary:")
        click.echo(summary)

    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        # Optionally print traceback for debugging CLI errors locally
        # import traceback
        # traceback.print_exc()
        raise click.Abort()


if __name__ == "__main__":
    main()
