"""Tests for CryptographyManager - Fernet-based encryption."""

import base64
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from app.features.crypto.encryption import (
    FERNET_KEY_LENGTH,
    FERNET_TOKEN_MIN_ENCODED_LENGTH,
    CryptographyManager,
)
from app.features.crypto.exceptions import (
    DecryptionError,
    EncryptionError,
    InvalidKeyError,
    InvalidTokenError,
    KeyGenerationError,
)


# Test data - intentionally hardcoded values for encryption tests
SAMPLE_PLAINTEXT = "my_secret_token"
SAMPLE_PHRASE = "my_password"
SAMPLE_STRONG_PHRASE = "strong_password"
SAMPLE_WRONG_PHRASE = "wrong_password"
SAMPLE_NEW_PHRASE = "new_password"
SAMPLE_API_VALUE = "secret_api_key_12345"
SAMPLE_UNICODE = "秘密のトークン"  # Japanese "secret token"
SAMPLE_SPECIAL_CHARS = "!@#$%^&*()_+-=[]{}|;':\",./<>?"


@pytest.fixture
def logger() -> logging.Logger:
    """Create a test logger."""
    return logging.getLogger("test.crypto")


@pytest.fixture
def temp_key_file(tmp_path: Path) -> Path:
    """Create a temporary key file path."""
    return tmp_path / "test_encryption.key"


@pytest.fixture
def crypto_manager(logger: logging.Logger, temp_key_file: Path) -> CryptographyManager:
    """Create a CryptographyManager instance."""
    return CryptographyManager(logger, str(temp_key_file))


@pytest.fixture
def crypto_manager_with_key(logger: logging.Logger, temp_key_file: Path) -> CryptographyManager:
    """Create a CryptographyManager with initialized key."""
    # Create a valid Fernet key file
    key = Fernet.generate_key()
    temp_key_file.write_bytes(key)
    temp_key_file.chmod(0o600)
    return CryptographyManager(logger, str(temp_key_file))


class TestKeyGeneration:
    """Tests for key generation."""

    def test_generate_key_from_passphrase(self, crypto_manager: CryptographyManager) -> None:
        """Test generating key from passphrase."""
        key = crypto_manager._generate_key_from_passphrase("test_passphrase")

        assert isinstance(key, bytes)
        assert len(key) == FERNET_KEY_LENGTH  # Base64-encoded 32 bytes

    def test_same_passphrase_same_key(self, crypto_manager: CryptographyManager) -> None:
        """Test same passphrase produces same key (deterministic)."""
        key1 = crypto_manager._generate_key_from_passphrase("test_passphrase")
        key2 = crypto_manager._generate_key_from_passphrase("test_passphrase")

        assert key1 == key2

    def test_different_passphrases_different_keys(self, crypto_manager: CryptographyManager) -> None:
        """Test different passphrases produce different keys."""
        key1 = crypto_manager._generate_key_from_passphrase("passphrase1")
        key2 = crypto_manager._generate_key_from_passphrase("passphrase2")

        assert key1 != key2


class TestKeyLoadingAndCreation:
    """Tests for key loading and creation."""

    def test_create_new_key_when_file_missing(self, crypto_manager: CryptographyManager, temp_key_file: Path) -> None:
        """Test new key is created when file doesn't exist."""
        assert not temp_key_file.exists()

        key = crypto_manager._load_or_create_key()

        assert temp_key_file.exists()
        assert len(key) == FERNET_KEY_LENGTH

    def test_load_existing_key(self, logger: logging.Logger, temp_key_file: Path) -> None:
        """Test existing key is loaded."""
        # Create a valid key file
        existing_key = Fernet.generate_key()
        temp_key_file.write_bytes(existing_key)

        manager = CryptographyManager(logger, str(temp_key_file))
        loaded_key = manager._load_or_create_key()

        assert loaded_key == existing_key

    def test_key_file_has_secure_permissions(self, crypto_manager: CryptographyManager, temp_key_file: Path) -> None:
        """Test key file is created with secure permissions."""
        crypto_manager._load_or_create_key()

        # Check permissions (owner read/write only)
        mode = temp_key_file.stat().st_mode & 0o777
        assert mode == 0o600

    def test_invalid_key_file_raises_error(self, logger: logging.Logger, temp_key_file: Path) -> None:
        """Test invalid key file raises ValueError."""
        # Write invalid key data
        temp_key_file.write_bytes(b"invalid_key_data")

        manager = CryptographyManager(logger, str(temp_key_file))

        with pytest.raises(KeyGenerationError):
            manager._load_or_create_key()


