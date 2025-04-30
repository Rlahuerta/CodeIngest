# src/CodeIngest/schemas/filesystem_schema.py
"""Define the schema for the filesystem representation."""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Iterator, List, Optional

from CodeIngest.utils.file_utils import get_preferred_encodings, is_text_file
from CodeIngest.utils.notebook_utils import process_notebook

SEPARATOR = "=" * 48
DEFAULT_CHUNK_SIZE = 8192


class FileSystemNodeType(Enum):
    DIRECTORY = auto()
    FILE = auto()
    SYMLINK = auto()


@dataclass
class FileSystemStats:
    visited: set[Path] = field(default_factory=set)
    total_files: int = 0
    total_size: int = 0
    depth_limit_reached: bool = False
    total_file_limit_reached: bool = False
    total_size_limit_reached: bool = False


@dataclass
class FileSystemNode:
    name: str
    type: FileSystemNodeType
    path_str: str
    path: Path
    size: int = 0
    file_count: int = 0
    dir_count: int = 0
    depth: int = 0
    children: List[FileSystemNode] = field(default_factory=list)

    def sort_children(self) -> None:
        if self.type != FileSystemNodeType.DIRECTORY:
            raise ValueError("Cannot sort children of a non-directory node")
        def _sort_key(child: FileSystemNode) -> tuple[int, str]:
            name = child.name.lower(); node_type = child.type
            if node_type == FileSystemNodeType.FILE:
                if name == "readme.md": return (0, name)
                return (1, name) if not name.startswith(".") else (2, name)
            if node_type == FileSystemNodeType.DIRECTORY:
                return (3, name) if not name.startswith(".") else (4, name)
            if node_type == FileSystemNodeType.SYMLINK:
                 return (5, name) if not name.startswith(".") else (6, name)
            return (7, name)
        self.children.sort(key=_sort_key)


    def read_chunks(self, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterator[str]:
        """Reads file content in chunks, handling errors."""
        if self.type == FileSystemNodeType.SYMLINK: return
        if self.type != FileSystemNodeType.FILE:
            raise ValueError("Cannot read chunks of a non-file/non-symlink node")

        if not self.path.is_file():
            warnings.warn(f"Path is not a file during read_chunks: {self.path}", UserWarning)
            yield f"Error: Path is not a file ({self.path_str})"; return

        if not is_text_file(self.path):
            yield "[Non-text file]"; return

        if self.path.suffix == ".ipynb":
            try: yield process_notebook(self.path); return
            except Exception as exc:
                warnings.warn(f"Error processing notebook {self.path}: {exc}", UserWarning)
                yield f"Error processing notebook: {exc}"; return

        # --- Refined Encoding/Error Handling ---
        file_successfully_read = False
        decode_error_occurred = False
        for encoding in get_preferred_encodings():
            try:
                with self.path.open(mode='r', encoding=encoding, errors='strict') as f:
                    while True:
                        chunk = f.read(chunk_size)
                        if not chunk: break
                        yield chunk # Yield chunks as they are read successfully
                # If loop completes without error for this encoding:
                file_successfully_read = True
                break # Exit encoding loop
            except (UnicodeDecodeError, UnicodeError) as ude:
                decode_error_occurred = True # Mark error and try next encoding
                continue
            except OSError as exc:
                 warnings.warn(f"Error opening/reading file {self.path} with {encoding}: {exc}", UserWarning)
                 yield f"Error reading file: {exc}"; return # Stop processing
            except Exception as e:
                 warnings.warn(f"Unexpected error reading file {self.path} with {encoding}: {e}", UserWarning)
                 yield f"Unexpected error reading file: {e}"; return # Stop processing

        # After trying all encodings:
        if not file_successfully_read and decode_error_occurred:
             # --- FIX: Yield the error message *after* the loop ---
             warnings.warn(f"Failed to decode file {self.path} with available encodings.", UserWarning)
             yield "Error: Unable to decode file with available encodings"
             # --- End FIX ---
        # If no error occurred but still not read (e.g., empty file), yield nothing implicitly.
