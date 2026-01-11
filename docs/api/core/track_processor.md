# Track Processor

The Track Processor orchestrates the full track update pipeline.

## Overview

`TrackProcessor` manages:

- Fetching tracks from Apple Music library
- Filtering tracks for incremental updates
- Coordinating genre and year updates
- Batch processing for large libraries

## Module Reference

::: core.tracks.track_processor
    options:
      show_root_heading: true
      heading_level: 2
      members_order: source
      show_source: true

## Related Modules

### Batch Fetcher

Efficient batch fetching of track data:

::: core.tracks.batch_fetcher
    options:
      show_root_heading: true
      heading_level: 3
      members_order: source

### Incremental Filter

Filtering for recently modified tracks:

::: core.tracks.incremental_filter
    options:
      show_root_heading: true
      heading_level: 3
      members_order: source

### Update Executor

Executes the actual updates via AppleScript:

::: core.tracks.update_executor
    options:
      show_root_heading: true
      heading_level: 3
      members_order: source
