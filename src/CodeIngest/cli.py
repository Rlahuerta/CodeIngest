# src/CodeIngest/cli.py
"""Command-line interface for the Gitingest package."""

# pylint: disable=no-value-for-parameter

import asyncio
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
     Main entry point for the CLI. This function is called when the CLI is run as a script.

    It calls the async main function to run the command.

    Parameters
    ----------
    source : str
        The source directory or repository to analyze.
    output : str, optional
        The path where the output file will be written. If not specified, the output will be written
        to a file named `<repo_name>.txt` in the current directory.
    max_size : int
        The maximum file size to process, in bytes. Files larger than this size will be ignored.
    exclude_pattern : Tuple[str, ...]
        A tuple of patterns to exclude during the analysis. Files matching these patterns will be ignored.
    include_pattern : Tuple[str, ...]
        A tuple of patterns to include during the analysis. Only files matching these patterns will be processed.
    branch : str, optional
        The branch to clone (optional).
    """
    # Main entry point for the CLI. This function is called when the CLI is run as a script.
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

    This command analyzes the contents of a specified source directory or repository, applies custom include and
    exclude patterns, and generates a text summary of the analysis which is then written to an output file.

    Parameters
    ----------
    source : str
        The source directory or repository to analyze.
    output : str, optional
        The path where the output file will be written. If not specified, the output will be written
        to a file named `<repo_name>.txt` in the current directory.
    max_size : int
        The maximum file size to process, in bytes. Files larger than this size will be ignored.
    exclude_pattern : Tuple[str, ...]
        A tuple of patterns to exclude during the analysis. Files matching these patterns will be ignored.
    include_pattern : Tuple[str, ...]
        A tuple of patterns to include during the analysis. Only files matching these patterns will be processed.
    branch : str, optional
        The branch to clone (optional).

    Raises
    ------
    Abort
        If there is an error during the execution of the command, this exception is raised to abort the process.
    """
    try:
        # Combine default and custom ignore patterns
        exclude_patterns_set = set(exclude_pattern)
        include_patterns_set = set(include_pattern)

        if not output:
            # Try to generate a more specific output filename if it's a remote repo
            # This is a simplified version; a more robust way would be to parse the source
            # similar to how parse_query does, but that adds complexity here.
            # For CLI, a generic name or user-provided one is often sufficient.
            if "github.com" in source or "gitlab.com" in source or "bitbucket.org" in source:
                try:
                    repo_name_part = source.split('/')[-1]
                    if repo_name_part.endswith(".git"):
                        repo_name_part = repo_name_part[:-4]
                    
                    if branch:
                        branch_part = branch.replace('/', '_') # Sanitize slashes in branch names
                        output_filename_candidate = f"{repo_name_part}_{branch_part}.txt"
                    else:
                        output_filename_candidate = f"{repo_name_part}.txt"
                    
                    # Basic sanitization for the generated filename
                    output_filename_candidate = "".join(c if c.isalnum() or c in ['_', '.', '-'] else '_' for c in output_filename_candidate)
                    output = output_filename_candidate if output_filename_candidate else OUTPUT_FILE_NAME

                except IndexError:
                    output = OUTPUT_FILE_NAME # Fallback
            else: # Local path
                path_name = source.strip("./").replace(r"[\/\\]", "_") # Basic sanitization
                output_filename_candidate = f"{path_name}.txt" if path_name and path_name != "." else OUTPUT_FILE_NAME
                output = output_filename_candidate if output_filename_candidate else OUTPUT_FILE_NAME

        # Corrected unpacking: ingest_async now returns 4 values
        summary, _, _, _ = await ingest_async(
            source, 
            max_file_size=max_size,  # Ensure max_size is passed correctly
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
        raise click.Abort()


if __name__ == "__main__":
    main()