"""Secure configuration management for cryptography operations.

This module provides the SecureConfig class for secure token and configuration
management using Fernet encryption. It serves as the main interface for all
cryptography operations in the application.

Centralized in src/typings/cryptography/ for better organization alongside
other cryptography-related type definitions and implementations.
"""

from __future__ import annotations

from typing import Any, NoReturn, TYPE_CHECKING

# Import cryptography manager
from app.features.crypto import (
    CryptographyError,
    CryptographyManager,
    DecryptionError,
    EncryptionError,
    InvalidTokenError,
)

if TYPE_CHECKING:
    import logging


class SecurityConfigError(Exception):
    """Exception raised for security configuration errors in SecureConfig operations."""


class SecureConfig:
    """Secure configuration management using Fernet encryption.

    This class provides the main interface for cryptography operations in the
    application, including token encryption/decryption, key management, and
    security status monitoring.

    Centralized in the cryptography module for better organization and
    maintainability of all security-related functionality.
    """

    def __init__(self, logger: logging.Logger, key_file_path: str = "encryption.key") -> None:
        """Initialize SecureConfig with cryptography manager.

        Args:
            logger: Logger instance for error reporting and debugging
            key_file_path: Path to encryption key file for secure storage

        """
        self.logger = logger
        self.key_file_path = key_file_path
        self.crypto_manager = CryptographyManager(logger, key_file_path)

    def is_token_encrypted(self, token: str) -> bool:
        """Check if a token is encrypted using Fernet format detection.

        Args:
            token: Token to check for encryption status

        Returns:
            True if token appears to be Fernet-encrypted, False otherwise

        """
        try:
            return self.crypto_manager.is_token_encrypted(token)
        except (CryptographyError, ValueError, TypeError) as e:
            self.logger.exception(f"Error checking token encryption status: {e!s}")
            return False

    def decrypt_token(self, token: str, key: str) -> str:
        """Decrypt a token using Fernet symmetric encryption.

        Provides secure decryption of tokens stored in the application's
        configuration or database, using the centralized cryptography module.

        Args:
            token: Encrypted token to decrypt
            key: Decryption key (base64-encoded Fernet key or password)

        Returns:
            Decrypted plain text token

        Raises:
            SecurityConfigError: If decryption fails due to invalid token or key

        """
        try:
            return self.crypto_manager.decrypt_token(token, key)
        except (DecryptionError, InvalidTokenError) as e:
            self._handle_crypto_error("Token decryption failed: ", e)
        except CryptographyError as e:
            self._handle_crypto_error("Cryptography operation failed: ", e)

    def encrypt_token(self, token: str, key: str) -> str:
        """Encrypt a token using Fernet symmetric encryption.

        Provides secure encryption of sensitive tokens for storage in the
        application's configuration or database, using the centralized
        cryptography module for consistent security practices.

        Args:
            token: Plain text token to encrypt
            key: Encryption key (base64-encoded Fernet key or password)

        Returns:
            Base64-encoded encrypted token suitable for secure storage

        Raises:
            SecurityConfigError: If encryption fails due to invalid input or key

        """
        try:
            return self.crypto_manager.encrypt_token(token, key)
        except EncryptionError as e:
            self._handle_crypto_error("Token encryption failed: ", e)
        except CryptographyError as e:
            self._handle_crypto_error("Cryptography operation failed: ", e)

    def _handle_crypto_error(self, message_prefix: str, error: CryptographyError) -> NoReturn:
        """Handle cryptography errors with consistent logging and exception raising.

        Args:
            message_prefix: Descriptive prefix for the error message
            error: The cryptography error that occurred

        Raises:
            SecurityConfigError: Always raised with the formatted error message

        """
        result = f"{message_prefix}{error!s}"
        self.logger.exception(result)
        raise SecurityConfigError(result) from error

    def rotate_key(self, new_passphrase: str | None = None) -> None:
        """Rotate the encryption key to a new one for enhanced security.

        Performs key rotation using the centralized cryptography module,
        ensuring all future encryption operations use the new key while
        maintaining the ability to decrypt existing tokens.

        Args:
            new_passphrase: Optional new passphrase for key derivation, if None
                            a random key will be generated

        Raises:
            SecurityConfigError: If key rotation fails

        """
        try:
            self.crypto_manager.rotate_key(new_passphrase)
            self.logger.info("Encryption key rotated successfully")
        except CryptographyError as e:
            error_message = f"Key rotation failed: {e.message}"
            self.logger.exception(error_message)
            raise SecurityConfigError(error_message) from e

    def get_secure_config_status(self) -> dict[str, Any]:
        """Get comprehensive security configuration status.

        Provides detailed status information about the current cryptography
        configuration, including key management status and security metrics
        from the centralized cryptography module.

        Returns:
            Dictionary containing security configuration status with keys:
            - key_file_path: Path to encryption key file
            - encryption_initialized: Whether encryption system is ready
            - password_configured: Whether password-based keys are configured
            - key_file_exists: Whether key file exists on filesystem
            - key_file_permissions: File permissions of key file (if exists)

        """
        try:
            return self.crypto_manager.get_secure_config_status()
        except (CryptographyError, OSError, ValueError) as e:
            self.logger.exception(f"Error getting security status: {e!s}")
            return {
                "key_file_path": self.key_file_path,
                "encryption_initialized": False,
                "password_configured": False,
                "error": str(e),
            }
