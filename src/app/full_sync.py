"""Full Media Library Resync Module.

This module performs a complete resynchronization of the track_list.csv database
with the current state of your Music.app library, ensuring complete bidirectional
synchronization.

Usage:
    python -m src.application.full_sync

Or from project root:
    python -c "import asyncio; from src.app.full_sync import main; asyncio.run(main())"
"""

import asyncio
import logging
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from src.core.core_config import load_config
from src.core.logger import get_loggers
from src.core.models.protocols import CacheServiceProtocol

# Add project root to path if needed
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Import after path setup to avoid import errors
from src.app.music_updater import MusicUpdater  # noqa: E402
from src.services.dependency_container import DependencyContainer  # noqa: E402
from src.core.logger import get_full_log_path  # noqa: E402
from src.core.models.metadata_utils import is_music_app_running  # noqa: E402
from src.metrics.change_reports import sync_track_list_with_current  # noqa: E402

if TYPE_CHECKING:
    from src.core.tracks.track_processor import TrackProcessor
    from src.services.cache.orchestrator import CacheOrchestrator


# noinspection PyArgumentEqualDefault
async def run_full_resync(
    console_logger: logging.Logger,
    error_logger: logging.Logger,
    config: dict[str, Any],
    cache_service: "CacheOrchestrator",
    track_processor: "TrackProcessor",
) -> None:
    """Run complete media library resynchronization.

    This function performs a full sync of the track_list.csv with the current state
    of the Music.app library, ensuring complete bidirectional synchronization.

    Args:
        console_logger: Logger for console output
        error_logger: Logger for error output
        config: Configuration dictionary
        cache_service: Cache service instance

        track_processor: Track processor instance

    """
    console_logger.info("🔄 Starting full media library resync...")

    try:
        # Check if Music.app is running
        if not is_music_app_running(error_logger):
            error_logger.error("❌ Music.app is not running! Please start Music.app before running this script.")
            return

        console_logger.info("✅ Music.app is running")

        # Fetch ALL current tracks from Music.app
        console_logger.info("📚 Fetching all tracks from Music.app...")
        all_tracks = await track_processor.fetch_tracks_async()

        if not all_tracks:
            console_logger.warning("⚠️ No tracks found in Music.app")
            return

        console_logger.info("📊 Found %d tracks in Music.app", len(all_tracks))

        # Perform full synchronization
        csv_path = get_full_log_path(config, "csv_output_file", "csv/track_list.csv")

        console_logger.info("💾 Synchronizing with database: %s", csv_path)

        await sync_track_list_with_current(
            all_tracks,
            csv_path,
            cast(CacheServiceProtocol, cache_service),
            console_logger,
            error_logger,
            partial_sync=False,
            applescript_client=track_processor.ap_client,
        )

        console_logger.info("✨ Full resync completed successfully!")
        console_logger.info("🎯 Database synchronized: %d tracks", len(all_tracks))

    except (OSError, RuntimeError, ValueError) as e:
        error_logger.exception("❌ Full resync failed: %s", e)
        raise


async def main() -> None:
    """Perform full media library resynchronization."""
    print("🚀 Music Genre Updater - Full Media Library Resync")
    print("=" * 55)

    # Determine config path - look for it in the current project directory
    # Try standard config names in order of preference
    config_files = ["config.yaml", "my-config.yaml"]
    if found_configs := [
        project_root / name
        for name in config_files
        if (project_root / name).exists()
    ]:
        config_path = found_configs[0]
        if len(found_configs) > 1:
            print(
                f"⚠️  Both '{config_files[0]}' and '{config_files[1]}' found in the project directory.\n"
                f"    Using '{config_path.name}' as configuration file (higher precedence).\n"
                "    Please remove the unused config file to avoid ambiguity."
            )

    else:
        # If neither exists, use default naming
        config_path = project_root / "config.yaml"
    if not config_path.exists():
        print(f"❌ Configuration file not found: {config_path}")
        print("Please make sure you're running this script from the correct directory.")
        sys.exit(1)

    print(f"📁 Using config: {config_path}")

    # Load configuration to setup proper loggers
    config = load_config(str(config_path))

    # Setup loggers using centralized system
    console_logger, error_logger, analytics_logger, year_updates_logger, db_verify_logger, listener = get_loggers(config)

    # Initialize variables for cleanup
    deps = None
    try:
        # Initialize dependency container with proper config path and loggers
        print("⚙️ Initializing services...")
        deps = DependencyContainer(
            config_path=str(config_path),
            console_logger=console_logger,
            error_logger=error_logger,
            analytics_logger=analytics_logger,
            year_updates_logger=year_updates_logger,
            db_verify_logger=db_verify_logger,
        )
        await deps.initialize()

        print("✅ Services initialized successfully")

        # Create MusicUpdater instance to get track processor
        music_updater = MusicUpdater(deps)

        # Run the full resync
        print("🔄 Starting full media library resync...")
        await run_full_resync(console_logger, error_logger, deps.config, deps.cache_service, music_updater.track_processor)

        print("✅ Full resync completed successfully!")
        print("🎯 Your track_list.csv is now fully synchronized with Music.app")

    except KeyboardInterrupt:
        print("\n⏹️ Resync cancelled by user")
        sys.exit(1)
    except (OSError, ValueError, RuntimeError) as e:
        print(f"❌ Resync failed: {e}")
        traceback.print_exc()
        sys.exit(1)
    finally:
        # Cleanup async resources
        if deps:
            await deps.close()

        # Cleanup logging resources
        if listener:
            try:
                listener.stop()
            except Exception as e:
                print(f"❌ Exception during listener.stop(): {e}")
                traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
