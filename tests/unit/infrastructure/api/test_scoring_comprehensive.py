"""Comprehensive unit tests for the scoring module."""

from unittest.mock import MagicMock

import pytest

from src.infrastructure.api.scoring import (
    ArtistPeriodContext,
    ReleaseScorer,
    ScoringConfig,
    create_release_scorer,
)


class TestArtistPeriodContext:
    """Test the ArtistPeriodContext TypedDict."""

    def test_initialization_with_dates(self) -> None:
        """Test creating context with activity dates."""
        context: ArtistPeriodContext = {
            "start_year": 1970,
            "end_year": 2020
        }

        assert context["start_year"] == 1970
        assert context["end_year"] == 2020

    def test_initialization_without_dates(self) -> None:
        """Test creating context without activity dates."""
        context: ArtistPeriodContext = {
            "start_year": None,
            "end_year": None
        }

        assert context["start_year"] is None
        assert context["end_year"] is None

    def test_partial_initialization(self) -> None:
        """Test creating context with partial data."""
        context: ArtistPeriodContext = {
            "start_year": 1980
        }

        assert context["start_year"] == 1980
        assert context.get("end_year") is None

    def test_empty_context(self) -> None:
        """Test creating empty context."""
        context: ArtistPeriodContext = {}

        assert context.get("start_year") is None
        assert context.get("end_year") is None


class TestScoringConfig:
    """Test the ScoringConfig TypedDict."""

    def test_default_configuration(self) -> None:
        """Test default scoring configuration from ReleaseScorer."""
        scorer = ReleaseScorer()
        config = scorer.scoring_config

        # Check base scoring values
        assert config.get("base_score") == 10
        assert config.get("artist_exact_match_bonus") == 20
        assert config.get("album_exact_match_bonus") == 25
        assert config.get("album_variation_bonus") == 10

        # Check penalties
        assert config.get("album_substring_penalty") == -15
        assert config.get("album_unrelated_penalty") == -40
        assert config.get("status_bootleg_penalty") == -50

        # Check source bonuses
        assert config.get("source_mb_bonus") == 5
        assert config.get("source_discogs_bonus") == 2
        assert config.get("source_lastfm_penalty") == -5

    def test_custom_configuration(self) -> None:
        """Test custom scoring configuration."""
        custom_config = {
            "base_score": 20,
            "artist_exact_match_bonus": 30,
            "album_exact_match_bonus": 35,
            "source_mb_bonus": 10
        }

        scorer = ReleaseScorer(scoring_config=custom_config)

        assert scorer.scoring_config["base_score"] == 20
        assert scorer.scoring_config["artist_exact_match_bonus"] == 30
        assert scorer.scoring_config["album_exact_match_bonus"] == 35
        assert scorer.scoring_config["source_mb_bonus"] == 10

    def test_scoring_config_structure(self) -> None:
        """Test that ScoringConfig TypedDict structure is valid."""
        config: ScoringConfig = {
            "base_score": 10,
            "artist_exact_match_bonus": 20,
            "album_exact_match_bonus": 25,
            "major_market_codes": ["us", "gb", "uk"]
        }

        assert config["base_score"] == 10
        assert config["artist_exact_match_bonus"] == 20
        assert isinstance(config["major_market_codes"], list)
        assert "us" in config["major_market_codes"]


