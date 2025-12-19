"""Tests for album edition normalization and artist mismatch penalty in scoring.

This module tests the following features:
1. _strip_edition_suffix() - removes edition suffixes like (Deluxe), (Remastered)
2. remaster_keywords parameter - config-driven edition detection
3. Album match scoring with edition normalization
4. Artist mismatch penalty - penalizes wrong artist matches
"""

from typing import Any

import pytest

from services.api import year_scoring
from services.api.year_scoring import (
    ReleaseScorer,
    create_release_scorer,
)


class TestStripEditionSuffix:
    """Tests for _strip_edition_suffix() method."""

    @pytest.fixture
    def remaster_keywords(self) -> list[str]:
        """Standard remaster keywords for testing."""
        return [
            "remaster",
            "deluxe",
            "anniversary",
            "expanded",
            "bonus",
            "special",
            "collector",
            "edition",
            "version",
            "explicit",
            "clean",
        ]

    @pytest.fixture
    def scorer_with_keywords(self, remaster_keywords: list[str]) -> ReleaseScorer:
        """Create scorer with remaster keywords."""
        return ReleaseScorer(remaster_keywords=remaster_keywords)

    @pytest.fixture
    def scorer_without_keywords(self) -> ReleaseScorer:
        """Create scorer without remaster keywords."""
        return ReleaseScorer(remaster_keywords=[])

    def test_strips_deluxe_edition(self, scorer_with_keywords: ReleaseScorer) -> None:
        """Test stripping (Deluxe Edition) suffix."""
        result = scorer_with_keywords._strip_edition_suffix("Fallen (Deluxe Edition)")
        assert result == "Fallen"

    def test_strips_remastered(self, scorer_with_keywords: ReleaseScorer) -> None:
        """Test stripping (Remastered) suffix."""
        result = scorer_with_keywords._strip_edition_suffix("Abbey Road (Remastered)")
        assert result == "Abbey Road"

    def test_strips_year_remaster(self, scorer_with_keywords: ReleaseScorer) -> None:
        """Test stripping (2011 Remaster) suffix with year."""
        result = scorer_with_keywords._strip_edition_suffix("Dark Side of the Moon (2011 Remaster)")
        assert result == "Dark Side of the Moon"

    def test_strips_anniversary_edition(self, scorer_with_keywords: ReleaseScorer) -> None:
        """Test stripping (25th Anniversary Edition) suffix."""
        result = scorer_with_keywords._strip_edition_suffix("Nevermind (25th Anniversary Edition)")
        assert result == "Nevermind"

    def test_strips_expanded_edition(self, scorer_with_keywords: ReleaseScorer) -> None:
        """Test stripping (Expanded Edition) suffix."""
        result = scorer_with_keywords._strip_edition_suffix("OK Computer (Expanded Edition)")
        assert result == "OK Computer"

    def test_strips_special_edition(self, scorer_with_keywords: ReleaseScorer) -> None:
        """Test stripping (Special Edition) suffix."""
        result = scorer_with_keywords._strip_edition_suffix("The Wall (Special Edition)")
        assert result == "The Wall"

    def test_strips_collectors_edition(self, scorer_with_keywords: ReleaseScorer) -> None:
        """Test stripping (Collector's Edition) suffix."""
        result = scorer_with_keywords._strip_edition_suffix("Hybrid Theory (Collector's Edition)")
        assert result == "Hybrid Theory"

    def test_strips_bonus_tracks(self, scorer_with_keywords: ReleaseScorer) -> None:
        """Test stripping (with Bonus Tracks) suffix."""
        result = scorer_with_keywords._strip_edition_suffix("Meteora (with Bonus Tracks)")
        assert result == "Meteora"

    def test_strips_explicit_version(self, scorer_with_keywords: ReleaseScorer) -> None:
        """Test stripping (Explicit Version) suffix."""
        result = scorer_with_keywords._strip_edition_suffix("Recovery (Explicit Version)")
        assert result == "Recovery"

    def test_strips_clean_version(self, scorer_with_keywords: ReleaseScorer) -> None:
        """Test stripping (Clean Version) suffix."""
        result = scorer_with_keywords._strip_edition_suffix("The Eminem Show (Clean Version)")
        assert result == "The Eminem Show"

    def test_preserves_album_without_suffix(self, scorer_with_keywords: ReleaseScorer) -> None:
        """Test that album names without edition suffixes are preserved."""
        result = scorer_with_keywords._strip_edition_suffix("Abbey Road")
        assert result == "Abbey Road"

    def test_preserves_non_edition_parentheses(self, scorer_with_keywords: ReleaseScorer) -> None:
        """Test that non-edition parenthetical content is preserved."""
        result = scorer_with_keywords._strip_edition_suffix("The Colour and the Shape (Foo Fighters)")
        assert result == "The Colour and the Shape (Foo Fighters)"

    def test_preserves_year_only_parentheses(self, scorer_with_keywords: ReleaseScorer) -> None:
        """Test that year-only parentheses are preserved."""
        result = scorer_with_keywords._strip_edition_suffix("Black Album (1991)")
        assert result == "Black Album (1991)"

    def test_handles_empty_string(self, scorer_with_keywords: ReleaseScorer) -> None:
        """Test handling of empty string."""
        result = scorer_with_keywords._strip_edition_suffix("")
        assert result == ""

    def test_handles_none_keywords(self, scorer_without_keywords: ReleaseScorer) -> None:
        """Test that without keywords, album names are unchanged."""
        result = scorer_without_keywords._strip_edition_suffix("Fallen (Deluxe Edition)")
        assert result == "Fallen (Deluxe Edition)"

    def test_strips_multiple_suffixes(self, scorer_with_keywords: ReleaseScorer) -> None:
        """Test stripping album with multiple edition-related parentheses."""
        result = scorer_with_keywords._strip_edition_suffix("Hybrid Theory (20th Anniversary Edition) (Remastered)")
        assert result == "Hybrid Theory"

    def test_case_insensitive_matching(self, scorer_with_keywords: ReleaseScorer) -> None:
        """Test that keyword matching is case-insensitive."""
        result = scorer_with_keywords._strip_edition_suffix("Abbey Road (REMASTERED)")
        assert result == "Abbey Road"

    def test_strips_bracket_suffixes(self, scorer_with_keywords: ReleaseScorer) -> None:
        """Test stripping [Deluxe] with square brackets."""
        result = scorer_with_keywords._strip_edition_suffix("Fallen [Deluxe Edition]")
        assert result == "Fallen"


