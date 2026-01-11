# API Reference

This section provides detailed API documentation for Music Genre Updater, automatically generated from source code docstrings.

## Module Overview

### Core Modules

The core business logic of the application:

| Module | Description |
|--------|-------------|
| [Genre Manager](core/genre_manager.md) | Manages genre assignments and dominant genre calculation |
| [Year Retriever](core/year_retriever.md) | Retrieves and validates album release years |
| [Track Processor](core/track_processor.md) | Processes tracks through the update pipeline |

### Services

External integrations and infrastructure:

| Module | Description |
|--------|-------------|
| [AppleScript Client](services/applescript.md) | Interface to Apple Music via AppleScript |
| [API Clients](services/api_clients.md) | External API integrations (MusicBrainz, Discogs, iTunes) |
| [Cache](services/cache.md) | Multi-tier caching system |

### Models

Data structures and protocols:

| Module | Description |
|--------|-------------|
| [Track Models](models/track.md) | Core data models for tracks and albums |
| [Protocols](models/protocols.md) | Interface definitions and type protocols |

## Architecture Principles

The API follows these design principles:

- **Async-first**: All I/O operations use `async/await`
- **Protocol-based**: Interfaces defined via `typing.Protocol`
- **Pydantic validation**: All external data validated with Pydantic v2
- **Dependency injection**: Services wired via `DependencyContainer`

## Quick Navigation

```python
# Core processing
from core.tracks.genre_manager import GenreManager
from core.tracks.year_retriever import YearRetriever
from core.tracks.track_processor import TrackProcessor

# Services
from services.applescript_client import AppleScriptClient
from services.api.orchestrator import ExternalApiOrchestrator
from services.cache.album_cache import AlbumCacheService

# Models
from core.models.track_models import Track, Album
from core.models.protocols import ExternalApiServiceProtocol
```
