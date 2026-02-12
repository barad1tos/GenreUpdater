"""Pydantic models for configuration and data validation."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, TypedDict, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Type checking improvements for better IDE support and type safety
T = TypeVar("T")

# Type alias for TrackDict field values - covers all possible field types
TrackFieldValue = str | int | None


# TypedDict for MusicBrainz artist structure/
class MBArtist(TypedDict, total=False):
    """Type definition for MusicBrainz artist data structure."""

    id: str
    name: str
    sort_name: str | None
    type: str | None
    disambiguation: str | None


# TypedDict for MusicBrainz artist credit structure
class MBArtistCredit(TypedDict, total=False):
    """Type definition for MusicBrainz artist credit data structure."""

    artist: MBArtist
    name: str | None
    joinphrase: str | None


class LogLevel(StrEnum):
    """Log level enumeration."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    NOTSET = "NOTSET"


class PreferredApi(StrEnum):
    """Preferred API enumeration."""

    MUSICBRAINZ = "musicbrainz"
    DISCOGS = "discogs"
    ITUNES = "itunes"


class ScriptType(StrEnum):
    """Script type enumeration for API prioritization."""

    CYRILLIC = "cyrillic"
    LATIN = "latin"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ChangeDisplayMode(StrEnum):
    """Change display mode enumeration."""

    COMPACT = "compact"
    TABLE = "table"


class PythonSettings(BaseModel):
    """Python environment settings."""

    prevent_bytecode: bool


class AppleScriptTimeoutsConfig(BaseModel):
    """Per-operation AppleScript timeout overrides."""

    default: int = Field(default=3600, ge=1)
    full_library_fetch: int = Field(default=3600, ge=1)
    single_artist_fetch: int = Field(default=600, ge=1)
    batch_update: int = Field(default=1800, ge=1)
    ids_batch_fetch: int = Field(default=120, ge=1)


class AppleScriptRateLimitConfig(BaseModel):
    """AppleScript operation rate limiter settings."""

    enabled: bool = False
    requests_per_window: int = Field(default=10, ge=1)
    window_size_seconds: float = Field(default=1.0, gt=0)


class ExperimentalConfig(BaseModel):
    """Experimental feature toggles."""

    batch_updates_enabled: bool = False
    max_batch_size: int = Field(default=5, ge=1)


class AppleScriptRetryConfig(BaseModel):
    """AppleScript operation retry policy."""

    max_retries: int = Field(default=3, ge=0)
    base_delay_seconds: float = Field(default=1.0, ge=0)
    max_delay_seconds: float = Field(default=10.0, ge=0)
    jitter_range: float = Field(default=0.2, ge=0, le=1)
    operation_timeout_seconds: float = Field(default=60.0, ge=0)


class BatchProcessingConfig(BaseModel):
    """Batch processing size configuration."""

    ids_batch_size: int = Field(default=200, ge=1)
    batch_size: int = Field(default=1000, ge=1)


class ArtistRenamerConfig(BaseModel):
    """Artist renamer configuration."""

    config_path: str = "artist-renames.yaml"


class PendingVerificationConfig(BaseModel):
    """Pending verification auto-run settings."""

    auto_verify_days: int = Field(default=14, ge=0)


class AlbumTypeDetectionConfig(BaseModel):
    """Album type detection patterns for year fallback logic."""

    special_patterns: list[str] = Field(default_factory=list)
    compilation_patterns: list[str] = Field(default_factory=list)
    reissue_patterns: list[str] = Field(default_factory=list)
    soundtrack_patterns: list[str] = Field(default_factory=list)
    various_artists_names: list[str] = Field(default_factory=list)


class LibrarySnapshotConfig(BaseModel):
    """Library snapshot persistence settings."""

    enabled: bool = True
    delta_enabled: bool = True
    cache_file: str = "cache/library_snapshot.json"
    max_age_hours: int = Field(default=24, ge=1)
    compress: bool = True
    compress_level: int = Field(default=6, ge=1, le=9)