class TestRemasterKeywordsParameter:
    """Tests for remaster_keywords parameter in ReleaseScorer."""

    def test_default_keywords_empty(self) -> None:
        """Test that default remaster_keywords is empty list."""
        scorer = ReleaseScorer()
        assert scorer.remaster_keywords == []
        assert isinstance(scorer.remaster_keywords, list)

    def test_custom_keywords_stored(self) -> None:
        """Test that custom keywords are stored correctly."""
        keywords: list[str] = ["deluxe", "remaster"]
        scorer = ReleaseScorer(remaster_keywords=keywords)
        assert scorer.remaster_keywords == keywords

    def test_empty_list_keywords_stored(self) -> None:
        """Test that empty list keywords are stored correctly."""
        scorer = ReleaseScorer(remaster_keywords=[])
        assert scorer.remaster_keywords == []

    def test_factory_passes_keywords(self) -> None:
        """Test that create_release_scorer passes keywords correctly."""
        keywords: list[str] = ["deluxe", "special"]
        scorer = create_release_scorer(remaster_keywords=keywords)
        assert scorer.remaster_keywords == keywords

    def test_factory_default_keywords(self) -> None:
        """Test that create_release_scorer default is empty list."""
        scorer = create_release_scorer()
        assert scorer.remaster_keywords == []


