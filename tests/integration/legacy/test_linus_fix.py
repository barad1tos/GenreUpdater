#!/usr/bin/env python3

"""Тест фіксу для Linus - чи справді є гонка потоків."""

import asyncio
import logging
import time
from pathlib import Path

from src.infrastructure.dependencies_service import DependencyContainer


async def test_race_condition() -> bool | None:
    """Тестуємо гонку потоків в save_cache."""
    print("🧪 Тест гонки потоків в cache saving...")

    # Простий логгінг
    logging.basicConfig(level=logging.INFO)
    console_logger = logging.getLogger("console")
    error_logger = logging.getLogger("error")
    analytics_logger = logging.getLogger("analytics")
    year_updates_logger = logging.getLogger("year_updates")
    db_verify_logger = logging.getLogger("db_verify")

    try:
        deps = DependencyContainer(
            config_path="my-config.yaml",
            console_logger=console_logger,
            error_logger=error_logger,
            analytics_logger=analytics_logger,
            year_updates_logger=year_updates_logger,
            db_verify_logger=db_verify_logger,
            logging_listener=None,
            dry_run=True,
        )

        await deps.initialize()
        cache_service = deps.cache_service

        # Додаємо дані в обидва кеші
        await cache_service.set_async("test_key_1", "test_value_1", ttl=3600)
        await cache_service.set_async("test_key_2", "test_value_2", ttl=3600)
        await cache_service.store_album_year_in_cache("Test Artist 1", "Test Album 1", "2023")
        await cache_service.store_album_year_in_cache("Test Artist 2", "Test Album 2", "2024")

        print("✅ Додано дані в обидва кеші")

        # Виконуємо збереження декілька разів підряд
        start_time = time.time()
        for i in range(3):
            print(f"🔄 Збереження #{i + 1}...")
            await cache_service.save_cache()

        end_time = time.time()

        await deps.close()
        deps.shutdown()

        print(f"✅ Всі збереження завершилися за {end_time - start_time:.2f}s")

        # Перевіряємо файли
        cache_json = Path("/Users/romanborodavkin/Library/Mobile Documents/com~apple~CloudDocs/4. Dev/MGU logs/cache/cache.json")
        cache_csv = Path("/Users/romanborodavkin/Library/Mobile Documents/com~apple~CloudDocs/4. Dev/MGU logs/csv/cache_albums.csv")

        json_ok = cache_json.exists()
        csv_ok = cache_csv.exists()

        print(f"📄 JSON cache: {'✅' if json_ok else '❌'}")
        print(f"📄 CSV cache: {'✅' if csv_ok else '❌'}")

        if json_ok and csv_ok:
            print("✅ Linus може бути задоволений - файли створюються")
            return True
        print("❌ Є проблеми з збереженням")
        return False

    except Exception as e:
        print(f"❌ Помилка тесту: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    result = asyncio.run(test_race_condition())
    print(f"\n{'✅ Test passed' if result else '❌ Test failed'}")
import pytest

pytestmark = pytest.mark.integration
