"""Shared test factories for Genres Autoupdater v2.0.

Provides reusable factory functions and minimal config data
that can be imported by any test module (including xdist workers).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.models.track_models import AppConfig


MINIMAL_CONFIG_DATA: dict[str, Any] = {
    "music_library_path": "/tmp/test-library",
    "apple_scripts_dir": "/tmp/test-scripts",
    "logs_base_dir": "/tmp/test-logs",
    "python_settings": {"prevent_bytecode": True},
    "apple_script_concurrency": 2,
    "applescript_timeout_seconds": 60,
    "max_retries": 3,
    "retry_delay_seconds": 1.0,
    "incremental_interval_minutes": 15,
    "cache_ttl_seconds": 1200,
    "cleaning": {
        "remaster_keywords": ["remaster"],
        "album_suffixes_to_remove": [],
    },
    "exceptions": {"track_cleaning": []},
    "database_verification": {"auto_verify_days": 7, "batch_size": 10},
    "development": {"test_artists": []},
    "logging": {
        "max_runs": 3,
        "main_log_file": "test.log",
        "analytics_log_file": "analytics.log",
        "csv_output_file": "output.csv",
        "changes_report_file": "changes.json",
        "dry_run_report_file": "dryrun.json",
        "last_incremental_run_file": "lastrun.json",
        "pending_verification_file": "pending.json",
        "last_db_verify_log": "dbverify.log",
        "levels": {
            "console": "INFO",
            "main_file": "INFO",
            "analytics_file": "INFO",
        },
    },
    "analytics": {
        "duration_thresholds": {
            "short_max": 2,
            "medium_max": 5,
            "long_max": 10,
        },
        "max_events": 10000,
        "compact_time": False,
    },
    "genre_update": {"batch_size": 50, "concurrent_limit": 5},
    "year_retrieval": {
        "enabled": False,
        "preferred_api": "musicbrainz",
        "api_auth": {
            "discogs_token": "test-token",
            "musicbrainz_app_name": "TestApp/1.0",
            "contact_email": "test@example.com",
        },
        "rate_limits": {
            "discogs_requests_per_minute": 25,
            "musicbrainz_requests_per_second": 1,
            "concurrent_api_calls": 3,
        },
        "processing": {
            "batch_size": 10,
            "delay_between_batches": 60,
            "adaptive_delay": False,
            "cache_ttl_days": 30,
            "pending_verification_interval_days": 30,
        },
        "logic": {
            "min_valid_year": 1900,
            "definitive_score_threshold": 85,
            "definitive_score_diff": 15,
            "preferred_countries": [],
            "major_market_codes": [],
        },
        "reissue_detection": {"reissue_keywords": []},
        "scoring": {
            "base_score": 0,
            "artist_exact_match_bonus": 0,
            "album_exact_match_bonus": 0,
            "perfect_match_bonus": 0,
            "album_variation_bonus": 0,
            "album_substring_penalty": 0,
            "album_unrelated_penalty": 0,
            "mb_release_group_match_bonus": 0,
            "type_album_bonus": 0,
            "type_ep_single_penalty": 0,
            "type_compilation_live_penalty": 0,
            "status_official_bonus": 0,
            "status_bootleg_penalty": 0,
            "status_promo_penalty": 0,
            "reissue_penalty": 0,
            "year_diff_penalty_scale": 0,
            "year_diff_max_penalty": 0,
            "year_before_start_penalty": 0,
            "year_after_end_penalty": 0,
            "year_near_start_bonus": 0,
            "country_artist_match_bonus": 0,
            "country_major_market_bonus": 0,
            "source_mb_bonus": 0,
            "source_discogs_bonus": 0,
        },
    },
}


def create_test_app_config(**overrides: Any) -> AppConfig:
    """Create a minimal valid AppConfig for testing.

    Uses shallow merge: passing ``logging={...}`` replaces the entire
    logging dict rather than merging individual keys.

    Args:
        **overrides: Top-level fields to replace in the config data.

    """
    from core.models.track_models import AppConfig

    data = {**MINIMAL_CONFIG_DATA, **overrides}
    return AppConfig(**data)
