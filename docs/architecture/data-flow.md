# Data Flow

How data moves through Music Genre Updater from input to output.

## High-Level Flow

```mermaid
sequenceDiagram
    participant User
    participant CLI
    participant Orchestrator
    participant TrackProcessor
    participant AppleScript
    participant Music as Music.app
    participant APIs as External APIs

    User->>CLI: python main.py
    CLI->>Orchestrator: parse args
    Orchestrator->>TrackProcessor: process()

    TrackProcessor->>AppleScript: fetch_tracks()
    AppleScript->>Music: run script
    Music-->>AppleScript: track data
    AppleScript-->>TrackProcessor: Track[]

    loop For each album
        TrackProcessor->>APIs: fetch_year()
        APIs-->>TrackProcessor: year, score
    end

    TrackProcessor->>AppleScript: update_tracks()
    AppleScript->>Music: run script
    Music-->>AppleScript: success

    TrackProcessor-->>Orchestrator: results
    Orchestrator-->>CLI: exit code
    CLI-->>User: output
```

## Track Fetching

### From Music.app

```mermaid
flowchart LR
    A[AppleScript] --> B[Music.app]
    B --> C[Raw Output]
    C --> D[Parser]
    D --> E[Track Objects]
```

AppleScript returns delimited text:

```
Artist\x1DAlbum\x1DTrack\x1DGenre\x1DYear\x1E
Artist2\x1DAlbum2\x1D...
```

- `\x1D` = field separator
- `\x1E` = record separator

### Parsing Pipeline

```python test="skip"
raw_output: str
    → split by '\x1E'
    → for each record: split by '\x1D'
    → validate with Pydantic
    → Track objects
```

## Year Retrieval Flow

```mermaid
flowchart TD
    A[Album] --> B{In Cache?}
    B -->|Yes| C[Return Cached]
    B -->|No| D[Query APIs]

    D --> E[MusicBrainz]
    D --> F[Discogs]
    D --> G[iTunes]

    E --> H[Score Results]
    F --> H
    G --> H

    H --> I{Score >= 70?}
    I -->|Yes| J[Apply Year]
    I -->|No| K[Mark Pending]

    J --> L[Update Cache]
```

## Incremental Processing

Only process recently changed tracks:

```mermaid
flowchart LR
    A[All Tracks] --> B{Modified Since Last Run?}
    B -->|Yes| C[Process]
    B -->|No| D[Skip]
```

### Modification Detection

```python test="skip"
last_run = load_last_run_timestamp()
for track in tracks:
    if track.date_modified > last_run:
        yield track
```

## Caching Layers

```mermaid
flowchart TB
    subgraph "Layer 1: Memory"
        MC[In-Memory Cache]
    end

    subgraph "Layer 2: Disk"
        AC[Album Cache JSON]
        SC[Library Snapshot]
    end

    subgraph "Layer 3: Source"
        API[External APIs]
        Music[Music.app]
    end

    MC --> AC
    AC --> API
    SC --> Music
```

### Cache Priorities

1. **Memory**: Hot data, TTL 30 min
2. **Album Cache**: Year data, TTL 100 years (immutable)
3. **Library Snapshot**: Full track list, TTL 24 hours
4. **Negative Cache**: "Not found" results, TTL 30 days

## Batch Processing

Large operations use batching to avoid timeouts:

```mermaid
flowchart LR
    A[30K Tracks] --> B[Batch 1: 200]
    A --> C[Batch 2: 200]
    A --> D[...]
    A --> E[Batch 150: 200]

    B --> F[Process]
    C --> F
    D --> F
    E --> F

    F --> G[Aggregate Results]
```

### Batch Sizes

| Operation | Default Size | Configurable |
|-----------|--------------|--------------|
| Track Fetch | 200 | `ids_batch_size` |
| Year Update | 25 | `batch_size` |
| Genre Update | 50 | `batch_size` |

## Update Pipeline

```mermaid
flowchart TD
    A[Changed Track] --> B{Has Genre?}
    B -->|No| C[Calculate Dominant]
    B -->|Yes| D{Has Year?}

    C --> D

    D -->|No| E[Fetch from APIs]
    D -->|Yes| F{Year Valid?}

    E --> F

    F -->|Yes| G[Queue Update]
    F -->|No| H[Mark Pending]

    G --> I[Batch Updates]
    I --> J[AppleScript Execute]
    J --> K[Log Changes]
```

## Error Recovery

```mermaid
flowchart TD
    A[API Call] --> B{Success?}
    B -->|Yes| C[Process Result]
    B -->|No| D{Retriable?}

    D -->|Yes| E[Wait]
    E --> F[Retry]
    F --> A

    D -->|No| G{Rate Limited?}
    G -->|Yes| H[Long Wait]
    H --> A

    G -->|No| I[Log Error]
    I --> J[Skip Item]
```

### Retry Policy

| Error Type | Retries | Backoff |
|------------|---------|---------|
| Network | 3 | Exponential |
| Rate Limit | ∞ | Fixed 60s |
| Not Found | 0 | N/A |
| Server Error | 2 | Linear |
