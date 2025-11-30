"""Mock infrastructure for Music Genre Updater tests."""

from __future__ import annotations

from tests.mocks.protocol_mocks import MockAppleScriptClient
from tests.mocks.track_data import DummyTrackData, FakeTrackMap, MockCSVLoader

__all__ = [
    "DummyTrackData",
    "FakeTrackMap",
    "MockAppleScriptClient",
    "MockCSVLoader",
]