class TestEncryption:
    """Tests for token encryption."""

    def test_encrypt_token(self, crypto_manager_with_key: CryptographyManager) -> None:
        """Test basic token encryption."""
        encrypted = crypto_manager_with_key.encrypt_token(SAMPLE_PLAINTEXT)

        assert isinstance(encrypted, str)
        assert encrypted != SAMPLE_PLAINTEXT
        assert len(encrypted) >= FERNET_TOKEN_MIN_ENCODED_LENGTH

    def test_encrypt_empty_token_raises_error(self, crypto_manager_with_key: CryptographyManager) -> None:
        """Test encrypting empty token raises error."""
        with pytest.raises(EncryptionError, match="cannot be empty"):
            crypto_manager_with_key.encrypt_token("")

    def test_encrypt_with_passphrase(self, crypto_manager: CryptographyManager) -> None:
        """Test encryption with passphrase."""
        encrypted = crypto_manager.encrypt_token(SAMPLE_PLAINTEXT, passphrase=SAMPLE_PHRASE)

        assert encrypted != SAMPLE_PLAINTEXT

    def test_encrypt_with_key(self, crypto_manager: CryptographyManager) -> None:
        """Test encryption with explicit key."""
        key = Fernet.generate_key().decode()
        encrypted = crypto_manager.encrypt_token(SAMPLE_PLAINTEXT, key=key)

        assert encrypted != SAMPLE_PLAINTEXT


class TestDecryption:
    """Tests for token decryption."""

    def test_decrypt_token(self, crypto_manager_with_key: CryptographyManager) -> None:
        """Test basic token decryption."""
        encrypted = crypto_manager_with_key.encrypt_token(SAMPLE_PLAINTEXT)
        decrypted = crypto_manager_with_key.decrypt_token(encrypted)

        assert decrypted == SAMPLE_PLAINTEXT

    def test_decrypt_empty_token_raises_error(self, crypto_manager_with_key: CryptographyManager) -> None:
        """Test decrypting empty token raises error."""
        with pytest.raises(InvalidTokenError, match="cannot be empty"):
            crypto_manager_with_key.decrypt_token("")

    def test_decrypt_invalid_base64_raises_error(self, crypto_manager_with_key: CryptographyManager) -> None:
        """Test decrypting invalid base64 raises error."""
        with pytest.raises(InvalidTokenError, match="not valid base64"):
            crypto_manager_with_key.decrypt_token("not!valid@base64#")

    def test_decrypt_wrong_key_raises_error(self, crypto_manager_with_key: CryptographyManager, crypto_manager: CryptographyManager) -> None:
        """Test decrypting with wrong key raises error."""
        encrypted = crypto_manager_with_key.encrypt_token(SAMPLE_PLAINTEXT)

        # Try to decrypt with different key
        with pytest.raises(DecryptionError):
            crypto_manager.decrypt_token(encrypted, passphrase=SAMPLE_WRONG_PHRASE)

    def test_roundtrip_with_passphrase(self, crypto_manager: CryptographyManager) -> None:
        """Test encryption/decryption roundtrip with passphrase."""
        encrypted = crypto_manager.encrypt_token(SAMPLE_API_VALUE, passphrase=SAMPLE_STRONG_PHRASE)
        decrypted = crypto_manager.decrypt_token(encrypted, passphrase=SAMPLE_STRONG_PHRASE)

        assert decrypted == SAMPLE_API_VALUE


