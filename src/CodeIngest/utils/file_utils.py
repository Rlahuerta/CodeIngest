"""Utility functions for working with files and directories."""

import locale
import platform
from pathlib import Path
from typing import List, Iterator # Import Iterator

try:
    locale.setlocale(locale.LC_ALL, "")
except locale.Error:
    locale.setlocale(locale.LC_ALL, "C")


def get_preferred_encodings() -> List[str]:
    """
    Get list of encodings to try, prioritized for the current platform.

    Returns
    -------
    List[str]
        List of encoding names to try in priority order, starting with the
        platform's default encoding followed by common fallback encodings.
    """
    # Added more common encodings for better compatibility
    encodings = [locale.getpreferredencoding(), "utf-8", "utf-16", "utf-16le", "utf-8-sig", "latin-1", "ascii"]
    if platform.system() == "Windows":
        encodings += ["cp1252", "iso-8859-1"]
    # Remove duplicates while preserving order as much as possible
    seen = set()
    ordered_encodings = []
    for enc in encodings:
        if enc not in seen:
            ordered_encodings.append(enc)
            seen.add(enc)
    return ordered_encodings


def is_text_file(path: Path) -> bool:
    """
    Determine if the file is likely a text file by trying to decode a small chunk
    with multiple encodings, and checking for common binary markers.

    Parameters
    ----------
    path : Path
        The path to the file to check.

    Returns
    -------
    bool
        True if the file is likely textual; False if it appears to be binary.
    """

    # Attempt to read a portion of the file in binary mode
    try:
        with path.open("rb") as f:
            chunk = f.read(1024)
    except OSError:
        return False # Cannot read the file, assume not text

    # If file is empty, treat as text
    if not chunk:
        return True

    # Check obvious binary bytes
    # Added more common binary indicators
    binary_indicators = {b"\x00", b"\xff", b"\xfe\xff", b"\xff\xfe"}
    if any(indicator in chunk for indicator in binary_indicators):
        return False

    # Attempt multiple encodings on the chunk to see if it can be decoded
    # This is a heuristic, not foolproof
    for enc in get_preferred_encodings():
        try:
            chunk.decode(enc)
            return True # Successfully decoded a chunk, likely text
        except UnicodeDecodeError:
            continue
        except UnicodeError:
            continue
        except Exception:
            # Catch any other potential decoding errors
            continue


    return False # Could not decode the chunk with any preferred encoding
