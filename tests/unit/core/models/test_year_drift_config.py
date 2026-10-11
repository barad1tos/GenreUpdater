"""The year-drift settings load with the spec's defaults, and a write records why it happened."""

from __future__ import annotations

from core.models.track_models import ChangeLogEntry
from tests.factories import create_test_app_config


def test_processing_defaults_follow_the_spec() -> None:
    processing = create_test_app_config().year_retrieval.processing

    assert (processing.presence_idle_seconds, processing.drift_dialog_timeout_seconds, processing.drift_dialog_max_per_run) == (600, 120, 3)


def test_lock_file_has_a_default_path() -> None:
    assert create_test_app_config().logging.year_locks_file == "csv/year_locks.csv"


def test_a_change_is_a_pipeline_change_unless_said_otherwise() -> None:
    entry = ChangeLogEntry(timestamp="2026-10-11 12:00:00", change_type="year_update", track_id="1", artist="A")

    assert entry.reason == "pipeline"
