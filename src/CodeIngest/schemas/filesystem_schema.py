"""Define the schema for the filesystem representation."""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Iterator

# Import necessary utils carefully to avoid circular imports if moved
from CodeIngest.utils.file_utils import get_preferred_encodings, is_text_file
from CodeIngest.utils.notebook_utils import process_notebook

SEPARATOR = "=" * 48
DEFAULT_CHUNK_SIZE = 8192


class FileSystemNodeType(Enum):
    """Enum representing the type of a file system node (directory or file)."""

    DIRECTORY = auto()
    FILE = auto()
    SYMLINK = auto()


@dataclass
class FileSystemStats:
    """Class for tracking statistics during file system traversal."""

    visited: set[Path] = field(default_factory=set)
    total_files: int = 0
    total_size: int = 0


@dataclass
class FileSystemNode:
    """
    Class representing a node in the file system (either a file or directory).

    Tracks properties of files/directories for comprehensive analysis.
    Does NOT store file content directly for large files.
    """

    name: str
    type: FileSystemNodeType
    path_str: str
    path: Path
    size: int = 0
    file_count: int = 0
    dir_count: int = 0
    depth: int = 0
    children: list[FileSystemNode] = field(default_factory=list)

    def sort_children(self) -> None:
        """
        Sort the children nodes of a directory according to a specific order.
        """
        if self.type != FileSystemNodeType.DIRECTORY:
            raise ValueError("Cannot sort children of a non-directory node")

        def _sort_key(child: FileSystemNode) -> tuple[int, str]:
            name = child.name.lower()
            if child.type == FileSystemNodeType.FILE:
                if name == "readme.md": return (0, name)
                return (1 if not name.startswith(".") else 2, name)
            return (3 if not name.startswith(".") else 4, name)

        self.children.sort(key=_sort_key)

    # Removed content_string property

    def read_chunks(self, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterator[str]:
        """
        Reads the content of a file in chunks.

        Parameters
        ----------
        chunk_size : int
            The size of each chunk to yield in bytes.

        Yields
        ------
        str
            A chunk of the file content, or an error message string.

        Raises
        ------
        ValueError
            If the node is not a file or symlink.
        """
        # --- FIX: Handle SYMLINK first - return empty iterator ---
        if self.type == FileSystemNodeType.SYMLINK:
            return # Yield nothing for symlinks

        if self.type != FileSystemNodeType.FILE:
             # Raise error only if not FILE and not SYMLINK
            raise ValueError("Cannot read chunks of a non-file node")


        if not self.path.is_file():
            warnings.warn(f"Path is not a file: {self.path}", UserWarning)
            yield f"Error: Path is not a file ({self.path_str})"
            return

        if not is_text_file(self.path):
            yield "[Non-text file]"
            return

        if self.path.suffix == ".ipynb":
            try:
                yield process_notebook(self.path)
                return
            except Exception as exc:
                yield f"Error processing notebook: {exc}"
                return

        # Try multiple encodings for regular text files
        last_error = None
        for encoding in get_preferred_encodings():
            try:
                with self.path.open(mode='r', encoding=encoding, errors='strict') as f:
                    while True:
                        try:
                            chunk = f.read(chunk_size)
                            if not chunk:
                                break
                            yield chunk
                        except UnicodeDecodeError as ude_read:
                            warnings.warn(f"UnicodeDecodeError while reading chunk from {self.path} with {encoding}: {ude_read}", UserWarning)
                            raise UnicodeDecodeError(encoding, b'', 0, 0, 'Error during chunk read') # Re-raise

                # Successfully read the whole file
                return
            except UnicodeDecodeError:
                # Failed to decode with this encoding, try the next one
                last_error = "decode" # Mark that decoding failed at least once
                continue
            except OSError as exc:
                 # --- FIX: Yield specific OS error and STOP trying other encodings ---
                warnings.warn(f"Error opening file {self.path} with {encoding}: {exc}", UserWarning)
                yield f"Error reading file: {exc}"
                return
            except Exception as e:
                 # Catch other unexpected errors during open/read
                 warnings.warn(f"Unexpected error reading file {self.path} with {encoding}: {e}", UserWarning)
                 yield f"Unexpected error reading file: {e}"
                 return

        # If loop finished without returning (i.e., all encodings failed or resulted in decode errors)
        if last_error == "decode":
            yield "Error: Unable to decode file with available encodings"
        # If no error occurred but loop finished (e.g., empty file after is_text_file check?), yield nothing implicitly