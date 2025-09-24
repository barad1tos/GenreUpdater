#!/usr/bin/env python3

"""Configuration management for Genres Autoupdater v2.0."""

from __future__ import annotations

import logging
import os
import pathlib
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import ValidationError

from src.shared.core.exceptions import ConfigurationError
from src.shared.core.retry_handler import ConfigurationRetryHandler
from src.shared.data.models import AppConfig

# Type definitions for configuration
ConfigValue = dict[str, Any] | list[Any] | str | int | float | bool | None

# Set up logger early so it's available for import errors
logger = logging.getLogger("config")
# Don't set basicConfig here - it will be done later by the main logger setup
# This prevents early log output before Rich is initialized
if not logger.handlers:
    logger.addHandler(logging.NullHandler())
logger.setLevel(logging.INFO)

# Define constants
LOG_LEVELS: list[str] = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "NOTSET"]
REQUIRED_ENV_VARS: list[str] = ["DISCOGS_TOKEN", "CONTACT_EMAIL"]


def resolve_env_vars(config: ConfigValue) -> ConfigValue:
    """Recursively resolve environment variables in config values.

    Args:
        config: Configuration value (dict, list, or primitive).

    Returns:
        ConfigValue: Config with environment variables resolved.

    """
    if isinstance(config, dict):
        return {str(k): resolve_env_vars(v) for k, v in config.items()}
    if isinstance(config, list):
        return [resolve_env_vars(item) for item in config]
    if isinstance(config, str) and config.startswith("${") and config.endswith("}"):
        var_name = config[2:-1]
        return os.getenv(var_name, "")
    return config


def _validate_config_path(path: str) -> pathlib.Path:
    """Resolve and validate the configuration file path for security and correctness.

    Args:
        path: The user-provided path to the configuration file.

    Returns:
        A resolved and validated pathlib.Path object.

    Raises:
        FileNotFoundError: If the path does not exist or is not a file.
        ValueError: If the path is outside allowed directories or has a wrong extension.
        PermissionError: If the file is not readable.

    """
    try:
        # Resolve the path to get the absolute, canonical path, preventing symlink/../ attacks
        resolved_path = pathlib.Path(path).resolve(strict=True)
    except FileNotFoundError as e:
        msg = f"Config file not found at the specified path: {path}"
        raise FileNotFoundError(msg) from e

    if not resolved_path.is_file():
        msg = f"Config path does not point to a file: {resolved_path}"
        raise FileNotFoundError(msg)

    # Define allowed directories for security to prevent loading from arbitrary locations
    allowed_dirs = [
        pathlib.Path.cwd().resolve(),
        (pathlib.Path.home() / ".config").resolve(),
    ]

    # Ensure the resolved path is within one of the allowed directories
    if not any(str(resolved_path).startswith(str(d)) for d in allowed_dirs):
        allowed_paths_str = ", ".join(f'"{d}"' for d in allowed_dirs)
        msg = f"Access to {resolved_path} is not allowed. Config must be in one of: {allowed_paths_str}"
        raise ValueError(
            msg,
        )

    if not os.access(resolved_path, os.R_OK):
        msg = f"No read permission for config file: {resolved_path}"
        raise PermissionError(msg)

    if resolved_path.suffix.lower() not in (".yaml", ".yml"):
        msg = "Configuration file must have a .yaml or .yml extension"
        raise ValueError(msg)

    return resolved_path


def _read_and_parse_config(path: pathlib.Path) -> dict[str, Any] | list[Any] | str | int | float | bool | None:
    """Read and parse the YAML config file with size validation."""
    max_size = 1024 * 1024  # 1MB
    if path.stat().st_size > max_size:
        msg = f"Config file {path} is too large (max {max_size} bytes)"
        raise ValueError(msg)

    logger.info("Loading config from: %s", path)
    content = path.read_text(encoding="utf-8")
    parsed_yaml: dict[str, Any] | list[Any] | str | int | float | bool | None = yaml.safe_load(content)
    return parsed_yaml


def validate_required_env_vars() -> list[str]:
    """Validate required environment variables.

    Returns:
        list[str]: List of missing required environment variables.

    """
    missing: list[str] = []
    for var in REQUIRED_ENV_VARS:
        value = os.getenv(var)
        if not value or value.startswith("$"):
            missing.append(var)
    return missing


def _validate_config_data_type(config_data: dict[str, Any] | list[Any] | str | float | bool | None) -> dict[str, Any]:
    """Validate that config data is a dictionary and return it typed correctly.

    Args:
        config_data: Configuration data from YAML parsing

    Returns:
        The config data as a dictionary

    Raises:
        TypeError: If configuration data is not a dictionary

    """
    if not isinstance(config_data, dict):
        msg = "Configuration data is not a dictionary after parsing."
        raise TypeError(msg)
    return config_data


