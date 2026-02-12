"""Configuration management for Music Genre Updater."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from core.core_config import load_config as load_yaml_config

if TYPE_CHECKING:
    from core.models.track_models import AppConfig

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[assignment]

# User config takes precedence over template (my-config.yaml is gitignored)
DEFAULT_CONFIG_FILES: list[str] = ["my-config.yaml", "config.yaml"]


class Config:
    """Configuration manager for the application.

    Handles config file discovery (env var, defaults) and delegates
    YAML loading + Pydantic validation to ``core.core_config.load_config``.
    """

    def __init__(self, config_path: str | None = None) -> None:
        """Initialize configuration.

        Args:
            config_path: Path to the configuration file (use default if None)

        """
        if config_path is None:
            # Load .env file if not already loaded
            if load_dotenv is not None:
                load_dotenv()

            # Try environment variable first, then try each default config file
            config_path = os.getenv("CONFIG_PATH")
        if config_path is None:
            for default_file in DEFAULT_CONFIG_FILES:
                if Path(default_file).exists():
                    config_path = default_file
                    break
            else:
                # Fail fast - no config file found
                msg = (
                    f"No configuration file found. Checked CONFIG_PATH env var and files: {DEFAULT_CONFIG_FILES}. "
                    "Please create a config.yaml file or set CONFIG_PATH environment variable."
                )
                raise FileNotFoundError(msg)

        # config_path is guaranteed to be not None at this point
        self.config_path = config_path
        self._resolved_path: str | None = None
        self._app_config: AppConfig | None = None

    def _resolve_config_path(self) -> str:
        """Resolve the configuration file path to an absolute path."""
        load_path = Path(os.path.expandvars(self.config_path)).expanduser()
        try:
            return str(load_path.resolve())
        except (OSError, ValueError):
            # If resolve fails, use absolute path as fallback
            return str(load_path.absolute())

    def load(self) -> AppConfig:
        """Load configuration from the file.

        Returns:
            Validated AppConfig Pydantic model.

        """
        if self._app_config is None:
            load_path = Path(os.path.expandvars(self.config_path)).expanduser()
            try:
                app_config = load_yaml_config(str(load_path))
            except Exception as e:
                msg = f"Failed to load configuration from '{load_path}': {e}"
                raise RuntimeError(msg) from e
            self._app_config = app_config
            self._resolved_path = self._resolve_config_path()

        return self._app_config

    @property
    def resolved_path(self) -> str:
        """Get the resolved absolute path to the configuration file."""
        if self._resolved_path is None:
            # Ensure config is loaded first
            self.load()

        # If still None after load (should not happen), resolve manually as fallback
        if self._resolved_path is None:
            return self._resolve_config_path()

        return self._resolved_path
