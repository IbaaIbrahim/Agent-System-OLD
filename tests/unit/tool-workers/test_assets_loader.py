"""Unit tests for tool asset loader."""

import json
import importlib.util
from unittest.mock import patch, mock_open
import pytest

def get_loader_module():
    """Safely load the asset loader module without triggering other imports."""
    module_name = "src.tools.assets.loader"
    file_path = "services/tool-workers/src/tools/assets/loader.py"

    spec = importlib.util.spec_from_file_location(module_name, file_path)
    loader_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader_mod)
    return loader_mod

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