class TestAlbumMatchWithEditionNormalization:
    """Tests for album matching with edition suffix stripping."""

    @pytest.fixture
    def scorer(self) -> ReleaseScorer:
        """Create scorer with remaster keywords."""
        return ReleaseScorer(remaster_keywords=["deluxe", "remaster", "edition", "version", "anniversary"])

    @pytest.fixture
    def scorer_without_keywords(self) -> ReleaseScorer:
        """Create scorer without remaster keywords."""
        return ReleaseScorer(remaster_keywords=[])

    def test_exact_match_with_edition_suffix_in_user_album(self, scorer: ReleaseScorer) -> None:
        """Test exact match when user has edition suffix but API doesn't."""
        release: dict[str, Any] = {
            "title": "Fallen",
            "artist": "Evanescence",
            "year": "2003",
            "source": "musicbrainz",
        }
        # User has "Fallen (Deluxe Edition)" - pass original name for edition stripping
        score = scorer.score_original_release(
            release,
            "evanescence",
            "fallen",  # normalized (unused when album_orig provided)
            artist_region=None,
            album_orig="Fallen (Deluxe Edition)",  # Original name for stripping
        )
        # Should get album exact match bonus because editions are stripped
        assert score > 30

    def test_exact_match_with_edition_suffix_in_api_album(self, scorer: ReleaseScorer) -> None:
        """Test exact match when API has edition suffix but user doesn't."""
        release: dict[str, Any] = {
            "title": "Dark Side of the Moon (2011 Remaster)",
            "artist": "Pink Floyd",
            "year": "1973",
            "source": "musicbrainz",
        }
        score = scorer.score_original_release(
            release,
            "pink floyd",
            "dark side of the moon",  # user has clean name
            artist_region=None,
        )
        # API title has (2011 Remaster) which gets stripped
        assert score > 30

    def test_exact_match_both_have_different_editions(self, scorer: ReleaseScorer) -> None:
        """Test exact match when both have different edition suffixes."""
        release: dict[str, Any] = {
            "title": "Hybrid Theory (20th Anniversary Edition)",
            "artist": "Linkin Park",
            "year": "2000",
            "source": "musicbrainz",
        }
        score = scorer.score_original_release(
            release,
            "linkin park",
            "hybrid theory",  # normalized (unused when album_orig provided)
            artist_region=None,
            album_orig="Hybrid Theory (Deluxe Edition)",  # Different edition
        )
        # Both strip to "Hybrid Theory" so should match
        assert score > 30

    def test_no_edition_stripping_without_keywords(self, scorer_without_keywords: ReleaseScorer) -> None:
        """Test that without keywords, edition suffixes are not stripped."""
        release: dict[str, Any] = {
            "title": "Fallen",
            "artist": "Evanescence",
            "year": "2003",
            "source": "musicbrainz",
        }
        # Without keywords, "Fallen" vs "Fallen (Deluxe Edition)" won't match exactly
        score_with_suffix = scorer_without_keywords.score_original_release(
            release,
            "evanescence",
            "fallen",
            artist_region=None,
            album_orig="Fallen (Deluxe Edition)",  # Won't be stripped without keywords
        )
        score_without_suffix = scorer_without_keywords.score_original_release(
            release,
            "evanescence",
            "fallen",
            artist_region=None,
        )
        # Clean match should score higher without keyword stripping
        assert score_without_suffix > score_with_suffix


class TestArtistMismatchPenalty:
    """Tests for artist mismatch penalty in scoring."""

    @pytest.fixture
    def scorer(self) -> ReleaseScorer:
        """Create a scorer instance."""
        return ReleaseScorer()

    def test_exact_artist_match_gets_bonus(self, scorer: ReleaseScorer) -> None:
        """Test that exact artist match gets bonus."""
        release: dict[str, Any] = {
            "title": "Fallen",
            "artist": "Evanescence",
            "year": "2003",
            "source": "musicbrainz",
        }
        score = scorer.score_original_release(release, "evanescence", "fallen", artist_region=None)
        # Should get positive score with artist match bonus
        assert score > 30

    def test_completely_different_artist_gets_large_penalty(self, scorer: ReleaseScorer) -> None:
        """Test that completely different artist gets large penalty."""
        # "Scorn" by artist "Scorn" should not match when searching for "Evanescence"
        release: dict[str, Any] = {
            "title": "Evanescence",  # Album name matches
            "artist": "Scorn",  # But artist is wrong
            "year": "1994",
            "source": "musicbrainz",
        }
        score = scorer.score_original_release(release, "evanescence", "evanescence", artist_region=None)
        # Should get very low or zero score due to artist mismatch
        assert score < 20

    def test_substring_artist_match_gets_smaller_penalty(self, scorer: ReleaseScorer) -> None:
        """Test that substring artist match gets smaller penalty than complete mismatch."""
        # "The Beatles" contains "Beatles"
        release_substring: dict[str, Any] = {
            "title": "Abbey Road",
            "artist": "The Beatles",
            "year": "1969",
            "source": "musicbrainz",
        }
        release_mismatch: dict[str, Any] = {
            "title": "Abbey Road",
            "artist": "Pink Floyd",  # Completely different
            "year": "1969",
            "source": "musicbrainz",
        }
        score_substring = scorer.score_original_release(release_substring, "beatles", "abbey road", artist_region=None)
        score_mismatch = scorer.score_original_release(release_mismatch, "beatles", "abbey road", artist_region=None)
        # Substring match should score higher than complete mismatch
        assert score_substring > score_mismatch

    def test_prevents_wrong_artist_same_album_name(self, scorer: ReleaseScorer) -> None:
        """Test that wrong artist with same album name is penalized heavily.

        This is the Evanescence bug case: "Scorn - Evanescence (1994)" should not
        match when searching for "Evanescence - Evanescence (2011)".
        """
        correct_release: dict[str, Any] = {
            "title": "Evanescence",
            "artist": "Evanescence",
            "year": "2011",
            "source": "musicbrainz",
        }
        wrong_release: dict[str, Any] = {
            "title": "Evanescence",  # Same album name!
            "artist": "Scorn",  # Wrong artist
            "year": "1994",
            "source": "musicbrainz",
        }
        score_correct = scorer.score_original_release(correct_release, "evanescence", "evanescence", artist_region=None)
        score_wrong = scorer.score_original_release(wrong_release, "evanescence", "evanescence", artist_region=None)
        # Correct artist+album should score MUCH higher
        assert score_correct > score_wrong + 40


