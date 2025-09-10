"""Fernet-based encryption implementation for secure token handling."""

import base64
import binascii
import contextlib
import hashlib
import logging
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .exceptions import (
    DecryptionError,
    EncryptionError,
    InvalidKeyError,
    InvalidTokenError,
    KeyGenerationError,
)

# Constants for Fernet token validation
FERNET_KEY_LENGTH = 44  # Standard Fernet key length in base64 encoding
FERNET_TOKEN_MIN_LENGTH = 57  # Minimum length for valid Fernet tokens
FERNET_TOKEN_MIN_ENCODED_LENGTH = 80  # Minimum length for base64-encoded Fernet tokens
FERNET_VERSION_BYTE = 0x80  # Fernet token version identifier byte


class CryptographyManager:
    """Manages Fernet-based encryption for tokens and configuration data."""

    def __init__(self, logger: logging.Logger, key_file_path: str = "encryption.key") -> None:
        """Initialize CryptographyManager.

        Args:
            logger: Logger instance for error reporting
            key_file_path: Path to encryption key file

        """
        self.logger = logger
        self.key_file_path = Path(key_file_path)
        self._fernet: Fernet | None = None
        self._encryption_key: bytes | None = None

    def _generate_key_from_password(self, password: str) -> bytes:
        """Generate encryption key from password using PBKDF2.

        Uses a fixed salt derived from password for consistency.

        Args:
            password: Password for key derivation

        Returns:
            32-byte encryption key

        Raises:
            KeyGenerationError: If key generation fails

        """
        try:
            # Use a deterministic salt based on password hash for consistency
            salt = hashlib.sha256(password.encode()).digest()[:16]

            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=100000,
            )
            return base64.urlsafe_b64encode(kdf.derive(password.encode()))
        except Exception as e:
            self._raise_key_error("Key generation failed: ", e)
            # This line should never be reached due to the exception above, but helps type checker
            raise  # pragma: no cover

    def _load_or_create_key(self, password: str | None = None) -> bytes:
        """Load existing encryption key or create a new one.

        Args:
            password: Password for key derivation

        Returns:
            Encryption key bytes

        Raises:
            KeyGenerationError: If key operations fail

        """
        try:
            return self._load_existing_or_generate_new_key(password)
        except Exception as e:
            self._raise_key_error("Key management failed: ", e)
            # This line should never be reached due to the exception above, but helps type checker
            raise  # pragma: no cover

    def _load_existing_or_generate_new_key(self, password: str | None) -> bytes:
        """Load existing encryption key or generate a new one.

        Args:
            password: Optional password for key derivation

        Returns:
            Encryption key bytes

        """
        # Try to load existing key
        if self.key_file_path.exists():
            key_data = self.key_file_path.read_bytes()
            self.logger.info("Loaded encryption key from %s", self.key_file_path)
            return key_data

            # Generate new key
        key = self._generate_key_from_password(password) if password else Fernet.generate_key()
        # Save key securely
        self.key_file_path.write_bytes(key)
        self.key_file_path.chmod(0o600)  # Owner read/write only

        self.logger.info("Generated new encryption key at %s", self.key_file_path)
        return key

    def _get_fernet(self, key: str | None = None, password: str | None = None) -> Fernet:
        """Get or create Fernet cipher instance.

        Args:
            key: Base64-encoded encryption key or password for key derivation
            password: Password for key derivation (alternative to key parameter)

        Returns:
            Fernet cipher instance

        Raises:
            InvalidKeyError: If key is invalid

        """
        try:
            if key:
                # Try to determine if the key is base64-encoded or a password
                with contextlib.suppress(Exception):
                    # First, try treating it as a base64-encoded key
                    if len(key) == FERNET_KEY_LENGTH and key.endswith("="):  # Fernet key characteristics
                        key_bytes = base64.urlsafe_b64decode(key.encode())
                        return Fernet(base64.urlsafe_b64encode(key_bytes))
                # If that fails, treat it as a password for key derivation
                derived_key = self._generate_key_from_password(key)
                return Fernet(derived_key)

            if password:
                # Use password for key derivation
                derived_key = self._generate_key_from_password(password)
                return Fernet(derived_key)

            # Use cached Fernet or create new one
            if self._fernet is None:
                if self._encryption_key is None:
                    self._encryption_key = self._load_or_create_key()
                self._fernet = Fernet(self._encryption_key)

            return self._fernet

        except Exception as e:
            error_message = f"Fernet initialization failed: {e!s}"
            self.logger.exception(error_message)
            raise InvalidKeyError(error_message, {"original_error": str(e)}) from e

    @staticmethod
    def is_token_encrypted(token: str) -> bool:
        """Check if a token is encrypted (Fernet format).

        Args:
            token: Token to check

        Returns:
            True if token appears to be Fernet-encrypted

        """
        try:
            if not token:
                return False

            # Check if token looks like base64-encoded Fernet token
            # Fernet tokens are typically 100+ characters and base64-encoded
            if len(token) < FERNET_TOKEN_MIN_ENCODED_LENGTH:
                return False

            # Try to decode the double-base64 encoded Fernet token
            try:
                # First decode the outer base64 layer
                outer_decoded = base64.urlsafe_b64decode(token.encode())
                # Then decode the inner base64 layer (Fernet token)
                inner_decoded = base64.urlsafe_b64decode(outer_decoded)

                # Fernet tokens have a minimum length and start with version byte
                return False if len(inner_decoded) < FERNET_TOKEN_MIN_LENGTH else inner_decoded[0] == FERNET_VERSION_BYTE
            except binascii.Error:
                # If double-base64 fails, try single base64 decode
                try:
                    decoded = base64.urlsafe_b64decode(token.encode())

                    # Fernet tokens have a minimum length and start with version byte
                    return False if len(decoded) < FERNET_TOKEN_MIN_LENGTH else decoded[0] == FERNET_VERSION_BYTE
                except binascii.Error:
                    return False

        except (ValueError, TypeError, AttributeError):
            return False

    def encrypt_token(self, token: str, key: str | None = None, password: str | None = None) -> str:
        """Encrypt a token using Fernet symmetric encryption.

        Args:
            token: Token to encrypt
            key: Optional base64-encoded encryption key
            password: Optional password for key derivation

        Returns:
            Base64-encoded encrypted token

        Raises:
            EncryptionError: If encryption fails

        """
        try:
            if not token:
                error_message = "Token cannot be empty"
                raise EncryptionError(error_message)

            fernet = self._get_fernet(key, password)
            encrypted_bytes = fernet.encrypt(token.encode())
            encrypted_token = base64.urlsafe_b64encode(encrypted_bytes).decode()

            self.logger.debug("Token encrypted successfully")
            return encrypted_token

        except (InvalidKeyError, KeyGenerationError):
            raise  # Re-raise key-specific errors
        except Exception as e:
            error_message = f"Token encryption failed: {e!s}"
            self.logger.exception(error_message)
            raise EncryptionError(error_message, {"original_error": str(e)}) from e

    def decrypt_token(self, encrypted_token: str, key: str | None = None, password: str | None = None) -> str:
        """Decrypt a token using Fernet symmetric encryption.

        Args:
            encrypted_token: Base64-encoded encrypted token
            key: Optional base64-encoded encryption key
            password: Optional password for key derivation

        Returns:
            Decrypted token

        Raises:
            DecryptionError: If decryption fails
            InvalidTokenError: If token format is invalid

        """
        try:
            if not encrypted_token:
                error_message = "Encrypted token cannot be empty"
                raise InvalidTokenError(error_message)

            # Decode the base64-encoded token
            try:
                encrypted_bytes = base64.urlsafe_b64decode(encrypted_token.encode())
            except Exception as e:
                error_message = "Invalid token format - not valid base64"
                raise InvalidTokenError(error_message) from e

            fernet = self._get_fernet(key, password)
            decrypted_bytes = fernet.decrypt(encrypted_bytes)
            decrypted_token: str = decrypted_bytes.decode()

            self.logger.debug("Token decrypted successfully")
            return decrypted_token

        except InvalidToken as e:
            error_message = "Token decryption failed - invalid token or key"
            self.logger.exception(error_message)
            raise DecryptionError(error_message) from e
        except (InvalidKeyError, KeyGenerationError, InvalidTokenError):
            raise  # Re-raise specific errors
        except Exception as e:
            error_message = f"Token decryption failed: {e!s}"
            self.logger.exception(error_message)
            raise DecryptionError(error_message, {"original_error": str(e)}) from e

    def rotate_key(self, new_password: str | None = None, backup_old_key: bool = True) -> None:
        """Rotate the encryption key to a new one.

        Args:
            new_password: Password for new key derivation
            backup_old_key: Whether to backup the old key

        Raises:
            KeyGenerationError: If key rotation fails

        """
        try:
            # Backup old key if requested
            if backup_old_key and self.key_file_path.exists():
                backup_path = self.key_file_path.with_suffix(".key.backup")
                backup_path.write_bytes(self.key_file_path.read_bytes())
                self.logger.info("Backed up old key to %s", backup_path)

            # Generate new key
            new_key = self._generate_key_from_password(new_password) if new_password else Fernet.generate_key()

            # Save new key
            self.key_file_path.write_bytes(new_key)
            self.key_file_path.chmod(0o600)

            # Reset cached instances
            self._fernet = None
            self._encryption_key = new_key

            self.logger.info("Encryption key rotated successfully")

        except (OSError, ValueError, TypeError) as e:
            self._raise_key_error("Key rotation failed: ", e)

    def _raise_key_error(self, error_prefix: str, original_error: Exception) -> None:
        """Raise a KeyGenerationError with consistent logging and error context.

        Args:
            error_prefix: Descriptive error prefix message
            original_error: The original exception that caused the error

        Raises:
            KeyGenerationError: Always raised with context

        """
        error_message = f"{error_prefix}{original_error!s}"
        self.logger.exception(error_message)
        raise KeyGenerationError(error_message, {"original_error": str(original_error)}) from original_error

    def get_secure_config_status(self) -> dict[str, Any]:
        """Get security configuration status.

        Returns:
            Status dictionary with current configuration

        """
        return {
            "key_file_path": str(self.key_file_path),
            "encryption_initialized": self._fernet is not None,
            "password_configured": self._encryption_key is not None,
            "key_file_exists": self.key_file_path.exists(),
            "key_file_permissions": (oct(self.key_file_path.stat().st_mode)[-3:] if self.key_file_path.exists() else None),
        }
