# Contributing

Guidelines for contributing to Music Genre Updater.

## Development Setup

### Prerequisites

- Python 3.13+
- [uv](https://github.com/astral-sh/uv) package manager
- macOS with Music.app

### Installation

```bash
# Clone the repository
git clone https://github.com/barad1tos/GenreUpdater.git
cd GenreUpdater

# Install dependencies
uv sync

# Set up prek hooks (pre-commit alternative)
prek install
```

### Environment Variables

Create a `.env` file:

```bash
DISCOGS_TOKEN=your_discogs_token
CONTACT_EMAIL=your@email.com
```

## Code Quality

### Linting

```bash
# Run ruff linter
uv run ruff check src/

# Auto-fix issues
uv run ruff check --fix src/

# Format code
uv run ruff format src/
```

### Type Checking

This project uses **ty** (not mypy):

```bash
uv run ty check src/ --python .venv
```

### All Checks

```bash
prek run --all-files
```

## Testing

### Run Tests

```bash
# All tests
uv run pytest

# Specific file
uv run pytest tests/unit/core/test_genre_manager.py

# With coverage
uv run pytest --cov=src --cov-report=html
```

### Test Categories

| Marker | Description |
|--------|-------------|
| `unit` | Fast, isolated tests |
| `integration` | Tests with real cache |
| `e2e` | Requires Music.app |
| `slow` | Long-running tests |

Run specific categories:

```bash
uv run pytest -m unit
uv run pytest -m "not slow"
```

## Git Workflow

### Branch Strategy

- `main` — Protected stable branch
- `dev` — Primary development branch
- `feature/*` — Feature branches

### Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(domain): add artist name normalization
fix(cache): prevent duplicate cache writes
refactor(api): extract scoring logic
docs: update architecture documentation
```

### Pull Request Process

1. Create feature branch from `dev`
2. Make changes
3. Run all checks: `prek run --all-files`
4. Push and create PR to `dev`
5. After review, squash merge to `dev`
6. Periodically, `dev` is merged to `main` via PR

## Code Style

### Docstrings

Use Google style:

```python
def fetch_year(self, artist: str, album: str) -> tuple[int | None, int]:
    """Fetch album release year from external API.

    Args:
        artist: Artist name
        album: Album name

    Returns:
        Tuple of (year, confidence_score). Year is None if not found.

    Raises:
        RateLimitError: If API rate limit exceeded
    """
```

### Type Hints

All functions must have type hints:

```python
async def process_tracks(
    self,
    tracks: list[Track],
    *,
    force: bool = False,
) -> ProcessingResult:
    ...
```

### Error Handling

Follow the project's error design principles:

```python
# ✅ Good - includes context
except OSError as e:
    logger.warning(
        "Error reading %s for artist %s: %s",
        file_path,
        artist_name,
        e,
    )

# ❌ Bad - no context
except OSError as e:
    logger.warning("Error: %s", e)
```

## Architecture Guidelines

### Adding New Features

1. Check if infrastructure already exists (see CLAUDE.md)
2. Follow the layer separation:
   - `app/` — Presentation
   - `core/` — Business logic
   - `services/` — External integrations
3. Use dependency injection via `DependencyContainer`
4. Define interfaces with `Protocol`

### Adding API Clients

1. Inherit from `BaseApiClient`
2. Implement `ExternalApiServiceProtocol`
3. Add rate limiting
4. Register in `ExternalApiOrchestrator`

## Documentation

### Building Docs

```bash
# Serve locally
uv run mkdocs serve

# Build static site
uv run mkdocs build
```

### Adding Pages

1. Create `.md` file in `docs/`
2. Add to `nav` section in `mkdocs.yml`
3. Use mkdocstrings for API docs:

```markdown
::: module.path.ClassName
    options:
      show_source: true
```

## Questions?

- Open an issue on GitHub
- Check existing documentation
- Review the codebase's CLAUDE.md for detailed guidelines
