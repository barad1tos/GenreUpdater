"""Comprehensive unit tests for the scoring module."""

from unittest.mock import MagicMock

import pytest

from services.api.year_scoring import (
    ArtistPeriodContext,
    ReleaseScorer,
    ScoringConfig,
    create_release_scorer,
)


class TestArtistPeriodContext:
    """Test the ArtistPeriodContext TypedDict."""

    def test_initialization_with_dates(self) -> None:
        """Test creating context with activity dates."""
        context: ArtistPeriodContext = {"start_year": 1970, "end_year": 2020}

        assert context["start_year"] == 1970
        assert context["end_year"] == 2020

    def test_initialization_without_dates(self) -> None:
        """Test creating context without activity dates."""
        context: ArtistPeriodContext = {"start_year": None, "end_year": None}

        assert context["start_year"] is None
        assert context["end_year"] is None

    def test_partial_initialization(self) -> None:
        """Test creating context with partial data."""
        context: ArtistPeriodContext = {"start_year": 1980}

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
        custom_config = {"base_score": 20, "artist_exact_match_bonus": 30, "album_exact_match_bonus": 35, "source_mb_bonus": 10}

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
            "major_market_codes": ["us", "gb", "uk"],
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
        return {"base_score": 15, "artist_exact_match_bonus": 25, "album_exact_match_bonus": 30}

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
            "source": "musicbrainz",
        }

    def test_score_original_release_complete(self, scorer: ReleaseScorer, sample_release: dict) -> None:
        """Test scoring with complete metadata."""
        score = scorer.score_original_release(sample_release, "test artist", "test album", artist_region="us", source="musicbrainz")
        assert score > 30  # Should get base score plus bonuses

    def test_score_original_release_partial(self, scorer: ReleaseScorer) -> None:
        """Test scoring with partial metadata."""
        release = {"title": "Test Album", "artist": "Test Artist", "year": "2020", "source": "musicbrainz"}
        score = scorer.score_original_release(release, "test artist", "test album", artist_region=None, source="musicbrainz")
        assert score > 0  # Should still get some score

    def test_score_invalid_year(self, scorer: ReleaseScorer) -> None:
        """Test scoring with invalid year."""
        release = {"title": "Album", "artist": "Artist", "year": "invalid", "source": "test"}
        score = scorer.score_original_release(release, "artist", "album", artist_region=None, source="test")
        assert score == 0  # Invalid year should score zero

    def test_score_source_quality(self, scorer: ReleaseScorer) -> None:
        """Test source quality scoring."""
        # MusicBrainz should score higher than others
        mb_release = {"title": "Album", "artist": "Artist", "year": "2020", "source": "musicbrainz"}
        mb_score = scorer.score_original_release(mb_release, "artist", "album", artist_region=None, source="musicbrainz")

        # Discogs should score well but less than MB
        discogs_release = {"title": "Album", "artist": "Artist", "year": "2020", "source": "discogs"}
        discogs_score = scorer.score_original_release(discogs_release, "artist", "album", artist_region=None, source="discogs")

        # Unknown source should score lower
        unknown_release = {"title": "Album", "artist": "Artist", "year": "2020", "source": "unknown"}
        unknown_score = scorer.score_original_release(unknown_release, "artist", "album", artist_region=None)

        # MusicBrainz gets a bonus, so should score higher
        assert mb_score >= discogs_score
        assert discogs_score >= unknown_score

    def test_year_validation(self, scorer: ReleaseScorer) -> None:
        """Test year validation in scoring."""
        # Valid year should score
        valid_release = {"title": "Album", "artist": "Artist", "year": "2020", "source": "test"}
        valid_score = scorer.score_original_release(valid_release, "artist", "album", artist_region=None)
        assert valid_score > 0

        self._assert_invalid_year_scores_zero("20", scorer)
        self._assert_invalid_year_scores_zero("", scorer)

    @staticmethod
    def _assert_invalid_year_scores_zero(
        year_value: str,
        scorer: ReleaseScorer,
    ) -> None:
        """Assert that invalid year format scores zero."""
        invalid_release = {
            "title": "Album",
            "artist": "Artist",
            "year": year_value,
            "source": "test",
        }
        invalid_score = scorer.score_original_release(invalid_release, "artist", "album", artist_region=None)
        assert invalid_score == 0

    def test_artist_period_context(self, scorer: ReleaseScorer) -> None:
        """Test scoring with artist period context."""
        context: ArtistPeriodContext = {"start_year": 1980, "end_year": 2000}
        scorer.set_artist_period_context(context)

        # Release within period should score better
        within_release = {"title": "Album", "artist": "Artist", "year": "1990", "source": "test"}
        within_score = scorer.score_original_release(within_release, "artist", "album", artist_region=None)

        # Release outside period should be penalized
        outside_release = {"title": "Album", "artist": "Artist", "year": "2010", "source": "test"}
        outside_score = scorer.score_original_release(outside_release, "artist", "album", artist_region=None)

        # Within period should score higher
        assert within_score > outside_score

        # Clear context
        scorer.clear_artist_period_context()

    def test_country_matching(self, scorer: ReleaseScorer) -> None:
        """Test country/region matching in scoring."""
        # Matching country should get bonus
        matching_release = {"title": "Album", "artist": "Artist", "year": "2020", "country": "US", "source": "test"}
        matching_score = scorer.score_original_release(matching_release, "artist", "album", artist_region="us")

        # Different country should not get bonus
        different_release = {"title": "Album", "artist": "Artist", "year": "2020", "country": "JP", "source": "test"}
        different_score = scorer.score_original_release(different_release, "artist", "album", artist_region="us")

        # Matching should score higher
        assert matching_score > different_score

    def test_release_type_scoring(self, scorer: ReleaseScorer) -> None:
        """Test release type scoring."""
        # Album should get bonus
        album_release = {"title": "Album", "artist": "Artist", "year": "2020", "album_type": "album", "source": "test"}
        album_score = scorer.score_original_release(album_release, "artist", "album", artist_region=None)

        # Compilation should get penalty
        compilation_release = {"title": "Album", "artist": "Artist", "year": "2020", "album_type": "compilation", "source": "test"}
        compilation_score = scorer.score_original_release(compilation_release, "artist", "album", artist_region=None)

        # Album should score higher than compilation
        assert album_score > compilation_score

    def test_score_release_complete(self, scorer: ReleaseScorer, sample_release: dict) -> None:
        """Test complete release scoring with all features."""
        context: ArtistPeriodContext = {"start_year": 2015, "end_year": 2023}
        scorer.set_artist_period_context(context)

        score = scorer.score_original_release(sample_release, "test artist", "test album", artist_region="us")

        assert isinstance(score, int)
        assert score >= 0

        scorer.clear_artist_period_context()

    def test_score_release_without_context(self, scorer: ReleaseScorer, sample_release: dict) -> None:
        """Test scoring without artist context."""
        score = scorer.score_original_release(sample_release, "test artist", "test album", artist_region=None)

        assert isinstance(score, int)
        assert score >= 0

    def test_perfect_match_bonus(self, scorer: ReleaseScorer) -> None:
        """Test perfect artist and album match bonus."""
        release = {"title": "Test Album", "artist": "Test Artist", "year": "2020", "source": "musicbrainz"}

        # Perfect match should score higher
        perfect_score = scorer.score_original_release(
            release,
            "test artist",  # Exact match (normalized)
            "test album",  # Exact match (normalized)
            artist_region=None,
        )

        # Mismatch should score lower
        mismatch_score = scorer.score_original_release(release, "different artist", "different album", artist_region=None)

        assert perfect_score > mismatch_score


