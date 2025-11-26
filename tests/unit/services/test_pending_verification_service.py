"""Unit tests for PendingVerificationService."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.services.pending import PendingVerificationService


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
        "src.services.pending.UnifiedHashService.hash_pending_key",
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

    _timestamp, artist, album, reason, metadata_str = pending[0]
    assert artist == "Radiohead"
    assert album == "OK Computer"
    assert reason == "prerelease"
    metadata = json.loads(metadata_str)
    assert metadata["source"] == "discogs"
    assert metadata.get("recheck_days") == service.prerelease_recheck_days


@pytest.mark.asyncio
async def test_is_verification_needed_returns_true_when_interval_elapsed(
    service: PendingVerificationService,
) -> None:
    await service.initialize()
    await service.mark_for_verification("Artist", "Album")

    key = service.generate_album_key("Artist", "Album")
    timestamp, artist, album, reason, metadata_str = service.pending_albums[key]
    service.pending_albums[key] = (
        timestamp - timedelta(days=2),
        artist,
        album,
        reason,
        metadata_str,
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
    artists = {artist for artist, _album, _timestamp, _metadata in result}
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
    _timestamp, artist, album, reason, metadata_str = pending[0]
    assert artist == "Artist1"
    assert album == "Album1"
    assert reason == "no_year_found"
    assert metadata_str == ""


@pytest.mark.asyncio
async def test_generate_problematic_albums_report_writes_file(
    service: PendingVerificationService,
) -> None:
    await service.initialize()
    await service.mark_for_verification("Artist", "Album")

    key = service.generate_album_key("Artist", "Album")
    timestamp, artist, album, reason, metadata_str = service.pending_albums[key]
    service.pending_albums[key] = (
        timestamp - timedelta(days=5),
        artist,
        album,
        reason,
        metadata_str,
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
