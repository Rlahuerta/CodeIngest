# src/CodeIngest/cli.py
"""Command-line interface for the Gitingest package."""

# pylint: disable=no-value-for-parameter

import asyncio
import json # Added import
import os # <--- ENSURED IMPORT IS PRESENT
from pathlib import Path
from typing import Optional, Tuple

import click

from CodeIngest.config import MAX_FILE_SIZE, OUTPUT_FILE_NAME
from CodeIngest.entrypoint import ingest_async


@click.command()
@click.argument("source", type=str, default=".")
@click.option("--output", "-o", default=None, help="Output file path (default: <repo_name>.<format_extension> in current directory)")
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(['txt', 'json'], case_sensitive=False),
    default='txt',
    show_default=True,
    help="Output format."
)
@click.option("--max-size", "-s", default=MAX_FILE_SIZE, help="Maximum file size to process in bytes")
@click.option("--exclude-pattern", "-e", multiple=True, help="Patterns to exclude")
@click.option("--include-pattern", "-i", multiple=True, help="Patterns to include")
@click.option("--branch", "-b", default=None, help="Branch to clone and ingest")
def main(
    source: str,
    output: Optional[str],
    output_format: str, # Added output_format
    max_size: int,
    exclude_pattern: Tuple[str, ...],
    include_pattern: Tuple[str, ...],
    branch: Optional[str],
):
    """
     Main entry point for the CLI.
     # ... (rest of docstring)
    """
    asyncio.run(_async_main(source, output, output_format, max_size, exclude_pattern, include_pattern, branch))


async def _async_main(
    source: str,
    output: Optional[str],
    output_format: str, # Added output_format
    max_size: int,
    exclude_pattern: Tuple[str, ...],
    include_pattern: Tuple[str, ...],
    branch: Optional[str],
) -> None:
    """
    Analyze a directory or repository and create a text dump or JSON of its contents.
     # ... (rest of docstring)
    """
    try:
        exclude_patterns_set = set(exclude_pattern)
        include_patterns_set = set(include_pattern)

        final_output_path: str
        user_provided_output = bool(output)

        if not output:
            name_part = ""
            # If source is current directory, use its name
            if source == ".":
                name_part = Path(".").resolve().name
            # Else, if it's a path or URL, extract the last part
            elif "/" in source or "\\" in source:
                name_part = source.split('/')[-1].split('\\')[-1]
                if name_part.endswith(".git"):
                    name_part = name_part[:-4]

            sanitized_name = "".join(c if c.isalnum() or c in ['_', '.', '-'] else '_' for c in (name_part or OUTPUT_FILE_NAME))
            if sanitized_name.endswith(".txt"): # remove default .txt if present, will add correct ext later
                sanitized_name = sanitized_name[:-4]

            sanitized_branch_part_if_any = ""
            if branch:
                sanitized_branch = "".join(c if c.isalnum() or c in ['_', '.', '-'] else '_' for c in branch)
                sanitized_branch_part_if_any = f"_{sanitized_branch}"

            ext = ".json" if output_format == "json" else ".txt"
            output_filename_candidate = f"{sanitized_name}{sanitized_branch_part_if_any}{ext}"
            final_output_path = output_filename_candidate
        else:
            final_output_path = output
            # Warning if user-provided extension does not match format
            current_ext = Path(final_output_path).suffix
            expected_ext = ".json" if output_format == "json" else ".txt"
            if current_ext.lower() != expected_ext.lower():
                click.echo(f"Warning: Output format is '{output_format}' but output file extension is '{current_ext}'. Consider using '{expected_ext}'.", err=True)


        # ingest_async now returns 4 values, pass output=None
        summary, tree_data, content_str, query_obj = await ingest_async(
            source,
            max_file_size=max_size,
            include_patterns=include_patterns_set,
            exclude_patterns=exclude_patterns_set,
            branch=branch,
            output=None # Pass None to prevent internal writing
        )

        payload_str: str
        if output_format == 'json':
            data_dict = {
                "summary": summary,
                "tree": tree_data, # tree_data is already a list of dicts
                "content": content_str,
                "query": query_obj.model_dump(mode='json') if query_obj else None # Changed this line
            }
            payload_str = json.dumps(data_dict, indent=2)
        else: # txt format
            formatted_tree_lines = []
            for item in tree_data: # tree_data is a list of dicts
                formatted_tree_lines.append(f"{item.get('prefix', '')}{item.get('name', '')}") # Use .get for safety
            formatted_tree = "\n".join(formatted_tree_lines)
            # Construct the text payload, ensuring summary is not duplicated if printed to console
            # The original text digest structure was: summary, then dir structure, then content
            # Summary is printed to console for txt, so payload for file is just structure and content
            payload_str = f"Directory structure:\n{formatted_tree}\n\n{content_str}"


        with open(final_output_path, "w", encoding="utf-8") as f:
            f.write(payload_str)

        click.echo(f"Analysis complete! Output written to: {final_output_path}")
        if output_format == 'txt':
            click.echo("\nSummary:")
            click.echo(summary)
        # If JSON and output is to a file (which we assume for now), summary is in the file.

    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        # Potentially print traceback for debugging if needed
        # import traceback
        # click.echo(traceback.format_exc(), err=True)
        raise click.Abort()


if __name__ == "__main__":
    main()