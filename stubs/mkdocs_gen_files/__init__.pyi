"""Type stubs for mkdocs-gen-files package."""

from collections.abc import Generator, Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Literal

class Nav:
    """Navigation builder for literate-nav plugin."""

    def __getitem__(self, keys: tuple[str, ...]) -> str: ...
    def __setitem__(self, keys: tuple[str, ...], value: str) -> None: ...
    def build_literate_nav(self) -> Iterable[str]: ...

@contextmanager
def open(
    path: str | Path,
    mode: Literal["w", "r", "a"] = "w",
    encoding: str = "utf-8",
) -> Generator[IO[str], None, None]: ...

def set_edit_path(dest: str | Path, src: str | Path) -> None: ...