class CleaningConfig(BaseModel):
    """Cleaning configuration."""

    remaster_keywords: list[str]
    album_suffixes_to_remove: list[str]


class TrackCleaningException(BaseModel):
    """Track cleaning exception."""

    artist: str
    album: str


class ExceptionsConfig(BaseModel):
    """Exceptions configuration."""

    track_cleaning: list[TrackCleaningException]


class DatabaseVerificationConfig(BaseModel):
    """Database verification configuration."""

    auto_verify_days: int = Field(ge=0)
    batch_size: int = Field(ge=1)


class DevelopmentConfig(BaseModel):
    """Development configuration."""

    test_artists: list[str]
    debug_mode: bool = False

    @field_validator("test_artists", mode="before")
    @classmethod
    def parse_test_artists(cls, v: str | list[str] | tuple[str, ...]) -> list[str]:
        """Convert comma-separated string, list, or tuple to list of strings.

        Supports formats:
        - YAML list: [ Amon Amarth, Children of Bodom ]
        - Comma string: "Amon Amarth, Children of Bodom"
        - Tuple: ("Amon Amarth", "Children of Bodom")

        Raises:
            ValueError: If input is not a string, list, or tuple.
            TypeError: If list/tuple contains non-string elements.
        """
        if isinstance(v, str):
            return [a.strip() for a in v.split(",") if a.strip()]
        if isinstance(v, (list, tuple)):
            result = []
            for i, item in enumerate(v):
                if not isinstance(item, str):
                    msg = f"test_artists[{i}] must be str, got {type(item).__name__}"
                    raise TypeError(msg)
                stripped = item.strip()
                if stripped:
                    result.append(stripped)
            return result
        msg = f"test_artists must be a string, list, or tuple, got {type(v).__name__}"
        raise ValueError(msg)


class LogLevelsConfig(BaseModel):
    """Log levels configuration."""

    console: LogLevel
    main_file: LogLevel
    analytics_file: LogLevel


class LoggingConfig(BaseModel):
    """Logging configuration."""

    max_runs: int = Field(ge=0)
    main_log_file: str
    analytics_log_file: str
    csv_output_file: str
    changes_report_file: str
    dry_run_report_file: str
    last_incremental_run_file: str
    pending_verification_file: str
    last_db_verify_log: str
    levels: LogLevelsConfig


class DurationThresholds(BaseModel):
    """Duration thresholds for analytics."""

    short_max: float = Field(ge=0)
    medium_max: float = Field(ge=0)
    long_max: float = Field(ge=0)


class AnalyticsConfig(BaseModel):
    """Analytics configuration."""

    enabled: bool = True
    duration_thresholds: DurationThresholds
    max_events: int = Field(ge=0)
    compact_time: bool


class GenreUpdateConfig(BaseModel):
    """Genre update configuration."""

    batch_size: int = Field(ge=1)
    concurrent_limit: int = Field(ge=1)


class ApiAuthConfig(BaseModel):
    """API authentication configuration."""

    discogs_token: str
    musicbrainz_app_name: str
    contact_email: str


class RateLimitsConfig(BaseModel):
    """Rate limits configuration."""

    discogs_requests_per_minute: int = Field(ge=1)
    musicbrainz_requests_per_second: float = Field(ge=0)
    concurrent_api_calls: int = Field(ge=1)


class ProcessingConfig(BaseModel):
    """Processing configuration for year retrieval."""

    batch_size: int = Field(ge=1)
    delay_between_batches: float = Field(ge=0)
    adaptive_delay: bool
    cache_ttl_days: int = Field(ge=0)
    pending_verification_interval_days: int = Field(ge=0)
    skip_prerelease: bool = True
    future_year_threshold: int = Field(default=1, ge=0)
    prerelease_recheck_days: int = Field(default=30, ge=0)
    prerelease_handling: str = "process_editable"