class TestReleaseScorer:
    """Test the ReleaseScorer class."""

    @pytest.fixture
    def mock_logger(self) -> MagicMock:
        """Create a mock logger."""
        return MagicMock()

    @pytest.fixture
    def custom_config(self) -> dict:
        """Create custom scoring configuration."""
        return {
            "base_score": 15,
            "artist_exact_match_bonus": 25,
            "album_exact_match_bonus": 30
        }

    @pytest.fixture
    def scorer(self, mock_logger: MagicMock) -> ReleaseScorer:
        """Create a scorer instance."""
        return ReleaseScorer(console_logger=mock_logger)

    @pytest.fixture
    def sample_release(self) -> dict:
        """Create a sample release for testing."""
        return {
            "id": "123",
            "title": "Test Album",
            "artist": "Test Artist",
            "year": "2020",
            "country": "US",
            "album_type": "album",
            "status": "official",
            "source": "musicbrainz"
        }

    def test_score_original_release_complete(self, scorer: ReleaseScorer, sample_release: dict) -> None:
        """Test scoring with complete metadata."""
        score = scorer.score_original_release(
            sample_release,
            "test artist",
            "test album",
            artist_region="us",
            source="musicbrainz"
        )
        assert score > 30  # Should get base score plus bonuses

    def test_score_original_release_partial(self, scorer: ReleaseScorer) -> None:
        """Test scoring with partial metadata."""
        release = {
            "title": "Test Album",
            "artist": "Test Artist",
            "year": "2020",
            "source": "musicbrainz"
        }
        score = scorer.score_original_release(
            release,
            "test artist",
            "test album",
            artist_region=None,
            source="musicbrainz"
        )
        assert score > 0  # Should still get some score

    def test_score_invalid_year(self, scorer: ReleaseScorer) -> None:
        """Test scoring with invalid year."""
        release = {
            "title": "Album",
            "artist": "Artist",
            "year": "invalid",
            "source": "test"
        }
        score = scorer.score_original_release(
            release,
            "artist",
            "album",
            artist_region=None,
            source="test"
        )
        assert score == 0  # Invalid year should score zero

    def test_score_source_quality(self, scorer: ReleaseScorer) -> None:
        """Test source quality scoring."""
        # MusicBrainz should score higher than others
        mb_release = {
            "title": "Album",
            "artist": "Artist",
            "year": "2020",
            "source": "musicbrainz"
        }
        mb_score = scorer.score_original_release(
            mb_release, "artist", "album", artist_region=None, source="musicbrainz"
        )

        # Discogs should score well but less than MB
        discogs_release = {
            "title": "Album",
            "artist": "Artist",
            "year": "2020",
            "source": "discogs"
        }
        discogs_score = scorer.score_original_release(
            discogs_release, "artist", "album", artist_region=None, source="discogs"
        )

        # Unknown source should score lower
        unknown_release = {
            "title": "Album",
            "artist": "Artist",
            "year": "2020",
            "source": "unknown"
        }
        unknown_score = scorer.score_original_release(
            unknown_release, "artist", "album", artist_region=None
        )

        # MusicBrainz gets a bonus, so should score higher
        assert mb_score >= discogs_score
        assert discogs_score >= unknown_score

    def test_year_validation(self, scorer: ReleaseScorer) -> None:
        """Test year validation in scoring."""
        # Valid year should score
        valid_release = {
            "title": "Album",
            "artist": "Artist",
            "year": "2020",
            "source": "test"
        }
        valid_score = scorer.score_original_release(
            valid_release, "artist", "album", artist_region=None
        )
        assert valid_score > 0

        # Invalid format should score zero (too short)
        invalid_release = {
            "title": "Album",
            "artist": "Artist",
            "year": "20",  # Too short
            "source": "test"
        }
        invalid_score = scorer.score_original_release(
            invalid_release, "artist", "album", artist_region=None
        )
        assert invalid_score == 0

        # Empty year should score zero
        empty_release = {
            "title": "Album",
            "artist": "Artist",
            "year": "",
            "source": "test"
        }
        empty_score = scorer.score_original_release(
            empty_release, "artist", "album", artist_region=None
        )
        assert empty_score == 0

    def test_artist_period_context(self, scorer: ReleaseScorer) -> None:
        """Test scoring with artist period context."""
        context: ArtistPeriodContext = {
            "start_year": 1980,
            "end_year": 2000
        }
        scorer.set_artist_period_context(context)

        # Release within period should score better
        within_release = {
            "title": "Album",
            "artist": "Artist",
            "year": "1990",
            "source": "test"
        }
        within_score = scorer.score_original_release(
            within_release, "artist", "album", artist_region=None
        )

        # Release outside period should be penalized
        outside_release = {
            "title": "Album",
            "artist": "Artist",
            "year": "2010",
            "source": "test"
        }
        outside_score = scorer.score_original_release(
            outside_release, "artist", "album", artist_region=None
        )

        # Within period should score higher
        assert within_score > outside_score

        # Clear context
        scorer.clear_artist_period_context()

    def test_country_matching(self, scorer: ReleaseScorer) -> None:
        """Test country/region matching in scoring."""
        # Matching country should get bonus
        matching_release = {
            "title": "Album",
            "artist": "Artist",
            "year": "2020",
            "country": "US",
            "source": "test"
        }
        matching_score = scorer.score_original_release(
            matching_release, "artist", "album", artist_region="us"
        )

        # Different country should not get bonus
        different_release = {
            "title": "Album",
            "artist": "Artist",
            "year": "2020",
            "country": "JP",
            "source": "test"
        }
        different_score = scorer.score_original_release(
            different_release, "artist", "album", artist_region="us"
        )

        # Matching should score higher
        assert matching_score > different_score

    def test_release_type_scoring(self, scorer: ReleaseScorer) -> None:
        """Test release type scoring."""
        # Album should get bonus
        album_release = {
            "title": "Album",
            "artist": "Artist",
            "year": "2020",
            "album_type": "album",
            "source": "test"
        }
        album_score = scorer.score_original_release(
            album_release, "artist", "album", artist_region=None
        )

        # Compilation should get penalty
        compilation_release = {
            "title": "Album",
            "artist": "Artist",
            "year": "2020",
            "album_type": "compilation",
            "source": "test"
        }
        compilation_score = scorer.score_original_release(
            compilation_release, "artist", "album", artist_region=None
        )

        # Album should score higher than compilation
        assert album_score > compilation_score

    def test_score_release_complete(self, scorer: ReleaseScorer, sample_release: dict) -> None:
        """Test complete release scoring with all features."""
        context: ArtistPeriodContext = {
            "start_year": 2015,
            "end_year": 2023
        }
        scorer.set_artist_period_context(context)

        score = scorer.score_original_release(
            sample_release,
            "test artist",
            "test album",
            artist_region="us"
        )

        assert isinstance(score, int)
        assert score >= 0

        scorer.clear_artist_period_context()

    def test_score_release_without_context(self, scorer: ReleaseScorer, sample_release: dict) -> None:
        """Test scoring without artist context."""
        score = scorer.score_original_release(
            sample_release,
            "test artist",
            "test album",
            artist_region=None
        )

        assert isinstance(score, int)
        assert score >= 0

    def test_perfect_match_bonus(self, scorer: ReleaseScorer) -> None:
        """Test perfect artist and album match bonus."""
        release = {
            "title": "Test Album",
            "artist": "Test Artist",
            "year": "2020",
            "source": "musicbrainz"
        }

        # Perfect match should score higher
        perfect_score = scorer.score_original_release(
            release,
            "test artist",  # Exact match (normalized)
            "test album",   # Exact match (normalized)
            artist_region=None
        )

        # Mismatch should score lower
        mismatch_score = scorer.score_original_release(
            release,
            "different artist",
            "different album",
            artist_region=None
        )

        assert perfect_score > mismatch_score


class TestCreateFunction:
    """Test the create_release_scorer factory function."""

    def test_create_scorer(self) -> None:
        """Test creating a scorer instance."""
        mock_logger = MagicMock()
        custom_config = {
            "base_score": 20,
            "artist_exact_match_bonus": 30
        }

        scorer = create_release_scorer(
            scoring_config=custom_config,
            min_valid_year=1950,
            definitive_score_threshold=90,
            console_logger=mock_logger
        )

        assert isinstance(scorer, ReleaseScorer)
        assert scorer.scoring_config["base_score"] == 20
        assert scorer.min_valid_year == 1950
        assert scorer.definitive_score_threshold == 90
        assert scorer.console_logger == mock_logger
