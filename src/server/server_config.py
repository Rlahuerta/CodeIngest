"""Configuration for the server."""

from typing import Dict, List

from fastapi.templating import Jinja2Templates

MAX_DISPLAY_SIZE: int = 300_000
DELETE_REPO_AFTER: int = 60 * 60  # In seconds


# List of example repositories to display on the home page
# Updated the first example to reflect the new name and potentially your repo URL
EXAMPLE_REPOS: List[Dict[str, str]] = [
    {"name": "CodeIngest", "url": "https://github.com/Rlahuerta/CodeIngest"}, # Updated example
    {"name": "FastAPI", "url": "https://github.com/tiangolo/fastapi"},
    {"name": "Flask", "url": "https://github.com/pallets/flask"},
    {"name": "Excalidraw", "url": "https://github.com/excalidraw/excalidraw"},
    {"name": "ApiAnalytics", "url": "https://github.com/tom-draper/api-analytics"},
    # Add or remove examples as needed
]

templates = Jinja2Templates(directory="server/templates")