class TestIsTokenEncrypted:
    """Tests for encrypted token detection."""

    def test_encrypted_token_detected(self, crypto_manager_with_key: CryptographyManager) -> None:
        """Test encrypted token is detected."""
        encrypted = crypto_manager_with_key.encrypt_token("secret")

        assert CryptographyManager.is_token_encrypted(encrypted) is True

    def test_plain_text_not_detected(self) -> None:
        """Test plain text is not detected as encrypted."""
        assert CryptographyManager.is_token_encrypted("plain_text") is False

    def test_short_string_not_detected(self) -> None:
        """Test short string is not detected as encrypted."""
        assert CryptographyManager.is_token_encrypted("abc") is False

    def test_empty_string_not_detected(self) -> None:
        """Test empty string is not detected as encrypted."""
        assert CryptographyManager.is_token_encrypted("") is False

    def test_random_base64_not_detected(self) -> None:
        """Test random base64 is not detected as encrypted."""
        random_b64 = base64.urlsafe_b64encode(b"x" * 100).decode()
        assert CryptographyManager.is_token_encrypted(random_b64) is False


class TestKeyRotation:
    """Tests for key rotation."""

    def test_rotate_key_creates_backup(self, crypto_manager_with_key: CryptographyManager, temp_key_file: Path) -> None:
        """Test key rotation creates backup."""
        # Ensure key file exists
        _ = crypto_manager_with_key._load_or_create_key()

        crypto_manager_with_key.rotate_key()

        # Backup has timestamp: .key.backup.YYYYMMDD_HHMMSS
        backup_files = list(temp_key_file.parent.glob("*.key.backup.*"))
        assert len(backup_files) == 1

    def test_rotate_key_without_backup(self, crypto_manager_with_key: CryptographyManager, temp_key_file: Path) -> None:
        """Test key rotation without backup."""
        # Ensure key file exists
        _ = crypto_manager_with_key._load_or_create_key()

        crypto_manager_with_key.rotate_key(backup_old_key=False)

        backup_path = temp_key_file.with_suffix(".key.backup")
        assert not backup_path.exists()

    def test_rotate_key_with_passphrase(self, crypto_manager_with_key: CryptographyManager) -> None:
        """Test key rotation with passphrase."""
        crypto_manager_with_key.rotate_key(new_passphrase=SAMPLE_NEW_PHRASE)

        # Should be able to encrypt with new passphrase-derived key
        encrypted = crypto_manager_with_key.encrypt_token("test", passphrase=SAMPLE_NEW_PHRASE)
        decrypted = crypto_manager_with_key.decrypt_token(encrypted, passphrase=SAMPLE_NEW_PHRASE)

        assert decrypted == "test"


class TestGetSecureConfigStatus:
    """Tests for security status reporting."""

    def test_status_before_initialization(self, crypto_manager: CryptographyManager) -> None:
        """Test status before key initialization."""
        self._assert_crypto_status(crypto_manager, expected_initialized=False)

    def test_status_after_initialization(self, crypto_manager_with_key: CryptographyManager) -> None:
        """Test status after key initialization."""
        # Initialize encryption
        crypto_manager_with_key.encrypt_token("test")

        status = self._assert_crypto_status(crypto_manager_with_key, expected_initialized=True)
        assert status["key_file_permissions"] == "600"

    @staticmethod
    def _assert_crypto_status(crypto_manager: CryptographyManager, *, expected_initialized: bool) -> dict[str, object]:
        """Assert encryption status and return the status dict."""
        result = crypto_manager.get_secure_config_status()
        assert result["encryption_initialized"] is expected_initialized
        assert result["key_file_exists"] is expected_initialized
        return result