class LogicConfig(BaseModel):
    """Year retrieval logic configuration."""

    min_valid_year: int = Field(ge=1000)
    absurd_year_threshold: int = Field(default=1970, ge=1000)
    suspicion_threshold_years: int = Field(default=10, ge=0)
    definitive_score_threshold: float = Field(ge=0, le=100)
    definitive_score_diff: float = Field(ge=0)
    min_confidence_for_new_year: float = Field(default=30, ge=0, le=100)
    preferred_countries: list[str]
    major_market_codes: list[str]


class ReissueDetectionConfig(BaseModel):
    """Reissue detection configuration."""

    reissue_keywords: list[str]


class ScoringConfig(BaseModel):
    """Scoring configuration."""

    base_score: float
    artist_exact_match_bonus: float
    album_exact_match_bonus: float
    perfect_match_bonus: float
    album_variation_bonus: float
    album_substring_penalty: float = Field(le=0)
    album_unrelated_penalty: float = Field(le=0)
    mb_release_group_match_bonus: float
    type_album_bonus: float
    type_ep_single_penalty: float = Field(le=0)
    type_compilation_live_penalty: float = Field(le=0)
    status_official_bonus: float
    status_bootleg_penalty: float = Field(le=0)
    status_promo_penalty: float = Field(le=0)
    reissue_penalty: float = Field(le=0)
    year_diff_penalty_scale: float = Field(le=0)
    year_diff_max_penalty: float = Field(le=0)
    year_before_start_penalty: float = Field(le=0)
    year_after_end_penalty: float = Field(le=0)
    year_near_start_bonus: float
    country_artist_match_bonus: float
    country_major_market_bonus: float
    source_mb_bonus: float
    source_discogs_bonus: float
    source_itunes_bonus: float = 0
    future_year_penalty: float = Field(default=0, le=0)
    artist_cross_script_penalty: float = Field(default=0, le=0)
    soundtrack_compensation_bonus: float = 0


class FallbackConfig(BaseModel):
    """Year retrieval fallback settings."""

    enabled: bool = True
    year_difference_threshold: int = Field(default=5, ge=0)
    trust_api_score_threshold: float = Field(default=70, ge=0, le=100)


class ScriptApiPriority(BaseModel):
    """API priority configuration for a specific script type."""

    primary: list[str]
    fallback: list[str] = Field(default_factory=list)


class YearRetrievalConfig(BaseModel):
    """Year retrieval configuration."""

    enabled: bool
    preferred_api: PreferredApi
    api_auth: ApiAuthConfig
    rate_limits: RateLimitsConfig
    processing: ProcessingConfig
    logic: LogicConfig
    reissue_detection: ReissueDetectionConfig
    scoring: ScoringConfig
    script_api_priorities: dict[str, ScriptApiPriority] = Field(default_factory=dict)
    fallback: FallbackConfig = Field(default_factory=FallbackConfig)


class CachingConfig(BaseModel):
    """Caching configuration."""

    default_ttl_seconds: int = Field(default=900, ge=0)
    album_cache_sync_interval: int = Field(default=300, ge=0)
    cleanup_error_retry_delay: int = Field(default=60, ge=0)
    cleanup_interval_seconds: int = Field(default=300, ge=0)
    negative_result_ttl: float = Field(default=2592000, ge=0)  # 30 days
    api_result_cache_path: str = "cache/api_results.json"
    library_snapshot: LibrarySnapshotConfig = Field(default_factory=LibrarySnapshotConfig)


class ReportingConfig(BaseModel):
    """Reporting configuration."""

    problematic_albums_path: str = "reports/albums_without_year.csv"
    min_attempts_for_report: float = Field(default=3, ge=1)
    change_display_mode: ChangeDisplayMode = ChangeDisplayMode.COMPACT


