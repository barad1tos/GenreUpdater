# Development Workflow

This document describes the development flow for the Music Genre Updater project, considering its dual-directory
architecture with iCloud sync and automated daemon deployment.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              iCloud Drive                                   │
│                                                                             │
│  ┌─────────────────────────────────┐    ┌─────────────────────────────────┐ │
│  │  Genres Autoupdater v2.0/       │    │  Genres Autoupdater v2.0-daemon/│ │
│  │  (Development)                  │    │  (Production)                   │ │
│  │                                 │    │                                 │ │
│  │  branch: dev (or feature/*)     │    │  branch: main (always)          │ │
│  │  .venv/ (local)                 │    │  .venv/ (local)                 │ │
│  │  my-config.yaml                 │◄───│  my-config.yaml (symlink)       │ │
│  │                                 │    │                                 │ │
│  │  You work here                  │    │  Daemon runs here               │ │
│  └─────────────────────────────────┘    └─────────────────────────────────┘ │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    │                               │
                    ▼                               ▼
        ┌───────────────────┐           ┌───────────────────┐
        │     Machine A     │           │     Machine B     │
        │                   │           │                   │
        │  ~/Library/App.   │           │  ~/Library/App.   │
        │  Support/Genre.   │           │  Support/Genre.   │
        │  (local state)    │           │  (local state)    │
        └───────────────────┘           └───────────────────┘
```

## Key Concepts

### Two Directories, One Codebase

| Directory      | Purpose     | Branch             | Who Uses It        |
|----------------|-------------|--------------------|--------------------|
| `v2.0/`        | Development | `dev`, `feature/*` | You (developer)    |
| `v2.0-daemon/` | Production  | `main`             | Daemon (automated) |

Both are **independent git clones** of the same repository, synced via iCloud.

### State Isolation

| What                      | Where                                | Synced?               |
|---------------------------|--------------------------------------|-----------------------|
| Source code               | iCloud                               | Yes                   |
| Config (`my-config.yaml`) | iCloud (in dev, symlinked to daemon) | Yes                   |
| Virtual env (`.venv/`)    | Each directory                       | No (machine-specific) |
| Lock/timestamp            | `~/Library/Application Support/`     | No (machine-specific) |
| Logs                      | `~/Library/Application Support/`     | No (machine-specific) |

## Development Flow

### Daily Development

```
┌─────────────────────────────────────────────────────────────────┐
│  1. DEVELOP                                                     │
│     cd "v2.0/"                                                  │
│     git checkout dev (or feature/your-feature)                  │
│     # make changes                                              │
│     uv run pytest                                               │
│     git commit                                                  │
│                                                                 │
│  2. PUSH                                                        │
│     git push origin dev                                         │
│                                                                 │
│  3. CREATE PR                                                   │
│     gh pr create --base main --head dev                         │
│                                                                 │
│  4. MERGE (on GitHub)                                           │
│     # Review, approve, merge                                    │
│                                                                 │
│  5. DAEMON AUTO-UPDATES                                         │
│     # On next Music Library change:                             │
│     # daemon does: git pull origin main                         │
└─────────────────────────────────────────────────────────────────┘
```

### Branch Strategy

```
main          ─────●─────────●─────────●─────────●───────▶
                   ▲         ▲         ▲         ▲
                   │ PR      │ PR      │ PR      │ PR
                   │         │         │         │
dev           ─●───●───●─────●───●─────●───●─────●───●───▶
               │       │         │         │
feature/x    ──●───●───┘         │         │
                                 │         │
feature/y    ────────────────●───┘         │
                                           │
hotfix/z     ──────────────────────────●───┘
```

**Rules:**

- `main` = production (daemon runs this)
- `dev` = integration branch
- `feature/*` = feature branches (merge to dev)
- `hotfix/*` = urgent fixes (can merge directly to main)

### Commit Convention

```bash
# Format: type(scope): description

feat(domain): add artist name normalization
fix(cache): prevent duplicate cache writes
refactor(api): extract scoring logic to separate module
docs: update development workflow
test(year): add edge case tests for year retrieval
chore: update dependencies
```

**Types:** `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `style`

## Common Workflows

### Starting a New Feature

```bash
# 1. Ensure dev is up to date
cd "~/Library/Mobile Documents/com~apple~CloudDocs/3. Git/Own/scripts/python/Genres Autoupdater v2.0"
git checkout dev
git pull origin dev

# 2. Create feature branch
git checkout -b feature/my-new-feature

# 3. Develop
# ... make changes ...
uv run pytest
uv run ruff check src/
uv run mypy src/

# 4. Commit
git add -A
git commit -m "feat(scope): description"

# 5. Push
git push origin feature/my-new-feature

# 6. Create PR to dev
gh pr create --base dev --head feature/my-new-feature
```

### Releasing to Production (Daemon)

```bash
# 1. Ensure dev is stable
cd "v2.0/"
git checkout dev
uv run pytest
uv run ruff check src/
uv run mypy src/

# 2. Create PR: dev → main
gh pr create --base main --head dev --title "Release: <description>"

# 3. Review and merge on GitHub

# 4. (Optional) Manually update daemon immediately
~/Library/Application\ Support/GenreUpdater/bin/update.sh

# Or wait for next Music Library change to trigger auto-update
```

### Hotfix (Urgent Production Fix)

```bash
# 1. Create hotfix from main
git checkout main
git pull origin main
git checkout -b hotfix/critical-bug

# 2. Fix and test
# ... make minimal fix ...
uv run pytest

# 3. Push and create PR directly to main
git push origin hotfix/critical-bug
gh pr create --base main --head hotfix/critical-bug

# 4. After merge, backport to dev
git checkout dev
git pull origin main
git push origin dev
```

### Testing Daemon Locally

```bash
# 1. Force daemon to run (bypass cooldown)
touch ~/Library/Application\ Support/GenreUpdater/state/cooldown_override

# 2. Trigger manually
launchctl kickstart -k gui/$(id -u)/com.music.genreautoupdater

# 3. Watch logs
tail -f ~/Library/Application\ Support/GenreUpdater/logs/daemon.log
```

### Updating Daemon Code Manually

```bash
# If you don't want to wait for auto-update after merging PR
~/Library/Application\ Support/GenreUpdater/bin/update.sh
```

## Multi-Machine Development

### Scenario: Working on Multiple Macs

Both machines sync via iCloud. Each has:

- Same code (synced)
- Same config (synced)
- Different `.venv/` (not synced, must set up on each)
- Different state/logs (not synced)

### First-Time Setup on New Machine

```bash
# 1. Wait for iCloud to sync (or manually trigger)

# 2. Set up dev venv
cd "~/Library/Mobile Documents/com~apple~CloudDocs/3. Git/Own/scripts/python/Genres Autoupdater v2.0"
uv venv --python 3.13
uv sync

# 3. Set up daemon venv
cd "../Genres Autoupdater v2.0-daemon"
uv venv --python 3.13
uv sync

# 4. Install daemon service
~/Library/Application\ Support/GenreUpdater/bin/install.sh
```

### Avoiding iCloud Conflicts

**DO:**

- Commit and push before switching machines
- Pull after switching machines
- Use feature branches for concurrent work

**DON'T:**

- Edit same files on two machines simultaneously
- Leave uncommitted changes when switching machines
- Sync `.venv/`, `.mypy_cache/`, `__pycache__/`

**If Conflicts Occur:**

```bash
# Find conflict files (iCloud creates "file 2" copies)
find . -name "* 2" -o -name "* 2.*"

# Remove them (they're duplicates)
find . \( -name "* 2" -o -name "* 2.*" \) -exec rm -rf {} +

# Recreate venv if corrupted
rm -rf .venv
uv venv --python 3.13
uv sync
```

## Testing Strategy

### Before Committing

```bash
# Quick validation
uv run pytest -x -q                    # Stop on first failure
uv run ruff check src/                 # Linting
uv run mypy src/ --no-error-summary    # Type checking
```

### Before Creating PR

```bash
# Full test suite
uv run pytest --cov=src --cov-report=html

# All linters
uv run ruff check src/
uv run ruff format --check src/
uv run mypy src/
uv run vulture src/ --min-confidence 80

# View coverage report
open reports/html/coverage/index.html
```

### Testing Specific Areas

```bash
# Unit tests only
uv run pytest tests/unit/ -v

# Integration tests
uv run pytest tests/integration/ -v

# Specific module
uv run pytest tests/unit/domain/tracks/ -v

# By marker
uv run pytest -m "not slow"
```

## CI/CD Flow

```
┌─────────────┐     push      ┌─────────────┐
│  Developer  │ ────────────▶ │   GitHub    │
└─────────────┘               └──────┬──────┘
                                     │
                              ┌──────▼──────┐
                              │  PR Checks  │
                              │  - pytest   │
                              │  - ruff     │
                              │  - mypy     │
                              └──────┬──────┘
                                     │
                              ┌──────▼──────┐
                              │   Merge     │
                              └──────┬──────┘
                                     │
                    ┌────────────────┴───────────────┐
                    │                                │
             ┌──────▼──────┐                  ┌──────▼──────┐
             │  Machine A  │                  │  Machine B  │
             │  Daemon     │                  │  Daemon     │
             │  git pull   │                  │  git pull   │
             │  on trigger │                  │  on trigger │
             └─────────────┘                  └─────────────┘
```

## Directory Structure Reference

```
Genres Autoupdater v2.0/          # Development directory
├── src/
│   ├── application/              # CLI, orchestrator, commands
│   ├── domain/                   # Business logic
│   ├── infrastructure/           # External integrations
│   └── shared/                   # Cross-cutting concerns
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── docs/
│   ├── plans/                    # Design documents
│   └── DEVELOPMENT_WORKFLOW.md   # This file
├── launchctl/                    # Daemon templates
├── applescripts/                 # AppleScript files
├── my-config.yaml                # Your config (gitignored)
├── config.yaml                   # Template config
└── pyproject.toml

Genres Autoupdater v2.0-daemon/   # Daemon directory
├── (same structure)
├── my-config.yaml → symlink      # Points to dev config
└── .venv/                        # Separate venv

~/Library/Application Support/GenreUpdater/
├── bin/
│   ├── run-daemon.sh             # Main wrapper
│   ├── install.sh                # Setup script
│   ├── update.sh                 # Manual update
│   └── notify.sh                 # Notifications
├── state/
│   ├── last_run.timestamp
│   ├── run.lock
│   └── cooldown_override
└── logs/
    ├── daemon.log
    ├── stdout.log
    └── stderr.log
```

## Quick Reference

### Everyday Commands

```bash
# Run tests
uv run pytest

# Lint
uv run ruff check src/ --fix

# Type check
uv run mypy src/

# Format
uv run ruff format src/

# Run app (dry run)
uv run python main.py --dry-run

# Run app (test mode)
uv run python main.py --test-mode
```

### Git Commands

```bash
# Status
git status

# Create feature branch
git checkout -b feature/name

# Commit
git add -A && git commit -m "type(scope): message"

# Push
git push origin branch-name

# Create PR
gh pr create --base dev --head feature/name

# Merge PR (after approval)
gh pr merge --squash
```

### Daemon Commands

```bash
# View logs
tail -f ~/Library/Application\ Support/GenreUpdater/logs/daemon.log

# Force run
touch ~/Library/Application\ Support/GenreUpdater/state/cooldown_override
launchctl kickstart -k gui/$(id -u)/com.music.genreautoupdater

# Manual update
~/Library/Application\ Support/GenreUpdater/bin/update.sh

# Service status
launchctl list | grep genreautoupdater
```

---

**Last updated:** 2025-01-25
