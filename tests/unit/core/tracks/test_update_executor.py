"""Tests for TrackUpdateExecutor - track metadata update operations."""

import logging
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.models.track_models import TrackDict
from core.models.validators import SecurityValidationError, SecurityValidator
from core.tracks.update_executor import TrackUpdateExecutor

if TYPE_CHECKING:
    from core.models.protocols import CacheServiceProtocol
    from core.models.types import AppleScriptClientProtocol


@pytest.fixture
def logger() -> logging.Logger:
    """Create a test logger."""
    return logging.getLogger("test.update_executor")


@pytest.fixture
def error_logger() -> logging.Logger:
    """Create a test error logger."""
    return logging.getLogger("test.update_executor.error")


@pytest.fixture
def mock_ap_client() -> AsyncMock:
    """Create a mock AppleScript client."""
    client = AsyncMock()
    client.run_script = AsyncMock(return_value="Success: updated")
    return client


@pytest.fixture
def mock_cache_service() -> AsyncMock:
    """Create a mock cache service."""
    service = AsyncMock()
    service.invalidate_for_track = AsyncMock()
    return service


@pytest.fixture
def mock_security_validator() -> MagicMock:
    """Create a mock security validator."""
    validator = MagicMock(spec=SecurityValidator)
    validator.sanitize_string = MagicMock(side_effect=lambda x, _: x)
    return validator


@pytest.fixture
def config() -> dict[str, Any]:
    """Create test configuration."""
    return {
        "applescript_timeouts": {"batch_update": 60},
        "experimental": {"batch_updates_enabled": False, "max_batch_size": 5},
    }


@pytest.fixture
def executor(
    mock_ap_client: AsyncMock,
    mock_cache_service: AsyncMock,
    mock_security_validator: MagicMock,
    config: dict[str, Any],
    logger: logging.Logger,
    error_logger: logging.Logger,
) -> TrackUpdateExecutor:
    """Create a TrackUpdateExecutor instance."""
    return TrackUpdateExecutor(
        ap_client=cast("AppleScriptClientProtocol", cast(object, mock_ap_client)),
        cache_service=cast("CacheServiceProtocol", cast(object, mock_cache_service)),
        security_validator=mock_security_validator,
        config=config,
        console_logger=logger,
        error_logger=error_logger,
        analytics=MagicMock(),
    )


@pytest.fixture
def dry_run_executor(
    mock_ap_client: AsyncMock,
    mock_cache_service: AsyncMock,
    mock_security_validator: MagicMock,
    config: dict[str, Any],
    logger: logging.Logger,
    error_logger: logging.Logger,
) -> TrackUpdateExecutor:
    """Create a TrackUpdateExecutor instance in dry-run mode."""
    return TrackUpdateExecutor(
        ap_client=cast("AppleScriptClientProtocol", cast(object, mock_ap_client)),
        cache_service=cast("CacheServiceProtocol", cast(object, mock_cache_service)),
        security_validator=mock_security_validator,
        config=config,
        console_logger=logger,
        error_logger=error_logger,
        analytics=MagicMock(),
        dry_run=True,
    )


class TestInit:
    """Tests for TrackUpdateExecutor initialization."""

    def test_init_default(self, executor: TrackUpdateExecutor) -> None:
        """Test default initialization."""
        assert executor.dry_run is False
        assert executor._dry_run_actions == []

    def test_init_dry_run(self, dry_run_executor: TrackUpdateExecutor) -> None:
        """Test initialization with dry_run=True."""
        assert dry_run_executor.dry_run is True


class TestSetDryRun:
    """Tests for set_dry_run method."""

    def test_set_dry_run_true(self, executor: TrackUpdateExecutor) -> None:
        """Test setting dry run to True."""
        executor.set_dry_run(True)
        assert executor.dry_run is True

    def test_set_dry_run_false_clears_actions(self, dry_run_executor: TrackUpdateExecutor) -> None:
        """Test setting dry run to False clears recorded actions."""
        dry_run_executor._dry_run_actions = [{"action": "test"}]
        dry_run_executor.set_dry_run(False)
        assert dry_run_executor.dry_run is False
        assert not dry_run_executor._dry_run_actions


