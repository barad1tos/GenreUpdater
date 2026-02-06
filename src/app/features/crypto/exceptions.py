"""Cryptography-specific exceptions."""

from __future__ import annotations

from typing import Any


class CryptographyError(Exception):
    """Base exception for cryptography operations."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        """Initialize CryptographyError.

        Args:
            message: Error description
            details: Additional error context

        """
        super().__init__(message)
        self.message = message
        self.details = details or {}


class KeyGenerationError(CryptographyError):
    """Exception raised when key generation fails."""


class DecryptionError(CryptographyError):
    """Exception raised when decryption fails."""


class EncryptionError(CryptographyError):
    """Exception raised when encryption fails."""


class InvalidTokenError(CryptographyError):
    """Exception raised when token format is invalid."""


class InvalidKeyError(CryptographyError):
    """Exception raised when encryption key is invalid."""