class TestGetFernet:
    """Tests for Fernet instance management."""

    def test_get_fernet_with_valid_key(self, crypto_manager: CryptographyManager) -> None:
        """Test getting Fernet with valid base64 key."""
        key = Fernet.generate_key().decode()
        fernet = crypto_manager._get_fernet(key=key)

        assert isinstance(fernet, Fernet)

    def test_get_fernet_caches_instance(self, crypto_manager_with_key: CryptographyManager) -> None:
        """Test Fernet instance is cached."""
        fernet1 = crypto_manager_with_key._get_fernet()
        fernet2 = crypto_manager_with_key._get_fernet()

        assert fernet1 is fernet2


class TestEdgeCases:
    """Tests for edge cases."""

    def test_encrypt_unicode_token(self, crypto_manager_with_key: CryptographyManager) -> None:
        """Test encrypting unicode token."""
        encrypted = crypto_manager_with_key.encrypt_token(SAMPLE_UNICODE)
        decrypted = crypto_manager_with_key.decrypt_token(encrypted)

        assert decrypted == SAMPLE_UNICODE

    def test_encrypt_long_token(self, crypto_manager_with_key: CryptographyManager) -> None:
        """Test encrypting long token."""
        token = "x" * 10000
        encrypted = crypto_manager_with_key.encrypt_token(token)
        decrypted = crypto_manager_with_key.decrypt_token(encrypted)

        assert decrypted == token

    def test_encrypt_special_characters(self, crypto_manager_with_key: CryptographyManager) -> None:
        """Test encrypting token with special characters."""
        encrypted = crypto_manager_with_key.encrypt_token(SAMPLE_SPECIAL_CHARS)
        decrypted = crypto_manager_with_key.decrypt_token(encrypted)

        assert decrypted == SAMPLE_SPECIAL_CHARS


