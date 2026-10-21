# Getting Support

Thank you for using `isolation-check`.

As an early alpha development tool, support is provided on a best-effort basis by contributors.

---

## Where to Get Help

### 1. Documentation & Guides
Before opening an issue, please consult:
- [README.md](README.md) for installation, commands, and scope limitations.
- [Troubleshooting Guide](README.md#troubleshooting-guide) for common errors (ports, Docker Compose tags, path validation).
- [Examples](examples/postgres-app/) for a verified minimal working setup.
- [Agent Integration Guide](docs/agent-integration.md) for coding assistant safety rules.

### 2. Bug Reports
If you encounter unexpected behavior, crashes, or incorrect collision flags:
- Search existing [GitHub Issues](https://github.com/rxmna8502/isolation-check/issues) to see if it has already been reported.
- Open a new issue using our [Bug Report Template](.github/ISSUE_TEMPLATE/bug_report.yml).
- **CRITICAL:** Do NOT post real passwords, tokens, API keys, full `.env` files, or private database connection URLs. Always redact credentials before submitting.

### 3. Feature Requests & Proposals
- Open a new issue using the [Feature Request Template](.github/ISSUE_TEMPLATE/feature_request.yml).
- Note: Current scope is strictly focused on local Linux, Docker Compose, PostgreSQL, and single-application service isolation. Large architectural expansions (e.g., multi-database support, destructive destroy) are deferred to later roadmap milestones.

### 4. Security Vulnerabilities
- For security issues, do NOT open a public issue. See [SECURITY.md](SECURITY.md) for private reporting procedures.
