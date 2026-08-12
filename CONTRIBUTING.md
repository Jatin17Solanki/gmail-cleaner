# Contributing to Gmail Cleaner

Thanks for your interest in contributing! 🎉

## Getting Started

1. **Fork** the repository
2. **Clone** your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/gmail-cleaner.git
   cd gmail-cleaner
   ```
3. **Create a branch** for your changes:
   ```bash
   git checkout -b feature/your-feature-name
   ```

## Development Setup

### Prerequisites
- Python 3.9+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- Your own Google Cloud OAuth credentials

### Running Locally

```bash
# Install dependencies
uv sync

# Run the app
uv run python main.py
```

The app will be available at http://localhost:8766

### Docker Development

```bash
docker compose up --build
```

## Code Style

- **Python**: Follow PEP 8, use type hints where possible

## Making Changes

### For New Features
1. Open a feature request issue to discuss the idea or feel free to open an pr
2. Keep PRs focused and small

## Pull Request Process

1. Update documentation if needed
2. Please Test your changes locally
3. Please Test Docker build if you modified Dockerfile or dependencies
4. A maintainer will be automatically requested for review via CODEOWNERS on all pull requests


## Maintainer Notes

### Rotating the GHCR publish token

`.github/workflows/build-and-push.yml` pushes Docker images to
`ghcr.io/jatin17solanki/gmail-cleaner` using a repo secret,
`GHCR_GMAIL_CLEANER_PAT` — a classic PAT with `repo` + `write:packages`
scopes, set with a 90-day expiry (created 2026-08-12, expires ~2026-11-10).
GitHub does not send an automated warning to repo secrets when the
underlying token expires — the first sign will be the "Build and Publish
Docker images" workflow failing at its "Log in to Github Packages" step on
the next release.

To rotate it:

1. Go to <https://github.com/settings/tokens>, generate a new classic PAT
   with the same scopes (`repo`, `write:packages`). Give it a new 90-day
   (or longer) expiry.
2. Go to
   <https://github.com/Jatin17Solanki/gmail-cleaner/settings/secrets/actions>,
   click `GHCR_GMAIL_CLEANER_PAT` → **Update secret**, paste the new token
   value. The secret name stays the same, so no workflow changes are
   needed.
3. Revoke the old PAT at <https://github.com/settings/tokens> once the new
   one is confirmed working (trigger a manual run via `gh workflow run
   build-and-push.yml` or the Actions tab's "Run workflow" button, and
   check it succeeds).

## Questions?

Feel free to open an issue or start a discussion!

---

Thank you for contributing! ❤️
