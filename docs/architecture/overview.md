# Architecture Overview

Music Genre Updater follows a **clean architecture** pattern with clear separation of concerns.

## C4 Model Diagrams

### System Context (Level 1)

How the system interacts with external actors:

```mermaid
graph LR
    User((User))

    subgraph System["Music Genre Updater"]
        MGU[Application]
    end

    MusicApp[(Music.app)]
    MB[(MusicBrainz API)]
    DG[(Discogs API)]
    FS[(File System)]
    User -->|commands| MGU
    MGU -->|read tracks| MusicApp
    MGU -->|write updates| MusicApp
    MGU -->|query metadata| MB
    MGU -->|query metadata| DG
    MGU -->|cache/reports| FS
```

### Container Diagram (Level 2)

Main containers and data flow:

```mermaid
graph TB
    User((User))
    MusicApp[(Music.app)]
    ExtAPIs[(External APIs)]
    FileSystem[(File System)]

    subgraph System["Music Genre Updater"]
        CLI[CLI Parser]
        Orch[Orchestrator]
        Pipes[Pipelines]
        Core[Track Processor]
        Apple[AppleScript Client]
        Cache[Cache Service]
        APIs[API Clients]
        Metrics[Reports]
    end

    User -->|" --dry-run, --force "| CLI
    CLI -->|parsed args| Orch
    Orch -->|route command| Pipes
    Pipes -->|process tracks| Core
    Core -->|fetch/update| Apple
    Core -->|get metadata| APIs
    Core -->|read/write| Cache
    Pipes -->|generate| Metrics
    Apple <-->|AppleScript| MusicApp
    APIs -->|HTTP| ExtAPIs
    Cache <-->|JSON/pickle| FileSystem
    Metrics -->|HTML/CSV| FileSystem
```

## Data Flow Diagrams

### Genre Update Flow

```mermaid
sequenceDiagram
    participant U as User
    participant CLI as CLI
    participant O as Orchestrator
    participant P as Pipeline
    participant A as AppleScript
    participant M as Music.app
    participant C as Cache
    U ->> CLI: uv run python main.py
    CLI ->> O: parsed arguments
    O ->> A: fetch all tracks
    A ->> M: AppleScript query
    M -->> A: track data (30K+)
    A -->> O: List[TrackDict]
    O ->> C: check snapshot
    C -->> O: delta (changed tracks)
    O ->> P: process tracks
    Note over P: Dominant genre = genre from<br/>earliest added album
    P ->> A: update genre
    A ->> M: AppleScript set
    M -->> A: success
    P -->> O: changes made
    O -->> CLI: summary report
```

### Year Update Flow

```mermaid
sequenceDiagram
    participant P as Pipeline
    participant API as API Orchestrator
    participant MB as MusicBrainz
    participant DG as Discogs
    participant C as Cache
    participant A as AppleScript
    P ->> C: check cached year
    alt cache hit
        C -->> P: cached year + confidence
    else cache miss
        P ->> API: fetch year (artist, album)
        par query all APIs
            API ->> MB: search release
            API ->> DG: search release
        end
        MB -->> API: year + score
        DG -->> API: year + score
        API ->> API: resolve best year (scoring)
        API -->> P: year + confidence
        P ->> C: store in cache
    end
    P ->> A: update year
```

## Component Diagrams

### App Layer (`src/app/`)

```mermaid
graph LR
    subgraph Entry["Entry Point"]
        CLI[cli.py]
        Orch[orchestrator.py]
    end

    subgraph Pipelines["Processing Pipelines"]
        MU[music_updater]
        FS[full_sync]
        YU[year_update]
        TC[track_cleaning]
    end

    subgraph Features["Feature Modules"]
        Batch[batch/processor]
        Crypto[crypto/encryption]
        Verify[verify/database]
    end

    CLI -->|args| Orch
    Orch -->|genre+year| MU
    Orch -->|full library| FS
    Orch -->|years only| YU
    Orch -->|clean metadata| TC
    Orch -->|batch/crypto/verify| Features
```

### Core Layer (`src/core/`)

Business logic for track processing:

