#!/usr/bin/env python3
"""Music Genre Updater v3.0 - Main entry point.

This is the main entry point for the Music Genre Updater application.
It uses a fully modularized architecture for better maintainability.
"""

import argparse
import asyncio
import logging
import sys
import time
import warnings

from pathlib import Path

from src.core.cli import CLI
from src.core.config import Config
from src.core.orchestrator import Orchestrator
from src.services.dependencies_service import DependencyContainer
from src.utils.core.logger import SafeQueueListener, get_loggers

# Suppress Pydantic migration warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic._migration")

# Add a project root to a Python path
sys.path.insert(0, str(Path(__file__).parent))


async def _setup_environment(args: argparse.Namespace) -> tuple[DependencyContainer, SafeQueueListener | None, logging.Logger, logging.Logger]:
    """Set up configuration, logging, and dependencies.

    Args:
        args: Parsed command line arguments

    Returns:
        Tuple of (deps, listener, logger_console, logger_error)

    """
    # Load configuration
    config_manager = Config(args.config if hasattr(args, "config") else None)
    config = config_manager.load()

    # Initialize logging
    logger_console, logger_error, analytics_logger, year_updates_logger, db_verify_logger, listener = get_loggers(config)

    # Create dependency container
    deps = DependencyContainer(
        config_path=config_manager.config_path,
        console_logger=logger_console,
        error_logger=logger_error,
        analytics_logger=analytics_logger,
        year_updates_logger=year_updates_logger,
        db_verify_logger=db_verify_logger,
        logging_listener=listener,
        dry_run=args.dry_run,
    )

    # Initialize all services
    await deps.initialize()

    return deps, listener, logger_console, logger_error


def _handle_keyboard_interrupt(logger_console: logging.Logger | None) -> None:
    """Handle keyboard interrupt gracefully."""
    if logger_console:
        logger_console.info("\nScript interrupted by user.")
    sys.exit(130)


def _handle_critical_error(error: Exception, logger_error: logging.Logger | None) -> None:
    """Handle critical errors."""
    if logger_error:
        logger_error.critical("A critical error occurred: %s", error, exc_info=True)
    else:
        print(f"A critical error occurred: {error}", file=sys.stderr)
    sys.exit(1)


def _generate_analytics_report(
    deps: DependencyContainer,
    args: argparse.Namespace,
    logger_console: logging.Logger | None,
    logger_error: logging.Logger | None,
) -> None:
    """Generate analytics reports if available."""
    if deps and hasattr(deps, "analytics") and deps.analytics:
        try:
            deps.analytics.generate_reports(force_mode=hasattr(args, "force") and args.force)
            if logger_console:
                logger_console.info("📊 Analytics HTML report generated")
        except (OSError, RuntimeError, ValueError) as e:
            if logger_error:
                logger_error.warning("Failed to generate analytics report: %s", e)


async def _cleanup_resources(
    deps: DependencyContainer,
    listener: SafeQueueListener | None,
    logger_console: logging.Logger | None,
    start_time: float,
) -> None:
    """Cleanup all resources and log execution time."""
    # Cleanup resources
    if deps:
        await deps.close()  # Close async resources first
        deps.shutdown()  # Then shutdown non-async resources

    if listener:
        listener.stop()

    if logger_console:
        execution_time = time.time() - start_time
        logger_console.info("\nTotal script execution time: %.2f seconds", execution_time)


async def main_async() -> None:
    """Execute main async entry point."""
    # Parse arguments and record start time
    cli = CLI()
    args = cli.parse_args()
    start_time = time.time()

    # Setup environment
    deps, listener, logger_console, logger_error = await _setup_environment(args)

    try:
        # Create and run orchestrator
        orchestrator = Orchestrator(deps)
        await orchestrator.run_command(args)

    except KeyboardInterrupt:
        _handle_keyboard_interrupt(logger_console)

    except (RuntimeError, ValueError, OSError, ImportError) as e:
        _handle_critical_error(e, logger_error)

    finally:
        # Generate analytics reports before cleanup
        _generate_analytics_report(deps, args, logger_console, logger_error)

        # Cleanup resources
        await _cleanup_resources(deps, listener, logger_console, start_time)


def main() -> None:
    """Execute the main entry point."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