class TestCreateFunction:
    """Test the create_release_scorer factory function."""

    def test_create_scorer(self) -> None:
        """Test creating a scorer instance."""
        mock_logger = MagicMock()
        custom_config = {"base_score": 20, "artist_exact_match_bonus": 30}

        scorer = create_release_scorer(scoring_config=custom_config, min_valid_year=1950, definitive_score_threshold=90, console_logger=mock_logger)

        assert isinstance(scorer, ReleaseScorer)
        assert scorer.scoring_config["base_score"] == 20
        assert scorer.min_valid_year == 1950
        assert scorer.definitive_score_threshold == 90
        assert scorer.console_logger == mock_logger


class TestCrossScriptMatching:
    """Test cross-script artist matching for iTunes/Apple Music results.

    iTunes returns Latinized artist names for non-Latin artists (e.g., Cyrillic),
    but preserves original script for album titles. This tests that cross-script
    comparisons (Cyrillic target vs Latin result) don't get heavily penalized.
    """

    @pytest.fixture
    def scorer(self) -> ReleaseScorer:
        """Create a scorer instance for testing."""
        return create_release_scorer()

    def test_cross_script_cyrillic_vs_latin_gets_reduced_penalty(self, scorer: ReleaseScorer) -> None:
        """Test Cyrillic vs Latin artist comparison gets reduced penalty.

        iTunes returns 'Druha Rika' for 'Друга Ріка' but album titles in Cyrillic.
        This should not be penalized as heavily as unrelated artists.
        """
        # iTunes result: Latin artist, Cyrillic album (matches)
        itunes_release = {
            "title": "Два",  # Cyrillic album (exact match)
            "artist": "Druha Rika",  # Latin (transliterated)
            "year": "2003",
            "album_type": "Album",
            "source": "itunes",
        }

        # Target: Cyrillic artist, Cyrillic album
        cross_script_score = scorer.score_original_release(
            itunes_release,
            artist_norm="друга ріка",  # Cyrillic target
            album_norm="два",  # Matches
            artist_region=None,
        )

        # Score should be positive (not filtered out)
        assert cross_script_score > 0, "Cross-script match should have positive score"

    def test_cross_script_detection_method(self, scorer: ReleaseScorer) -> None:
        """Test the _is_cross_script_comparison method directly."""
        # Cyrillic vs Latin should be cross-script
        assert scorer._is_cross_script_comparison("друга ріка", "druha rika") is True

        # Same script (both Latin) should not be cross-script
        assert scorer._is_cross_script_comparison("pink floyd", "the beatles") is False

        # Same script (both Cyrillic) should not be cross-script
        assert scorer._is_cross_script_comparison("друга ріка", "океан ельзи") is False

    def test_cross_script_vs_same_script_mismatch(self, scorer: ReleaseScorer) -> None:
        """Test cross-script gets smaller penalty than same-script mismatch.

        Cross-script (Cyrillic vs Latin) should be penalized less because
        it's likely a transliteration, not a completely wrong artist.
        """
        release = {"title": "Album", "artist": "Artist", "year": "2020", "source": "test"}

        # Same-script mismatch (both Latin, unrelated)
        same_script_score = scorer.score_original_release(
            release,
            artist_norm="completely different",  # Latin, unrelated
            album_norm="album",
            artist_region=None,
        )

        # Cross-script (Cyrillic target, Latin result)
        release_latin = {"title": "Album", "artist": "Druha Rika", "year": "2020", "source": "test"}
        cross_script_score = scorer.score_original_release(
            release_latin,
            artist_norm="друга ріка",  # Cyrillic target
            album_norm="album",
            artist_region=None,
        )

        # Cross-script should score higher (smaller penalty)
        assert cross_script_score > same_script_score, (
            f"Cross-script ({cross_script_score}) should score higher than same-script mismatch ({same_script_score})"
        )

    def test_japanese_vs_latin_is_cross_script(self, scorer: ReleaseScorer) -> None:
        """Test Japanese vs Latin is detected as cross-script."""
        assert scorer._is_cross_script_comparison("音楽", "ongaku") is True

    def test_arabic_vs_latin_is_cross_script(self, scorer: ReleaseScorer) -> None:
        """Test Arabic vs Latin is detected as cross-script."""
        assert scorer._is_cross_script_comparison("موسيقى", "musica") is True
