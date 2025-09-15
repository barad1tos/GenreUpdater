"""Configuration management for Music Genre Updater."""

import os
from pathlib import Path
from typing import Any, TypeVar, overload, cast

from src.utils.core.config import load_config as load_yaml_config

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[assignment]

T = TypeVar("T")


# noinspection PyMissingOrEmptyDocstring
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
            config_path = os.getenv("CONFIG_PATH", "config.yaml")
        self.config_path = config_path
        self._config: dict[str, Any] = {}
        self._loaded = False

    def load(self) -> dict[str, Any]:
        """Load configuration from the file.

        Returns:
            Configuration dictionary

        """
        if not self._loaded:
            self._config = load_yaml_config(self.config_path)
            self._loaded = True
        return self._config

    @overload
    def get(self, key: str, default: None = None) -> Any: ...

    @overload
    def get(self, key: str, default: T) -> Any | T: ...

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
                current = cast(Any, current[k])
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
        result: list[Any] = default or []
        value: Any = self.get(key, result)

        return list(cast(list[Any], value)) if isinstance(value, list) else result

    def get_dict(self, key: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
        """Get configuration dict value.

        Args:
            key: Configuration key
            default: Default dict if not found

        Returns:
            Dict value

        """
        result: dict[str, Any] = default or {}
        value: Any = self.get(key, result)

        return dict(cast(dict[str, Any], value)) if isinstance(value, dict) else result

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
            return value.lower() in ("true", "yes", "1", "on")
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
