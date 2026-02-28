"""Unit tests for snapshot service (NUL sanitization for PostgreSQL)."""

import pytest

from libs.common import sanitize_for_postgres


class TestSanitizeForPostgres:
    """Tests for sanitize_for_postgres (strip NUL for PostgreSQL text/JSONB)."""

    def test_leaves_non_strings_unchanged(self):
        """Numbers, bools, None are returned as-is."""
        assert sanitize_for_postgres(1) == 1
        assert sanitize_for_postgres(1.5) == 1.5
        assert sanitize_for_postgres(True) is True
        assert sanitize_for_postgres(None) is None

    def test_strips_nul_from_string(self):
        """NUL character is removed from strings."""
        assert sanitize_for_postgres("a\u0000b") == "ab"
        assert sanitize_for_postgres("\u0000\u0000") == ""

    def test_leaves_clean_strings_unchanged(self):
        """Strings without NUL are unchanged."""
        s = "hello world"
        assert sanitize_for_postgres(s) is s

    def test_recursively_sanitizes_dict(self):
        """NUL is stripped from nested string values in dicts."""
        out = sanitize_for_postgres({"a": "x\u0000y", "b": {"c": "\u0000"}})
        assert out == {"a": "xy", "b": {"c": ""}}

    def test_recursively_sanitizes_list(self):
        """NUL is stripped from nested string values in lists."""
        out = sanitize_for_postgres(["ok", "bad\u0000here", ["nested\u0000"]])
        assert out == ["ok", "badhere", ["nested"]]

    def test_mixed_structure(self):
        """Realistic snapshot-like structure is sanitized."""
        raw = {
            "job_id": "db71ca85-c003-46e3-8dfc-bb60667a4760",
            "messages": [
                {"role": "user", "content": "hello\u0000world"},
                {"role": "assistant", "content": "hi"},
            ],
        }
        out = sanitize_for_postgres(raw)
        assert out["messages"][0]["content"] == "helloworld"
        assert out["messages"][1]["content"] == "hi"
