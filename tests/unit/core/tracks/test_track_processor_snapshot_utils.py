from __future__ import annotations

from src.core.tracks.processor import TrackProcessor
from src.core.models.track import TrackDict


def test_merge_tracks_replaces_and_appends() -> None:
    existing = [
        TrackDict(id="1", name="Alpha", artist="Artist", album="Album"),
        TrackDict(id="2", name="Beta", artist="Artist", album="Album"),
    ]
    updates = [
        TrackDict(id="1", name="Alpha (Remix)", artist="Artist", album="Album"),
        TrackDict(id="3", name="Gamma", artist="Artist", album="Album"),
    ]

    merged = TrackProcessor._merge_tracks(existing, updates)

    assert [track.id for track in merged] == ["1", "2", "3"]
    assert merged[0].name == "Alpha (Remix)"
    assert merged[2].name == "Gamma"
