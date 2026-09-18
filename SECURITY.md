# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it via GitHub
Issues or email [security contact]. Do not open a public issue for
unpatched vulnerabilities.

We will acknowledge within 3 business days and provide a detailed
response within 7 business days.

Please include:
- Description of the vulnerability and impact
- Steps to reproduce
- Affected versions
- Suggested fix (if any)

## Security Practices

- API keys are stored in the OS credential manager (Windows Credential
  Manager), never in repo or config files.
- Downloads use streaming with SHA-256 verification.
- SQLite database lives in user data directory with standard OS
  permissions.
- FFmpeg subprocesses are spawned with limited shell exposure and
  timeouts.
