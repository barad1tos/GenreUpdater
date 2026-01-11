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
    classDef external fill:#F28779, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef system fill:#73D0FF, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef user fill:#BAE67E, stroke:#1F2430, stroke-width:2px, color:#1F2430
    class MusicApp,MB,DG,FS external
    class MGU system
    class User user
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
    classDef external fill:#F28779, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef internal fill:#73D0FF, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef user fill:#BAE67E, stroke:#1F2430, stroke-width:2px, color:#1F2430
    class MusicApp,ExtAPIs,FileSystem external
    class CLI,Orch,Pipes,Core,Apple,Cache,APIs,Metrics internal
    class User user
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
    classDef entry fill:#73D0FF, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef pipeline fill:#5BC0EB, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef feature fill:#95E6CB, stroke:#1F2430, stroke-width:2px, color:#1F2430
    class CLI,Orch entry
    class MU,FS,YU,TC pipeline
    class Batch,Crypto,Verify feature
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
    classDef io fill:#F28779, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef proc fill:#FFD580, stroke:#1F2430, stroke-width:2px, color:#1F2430
    class IN,OUT io
    class TP,GM,YR,AR,IF,UE proc
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
    classDef caller fill:#FFD580, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef apple fill:#D4BFFF, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef cache fill:#CE93D8, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef api fill:#BA68C8, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef external fill:#F28779, stroke:#1F2430, stroke-width:2px, color:#1F2430
    class Core caller
    class AC,AE,RL apple
    class CO,SS,ALB,API_C cache
    class AO,MB,DG,YS api
    class MusicApp,ExtAPI,Files external
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
    classDef io fill:#F28779, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef track fill:#BAE67E, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef report fill:#C5E1A5, stroke:#1F2430, stroke-width:2px, color:#1F2430
    class Data,HTML,CSV io
    class AN,MO track
    class HR,CR,ER report
```

## Directory Structure

```
src/
├── app/                    # Presentation layer
│   ├── cli.py             # Command-line interface
│   ├── orchestrator.py    # Command routing
│   └── features/          # Feature modules
│
├── core/                   # Business logic
│   ├── models/            # Pydantic data models
│   │   ├── track_models.py
│   │   ├── protocols.py
│   │   └── validators.py
│   ├── tracks/            # Track processing
│   │   ├── genre_manager.py
│   │   ├── year_retriever.py
│   │   └── track_processor.py
│   └── utils/             # Shared utilities
│
├── services/              # External integrations
│   ├── applescript_client.py
│   ├── api/               # API clients
│   │   ├── orchestrator.py
│   │   ├── musicbrainz.py
│   │   ├── discogs.py
│   │   └── applemusic.py
│   ├── cache/             # Caching
│   │   ├── album_cache.py
│   │   └── snapshot.py
│   └── dependency_container.py
│
└── metrics/               # Analytics & reporting
    ├── analytics.py
    └── html_reports.py
```

## Layer Responsibilities

| Layer | Path | What it does |
|-------|------|--------------|
| **App** | `src/app/` | Entry point, command routing, pipeline selection |
| **Core** | `src/core/` | Business logic: genre calculation, year determination, track filtering |
| **Services** | `src/services/` | I/O adapters: AppleScript, cache, external API clients |
| **Metrics** | `src/metrics/` | Observability: timing, reports, error tracking |

## Key Design Patterns

### Dependency Injection

All services are wired via `DependencyContainer`:

```python
container = DependencyContainer(config, logger)
await container.initialize()

# Services available
genre_manager = container.genre_manager
year_retriever = container.year_retriever
```

### Protocol-Based Interfaces

Interfaces defined with `typing.Protocol`:

```python
class ExternalApiServiceProtocol(Protocol):
    async def fetch_year(
        self, artist: str, album: str
    ) -> tuple[int | None, int]: ...
```

### Async-First

All I/O operations use `async/await`:

```python
async def process_tracks(self, tracks: list[Track]) -> None:
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*[
            self.process_track(track, session)
            for track in tracks
        ])
```

## AppleScript Integration

Scripts in `applescripts/` directory:

| Script | Purpose | Output Format |
|--------|---------|---------------|
| `fetch_tracks.scpt` | Get all tracks or filtered by artist | ASCII-delimited: `\x1E` (field), `\x1D` (record) |
| `fetch_tracks_by_ids.scpt` | Get specific tracks by ID list | Same format |
| `update_property.applescript` | Set single track property | "Success: ..." or "No Change: ..." |
| `batch_update_tracks.applescript` | Batch updates (experimental) | JSON status array |

## Error Handling

Errors categorized by recoverability:

| Category | Action |
|----------|--------|
| Transient | Retry with backoff |
| Rate Limit | Wait and retry |
| Not Found | Log and skip |
| Permanent | Fail fast |

## Testing Strategy

```
tests/
├── unit/          # Fast, isolated tests
├── integration/   # Service tests with real cache
└── e2e/          # Full tests with Music.app
```
