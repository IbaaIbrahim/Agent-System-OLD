"""Asset loader utility for tool resources."""

import json
from pathlib import Path
from typing import Any

ASSETS_DIR = Path(__file__).parent


def load_json_asset(tool_name: str, filename: str) -> dict[str, Any]:
    """Load a JSON asset file for a tool.

    Args:
        tool_name: Name of the tool (subfolder in assets/)
        filename: Name of the JSON file to load

    Returns:
        Parsed JSON as dictionary

    Raises:
        FileNotFoundError: If the asset file doesn't exist
    """
    path = ASSETS_DIR / tool_name / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_text_asset(tool_name: str, filename: str) -> str:
    """Load a text asset file for a tool.

    Args:
        tool_name: Name of the tool (subfolder in assets/)
        filename: Name of the text file to load

    Returns:
        File contents as string (stripped)

    Raises:
        FileNotFoundError: If the asset file doesn't exist
    """
    path = ASSETS_DIR / tool_name / filename
    with open(path, encoding="utf-8") as f:
        return f.read().strip()
