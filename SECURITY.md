# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 2.x.x   | :white_check_mark: |
| 1.x.x   | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it responsibly:

1. **Do NOT** open a public GitHub issue for security vulnerabilities
2. Email the maintainer directly at: [roman.borodavkin@gmail.com](mailto:barad1tos@gmail.com)
3. Include as much detail as possible:
    - Description of the vulnerability
    - Steps to reproduce
    - Potential impact
    - Suggested fix (if any)

### Response Timeline

- **Initial Response**: Within 48 hours
- **Status Update**: Within 7 days
- **Resolution Target**: Within 30 days (depending on severity)

## Security Considerations

### API Keys

This application handles API keys for external services (MusicBrainz, Discogs, Last.fm):

- API keys are stored encrypted using the `cryptography` library (Fernet symmetric encryption)
- Keys can be rotated using the `rotate_keys` command
- Never commit `.env` or files containing plaintext API keys

### AppleScript Execution

The application executes AppleScript commands to interact with Music.app:

- All AppleScript files are located in the `applescripts/` directory
- User input is sanitized before being passed to AppleScript
- No arbitrary code execution is allowed

### File System Access

- The application reads/writes to configured directories only
- Cache files are stored in the `cache/` directory
- Logs are stored in configured log directories
- Snapshot files contain SHA-256 checksums for integrity verification

## Security Best Practices

1. **Keep dependencies updated** - Run `uv sync` regularly
2. **Use encrypted API keys** - Never store plaintext keys in config
3. **Review AppleScript files** - Before running, verify script contents
4. **Limit file permissions** - Ensure config files are not world-readable

## Acknowledgments

We appreciate responsible disclosure of security vulnerabilities. Contributors who report valid security issues will be
acknowledged in this file (with their permission).
