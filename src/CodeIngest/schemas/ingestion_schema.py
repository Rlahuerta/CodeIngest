"""This module contains the dataclasses for the ingestion process."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Set

from pydantic import BaseModel, ConfigDict, Field

from CodeIngest.config import MAX_FILE_SIZE


@dataclass
class CloneConfig:
    """
    Configuration for cloning a Git repository.

    This class holds the necessary parameters for cloning a repository to a local path, including
    the repository's URL, the target local path, and optional parameters for a specific commit or branch.

    Attributes
    ----------
    url : str
        The URL of the Git repository to clone.
    local_path : str
        The local directory where the repository will be cloned.
    commit : str, optional
        The specific commit hash to check out after cloning (default is None).
    branch : str, optional
        The branch to clone (default is None).
    subpath : str
        The subpath to clone from the repository (default is "/").
    """

    url: str
    local_path: str
    commit: Optional[str] = None
    branch: Optional[str] = None
    subpath: str = "/"
    blob: bool = False


class IngestionQuery(BaseModel):  # pylint: disable=too-many-instance-attributes
    """
    Pydantic model to store the parsed details of the repository or file path.
    """

    user_name: Optional[str] = None
    repo_name: Optional[str] = None
    local_path: Path # This will point to the actual dir to ingest (original or temp extract)
    url: Optional[str] = None
    slug: str
    id: str
    subpath: str = "/"
    type: Optional[str] = None
    branch: Optional[str] = None
    commit: Optional[str] = None
    max_file_size: int = Field(default=MAX_FILE_SIZE)
    ignore_patterns: Optional[Set[str]] = None
    include_patterns: Optional[Set[str]] = None
    # --- Added fields for zip handling ---
    original_zip_path: Optional[Path] = None # Stores the path to the original zip if source was a zip
    temp_extract_path: Optional[Path] = None # Stores the path to the temp dir if extracted from zip

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def extract_clone_config(self) -> CloneConfig:
        """
        Extract the relevant fields for the CloneConfig object.

        Returns
        -------
        CloneConfig
            A CloneConfig object containing the relevant fields.

        Raises
        ------
        ValueError
            If the 'url' parameter is not provided.
        """
        if not self.url:
            # Changed error message slightly to reflect it's for remote cloning
            raise ValueError("Clone config can only be extracted for remote repository URLs.")

        return CloneConfig(
            url=self.url,
            local_path=str(self.local_path), # Note: for remote, local_path is already the temp clone dir
            commit=self.commit,
            branch=self.branch,
            subpath=self.subpath,
            blob=self.type == "blob",
        )
