# Type stubs for pydantic v1.x to improve type checking

from collections.abc import Callable
from typing import Any, Self, overload

class BaseModel:
    def __init__(self, **data: Any) -> None: ...
    def dict(self, **kwargs: Any) -> dict[str, Any]: ...
    def json(self, **kwargs: Any) -> str: ...
    @classmethod
    def parse_obj(cls, obj: Any) -> Self: ...
    @classmethod
    def parse_raw(cls, b: str | bytes, **kwargs: Any) -> Self: ...

def field(default: Any = ..., **kwargs: Any) -> Any: ...
def validator(
    field_name: str,
    /,
    *fields: str,
    pre: bool = False,
    each_item: bool = False,
    always: bool = False,
    check_fields: bool = True,
    whole: bool | None = None,
    allow_reuse: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...
@overload
def root_validator(
    _func: Callable[..., Any],
) -> Callable[..., Any]: ...
@overload
def root_validator(
    *,
    pre: bool = False,
    allow_reuse: bool = False,
    skip_on_failure: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...
