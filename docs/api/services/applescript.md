# AppleScript Client

The AppleScript Client provides the interface to Apple Music via AppleScript execution.

## Overview

`AppleScriptClient` handles:

- Fetching tracks from the Music.app library
- Updating track metadata (genres, years)
- Managing AppleScript timeouts and retries
- Batch operations for efficiency

## Module Reference

::: services.apple.applescript_client
    options:
      show_root_heading: true
      heading_level: 2
      members_order: source
      show_source: true

## Dry Run Support

For testing without modifying the library:

::: core.dry_run
    options:
      show_root_heading: true
      heading_level: 2
      members_order: source