class AppConfig(BaseModel):
    """Main application configuration model."""

    # Main paths and environment
    music_library_path: str
    apple_scripts_dir: str
    logs_base_dir: str
    python_settings: PythonSettings
    dry_run: bool = False
    api_cache_file: str = "cache.json"

    # Execution and performance
    apple_script_concurrency: int = Field(ge=1)
    applescript_timeout_seconds: int = Field(ge=1)
    applescript_timeouts: AppleScriptTimeoutsConfig = Field(
        default_factory=AppleScriptTimeoutsConfig,
    )
    apple_script_rate_limit: AppleScriptRateLimitConfig = Field(
        default_factory=AppleScriptRateLimitConfig,
    )
    applescript_retry: AppleScriptRetryConfig = Field(
        default_factory=AppleScriptRetryConfig,
    )
    max_retries: int = Field(ge=0)
    retry_delay_seconds: float = Field(ge=0)
    incremental_interval_minutes: int = Field(ge=1)
    cache_ttl_seconds: int = Field(ge=0)
    max_generic_entries: int = Field(default=10000, ge=1)

    # Feature toggles and settings
    cleaning: CleaningConfig
    exceptions: ExceptionsConfig
    database_verification: DatabaseVerificationConfig
    development: DevelopmentConfig
    artist_renamer: ArtistRenamerConfig = Field(default_factory=ArtistRenamerConfig)
    pending_verification: PendingVerificationConfig = Field(
        default_factory=PendingVerificationConfig,
    )
    album_type_detection: AlbumTypeDetectionConfig = Field(
        default_factory=AlbumTypeDetectionConfig,
    )
    batch_processing: BatchProcessingConfig = Field(
        default_factory=BatchProcessingConfig,
    )
    experimental: ExperimentalConfig = Field(default_factory=ExperimentalConfig)

    # Logging and analytics
    logging: LoggingConfig
    analytics: AnalyticsConfig

    # Genre update
    genre_update: GenreUpdateConfig

    # Year retrieval
    year_retrieval: YearRetrievalConfig

    # Cache paths
    album_years_cache_file: str = "cache/album_years.csv"

    # Optional sections
    caching: CachingConfig = Field(default_factory=CachingConfig)
    reporting: ReportingConfig = Field(default_factory=ReportingConfig)

    # Legacy / compat — top-level test_artists (prefer development.test_artists)
    test_artists: list[str] = Field(default_factory=list)


# API Response Models


class TrackDict(BaseModel):
    """Track information from Apple Music."""

    id: str  # Track ID from Music.app
    name: str  # Track name
    artist: str
    album: str
    genre: str | None = None
    year: str | None = None  # Year as string
    date_added: str | None = None
    last_modified: str | None = None
    track_status: str | None = None
    original_artist: str | None = None
    original_album: str | None = None
    year_before_mgu: str | None = None  # Original year before first MGU update
    year_set_by_mgu: str | None = None  # Year that MGU applied
    release_year: str | None = None  # Year from Music.app release date field
    original_pos: int | None = None  # Original position in the track list
    album_artist: str | None = None  # Album artist for proper grouping of collaborations

    model_config = ConfigDict(extra="allow")

    def get(self, key: str, default: TrackFieldValue = None) -> TrackFieldValue:
        """Get attribute value with default, mimicking dict.get() behavior.

        Args:
            key: The attribute name to retrieve
            default: The default value to return if attribute doesn't exist

        Returns:
            The attribute value if it exists, otherwise the default value

        """
        try:
            # First, try to get the field value using getattr
            value = getattr(self, key)
        except AttributeError:
            # For Pydantic v1 with extra="allow", additional fields are stored in __dict__
            if key in self.__dict__:
                value = self.__dict__[key]
                return default if value is None and default is not None else value
            return default

        # Return the value, but if it's None, and we have a non-None default, use default
        return default if value is None and default is not None else value

    def copy(self, **kwargs: Any) -> TrackDict:
        """Create a copy of the TrackDict with optional field updates.

        Args:
            **kwargs: Fields to update in the copy

        Returns:
            A new TrackDict instance with updated fields

        """
        # Pydantic v2 model_dump() includes extra fields automatically
        data = self.model_dump()
        data.update(kwargs)
        return TrackDict(**data)


