# Installation

## Requirements

- **macOS** 10.15 (Catalina) or later
- **Python** 3.13 or later
- **Apple Music** app (must be running for most operations)

## Install with uv (Recommended)

[uv](https://github.com/astral-sh/uv) is a fast Python package manager from Astral (the Ruff team).

```bash test="skip"
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone the repository
git clone https://github.com/barad1tos/GenreUpdater.git
cd GenreUpdater

# Install dependencies
uv sync
```

## Install with pip

If you prefer traditional pip/venv:

```bash test="skip"
# Clone the repository
git clone https://github.com/barad1tos/GenreUpdater.git
cd GenreUpdater

# Create virtual environment
python3.13 -m venv .venv
source .venv/bin/activate

# Install in editable mode
pip install -e .
```

## Verify Installation

```bash
# With uv
uv run python main.py --help

# With pip
python main.py --help
```

You should see the available commands and options.

## Next Steps

- [Quick Start](quickstart.md) — Run your first update
- [Configuration](configuration.md) — Customize paths and settings
