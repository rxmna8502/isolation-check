# Security Policy

## Supported Versions

Only the latest development alpha version and the `main` branch receive security updates:

| Version | Supported          |
| ------- | ------------------ |
| 0.3.0a1 | :white_check_mark: |
| < 0.3.0 | :x:                |

## Reporting a Vulnerability

The `isolation-check` project takes security seriously. If you discover a security vulnerability—such as credential leakage, path traversal escapes, unsafe subprocess execution, or unintended access to host resources—please report it privately.

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please report security vulnerabilities via:
- GitHub Private Vulnerability Reporting: [Report a vulnerability](https://github.com/rxmna8502/isolation-check/security/advisories/new)
- Or contact the maintainers directly via email: `sairamanladi2007@gmail.com` with the subject `[SECURITY] isolation-check vulnerability`.

### What to Include in Your Report
- A description of the vulnerability and its potential impact.
- Steps to reproduce or a minimal proof of concept.
- Affected components or CLI subcommands.

### Critical Safety Instructions
- **Do NOT post real secrets, production database URLs, access tokens, API keys, or private `.env` files** in vulnerability reports or issues.
- Always use sanitized values (e.g. `postgresql://user:[REDACTED]@host:5432/dbname`) in reproduction scripts.

### Response Timeline
- We will acknowledge receipt of your report within 48 hours.
- We will provide status updates as we investigate and develop a remediation.
- Once a fix is verified, a patch release and advisory will be coordinated.
