"""Pydantic models for configuration and data validation."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, TypedDict, TypeVar

from pydantic import BaseModel, ConfigDict, Field

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


class LogLevel(str, Enum):
    """Log level enumeration."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    NOTSET = "NOTSET"


class PreferredApi(str, Enum):
    """Preferred API enumeration."""

    MUSICBRAINZ = "musicbrainz"
    DISCOGS = "discogs"
    LASTFM = "lastfm"
    ITUNES = "itunes"


class ScriptType(str, Enum):
    """Script type enumeration for API prioritization."""

    CYRILLIC = "cyrillic"
    LATIN = "latin"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ChangeDisplayMode(str, Enum):
    """Change display mode enumeration."""

    COMPACT = "compact"
    TABLE = "table"


class PythonSettings(BaseModel):
    """Python environment settings."""

    prevent_bytecode: bool


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


class LogLevelsConfig(BaseModel):
    """Log levels configuration."""

    console: LogLevel
    main_file: LogLevel
    analytics_file: LogLevel
    year_updates_file: LogLevel


class LoggingConfig(BaseModel):
    """Logging configuration."""

    max_runs: int = Field(ge=0)
    main_log_file: str
    analytics_log_file: str
    csv_output_file: str
    album_cache_csv: str
    changes_report_file: str
    dry_run_report_file: str
    last_incremental_run_file: str
    pending_verification_file: str
    last_db_verify_log: str
    year_changes_log_file: str
    levels: LogLevelsConfig


class DurationThresholds(BaseModel):
    """Duration thresholds for analytics."""

    short_max: float = Field(ge=0)
    medium_max: float = Field(ge=0)
    long_max: float = Field(ge=0)


class AnalyticsConfig(BaseModel):
    """Analytics configuration."""

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
    use_lastfm: bool
    lastfm_api_key: str

    @staticmethod
    def validate_email(v: str) -> str:
        """Validate email format."""
        if "@" not in v:
            msg = "Invalid email address"
            raise ValueError(msg)
        return v


class RateLimitsConfig(BaseModel):
    """Rate limits configuration."""

    discogs_requests_per_minute: int = Field(ge=1)
    musicbrainz_requests_per_second: float = Field(ge=0)
    lastfm_requests_per_second: float = Field(ge=0)
    concurrent_api_calls: int = Field(ge=1)


class ProcessingConfig(BaseModel):
    """Processing configuration."""

    batch_size: int = Field(ge=1)
    delay_between_batches: float = Field(ge=0)
    adaptive_delay: bool
    cache_ttl_days: int = Field(ge=0)
    pending_verification_interval_days: int = Field(ge=0)


class LogicConfig(BaseModel):
    """Logic configuration."""

    min_valid_year: int = Field(ge=1000)
    definitive_score_threshold: float = Field(ge=0, le=100)
    definitive_score_diff: float = Field(ge=0)
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
    source_lastfm_penalty: float = Field(le=0)


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


class CachingConfig(BaseModel):
    """Caching configuration."""

    negative_result_ttl: float = Field(default=2592000, ge=0)  # 30 days
    api_result_cache_path: str = "cache/api_results.json"


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
    max_retries: int = Field(ge=0)
    retry_delay_seconds: float = Field(ge=0)
    incremental_interval_minutes: int = Field(ge=1)
    cache_ttl_seconds: int = Field(ge=0)
    album_cache_sync_interval: int = Field(ge=0)

    # Feature toggles and settings
    cleaning: CleaningConfig
    exceptions: ExceptionsConfig
    database_verification: DatabaseVerificationConfig
    development: DevelopmentConfig

    # Logging and analytics
    logging: LoggingConfig
    analytics: AnalyticsConfig

    # Genre update
    genre_update: GenreUpdateConfig

    # Year retrieval
    year_retrieval: YearRetrievalConfig

    # Optional sections
    caching: CachingConfig = Field(default_factory=CachingConfig)
    reporting: ReportingConfig = Field(default_factory=ReportingConfig)

    # Validation is handled by individual field validators in nested models


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
    old_year: str | None = None  # For tracking changes
    new_year: str | None = None  # For tracking changes
    release_year: str | None = None  # Year from Music.app release date field
    original_pos: int | None = None  # Original position in the track list

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
        # Get current data as dict (including extra fields from Config.extra="allow")
        # Use Pydantic v2 model_dump
        data = self.model_dump()

        # Include any extra fields that might be stored directly in __dict__
        for key, value in self.__dict__.items():
            if not key.startswith("_") and key not in data:
                data[key] = value
        # Update with provided kwargs
        data.update(kwargs)
        # Create new instance
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
    old_year: str | None = None
    new_year: str | None = None
    old_track_name: str | None = None
    new_track_name: str | None = None
    old_album_name: str | None = None
    new_album_name: str | None = None


class CachedApiResult(BaseModel):
    """Cached API result."""

    artist: str
    album: str
    year: str | None  # Year as string
    source: str  # "musicbrainz", "discogs", "lastfm"
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


# Last.fm API Response Models


class LastFmImage(BaseModel):
    """Last.fm image information."""

    size: str
    text: str = Field(alias="#text")


class LastFmAlbum(BaseModel):
    """Last.fm album information."""

    name: str
    artist: str
    url: str | None = None
    image: list[LastFmImage] = Field(default_factory=list)
    listeners: str | None = None
    playcount: str | None = None
    wiki: dict[str, Any] | None = None

    @staticmethod
    def extract_published_date(v: dict[str, Any] | None) -> dict[str, Any] | None:
        """Extract published date from wiki if available."""
        return v


class LastFmSearchResult(BaseModel):
    """Last.fm search result."""

    # Field name matches Last.fm API response format exactly
    albummatches: dict[str, Any] = Field(default_factory=dict)

    @property
    def albums(self) -> list[LastFmAlbum]:
        """Get albums from search results."""
        album_data = self.albummatches.get("album", [])
        albums: list[dict[str, Any]] = album_data
        return [LastFmAlbum(**album) for album in albums]
