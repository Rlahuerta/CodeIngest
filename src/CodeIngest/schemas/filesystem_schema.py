# src/CodeIngest/schemas/filesystem_schema.py
"""Define the schema for the filesystem representation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
import warnings # Import warnings

from CodeIngest.utils.file_utils import get_preferred_encodings, is_text_file
from CodeIngest.utils.notebook_utils import process_notebook

SEPARATOR = "=" * 48  # Tiktoken, the tokenizer openai uses, counts 2 tokens if we have more than 48


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
    # --- ADDED Missing Flags ---
    depth_limit_reached: bool = False
    total_file_limit_reached: bool = False
    total_size_limit_reached: bool = False


@dataclass
class FileSystemNode:  # pylint: disable=too-many-instance-attributes
    """
    Class representing a node in the file system (either a file or directory).

    Tracks properties of files/directories for comprehensive analysis.
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
    _content_cache: str | None = field(default=None, repr=False) # Add cache for content


    def sort_children(self) -> None:
        """
        Sort the children nodes of a directory according to a specific order.

        Order of sorting:
          1. README.md (if present)
          2. Regular files (not starting with dot)
          3. Hidden files (starting with dot)
          4. Regular directories (not starting with dot)
          5. Hidden directories (starting with dot)
          6. Symlinks (sorted by name after directories)


        All groups are sorted alphanumerically within themselves.

        Raises
        ------
        ValueError
            If the node is not a directory.
        """
        if self.type != FileSystemNodeType.DIRECTORY:
            raise ValueError("Cannot sort children of a non-directory node")

        def _sort_key(child: FileSystemNode) -> tuple[int, str]:
            # returns the priority order for the sort function, 0 is first
            # Groups: 0=README, 1=regular file, 2=hidden file, 3=regular dir, 4=hidden dir, 5=symlink
            name = child.name.lower()
            if child.type == FileSystemNodeType.FILE:
                if name == "readme.md":
                    return (0, name)
                return (1 if not name.startswith(".") else 2, name)
            elif child.type == FileSystemNodeType.DIRECTORY:
                return (3 if not name.startswith(".") else 4, name)
            elif child.type == FileSystemNodeType.SYMLINK:
                 return (5, name) # Sort symlinks last
            return (6, name) # Should not happen, fallback

        self.children.sort(key=_sort_key)

    # --- RESTORED content property ---
    @property
    def content(self) -> str:  # pylint: disable=too-many-return-statements
        """
        Read the content of a file if it's text (or a notebook). Return an error message otherwise.
        Caches the content after the first read.

        Returns
        -------
        str
            The content of the file, or an error message if the file could not be read.

        Raises
        ------
        ValueError
            If the node is a directory.
        """
        if self._content_cache is not None:
            return self._content_cache

        if self.type == FileSystemNodeType.DIRECTORY:
            raise ValueError("Cannot read content of a directory node")

        if self.type == FileSystemNodeType.SYMLINK:
            # Symlinks themselves don't have readable content in this context
            self._content_cache = ""
            return self._content_cache

        # Add a size check before attempting to read
        # Avoid reading excessively large files into memory here if they somehow bypassed earlier checks
        # Use a reasonable upper limit, e.g., 100MB, adjust as needed
        MAX_READ_SIZE = 100 * 1024 * 1024
        # Ensure size is checked before attempting stat if size is already known
        if self.size > MAX_READ_SIZE:
             warnings.warn(f"File {self.name} ({self.size} bytes) too large to read content directly, skipping.", UserWarning)
             self._content_cache = "[File content too large to display/process directly]"
             return self._content_cache
        # Stat only if size wasn't pre-populated or is zero (might happen for empty files)
        elif self.size == 0:
             try:
                 actual_size = self.path.stat().st_size
                 if actual_size > MAX_READ_SIZE:
                     warnings.warn(f"File {self.name} ({actual_size} bytes) too large to read content directly, skipping.", UserWarning)
                     self._content_cache = "[File content too large to display/process directly]"
                     return self._content_cache
             except OSError:
                 # If stat fails, we probably can't read it anyway
                 pass # Let the read attempt handle the error


        if not is_text_file(self.path):
            self._content_cache = "[Non-text file]"
            return self._content_cache

        if self.path.suffix == ".ipynb":
            try:
                self._content_cache = process_notebook(self.path)
                return self._content_cache
            except Exception as exc:
                warnings.warn(f"Error processing notebook {self.path}: {exc}", UserWarning) # Added warning
                self._content_cache = f"Error processing notebook: {exc}"
                return self._content_cache

        # Try multiple encodings
        for encoding in get_preferred_encodings():
            try:
                with self.path.open(encoding=encoding) as f:
                    self._content_cache = f.read()
                    return self._content_cache
            except UnicodeDecodeError:
                continue
            except UnicodeError: # Catch broader Unicode errors
                continue
            except OSError as exc:
                 # Handle cases like permission errors during open
                 warnings.warn(f"Error opening/reading file {self.path} with encoding {encoding}: {exc}", UserWarning) # Added warning
                 self._content_cache = f"Error reading file: {exc}"
                 return self._content_cache
            except Exception as exc: # Catch any other unexpected errors
                 warnings.warn(f"Unexpected error reading file {self.path} with encoding {encoding}: {exc}", UserWarning) # Added warning
                 self._content_cache = f"Unexpected error reading file: {exc}"
                 return self._content_cache


        warnings.warn(f"Failed to decode file {self.path} with available encodings.", UserWarning) # Added warning
        self._content_cache = "Error: Unable to decode file with available encodings"
        return self._content_cache

    # --- RESTORED content_string property ---
    @property
    def content_string(self) -> str:
        """
        Return the content of the node as a string, including path and content.

        Returns
        -------
        str
            A string representation of the node's content.
        """
        link_info = ""
        node_content = "" # Initialize to empty

        if self.type == FileSystemNodeType.SYMLINK:
            try:
                link_target = self.path.readlink().as_posix()
                link_info = f" -> {link_target}"
            except OSError:
                link_info = " -> [Broken Link]"
            # No content for symlinks themselves
        elif self.type == FileSystemNodeType.FILE:
             # Access the restored content property to read/get content
             node_content = self.content
        # No need for DIRECTORY case, node_content remains empty

        # Ensure path_str is treated correctly (relative path)
        display_path_str = str(self.path_str).replace(os.sep, '/')

        parts = [
            SEPARATOR,
            f"{self.type.name}: {display_path_str}{link_info}",
            SEPARATOR,
            f"{node_content}", # Use the retrieved content
        ]

        return "\n".join(parts) + "\n\n"