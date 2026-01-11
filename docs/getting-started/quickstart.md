# Quick Start

This guide walks you through your first genre update.

## 1. Configure Paths

Copy the template config and edit it:

```bash
cp config.yaml my-config.yaml
```

Edit `my-config.yaml` with your paths:

```yaml
music_library_path: /Users/YOUR_USERNAME/Music/Music/Music Library.musiclibrary
apple_scripts_dir: /path/to/GenreUpdater/applescripts
logs_base_dir: /path/to/logs
```

!!! warning "Important"
    Never commit `my-config.yaml` — it contains your personal paths.

## 2. Preview Changes (Dry Run)

Always preview before making changes:

```bash
uv run python main.py --dry-run
```

This shows what would change without modifying your library.

## 3. Apply Changes

If the preview looks good:

```bash
uv run python main.py
```

## 4. Check Results

View the changes report:

```bash
cat logs/csv/changes_report.csv
```

## Common Workflows

### Update Only Years

```bash
uv run python main.py update_years
```

### Update Specific Artist

```bash
uv run python main.py update_years --artist "Pink Floyd"
```

### Clean Metadata Clutter

```bash
uv run python main.py clean_artist --artist "Metallica"
```

### Revert Year Changes

```bash
uv run python main.py revert_years --artist "Otep" --album "The God Slayer"
```

## What's Next?

- [CLI Commands](../guide/cli.md) — All available commands
- [Configuration](configuration.md) — Advanced settings
- [Automation](../guide/automation.md) — Run on schedule with launchctl
