"""Apple Music integration module.

This module provides interfaces for interacting with Apple Music via Music.app.

Public API:
    - AppleScriptClient: Traditional client using AppleScript (stable, slower)
    - SwiftBridge: High-performance client using Swift daemon (~60x faster)
    - AppleScriptSanitizer: Security validator for AppleScript code
    - AppleScriptSanitizationError: Exception for security violations
    - AppleScriptExecutionError: Exception for execution failures (transient)
    - EnhancedRateLimiter: Rate limiting for AppleScript execution

Performance comparison (37K tracks):
    - fetch_all_track_ids: AppleScript ~95s → SwiftBridge ~1.5s
    - fetch_tracks: AppleScript ~120s → SwiftBridge ~3s
    - batch_update (100): AppleScript ~50s → SwiftBridge ~0.5s
"""

from services.apple.applescript_client import AppleScriptClient
from services.apple.applescript_executor import AppleScriptExecutionError
from services.apple.rate_limiter import EnhancedRateLimiter
from services.apple.sanitizer import (
    MAX_SCRIPT_SIZE,
    AppleScriptSanitizationError,
    AppleScriptSanitizer,
)
from services.apple.swift_bridge import SwiftBridge

__all__ = [
    "MAX_SCRIPT_SIZE",
    "AppleScriptClient",
    "AppleScriptExecutionError",
    "AppleScriptSanitizationError",
    "AppleScriptSanitizer",
    "EnhancedRateLimiter",
    "SwiftBridge",
]
