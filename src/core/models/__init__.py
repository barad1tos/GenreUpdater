"""Data models and protocols."""

from core.models.normalization import are_names_equal, normalize_for_matching
from core.models.protocols import AppleScriptClientProtocol, CacheServiceProtocol
from core.models.track_models import AppConfig, ChangeLogEntry, TrackDict, TrackFieldValue

__all__ = [
    "AppConfig",
    "AppleScriptClientProtocol",
    "CacheServiceProtocol",
    "ChangeLogEntry",
    "TrackDict",
    "TrackFieldValue",
    "are_names_equal",
    "normalize_for_matching",
]