class TestExceptHandlerCoverage:
    """Tests that exercise specific except handler branches introduced by narrowed exception types."""

    def test_generate_key_from_passphrase_value_error(self, crypto_manager: CryptographyManager) -> None:
        """Trigger ValueError in _generate_key_from_passphrase via PBKDF2HMAC.derive failure."""
        with patch(
            "app.features.crypto.encryption.PBKDF2HMAC",
        ) as mock_kdf_cls:
            mock_kdf_cls.return_value.derive.side_effect = ValueError("bad derivation input")
            with pytest.raises(KeyGenerationError, match="Key generation failed"):
                crypto_manager._generate_key_from_passphrase("some_passphrase")

    def test_generate_key_from_passphrase_type_error(self, crypto_manager: CryptographyManager) -> None:
        """Trigger TypeError in _generate_key_from_passphrase via PBKDF2HMAC.derive failure."""
        with patch(
            "app.features.crypto.encryption.PBKDF2HMAC",
        ) as mock_kdf_cls:
            mock_kdf_cls.return_value.derive.side_effect = TypeError("unexpected type")
            with pytest.raises(KeyGenerationError, match="Key generation failed"):
                crypto_manager._generate_key_from_passphrase("some_passphrase")

    def test_generate_key_from_passphrase_os_error(self, crypto_manager: CryptographyManager) -> None:
        """Trigger OSError in _generate_key_from_passphrase."""
        with patch(
            "app.features.crypto.encryption.PBKDF2HMAC",
        ) as mock_kdf_cls:
            mock_kdf_cls.return_value.derive.side_effect = OSError("system failure")
            with pytest.raises(KeyGenerationError, match="Key generation failed"):
                crypto_manager._generate_key_from_passphrase("some_passphrase")

    def test_load_or_create_key_os_error(self, crypto_manager: CryptographyManager) -> None:
        """Trigger OSError in _load_or_create_key (e.g. permission denied reading key file)."""
        with (
            patch.object(
                crypto_manager,
                "_load_existing_or_generate_new_key",
                side_effect=OSError("permission denied"),
            ),
            pytest.raises(KeyGenerationError, match="Key management failed"),
        ):
            crypto_manager._load_or_create_key()

    def test_load_or_create_key_type_error(self, crypto_manager: CryptographyManager) -> None:
        """Trigger TypeError in _load_or_create_key."""
        with (
            patch.object(
                crypto_manager,
                "_load_existing_or_generate_new_key",
                side_effect=TypeError("unexpected type"),
            ),
            pytest.raises(KeyGenerationError, match="Key management failed"),
        ):
            crypto_manager._load_or_create_key()

    def test_load_existing_key_validation_value_error(
        self,
        logger: logging.Logger,
        tmp_path: Path,
    ) -> None:
        """Trigger ValueError in _load_existing_or_generate_new_key key validation."""
        key_file = tmp_path / "test.key"
        # Write a valid base64 string that is NOT a valid Fernet key (wrong length)
        bad_key = base64.urlsafe_b64encode(b"short").decode()
        key_file.write_text(bad_key)

        manager = CryptographyManager(logger, str(key_file))
        with pytest.raises(ValueError, match="Invalid encryption key format"):
            manager._load_existing_or_generate_new_key(None)

    def test_load_existing_key_validation_binascii_error(
        self,
        logger: logging.Logger,
        tmp_path: Path,
    ) -> None:
        """Trigger binascii.Error in _load_existing_or_generate_new_key key validation."""
        key_file = tmp_path / "test.key"
        # Write bytes that are not valid base64 at all
        key_file.write_bytes(b"\xff\xfe\xfd\x00\x01\x02invalid-not-base64!!!")

        manager = CryptographyManager(logger, str(key_file))
        with pytest.raises(ValueError, match="Invalid encryption key format"):
            manager._load_existing_or_generate_new_key(None)

    def test_get_fernet_value_error(self, crypto_manager: CryptographyManager) -> None:
        """Trigger ValueError in _get_fernet when Fernet instantiation fails."""
        # Set a corrupted encryption key that will make Fernet() raise
        crypto_manager._encryption_key = b"not-a-valid-fernet-key"
        with pytest.raises(InvalidKeyError, match="Fernet initialization failed"):
            crypto_manager._get_fernet()

    def test_get_fernet_type_error(self, crypto_manager: CryptographyManager) -> None:
        """Trigger TypeError in _get_fernet."""
        # Mock _load_or_create_key to return an int, causing Fernet(int) -> TypeError
        with (
            patch.object(
                crypto_manager,
                "_load_or_create_key",
                return_value=12345,
            ),
            pytest.raises(InvalidKeyError, match="Fernet initialization failed"),
        ):
            crypto_manager._get_fernet()

    def test_encrypt_token_type_error(self, crypto_manager: CryptographyManager) -> None:
        """Trigger TypeError in encrypt_token when fernet.encrypt raises."""
        valid_key = Fernet.generate_key().decode()
        with patch(
            "app.features.crypto.encryption.Fernet",
        ) as mock_fernet_cls:
            mock_instance = mock_fernet_cls.return_value
            mock_instance.encrypt.side_effect = TypeError("expected bytes-like object")
            with pytest.raises(EncryptionError, match="Token encryption failed"):
                crypto_manager.encrypt_token("test_token", key=valid_key)

    def test_decrypt_token_base64_binascii_error(self, crypto_manager_with_key: CryptographyManager) -> None:
        """Trigger binascii.Error in decrypt_token base64 decode."""
        # A string that fails base64 decode
        with pytest.raises(InvalidTokenError, match="not valid base64"):
            crypto_manager_with_key.decrypt_token("!!!invalid-base64-data!!!")

    def test_decrypt_token_unicode_decode_error(self, crypto_manager_with_key: CryptographyManager) -> None:
        """Trigger UnicodeDecodeError in decrypt_token via fernet.decrypt result."""
        # Get a valid Fernet instance, then mock decrypt to return non-UTF-8 bytes
        encrypted = crypto_manager_with_key.encrypt_token("test")
        with patch.object(
            crypto_manager_with_key,
            "_get_fernet",
        ) as mock_get:
            mock_fernet = mock_get.return_value
            mock_fernet.decrypt.return_value = b"\xff\xfe\xfd"  # Invalid UTF-8
            with pytest.raises(DecryptionError, match="Token decryption failed"):
                crypto_manager_with_key.decrypt_token(encrypted)
