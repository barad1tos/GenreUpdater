# API Clients

External API integrations for fetching album metadata from MusicBrainz, Discogs, and iTunes.

## Overview

The API client system provides:

- Multiple data source support (MusicBrainz, Discogs, iTunes/Apple Music)
- Intelligent rate limiting per API
- Result scoring and confidence calculation
- Reissue detection

## API Orchestrator

Coordinates queries across all API providers:

::: services.api.orchestrator
    options:
      show_root_heading: true
      heading_level: 2
      members_order: source
      show_source: true

## Base Client

Common functionality for all API clients:

::: services.api.api_base
    options:
      show_root_heading: true
      heading_level: 2
      members_order: source

## MusicBrainz Client

Primary metadata source with release group support:

::: services.api.musicbrainz
    options:
      show_root_heading: true
      heading_level: 2
      members_order: source
      filters:
        - "!^_"
        - "!TypedDict"

## Discogs Client

Secondary source with detailed release information:

::: services.api.discogs
    options:
      show_root_heading: true
      heading_level: 2
      members_order: source

## iTunes/Apple Music Client

Apple's catalog for verification:

::: services.api.applemusic
    options:
      show_root_heading: true
      heading_level: 2
      members_order: source

## Year Score Resolver

Resolves final year from multiple API responses:

::: services.api.year_score_resolver
    options:
      show_root_heading: true
      heading_level: 2
      members_order: source