def load_config(config_path: str) -> dict[str, Any]:
    """Load the configuration from a YAML file, resolve environment variables, and validate it.

    Args:
        config_path: Path to the configuration YAML file.

    Returns:
        dict: Dictionary containing the validated configuration with resolved env vars.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the configuration is invalid, the path is insecure, or env vars are missing.
        PermissionError: If the config file cannot be read.
        yaml.YAMLError: If there is an error parsing the YAML file.
        RuntimeError: For unexpected errors during additional validation steps.

    """
    env_loaded = load_dotenv()
    logger.info(".env file %s", "found and loaded" if env_loaded else "not found, using system environment variables")

    if missing_vars := validate_required_env_vars():
        error_msg = f"Missing required environment variables: {', '.join(missing_vars)}"
        logger.critical(error_msg)
        raise ValueError(error_msg)

    try:
        validated_path = _validate_config_path(config_path)
        config_data = _read_and_parse_config(validated_path)

        logger.debug(
            "[CONFIG] Raw config (before env var resolution):\n%s",
            yaml.dump(config_data),
        )
        config_data = resolve_env_vars(config_data)
        logger.debug(
            "[CONFIG] Resolved config (after env var resolution):\n%s",
            yaml.dump(config_data),
        )

        config_data = _validate_config_data_type(config_data)

        # Validate with Pydantic
        try:
            config_model = AppConfig(**config_data)
            # Convert Pydantic model to dict using modern v2 method
            # Use model_dump() for Pydantic v2, with intelligent fallback
            if hasattr(config_model, "model_dump") and callable(config_model.model_dump):
                validated_config: dict[str, Any] = config_model.model_dump()
            else:
                # Fallback for edge cases - use model_fields for field extraction
                fields = getattr(config_model, "model_fields", {})
                validated_config = {field_name: getattr(config_model, field_name) for field_name in fields}
        except ValidationError as e:
            error_details = format_pydantic_errors(e)
            msg = f"Configuration validation failed:\n{error_details}"
            raise ValueError(msg) from e

        logger.info("Configuration successfully loaded and validated.")
        return validated_config

    except (FileNotFoundError, PermissionError, ValueError, yaml.YAMLError) as e:
        logger.critical("Configuration loading failed: %s", e)
        raise
    except Exception as e:
        logger.critical("An unexpected error occurred during config loading: %s", e, exc_info=True)
        msg = f"An unexpected error occurred during config loading: {e}"
        raise RuntimeError(msg) from e


def format_pydantic_errors(error: ValidationError) -> str:
    """Format Pydantic validation errors into a readable string.

    Args:
        error: Pydantic ValidationError instance.

    Returns:
        str: Formatted error message string.

    """
    error_messages: list[str] = []

    for err in error.errors():
        loc_path = ".".join(str(loc) for loc in err["loc"])
        msg = err["msg"]
        error_type = err["type"]

        if error_type == "missing":
            error_messages.append(f"{loc_path}: Missing required field")
        elif error_type in ("type_error", "value_error", "assertion_error"):
            error_messages.append(f"{loc_path}: {msg}")
        else:
            error_messages.append(f"{loc_path}: {msg} (type: {error_type})")

    return "\n".join(error_messages)


def validate_api_auth(api_auth: dict[str, Any]) -> None:
    """Validate API authentication configuration.

    Args:
        api_auth: Dictionary containing API authentication settings.

    """
    if not api_auth:
        logger.warning("'api_auth' section is missing in year_retrieval config")
        return

    missing_fields: list[str] = []
    if not api_auth.get("discogs_token"):
        missing_fields.append("DISCOGS_TOKEN")
    if not api_auth.get("contact_email"):
        missing_fields.append("CONTACT_EMAIL")
    if api_auth.get("use_lastfm") and not api_auth.get("lastfm_api_key"):
        missing_fields.append("LASTFM_API_KEY (required when use_lastfm is enabled)")

    for field in missing_fields:
        logger.warning("%s is not set in .env file", field)
    if missing_fields:
        msg = f"API authentication config incomplete: {', '.join(missing_fields)}"
        raise ValueError(msg)


def load_config_with_fallback(config_path: str, fallback_paths: list[str] | None = None) -> dict[str, Any]:
    """Load configuration with fallback mechanisms and retry logic.

    This function implements TASK-007 recovery mechanisms by providing
    automatic fallback to backup configuration files and retry logic
    for transient failures.

    Args:
        config_path: Primary configuration file path.
        fallback_paths: List of fallback configuration file paths.

    Returns:
        Dictionary containing the validated configuration.

    Raises:
        ConfigurationError: If no configuration can be loaded from any source.
        ValueError: If configuration validation fails.

    Example:
        >>> config = load_config_with_fallback("my-config.yaml", ["my-config.backup.yaml", "default-config.yaml"])

    """
    retry_handler = ConfigurationRetryHandler(logger)

    try:
        return retry_handler.load_config_with_fallback(config_path, fallback_paths)
    except ConfigurationError as e:
        logger.critical("Configuration loading failed with all fallbacks: %s", e)
        raise
    except Exception as e:
        logger.critical("Unexpected error during configuration loading: %s", e, exc_info=True)
        msg = f"Unexpected error during configuration loading: {e}"
        raise RuntimeError(msg) from e


def create_fallback_config_paths(primary_path: str) -> list[str]:
    """Generate common fallback configuration paths.

    Args:
        primary_path: Primary configuration file path.

    Returns:
        List of potential fallback configuration paths.

    """
    primary_pathlib = pathlib.Path(primary_path)
    # Add backup file in the same directory
    backup_path = primary_pathlib.with_suffix(f".backup{primary_pathlib.suffix}")
    fallback_paths: list[str] = [str(backup_path)]
    # Add config in user's home config directory
    home_config = pathlib.Path.home() / ".config" / "genres-autoupdater" / primary_pathlib.name
    fallback_paths.append(str(home_config))

    # Add default config in the same directory
    default_path = primary_pathlib.with_name(f"default-{primary_pathlib.name}")
    fallback_paths.append(str(default_path))

    return fallback_paths
