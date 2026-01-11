# Year Retriever

The Year Retriever fetches and validates album release years from external APIs.

## Overview

`YearRetriever` coordinates:

- Querying multiple APIs (MusicBrainz, Discogs, iTunes)
- Scoring and validating year candidates
- Handling reissue detection and year conflicts

## Module Reference

::: core.tracks.year_retriever
    options:
      show_root_heading: true
      heading_level: 2
      members_order: source
      show_source: true

## Related Modules

### Year Determination

Logic for determining the best year from multiple candidates:

::: core.tracks.year_determination
    options:
      show_root_heading: true
      heading_level: 3
      members_order: source

### Year Utilities

Helper functions for year validation:

::: core.tracks.year_utils
    options:
      show_root_heading: true
      heading_level: 3
      members_order: source
