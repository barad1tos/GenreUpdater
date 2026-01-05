"""Unit tests for PendingVerificationService."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from services.pending_verification import (
    PendingAlbumEntry,
    PendingVerificationService,
    VerificationReason,
)


@pytest.fixture
def config(tmp_path: Path) -> dict[str, Any]:
    """Provide minimal configuration required by the service."""
    return {
        "logs_base_dir": str(tmp_path),
        "logging": {
            "pending_verification_file": "csv/pending_verification.csv",
        },
        "year_retrieval": {
            "processing": {
                "pending_verification_interval_days": 1,
                "prerelease_recheck_days": 7,
            },
        },
        "reporting": {
            "problematic_albums_path": "reports/problematic_albums.csv",
        },
    }


@pytest.fixture
def console_logger() -> MagicMock:
    """Return a mocked console logger."""
    return MagicMock()


@pytest.fixture
def error_logger() -> MagicMock:
    """Return a mocked error logger."""
    return MagicMock()


@pytest.fixture
def service(
    config: dict[str, Any],
    console_logger: MagicMock,
    error_logger: MagicMock,
) -> PendingVerificationService:
    """Create a service instance with mocked dependencies."""
    return PendingVerificationService(config, console_logger, error_logger)


@pytest.mark.asyncio
async def test_initialize_creates_empty_pending_cache(service: PendingVerificationService) -> None:
    await service.initialize()
    assert service.pending_albums == {}
    assert Path(service.pending_file_path).parent.exists()


def test_generate_album_key_uses_hash_service(service: PendingVerificationService) -> None:
    with patch(
        "services.pending_verification.UnifiedHashService.hash_pending_key",
        return_value="hashed-key",
    ) as hash_mock:
        key = service.generate_album_key("Artist", "Album")
    hash_mock.assert_called_once_with("Artist|Album")
    assert key == "hashed-key"


@pytest.mark.asyncio
async def test_mark_for_verification_persists_entry(service: PendingVerificationService) -> None:
    await service.initialize()

    await service.mark_for_verification(
        "Radiohead",
        "OK Computer",
        reason="prerelease",
        metadata={"source": "discogs"},
    )

    pending = await service.get_all_pending_albums()
    assert len(pending) == 1

    entry = pending[0]
    assert entry.artist == "Radiohead"
    assert entry.album == "OK Computer"
    assert entry.reason == VerificationReason.PRERELEASE
    metadata = json.loads(entry.metadata)
    assert metadata["source"] == "discogs"
    assert metadata.get("recheck_days") == service.prerelease_recheck_days


@pytest.mark.asyncio
async def test_is_verification_needed_returns_true_when_interval_elapsed(
    service: PendingVerificationService,
) -> None:
    await service.initialize()
    await service.mark_for_verification("Artist", "Album")

    key = service.generate_album_key("Artist", "Album")
    entry = service.pending_albums[key]
    service.pending_albums[key] = PendingAlbumEntry(
        timestamp=entry.timestamp - timedelta(days=2),
        artist=entry.artist,
        album=entry.album,
        reason=entry.reason,
        metadata=entry.metadata,
    )

    assert await service.is_verification_needed("Artist", "Album") is True


@pytest.mark.asyncio
async def test_is_verification_needed_false_within_prerelease_window(
    service: PendingVerificationService,
) -> None:
    await service.initialize()
    await service.mark_for_verification("Artist", "Album", reason="prerelease")

    assert await service.is_verification_needed("Artist", "Album") is False


@pytest.mark.asyncio
async def test_remove_from_pending_removes_entry(service: PendingVerificationService) -> None:
    await service.initialize()
    await service.mark_for_verification("Artist", "Album")

    await service.remove_from_pending("Artist", "Album")

    assert await service.get_all_pending_albums() == []


@pytest.mark.asyncio
async def test_get_pending_albums_by_reason_filters_results(
    service: PendingVerificationService,
) -> None:
    await service.initialize()
    await service.mark_for_verification("Artist One", "Album One")
    await service.mark_for_verification("Artist Two", "Album Two", reason="prerelease")
    await service.mark_for_verification("Artist Three", "Album Three")

    result = await service.get_pending_albums_by_reason("no_year_found")
    artists = {entry.artist for entry in result}
    assert artists == {"Artist One", "Artist Three"}


@pytest.mark.asyncio
async def test_initialize_loads_existing_csv(service: PendingVerificationService) -> None:
    pending_file = Path(service.pending_file_path)
    pending_file.parent.mkdir(parents=True, exist_ok=True)
    pending_file.write_text(
        "artist,album,timestamp,reason,metadata\nArtist1,Album1,2024-01-01 00:00:00,no_year_found,\n",
        encoding="utf-8",
    )

    await service.initialize()

    pending = await service.get_all_pending_albums()
    assert len(pending) == 1
    entry = pending[0]
    assert entry.artist == "Artist1"
    assert entry.album == "Album1"
    assert entry.reason == VerificationReason.NO_YEAR_FOUND
    assert entry.metadata == ""


@pytest.mark.asyncio
async def test_generate_problematic_albums_report_writes_file(
    service: PendingVerificationService,
) -> None:
    await service.initialize()
    await service.mark_for_verification("Artist", "Album")

    key = service.generate_album_key("Artist", "Album")
    entry = service.pending_albums[key]
    service.pending_albums[key] = PendingAlbumEntry(
        timestamp=entry.timestamp - timedelta(days=5),
        artist=entry.artist,
        album=entry.album,
        reason=entry.reason,
        metadata=entry.metadata,
    )

    report_path = Path(service.pending_file_path).parent / "problematic.csv"
    count = await service.generate_problematic_albums_report(
        min_attempts=2,
        report_path=str(report_path),
    )

    assert count == 1
    assert report_path.exists()
    report_content = report_path.read_text(encoding="utf-8")
    assert "Artist" in report_content
    assert "Album" in report_content


@pytest.mark.asyncio
async def test_thread_safety_handles_concurrent_writes(service: PendingVerificationService) -> None:
    await service.initialize()

    await asyncio.gather(*(service.mark_for_verification(f"Artist {index}", f"Album {index}") for index in range(5)))

    pending_entries = await service.get_all_pending_albums()
    assert len(pending_entries) == 5


# ============================================================================
# Attempt Count Tests
# ============================================================================


@pytest.mark.asyncio
async def test_attempt_count_starts_at_one(service: PendingVerificationService) -> None:
    """First mark_for_verification should set attempt_count to 1."""
    await service.initialize()
    await service.mark_for_verification("Artist", "Album")

    count = await service.get_attempt_count("Artist", "Album")
    assert count == 1


@pytest.mark.asyncio
async def test_attempt_count_increments_on_subsequent_marks(
    service: PendingVerificationService,
) -> None:
    """Each mark_for_verification should increment attempt_count."""
    await service.initialize()

    await service.mark_for_verification("Artist", "Album")
    assert await service.get_attempt_count("Artist", "Album") == 1

    await service.mark_for_verification("Artist", "Album")
    assert await service.get_attempt_count("Artist", "Album") == 2

    await service.mark_for_verification("Artist", "Album")
    assert await service.get_attempt_count("Artist", "Album") == 3


@pytest.mark.asyncio
async def test_get_attempt_count_returns_zero_for_unknown_album(
    service: PendingVerificationService,
) -> None:
    """get_attempt_count should return 0 for albums not in pending list."""
    await service.initialize()

    count = await service.get_attempt_count("Unknown Artist", "Unknown Album")
    assert count == 0


@pytest.mark.asyncio
async def test_attempt_count_persisted_to_csv(service: PendingVerificationService) -> None:
    """attempt_count should be persisted and loaded from CSV."""
    await service.initialize()

    # Mark multiple times to increment counter
    await service.mark_for_verification("Artist", "Album")
    await service.mark_for_verification("Artist", "Album")
    await service.mark_for_verification("Artist", "Album")

    # Read CSV content directly
    csv_content = Path(service.pending_file_path).read_text(encoding="utf-8")
    assert "attempt_count" in csv_content  # Column should exist
    assert ",3" in csv_content or ",3\n" in csv_content  # Value should be 3


@pytest.mark.asyncio
async def test_attempt_count_loaded_from_csv(
    service: PendingVerificationService,
) -> None:
    """attempt_count should be loaded correctly from existing CSV."""
    pending_file = Path(service.pending_file_path)
    pending_file.parent.mkdir(parents=True, exist_ok=True)
    pending_file.write_text(
        "artist,album,timestamp,reason,metadata,attempt_count\nArtist,Album,2024-01-01 00:00:00,no_year_found,,5\n",
        encoding="utf-8",
    )

    await service.initialize()

    count = await service.get_attempt_count("Artist", "Album")
    assert count == 5


@pytest.mark.asyncio
async def test_legacy_csv_without_attempt_count_defaults_to_zero(
    service: PendingVerificationService,
) -> None:
    """Legacy CSV without attempt_count column should default to 0."""
    pending_file = Path(service.pending_file_path)
    pending_file.parent.mkdir(parents=True, exist_ok=True)
    pending_file.write_text(
        "artist,album,timestamp,reason,metadata\nArtist,Album,2024-01-01 00:00:00,no_year_found,\n",
        encoding="utf-8",
    )

    await service.initialize()

    # Legacy entry without attempt_count should default to 0
    count = await service.get_attempt_count("Artist", "Album")
    assert count == 0


@pytest.mark.asyncio
async def test_remove_from_pending_resets_attempt_count(
    service: PendingVerificationService,
) -> None:
    """Removing from pending should reset attempt count to 0."""
    await service.initialize()

    await service.mark_for_verification("Artist", "Album")
    await service.mark_for_verification("Artist", "Album")
    assert await service.get_attempt_count("Artist", "Album") == 2

    await service.remove_from_pending("Artist", "Album")
    assert await service.get_attempt_count("Artist", "Album") == 0


@pytest.mark.asyncio
async def test_malformed_attempt_count_defaults_to_zero(
    config: dict[str, Any],
    console_logger: MagicMock,
    error_logger: MagicMock,
    tmp_path: Path,
) -> None:
    """Invalid attempt_count in CSV should default to 0."""
    # Update config to use tmp_path for logs
    config["logs_base_dir"] = str(tmp_path)

    # Create CSV with malformed attempt_count (non-integer value)
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)
    csv_file = csv_dir / "pending_verification.csv"
    csv_file.write_text(
        "artist,album,timestamp,reason,metadata,attempt_count\nTest Artist,Test Album,2024-01-01 00:00:00,no_year_found,,foo\n",
        encoding="utf-8",
    )

    # Create new service and initialize
    service = PendingVerificationService(config, console_logger, error_logger)
    await service.initialize()

    # Should default to 0, not crash
    count = await service.get_attempt_count("Test Artist", "Test Album")
    assert count == 0


# ============================================================================
# Should Auto Verify Tests
# ============================================================================


class TestShouldAutoVerify:
    """Tests for should_auto_verify method."""

    @pytest.mark.asyncio
    async def test_returns_true_when_no_previous_verification(self, service: PendingVerificationService, tmp_path: Path) -> None:
        """Should return True when no timestamp file exists."""
        await service.initialize()
        csv_file = tmp_path / "pending.csv"
        csv_file.write_text("")
        with patch.object(service, "pending_file_path", str(csv_file)):
            result = await service.should_auto_verify()
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_threshold_passed(self, service: PendingVerificationService, tmp_path: Path) -> None:
        """Should return True when auto_verify_days have passed."""
        await service.initialize()
        csv_file = tmp_path / "pending.csv"
        csv_file.write_text("")
        last_verify_file = tmp_path / "pending_last_verify.txt"
        old_time = datetime.now(tz=UTC) - timedelta(days=20)
        last_verify_file.write_text(old_time.isoformat())

        with patch.object(service, "pending_file_path", str(csv_file)):
            result = await service.should_auto_verify()
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_recently_verified(self, service: PendingVerificationService, tmp_path: Path) -> None:
        """Should return False when within threshold."""
        await service.initialize()
        csv_file = tmp_path / "pending.csv"
        csv_file.write_text("")
        last_verify_file = tmp_path / "pending_last_verify.txt"
        recent_time = datetime.now(tz=UTC) - timedelta(days=2)
        last_verify_file.write_text(recent_time.isoformat())

        with patch.object(service, "pending_file_path", str(csv_file)):
            result = await service.should_auto_verify()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_disabled(self, service: PendingVerificationService) -> None:
        """Should return False when auto_verify_days is 0."""
        service.config["pending_verification"] = {"auto_verify_days": 0}
        await service.initialize()
        result = await service.should_auto_verify()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_timestamp_file_malformed(self, service: PendingVerificationService, tmp_path: Path) -> None:
        """Should return True (fail-open) when timestamp file contains invalid data."""
        await service.initialize()
        csv_file = tmp_path / "pending.csv"
        csv_file.write_text("")
        last_verify_file = tmp_path / "pending_last_verify.txt"
        last_verify_file.write_text("not-a-valid-iso-timestamp")

        with patch.object(service, "pending_file_path", str(csv_file)):
            result = await service.should_auto_verify()
        # Should return True (run verification) when we can't parse timestamp
        assert result is True

    @pytest.mark.asyncio
    async def test_handles_naive_datetime_in_timestamp_file(self, service: PendingVerificationService, tmp_path: Path) -> None:
        """Should handle naive datetime by assuming UTC."""
        await service.initialize()
        csv_file = tmp_path / "pending.csv"
        csv_file.write_text("")
        last_verify_file = tmp_path / "pending_last_verify.txt"
        # Write naive datetime (no timezone info) - should be treated as UTC
        naive_time = datetime.now(tz=UTC) - timedelta(days=2)
        last_verify_file.write_text(naive_time.strftime("%Y-%m-%dT%H:%M:%S"))

        with patch.object(service, "pending_file_path", str(csv_file)):
            result = await service.should_auto_verify()
        # Within threshold (2 days < 14 days default), should return False
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_file_read_fails(self, service: PendingVerificationService, tmp_path: Path) -> None:
        """Should return True (fail-open) when file read raises OSError."""
        await service.initialize()
        csv_file = tmp_path / "pending.csv"
        csv_file.write_text("")
        last_verify_file = tmp_path / "pending_last_verify.txt"
        last_verify_file.write_text("2024-01-01T00:00:00+00:00")

        with (
            patch.object(service, "pending_file_path", str(csv_file)),
            patch("pathlib.Path.open", side_effect=OSError("Permission denied")),
        ):
            result = await service.should_auto_verify()
        # Should return True when file read fails
        assert result is True


# ============================================================================
# Update Verification Timestamp Tests
# ============================================================================


class TestUpdateVerificationTimestamp:
    """Tests for update_verification_timestamp method."""

    @pytest.mark.asyncio
    async def test_writes_timestamp_file(self, service: PendingVerificationService, tmp_path: Path) -> None:
        """Should write ISO timestamp to file."""
        await service.initialize()
        csv_file = tmp_path / "pending.csv"
        csv_file.write_text("")

        with patch.object(service, "pending_file_path", str(csv_file)):
            await service.update_verification_timestamp()

        last_verify_file = tmp_path / "pending_last_verify.txt"
        assert last_verify_file.exists()
        content = last_verify_file.read_text().strip()
        # Should be valid ISO format
        parsed = datetime.fromisoformat(content)
        assert parsed.tzinfo is not None

    @pytest.mark.asyncio
    async def test_handles_write_failure_gracefully(self, service: PendingVerificationService, tmp_path: Path) -> None:
        """Should not raise when file write fails."""
        await service.initialize()
        csv_file = tmp_path / "pending.csv"
        csv_file.write_text("")

        with (
            patch.object(service, "pending_file_path", str(csv_file)),
            patch("pathlib.Path.open", side_effect=OSError("Disk full")),
        ):
            # Should not raise despite the underlying OSError
            await service.update_verification_timestamp()