```mermaid
graph TB
    subgraph Input["Input"]
        IN[TrackDict from AppleScript]
    end

    subgraph Processing["tracks/"]
        TP[track_processor]
        GM[genre_manager]
        YR[year_retriever]
        AR[artist_renamer]
        IF[incremental_filter]
        UE[update_executor]
    end

    subgraph Output["Output"]
        OUT[Updated TrackDict]
    end

    IN -->|raw tracks| IF
    IF -->|filtered delta| TP
    TP -->|artist tracks| GM
    GM -->|dominant genre| TP
    TP -->|album info| YR
    YR -->|release year| TP
    TP -->|dirty names| AR
    AR -->|clean names| TP
    TP -->|changes| UE
    UE -->|execute| OUT
```

### Services Layer (`src/services/`)

I/O adapters and external integrations:

```mermaid
graph TB
    subgraph Callers["From Core Layer"]
        Core[Track Processor]
    end

    subgraph Apple["apple/"]
        AC[applescript_client]
        AE[executor]
        RL[rate_limiter]
    end

    subgraph Cache["cache/"]
        CO[orchestrator]
        SS[snapshot]
        ALB[album_cache]
        API_C[api_cache]
    end

    subgraph APIs["api/"]
        AO[orchestrator]
        MB[musicbrainz]
        DG[discogs]
        YS[year_scoring]
    end

    subgraph External["External Systems"]
        MusicApp[(Music.app)]
        ExtAPI[(HTTP APIs)]
        Files[(File System)]
    end

    Core -->|fetch/update tracks| AC
    AC --> AE --> RL
    RL -->|AppleScript| MusicApp
    Core -->|get/set cache| CO
    CO --> SS & ALB & API_C
    SS & ALB & API_C -->|read/write| Files
    Core -->|query metadata| AO
    AO --> MB & DG
    AO --> YS
    MB & DG -->|HTTP| ExtAPI
```

### Metrics Layer (`src/metrics/`)

Observability and reporting:

```mermaid
graph LR
    subgraph Input["From Pipelines"]
        Data[Processing Results]
    end

    subgraph Analytics["Tracking"]
        AN[analytics]
        MO[monitoring]
    end

    subgraph Reports["Generation"]
        HR[html_reports]
        CR[change_reports]
        ER[error_reports]
    end

    subgraph Output["To File System"]
        HTML[reports/*.html]
        CSV[reports/*.csv]
    end

    Data --> AN & MO
    AN --> HR & CR
    MO --> ER
    HR --> HTML
    CR & ER --> CSV
```

## Directory Structure

```text
src/
├── app/                        # Presentation layer
│   ├── cli.py                  # CLI argument parsing
│   ├── orchestrator.py         # Command routing
│   ├── *_update.py             # Pipeline modules (music, genre, year, full_sync)
│   ├── track_cleaning.py       # Metadata cleanup
│   └── features/               # Feature modules
│       ├── batch/              # Batch processing
│       ├── crypto/             # API key encryption
│       └── verify/             # Database verification
│
├── core/                       # Business logic
│   ├── analytics_decorator.py  # Standalone track_instance_method
│   ├── core_config.py          # Configuration loading
│   ├── logger.py               # Logging setup
│   ├── dry_run.py              # Dry-run simulation
│   ├── models/                 # Data models, protocols, cache types
│   ├── tracks/                 # Track processing (processor, genre, year)
│   └── utils/                  # Shared utilities
│
├── services/                   # External integrations
│   ├── dependency_container.py # DI container
│   ├── apple/                  # Music.app AppleScript integration
│   ├── api/                    # External APIs (MusicBrainz, Discogs, etc.)
│   └── cache/                  # Multi-tier caching (snapshot, album, API)
│
└── metrics/                    # Analytics & reporting
    └── *.py                    # Reports (HTML, CSV, analytics)
```

## Layer Responsibilities

| Layer        | Path            | What it does                                                           |
|--------------|-----------------|------------------------------------------------------------------------|
| **App**      | `src/app/`      | Entry point, command routing, pipeline selection                       |
| **Core**     | `src/core/`     | Business logic: genre calculation, year determination, track filtering |
| **Services** | `src/services/` | I/O adapters: AppleScript, cache, external API clients                 |
| **Metrics**  | `src/metrics/`  | Observability: timing, reports, error tracking                         |

