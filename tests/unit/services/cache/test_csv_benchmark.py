"""Unit tests covering CSV lookup caching behaviour."""

# ruff: noqa: RUF001

from __future__ import annotations

import csv
import logging
from typing import TYPE_CHECKING

import pytest
from services.cache.generic_cache import GenericCacheService

if TYPE_CHECKING:
    from pathlib import Path


ARTIST_ZHADAN = "Жадан і собаки"
ALBUM_PSY = "Пси"
TRACK_TRAMVAI = "Трамвай"
TRACK_MANIFEST = "Маніфест"


class SimpleCsvArtistIndex:
    """Minimal CSV index wrapper that leverages GenericCacheService for caching."""

    def __init__(self, csv_path: Path, cache_service: GenericCacheService) -> None:
        self.csv_path = csv_path
        self.cache_service = cache_service
        self._read_count = 0

    async def get_tracks_by_artist(self, artist: str) -> list[dict[str, str]]:
        """Return tracks for the given artist, caching results after the first read."""
        cache_key = f"artist::{artist.lower()}"
        cached = self.cache_service.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        tracks = self._read_tracks_for_artist(artist)
        self.cache_service.set(cache_key, tracks)
        return tracks

    def _read_tracks_for_artist(self, artist: str) -> list[dict[str, str]]:
        """Read all tracks for the supplied artist from the CSV file."""
        self._read_count += 1
        with self.csv_path.open(encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            return [row for row in reader if row.get("artist") == artist]

    @property
    def read_count(self) -> int:
        """The number of times the CSV file has been read.

        Returns:
            int: The read count.
        """
        return self._read_count


@pytest.mark.asyncio
async def test_csv_lookup_uses_cache(tmp_path: Path) -> None:
    csv_path = tmp_path / "tracks.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["artist", "album", "name"])
        writer.writeheader()
        writer.writerow({"artist": ARTIST_ZHADAN, "album": ALBUM_PSY, "name": TRACK_TRAMVAI})
        writer.writerow({"artist": ARTIST_ZHADAN, "album": ALBUM_PSY, "name": TRACK_MANIFEST})
        writer.writerow({"artist": "Abney Park", "album": "Æther Shanties", "name": "Under the Radar"})

    logger = logging.getLogger("test.csv_index.cache")
    logger.addHandler(logging.NullHandler())

    cache_service = GenericCacheService({"cleanup_interval": 300}, logger)
    index = SimpleCsvArtistIndex(csv_path, cache_service)

    first_lookup = await index.get_tracks_by_artist(ARTIST_ZHADAN)
    second_lookup = await index.get_tracks_by_artist(ARTIST_ZHADAN)

    assert index.read_count == 1
    assert id(first_lookup) == id(second_lookup)
    assert len(first_lookup) == 2
    assert {row["name"] for row in first_lookup} == {"Трамвай", "Маніфест"}


@pytest.mark.asyncio
async def test_csv_lookup_cache_miss_for_new_artist(tmp_path: Path) -> None:
    csv_path = tmp_path / "tracks.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["artist", "album", "name"])
        writer.writeheader()
        writer.writerow({"artist": "Artist A", "album": "Album", "name": "Track"})

    logger = logging.getLogger("test.csv_index.cache_miss")
    logger.addHandler(logging.NullHandler())

    cache_service = GenericCacheService({"cleanup_interval": 300}, logger)
    index = SimpleCsvArtistIndex(csv_path, cache_service)

    result = await index.get_tracks_by_artist("Unknown Artist")
    assert result == []
    assert index.read_count == 1

    cached = cache_service.get("artist::unknown artist")
    assert cached == []
