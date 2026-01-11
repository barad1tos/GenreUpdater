# Feature Modules

Application feature modules for specific operations.

## Available Features

| Module | Description |
|--------|-------------|
| `app.features.batch` | Batch processing of large libraries |
| `app.features.crypto` | API key encryption/decryption |
| `app.features.verify` | Database verification utilities |

## Batch Processing

The batch processor handles large-scale operations on track libraries.

```python
from app.features.batch.batch_processor import BatchProcessor

processor = BatchProcessor(music_updater, console_logger, error_logger)
await processor.process_batch(artists, batch_size=100)
```

### BatchProcessor

Handles batch processing of multiple artists with progress tracking.

**Methods:**
- `process_batch(artists, batch_size)` - Process artists in batches
- `process_single(artist)` - Process single artist

## Cryptography

Handles secure storage of API keys using Fernet encryption.

```python
from app.features.crypto.encryption import CryptographyManager

manager = CryptographyManager(logger, key_file_path="encryption.key")
encrypted = manager.encrypt(plaintext)
decrypted = manager.decrypt(encrypted)
```

### CryptographyManager

Manages Fernet-based encryption for tokens and configuration data.

**Methods:**
- `encrypt(data)` - Encrypt plaintext data
- `decrypt(token)` - Decrypt Fernet token
- `rotate_key()` - Rotate encryption key safely

## Database Verification

Verifies database integrity and consistency.

```python
from app.features.verify.database_verifier import DatabaseVerifier

verifier = DatabaseVerifier(cache_service, logger)
await verifier.verify_all()
```

### DatabaseVerifier

Verifies cache database consistency with Apple Music library.

**Methods:**
- `verify_all()` - Full database verification
- `verify_artist(artist)` - Verify single artist
