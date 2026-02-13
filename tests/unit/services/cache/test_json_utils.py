"""Smoke tests for JSON helpers with orjson fallback (#219)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from services.cache.json_utils import dumps_json, loads_json


class TestRoundTrip:
    @pytest.mark.parametrize(
        "data",
        [
            {"key": "value", "number": 42},
            [1, 2, 3],
            "plain string",
            None,
            True,
        ],
        ids=["dict", "list", "string", "null", "bool"],
    )
    def test_dumps_then_loads_preserves_data(self, data: Any) -> None:
        raw = dumps_json(data)
        assert isinstance(raw, bytes)
        assert loads_json(raw) == data

    def test_unicode_preserved(self) -> None:
        data = {"name": "Кирилиця", "emoji": "🎵"}
        assert loads_json(dumps_json(data)) == data


class TestDumpsJson:
    def test_returns_bytes(self) -> None:
        assert isinstance(dumps_json({"a": 1}), bytes)

    def test_indent_produces_newlines(self) -> None:
        raw = dumps_json({"a": 1}, indent=True)
        assert b"\n" in raw

    def test_no_indent_compact(self) -> None:
        raw = dumps_json({"a": 1}, indent=False)
        # Compact output should not have leading newlines (may have none or one)
        assert b"\n  " not in raw


class TestStdlibFallback:
    """Ensure stdlib json path works when orjson is unavailable."""

    def test_round_trip_without_orjson(self) -> None:
        with patch("services.cache.json_utils._ORJSON", None):
            data = {"test": [1, 2, 3], "nested": {"a": True}}
            raw = dumps_json(data)
            assert isinstance(raw, bytes)
            assert loads_json(raw) == data

    def test_indent_without_orjson(self) -> None:
        with patch("services.cache.json_utils._ORJSON", None):
            raw = dumps_json({"a": 1}, indent=True)
            assert b"\n" in raw