## Key Design Patterns

### Dependency Injection

All services are wired via `DependencyContainer`:

```python test="skip"
container = DependencyContainer(config, logger)
await container.initialize()

# Services available
genre_manager = container.genre_manager
year_retriever = container.year_retriever
```

### Protocol-Based Interfaces

Interfaces defined with `typing.Protocol` in `core/models/protocols.py`:

- `CacheServiceProtocol` — unified cache operations
- `ExternalApiServiceProtocol` — external API clients
- `AppleScriptClientProtocol` — Music.app communication
- `PendingVerificationServiceProtocol` — verification queue
- `AnalyticsProtocol` — wrapped call execution and batch mode
- `LibrarySnapshotServiceProtocol` — snapshot persistence

`core/` depends only on protocols, never on concrete service classes.
The `track_instance_method` decorator in `core/analytics_decorator.py`
uses duck typing (MRO-based method lookup) to avoid importing the
concrete `Analytics` class. When analytics is missing on a decorated
instance, the wrapper logs an error and falls back to untracked execution.
Test factories use `cast(Protocol, cast(object, mock))` to satisfy strict
type checkers when passing mock objects as protocol-typed parameters.

```python test="skip"
class ExternalApiServiceProtocol(Protocol):
    async def get_album_year(
            self, artist: str, album: str, ...
    ) -> tuple[str | None, bool, int, dict]: ...
```

### Configuration Type Safety

All YAML config sections have corresponding Pydantic v2 models in
`core/models/track_models.py`. The root model `AppConfig` validates
every config section at load time, catching typos and type mismatches
before they reach runtime:

| Config Section              | Pydantic Model                |
|-----------------------------|-------------------------------|
| `processing`                | `ProcessingConfig`            |
| `logic`                     | `LogicConfig`                 |
| `scoring`                   | `ScoringConfig`               |
| `caching`                   | `CachingConfig`               |
| `caching.library_snapshot`  | `LibrarySnapshotConfig`       |
| `year_retrieval`            | `YearRetrievalConfig`         |
| `analytics`                 | `AnalyticsConfig`             |
| `database_verification`     | `DatabaseVerificationConfig`  |
| `development`               | `DevelopmentConfig`           |
| `applescript_timeouts`      | `ApplescriptTimeoutsConfig`   |
| `apple_script_rate_limit`   | `AppleScriptRateLimitConfig`  |
| `album_type_detection`      | `AlbumTypeDetectionConfig`    |
| `batch_processing`          | `BatchProcessingConfig`       |
| `experimental`              | `ExperimentalConfig`          |

### Async-First

All I/O operations use `async/await`:

```python test="skip"
async def process_tracks(self, tracks: list[Track]) -> None:
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*[
            self.process_track(track, session)
            for track in tracks
        ])
```

## AppleScript Integration

Scripts in `applescripts/` directory (canonical names defined in `core/apple_script_names.py`):

| Script                            | Purpose                              | Output Format                                    |
|-----------------------------------|--------------------------------------|--------------------------------------------------|
| `fetch_tracks.applescript`        | Get all tracks or filtered by artist | ASCII-delimited: `\x1E` (field), `\x1D` (record) |
| `fetch_track_ids.applescript`     | Get all track IDs                    | Comma-separated IDs                              |
| `fetch_tracks_by_ids.applescript` | Get specific tracks by ID list       | Same as `fetch_tracks`                           |
| `update_property.applescript`     | Set single track property            | "Success: ..." or "No Change: ..."               |
| `batch_update_tracks.applescript` | Batch updates (experimental)         | JSON status array                                |

## Error Handling

Errors categorized by recoverability:

| Category   | Action             |
|------------|--------------------|
| Transient  | Retry with backoff |
| Rate Limit | Wait and retry     |
| Not Found  | Log and skip       |
| Permanent  | Fail fast          |

## Testing Strategy

```
tests/
├── unit/          # Fast, isolated tests
├── integration/   # Service tests with real cache
└── e2e/          # Full tests with Music.app
```

Tests run with `pytest-xdist` (parallel workers). Module-level singletons
like `album_type._configured_patterns` require `reset_patterns()` autouse
fixtures to prevent cross-worker state pollution.
