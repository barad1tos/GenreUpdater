#!/usr/bin/env python3

"""Tests for validating race condition fix in cache_service.py."""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.infrastructure.cache.cache_service_wrapper import CacheServiceWrapper as CacheService


class TestRaceConditionFix:
    """Tests for validating race condition fix in save_cache()."""

    @pytest.fixture
    def mock_loggers(self):
        """Creates mock loggers."""
        console_logger = MagicMock()
        error_logger = MagicMock()
        return console_logger, error_logger

    @pytest.fixture
    def cache_service(self, mock_loggers):
        """Creates CacheService with temporary files."""
        console_logger, error_logger = mock_loggers

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "cache.json"
            csv_file = Path(temp_dir) / "cache_albums.csv"

            # Configuration with temporary paths
            config = {"cache": {"cache_file": str(cache_file)}, "albums": {"album_cache_csv": str(csv_file)}}

            service = CacheService(config=config, console_logger=console_logger, error_logger=error_logger)
            yield service

    async def test_no_race_condition(self, cache_service):
        """Test that only one executor is called."""
        with patch("asyncio.get_event_loop") as mock_loop:
            mock_executor = AsyncMock()
            mock_loop.return_value.run_in_executor = mock_executor

            # Add data to both caches
            cache_service.cache = {"test_key": ("test_value", None)}
            cache_service.album_years_cache = {"hash1": ("Artist", "Album", "2023")}

            await cache_service.save_cache()

            # Verify that run_in_executor was called only once
            assert mock_executor.call_count == 1
            # Verify that blocking_save_both was called
            args = mock_executor.call_args[0]
            assert args[1] == cache_service.blocking_save_both

    async def test_both_caches_saved(self, cache_service):
        """Test that both cache types are saved."""
        # Add data to both caches
        cache_service.cache = {"test_key": ("test_value", None)}
        cache_service.album_years_cache = {"hash1": ("Artist", "Album", "2023")}

        # Save caches
        await cache_service.save_cache()

        # Verify that files exist
        cache_json = Path(cache_service.cache_file)
        cache_csv = Path(cache_service.album_cache_csv)

        assert cache_json.exists(), "JSON cache not saved"
        assert cache_csv.exists(), "CSV cache not saved"

        # Verify JSON content
        with cache_json.open() as f:
            json_data = json.load(f)
        assert "test_key" in json_data

        # Verify CSV content
        csv_content = cache_csv.read_text()
        assert "Artist" in csv_content
        assert "Album" in csv_content
        assert "2023" in csv_content

    def test_error_isolation_json_fails(self, cache_service):
        """Тестуємо що failure JSON кешу не впливає на CSV."""
        # Додаємо дані в обидва кеші
        cache_service.cache = {"test_key": ("test_value", None)}
        cache_service.album_years_cache = {"hash1": ("Artist", "Album", "2023")}

        # Мокаємо Path.open щоб JSON збереження падало
        with patch("pathlib.Path.open", side_effect=OSError("JSON fail")) as mock_open:
            with patch.object(cache_service, "_write_csv_data") as csv_mock:
                cache_service.blocking_save_both()

                # CSV збереження має спрацювати незважаючи на JSON failure
                csv_mock.assert_called_once()
                # Перевіряємо що JSON збереження було спробовано
                mock_open.assert_called()

    def test_error_isolation_csv_fails(self, cache_service):
        """Тестуємо що failure CSV кешу не впливає на JSON."""
        # Додаємо дані в обидва кеші
        cache_service.cache = {"test_key": ("test_value", None)}
        cache_service.album_years_cache = {"hash1": ("Artist", "Album", "2023")}

        # Мокаємо _write_csv_data щоб CSV збереження падало
        with patch.object(cache_service, "_write_csv_data", side_effect=Exception("CSV fail")):
            cache_service.blocking_save_both()

            # JSON має зберегтись
            cache_json = Path(cache_service.cache_file)
            assert cache_json.exists(), "JSON кеш має зберегтись навіть якщо CSV падає"

    def test_empty_caches_handling(self, cache_service):
        """Тестуємо обробку порожніх кешів."""
        # Порожні кеші
        cache_service.cache = {}
        cache_service.album_years_cache = {}

        # Викликаємо blocking_save_both
        cache_service.blocking_save_both()

        # Перевіряємо логування
        cache_service.console_logger.debug.assert_called_with("Немає даних для збереження")

    def test_json_only_cache(self, cache_service):
        """Тестуємо збереження тільки JSON кешу."""
        # Тільки JSON дані
        cache_service.cache = {"test_key": ("test_value", None)}
        cache_service.album_years_cache = {}

        cache_service.blocking_save_both()

        # Перевіряємо що JSON збережено
        cache_json = Path(cache_service.cache_file)
        assert cache_json.exists()

        # Перевіряємо логування
        cache_service.console_logger.warning.assert_called_with("⚠️ Збережено тільки JSON кеш")

    def test_csv_only_cache(self, cache_service):
        """Тестуємо збереження тільки CSV кешу."""
        # Тільки CSV дані
        cache_service.cache = {}
        cache_service.album_years_cache = {"hash1": ("Artist", "Album", "2023")}

        cache_service.blocking_save_both()

        # Перевіряємо що CSV збережено
        cache_csv = Path(cache_service.album_cache_csv)
        assert cache_csv.exists()

        # Перевіряємо логування
        cache_service.console_logger.warning.assert_called_with("⚠️ Збережено тільки CSV кеш")

    def test_successful_both_caches(self, cache_service):
        """Тестуємо успішне збереження обох кешів."""
        # Дані в обидва кеші
        cache_service.cache = {"test_key": ("test_value", None)}
        cache_service.album_years_cache = {"hash1": ("Artist", "Album", "2023")}

        cache_service.blocking_save_both()

        # Перевіряємо логування успіху
        cache_service.console_logger.info.assert_called_with("✅ Обидва кеші успішно збережено")

    async def test_save_cache_early_return(self, cache_service):
        """Тестуємо early return коли немає даних."""
        # Порожні кеші
        cache_service.cache = {}
        cache_service.album_years_cache = {}

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_executor = AsyncMock()
            mock_loop.return_value.run_in_executor = mock_executor

            await cache_service.save_cache()

            # Executor не має викликатись
            mock_executor.assert_not_called()
            # Перевіряємо логування
            cache_service.console_logger.debug.assert_called_with("Немає даних для збереження")

    def test_alias_method(self, cache_service):
        """Тестуємо що alias метод працює."""
        # Додаємо CSV дані
        cache_service.album_years_cache = {"hash1": ("Artist", "Album", "2023")}

        # Викликаємо alias метод
        cache_service._save_album_cache_sync()

        # Перевіряємо що файл створено
        cache_csv = Path(cache_service.album_cache_csv)
        assert cache_csv.exists(), "Alias метод має створити CSV файл"


if __name__ == "__main__":
    pass
import pytest

pytestmark = pytest.mark.integration
