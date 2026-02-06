# Troubleshooting

Solutions for common issues when running Music Genre Updater.

## Common Issues

| Problem                             | Solution                                                                          |
|-------------------------------------|-----------------------------------------------------------------------------------|
| "Music app is not running"          | Launch Music.app before running (except `rotate_keys`)                            |
| AppleScript timeout                 | Increase `applescript_timeouts` values in config                                  |
| Cache corruption                    | Delete `cache/` directory and re-run                                              |
| Parse failures in batch             | Reduce `batch_processing.ids_batch_size` (try 100)                                |
| "Permission denied" for AppleScript | Grant Terminal/IDE access in System Preferences → Security → Privacy → Automation |
| Year fetching returns wrong year    | Check if album is remastered; use `revert_years` to fix                           |

## Diagnostic Commands

```bash
# Check Python version (must be 3.13+)
python3 --version

# Test AppleScript connection
osascript applescripts/fetch_tracks.applescript "" 0 10  # Fetch first 10 tracks

# Check launch agent status
launchctl list | grep genreautoupdater

# Watch logs in real-time
tail -f /path/to/logs/main/main.log

# Check for errors
grep -i error /path/to/logs/main/main.log | tail -20

# Verify database integrity
uv run python main.py verify_database

# Check pending year lookups
uv run python main.py verify_pending
```

## Log File Locations

All paths relative to `logs_base_dir` in config:

| File                               | Contents                                |
|------------------------------------|-----------------------------------------|
| `main/main.log`                    | Main application log (INFO level)       |
| `main/year_changes.log`            | Year update decisions and API responses |
| `csv/track_list.csv`               | Full track listing from last run        |
| `csv/changes_report.csv`           | All changes made (for revert)           |
| `csv/dry_run_report.csv`           | Changes that would be made (dry-run)    |
| `analytics/analytics.log`          | Function timing and call counts         |
| `analytics/reports/analytics.html` | Visual performance dashboard            |

## Performance Issues

### Slow Library Scan

If initial library scan is slow (>30 seconds for 30K tracks):

1. **Enable snapshot caching**:
   ```yaml
   library_snapshot:
     enabled: true
     delta_enabled: true
   ```

2. **Reduce batch size** if parse failures occur:
   ```yaml
   batch_processing:
     ids_batch_size: 100  # Default: 200
   ```

### High Memory Usage

For very large libraries (50K+ tracks):

1. **Enable cleanup intervals**:
   ```yaml
   caching:
     cleanup_interval_seconds: 300
   ```

2. **Disable analytics during batch runs**:
   ```yaml
   analytics:
     enabled: false
   ```

## API Rate Limiting

If you see rate limit errors:

```yaml
year_retrieval:
  rate_limits:
    discogs_requests_per_minute: 25  # Reduce from 55
    musicbrainz_requests_per_second: 0.5  # Reduce from 1
    concurrent_api_calls: 1  # Reduce from 2
```

## Getting Help

1. Check logs for specific error messages
2. Run with `--verbose` for detailed output
3. Use `--dry-run` to test without making changes
4. [Open an issue](https://github.com/barad1tos/GenreUpdater/issues) with log excerpts