class TestNonAlphanumPattern:
    """Tests for the _NON_ALPHANUM_PATTERN module constant."""

    def test_pattern_is_defined(self) -> None:
        """Test that the pattern constant is defined."""
        assert year_scoring._NON_ALPHANUM_PATTERN == r"[^\w\s]"

    def test_pattern_used_in_normalize_name(self) -> None:
        """Test that pattern is used correctly in _normalize_name."""
        scorer = ReleaseScorer()
        # Punctuation should be removed
        result = scorer._normalize_name("Test's Album (2020)")
        assert "'" not in result
        assert "(" not in result
        assert ")" not in result
        # Alphanumerics and spaces should remain
        assert "test" in result
        assert "album" in result
        assert "2020" in result


class TestCalculateArtistMatch:
    """Tests for _calculate_artist_match helper method."""

    @pytest.fixture
    def scorer(self) -> ReleaseScorer:
        """Create a scorer instance."""
        return ReleaseScorer()

    def test_exact_match_returns_bonus(self, scorer: ReleaseScorer) -> None:
        """Test that exact artist match returns bonus."""
        score_components: list[str] = []
        bonus, score = scorer._calculate_artist_match("the beatles", "the beatles", score_components)
        assert bonus > 0
        assert score > 0
        assert any("exact" in comp.lower() for comp in score_components)

    def test_substring_match_returns_penalty(self, scorer: ReleaseScorer) -> None:
        """Test that substring match returns penalty."""
        score_components: list[str] = []
        bonus, score = scorer._calculate_artist_match("the beatles", "beatles", score_components)
        assert bonus == 0
        assert score < 0
        assert any("substring" in comp.lower() for comp in score_components)

    def test_no_match_returns_large_penalty(self, scorer: ReleaseScorer) -> None:
        """Test that no match returns large penalty."""
        score_components: list[str] = []
        bonus, score = scorer._calculate_artist_match("pink floyd", "the beatles", score_components)
        assert bonus == 0
        assert score < -50
        assert any("mismatch" in comp.lower() for comp in score_components)


class TestCalculateAlbumMatch:
    """Tests for _calculate_album_match helper method."""

    @pytest.fixture
    def scorer(self) -> ReleaseScorer:
        """Create a scorer with remaster keywords."""
        return ReleaseScorer(remaster_keywords=["deluxe", "remaster"])

    def test_exact_match_returns_bonus(self, scorer: ReleaseScorer) -> None:
        """Test that exact album match returns bonus."""
        score_components: list[str] = []
        score = scorer._calculate_album_match("abbey road", "abbey road", artist_match_bonus=20, score_components=score_components)
        assert score > 0
        assert any("exact" in comp.lower() for comp in score_components)

    def test_perfect_match_with_artist_bonus(self, scorer: ReleaseScorer) -> None:
        """Test that perfect match with artist bonus gives extra points."""
        score_components: list[str] = []
        score = scorer._calculate_album_match("abbey road", "abbey road", artist_match_bonus=20, score_components=score_components)
        # Should have both album exact match and perfect match bonus
        assert score > 0
        assert any("perfect" in comp.lower() for comp in score_components)

    def test_edition_suffix_stripped_before_match(self, scorer: ReleaseScorer) -> None:
        """Test that edition suffixes are stripped before matching."""
        score_components: list[str] = []
        # "Abbey Road (Remaster)" should match "Abbey Road"
        score = scorer._calculate_album_match(
            "abbey road remaster",  # already normalized (no parens)
            "abbey road",
            artist_match_bonus=0,
            score_components=score_components,
        )
        # The _strip_edition_suffix works on original names, not normalized
        # So this tests variation matching
        assert score != 0  # Should have some match score

    def test_unrelated_albums_get_penalty(self, scorer: ReleaseScorer) -> None:
        """Test that unrelated albums get penalty."""
        score_components: list[str] = []
        score = scorer._calculate_album_match(
            "dark side of the moon",
            "abbey road",
            artist_match_bonus=0,
            score_components=score_components,
        )
        assert score < 0
        assert any("unrelated" in comp.lower() for comp in score_components)


