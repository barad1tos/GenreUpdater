"""Release scoring system for music metadata evaluation.

This module contains the core scoring algorithm that evaluates music releases
to determine the most likely original release year. The scoring system considers
multiple factors including artist/album matching, release characteristics,
contextual information, and source reliability.

Extracted from the legacy external API service to enable modular usage
across different API providers while preserving the sophisticated scoring logic.
"""

import contextlib
import logging
import re
from datetime import UTC
from datetime import datetime as dt
from typing import Any, TypedDict

from core.models.metadata_utils import remove_parentheses_with_keywords
from core.models.script_detection import ScriptType, detect_primary_script

# Module-level constant for name normalization
_NON_ALPHANUM_PATTERN = r"[^\w\s]"


# Type definitions for scoring context
class ArtistPeriodContext(TypedDict, total=False):
    """Context about an artist's active period."""

    start_year: int | None
    end_year: int | None


class ScoringConfig(TypedDict, total=False):
    """Configuration parameters for release scoring."""

    # Base scoring
    base_score: int

    # Match bonuses
    artist_exact_match_bonus: int
    album_exact_match_bonus: int
    album_variation_bonus: int
    perfect_match_bonus: int

    # Match penalties
    album_substring_penalty: int
    album_unrelated_penalty: int

    # Release characteristics
    type_album_bonus: int
    type_ep_single_penalty: int
    type_compilation_live_penalty: int
    status_official_bonus: int
    status_bootleg_penalty: int
    status_promo_penalty: int
    reissue_penalty: int

    # MusicBrainz specific
    mb_release_group_match_bonus: int

    # Artist activity period context
    year_before_start_penalty: int
    year_after_end_penalty: int
    year_near_start_bonus: int

    # Year difference from the release group
    year_diff_penalty_scale: int
    year_diff_max_penalty: int

    # Country/region matching
    country_artist_match_bonus: int
    country_major_market_bonus: int
    major_market_codes: list[str]

    # Source reliability
    source_mb_bonus: int
    source_discogs_bonus: int
    source_itunes_bonus: int
    source_lastfm_penalty: int