class ChangeLogEntry(BaseModel):
    """Change log entry for track updates.

    Supports genre updates, year updates, and metadata cleaning with optional fields.
    """

    timestamp: str
    change_type: str
    track_id: str
    artist: str
    track_name: str = ""
    album_name: str = ""
    old_genre: str | None = None
    new_genre: str | None = None
    year_before_mgu: str | None = None
    year_set_by_mgu: str | None = None
    old_track_name: str | None = None
    new_track_name: str | None = None
    old_album_name: str | None = None
    new_album_name: str | None = None


class CachedApiResult(BaseModel):
    """Cached API result."""

    artist: str
    album: str
    year: str | None  # Year as string
    source: str  # "musicbrainz", "discogs"
    timestamp: float
    ttl: int | None = None  # seconds, None for permanent
    metadata: dict[str, Any] = Field(default_factory=dict)  # additional data from API
    api_response: dict[str, Any] | None = None

    def model_dump(self, **_kwargs: Any) -> dict[str, Any]:
        """Export model as dictionary (Pydantic v2 compatibility)."""
        return {
            "artist": self.artist,
            "album": self.album,
            "year": self.year,
            "source": self.source,
            "timestamp": self.timestamp,
            "ttl": self.ttl,
            "metadata": self.metadata,
            "api_response": self.api_response,
        }


class ScriptAction(BaseModel):
    """AppleScript action definition."""

    script: str
    args: list[Any] | None = None


class CodeAction(BaseModel):
    """AppleScript code action definition."""

    code: str


# Extended versions for core/helpers.py
class ScriptActionExtended(BaseModel):
    """Extended script action with type and path."""

    type: Literal["script"]
    script_path: str
    content: str
    args: list[str] = Field(default_factory=list)


class CodeActionExtended(BaseModel):
    """Extended code action with type."""

    type: Literal["code"]
    code: str
    args: list[str] = Field(default_factory=list)


# MusicBrainz API Response Models


class MusicBrainzArtist(BaseModel):
    """MusicBrainz artist information."""

    id: str
    name: str
    sort_name: str | None = None
    disambiguation: str | None = None


class MusicBrainzReleaseGroup(BaseModel):
    """MusicBrainz release group information."""

    id: str
    title: str
    primary_type: str | None = None
    secondary_types: list[str] = Field(default_factory=list)
    first_release_date: str | None = None


class MusicBrainzRelease(BaseModel):
    """MusicBrainz release information."""

    id: str
    title: str
    status: str | None = None
    country: str | None = None
    date: str | None = None
    release_group: MusicBrainzReleaseGroup | None = None
    artist_credit: list[MBArtistCredit] = Field(default_factory=list)


class MusicBrainzSearchResult(BaseModel):
    """MusicBrainz search result."""

    releases: list[MusicBrainzRelease] = Field(default_factory=list)
    count: int = 0
    offset: int = 0


# Discogs API Response Models


class DiscogsArtist(BaseModel):
    """Discogs artist information."""

    id: int
    name: str
    resource_url: str | None = None


class DiscogsRelease(BaseModel):
    """Discogs release information."""

    id: int
    title: str
    year: int | None = None
    country: str | None = None
    format: list[str] = Field(default_factory=list)
    genre: list[str] = Field(default_factory=list)
    style: list[str] = Field(default_factory=list)
    master_id: int | None = None
    master_url: str | None = None


class DiscogsSearchResult(BaseModel):
    """Discogs search result."""

    results: list[DiscogsRelease] = Field(default_factory=list)
    pagination: dict[str, Any] = Field(default_factory=dict)
