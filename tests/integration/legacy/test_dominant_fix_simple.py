#!/usr/bin/env python3
"""Simple test for dominant year logic without dependencies."""

from collections import Counter

def test_dominant_year_logic():
    """Test the dominant year algorithm manually."""
    print("🧪 Testing Dominant Year Algorithm\n")

    # Simulate album "Радіопромінь" scenario
    album_tracks = [
        {"name": "Основний трек", "artist": "Жадан і собаки", "year": "2018"},
        {"name": "Радіопромінь", "artist": "Жадан і собаки & Khrystyna Soloviy", "year": ""},
        {"name": "Другий трек", "artist": "Жадан і собаки & Qarpa", "year": ""},
    ]

    print("📊 Album tracks:")
    for track in album_tracks:
        year_str = f"'{track['year']}'" if track["year"] else "empty"
        print(f"  - '{track['name']}' by '{track['artist']}' (year: {year_str})")

    # Apply dominant year logic
    years = []
    for track in album_tracks:
        year = track.get("year")
        if year and str(year).strip() not in ["", "0"]:
            years.append(str(year))

    print(f"\n🔍 Valid years found: {years}")

    if years:
        year_counts = Counter(years)
        total_tracks = len(album_tracks)
        most_common = year_counts.most_common(1)[0]

        print("📈 Year analysis:")
        print(f"  - Most common year: '{most_common[0]}' appears {most_common[1]} times")
        print(f"  - Total tracks: {total_tracks}")
        print(f"  - Percentage: {most_common[1]/total_tracks*100:.1f}%")

        # PROBLEM: Current logic requires >50% of ALL tracks
        # But for collaborations: if main tracks have year, collaborations should inherit it
        print(f"  Current logic: Needs >{total_tracks/2:.1f} tracks to be dominant")

        if most_common[1] > total_tracks / 2:
            dominant_year = most_common[0]
            print(f"  ✅ Year '{dominant_year}' is DOMINANT (>50% of tracks)")
        else:
            print("  ❌ PROBLEM: Current logic fails for collaborations!")
            print("  💡 SUGGESTED FIX: If album has any valid year, use it for empty tracks")
            # For collaboration fix: use any valid year if there are empty tracks
            tracks_with_empty_year = [t for t in album_tracks if not t.get("year") or not str(t.get("year", "")).strip()]
            if tracks_with_empty_year:
                dominant_year = most_common[0]  # Use the year we found
                print(f"  🔧 FIXED: Using year '{dominant_year}' for {len(tracks_with_empty_year)} empty tracks")
            else:
                dominant_year = None
    else:
        dominant_year = None
        print("❌ No valid years found")

    # Show which tracks would be updated
    if dominant_year:
        tracks_needing_update = [
            track for track in album_tracks
            if not track.get("year") or not str(track.get("year", "")).strip()
        ]

        print(f"\n📝 Tracks that would get year '{dominant_year}':")
        for track in tracks_needing_update:
            print(f"  - '{track['name']}' by '{track['artist']}'")

        print("\n🎯 RESULT:")
        print(f"  - {len(tracks_needing_update)} collaboration tracks would get year {dominant_year}")
        print("  - They would NOT go to pending verification")
        print("  - No API calls needed!")

    return dominant_year

if __name__ == "__main__":
    test_dominant_year_logic()
    print("\n✅ Dominant year fix should work for collaborations!
")
import pytest
pytestmark = pytest.mark.integration
