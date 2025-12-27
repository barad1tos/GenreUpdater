"""Command-line interface for Music Genre Updater."""

import argparse
from typing import Any


def _add_clean_artist_command(subparsers: Any) -> None:
    """Add a clean artist command."""
    parser = subparsers.add_parser(
        "clean_artist",
        aliases=["clean"],
        help="Clean track and album names for a specific artist",
        description="Remove promotional text and clean metadata for all tracks by an artist",
    )
    parser.add_argument(
        "--artist",
        required=True,
        help="Artist name to process",
    )


def _add_update_years_command(subparsers: Any) -> None:
    """Add update years command."""
    parser = subparsers.add_parser(
        "update_years",
        aliases=["years"],
        help="Update album years from external APIs",
        description="Fetch missing album years from MusicBrainz and Discogs",
    )
    parser.add_argument(
        "--artist",
        help="Artist name (optional, processes all if not specified)",
    )


def _add_revert_years_command(subparsers: Any) -> None:
    """Add revert years command."""
    parser = subparsers.add_parser(
        "revert_years",
        aliases=["revert"],
        help="Revert year changes for an artist (optionally per album)",
        description=(
            "Revert previously applied year updates. By default uses the latest changes_report.csv; you can also provide a backup CSV path."
        ),
    )
    parser.add_argument(
        "--artist",
        required=True,
        help="Artist name to revert",
    )
    parser.add_argument(
        "--album",
        required=False,
        help="Album name to revert (if omitted, reverts all albums for the artist)",
    )
    parser.add_argument(
        "--backup-csv",
        required=False,
        help="Path to backup track_list.csv to use as the source of truth for years",
    )


class CLI:
    """Command-line interface handler."""

    def __init__(self) -> None:
        """Initialize CLI parser."""
        self.parser = self._create_parser()

    @staticmethod
    def _create_parser() -> argparse.ArgumentParser:
        """Create the argument parser.

        Returns:
            Configured ArgumentParser

        """
        parser = argparse.ArgumentParser(
            prog="python main.py",
            description="Music Genre Updater - Automatically update genres and metadata in Music.app",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
    # Run the default update process
    %(prog)s

    # Clean metadata for a specific artist
    %(prog)s clean_artist --artist "Pink Floyd"

    # Update years for all albums
    %(prog)s update_years --force

    # Run in test mode
    %(prog)s --test-mode --dry-run

    # Verify the track database
    %(prog)s verify_database
            """,
        )

        # Global options
        parser.add_argument(
            "--fresh",
            action="store_true",
            help="Clear all caches and snapshots before running (fetches fresh data from Music.app)",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force run, bypassing incremental checks and cache",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate changes without applying them",
        )
        parser.add_argument(
            "--test-mode",
            action="store_true",
            help="Run only on artists defined in development.test_artists",
        )
        parser.add_argument(
            "--verbose",
            "-v",
            action="store_true",
            help="Enable verbose logging",
        )
        parser.add_argument(
            "--quiet",
            "-q",
            action="store_true",
            help="Suppress non-critical output",
        )
        parser.add_argument(
            "--config",
            type=str,
            help="Path to configuration file. If not specified, tries 'my-config.yaml' first, then 'config.yaml' as fallback.",
        )

        # Subcommands
        subparsers = parser.add_subparsers(
            dest="command", title="Commands", description="Available commands", help="Use '%(prog)s COMMAND --help' for command-specific help"
        )

        # Clean artist command
        _add_clean_artist_command(subparsers)

        # Update years command
        _add_update_years_command(subparsers)

        # Revert years command
        _add_revert_years_command(subparsers)

        # Verify database command
        CLI._add_verify_database_command(subparsers)

        # Verify pending command
        CLI._add_verify_pending_command(subparsers)

        # Batch commands
        CLI._add_batch_command(subparsers)

        # Rotate encryption keys command
        CLI._add_rotate_keys_command(subparsers)

        return parser

    @staticmethod
    def _add_verify_database_command(subparsers: Any) -> None:
        """Add verify database command."""
        subparsers.add_parser(
            "verify_database",
            aliases=["verify-db"],
            help="Verify track database against Music.app",
            description="Check that all tracks in the database still exist in Music.app",
        )

    @staticmethod
    def _add_verify_pending_command(subparsers: Any) -> None:
        """Add verify pending command."""
        subparsers.add_parser(
            "verify_pending",
            aliases=["pending"],
            help="Re-verify albums pending year verification",
            description="Retry fetching years for albums that previously failed",
        )

    @staticmethod
    def _add_batch_command(subparsers: Any) -> None:
        """Add batch processing command."""
        parser = subparsers.add_parser(
            "batch",
            help="Process multiple artists from a file",
            description="Read artist names from a file and process them",
        )
        parser.add_argument(
            "--file",
            required=True,
            help="Path to file containing artist names (one per line)",
        )
        parser.add_argument(
            "--operation",
            choices=["clean", "years", "full"],
            default="full",
            help="Operation to perform on each artist",
        )

    @staticmethod
    def _add_rotate_keys_command(subparsers: Any) -> None:
        """Add rotate encryption keys command."""
        parser = subparsers.add_parser(
            "rotate_keys",
            aliases=["rotate-keys"],
            help="Rotate encryption keys and re-encrypt all tokens",
            description="Rotate the encryption key used for API tokens and re-encrypt all stored tokens",
        )
        parser.add_argument(
            "--new-password",
            help="New password for key derivation (optional, will generate if not provided)",
        )
        parser.add_argument(
            "--no-backup",
            action="store_true",
            help="Skip creating backup of old encryption key",
        )

    def parse_args(self, args: list[str] | None = None) -> argparse.Namespace:
        """Parse command-line arguments.

        Args:
            args: List of arguments (use sys.argv if None)

        Returns:
            Parsed arguments namespace

        """
        return self.parser.parse_args(args)

    def print_help(self) -> None:
        """Print help message."""
        self.parser.print_help()
