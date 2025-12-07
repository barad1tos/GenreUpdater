"""Configuration management for Music Genre Updater."""

import os
from pathlib import Path
from typing import Any, TypeVar, overload

from core.core_config import load_config as load_yaml_config

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[assignment]

T = TypeVar("T")

# Prioritize standard config name, fallback to legacy name for compatibility
DEFAULT_CONFIG_FILES = ["config.yaml", "my-config.yaml"]


class Config:
    """Configuration manager for the application."""

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
        self._config: dict[str, Any] = {}
        self._loaded = False

    def _resolve_config_path(self) -> str:
        """Resolve the configuration file path to an absolute path.

        Returns:
            Absolute path to the configuration file

        """
        load_path = Path(os.path.expandvars(self.config_path)).expanduser()
        try:
            return str(load_path.resolve())
        except (OSError, ValueError):
            # If resolve fails, use absolute path as fallback
            return str(load_path.absolute())

    def load(self) -> dict[str, Any]:
        """Load configuration from the file.

        Returns:
            Configuration dictionary

        """
        if not self._loaded:
            load_path = Path(os.path.expandvars(self.config_path)).expanduser()
            try:
                self._config = load_yaml_config(str(load_path))
            except Exception as e:
                msg = f"Failed to load configuration from '{load_path}': {e}"
                raise RuntimeError(msg) from e
            self._resolved_path = self._resolve_config_path()
            self._loaded = True
        return self._config

    @property
    def resolved_path(self) -> str:
        """Get the resolved absolute path to the configuration file.

        Returns:
            Absolute path to the configuration file

        """
        if self._resolved_path is None:
            # Ensure config is loaded first
            self.load()

        # If still None after load (should not happen), resolve manually as fallback
        if self._resolved_path is None:
            return self._resolve_config_path()

        return self._resolved_path

    @overload
    def get(self, key: str, default: None = None) -> Any:
        """Get configuration value with None default."""

    @overload
    def get(self, key: str, default: T) -> Any | T:
        """Get configuration value with typed default."""

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value.

        Args:
            key: Configuration key (supports dot notation)
            default: Default value if key not found

        Returns:
            Configuration value or default

        """
        if not self._loaded:
            self.load()

        # Support dot notation
        keys = key.split(".")
        current: Any = self._config

        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return default
        return current

    def get_path(self, key: str, default: str = "") -> Path:
        """Get configuration path value.

        Args:
            key: Configuration key for path
            default: Default path if not found

        Returns:
            Path object

        """
        if path_str := self.get(key, default):
            return Path(os.path.expandvars(path_str)).expanduser()
        return Path(default)

    def get_list(self, key: str, default: list[Any] | None = None) -> list[Any]:
        """Get configuration list value.

        Args:
            key: Configuration key
            default: Default list if not found

        Returns:
            List value

        """
        result: list[Any] = default if default is not None else []
        value: Any = self.get(key, result)

        return list(value) if isinstance(value, list) else result

    def get_dict(self, key: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
        """Get configuration dict value.

        Args:
            key: Configuration key
            default: Default dict if not found

        Returns:
            Dict value

        """
        result: dict[str, Any] = default if default is not None else {}
        value: Any = self.get(key, result)

        return dict(value) if isinstance(value, dict) else result

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Get configuration boolean value.

        Args:
            key: Configuration key
            default: Default boolean if not found

        Returns:
            Boolean value

        """
        value = self.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            true_values = {"true", "yes", "1", "on"}
            false_values = {"false", "no", "0", "off"}
            val = value.strip().lower()
            if val in true_values:
                return True
            if val in false_values:
                return False
        return bool(value)

    def get_int(self, key: str, default: int = 0) -> int:
        """Get configuration integer value.

        Args:
            key: Configuration key
            default: Default integer if not found

        Returns:
            Integer value

        """
        value = self.get(key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        """Get configuration float value.

        Args:
            key: Configuration key
            default: Default float if not found

        Returns:
            Float value

        """
        value = self.get(key, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
