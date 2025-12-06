# Contributing to Music Genre Updater

Thank you for your interest in contributing! This document provides guidelines and instructions for contributing to this
project.

## Getting Started

### Prerequisites

- macOS 10.15+ (required for Apple Music integration)
- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager (recommended)
- Apple Music app with a music library

### Development Setup

1. **Fork and clone the repository**
   ```bash
   git clone https://github.com/YOUR_USERNAME/GenreUpdater.git
   cd GenreUpdater
   ```

2. **Install dependencies**
   ```bash
   uv sync
   ```

3. **Create your config file**
   ```bash
   cp config.yaml my-config.yaml
   # Edit my-config.yaml with your settings
   ```

4. **Run tests to verify setup**
   ```bash
   uv run pytest tests/unit/ -x -q
   ```

## Development Workflow

### Branch Strategy

- `main` - Stable releases
- `dev` - Development branch (merge PRs here first)
- Feature branches: `feature/short-description`
- Bug fix branches: `fix/short-description`

### Making Changes

1. **Create a feature branch from `dev`**
   ```bash
   git checkout dev
   git pull origin dev
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes**
    - Follow the existing code style
    - Add tests for new functionality
    - Update documentation if needed

3. **Run quality checks**
   ```bash
   # Linting
   uv run ruff check src/ tests/

   # Formatting
   uv run ruff format src/ tests/

   # Type checking
   uv run mypy src/

   # Tests
   uv run pytest tests/unit/ -x
   ```

4. **Commit your changes**
   ```bash
   git add .
   git commit -m "feat(scope): description"
   ```

5. **Push and create a Pull Request**
   ```bash
   git push origin feature/your-feature-name
   ```

## Commit Message Convention

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

### Types

| Type       | Description                                             |
|------------|---------------------------------------------------------|
| `feat`     | New feature                                             |
| `fix`      | Bug fix                                                 |
| `docs`     | Documentation only                                      |
| `style`    | Code style (formatting, no logic change)                |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `perf`     | Performance improvement                                 |
| `test`     | Adding or updating tests                                |
| `ci`       | CI/CD changes                                           |
| `chore`    | Maintenance tasks                                       |

### Examples

```bash
feat(api): add Last.fm fallback for year lookup
fix(cache): prevent duplicate cache writes
docs: update installation instructions
refactor(tracks): extract genre scoring logic
test(api): add MusicBrainz timeout tests
```

## Code Style

### Python Guidelines

- **Type hints**: All functions must have type annotations
- **Async/await**: Use async for all I/O operations
- **Docstrings**: Use Google-style docstrings for public functions
- **Line length**: 150 characters maximum (configured in ruff)

### Architecture

The codebase follows clean architecture:

```
src/
├── core/       # Business logic and domain models
├── app/        # Application layer (CLI, orchestration)
├── services/   # External integrations (APIs, Apple Music)
└── metrics/    # Monitoring and reporting
```

See `CLAUDE.md` for detailed architecture documentation.

## Testing

### Test Organization

```
tests/
├── unit/           # Fast, isolated tests
├── integration/    # Service integration tests
└── e2e/           # End-to-end tests
```

### Running Tests

```bash
# All unit tests
uv run pytest tests/unit/

# With coverage
uv run pytest tests/unit/ --cov=src --cov-report=html

# Specific file
uv run pytest tests/unit/core/test_genre_manager.py

# By marker
uv run pytest -m "not slow"
```

### Writing Tests

- Use `pytest` and `pytest-asyncio` for async tests
- Mock external dependencies (AppleScript, APIs)
- Follow existing test patterns in the codebase

## Pull Request Process

1. **Ensure all checks pass**
    - Lint (ruff)
    - Format (ruff format)
    - Type check (mypy)
    - Tests (pytest)

2. **Update documentation** if needed

3. **Fill out the PR template** with:
    - Summary of changes
    - Test plan
    - Related issues

4. **Request review** from maintainers

5. **Address feedback** and update as needed

## Questions?

- Open an issue for bugs or feature requests
- Check existing issues before creating new ones
- Be respectful and constructive in discussions

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
