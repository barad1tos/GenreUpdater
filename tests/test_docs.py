from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from pytest_examples import CodeExample, EvalExample, find_examples

DOCS_DIR = Path(__file__).parent.parent / "docs"

_EXCLUDED_DIRS = {"plans"}


def _is_excluded(example: CodeExample) -> bool:
    """Check if example is from an excluded directory."""
    path_str = str(example.path)
    return any(f"docs/{excluded}/" in path_str for excluded in _EXCLUDED_DIRS)


# Python code block validation via pytest-examples
@pytest.mark.parametrize("example", find_examples("docs/"), ids=str)
def test_docs_python_examples(example: CodeExample, eval_example: EvalExample) -> None:
    """Validate Python code blocks in documentation."""
    if _is_excluded(example):
        pytest.skip("Internal planning document")
    if example.prefix_settings().get("test") == "skip":
        pytest.skip("Marked as illustrative")
    eval_example.lint(example)


# Bash code block validation via shellcheck
@dataclass
class BashBlock:
    """A bash code block extracted from a markdown file."""

    file: Path
    line: int
    source: str
    skip: bool


_FENCE_PATTERN = re.compile(r"^```bash\s*(.*?)$")
_FENCE_CLOSE = re.compile(r"^```\s*$")


def _extract_bash_blocks(docs_dir: Path) -> list[BashBlock]:
    """Extract all bash code blocks from markdown files."""
    blocks: list[BashBlock] = []
    for md_file in sorted(docs_dir.rglob("*.md")):
        if any(part in _EXCLUDED_DIRS for part in md_file.relative_to(docs_dir).parts):
            continue
        text = md_file.read_text(encoding="utf-8")
        lines = text.splitlines()
        idx = 0
        while idx < len(lines):
            fence_match = _FENCE_PATTERN.match(lines[idx])
            if fence_match:
                settings = fence_match.group(1).strip()
                skip = 'test="skip"' in settings or "test='skip'" in settings
                start_line = idx + 1
                idx += 1
                block_lines: list[str] = []
                while idx < len(lines) and not _FENCE_CLOSE.match(lines[idx]):
                    block_lines.append(lines[idx])
                    idx += 1
                blocks.append(
                    BashBlock(
                        file=md_file,
                        line=start_line,
                        source="\n".join(block_lines),
                        skip=skip,
                    )
                )
            idx += 1
    return blocks


def _bash_block_id(block: BashBlock) -> str:
    relative = block.file.relative_to(DOCS_DIR)
    return f"{relative}:{block.line}"


_BASH_BLOCKS = _extract_bash_blocks(DOCS_DIR)


@pytest.mark.parametrize("block", _BASH_BLOCKS, ids=_bash_block_id)
def test_docs_bash_examples(block: BashBlock) -> None:
    """Validate bash code blocks in documentation with shellcheck."""
    if block.skip:
        pytest.skip("Marked as illustrative")
    if not block.source.strip():
        pytest.skip("Empty block")
    result = subprocess.run(
        ["shellcheck", "--shell=bash", "-"],
        input=block.source,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, f"shellcheck errors in {block.file}:{block.line}:\n{result.stdout}\n{result.stderr}"
