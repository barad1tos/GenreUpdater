#!/usr/bin/env python3
"""Test script to verify collaboration grouping logic."""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.core.modules.processing.year_retriever import YearRetriever
from src.utils.data.models import TrackDict

def test_normalization():
    """Test the collaboration normalization function."""
    print("🧪 Testing collaboration normalization...")
    
    test_cases = [
        "Жадан і собаки",
        "Жадан і собаки & Khrystyna Soloviy", 
        "Жадан і собаки & Qarpa",
        "Faun",
        "Faun & Chelsea Wolfe",
        "Artist feat. Someone",
        "Artist ft. Someone",
        "Artist vs. Someone"
    ]
    
    for case in test_cases:
        normalized = YearRetriever._normalize_collaboration_artist(case)
        print(f"  '{case}' → '{normalized}'")

def test_grouping():
    """Test album grouping with mock tracks."""
    print("\n🧪 Testing album grouping...")
    
    # Mock tracks for Жадан і собаки
    mock_tracks = [
        # Regular tracks
        TrackDict(artist="Жадан і собаки", album="Зброя пролетаріату", name="Інтро"),
        TrackDict(artist="Жадан і собаки", album="Зброя пролетаріату", name="Камон мен"),
        
        # Collaboration tracks that should be grouped together
        TrackDict(artist="Жадан і собаки & Khrystyna Soloviy", album="Радіопромінь", name="Радіопромінь"),
        TrackDict(artist="Жадан і собаки & Qarpa", album="Радіопромінь", name="Другий трек"),
        
        # Different artist (should not be grouped)
        TrackDict(artist="Faun", album="Some Album", name="Some Song"),
    ]
    
    # Test grouping
    grouped = YearRetriever._group_tracks_by_album(mock_tracks)
    
    print("  Albums found:")
    for (artist, album), tracks in grouped.items():
        print(f"    '{artist}' - '{album}': {len(tracks)} tracks")
        for track in tracks:
            original_artist = track.get("artist", "")
            print(f"      - '{track.get('name')}' (orig: '{original_artist}')")

def main():
    """Run all tests."""
    print("🚀 Testing Collaboration Grouping Logic\n")
    
    test_normalization()
    test_grouping()
    
    print("\n✅ Testing complete!")

if __name__ == "__main__":
    main()
import pytest
pytestmark = pytest.mark.integration
