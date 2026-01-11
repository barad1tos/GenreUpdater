# Frequently Asked Questions

## How do I change the update frequency?

Two settings control this:

1. **launchctl interval** — `StartInterval` in plist (seconds between runs)
2. **Incremental filter** — `incremental_interval_minutes` in config.yaml (skip tracks not modified recently)

**Example:** Run every hour, but only process tracks modified in last 30 minutes:

- plist: `<integer>3600</integer>` (1 hour)
- config: `incremental_interval_minutes: 30`

See [Automation](automation.md) for detailed launchctl setup.

## How can I see what was changed?

Check `<logs_base_dir>/csv/changes_report.csv`. It contains:

- Track ID, name, artist, album
- Old and new values for genre/year
- Timestamp of change

Use this file with `revert_years` command to undo changes:

```bash
uv run python main.py revert_years --artist "Artist" --backup-csv path/to/changes_report.csv
```

## Can I exclude specific artists or albums?

Yes, add to `exceptions.track_cleaning` in your config file:

```yaml
exceptions:
  track_cleaning:
    - artist: "Weird Al Yankovic"  # Skip all albums
    - artist: "Pink Floyd"
      album: "The Wall"            # Skip specific album only
```

## Why are some years wrong?

Year determination uses scoring from 3 APIs (MusicBrainz, Discogs, iTunes). Sometimes:

- APIs disagree on the release year
- Remastered versions have different years
- Regional releases vary

**To fix:**

1. Use `revert_years` to restore the original year
2. Add the album to exceptions if it keeps getting the wrong year
3. Use `restore_release_years` to use Apple's built-in release year data

```bash
# Revert specific album
uv run python main.py revert_years --artist "Pink Floyd" --album "The Wall"

# Restore from Apple's release_year field
uv run python main.py restore_release_years --artist "Pink Floyd"
```

## Is it safe to run on my library?

Yes, with caveats:

1. **Always run `--dry-run` first** to preview changes
2. **Changes sync to iCloud immediately** and cannot be undone via Time Machine
3. **Use `revert_years`** with `changes_report.csv` to undo year changes
4. Genre changes have no built-in revert (restore from backup if needed)

!!! warning "Important"
If you use iCloud Music Library, changes propagate immediately to all devices. There is no "undo" button.

## How does genre determination work?

The app uses the **"dominant genre"** concept:

1. For each artist, find the **earliest added album** in your library
2. Use that album's genre as the "dominant" genre
3. Apply it to all tracks by that artist

**Why earliest album?** The assumption is that your first album by an artist has the genre you intended when you added
it to your library.

!!! tip "Set the right genre first"
Before running, make sure your earliest album for each artist has the correct genre. All other albums will inherit it.

## What APIs does it use for year lookup?

Three external APIs:

| API               | What it provides                      |
|-------------------|---------------------------------------|
| **MusicBrainz**   | Original release year, high accuracy  |
| **Discogs**       | Release year, good for vinyl/reissues |
| **iTunes Search** | Apple's own release data              |

The app queries all three and uses a scoring system to pick the most likely correct year.

## Can I run it headless (without GUI)?

Yes, via launchctl daemon. See [Automation](automation.md) for setup.

Requirements:

- Music.app must be running (even in background)
- Terminal/script must have Automation permissions

## How do I update the app?

```bash
cd /path/to/GenreUpdater
git pull
uv sync  # or pip install -e .
```

If you use launchctl, restart the daemon:

```bash
launchctl unload ~/Library/LaunchAgents/com.music.genreautoupdater.plist
launchctl load ~/Library/LaunchAgents/com.music.genreautoupdater.plist
```
