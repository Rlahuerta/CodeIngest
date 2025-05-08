# src/server/server_config.py
"""Configuration for the server."""

from typing import Dict, List
# from jinja2 import Environment # No longer needed
from fastapi.templating import Jinja2Templates # Keep this

MAX_DISPLAY_SIZE: int = 300_000
DELETE_REPO_AFTER: int = 60 * 60  # In seconds

# List of example repositories
EXAMPLE_REPOS: List[Dict[str, str]] = [
    {"name": "CodeIngest", "url": "https://github.com/Rlahuerta/CodeIngest"},
    {"name": "FastAPI", "url": "https://github.com/tiangolo/fastapi"},
    {"name": "Flask", "url": "https://github.com/pallets/flask"},
    {"name": "Excalidraw", "url": "https://github.com/excalidraw/excalidraw"},
    {"name": "ApiAnalytics", "url": "https://github.com/tom-draper/api-analytics"},
]

# --- REVERTED: Initialize Jinja2Templates normally ---
templates = Jinja2Templates(directory="server/templates")
# --- END REVERT ---