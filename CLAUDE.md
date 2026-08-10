# CLAUDE.md

**Before anything else, read `PROGRESS.md` at the repo root** (gitignored,
exists on disk — not this file). It's the current-state log: what's shipped,
what's in flight, what's backlogged, what phase comes next. This file
(CLAUDE.md) only covers stable conventions that don't change phase to
phase — it will not tell you where things currently stand.

Guidance for AI coding assistants working in this repo, so consistency holds
across sessions that may be days or weeks apart. The full spec is `PRD.md`
at the repo root (gitignored — exists on disk, not committed; read it before
starting any phase work).

## What this is

A personal, self-hosted Gmail inbox-cleanup tool: FastAPI backend, server-
rendered templates + vanilla JS frontend, Docker deployment, OAuth 2.0
against a user-provided Google Cloud project. Forked from
[Gururagavendra/gmail-cleaner](https://github.com/Gururagavendra/gmail-cleaner)
and extended — do not rewrite working subsystems; extend them following
existing patterns.

## Conventions

- **All Gmail search queries go through `build_gmail_query()`** in
  `app/services/gmail/helpers.py` — never build ad-hoc f-string queries.
  It defaults to `label:INBOX` unless an explicit `category` filter is
  given (Phase 1, #104): "no filters" must mean Inbox, not all mail.
- When an operation (delete, label add/remove, etc.) needs to stay scoped
  to whatever filters produced the sender list it's acting on, merge those
  filters with the target `sender` into one dict and pass it through
  `build_gmail_query()` — see `delete_emails_by_sender()` and
  `_apply_label_operation_background()` in `app/services/gmail/` for the
  pattern (Phase 1, #107).
- The most recent delete-scan's filters live in `state.delete_scan_filters`
  (`app/core/state.py`), set by `scan_senders_for_delete()` and read by the
  API layer (`app/api/actions.py`) so delete/label calls reuse them without
  the frontend needing to resend filters on every action.
- `archive.py` is the reference pattern for Inbox-scoping a query
  (`in:inbox`), cited in the PRD as already correct — it wasn't part of the
  Phase 1 fix set.
- The app-level login gate (`app/core/security.py`,
  `app/core/middleware.py`) is unrelated to Gmail OAuth
  (`app/services/auth.py`). The login gate protects the UI itself via a
  single shared password (`APP_PASSWORD` env var) and session cookie;
  Gmail OAuth is the per-account sign-in flow. Don't conflate the two.

## Tests

- Structure: `tests/unit/{api,models,services}/`, mirroring `app/`.
  `tests/unit/services/gmail/` holds one file per service module
  (`test_delete.py`, `test_labels.py`, etc.); `tests/unit/core/` holds
  config/state/security/middleware tests.
- Style: one `TestXxx` class per function/endpoint under test, one
  `test_snake_case` method per behavior, short docstring stating the
  behavior (not the mechanics). Mock `get_gmail_service` and assert on the
  `q=` kwarg passed to `messages().list()` when testing query construction.
- `tests/conftest.py` has two autouse fixtures: `reset_app_state` (resets
  the `app.core.state` singleton before/after every test — required
  because it's shared global state and background tasks run synchronously
  under `TestClient`) and `mock_gmail_auth` (blocks real OAuth/browser
  launches during tests). Auth-gate tests additionally clear
  `security._active_sessions` and reset `security.settings.app_password`
  per test, since those aren't covered by `reset_app_state`.
- **Non-negotiable**: no feature or fix ships without tests, written and
  passing, before its PR opens. Audit existing tests for the files you're
  touching before writing new ones, so effort isn't duplicated.
- Test data safety: any test exercising real delete/archive/mark-as-
  read/label behavior against a live Gmail account must use synthetic data
  (e.g. `[TEST]`-prefixed subjects sent to the account itself) — never real
  inbox content.

## Git workflow

- One branch per phase (Phase 0 through the final E2E phase).
- Cadence per phase: implement → write/update tests → run tests →
  open PR → **stop and wait for human review/merge** → next phase.
- Never merge your own PRs. Never start the next phase until the previous
  one's PR is confirmed merged.
- Exception: Phase 0.5 (baseline housekeeping commit) is pushed directly to
  `main`, skipping the branch-per-phase ritual — that's a one-time carve-out
  for repo setup, not a precedent.
- Update the README's Roadmap table (Planned → Done) in the same PR that
  completes a phase — it should never go stale relative to actual progress.

## Handling ambiguity

Within a phase, don't stop for minor implementation ambiguity — make the
most reasonable call consistent with the PRD's conventions and design
system, and document the assumption in that phase's PR description. Only
raise an actual question when a decision would be genuinely costly to get
wrong or directly contradicts something explicit in the PRD.

## Secrets

`credentials.json`, `PRD.md`, and anything under `data/` (including the
auth password hash, `auth.json`) are gitignored and must never be
committed. Before any PR touching auth or credentials, re-check
`.gitignore` still covers all of them.
