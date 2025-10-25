"""Comprehensive unit tests for the pending verification service."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from src.infrastructure.cache.hash_service import UnifiedHashService
from src.infrastructure.pending_verification import PendingVerificationService


async def initialize_service_without_io(service: PendingVerificationService) -> None:
    """Initialize the service while avoiding real disk operations."""
    with patch.object(service, "_load_pending_albums", new=AsyncMock()), patch.object(
        service,
        "_normalize_pending_album_keys",
        new=AsyncMock(),
    ):
        await service.initialize()


@pytest.fixture
def config(tmp_path: Path) -> dict[str, Any]:
    """Provide a minimal configuration dictionary."""
    return {
        "logs_base_dir": str(tmp_path),
        "logging": {
            "pending_verification_file": "pending_verification.csv",
        },
        "year_retrieval": {
            "processing": {
                "pending_verification_interval_days": 30,
                "prerelease_recheck_days": 7,
            },
        },
    }


@pytest.fixture
def console_logger() -> MagicMock:
    """Return a mock console logger."""
    return MagicMock()


@pytest.fixture
def error_logger() -> MagicMock:
    """Return a mock error logger."""
    return MagicMock()


@pytest.fixture
def service(
    config: dict[str, Any],
    console_logger: MagicMock,
    error_logger: MagicMock,
) -> PendingVerificationService:
    """Create a PendingVerificationService instance for testing."""
    return PendingVerificationService(config, console_logger, error_logger)


@pytest.mark.asyncio
async def test_initialization(service: PendingVerificationService) -> None:
    """Service initialization should populate the cache."""
    await initialize_service_without_io(service)
    assert service.pending_albums == {}


def test_generate_album_key(service: PendingVerificationService) -> None:
    """Album key generation should use the unified hash service."""
    key = service.generate_album_key("Test Artist", "Test Album")
    expected = UnifiedHashService.hash_pending_key("Test Artist|Test Album")
    assert key == expected


@pytest.mark.asyncio
async def test_mark_for_verification(service: PendingVerificationService) -> None:
    """Marking an album should store metadata and persist changes."""
    await initialize_service_without_io(service)
    with patch.object(service, "_save_pending_albums", new=AsyncMock()) as save_mock:
        await service.mark_for_verification(
            artist="Test Artist",
            album="Test Album",
            reason="prerelease",
            metadata={"year": 2024},
        )

    key = service.generate_album_key("Test Artist", "Test Album")
    assert key in service.pending_albums
    timestamp, artist, album, reason, metadata_raw = service.pending_albums[key]
    assert isinstance(timestamp, datetime)
    assert artist == "Test Artist"
    assert album == "Test Album"
    assert reason == "prerelease"
    metadata = json.loads(metadata_raw)
    assert metadata["year"] == 2024
    assert metadata["recheck_days"] == 7
    assert save_mock.await_count == 2


@pytest.mark.asyncio
async def test_is_verification_needed(service: PendingVerificationService) -> None:
    """Albums past the verification interval should require a recheck."""
    await initialize_service_without_io(service)
    with patch.object(service, "_save_pending_albums", new=AsyncMock()):
        await service.mark_for_verification(
            artist="A",
            album="B",
        )

    key = service.generate_album_key("A", "B")
    _timestamp, artist, album, reason, metadata = service.pending_albums[key]
    past_time = datetime.now(UTC) - timedelta(days=31)
    service.pending_albums[key] = (past_time, artist, album, reason, metadata)

    needed = await service.is_verification_needed("A", "B")
    assert needed is True


@pytest.mark.asyncio
async def test_is_verification_not_needed(service: PendingVerificationService) -> None:
    """Freshly added albums should not need verification."""
    await initialize_service_without_io(service)
    with patch.object(service, "_save_pending_albums", new=AsyncMock()):
        await service.mark_for_verification(
            artist="A",
            album="B",
        )

    assert await service.is_verification_needed("A", "B") is False
    assert await service.is_verification_needed("Unknown", "Album") is False


@pytest.mark.asyncio
async def test_remove_from_pending(service: PendingVerificationService) -> None:
    """Removing an album should delete it and persist changes."""
    await initialize_service_without_io(service)
    with patch.object(service, "_save_pending_albums", new=AsyncMock()) as save_mock:
        await service.mark_for_verification(
            artist="Test Artist",
            album="Test Album",
        )
        await service.remove_from_pending(
            artist="Test Artist",
            album="Test Album",
        )

    key = service.generate_album_key("Test Artist", "Test Album")
    assert key not in service.pending_albums
    assert save_mock.await_count >= 3


@pytest.mark.asyncio
async def test_get_all_pending_albums(service: PendingVerificationService) -> None:
    """Retrieving all pending albums should return stored tuples."""
    await initialize_service_without_io(service)
    with patch.object(service, "_save_pending_albums", new=AsyncMock()):
        await service.mark_for_verification(
            artist="Artist1",
            album="Album1",
        )
        await service.mark_for_verification(
            artist="Artist2",
            album="Album2",
            reason="prerelease",
            metadata={"note": "promo"},
        )

    pending = await service.get_all_pending_albums()
    assert len(pending) == 2
    pairs = {(artist, album) for _, artist, album, _, _ in pending}
    assert pairs == {("Artist1", "Album1"), ("Artist2", "Album2")}


@pytest.mark.asyncio
async def test_get_pending_albums_by_reason(
    service: PendingVerificationService,
) -> None:
    """Filtering by reason should only return matching albums."""
    await initialize_service_without_io(service)
    with patch.object(service, "_save_pending_albums", new=AsyncMock()):
        await service.mark_for_verification(artist="Artist1", album="Album1")
        await service.mark_for_verification(
            artist="Artist2",
            album="Album2",
            reason="prerelease",
        )
        await service.mark_for_verification(
            artist="Artist3",
            album="Album3",
        )

    no_year = await service.get_pending_albums_by_reason("no_year_found")
    prerelease = await service.get_pending_albums_by_reason("prerelease")

    assert len(no_year) == 2
    assert len(prerelease) == 1
    assert {artist for artist, _, _, _ in no_year} == {"Artist1", "Artist3"}


@pytest.mark.asyncio
async def test_save_and_load_pending_albums(
    service: PendingVerificationService,
) -> None:
    """Saving and loading should persist pending albums."""
    await initialize_service_without_io(service)

    key = service.generate_album_key("Artist", "Album")
    service.pending_albums[key] = (
        datetime(2024, 1, 1, tzinfo=UTC),
        "Artist",
        "Album",
        "no_year_found",
        "",
    )

    with patch("pathlib.Path.open", mock_open()) as mocked_open:
        save_method_name = "_save_pending_albums"
        save_method = cast(Callable[[], Awaitable[None]], getattr(service, save_method_name))
        await save_method()
    handle = mocked_open.return_value.__enter__.return_value
    handle.write.assert_called()

    csv_content = (
        "artist,album,timestamp,reason,metadata\n"
        "Artist,Album,2024-01-01 00:00:00,no_year_found,\n"
    )
    service.pending_albums.clear()

    with patch("os.path.exists", return_value=True), patch(
        "pathlib.Path.open",
        mock_open(read_data=csv_content),
    ):
        load_method_name = "_load_pending_albums"
        load_method = cast(Callable[[], Awaitable[None]], getattr(service, load_method_name))
        await load_method()

    assert len(service.pending_albums) == 1


@pytest.mark.asyncio
async def test_generate_problematic_albums_report(
    service: PendingVerificationService,
) -> None:
    """Problematic album report should count qualifying entries."""
    await initialize_service_without_io(service)
    key = service.generate_album_key("Artist", "Album")
    old_timestamp = datetime.now(UTC) - timedelta(days=120)
    service.pending_albums[key] = (
        old_timestamp,
        "Artist",
        "Album",
        "no_year_found",
        "",
    )

    report_path = Path(service.pending_file_path).with_name("report.csv")
    with patch("pathlib.Path.open", mock_open()) as mocked_open:
        count = await service.generate_problematic_albums_report(
            min_attempts=2,
            report_path=str(report_path),
        )

    assert count == 1
    handle = mocked_open.return_value.__enter__.return_value
    handle.write.assert_called()


@pytest.mark.asyncio
async def test_prerelease_recheck(service: PendingVerificationService) -> None:
    """Prerelease entries should respect recheck interval overrides."""
    await initialize_service_without_io(service)
    with patch.object(service, "_save_pending_albums", new=AsyncMock()):
        await service.mark_for_verification(
            artist="Artist",
            album="Album",
            reason="prerelease",
        )

    key = service.generate_album_key("Artist", "Album")
    _timestamp, artist, album, reason, metadata = service.pending_albums[key]
    recent_time = datetime.now(UTC) - timedelta(days=3)
    service.pending_albums[key] = (recent_time, artist, album, reason, metadata)

    needed = await service.is_verification_needed("Artist", "Album")
    assert needed is False


@pytest.mark.asyncio
async def test_thread_safety(service: PendingVerificationService) -> None:
    """Concurrent updates should maintain dictionary integrity."""
    await initialize_service_without_io(service)

    with patch.object(service, "_save_pending_albums", new=AsyncMock()):

        async def add_album(artist: str, album: str) -> None:
            """Helper to add a pending album."""
            await service.mark_for_verification(
                artist=artist,
                album=album,
            )

        tasks = [add_album(f"Artist{i}", f"Album{i}") for i in range(10)]
        await asyncio.gather(*tasks)

    pending = await service.get_all_pending_albums()
    assert len(pending) == 10


@pytest.mark.asyncio
async def test_csv_file_operations(service: PendingVerificationService) -> None:
    """CSV loading should parse rows with metadata correctly."""
    await initialize_service_without_io(service)

    csv_rows = [
        "artist,album,timestamp,reason,metadata",
        f"Artist1,Album1,2024-01-01 00:00:00,no_year_found,{json.dumps({'source': 'manual'})}",
        f"Artist2,Album2,2024-01-02 00:00:00,prerelease,{json.dumps({'recheck_days': 5})}",
    ]
    csv_content = "\n".join(csv_rows) + "\n"

    with patch("os.path.exists", return_value=True), patch(
        "pathlib.Path.open",
        mock_open(read_data=csv_content),
    ):
        load_method_name = "_load_pending_albums"
        load_method = cast(Callable[[], Awaitable[None]], getattr(service, load_method_name))
        await load_method()

    assert len(service.pending_albums) == 2
    values = list(service.pending_albums.values())
    assert any(json.loads(item[-1]).get("source") == "manual" for item in values)


@pytest.mark.asyncio
async def test_error_handling(service: PendingVerificationService) -> None:
    """Invalid CSV rows should be ignored without raising exceptions."""
    await initialize_service_without_io(service)

    csv_content = (
        "artist,album,timestamp,reason,metadata\n"
        "Artist,,2024-01-01 00:00:00,no_year_found,\n"
    )

    with patch("os.path.exists", return_value=True), patch(
        "pathlib.Path.open",
        mock_open(read_data=csv_content),
    ):
        load_method_name = "_load_pending_albums"
        load_method = cast(Callable[[], Awaitable[None]], getattr(service, load_method_name))
        await load_method()

    assert service.pending_albums == {}