class TestGetDryRunActions:
    """Tests for get_dry_run_actions method."""

    def test_returns_empty_list_initially(self, executor: TrackUpdateExecutor) -> None:
        """Test returns empty list when no actions recorded."""
        assert executor.get_dry_run_actions() == []

    def test_returns_recorded_actions(self, dry_run_executor: TrackUpdateExecutor) -> None:
        """Test returns recorded actions."""
        action = {"action": "update_track", "track_id": "123"}
        dry_run_executor._dry_run_actions.append(action)
        assert dry_run_executor.get_dry_run_actions() == [action]


class TestIsReadOnlyTrack:
    """Tests for _is_read_only_track method."""

    def test_returns_false_for_none_status(self, executor: TrackUpdateExecutor) -> None:
        """Test returns False when status is None (editable)."""
        result = executor._is_read_only_track(None, "track_123")
        assert result is False

    def test_returns_false_for_purchased_status(self, executor: TrackUpdateExecutor) -> None:
        """Test returns False for purchased tracks."""
        result = executor._is_read_only_track("Purchased", "track_123")
        assert result is False

    def test_returns_true_for_prerelease_status(self, executor: TrackUpdateExecutor) -> None:
        """Test returns True for prerelease tracks (read-only)."""
        result = executor._is_read_only_track("Prerelease", "track_123")
        assert result is True

    def test_logs_when_read_only(self, executor: TrackUpdateExecutor) -> None:
        """Test logs message when track is read-only."""
        with patch.object(executor.console_logger, "info") as mock_log:
            executor._is_read_only_track("Prerelease", "track_123")
            mock_log.assert_called_once()


