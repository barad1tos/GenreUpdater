# 🎵 Music Genre Autoupdater Service

Automated service that refreshes album years using the MusicBrainz and Discogs APIs.

## 🚀 Quick Start

### Install the service

```bash
./manage_service.sh install
```

### Check status

```bash
./manage_service.sh status
```

### View logs

```bash
./manage_service.sh logs
```

## 📅 Schedule

- **When:** Every day at 2:00 AM
- **What:** Update album years for the entire music library
- **Duration:** 3-6 hours (depends on library size)

## 📊 Monitoring

### Logs live at

- **LaunchCtl logs:** `~/Library/Mobile Documents/com~apple~CloudDocs/4. Dev/MGU logs/launchctl/`
- **Primary logs:** `~/Library/Mobile Documents/com~apple~CloudDocs/4. Dev/MGU logs/main/`
- **Errors:**
  `~/Library/Mobile Documents/com~apple~CloudDocs/3. Git/Own/Python Scripts/Genres Autoupdater v2.0/error.log`
- **Analytics:**
  `~/Library/Mobile Documents/com~apple~CloudDocs/3. Git/Own/Python Scripts/Genres Autoupdater v2.0/analytics.log`

### Control commands

| Command                         | Description         |
| ------------------------------- | ------------------- |
| `./manage_service.sh install`   | Install the service |
| `./manage_service.sh uninstall` | Remove the service  |
| `./manage_service.sh start`     | Start immediately   |
| `./manage_service.sh stop`      | Stop the service    |
| `./manage_service.sh status`    | Show status         |
| `./manage_service.sh logs`      | Show logs           |
| `./manage_service.sh test`      | Dry-run test        |

## ⚙️ Configuration

### Key settings in `my-config.yaml`

```yaml
# Test artists (empty = entire library)
development:
  test_artists: [] # For production
  debug_mode: true

# API timeouts
applescript_timeouts:
  default: 3600 # 1 hour for full library
  single_artist_fetch: 600 # 10 minutes for a single artist
  full_library_fetch: 3600 # 1 hour for full library

# Batch processing
year_retrieval:
  processing:
    batch_size: 25
    delay_between_batches: 20
```

## 🔧 Technical Details

### System requirements

- macOS with the Music.app
- Python 3.12+ (via pyenv)
- Active Discogs and Last.fm API keys
- Internet connection

### Resource usage

- **Memory:** ~200 MB while running
- **CPU:** Low priority (`nice=10`)
- **Network:** ~1-2 API requests per second
- **Disk:** Logs and cache ~50-100 MB

### Security

- API keys encrypted in configuration
- Input validation for every entry point
- Automatic sanitisation of dangerous characters
- Timeout protection around all operations

## 🆘 Troubleshooting

### Service does not start

```bash
# Check status
./manage_service.sh status

# Inspect errors
./manage_service.sh logs

# Reinstall
./manage_service.sh uninstall
./manage_service.sh install
```

### Runtime errors

```bash
# Review main logs
tail -f "~/Library/Mobile Documents/com~apple~CloudDocs/4. Dev/MGU logs/main/main.log"

# Review errors
tail -f error.log

# Dry-run test
./manage_service.sh test
```

### Common issues

| Issue                 | Fix                                                  |
| --------------------- | ---------------------------------------------------- |
| AppleScript timeout   | Increase `applescript_timeouts` in the configuration |
| API rate limits       | Decrease `requests_per_second` in the configuration  |
| Out of disk space     | Clear cached files in the logs directory             |
| Music.app unavailable | Restart Music.app                                    |

## 📈 Performance Tuning

### For large libraries (>20K tracks)

```yaml
# Increase batch size
year_retrieval:
  processing:
    batch_size: 50
    delay_between_batches: 15

# Increase cache
caching:
  album_cache_max_entries: 100000
```

### For fast internet connections

```yaml
# Increase API throughput
year_retrieval:
  rate_limits:
    musicbrainz_requests_per_second: 2
    lastfm_requests_per_second: 10
```

## 🔄 Update Workflow

1. Stop the service: `./manage_service.sh stop`
2. Update the code
3. Verify configuration
4. Run the test: `./manage_service.sh test`
5. Restart: `./manage_service.sh start`

---

**Status:** ✅ Production-ready  
**Testing:** Passed successfully  
**Last update:** 2025-08-28
