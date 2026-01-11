# Cache Services

Multi-tier caching system for performance optimization.

## Overview

The caching system provides:

- In-memory caching for hot data
- Disk-based persistence for album years
- Library snapshots for fast startup
- Negative result caching to avoid repeated API calls

## Album Cache Service

Persistent cache for album year data:

::: services.cache.album_cache
    options:
      show_root_heading: true
      heading_level: 2
      members_order: source
      show_source: true

## Hash Service

Unified cache key generation:

::: services.cache.hash_service
    options:
      show_root_heading: true
      heading_level: 2
      members_order: source

## Cache Manager

Track-level cache coordination:

::: core.tracks.cache_manager
    options:
      show_root_heading: true
      heading_level: 2
      members_order: source