class TestUpdateProperty:
    """Tests for _update_property method."""

    @pytest.mark.asyncio
    async def test_success_returns_true_true(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test successful update returns (True, True)."""
        mock_ap_client.run_script.return_value = "Success: updated genre"
        success, changed = await executor._update_property("123", "genre", "Rock")
        assert success is True
        assert changed is True

    @pytest.mark.asyncio
    async def test_no_change_returns_true_false(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test no-change result returns (True, False)."""
        mock_ap_client.run_script.return_value = "No Change: genre already set"
        success, changed = await executor._update_property("123", "genre", "Rock")
        assert success is True
        assert changed is False

    @pytest.mark.asyncio
    async def test_failure_returns_false_false(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test failure result returns (False, False)."""
        mock_ap_client.run_script.return_value = "Error: track not found"
        success, changed = await executor._update_property("123", "genre", "Rock")
        assert success is False
        assert changed is False

    @pytest.mark.asyncio
    async def test_none_result_returns_false_false(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test None result returns (False, False)."""
        mock_ap_client.run_script.return_value = None
        success, changed = await executor._update_property("123", "genre", "Rock")
        assert success is False
        assert changed is False

    @pytest.mark.asyncio
    async def test_exception_returns_false_false(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test exception returns (False, False)."""
        mock_ap_client.run_script.side_effect = OSError("Script failed")
        success, changed = await executor._update_property("123", "genre", "Rock")
        assert success is False
        assert changed is False

    @pytest.mark.asyncio
    async def test_passes_context_to_script(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test passes artist/album/track context to script."""
        mock_ap_client.run_script.return_value = "Success"
        await executor._update_property("123", "genre", "Rock", artist="Beatles", album="Abbey Road", track_name="Come Together")
        mock_ap_client.run_script.assert_called_with(
            "update_property.applescript",
            ["123", "genre", "Rock"],
            timeout=30,
            context_artist="Beatles",
            context_album="Abbey Road",
            context_track="Come Together",
        )


class TestProcessUpdateResult:
    """Tests for _process_update_result method."""

    def test_success_result(self, executor: TrackUpdateExecutor) -> None:
        """Test processing Success result."""
        success, changed = executor._process_update_result("Success: updated", "genre", "123")
        assert success is True
        assert changed is True

    def test_no_change_result(self, executor: TrackUpdateExecutor) -> None:
        """Test processing No Change result."""
        success, changed = executor._process_update_result("No Change: same value", "genre", "123")
        assert success is True
        assert changed is False

    def test_error_result(self, executor: TrackUpdateExecutor) -> None:
        """Test processing error result."""
        success, changed = executor._process_update_result("Error: failed", "genre", "123")
        assert success is False
        assert changed is False

    def test_none_result(self, executor: TrackUpdateExecutor) -> None:
        """Test processing None result."""
        success, changed = executor._process_update_result(None, "genre", "123")
        assert success is False
        assert changed is False


class TestValidateAndSanitizeUpdateParameters:
    """Tests for _validate_and_sanitize_update_parameters method."""

    def test_valid_parameters(self, executor: TrackUpdateExecutor) -> None:
        """Test valid parameters are sanitized."""
        result = executor._validate_and_sanitize_update_parameters("123", "Track Name", "Album Name", "Rock", "2020")
        assert result is not None
        track_id, name, album, genre, year = result
        assert track_id == "123"
        assert name == "Track Name"
        assert album == "Album Name"
        assert genre == "Rock"
        assert year == "2020"

    def test_none_optional_parameters(self, executor: TrackUpdateExecutor) -> None:
        """Test None optional parameters stay None."""
        result = executor._validate_and_sanitize_update_parameters("123", None, None, None, None)
        assert result is not None
        track_id, name, album, genre, year = result
        assert track_id == "123"
        assert name is None
        assert album is None
        assert genre is None
        assert year is None

    def test_security_validation_error(self, executor: TrackUpdateExecutor, mock_security_validator: MagicMock) -> None:
        """Test returns None on security validation error."""
        mock_security_validator.sanitize_string.side_effect = SecurityValidationError("Invalid")
        result = executor._validate_and_sanitize_update_parameters("123<script>", "Track", None, None, None)
        assert result is None


class TestHandleDryRunUpdate:
    """Tests for _handle_dry_run_update method."""

    def test_records_all_updates(self, dry_run_executor: TrackUpdateExecutor) -> None:
        """Test records all provided updates."""
        result = dry_run_executor._handle_dry_run_update("123", "Track Name", "Album Name", "Rock", "2020")
        assert result is True
        actions = dry_run_executor.get_dry_run_actions()
        assert len(actions) == 1
        assert actions[0]["track_id"] == "123"
        assert actions[0]["updates"]["name"] == "Track Name"
        assert actions[0]["updates"]["album"] == "Album Name"
        assert actions[0]["updates"]["genre"] == "Rock"
        assert actions[0]["updates"]["year"] == "2020"

    def test_records_artist_update(self, dry_run_executor: TrackUpdateExecutor) -> None:
        """Test records artist update."""
        result = dry_run_executor._handle_dry_run_update("123", None, None, None, None, sanitized_artist_name="New Artist")
        assert result is True
        actions = dry_run_executor.get_dry_run_actions()
        assert actions[0]["updates"]["artist"] == "New Artist"

    def test_skips_none_values(self, dry_run_executor: TrackUpdateExecutor) -> None:
        """Test skips None values in updates dict."""
        dry_run_executor._handle_dry_run_update("123", None, "Album", None, None)
        actions = dry_run_executor.get_dry_run_actions()
        assert "name" not in actions[0]["updates"]
        assert "album" in actions[0]["updates"]
        assert "genre" not in actions[0]["updates"]
        assert "year" not in actions[0]["updates"]


class TestUpdateSingleProperty:
    """Tests for _update_single_property method."""

    @pytest.mark.asyncio
    async def test_logs_on_success_with_change(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test logs success message when change made."""
        mock_ap_client.run_script.return_value = "Success: updated"
        with patch.object(executor.console_logger, "info") as mock_log:
            result = await executor._update_single_property("123", "genre", "Rock")
            assert result is True
            mock_log.assert_called()

    @pytest.mark.asyncio
    async def test_logs_debug_on_no_change(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test logs debug message when no change needed."""
        mock_ap_client.run_script.return_value = "No Change"
        with patch.object(executor.console_logger, "debug") as mock_log:
            result = await executor._update_single_property("123", "genre", "Rock")
            assert result is True
            mock_log.assert_called()

    @pytest.mark.asyncio
    async def test_logs_warning_on_failure(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test logs warning message on failure."""
        mock_ap_client.run_script.return_value = "Error"
        with patch.object(executor.console_logger, "warning") as mock_log:
            result = await executor._update_single_property("123", "genre", "Rock")
            assert result is False
            mock_log.assert_called()


class TestPerformPropertyUpdates:
    """Tests for _perform_property_updates method."""

    @pytest.mark.asyncio
    async def test_updates_all_properties(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test updates all provided properties."""
        mock_ap_client.run_script.return_value = "Success"
        result = await executor._perform_property_updates("123", "Track", "Album", "Rock", "2020")
        assert result is True
        assert mock_ap_client.run_script.call_count == 4

    @pytest.mark.asyncio
    async def test_skips_none_properties(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test skips None properties."""
        mock_ap_client.run_script.return_value = "Success"
        result = await executor._perform_property_updates("123", None, None, "Rock", None)
        assert result is True
        assert mock_ap_client.run_script.call_count == 1


class TestResolveUpdatedArtist:
    """Tests for _resolve_updated_artist method."""

    def test_returns_artist_from_updates(self) -> None:
        """Test returns artist value from updates list."""
        updates = [("genre", "Rock"), ("artist", "New Artist"), ("year", "2020")]
        result = TrackUpdateExecutor._resolve_updated_artist(updates, "Old Artist")
        assert result == "New Artist"

    def test_returns_original_if_no_artist_update(self) -> None:
        """Test returns original artist if no artist in updates."""
        updates = [("genre", "Rock"), ("year", "2020")]
        result = TrackUpdateExecutor._resolve_updated_artist(updates, "Original Artist")
        assert result == "Original Artist"


class TestNotifyTrackCacheInvalidation:
    """Tests for _notify_track_cache_invalidation method."""

    @pytest.mark.asyncio
    async def test_invalidates_cache(self, executor: TrackUpdateExecutor, mock_cache_service: AsyncMock) -> None:
        """Test calls cache invalidation."""
        await executor._notify_track_cache_invalidation("123", "Artist", "Album", "Track")
        mock_cache_service.invalidate_for_track.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_cache_exception(self, executor: TrackUpdateExecutor, mock_cache_service: AsyncMock) -> None:
        """Test handles cache exception gracefully."""
        mock_cache_service.invalidate_for_track.side_effect = Exception("Cache error")
        # Should not raise
        await executor._notify_track_cache_invalidation("123", "Artist", "Album", "Track")

    @pytest.mark.asyncio
    async def test_includes_original_artist(self, executor: TrackUpdateExecutor, mock_cache_service: AsyncMock) -> None:
        """Test includes original artist in payload."""
        await executor._notify_track_cache_invalidation("123", "New Artist", "Album", "Track", original_artist="Old Artist")
        call_args = mock_cache_service.invalidate_for_track.call_args
        payload = call_args[0][0]
        assert payload.__dict__.get("original_artist") == "Old Artist"


class TestTryBatchUpdate:
    """Tests for _try_batch_update method."""

    @pytest.mark.asyncio
    async def test_success(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test successful batch update."""
        mock_ap_client.run_script.return_value = "Success: batch updated"
        updates = [("genre", "Rock"), ("year", "2020")]
        result = await executor._try_batch_update("123", updates)
        assert result is True

    @pytest.mark.asyncio
    async def test_failure_raises(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test failed batch update raises RuntimeError."""
        mock_ap_client.run_script.return_value = "Error: failed"
        updates = [("genre", "Rock"), ("year", "2020")]
        with pytest.raises(RuntimeError):
            await executor._try_batch_update("123", updates)

    @pytest.mark.asyncio
    async def test_builds_correct_command(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test builds correct batch command string with ASCII separators."""
        mock_ap_client.run_script.return_value = "Success"
        updates = [("genre", "Rock"), ("year", "2020")]
        await executor._try_batch_update("123", updates)
        call_args = mock_ap_client.run_script.call_args
        command = call_args[0][1][0]
        # ASCII 30 (Record Separator) between fields, ASCII 29 (Group Separator) between commands
        field_sep = chr(30)
        cmd_sep = chr(29)
        expected_genre = f"123{field_sep}genre{field_sep}Rock"
        expected_year = f"123{field_sep}year{field_sep}2020"
        assert expected_genre in command
        assert expected_year in command
        assert cmd_sep in command  # Commands should be separated

    @pytest.mark.asyncio
    async def test_invalid_timeout_raises(self, executor: TrackUpdateExecutor) -> None:
        """Test invalid timeout configuration raises ValueError."""
        executor.config["applescript_timeouts"]["batch_update"] = -1
        updates = [("genre", "Rock")]
        with pytest.raises(ValueError, match="Non-positive"):
            await executor._try_batch_update("123", updates)

    @pytest.mark.asyncio
    async def test_invalid_timeout_string_uses_default(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test invalid timeout string falls back to default."""
        executor.config["applescript_timeouts"]["batch_update"] = "invalid"
        mock_ap_client.run_script.return_value = "Success"
        updates = [("genre", "Rock")]
        # Should not raise, uses default 60.0
        result = await executor._try_batch_update("123", updates)
        assert result is True

    @pytest.mark.asyncio
    async def test_fallback_to_applescript_timeout_seconds(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test falls back to applescript_timeout_seconds when batch_update not set."""
        # Remove batch_update from config to trigger fallback
        del executor.config["applescript_timeouts"]["batch_update"]
        executor.config["applescript_timeout_seconds"] = 120
        mock_ap_client.run_script.return_value = "Success"
        updates = [("genre", "Rock")]
        result = await executor._try_batch_update("123", updates)
        assert result is True
        # Verify the timeout was used (120.0)
        call_kwargs = mock_ap_client.run_script.call_args[1]
        assert call_kwargs["timeout"] == 120.0

    @pytest.mark.asyncio
    async def test_handles_special_characters_in_values(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test ASCII separators handle special characters that would break old format.

        Old format used ':' and ';' as delimiters which could conflict with
        genre values like "Rock: Classic; Alternative". ASCII 30/29 separators
        don't appear in metadata, making them safe for any content.
        """
        mock_ap_client.run_script.return_value = "Success"
        # Genre with colons and semicolons - would break old format
        updates = [("genre", "Rock: Classic; Alternative")]
        await executor._try_batch_update("123", updates)
        call_args = mock_ap_client.run_script.call_args
        command = call_args[0][1][0]
        # The value should be preserved intact
        assert "Rock: Classic; Alternative" in command
        # ASCII separators should be present
        assert chr(30) in command  # Field separator


class TestApplyTrackUpdates:
    """Tests for _apply_track_updates method."""

    @pytest.mark.asyncio
    async def test_empty_updates_returns_true(self, executor: TrackUpdateExecutor) -> None:
        """Test empty updates list returns True."""
        result = await executor._apply_track_updates("123", [])
        assert result is True

    @pytest.mark.asyncio
    async def test_individual_updates_when_batch_disabled(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test uses individual updates when batch disabled."""
        mock_ap_client.run_script.return_value = "Success"
        updates = [("genre", "Rock"), ("year", "2020")]
        result = await executor._apply_track_updates("123", updates)
        assert result is True
        # Should call run_script once per update
        assert mock_ap_client.run_script.call_count == 2

    @pytest.mark.asyncio
    async def test_batch_update_when_enabled(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test uses batch update when enabled."""
        executor.config["experimental"]["batch_updates_enabled"] = True
        mock_ap_client.run_script.return_value = "Success"
        updates = [("genre", "Rock"), ("year", "2020")]
        result = await executor._apply_track_updates("123", updates)
        assert result is True
        # Batch update should be called once with batch_update_tracks.applescript
        assert mock_ap_client.run_script.call_count == 1
        call_args = mock_ap_client.run_script.call_args
        assert call_args[0][0] == "batch_update_tracks.applescript"

    @pytest.mark.asyncio
    async def test_falls_back_on_batch_failure(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test falls back to individual updates on batch failure."""
        executor.config["experimental"]["batch_updates_enabled"] = True
        # First call (batch) fails, subsequent calls (individual) succeed
        mock_ap_client.run_script.side_effect = [
            "Error: batch failed",  # batch fails
            "Success",  # individual update 1
            "Success",  # individual update 2
        ]
        updates = [("genre", "Rock"), ("year", "2020")]
        result = await executor._apply_track_updates("123", updates)
        assert result is True
        assert mock_ap_client.run_script.call_count == 3

    @pytest.mark.asyncio
    async def test_invalidates_cache_on_success(
        self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock, mock_cache_service: AsyncMock
    ) -> None:
        """Test invalidates cache after successful updates."""
        mock_ap_client.run_script.return_value = "Success"
        updates = [("genre", "Rock")]
        await executor._apply_track_updates("123", updates, "Artist", "Album", "Track")
        mock_cache_service.invalidate_for_track.assert_called_once()


class TestUpdateTrackAsync:
    """Tests for update_track_async method."""

    @pytest.mark.asyncio
    async def test_success(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test successful track update."""
        mock_ap_client.run_script.return_value = "Success"
        result = await executor.update_track_async("123", new_genre="Rock")
        assert result is True

    @pytest.mark.asyncio
    async def test_skips_read_only_track(self, executor: TrackUpdateExecutor) -> None:
        """Test skips read-only tracks (prerelease status)."""
        result = await executor.update_track_async("123", new_genre="Rock", track_status="Prerelease")
        assert result is False

    @pytest.mark.asyncio
    async def test_validation_failure(self, executor: TrackUpdateExecutor, mock_security_validator: MagicMock) -> None:
        """Test returns False on validation failure."""
        mock_security_validator.sanitize_string.side_effect = SecurityValidationError("Invalid")
        result = await executor.update_track_async("123", new_genre="Rock")
        assert result is False

    @pytest.mark.asyncio
    async def test_dry_run_mode(self, dry_run_executor: TrackUpdateExecutor) -> None:
        """Test dry run mode records action."""
        result = await dry_run_executor.update_track_async("123", new_genre="Rock")
        assert result is True
        actions = dry_run_executor.get_dry_run_actions()
        assert len(actions) == 1


class TestPrepareArtistUpdate:
    """Tests for _prepare_artist_update method."""

    def test_returns_none_for_empty_track_id(self, executor: TrackUpdateExecutor) -> None:
        """Test returns None when track has no ID."""
        track = TrackDict(id="", name="Track", artist="Artist", album="Album", genre="Rock", year="2020")
        result = executor._prepare_artist_update(track, "New Artist")
        assert result is None

    def test_returns_none_for_empty_new_artist(self, executor: TrackUpdateExecutor) -> None:
        """Test returns None when new artist is empty."""
        track = TrackDict(id="123", name="Track", artist="Artist", album="Album", genre="Rock", year="2020")
        result = executor._prepare_artist_update(track, "")
        assert result is None

    def test_returns_none_when_same_artist(self, executor: TrackUpdateExecutor) -> None:
        """Test returns None when artist already matches."""
        track = TrackDict(id="123", name="Track", artist="Artist", album="Album", genre="Rock", year="2020")
        result = executor._prepare_artist_update(track, "Artist")
        assert result is None

    def test_returns_none_for_read_only_track(self, executor: TrackUpdateExecutor) -> None:
        """Test returns None for read-only tracks (prerelease status)."""
        track = TrackDict(id="123", name="Track", artist="Artist", album="Album", genre="Rock", year="2020", track_status="Prerelease")
        result = executor._prepare_artist_update(track, "New Artist")
        assert result is None

    def test_returns_tuple_for_valid_update(self, executor: TrackUpdateExecutor) -> None:
        """Test returns tuple for valid update."""
        track = TrackDict(id="123", name="Track", artist="Old Artist", album="Album", genre="Rock", year="2020")
        result = executor._prepare_artist_update(track, "New Artist")
        assert result is not None
        sanitized_id, sanitized_artist, current_artist = result
        assert sanitized_id == "123"
        assert sanitized_artist == "New Artist"
        assert current_artist == "Old Artist"

    def test_returns_none_on_security_error(self, executor: TrackUpdateExecutor, mock_security_validator: MagicMock) -> None:
        """Test returns None on security validation error."""
        mock_security_validator.sanitize_string.side_effect = SecurityValidationError("Invalid")
        track = TrackDict(id="123", name="Track", artist="Old Artist", album="Album", genre="Rock", year="2020")
        result = executor._prepare_artist_update(track, "New Artist")
        assert result is None


class TestUpdateArtistAsync:
    """Tests for update_artist_async method."""

    @pytest.mark.asyncio
    async def test_success(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test successful artist update."""
        mock_ap_client.run_script.return_value = "Success"
        track = TrackDict(id="123", name="Track", artist="Old Artist", album="Album", genre="Rock", year="2020")
        result = await executor.update_artist_async(track, "New Artist")
        assert result is True
        assert track.artist == "New Artist"

    @pytest.mark.asyncio
    async def test_returns_false_on_prepare_failure(self, executor: TrackUpdateExecutor) -> None:
        """Test returns False when prepare fails."""
        track = TrackDict(id="", name="Track", artist="Artist", album="Album", genre="Rock", year="2020")
        result = await executor.update_artist_async(track, "New Artist")
        assert result is False

    @pytest.mark.asyncio
    async def test_dry_run_mode(self, dry_run_executor: TrackUpdateExecutor) -> None:
        """Test dry run mode records action."""
        track = TrackDict(id="123", name="Track", artist="Old Artist", album="Album", genre="Rock", year="2020")
        result = await dry_run_executor.update_artist_async(track, "New Artist")
        assert result is True
        actions = dry_run_executor.get_dry_run_actions()
        assert len(actions) == 1
        assert actions[0]["updates"]["artist"] == "New Artist"

    @pytest.mark.asyncio
    async def test_update_album_artist_when_matches(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test updates album_artist when it matches old artist."""
        mock_ap_client.run_script.return_value = "Success"
        track = TrackDict(id="123", name="Track", artist="Old Artist", album="Album", genre="Rock", year="2020")
        track.album_artist = "Old Artist"
        result = await executor.update_artist_async(track, "New Artist", update_album_artist=True)
        assert result is True
        assert track.artist == "New Artist"
        assert track.album_artist == "New Artist"

    @pytest.mark.asyncio
    async def test_does_not_update_album_artist_when_different(self, executor: TrackUpdateExecutor, mock_ap_client: AsyncMock) -> None:
        """Test does not update album_artist when it doesn't match."""
        mock_ap_client.run_script.return_value = "Success"
        track = TrackDict(id="123", name="Track", artist="Old Artist", album="Album", genre="Rock", year="2020")
        track.album_artist = "Different Artist"
        result = await executor.update_artist_async(track, "New Artist", update_album_artist=True)
        assert result is True
        assert track.artist == "New Artist"
        # album_artist should not be changed
        assert track.album_artist == "Different Artist"
