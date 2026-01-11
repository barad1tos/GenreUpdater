# Protocols

Interface definitions using `typing.Protocol` for dependency injection and testing.

## Overview

Protocol-based design enables:

- Clean dependency injection
- Easy mocking in tests
- Clear interface contracts
- Runtime structural subtyping

## Service Protocols

::: core.models.protocols
    options:
      show_root_heading: true
      heading_level: 2
      members_order: source
      show_source: true

## Dependency Container

Service wiring and lifecycle management:

::: services.dependency_container
    options:
      show_root_heading: true
      heading_level: 2
      members_order: source

## Search Strategy

API search strategy selection:

::: core.models.search_strategy
    options:
      show_root_heading: true
      heading_level: 2
      members_order: source
