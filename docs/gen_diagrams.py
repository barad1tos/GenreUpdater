"""Generate class diagrams from source code using pyreverse.

This script runs during mkdocs build to generate Mermaid class diagrams
for key modules in the codebase.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Final

import mkdocs_gen_files

# Security: All values are hardcoded constants, not user input
# pyreverse is a trusted tool from pylint package (installed via pyproject.toml)
# Use shutil.which() for cross-platform PATH resolution (works on macOS, Linux CI)
_PYREVERSE_EXECUTABLE: Final[str | None] = shutil.which("pyreverse")

# Module configurations - all values are trusted constants
_DIAGRAM_CONFIGS: Final[tuple[tuple[str, str, str], ...]] = (
    ("src/core/tracks", "core-tracks", "Core Track Processing"),
    ("src/core/models", "core-models", "Data Models"),
    ("src/services/api", "services-api", "API Clients"),
    ("src/services/cache", "services-cache", "Cache Services"),
    ("src/services/apple", "services-apple", "Apple Music Integration"),
    ("src/app", "app-layer", "Application Layer"),
    ("src/metrics", "metrics", "Metrics & Analytics"),
)


# noinspection PyArgumentEqualDefault
def _run_pyreverse(executable: str, output_dir: str, project_name: str, source_path: str) -> subprocess.CompletedProcess[str]:
    """Execute pyreverse with trusted arguments.

    All arguments are validated to be from _DIAGRAM_CONFIGS constant.

    Args:
        executable: Path to pyreverse executable (from shutil.which)
        output_dir: Temporary directory for output (from tempfile)
        project_name: Project name from _DIAGRAM_CONFIGS
        source_path: Source module path from _DIAGRAM_CONFIGS

    Returns:
        CompletedProcess with execution results
    """
    # S603: All arguments come from _DIAGRAM_CONFIGS (hardcoded constants) and tempfile.
    # This is a build script, not runtime code, and pyreverse is a trusted tool.
    return subprocess.run(
        [
            executable,
            "-o",
            "mmd",
            "-p",
            project_name,
            "-d",
            output_dir,
            "--colorized",
            source_path,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,  # PLW1510: explicit check=False - we handle errors via returncode
    )


def _generate_single_diagram(source_path: str, project_name: str) -> str | None:
    """Generate Mermaid diagram for a single module.

    Args:
        source_path: Path to the module directory (from _DIAGRAM_CONFIGS)
        project_name: Base name for output file (from _DIAGRAM_CONFIGS)

    Returns:
        Mermaid diagram content or None if generation failed
    """
    # Check if pyreverse is available in PATH (local var enables type narrowing)
    executable = _PYREVERSE_EXECUTABLE
    if executable is None:
        return None

    pyreverse_path = Path(executable)
    if not pyreverse_path.exists():
        return None

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _run_pyreverse(executable, tmpdir, project_name, source_path)

            if result.returncode != 0:
                return None

            # pyreverse creates classes_<name>.mmd
            mmd_file = Path(tmpdir) / f"classes_{project_name}.mmd"
            if mmd_file.exists():
                return mmd_file.read_text()

            # Fallback: find any .mmd file
            for mmd_path in Path(tmpdir).glob("*.mmd"):
                return mmd_path.read_text()

    except (subprocess.TimeoutExpired, OSError):
        return None

    return None


def _build_diagrams_page() -> str:
    """Build the complete diagrams markdown page.

    Returns:
        Markdown content for the diagrams page
    """
    content = """# Auto-Generated Class Diagrams

These diagrams are automatically generated from the source code using `pyreverse`.

!!! info "Auto-Updated"
    These diagrams update automatically when the documentation is built.
    They reflect the current state of the codebase.

"""

    for source_path, project_name, description in _DIAGRAM_CONFIGS:
        diagram = _generate_single_diagram(source_path, project_name)

        if diagram:
            content += f"""
## {description}

```mermaid
{diagram.strip()}
```

"""
        else:
            content += f"""
## {description}

!!! warning "Diagram generation failed"
    Could not generate diagram for `{source_path}`.

"""

    return content


# Main execution - write the diagrams page
_page_content = _build_diagrams_page()
with mkdocs_gen_files.open("architecture/class-diagrams.md", "w") as output_file:
    output_file.write(_page_content)
