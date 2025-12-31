"""Centralized debug configuration for the application.

This module provides a single place to enable/disable debug logging
for different components without removing debug statements from code.

Usage:
    from core.debug_utils import debug

    if debug.year:
        logger.info("Year processing details: %s", data)

    if debug.api:
        logger.info("API response: %s", response)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class DebugConfig:
    """Centralized debug configuration.

    All debug flags are False by default. Enable specific flags
    when you need detailed logging for troubleshooting.

    Attributes:
        year: Debug year retrieval and processing logic
        api: Debug external API calls and responses
        cache: Debug cache operations (hits, misses, invalidation)
        applescript: Debug AppleScript execution
        pipeline: Debug main processing pipeline flow

    Environment Variables:
        DEBUG_ALL: Enable all debug flags (set to "1" or "true")
        DEBUG_YEAR: Enable year debug (set to "1" or "true")
        DEBUG_API: Enable API debug (set to "1" or "true")
        DEBUG_CACHE: Enable cache debug (set to "1" or "true")
        DEBUG_APPLESCRIPT: Enable AppleScript debug (set to "1" or "true")
        DEBUG_PIPELINE: Enable pipeline debug (set to "1" or "true")
    """

    year: bool = field(default=False)
    api: bool = field(default=False)
    cache: bool = field(default=False)
    applescript: bool = field(default=False)
    pipeline: bool = field(default=False)

    def __post_init__(self) -> None:
        """Load debug flags from environment variables."""
        self._load_from_env()

    def _load_from_env(self) -> None:
        """Load debug configuration from environment variables."""
        debug_all = self._env_bool("DEBUG_ALL")

        if debug_all:
            self.year = True
            self.api = True
            self.cache = True
            self.applescript = True
            self.pipeline = True
        else:
            self.year = self._env_bool("DEBUG_YEAR") or self.year
            self.api = self._env_bool("DEBUG_API") or self.api
            self.cache = self._env_bool("DEBUG_CACHE") or self.cache
            self.applescript = self._env_bool("DEBUG_APPLESCRIPT") or self.applescript
            self.pipeline = self._env_bool("DEBUG_PIPELINE") or self.pipeline

    @staticmethod
    def _env_bool(key: str) -> bool:
        """Parse boolean from environment variable."""
        value = os.environ.get(key, "").lower()
        return value in ("1", "true", "yes", "on")


# Global debug configuration instance
# Import this in other modules: from core.debug_utils import debug
debug = DebugConfig()
