"""Generate API reference pages automatically from source code.

This script runs during mkdocs build to:
1. Scan all Python modules in src/
2. Generate markdown files with mkdocstrings directives
3. Build navigation structure automatically

Modules without docstrings are included but will show as empty
(controlled by mkdocstrings show_if_no_docstring option).

Note: mkdocs_gen_files is only available at mkdocs build runtime.
"""

from __future__ import annotations

from pathlib import Path

import mkdocs_gen_files

# Navigation builder
nav = mkdocs_gen_files.Nav()

# Scan source directory
src_root = Path("src")

# Module categories for better organization
CATEGORIES = {
    "app": "Application",
    "core": "Core",
    "services": "Services",
    "metrics": "Metrics",
}

# Modules to skip (cause import issues or are internal)
SKIP_MODULES = {
    # Internal modules
    "__init__",
    "__main__",
    # Features modules with complex TYPE_CHECKING imports
    "batch_processor",
    "database_verifier",
    "encryption",
    "exceptions",  # crypto exceptions
}

# Directories to skip entirely
SKIP_DIRS = {
    "__pycache__",
    ".git",
}

for path in sorted(src_root.rglob("*.py")):
    # Skip private modules
    if path.name.startswith("_"):
        continue

    # Skip modules in skip list
    if path.stem in SKIP_MODULES:
        continue

    # Skip if in a skipped directory
    if any(skip_dir in path.parts for skip_dir in SKIP_DIRS):
        continue

    # Get module path relative to src/
    module_path = path.relative_to(src_root).with_suffix("")

    # Build documentation path
    doc_path = path.relative_to(src_root).with_suffix(".md")
    full_doc_path = Path("reference") / doc_path

    # Get module parts for navigation
    parts = tuple(module_path.parts)

    # Skip if no parts
    if not parts:
        continue

    # Add to navigation with category names
    nav_parts = list(parts)
    if nav_parts[0] in CATEGORIES:
        nav_parts[0] = CATEGORIES[nav_parts[0]]

    nav[tuple(nav_parts)] = doc_path.as_posix()

    # Generate markdown file with mkdocstrings directive
    module_name = ".".join(parts)

    with mkdocs_gen_files.open(full_doc_path, "w") as fd:
        # Use filename as title, capitalize
        title = parts[-1].replace("_", " ").title()
        fd.write(f"# {title}\n\n")
        fd.write(f"::: {module_name}\n")

    # Set edit path to original source file
    mkdocs_gen_files.set_edit_path(full_doc_path, path)

# Write navigation file for literate-nav
with mkdocs_gen_files.open("reference/SUMMARY.md", "w") as nav_file:
    nav_file.writelines(nav.build_literate_nav())