class ReleaseScorer:
    """Release scoring system for evaluating music metadata quality.

    This class implements the sophisticated scoring algorithm that evaluates
    releases from different sources to determine the most likely original release.
    The algorithm considers multiple factors and applies configuration-driven
    scoring rules to ensure consistent and accurate results.

    Attributes:
        scoring_config: Configuration dictionary with scoring parameters
        min_valid_year: Minimum valid year for releases (default: 1900)
        current_year: Current year for validation (default: current system year)
        definitive_score_threshold: Threshold for considering a score definitive
        artist_period_context: Optional context about artist's active period
        console_logger: Logger for debug output

    """

    def __init__(
        self,
        scoring_config: dict[str, Any] | None = None,
        min_valid_year: int = 1900,
        definitive_score_threshold: int = 85,
        console_logger: logging.Logger | None = None,
        remaster_keywords: list[str] | None = None,
    ) -> None:
        """Initialize the release scorer.

        Args:
            scoring_config: Configuration dictionary with scoring parameters
            min_valid_year: Minimum valid year for releases
            definitive_score_threshold: Threshold for definitive scoring
            console_logger: Optional logger for debug output
            remaster_keywords: Keywords to identify edition suffixes (e.g., "deluxe", "remaster")

        """
        self.scoring_config = scoring_config or self._get_default_scoring_config()
        self.min_valid_year = min_valid_year
        self.current_year = dt.now(UTC).year
        self.definitive_score_threshold = definitive_score_threshold
        self.artist_period_context: ArtistPeriodContext | None = None
        self.console_logger = console_logger or logging.getLogger(__name__)
        self.remaster_keywords = remaster_keywords or []

        # Constants from the original implementation
        self.YEAR_LENGTH = 4

    def set_artist_period_context(self, context: ArtistPeriodContext | None) -> None:
        """Set the artist activity period context for scoring.

        Args:
            context: Dictionary with start_year and end_year information

        """
        self.artist_period_context = context

    def clear_artist_period_context(self) -> None:
        """Clear the artist activity period context."""
        self.artist_period_context = None

    @staticmethod
    def _get_default_scoring_config() -> dict[str, Any]:
        """Get the default scoring configuration.

        Returns:
            Dictionary with default scoring parameters

        """
        return {
            # Base scoring
            "base_score": 10,
            # Match bonuses
            "artist_exact_match_bonus": 20,
            "album_exact_match_bonus": 25,
            "album_variation_bonus": 10,
            "perfect_match_bonus": 10,
            # Soundtrack compensation (when artist is "Various Artists" etc.)
            # Needs to cover artist mismatch (-60) + album substring (-15) = 75
            "soundtrack_compensation_bonus": 75,
            # Match penalties
            "album_substring_penalty": -15,
            "album_unrelated_penalty": -40,
            # Release characteristics
            "type_album_bonus": 15,
            "type_ep_single_penalty": -10,
            "type_compilation_live_penalty": -25,
            "status_official_bonus": 10,
            "status_bootleg_penalty": -50,
            "status_promo_penalty": -20,
            "reissue_penalty": -30,
            # MusicBrainz specific
            "mb_release_group_match_bonus": 50,
            # Artist activity period context
            "year_before_start_penalty": -25,  # Variable penalty
            "year_after_end_penalty": -20,  # Variable penalty
            "year_near_start_bonus": 20,
            # Year difference from the release group
            "year_diff_penalty_scale": -5,
            "year_diff_max_penalty": -40,
            # Country/region matching
            "country_artist_match_bonus": 10,
            "country_major_market_bonus": 5,
            "major_market_codes": ["us", "gb", "uk", "de", "jp", "fr"],
            # Source reliability
            "source_mb_bonus": 5,
            "source_discogs_bonus": 2,
            "source_itunes_bonus": 4,
            "source_lastfm_penalty": -5,
            # Future year penalty
            "future_year_penalty": -10,
        }

    @staticmethod
    def _normalize_name(name: str) -> str:
        """Normalize an artist or album name for matching.

        Converts to lowercase, removes special characters, normalizes whitespace,
        and handles common variations like '&' vs 'and'.

        Args:
            name: The name to normalize

        Returns:
            Normalized name string

        """
        if not name:
            return ""

        # Convert to lowercase
        normalized = name.lower()

        # Replace '&' with 'and'
        normalized = normalized.replace("&", "and")

        # Remove common punctuation and special characters
        normalized = re.sub(_NON_ALPHANUM_PATTERN, "", normalized)

        # Normalize whitespace (multiple spaces to single space)
        normalized = re.sub(r"\s+", " ", normalized)

        # Strip leading/trailing whitespace
        return normalized.strip()

    def _is_valid_year(
        self,
        year_str: str,
        min_valid_year: int | None = None,
    ) -> bool:
        """Check if a year string represents a valid release year.

        Args:
            year_str: Year string to validate
            min_valid_year: Minimum valid year (optional)

        Returns:
            True if the year is valid, False otherwise

        """
        if not year_str:
            return False

        if not year_str.isdigit() or len(year_str) != self.YEAR_LENGTH:
            return False

        try:
            year = int(year_str)
            min_year = min_valid_year or self.min_valid_year
            # Trust the system - if datetime accepts it, it's valid
            dt(year, 1, 1, tzinfo=UTC)
            return year >= min_year
        except (ValueError, OverflowError, OSError):
            return False

    def _validate_year(self, year_str: str, score_components: list[str]) -> tuple[int | None, bool]:
        """Validate year string and return parsed year if valid.

        Args:
            year_str: Year string to validate
            score_components: List to append validation messages to

        Returns:
            Tuple of (parsed_year, is_valid)

        """
        if not year_str or not year_str.isdigit() or len(year_str) != self.YEAR_LENGTH:
            score_components.append("Year Invalid Format: score=0")
            return None, False

        try:
            year = int(year_str)
        except ValueError:
            score_components.append("Year Invalid Format: score=0")
            return None, False

        try:
            # Let the system validate the year
            dt(year, 1, 1, tzinfo=UTC)
            if year >= self.min_valid_year:
                score_components.append(f"Year Valid: {year} (system validated)")
                return year, True

            score_components.append(f"Year Too Old: {year} < {self.min_valid_year}: score=0")
            return None, False
        except (ValueError, OverflowError, OSError):
            score_components.append(f"Year Invalid: {year} (system rejected): score=0")
            return None, False

    def _calculate_match_score(
        self,
        release_artist_norm: str,
        artist_norm: str,
        release_title_norm: str,
        album_norm: str,
        *,
        score_components: list[str],
    ) -> int:
        """Calculate core match quality score.

        Args:
            release_artist_norm: Normalized release artist name
            artist_norm: Normalized target artist name
            release_title_norm: Normalized release title
            album_norm: Normalized target album name
            score_components: List to append score messages to

        Returns:
            Match score adjustment

        """
        # Normalize target names using same method as release names
        # This ensures consistent comparison (both lowercased, punctuation removed)
        target_artist_norm = self._normalize_name(artist_norm)
        target_album_norm = self._normalize_name(album_norm)

        # Calculate artist match score
        artist_match_bonus, artist_score = self._calculate_artist_match(release_artist_norm, target_artist_norm, score_components)

        # Calculate album match score
        album_score = self._calculate_album_match(release_title_norm, target_album_norm, artist_match_bonus, score_components)

        return artist_score + album_score

    def _calculate_artist_match(
        self,
        release_artist_norm: str,
        target_artist_norm: str,
        score_components: list[str],
    ) -> tuple[int, int]:
        """Calculate artist match score.

        Args:
            release_artist_norm: Normalized release artist name
            target_artist_norm: Normalized target artist name
            score_components: List to append score messages to

        Returns:
            Tuple of (artist_match_bonus, score_adjustment)

        """
        scoring_cfg = self.scoring_config

        if release_artist_norm and release_artist_norm == target_artist_norm:
            bonus = int(scoring_cfg.get("artist_exact_match_bonus", 20))
            score_components.append(f"Artist Exact Match: +{bonus}")
            return bonus, bonus

        if not (release_artist_norm and target_artist_norm):
            return 0, 0

        # Artist doesn't match - apply penalties
        if target_artist_norm in release_artist_norm or release_artist_norm in target_artist_norm:
            # Partial match (substring) - moderate penalty
            penalty = int(scoring_cfg.get("artist_substring_penalty", -20))
            score_components.append(f"Artist Substring Mismatch: {penalty}")
            return 0, penalty

        # Check for cross-script comparison (e.g., Cyrillic target vs Latin result from iTunes)
        # iTunes returns Latinized artist names for non-Latin artists, but Cyrillic album titles
        # In this case, reduce penalty to allow album matching to carry the score
        if self._is_cross_script_comparison(target_artist_norm, release_artist_norm):
            penalty = int(scoring_cfg.get("artist_cross_script_penalty", -10))
            score_components.append(f"Artist Cross-Script (transliteration likely): {penalty}")
            return 0, penalty

        # Completely different artist - LARGE penalty
        # This prevents "Scorn - Evanescence" from matching "Evanescence - Evanescence"
        penalty = int(scoring_cfg.get("artist_mismatch_penalty", -60))
        score_components.append(f"Artist Mismatch: {penalty}")
        return 0, penalty

    @staticmethod
    def _is_cross_script_comparison(text1: str, text2: str) -> bool:
        """Check if two texts use different writing scripts.

        This detects cases like Cyrillic vs Latin comparison, which often indicates
        a transliteration (e.g., "Ляпис Трубецкой" vs "Lyapis Trubetskoy" from iTunes).

        Args:
            text1: First text to compare
            text2: Second text to compare

        Returns:
            True if texts use different scripts (one Latin, one non-Latin)

        """
        script1 = detect_primary_script(text1)
        script2 = detect_primary_script(text2)

        # Cross-script if one is Latin and the other is a non-Latin script
        non_latin_scripts = {
            ScriptType.CYRILLIC,
            ScriptType.CHINESE,
            ScriptType.JAPANESE,
            ScriptType.KOREAN,
            ScriptType.ARABIC,
            ScriptType.HEBREW,
            ScriptType.GREEK,
            ScriptType.THAI,
            ScriptType.DEVANAGARI,
        }

        is_text1_latin = script1 == ScriptType.LATIN
        is_text2_latin = script2 == ScriptType.LATIN
        is_text1_non_latin = script1 in non_latin_scripts
        is_text2_non_latin = script2 in non_latin_scripts

        return (is_text1_latin and is_text2_non_latin) or (is_text1_non_latin and is_text2_latin)

    @staticmethod
    def _is_soundtrack_artist(artist_norm: str) -> bool:
        """Check if artist name indicates a soundtrack or compilation.

        Soundtrack albums often have generic artist names like "Various Artists",
        "Original Soundtrack", "OST", etc. When searching APIs, the actual composer
        is returned (e.g., "Hans Zimmer" for Interstellar), so artist matching
        should be relaxed to rely on album name matching.

        Args:
            artist_norm: Normalized artist name to check

        Returns:
            True if artist name matches soundtrack/compilation patterns

        """
        if not artist_norm:
            return False

        # Exact matches (normalized, lowercase)
        soundtrack_artists = {
            "various artists",
            "various",
            "va",
            "ost",
            "original soundtrack",
            "original motion picture soundtrack",
            "original score",
            "soundtrack",
            "film soundtrack",
            "movie soundtrack",
            "game soundtrack",
            "video game soundtrack",
            "tv soundtrack",
            "television soundtrack",
            "compilation",
            "various performers",
        }

        artist_lower = artist_norm.lower().strip()

        # Check exact match
        if artist_lower in soundtrack_artists:
            return True

        # Check if starts with common patterns
        soundtrack_prefixes = ("various ", "original ", "ost ", "soundtrack ")
        if any(artist_lower.startswith(prefix) for prefix in soundtrack_prefixes):
            return True

        # Check if contains definitive soundtrack indicators
        return "soundtrack" in artist_lower or "original score" in artist_lower

    def _calculate_soundtrack_compensation(
        self,
        target_artist_norm: str,
        release_title_norm: str,
        target_album_norm: str,
        release_genre: str,
        score_components: list[str],
    ) -> int:
        """Calculate compensation bonus for soundtrack albums.

        When searching for soundtracks (artist = "Various Artists", "OST", etc.),
        APIs return the actual composer (e.g., "Hans Zimmer" for Interstellar).
        This causes a heavy artist mismatch penalty.

        This method compensates for that penalty ONLY when:
        1. Target artist is a soundtrack pattern (Various Artists, OST, etc.)
        2. Album names match (substring matching - handles suffixes like "- Original Soundtrack")
        3. API confirms the genre is "Soundtrack"

        Without all 3 conditions, we risk false positives like matching
        "Various Artists - Chill Vibes" with unrelated "John Smith - Chill Vibes".

        Args:
            target_artist_norm: Normalized target artist name
            release_title_norm: Normalized release title from API
            target_album_norm: Normalized target album name
            release_genre: Genre string from API response
            score_components: List to append score explanations

        Returns:
            Compensation bonus (positive int) if all conditions met, else 0

        """
        # Condition 1: Target artist must be a soundtrack pattern
        if not self._is_soundtrack_artist(target_artist_norm):
            return 0

        # Condition 2: Album names must match (substring matching)
        # Use same pattern as _calculate_album_match (line 595)
        # This handles "Aladdin" matching "Aladdin - Original Soundtrack"
        comp_release = re.sub(_NON_ALPHANUM_PATTERN, "", release_title_norm.lower()).strip()
        comp_target = re.sub(_NON_ALPHANUM_PATTERN, "", target_album_norm.lower()).strip()

        if comp_target not in comp_release and comp_release not in comp_target:
            return 0

        # Condition 3: API must confirm this is a soundtrack
        # Genre field may contain multiple genres or variations
        genre_lower = (release_genre or "").lower()
        is_soundtrack_genre = any(keyword in genre_lower for keyword in ("soundtrack", "score", "film music", "ost"))

        if not is_soundtrack_genre:
            return 0

        # All conditions met - apply compensation
        # This should offset the artist mismatch penalty (~-60) to allow
        # the album match and other factors to determine the final score
        compensation: int = int(self.scoring_config.get("soundtrack_compensation_bonus", 75))
        score_components.append(f"Soundtrack Compensation (album match + genre confirmed): +{compensation}")
        return compensation

    def _strip_edition_suffix(self, album_name: str) -> str:
        """Strip edition suffixes like (Deluxe), (Remastered) for fairer comparison.

        Uses remaster_keywords from config to identify edition suffix patterns.
        Only strips content in parentheses/brackets containing keywords.

        Args:
            album_name: Album name that may contain edition suffixes

        Returns:
            Album name with edition suffixes removed

        """
        if not self.remaster_keywords or not album_name:
            return album_name

        return remove_parentheses_with_keywords(
            album_name,
            self.remaster_keywords,
            self.console_logger,
            self.console_logger,  # Use same logger for errors
        )

    def _calculate_album_match(
        self,
        release_title_norm: str,
        target_album_norm: str,
        artist_match_bonus: int,
        score_components: list[str],
    ) -> int:
        """Calculate album title match score.

        Args:
            release_title_norm: Normalized release title
            target_album_norm: Normalized target album name
            artist_match_bonus: Bonus from artist matching (for perfect match calculation)
            score_components: List to append score messages to

        Returns:
            Score adjustment

        """
        scoring_cfg = self.scoring_config

        # Simple normalization for comparison (edition stripping happens earlier in pipeline)
        comp_release_title = re.sub(_NON_ALPHANUM_PATTERN, "", release_title_norm.lower()).strip()
        comp_album_norm = re.sub(_NON_ALPHANUM_PATTERN, "", target_album_norm.lower()).strip()

        if comp_release_title == comp_album_norm:
            bonus = int(scoring_cfg.get("album_exact_match_bonus", 25))
            score_components.append(f"Album Exact Match: +{bonus}")
            if artist_match_bonus > 0:
                perfect_bonus = int(scoring_cfg.get("perfect_match_bonus", 10))
                score_components.append(f"Perfect Artist+Album Match: +{perfect_bonus}")
                return bonus + perfect_bonus
            return bonus

        if self._is_album_variation(comp_release_title, comp_album_norm):
            bonus = int(scoring_cfg.get("album_variation_bonus", 10))
            score_components.append(f"Album Variation (Suffix): +{bonus}")
            return bonus

        if self._is_album_variation(comp_album_norm, comp_release_title):
            bonus = int(scoring_cfg.get("album_variation_bonus", 10))
            score_components.append(f"Album Variation (Search Suffix): +{bonus}")
            return bonus

        if comp_album_norm in comp_release_title or comp_release_title in comp_album_norm:
            penalty = int(scoring_cfg.get("album_substring_penalty", -15))
            score_components.append(f"Album Substring Mismatch: {penalty}")
            return penalty

        penalty = int(scoring_cfg.get("album_unrelated_penalty", -40))
        score_components.append(f"Album Unrelated: {penalty}")
        return penalty

    @staticmethod
    def _is_album_variation(title1: str, title2: str) -> bool:
        """Check if title1 is a variation of title2 (e.g., with suffix in parentheses)."""
        return title1.startswith(title2) and bool(re.match(r"^[([^)\]]+[)\]]$", title1[len(title2) :].strip()))

    def _calculate_release_characteristics_score(
        self,
        release: dict[str, Any],
        year_str: str,
        source: str,
        score_components: list[str],
    ) -> tuple[int, int | None]:
        """Calculate release characteristics score and extract RG first year.

        Args:
            release: Release metadata dictionary
            year_str: Release year string
            source: Data source name
            score_components: List to append score messages to

        Returns:
            Tuple of (characteristics_score, rg_first_year)

        """
        scoring_cfg = self.scoring_config
        char_score = 0
        rg_first_year = None

        # Release Group First Date Match (MusicBrainz or Discogs Master Release)
        rg_first_date_str = release.get("releasegroup_first_date")
        if rg_first_date_str and isinstance(rg_first_date_str, str):
            rg_first_year = self._extract_rg_first_year(rg_first_date_str)
            # Only MusicBrainz gets the release group match bonus (RG is a MB concept)
            # Year diff penalty applies to all sources with rg_first_year
            if rg_first_year is not None and source == "musicbrainz" and year_str == str(rg_first_year):
                rg_match_bonus: int = int(scoring_cfg.get("mb_release_group_match_bonus", 50))
                char_score += rg_match_bonus
                score_components.append(f"MB RG First Date Match: +{rg_match_bonus}")

        # Release Type scoring
        char_score += self._score_release_type(release, score_components)

        # Release Status scoring
        char_score += self._score_release_status(release, score_components)

        # Reissue penalty
        if release.get("is_reissue", False):
            reissue_penalty: int = int(scoring_cfg.get("reissue_penalty", -30))
            char_score += reissue_penalty
            score_components.append(f"Reissue Indicator: {reissue_penalty}")

        return char_score, rg_first_year

    def _extract_rg_first_year(self, rg_first_date_str: str) -> int | None:
        """Extract RG first year from date string.

        Note: Score component logging is handled by caller based on source.
        """
        with contextlib.suppress(IndexError, ValueError, TypeError):
            rg_year_str = rg_first_date_str.split("-")[0]
            if len(rg_year_str) == self.YEAR_LENGTH and rg_year_str.isdigit():
                return int(rg_year_str)
        return None

    def _score_release_type(self, release: dict[str, Any], score_components: list[str]) -> int:
        """Score release type (album, EP, single, etc.)."""
        scoring_cfg = self.scoring_config
        release_type = str(release.get("album_type", release.get("type", ""))).lower()

        if "album" in release_type:
            type_bonus: int = int(scoring_cfg.get("type_album_bonus", 15))
            score_components.append(f"Type Album: +{type_bonus}")
            return type_bonus
        if any(t in release_type for t in ["ep", "single"]):
            type_penalty: int = int(scoring_cfg.get("type_ep_single_penalty", -10))
            score_components.append(f"Type EP/Single: {type_penalty}")
            return type_penalty
        if any(t in release_type for t in ["compilation", "live", "soundtrack", "remix"]):
            type_comp_penalty = int(scoring_cfg.get("type_compilation_live_penalty", -25))
            score_components.append(f"Type Comp/Live/Remix/Soundtrack: {type_comp_penalty}")
            return type_comp_penalty
        return 0

    def _score_release_status(self, release: dict[str, Any], score_components: list[str]) -> int:
        """Score release status (official, bootleg, promo, etc.)."""
        scoring_cfg = self.scoring_config
        status = str(release.get("status", "")).lower()

        if status == "official":
            status_bonus: int = int(scoring_cfg.get("status_official_bonus", 10))
            score_components.append(f"Status Official: +{status_bonus}")
            return status_bonus
        if any(s in status for s in ["bootleg", "unofficial", "pseudorelease"]):
            status_penalty: int = int(scoring_cfg.get("status_bootleg_penalty", -50))
            score_components.append(f"Status Bootleg/Unofficial: {status_penalty}")
            return status_penalty
        if any(s in status for s in ["promotion", "promo", "promotional"]):
            status_promo_penalty = int(scoring_cfg.get("status_promo_penalty", -20))
            score_components.append(f"Status Promo: {status_promo_penalty}")
            return status_promo_penalty
        return 0

    def _calculate_contextual_score(self, year: int, rg_first_year: int | None, score_components: list[str]) -> int:
        """Calculate contextual factors score (artist period, year differences).

        Args:
            year: Validated release year
            rg_first_year: Release group first year (if available)
            score_components: List to append score messages to

        Returns:
            Contextual score adjustment

        """
        contextual_score = 0

        # Apply Artist Activity Period Context
        if self.artist_period_context:
            contextual_score += self._score_artist_period(year, score_components)

        # Penalty based on difference from RG First Year
        if rg_first_year and year > rg_first_year + 1:
            contextual_score += self._score_year_difference(year, rg_first_year, score_components)

        return contextual_score

    def _score_artist_period(self, year: int, score_components: list[str]) -> int:
        """Score based on artist activity period context."""
        if self.artist_period_context is None:
            return 0
        scoring_cfg = self.scoring_config
        period_score = 0
        start_year: int | None = self.artist_period_context.get("start_year")
        end_year: int | None = self.artist_period_context.get("end_year")

        # Penalty if the year is before the artist's start (allow 1-year grace)
        # Config values are expected to be negative (per schema Field(le=0))
        if start_year and year < start_year - 1:
            years_before = start_year - year
            penalty_val = min(50, 5 + (years_before - 1) * 5)
            penalty: int = int(scoring_cfg.get("year_before_start_penalty", -penalty_val))
            period_score += penalty
            score_components.append(f"Year Before Start ({years_before} yrs): {penalty}")

        # Penalty if the year is after the artist's end (allow 3-year grace)
        if end_year and year > end_year + 3:
            years_after = year - end_year
            penalty_val = min(40, 5 + (years_after - 3) * 3)
            penalty_after = int(scoring_cfg.get("year_after_end_penalty", -penalty_val))
            period_score += penalty_after
            score_components.append(f"Year After End ({years_after} yrs): {penalty_after}")

        # Bonus if year is near artist start
        if start_year and 0 <= (year - start_year) <= 1:
            near_start_bonus: int = int(scoring_cfg.get("year_near_start_bonus", 20))
            period_score += near_start_bonus
            score_components.append(f"Year Near Start: +{near_start_bonus}")

        return period_score

    def _score_year_difference(self, year: int, rg_first_year: int, score_components: list[str]) -> int:
        """Score penalty based on the difference from the release group first year."""
        scoring_cfg = self.scoring_config
        year_diff = year - rg_first_year
        penalty_scale: int = int(scoring_cfg.get("year_diff_penalty_scale", -5))
        max_penalty: int = int(scoring_cfg.get("year_diff_max_penalty", -40))
        year_diff_penalty = max(max_penalty, (year_diff - 1) * penalty_scale)
        score_components.append(f"Year Diff from RG Date ({year_diff} yrs): {year_diff_penalty}")
        return year_diff_penalty

    def _calculate_country_score(
        self,
        release: dict[str, Any],
        artist_region: str | None,
        score_components: list[str],
    ) -> int:
        """Calculate country/region matching score.

        Args:
            release: Release metadata dictionary
            artist_region: Artist's region/country
            score_components: List to append score messages to

        Returns:
            Country score adjustment

        """
        scoring_cfg = self.scoring_config
        release_country = (release.get("country") or "").lower()
        artist_region_normalized = (artist_region or "").lower()

        # Normalize equivalent country codes (UK/GB are the same)
        country_aliases = {"uk": "gb"}
        release_country = country_aliases.get(release_country, release_country)
        artist_region_normalized = country_aliases.get(artist_region_normalized, artist_region_normalized)

        if not artist_region_normalized or not release_country:
            return 0

        if release_country == artist_region_normalized:
            country_bonus: int = int(scoring_cfg.get("country_artist_match_bonus", 10))
            score_components.append(f"Country Matches Artist Region ({artist_region_normalized.upper()}): +{country_bonus}")
            return country_bonus
        if release_country in scoring_cfg.get("major_market_codes", ["us", "gb", "uk", "de", "jp", "fr"]):
            market_bonus: int = int(scoring_cfg.get("country_major_market_bonus", 5))
            score_components.append(f"Country Major Market ({release_country.upper()}): +{market_bonus}")
            return market_bonus
        return 0

    def _calculate_source_score(self, source: str, score_components: list[str]) -> int:
        """Calculate source reliability score.

        Args:
            source: Data source name
            score_components: List to append score messages to

        Returns:
            Source reliability score adjustment

        """
        scoring_cfg = self.scoring_config
        source_adjustment = 0

        if source == "musicbrainz":
            source_adjustment = int(scoring_cfg.get("source_mb_bonus", 5))
        elif source == "discogs":
            source_adjustment = int(scoring_cfg.get("source_discogs_bonus", 2))
        elif source == "itunes":
            source_adjustment = int(scoring_cfg.get("source_itunes_bonus", 4))
        elif source == "lastfm":
            source_adjustment = int(scoring_cfg.get("source_lastfm_penalty", -5))

        if source_adjustment != 0:
            score_components.append(f"Source {source.title()}: {source_adjustment:+}")

        return source_adjustment

    def score_original_release(
        self,
        release: dict[str, Any],
        artist_norm: str,
        album_norm: str,
        *,
        artist_region: str | None,
        source: str = "unknown",
        album_orig: str | None = None,
    ) -> int:
        """REVISED scoring function prioritizing original release indicators (v3).

        This is the core scoring algorithm that evaluates a release against multiple
        criteria to determine how likely it is to be the original release of an album.

        The scoring considered:
        1. Core match quality (artist/album name matching)
        2. Release characteristics (type, status, reissue indicators)
        3. Contextual factors (year validation, artist activity period)
        4. Source reliability (MusicBrainz > Discogs > Last.fm)

        Args:
            release: Dictionary containing release metadata
            artist_norm: Normalized artist name for matching
            album_norm: Normalized album name for matching
            artist_region: Artist's region/country for bonus scoring
            source: Source of the release data (musicbrainz, discogs, lastfm)
            album_orig: Original album name with parentheses for edition stripping

        Returns:
            Integer score (0-100+) indicating release quality/originality

        """
        scoring_cfg = self.scoring_config
        score: int = int(scoring_cfg.get("base_score", 10))
        score_components: list[str] = []

        # Extract key fields
        release_title_orig = release.get("title", "") or ""
        release_artist_orig = release.get("artist", "") or ""
        year_str = release.get("year", "") or ""
        source = release.get("source", source)

        # Strip edition suffixes BEFORE normalization (preserves parentheses for stripping)
        release_title_stripped = self._strip_edition_suffix(release_title_orig)
        release_title_norm = self._normalize_name(release_title_stripped)
        release_artist_norm = self._normalize_name(release_artist_orig)

        # If original album name provided, strip editions from it too
        if album_orig:
            album_stripped = self._strip_edition_suffix(album_orig)
            album_norm = self._normalize_name(album_stripped)

        # Validate year first (early return if invalid)
        validated_year, is_valid = self._validate_year(year_str, score_components)
        if not is_valid or validated_year is None:
            return 0

        # At this point, validated_year is guaranteed to be int
        year: int = validated_year

        # Apply penalties for current and future year releases
        if year > self.current_year:
            # Future years are suspicious (likely incorrect data)
            future_penalty: int = int(scoring_cfg.get("future_year_penalty", -10))
            score += future_penalty
            score_components.append(f"Future Year ({year}): {future_penalty}")
        elif year == self.current_year:
            # Current year: small penalty to prefer earlier releases when ambiguous
            current_year_penalty: int = int(scoring_cfg.get("current_year_penalty", 0))
            score += current_year_penalty
            if current_year_penalty != 0:
                score_components.append(f"Current Year ({year}): {current_year_penalty}")

        # Calculate score components
        score += self._calculate_match_score(
            release_artist_norm,
            artist_norm,
            release_title_norm,
            album_norm,
            score_components=score_components,
        )

        # Soundtrack compensation: if target is "Various Artists" etc., but album matches exactly
        # and API confirms it's a soundtrack, compensate for the artist mismatch penalty
        score += self._calculate_soundtrack_compensation(
            target_artist_norm=artist_norm,
            release_title_norm=release_title_norm,
            target_album_norm=album_norm,
            release_genre=release.get("genre", ""),
            score_components=score_components,
        )

        char_score, rg_first_year = self._calculate_release_characteristics_score(release, year_str, source, score_components)
        score += char_score

        score += self._calculate_contextual_score(year, rg_first_year, score_components)
        score += self._calculate_country_score(release, artist_region, score_components)
        score += self._calculate_source_score(source, score_components)

        final_score = max(0, score)

        # Debug logging for significant scores
        if final_score > self.definitive_score_threshold - 20 or any("penalty" in comp.lower() for comp in score_components):
            debug_log_msg = f"Score Calculation for '{release_title_orig}' ({year_str}) [{source}]:\n"
            debug_log_msg += "\n".join([f"  - {comp}" for comp in score_components])
            debug_log_msg += f"\n  ==> Final Score: {final_score}"
            self.console_logger.debug(debug_log_msg)

        return final_score


# Factory function for easy usage
def create_release_scorer(
    scoring_config: dict[str, Any] | None = None,
    min_valid_year: int = 1900,
    definitive_score_threshold: int = 85,
    console_logger: logging.Logger | None = None,
    remaster_keywords: list[str] | None = None,
) -> ReleaseScorer:
    """Create a configured ReleaseScorer instance.

    Args:
        scoring_config: Configuration dictionary with scoring parameters
        min_valid_year: Minimum valid year for releases
        definitive_score_threshold: Threshold for definitive scoring
        console_logger: Optional logger for debug output
        remaster_keywords: Keywords to identify edition suffixes (e.g., "deluxe", "remaster")

    Returns:
        Configured ReleaseScorer instance

    """
    return ReleaseScorer(
        scoring_config=scoring_config,
        min_valid_year=min_valid_year,
        definitive_score_threshold=definitive_score_threshold,
        console_logger=console_logger,
        remaster_keywords=remaster_keywords,
    )
