# Repository Guidelines

## Project Structure & Module Organization

- Source code lives in `src/` with domains:
  - `src/core/` (CLI, orchestrators, processing modules)
  - `src/services/` (APIs, caching, AppleScript client)
  - `src/utils/` (data, monitoring, core helpers)
  - `src/typings/` (stubs and types)
- Entry points: `main.py` (runtime), `src/core/cli.py` (CLI helpers).
- Tests in `tests/`; AppleScript assets in `applescripts/`; docs in `docs/`.
- Configuration: `my-config.yaml` (local), `.env` for secrets (do not commit).

## Build, Test, and Development Commands

- Setup (venv optional): `python -m venv .venv && source .venv/bin/activate` or use uv: `uv run ...`.
- Run app: `uv run python main.py` (or `python3 main.py --dry-run`).
- Tests: `uv run pytest -q`.
- Coverage: `uv run pytest --cov=src --cov-report=term-missing`.
- Lint: `uv run ruff check .` and format: `uv run ruff format .`.
- Types: `uv run mypy src main.py`.
- Dead code scan: `uv run vulture src`.

## Coding Style & Naming Conventions

- Python 3.13; 4-space indentation; prefer max line length 150.
- Format with Ruff formatter (Black-compatible): double quotes, spaces, trailing commas.
- Naming: modules/functions `snake_case`, classes `CamelCase`, constants `UPPER_SNAKE`.
- Type hints required for public functions; keep docstrings Google-style.
- Keep external effects isolated; prefer pure functions in `core` and `utils`.

## Testing Guidelines

- Framework: pytest; tests under `tests/` named `test_*.py`.
- Markers: `unit`, `integration`, `slow` (see `pyproject.toml`).
- Aim to maintain or increase coverage; add fixtures over sleep/timeouts; mock external APIs.
- Run full suite before PR: `uv run pytest --cov=src`.

## Commit & Pull Request Guidelines

- Commits: imperative mood, concise subject (≤72 chars), include scope when helpful.
- Example: `feat(core): add batch processor metrics`.
- PRs: clear description, motivation, screenshots/log snippets when UI/CLI output changes, link issues, note config changes.
- Ensure CI-style checks pass locally (lint, types, tests) before requesting review.

## Security & Configuration Tips

- Do not commit secrets or personal paths; use `.env` and local `my-config.yaml`.
- macOS-only AppleScript paths live in `applescripts/`; keep scripts idempotent and log context (artist | album | track).

## Agent-Specific Instructions

- Place new features in domain folders (`core/modules/...`, `services/...`).
- Do not change public APIs without deprecation notes; avoid network calls in unit tests; keep changes minimal and focused.
