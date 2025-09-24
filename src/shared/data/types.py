"""Type definitions for the utils layer.

This module provides type imports that utils modules can use without
depending on the services layer, maintaining clean architecture boundaries.
"""

# Re-export protocol types for use in utils layer
# Re-export model types
from src.shared.data.models import CachedApiResult, TrackDict
from src.shared.data.protocols import (
    AnalyticsProtocol,
    AppleScriptClientProtocol,
    CacheServiceProtocol,
    ExternalApiServiceProtocol,
    PendingVerificationServiceProtocol,
    RateLimiterProtocol,
)

__all__ = [
    # Protocols
    "AnalyticsProtocol",
    "AppleScriptClientProtocol",
    "CacheServiceProtocol",
    # Models
    "CachedApiResult",
    "ExternalApiServiceProtocol",
    "PendingVerificationServiceProtocol",
    "RateLimiterProtocol",
    "TrackDict",
]
