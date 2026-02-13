"""API service modules for external music metadata providers.

This package contains specialized clients for different music metadata APIs:
- MusicBrainz: Comprehensive music metadata database
- Discogs: Music database and marketplace
- iTunes Search API: Apple's music catalog for new releases and official metadata
- Scoring: Advanced release scoring system for evaluating originality
- Orchestrator: Main coordination layer for all API providers
"""

from .applemusic import AppleMusicClient
from .api_base import ApiRateLimiter, BaseApiClient, ScoredRelease
from .discogs import DiscogsClient
from .musicbrainz import MusicBrainzClient
from .orchestrator import ExternalApiOrchestrator, create_external_api_orchestrator
from .year_scoring import ReleaseScorer, create_release_scorer

__all__ = [
    "ApiRateLimiter",
    "AppleMusicClient",
    "BaseApiClient",
    "DiscogsClient",
    "ExternalApiOrchestrator",
    "MusicBrainzClient",
    "ReleaseScorer",
    "ScoredRelease",
    "create_external_api_orchestrator",
    "create_release_scorer",
]
