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

from core.models.track_models import AppConfig

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


class ConfigurationError(Exception):
    """Raised when configuration loading or parsing fails."""

    def __init__(self, message: str, config_path: str | None = None) -> None:
        """Initialize the configuration error.

        Args:
            message: Error description
            config_path: Path to the config file that caused the error

        """
        super().__init__(message)
        self.config_path = config_path


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
    if isinstance(config, str):
        # Handle pure ${VAR} syntax - return empty string if var not set
        if config.startswith("${") and config.endswith("}"):
            var_name = config[2:-1]
            return os.getenv(var_name, "")
        # Handle path expansion patterns: ~, $VAR, ${VAR}
        if "~" in config or "$" in config:
            result = config
            # First, expand environment variables ($VAR and ${VAR})
            if "$" in result:
                result = os.path.expandvars(result)
            # Then, expand user home directory (~) using getpwuid (works without HOME env var)
            # This also handles cases where ${HOME} wasn't expanded due to missing env var
            if "~" in result or result.startswith("${HOME}"):
                result = str(pathlib.Path(result).expanduser())
            return result
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
    # Use relative_to() for secure path containment check (prevents bypass via /foo/bar2 matching /foo/bar)
    is_allowed = False
    for allowed_dir in allowed_dirs:
        try:
            resolved_path.relative_to(allowed_dir)
            is_allowed = True
            break
        except ValueError:
            continue

    if not is_allowed:
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
    """Read and parse the YAML config file with size validation.

    Reads the config file content and parses it as YAML. Enforces a maximum
    file size to prevent DoS via oversized config files.

    Args:
        path: Validated pathlib.Path to the configuration file.

    Returns:
        Parsed YAML content (typically a dict for valid configs).

    Raises:
        ValueError: If config file exceeds maximum size (1MB).
        yaml.YAMLError: If YAML parsing fails.
        OSError: If file cannot be read.

    """
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


def load_config(config_path: str) -> AppConfig:
    """Load the configuration from a YAML file, resolve environment variables, and validate it.

    Args:
        config_path: Path to the configuration YAML file.

    Returns:
        Validated AppConfig Pydantic model with resolved env vars.

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
        except ValidationError as e:
            error_details = format_pydantic_errors(e)
            msg = f"Configuration validation failed:\n{error_details}"
            raise ValueError(msg) from e

        logger.info("Configuration successfully loaded and validated.")
        return config_model

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

    Raises:
        ValueError: If api_auth section is missing or incomplete

    """
    if not api_auth:
        msg = "'api_auth' section is missing in year_retrieval config"
        raise ValueError(msg)

    missing_fields: list[str] = []
    if not api_auth.get("discogs_token"):
        missing_fields.append("DISCOGS_TOKEN")
    if not api_auth.get("contact_email"):
        missing_fields.append("CONTACT_EMAIL")

    for field in missing_fields:
        logger.warning("%s is not set in environment", field)
    if missing_fields:
        msg = f"API authentication config incomplete: {', '.join(missing_fields)}"
        raise ValueError(msg)