class TestNormalizeName:
    """Tests for _normalize_name static method."""

    def test_converts_to_lowercase(self) -> None:
        """Test that names are converted to lowercase."""
        result = ReleaseScorer._normalize_name("Abbey Road")
        assert result == "abbey road"

    def test_replaces_ampersand_with_and(self) -> None:
        """Test that & is replaced with 'and'."""
        result = ReleaseScorer._normalize_name("Guns & Roses")
        assert result == "guns and roses"

    def test_removes_punctuation(self) -> None:
        """Test that punctuation is removed."""
        result = ReleaseScorer._normalize_name("It's Time!")
        assert "'" not in result
        assert "!" not in result

    def test_normalizes_whitespace(self) -> None:
        """Test that multiple spaces are normalized to single space."""
        result = ReleaseScorer._normalize_name("Abbey   Road")
        assert result == "abbey road"

    def test_handles_empty_string(self) -> None:
        """Test handling of empty string."""
        result = ReleaseScorer._normalize_name("")
        assert result == ""

    def test_handles_special_characters(self) -> None:
        r"""Test handling of special characters.

        Note: Python's \\w includes unicode letters, so ö and ü are preserved.
        Only ASCII punctuation and symbols are removed.
        """
        result = ReleaseScorer._normalize_name("Mötley Crüe!")
        # Unicode letters are preserved (part of \w in Python 3)
        assert "mötley crüe" in result
        # But punctuation is removed
        assert "!" not in result


class TestIntegrationEditionNormalization:
    """Integration tests for edition normalization in full scoring pipeline."""

    @pytest.fixture
    def scorer(self) -> ReleaseScorer:
        """Create scorer with full config."""
        return ReleaseScorer(
            remaster_keywords=[
                "remaster",
                "deluxe",
                "anniversary",
                "expanded",
                "bonus",
                "special",
                "collector",
                "edition",
                "version",
            ]
        )

    def test_real_world_evanescence_case(self, scorer: ReleaseScorer) -> None:
        """Test real-world Evanescence album matching.

        User library: "Fallen (Deluxe Edition)"
        API returns: "Fallen"
        Both should match after edition stripping.
        """
        api_release: dict[str, Any] = {
            "title": "Fallen",
            "artist": "Evanescence",
            "year": "2003",
            "source": "musicbrainz",
        }
        score = scorer.score_original_release(
            api_release,
            "evanescence",
            "fallen",  # normalized (unused when album_orig provided)
            artist_region=None,
            album_orig="Fallen (Deluxe Edition)",  # User's original album name
        )
        # Should score well because editions are normalized
        assert score > 40

    def test_real_world_beatles_remaster_case(self, scorer: ReleaseScorer) -> None:
        """Test real-world Beatles remaster matching.

        API returns remastered version, user has clean name.
        API title gets edition stripped, user's clean name matches.
        """
        api_release: dict[str, Any] = {
            "title": "Abbey Road (2019 Remaster)",
            "artist": "The Beatles",
            "year": "1969",
            "source": "musicbrainz",
        }
        score = scorer.score_original_release(
            api_release,
            "the beatles",
            "abbey road",  # User has clean name
            artist_region=None,
        )
        # API title "(2019 Remaster)" is stripped -> "Abbey Road" matches user's album
        assert score > 40

    def test_real_world_pink_floyd_anniversary(self, scorer: ReleaseScorer) -> None:
        """Test Pink Floyd anniversary edition matching.

        User has anniversary edition, API returns clean name.
        User's edition suffix is stripped via album_orig.
        """
        api_release: dict[str, Any] = {
            "title": "The Dark Side of the Moon",
            "artist": "Pink Floyd",
            "year": "1973",
            "source": "musicbrainz",
        }
        score = scorer.score_original_release(
            api_release,
            "pink floyd",
            "the dark side of the moon",  # normalized (unused when album_orig provided)
            artist_region=None,
            album_orig="The Dark Side of the Moon (50th Anniversary Edition)",
        )
        assert score > 40
