"""Lightweight JSON helpers with optional orjson acceleration."""

from __future__ import annotations

import json
from typing import Any, cast

_ORJSON: Any | None = None

try:
    import orjson as _imported_orjson
except ModuleNotFoundError:
    pass
else:
    _ORJSON = _imported_orjson
    del _imported_orjson


def dumps_json(data: Any, *, indent: bool = False) -> bytes:
    """Serialize Python data to JSON bytes, preferring orjson when available."""
    if _ORJSON is not None:
        orjson_mod = cast(Any, _ORJSON)
        option = orjson_mod.OPT_INDENT_2 if indent else 0
        return cast(bytes, orjson_mod.dumps(data, option=option))

    kwargs: dict[str, Any] = {"ensure_ascii": False}
    if indent:
        kwargs["indent"] = 2
    return json.dumps(data, **kwargs).encode()


def loads_json(data: bytes) -> Any:
    """Deserialize JSON bytes into Python data."""
    if _ORJSON is not None:
        orjson_mod = cast(Any, _ORJSON)
        return orjson_mod.loads(data)
    return json.loads(data.decode())
