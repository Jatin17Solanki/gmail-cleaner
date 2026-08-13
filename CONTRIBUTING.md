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

`docker-compose.yml` defaults to this fork's published image (Option 1). To
test local changes, switch it to Option 2 (build from source) first — see
the comments in `docker-compose.yml` — then:

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

`GHCR_GMAIL_CLEANER_PAT` — a classic PAT with `repo` + `write:packages`
scopes, set with a 90-day expiry (created 2026-08-12, expires ~2026-11-10) —
is used by **two** workflows, not just one:

- `.github/workflows/build-and-push.yml` pushes Docker images to
  `ghcr.io/jatin17solanki/gmail-cleaner` (needs `write:packages`).
- `.github/workflows/update-changelog.yml` opens a PR with the changelog
  update on every release (needs `repo`, to push a branch and open a PR —
  the default `GITHUB_TOKEN` can't be used here: `main` requires a PR to
  merge, and GitHub deliberately doesn't trigger downstream workflows like
  `tests.yml` for pushes/PRs made with the default token, which would leave
  the PR stuck with no `test` status check ever running).

GitHub does not send an automated warning to repo secrets when the
underlying token expires — the first sign will be `build-and-push.yml`
failing at "Log in to Github Packages," or `update-changelog.yml` failing
to push its branch, on the next release.

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
   check it succeeds). `update-changelog.yml` only fires on a real release,
   so it's not independently testable the same way — its next real run is
   the actual check for that one.

## Questions?

Feel free to open an issue or start a discussion!

---

Thank you for contributing! ❤️
