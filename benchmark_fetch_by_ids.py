#!/usr/bin/env python3
"""Benchmark fetch_tracks_by_ids.scpt performance to evaluate Smart Delta feasibility.

This script measures how fast Music.app can lookup tracks by ID through AppleScript,
which is critical for determining if Smart Delta approach is viable.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.application.config import Config
from src.infrastructure.applescript_client import AppleScriptClient
from src.infrastructure.cache.library_snapshot_service import LibrarySnapshotService
from src.shared.core.logger import get_loggers
from src.shared.monitoring.analytics import Analytics, LoggerContainer

if TYPE_CHECKING:
    import logging


class BenchmarkResult:
    """Results from a single benchmark run."""

    def __init__(self, num_ids: int, duration: float, tracks_returned: int) -> None:
        self.num_ids = num_ids
        self.duration = duration
        self.tracks_returned = tracks_returned

    @property
    def per_id_time(self) -> float:
        """Time per ID in seconds."""
        return self.duration / self.num_ids if self.num_ids > 0 else 0.0

    @property
    def ids_per_second(self) -> float:
        """IDs processed per second."""
        return self.num_ids / self.duration if self.duration > 0 else 0.0


async def run_fetch_benchmark(
    client: AppleScriptClient,
    track_ids: list[str],
    logger: logging.Logger,
) -> BenchmarkResult:
    """Run a single fetch_by_ids benchmark."""
    num_ids = len(track_ids)
    ids_csv = ",".join(track_ids)

    logger.info("🔍 Testing with %d IDs...", num_ids)

    start_time = time.time()

    try:
        raw_output = await client.run_script(
            "fetch_tracks_by_ids.scpt",
            [ids_csv],
            timeout=600,  # 10 minutes max
        )
        duration = time.time() - start_time

        # Count returned tracks
        tracks_returned = 0
        if raw_output and raw_output != "NO_TRACKS_FOUND":
            if "\x1d" in raw_output:
                tracks_returned = raw_output.count("\x1d") + 1
            elif raw_output.strip():
                tracks_returned = 1

        return BenchmarkResult(num_ids, duration, tracks_returned)

    except (OSError, ValueError, RuntimeError) as exc:
        duration = time.time() - start_time
        logger.exception("❌ Benchmark failed after %.1fs: %s", duration, exc)
        return BenchmarkResult(num_ids, duration, 0)


def print_results(
    results: list[BenchmarkResult],
    total_library_size: int,
) -> None:
    """Print benchmark results and recommendation."""
    print("\n" + "=" * 70)
    print("📊 BENCHMARK RESULTS: fetch_tracks_by_ids.scpt")
    print("=" * 70)

    # Individual test results
    for i, result in enumerate(results, 1):
        print(f"\n🔸 Test {i}: {result.num_ids} IDs")
        print(f"   Time: {result.duration:.1f}s")
        print(f"   Per ID: {result.per_id_time * 1000:.1f}ms")
        print(f"   Speed: {result.ids_per_second:.1f} IDs/sec")
        print(f"   Returned: {result.tracks_returned} tracks")

    # Extrapolation
    print(f"\n{'=' * 70}")
    print(f"📈 EXTRAPOLATION FOR FULL LIBRARY ({total_library_size:,} tracks)")
    print("=" * 70)

    # Use the largest test for most accurate extrapolation
    best_result = max(results, key=lambda r: r.num_ids)
    estimated_time = total_library_size * best_result.per_id_time

    print(f"\nBased on {best_result.num_ids} IDs test:")
    print(f"   Per ID time: {best_result.per_id_time * 1000:.1f}ms")
    print(f"   Estimated full scan: {estimated_time / 60:.1f} minutes")

    _print_section_header("⚖️  COMPARISON")
    # From user's logs: ~3 minutes per 1000 tracks
    batch_scan_time_per_track = 3 * 60 / 1000  # ~0.18s per track
    batch_scan_total = total_library_size * batch_scan_time_per_track

    print(f"\nCurrent batch scan: {batch_scan_total / 60:.1f} minutes")
    print(f"Smart Delta fetch:  {estimated_time / 60:.1f} minutes")
    print("Processing delta:   ~5 minutes (estimated)")
    print(f"Smart Delta total:  {(estimated_time + 300) / 60:.1f} minutes")

    speedup = batch_scan_total / (estimated_time + 300)
    time_saved = (batch_scan_total - estimated_time - 300) / 60

    print(f"\nSpeedup: {speedup:.1f}x")
    print(f"Time saved: {time_saved:.1f} minutes per run")

    _print_section_header("🎯 RECOMMENDATION")
    if speedup >= 2.0:
        _print_recommendation(
            "\n✅ GO - Smart Delta is HIGHLY recommended!",
            time_saved,
            "   Smart Delta will significantly improve performance.",
        )
    elif speedup >= 1.3:
        _print_recommendation(
            "\n🤔 MAYBE - Smart Delta shows moderate improvement",
            time_saved,
            "   Consider implementing if metadata changes are frequent.",
        )
    elif speedup >= 1.0:
        _print_recommendation(
            "\n⚠️  MARGINAL - Smart Delta has minimal benefit",
            time_saved,
            "   Simple 'use old snapshot' approach may be better.",
        )
    else:
        print("\n❌ NO GO - Smart Delta is SLOWER than batch scan!")
        print(f"   Time penalty: {abs(time_saved):.0f} minutes WORSE")
        print("   Do NOT implement - use 'reuse old snapshot' approach instead.")

    print("\n" + "=" * 70)


def _print_recommendation(message: str, time_saved: float, details: str) -> None:
    """Print recommendation with time savings."""
    print(message)
    print(f"   Expected benefit: Save {time_saved:.0f} minutes per run")
    print(details)


def _print_section_header(title: str) -> None:
    """Print formatted section header."""
    print(f"\n{'=' * 70}")
    print(title)
    print("=" * 70)

async def main() -> int:
    """Run benchmark tests."""
    parser = argparse.ArgumentParser(description="Benchmark fetch_tracks_by_ids.scpt performance")
    parser.add_argument(
        "--sizes",
        type=str,
        default="100,500,1000",
        help="Comma-separated list of ID counts to test (default: 100,500,1000)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="my-config.yaml",
        help="Path to config file",
    )
    args = parser.parse_args()

    # Parse test sizes
    try:
        test_sizes = [int(s.strip()) for s in args.sizes.split(",")]
    except ValueError:
        print(f"❌ Invalid --sizes format: {args.sizes}")
        print("   Use comma-separated numbers, e.g., --sizes 100,500,1000")
        return 1

    # Setup logging
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"❌ Config file not found: {config_path}")
        return 1

    config_obj = Config(str(config_path))
    config: dict[str, Any] = config_obj.load()

    console_logger, error_logger, _, _, _, _ = get_loggers(config)
    logger_container = LoggerContainer(console_logger, error_logger, console_logger)

    print("🚀 Starting fetch_by_ids Performance Benchmark")
    print("=" * 70)

    # Initialize services
    console_logger.info("Initializing services...")

    snapshot_service = LibrarySnapshotService(config, console_logger)
    snapshot_tracks = await snapshot_service.load_snapshot()

    if not snapshot_tracks:
        console_logger.error("❌ Failed to load snapshot - cannot run benchmark")
        console_logger.info("Run the main script first to create a snapshot")
        return 1

    total_library_size = len(snapshot_tracks)
    console_logger.info("✓ Loaded snapshot with %d tracks", total_library_size)

    # Extract IDs
    all_ids = [str(track.id) for track in snapshot_tracks if track.id]
    console_logger.info("✓ Extracted %d track IDs", len(all_ids))

    # Initialize AppleScript client and analytics
    analytics = Analytics(config, logger_container)
    client = AppleScriptClient(config, analytics, console_logger, error_logger)
    await client.initialize()

    # Run benchmarks
    results: list[BenchmarkResult] = []

    for size in sorted(test_sizes):
        if size > len(all_ids):
            console_logger.warning("⚠️  Skipping size %d (only %d IDs available)", size, len(all_ids))
            continue

        test_ids = all_ids[:size]
        result = await run_fetch_benchmark(client, test_ids, console_logger)
        results.append(result)

        console_logger.info(
            "✓ Completed: %d IDs in %.1fs (%.1f ms/ID)",
            result.num_ids,
            result.duration,
            result.per_id_time * 1000,
        )

        # Small delay between tests
        await asyncio.sleep(2)

    # Print results
    if results:
        print_results(results, total_library_size)
    else:
        console_logger.error("❌ No benchmark results collected")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
