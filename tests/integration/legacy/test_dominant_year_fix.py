import pytest
pytestmark = pytest.mark.integration

#!/usr/bin/env python3
"""Test dominant year fix for collaboration tracks."""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.domain.tracks.year_retriever import YearRetriever
from src.shared.data.models import TrackDict

def test_dominant_year_scenario() -> None:
    """Test scenario where collaborations should get dominant year."""
    print("🧪 Testing Dominant Year Fix for Collaborations\n")

    # Mock the albumに scenario that SHOULD trigger dominant year logic:
    # Album "Радіопромінь" with mixed tracks - some with year, some without
    album_tracks = [
        # Track with year - should provide dominant year
        TrackDict(
            id="1",
            artist="Жадан і сбаки", 
            album="Радіопрмінь", 
            name="Основний трек",
            year="2018"  # This should be the dominant year
        ),

        # Collaboration tracks WITHOUT years - should GET dominant year
        TrackDict(
            id="2",
            artist="Жадан і собаки & Khrystya Soloviy", 
            album="Радіопрмінь", 
            name="Радіопромінь",
            year=""  # Empty year - should get 2018
        ),
        TrackDict(
            id="3",
            artist="Жадан і собак & Qarpa", 
            album="Радіопрмінь", 
            name="Другий трек",
            year=""  # Empty year - should get 2018
        ),
    ]

    print("📊 Album tracks:")
    for track in album_tracks:
        print(f"  - '{track.get('name')}' by '{track.get('artist')}' (year: '{track.get('year')}')")

    # Test 1: Check dominant year detection
    print("\n🔍 Testing dominant year detection:")
    retriever = YearRetriever(None, None, None, None, None, None, None, {}, False)
    dominant_year = retriever._get_dominant_year(album_tracks)
    print(f"  Dominant year detected: {dominant_year}")

    # Test 2: Check which tracks need updates
    if dominant_year:
        tracks_needing_update = [
            track for track in album_tracks
            if not track.get("year") or not str(track.get("year", "")).strip()
        ]
        print(f"\n📝 Tracks needing year update: {len(tracks_needing_update)}")
        for track in tracks_needing_update:
            print(f"  - '{track.get('name')}' by '{track.get('artist')}'")

        print("\n✅ Expected behavior:":")
        print(f"  - Dominant year '2018' should be applied to {len(tracks_needing_update)} collaboration tracks")
        print("  - This prevents them from going to pending verification")
        print("  - No API calls needed for these tracks!")

def main() -> None:
    """Run dominant year fix test."""
    test_dominant_year_scenario()
    print("\n🎯 This fix should resolve the collaboration year issue globally!")

if __name__ == "__main__":
    main()
