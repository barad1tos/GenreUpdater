"""Tests for SecureConfig - secure configuration management."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from src.app.features.crypto.exceptions import (
    CryptographyError,
    DecryptionError,
    EncryptionError,
    InvalidTokenError,
)
from src.types.cryptography.secure_config import SecureConfig, SecurityConfigError


@pytest.fixture
def logger() -> logging.Logger:
    """Create a test logger."""
    return logging.getLogger("test.secure_config")


@pytest.fixture
def temp_key_file(tmp_path: Path) -> Path:
    """Create a temporary key file path."""
    return tmp_path / "test_encryption.key"


@pytest.fixture
def secure_config(logger: logging.Logger, temp_key_file: Path) -> SecureConfig:
    """Create a SecureConfig instance."""
    return SecureConfig(logger, str(temp_key_file))


@pytest.fixture
def secure_config_with_key(logger: logging.Logger, temp_key_file: Path) -> SecureConfig:
    """Create a SecureConfig with initialized key."""
    key = Fernet.generate_key()
    temp_key_file.write_bytes(key)
    temp_key_file.chmod(0o600)
    return SecureConfig(logger, str(temp_key_file))


class TestSecureConfigInit:
    """Tests for SecureConfig initialization."""

    def test_init_stores_logger(self, logger: logging.Logger, temp_key_file: Path) -> None:
        """Should store logger correctly."""
        config = SecureConfig(logger, str(temp_key_file))
        assert config.logger is logger

    def test_init_stores_key_file_path(self, logger: logging.Logger, temp_key_file: Path) -> None:
        """Should store key file path correctly."""
        config = SecureConfig(logger, str(temp_key_file))
        assert config.key_file_path == str(temp_key_file)

    def test_init_creates_crypto_manager(self, logger: logging.Logger, temp_key_file: Path) -> None:
        """Should create CryptographyManager instance."""
        config = SecureConfig(logger, str(temp_key_file))
        assert config.crypto_manager is not None

    def test_init_default_key_file_path(self, logger: logging.Logger) -> None:
        """Should use default key file path when not specified."""
        config = SecureConfig(logger)
        assert config.key_file_path == "encryption.key"


class TestIsTokenEncrypted:
    """Tests for is_token_encrypted method."""

    def test_returns_true_for_encrypted_token(self, secure_config_with_key: SecureConfig) -> None:
        """Should return True for encrypted token."""
        key = secure_config_with_key.crypto_manager._load_or_create_key()
        # Use encrypt_token which double-base64 encodes
        encrypted = secure_config_with_key.encrypt_token("secret", key.decode())

        result = secure_config_with_key.is_token_encrypted(encrypted)

        assert result is True

    def test_returns_false_for_plain_text(self, secure_config: SecureConfig) -> None:
        """Should return False for plain text."""
        result = secure_config.is_token_encrypted("plain_text_token")
        assert result is False

    def test_returns_false_on_error(self, secure_config: SecureConfig) -> None:
        """Should return False when error occurs."""
        with patch.object(secure_config.crypto_manager, "is_token_encrypted", side_effect=CryptographyError("Test error")):
            result = secure_config.is_token_encrypted("any_token")
        assert result is False

    def test_returns_false_on_value_error(self, secure_config: SecureConfig) -> None:
        """Should return False when ValueError occurs."""
        with patch.object(secure_config.crypto_manager, "is_token_encrypted", side_effect=ValueError("Invalid")):
            result = secure_config.is_token_encrypted("any_token")
        assert result is False

    def test_returns_false_on_type_error(self, secure_config: SecureConfig) -> None:
        """Should return False when TypeError occurs."""
        with patch.object(secure_config.crypto_manager, "is_token_encrypted", side_effect=TypeError("Bad type")):
            result = secure_config.is_token_encrypted("any_token")
        assert result is False


class TestDecryptToken:
    """Tests for decrypt_token method."""

    def test_decrypts_token_successfully(self, secure_config_with_key: SecureConfig) -> None:
        """Should decrypt token successfully."""
        key = secure_config_with_key.crypto_manager._load_or_create_key()
        # Use encrypt_token which double-base64 encodes (matching decrypt_token expectation)
        encrypted = secure_config_with_key.encrypt_token("my_secret", key.decode())

        result = secure_config_with_key.decrypt_token(encrypted, key.decode())

        assert result == "my_secret"

    def test_raises_on_decryption_error(self, secure_config: SecureConfig) -> None:
        """Should raise SecurityConfigError on DecryptionError."""
        with (
            patch.object(secure_config.crypto_manager, "decrypt_token", side_effect=DecryptionError("Decryption failed")),
            pytest.raises(SecurityConfigError, match="Token decryption failed"),
        ):
            secure_config.decrypt_token("invalid_token", "any_key")

    def test_raises_on_invalid_token_error(self, secure_config: SecureConfig) -> None:
        """Should raise SecurityConfigError on InvalidTokenError."""
        with (
            patch.object(secure_config.crypto_manager, "decrypt_token", side_effect=InvalidTokenError("Invalid token")),
            pytest.raises(SecurityConfigError, match="Token decryption failed"),
        ):
            secure_config.decrypt_token("bad_token", "any_key")

    def test_raises_on_cryptography_error(self, secure_config: SecureConfig) -> None:
        """Should raise SecurityConfigError on CryptographyError."""
        with (
            patch.object(secure_config.crypto_manager, "decrypt_token", side_effect=CryptographyError("Crypto failed")),
            pytest.raises(SecurityConfigError, match="Cryptography operation failed"),
        ):
            secure_config.decrypt_token("token", "key")


class TestEncryptToken:
    """Tests for encrypt_token method."""

    def test_encrypts_token_successfully(self, secure_config_with_key: SecureConfig) -> None:
        """Should encrypt token successfully."""
        key = secure_config_with_key.crypto_manager._load_or_create_key()

        result = secure_config_with_key.encrypt_token("my_secret", key.decode())

        assert result != "my_secret"
        assert len(result) > 0

    def test_raises_on_encryption_error(self, secure_config: SecureConfig) -> None:
        """Should raise SecurityConfigError on EncryptionError."""
        with (
            patch.object(secure_config.crypto_manager, "encrypt_token", side_effect=EncryptionError("Encryption failed")),
            pytest.raises(SecurityConfigError, match="Token encryption failed"),
        ):
            secure_config.encrypt_token("secret", "key")

    def test_raises_on_cryptography_error(self, secure_config: SecureConfig) -> None:
        """Should raise SecurityConfigError on CryptographyError."""
        with (
            patch.object(secure_config.crypto_manager, "encrypt_token", side_effect=CryptographyError("Crypto failed")),
            pytest.raises(SecurityConfigError, match="Cryptography operation failed"),
        ):
            secure_config.encrypt_token("secret", "key")


class TestHandleCryptoError:
    """Tests for _handle_crypto_error method."""

    def test_logs_error(self, secure_config: SecureConfig, caplog: pytest.LogCaptureFixture) -> None:
        """Should log error message."""
        error = CryptographyError("Test error message")

        with (
            caplog.at_level(logging.ERROR),
            pytest.raises(SecurityConfigError),
        ):
            secure_config._handle_crypto_error("Prefix: ", error)

        assert "Prefix: Test error message" in caplog.text

    def test_raises_security_config_error(self, secure_config: SecureConfig) -> None:
        """Should raise SecurityConfigError with formatted message."""
        error = CryptographyError("Original error")

        with pytest.raises(SecurityConfigError, match="Prefix: Original error"):
            secure_config._handle_crypto_error("Prefix: ", error)

    def test_chains_original_error(self, secure_config: SecureConfig) -> None:
        """Should chain original error as cause."""
        original = CryptographyError("Root cause")

        with pytest.raises(SecurityConfigError) as exc_info:
            secure_config._handle_crypto_error("Msg: ", original)

        assert exc_info.value.__cause__ is original


class TestRotateKey:
    """Tests for rotate_key method."""

    def test_rotates_key_successfully(self, secure_config_with_key: SecureConfig, caplog: pytest.LogCaptureFixture) -> None:
        """Should rotate key successfully."""
        with (
            patch.object(secure_config_with_key.crypto_manager, "rotate_key") as mock_rotate,
            caplog.at_level(logging.INFO),
        ):
            secure_config_with_key.rotate_key("new_password")

        mock_rotate.assert_called_once_with("new_password")
        assert "Encryption key rotated successfully" in caplog.text

    def test_rotates_key_without_password(self, secure_config_with_key: SecureConfig) -> None:
        """Should rotate key without password (generates random)."""
        with patch.object(secure_config_with_key.crypto_manager, "rotate_key") as mock_rotate:
            secure_config_with_key.rotate_key()

        mock_rotate.assert_called_once_with(None)

    def test_raises_on_rotation_failure(self, secure_config: SecureConfig) -> None:
        """Should raise SecurityConfigError on rotation failure."""
        error = CryptographyError("Rotation failed")

        with (
            patch.object(secure_config.crypto_manager, "rotate_key", side_effect=error),
            pytest.raises(SecurityConfigError, match="Key rotation failed"),
        ):
            secure_config.rotate_key("password")


class TestGetSecureConfigStatus:
    """Tests for get_secure_config_status method."""

    def test_returns_status_from_crypto_manager(self, secure_config: SecureConfig) -> None:
        """Should return status from crypto manager."""
        expected_status: dict[str, Any] = {
            "key_file_path": "/some/path",
            "encryption_initialized": True,
            "password_configured": False,
        }

        with patch.object(secure_config.crypto_manager, "get_secure_config_status", return_value=expected_status):
            result = secure_config.get_secure_config_status()

        assert result == expected_status

    def test_returns_error_status_on_cryptography_error(self, secure_config: SecureConfig) -> None:
        """Should return error status on CryptographyError."""
        with patch.object(secure_config.crypto_manager, "get_secure_config_status", side_effect=CryptographyError("Status failed")):
            result = secure_config.get_secure_config_status()

        assert result["encryption_initialized"] is False
        assert result["password_configured"] is False
        assert "error" in result

    def test_returns_error_status_on_os_error(self, secure_config: SecureConfig) -> None:
        """Should return error status on OSError."""
        with patch.object(secure_config.crypto_manager, "get_secure_config_status", side_effect=OSError("File error")):
            result = secure_config.get_secure_config_status()

        assert result["encryption_initialized"] is False
        assert "error" in result

    def test_returns_error_status_on_value_error(self, secure_config: SecureConfig) -> None:
        """Should return error status on ValueError."""
        with patch.object(secure_config.crypto_manager, "get_secure_config_status", side_effect=ValueError("Invalid value")):
            result = secure_config.get_secure_config_status()

        assert result["encryption_initialized"] is False
        assert "Invalid value" in result["error"]


class TestSecurityConfigError:
    """Tests for SecurityConfigError exception."""

    def test_is_exception(self) -> None:
        """Should be an Exception."""
        assert issubclass(SecurityConfigError, Exception)

    def test_can_be_raised(self) -> None:
        """Should be raisable with message."""
        with pytest.raises(SecurityConfigError, match="Test message"):
            raise SecurityConfigError("Test message")
