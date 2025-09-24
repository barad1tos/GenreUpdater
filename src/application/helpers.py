"""Helper functions and utilities for Music Genre Updater."""

import logging
import os
import re
from typing import Any, TypeGuard

from src.shared.data.models import CodeActionExtended as CodeAction
from src.shared.data.models import ScriptActionExtended as ScriptAction
from src.shared.data.models import TrackDict

ActionType = ScriptAction | CodeAction


def is_script_action(action: ActionType) -> TypeGuard[ScriptAction]:
    """Type guard for ScriptAction."""
    return action.type == "script"


def is_code_action(action: ActionType) -> TypeGuard[CodeAction]:
    """Type guard for CodeAction."""
    return action.type == "code"


def resolve_env_vars(
    value: str | float | bool | list[Any] | dict[str, Any],
) -> str | int | float | bool | list[Any] | dict[str, Any]:
    """Recursively resolve environment variables in configuration values.

    Args:
        value: Configuration value that may contain env var references

    Returns:
        The value with all env var references resolved

    """
    if isinstance(value, str):
        # Handle ${VAR_NAME} pattern
        pattern = r"\$\{([^}]+)\}"

        def replacer(match: re.Match[str]) -> str:
            """Replace the environment variable reference with its value.

            Args:
                match: A regex match object containing the environment variable name

            Returns:
                str: The environment variable value if found, otherwise the original match

            """
            var_name = match.group(1)
            original_text = match.group()
            return os.environ.get(var_name, original_text)

        return re.sub(pattern, replacer, value)

    if isinstance(value, dict):
        return {k: resolve_env_vars(v) for k, v in value.items()}

    if isinstance(value, list):
        return [resolve_env_vars(item) for item in value]

    # For int, float, bool, etc., return as-is
    return value


def check_paths(paths: list[str], logger: logging.Logger) -> None:
    """Check if specified paths exist and log warnings if not.

    Args:
        paths: List of file/directory paths to check
        logger: Logger instance for warnings

    """
    for path in paths:
        if not os.path.exists(path):
            logger.warning("Path does not exist: %s", path)


def is_valid_track_item(item: Any) -> TypeGuard[TrackDict]:
    """Validate that the given object is a track dictionary.

    This performs runtime type checking to ensure the object
    has the required structure of a TrackDict.

    Args:
        item: Object to validate

    Returns:
        True if the object is a valid TrackDict, False otherwise

    """
    if not isinstance(item, dict):
        return False

    # Check required fields
    required_fields = ["id", "artist", "track_name", "album_name"]
    for field in required_fields:
        if field not in item:
            return False
        if not isinstance(item[field], str):
            return False

    # Check optional fields if present
    optional_fields = ["genre", "year", "date_added", "date_modified"]
    return not any(
        field in item and item[field] is not None and not isinstance(item[field], str) for field in optional_fields
    )
