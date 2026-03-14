"""Unit tests for tool asset loader."""

import json
import importlib.util
import importlib
import sys
from unittest.mock import patch, mock_open
import pytest

def get_loader_module():
    """Load the asset loader module via sys.path to match other tool-worker tests."""
    module_name = "src.tools.assets.loader"
    tools_root = "services/tool-workers"

    if tools_root not in sys.path:
        sys.path.insert(0, tools_root)

    return importlib.import_module(module_name)

class TestAssetLoader:
    """Tests for asset loader functions."""

    def test_load_json_asset_success(self):
        """Test successfully loading a JSON asset."""
        loader_mod = get_loader_module()
        mock_data = {"key": "value", "nested": [1, 2, 3]}
        json_content = json.dumps(mock_data)

        with patch("builtins.open", mock_open(read_data=json_content)):
            result = loader_mod.load_json_asset("test_tool", "test.json")

        assert result == mock_data

    def test_load_json_asset_file_not_found(self):
        """Test load_json_asset raises FileNotFoundError when file is missing."""
        loader_mod = get_loader_module()
        with patch("builtins.open", side_effect=FileNotFoundError):
            with pytest.raises(FileNotFoundError):
                loader_mod.load_json_asset("test_tool", "missing.json")

    def test_load_json_asset_invalid_json(self):
        """Test load_json_asset raises JSONDecodeError when content is invalid."""
        loader_mod = get_loader_module()
        with patch("builtins.open", mock_open(read_data="invalid json")):
            with pytest.raises(json.JSONDecodeError):
                loader_mod.load_json_asset("test_tool", "invalid.json")

    def test_load_text_asset_success(self):
        """Test successfully loading a text asset."""
        loader_mod = get_loader_module()
        mock_text = "  Hello World  \n"

        with patch("builtins.open", mock_open(read_data=mock_text)):
            result = loader_mod.load_text_asset("test_tool", "test.txt")

        assert result == "Hello World"

    def test_load_text_asset_file_not_found(self):
        """Test load_text_asset raises FileNotFoundError when file is missing."""
        loader_mod = get_loader_module()
        with patch("builtins.open", side_effect=FileNotFoundError):
            with pytest.raises(FileNotFoundError):
                loader_mod.load_text_asset("test_tool", "missing.txt")
