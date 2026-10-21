# Contributing to isolation-check

Thank you for your interest in contributing to `isolation-check`!

`isolation-check` is an open-source command-line tool that creates and verifies supported isolation boundaries for local Git worktree environments.

---

## Code of Conduct

All contributors and participants agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

---

## Development Setup

### Prerequisites
- Python 3.11 or higher
- Git 2.30 or higher
- Docker Compose v2.20+ (optional for unit tests, required for Compose rendering and integration tests)

### Local Environment
1. Clone the repository:
   ```bash
   git clone https://github.com/rxmna8502/isolation-check.git
   cd isolation-check
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   # On Linux/macOS:
   source .venv/bin/activate
   # On Windows (PowerShell):
   .venv\Scripts\Activate.ps1
   ```

3. Install the package in editable mode with development dependencies:
   ```bash
   pip install -e ".[dev]"
   ```

4. Run the test suite:
   ```bash
   python -m unittest discover -s tests -v
   ```

5. Build the source distribution and wheel:
   ```bash
   python -m build
   ```

---

## Coding Guidelines & Safety Rules

1. **Maintain Minimal Dependencies:**
   `isolation-check` core CLI has zero third-party runtime dependencies. Do not add runtime dependencies without prior discussion.

2. **Security & Subprocess Safety:**
   - Never use `shell=True` in `subprocess` calls.
   - Always use argument arrays (`["docker", "compose", ...]`).
   - Run subprocesses with sanitized environment variables (via `_build_safe_env()`) and explicit timeouts.
   - Bounded stdout/stderr capture is mandatory (default 1MB limit).
   - Never print or leak passwords, credentials, or private environment files. Use `[REDACTED]` in reports.

3. **File Permissions & Boundaries:**
   - Restrict permissions on sensitive generated files (e.g. `mode 0600` on credentials and runtime state).
   - Strictly validate paths against directory boundaries to prevent path traversal (`..`) or symlink escapes.

4. **Non-Destructive Operations:**
   - Never execute arbitrary commands or migrations inside user containers.
   - Never pass destructive flags (`-v`, `--volumes`) to `docker compose down`. Preserve database data.

---

## Submitting Pull Requests

1. Fork the repository and create a branch from `main`.
2. Ensure all tests pass (`python -m unittest discover tests`).
3. Add deterministic unit tests covering any new logic or bug fixes.
4. Keep commits focused and provide descriptive commit messages following Conventional Commits (`feat: ...`, `fix: ...`, `docs: ...`, `test: ...`).
5. Open a Pull Request referencing the PR template.

---

## Beginner-Friendly Contribution Opportunities

Good starting points for contributors:
- Expanding test fixtures in `tests/fixtures/` for various Docker Compose service declarations.
- Adding Compose rendering test cases for multi-file merge sequences.
- Improving error diagnostic messages when Docker Compose or Git encounters host permission errors.
- Documentation clarity improvements in `examples/` and README.
