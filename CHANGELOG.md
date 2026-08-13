# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each entry leads with a one-line summary. Longer entries have a collapsed
"Details" section underneath with the full story — root cause, files
touched, why a decision was made a particular way — for anyone who wants
it; collapsed by default so the file stays scannable.

## [Unreleased]

Nothing pending.

## [0.1.2] - 2026-08-13

### Added
- Added a "Future Work" section to the README — ideas raised during
  development but not built (editing a saved Routine, deeper per-sender
  message preview, an explicit "scan entire mailbox" mode, a sender-first
  search entry point, richer Restore entries, Routines using Gmail's
  change-history API instead of re-scanning). No timeline attached; kept
  visible instead of only living in this file.

### Fixed
- **Bulk actions (Delete, Archive, Mark as read, Unsubscribe, Download,
  Routine run, Restore) now show loading feedback instead of nothing at
  all between clicking and the completion toast.**
  <details>
  <summary>Details</summary>

  Found from real usage: a quota-paced bulk delete/unsubscribe can take
  minutes, and with the button staying enabled and unchanged the whole
  time, it was easy to mistake for nothing happening and click again —
  firing a duplicate operation. New `GmailCleaner.UI.setButtonLoading()`/
  `restoreButton()` helpers (`static/js/ui.js`) disable the triggering
  button and swap its label for a spinner, reusing the same spinner
  markup (`ti-loader-2` + the existing `.spin` CSS class) already used
  for the scan-in-progress indicator. Wired into `delete.js` (delete,
  unsubscribe, download), `archive.js`, `markread.js`, `routines.js`
  (routine run), and `restore.js`. Verified with `node --check` on every
  touched file (no JS test harness exists in this repo) and manually
  against a real account.
  </details>

- **`update-changelog.yml` opens a PR instead of pushing straight to
  `main`.**
  <details>
  <summary>Details</summary>

  With branch protection now requiring a PR to merge into `main`, the old
  direct-push behavior would have started failing on the next release.
  Switched to creating a `changelog/<tag>` branch and opening a PR, using
  the existing `GHCR_GMAIL_CLEANER_PAT` secret instead of the default
  `GITHUB_TOKEN` — necessary because GitHub doesn't trigger downstream
  workflows (like `tests.yml`, whose `test` check the branch-protection
  rule requires) for pushes/PRs made with the default token, which would
  otherwise leave an auto-opened PR permanently stuck with no way to
  satisfy its required status check. `CONTRIBUTING.md`'s PAT rotation
  notes updated to reflect this secret now backing two workflows.
  Verified by cutting `v0.1.2` itself: the workflow correctly opened a PR
  with its `test`/`require-labels` checks actually firing and passing.
  </details>

- **Version comparison links at the bottom of this file pointed to the
  upstream repo instead of this fork.**
  <details>
  <summary>Details</summary>

  `[0.1.0]`, `[0.1.1]`, and `[Unreleased]` all pointed to
  `Gururagavendra/gmail-cleaner` instead of `Jatin17Solanki/gmail-cleaner`
  — inherited unchanged from upstream's original `CHANGELOG.md` at the
  fork's baseline commit, then silently copied forward by every automated
  release since (the update-changelog script reuses the existing
  `[Unreleased]` link's base URL rather than hardcoding one). `[1.0.0]` is
  correctly left pointing upstream, since that release genuinely happened
  there, pre-fork. Correcting the base URL once means every future
  automated release copies the right one forward.
  </details>

## [0.1.1] - 2026-08-13

### Changed
- **`docker-compose.yml` now defaults to this fork's own published image**
  instead of building from source.
  <details>
  <summary>Details</summary>

  `image: ghcr.io/jatin17solanki/gmail-cleaner:latest` (`pull_policy:
  always`) is now Option 1; building from source moved to Option 2, for
  anyone working on the app itself. Every `docker compose up --build`
  reference in the README's setup/troubleshooting instructions updated to
  `docker compose up -d` accordingly (`--build` no longer does anything
  meaningful once the default service has no `build:` key), and
  `CONTRIBUTING.md`'s Docker Development section now notes contributors
  need to switch to Option 2 before testing local changes.
  </details>

### Fixed
- **Local (`uv run python main.py`) and Docker runs no longer read/write
  two different data directories.**
  <details>
  <summary>Details</summary>

  Docker used to auto-detect `/app/data` at startup and redirect
  `token_file` there (a fragile, undocumented `Settings.__init__` side
  effect inherited from upstream), while a local run kept the bare
  `token.json` default — so `accounts.json`/`auth.json`/
  `operations.json`/`routines.json`/`token_file` (every one of them keyed
  off `token_file`'s directory) silently diverged into two unrelated
  stores depending on which way the app was run. Confirmed concretely
  against this repo's own local state, which had two different Gmail
  accounts signed in via the two paths.

  `app/core/config.py`'s `DEFAULT_TOKEN_FILE` is now the plain relative
  `data/token.json` — Docker's `WORKDIR /app` (`Dockerfile`) already
  resolves that to `/app/data/token.json`, exactly what
  `docker-compose.yml`'s bind mount covers, with no detection logic
  needed. The old `/app/data` auto-detection is removed entirely
  (including its absolute-path traversal hardening, now moot since
  there's no absolute-path case left to guard). New
  `migrate_legacy_data_layout()`, called once from `main.py`'s real
  entrypoint (not at import time, so it can't run as a side effect of
  `pytest` collection): moves pre-unification local files into `data/`,
  but never overwrites a file already at the new location — real installs
  could have diverged data at both locations from before this fix, and
  guessing which copy should win is worse than leaving both in place with
  a logged warning. New `tests/unit/core/test_config.py`. All local +
  Docker account state in this repo was cleared for a clean start rather
  than reconciled, since nothing of value was persisted.
  </details>

## [0.1.0] - 2026-08-12

First published Docker image for this fork:
`ghcr.io/jatin17solanki/gmail-cleaner:v0.1.0` (and `:latest`). Covers
everything below — multi-account switching, quota-aware scanning,
Routines, per-email preview, Restore-from-Trash, and the app-level login
gate, on top of the original project's core delete/unsubscribe/archive/
mark-as-read/label functionality.

### Added
- CodeRabbit AI code review integration (`.coderabbit.yaml`).
- Pre-commit hooks (ruff, bandit, trailing whitespace, etc.).
- Comprehensive type annotations throughout the codebase.
- **Restore-from-Trash**: a permanent "Restore" sidebar tab backed by a
  local, app-scoped operation log, not a "restore everything in Gmail
  Trash" approach.
  <details>
  <summary>Details</summary>

  Every successful delete, archive, mark-as-read, or label add/remove
  writes a log entry recording exactly which messages it modified and
  how; restoring an entry reverses that exact change (delete → untrash,
  archive → re-add INBOX, mark-as-read → re-add UNREAD, label added →
  label removed) via one generic mechanism rather than per-action-type
  logic. Entries older than 30 days are pruned automatically, with a note
  on the Restore screen reflecting that window. Mark-important isn't
  covered. Backed by `./data/operations.json`.
  </details>

- **Full UI/UX redesign**: sidebar navigation (Delete / Mark as read /
  Archive / Routines / Restore), a per-view slide-over filter drawer, and
  inline Label/Important icon actions on every sender row. A real
  interaction rebuild, not a reskin.
  <details>
  <summary>Details</summary>

  - Unsubscribe is no longer its own tab — merged into Delete. One scan
    now also detects each sender's unsubscribe link/type, surfaced as a
    status badge (`Auto`/`Open link`/`No unsubscribe link`) and an
    independent "Unsub" toggle per row.
  - Archive gets its own scan (previously it only ever operated on
    whatever senders the Delete tab had already scanned, with no filters
    of its own).
  - Mark as read gets a real sender-row list, replacing the old aggregate
    unread-count card and blind "mark N most recent unread" picker.
  - Two new filter keys: unread-only and has-attachment, available in
    every view's drawer.
  - Expand/collapse ships as a shell in this phase — real per-message
    subjects and a copy-to-clipboard icon, but the "view full email" eye
    icon is inert and bulk actions still operate on whole senders, not
    individually unchecked messages (finished later — see the per-email
    preview entry below).
  - New `static/js/senderList.js` implements the shared sender-row-list
    shell (scan, render, expand/collapse, filter drawer, selection) once,
    used by `delete.js`/`markread.js`/`archive.js` instead of tripling
    near-identical code.
  - Routines appears as a real, always-visible sidebar item showing a
    "coming in a future phase" placeholder (built out later — see below).
  </details>

- **Multi-account switcher**: sign in to more than one Gmail account and
  switch between them without re-authenticating.
  <details>
  <summary>Details</summary>

  Tokens are now stored per account at `<data dir>/tokens/<email>.json`,
  keyed by email, with a small `accounts.json` index tracking the
  registered account list and which one is active — replacing the single
  shared `token.json` this app previously assumed. A one-time migration
  moves an existing pre-multi-account `token.json` into this layout the
  first time its email is confirmed via a Gmail profile call, so
  upgrading needs no manual steps. "Add another account" always forces a
  fresh OAuth consent screen and saves the result under a new slot,
  without touching whichever account was already active. The operation
  log / Restore is scoped per account, so restoring can never replay a
  change against the wrong mailbox. New account-switcher dropdown in the
  topbar (active account with a checkmark, others to switch to, "Add
  another account," Sign out).
  </details>

- **Gmail API quota awareness.**
  <details>
  <summary>Details</summary>

  A default-sized scan fetched per-message metadata for every result —
  tens of thousands of quota units in a few seconds against Gmail's
  6,000-units/minute/user cap, with zero retry/backoff anywhere in the
  codebase. When Gmail started rate-limiting mid-scan, the affected
  messages were silently dropped, producing incomplete, non-deterministic
  sender counts with no indication anything went wrong.

  New `app/services/gmail/quota.py`: a rolling 60-second usage tracker
  proactively blocks before a call would exceed the cap (surfacing a live
  "waiting Ns" message in the UI), retries a call once on a real 429 or
  quota-shaped 403 with exponential backoff, and batches per-message
  `.get()` calls with the same retry treatment for any sub-request that
  fails. An opt-in `QUOTA_TRACE_LOGGING` env var (off by default) logs a
  timestamped line for every charged call, for diagnosing scan pacing
  from server logs. A wall-clock time estimate ("This scan will take
  about N minutes — come back around H:MM") shows once a scan's estimate
  exceeds 30 seconds, with an info icon explaining the underlying quota
  math.

  This shipped after several rounds of real-account investigation into
  scans silently under-reporting results, which turned out to have two
  separate causes: (1) the 6,000-units/minute budget itself, and (2) a
  *separate* Gmail limit — max 50 concurrent requests per account,
  independent of total unit cost — which the batch-request code was
  blowing through at 100 concurrent sub-requests per batch. Fixing both
  (clamping concurrent batch size to 25, with 2 retry passes) was
  confirmed via a real repeat scan: `requested=1000 succeeded=1000
  failed=0`, zero loss.
  </details>

- **Routines**: a saved, named preset — a sender list, a relative age
  threshold, and one or more actions — that re-runs the same lookup with
  one click instead of re-entering filters every time. Always requires a
  preview/confirm step before running.
  <details>
  <summary>Details</summary>

  Scoped to a single account. Manual trigger only for now (the stored
  schema has a `schedule` field, always `null`, reserved for a future
  cron trigger). Combines every selected action into a single label diff
  applied once across all matched messages, rather than running each
  action as its own pass — cheaper, and produces exactly one
  operation-log entry per run, which is what makes a run undoable via
  Restore. New `app/services/routines.py` (storage),
  `app/services/gmail/routines.py` (preview/run), `app/api/routines.py`
  (endpoints), and a real Routines list/create-form/confirm-modal in the
  frontend.
  </details>

- **Per-email preview**: expand a sender to see and exclude individual
  messages before a bulk action runs, completing the mechanism the UI
  redesign shipped as a shell.
  <details>
  <summary>Details</summary>

  The eye icon deep-links to the real Gmail web UI in a new tab instead
  of fetching/rendering the message body in-app — zero extra Gmail API
  cost, and no attacker-controlled email HTML ever touches this app's
  origin, since Gmail's own site renders it (requires already being
  signed into that account in the browser).

  Per-message checkboxes exclude, not include: an action still queries
  Gmail fresh for everything matching the sender and active filters, then
  subtracts whatever was explicitly unchecked. An include-list reading
  would have silently skipped mail beyond whatever happened to be
  previewed, since a sender can have more messages than fit on screen.

  Sender rows show "X shown of Y total emails" whenever a sender's real
  total exceeds what was sampled during the scan, and a persistent note
  clarifies that acting on a sender affects all of its mail matching the
  active filters, not just what's previewed — added after review found
  that delete/archive/mark-read/label actions were never actually bounded
  by a scan's sample size in the first place (a gap predating this
  feature, made more consequential by the new "select all" affordance
  added alongside this).

  Also added: a "Select all" checkbox on every sender-row list.
  </details>

- Docker image publishing pipeline verified working end-to-end.
  <details>
  <summary>Details</summary>

  The GitHub Actions workflow for this was inherited from upstream but
  never actually exercised — no repo secret existed and no release had
  ever been cut. Added a manual `workflow_dispatch` trigger to verify the
  pipeline with a throwaway build first. Confirmed via a real run:
  multi-platform (`linux/amd64`+`linux/arm64`) build and push to
  `ghcr.io/jatin17solanki/gmail-cleaner` succeeded, and the resulting
  image is genuinely publicly pullable (checked anonymously) after
  flipping the GHCR package's visibility from its private-by-default
  setting to public — a separate toggle GitHub doesn't inherit from the
  source repo's own public visibility. Documented the publish token's
  90-day rotation runbook in `CONTRIBUTING.md`. `v0.1.0` itself was then
  cut from `main`, firing the real release trigger and smoke-tested
  directly (`docker pull` + standalone `docker run`): boots cleanly,
  finds the mounted `credentials.json`, serves the app correctly.
  </details>

- Housekeeping pass ahead of the end-to-end test phase:
  <details>
  <summary>Details</summary>

  - Default scan limit changed from 1000 to 500, for a lighter first-scan
    experience.
  - Added a favicon (an original small SVG mail glyph, no external
    asset).
  - The unsubscribe badge's meaning (`Auto`/`Open link`/`No unsubscribe
    link`) had a hover tooltip but no visible hint it existed — added a
    persistent info icon next to Delete's topbar.
  - Rewrote `ARCHITECTURE.md`, which had been untouched since the
    pre-fork upstream commit and described an app that no longer existed
    (single-account tokens, no login gate, no quota tracker, no Routines,
    no Restore).
  - Added a screenshot gallery to the README, replacing the wireframes-
    only reference with a redacted walkthrough of the real running app in
    user-journey order.
  - A full live end-to-end pass against a real account (browser-automated,
    supervised, every real delete/archive/mark-read/label/routine action
    immediately verified and reversed via Restore) found and fixed three
    real bugs: the selection-bar summary going stale after excluding a
    message, an invalid scan filter failing silently instead of showing
    an error, and Delete's confirm step using a native browser dialog
    (visually inconsistent with the rest of the app, and blocking for
    browser-automated testing) — replaced with the same in-app modal
    pattern used elsewhere.
  - Confirmed working end-to-end and otherwise unchanged: scan/filter/
    select-all/per-message exclude, the Gmail deep-link opening the
    correct message in the correct account, delete/archive/mark-read/
    label-add each followed by a Restore, and a full Routines create →
    preview → run → Restore cycle.
  </details>

- Full README revamp: table of contents, a rewritten Features section
  reflecting the actual current feature set, a condensed one-table
  Roadmap, a new Architecture section, an updated FAQ, and a shortened
  Credits section.
- Fixed two real correctness issues found while reviewing the README:
  the Setup section's `git clone` command pointed at the upstream repo
  instead of this fork, and `docker-compose.yml` defaulted to pulling
  upstream's published image — silently running the original app for
  anyone who just followed the README, missing every fix in this fork.
- Restored a "Support project" sidebar link (pointing to the original
  author's Buy Me a Coffee page) that existed in the pre-fork UI and was
  lost as incidental collateral of the full CSS/template rewrite above.

### Fixed
- Two UI polish issues found reviewing the Routines feature: the "New
  routine" form card was left-aligned instead of centered, and routine
  list rows had no visual gap between them.
- **The scan time-estimate message kept creeping forward instead of
  staying fixed.**
  <details>
  <summary>Details</summary>

  The displayed "come back around H:MM" time was recomputed from
  `Date.now()` on every status-poll tick, so it kept sliding forward with
  real time instead of staying pinned to when the scan actually started.
  Fixed by capturing the target timestamp once per scan and reusing that
  fixed value for every subsequent render.
  </details>

- **A subject line containing literal `"` characters broke the expanded
  message list's rendering.**
  <details>
  <summary>Details</summary>

  `escapeHtml()` only escaped what's safe for a text node, not an
  HTML-attribute-value context, which also needs quotes escaped. Fixed by
  building message rows via `createElement`/`textContent`/property
  assignment instead of string-templating sender-controlled text into
  HTML — removes the whole class of attribute-escaping bugs, not just
  this one instance.
  </details>

- Manual testing found several more issues, fixed in the same pass:
  <details>
  <summary>Details</summary>

  - The Tabler icon webfont was loaded from a CDN, which was unreliable
    in practice — every icon-only control silently lost its click target
    when it failed to load. Vendored the font locally instead, consistent
    with the app's "runs 100% locally" premise.
  - Toggling only a row's "Unsub" checkbox (without the delete checkbox)
    left the bottom actions bar hidden, making "Unsubscribe selected"
    unreachable.
  - The filter drawer had no way to close without applying or clearing
    filters — added click-outside and Escape to dismiss.
  - Buttons and icon-only row actions had no hover/active/focus-visible
    styling at all.
  - An expanded row's per-message checkboxes were fully interactive but
    silently ignored by every bulk action at the time (the feature that
    makes them do something shipped later) — disabled them with an
    explanatory tooltip so the UI didn't promise something it couldn't do
    yet.
  </details>

- Restoring a deleted email moved it out of Trash but not back into the
  Inbox — the delete path added the `TRASH` label but never explicitly
  removed `INBOX`, so there was nothing to re-add on restore.
- The Restore screen's summary for a bulk action only showed a sender
  count, not which senders — now lists up to 3 addresses inline.
- The filter bar was rendered globally and stayed visible on every view,
  including the new Restore tab where it has no function.
- Removed the filter bar from Mark as Read specifically as well, since it
  wasn't providing meaningful value there.
- Timezone handling in CSV filename generation now uses UTC.
- Various correctness cleanup: missing return type annotations, closure
  variable binding in batch callbacks, mock assertions in tests, a
  boolean positional argument pattern.
- Delete and label operations ignored the date/category filters used to
  find a sender, deleting/labeling more than the filtered subset shown.
- Scans defaulted to "all mail" instead of Inbox when no category filter
  was set, inflating counts with archived mail.
- No authentication existed on any action endpoint or the UI itself —
  added a shared-password login gate and bound Docker ports to
  `127.0.0.1` instead of all interfaces.
- Signing out left the "Sign in" button permanently stuck showing
  "Signing in..." — a successful sign-in never reset the button state a
  previous click had set.
- The custom date-range picker (loaded from a CDN) failed silently with
  no calendar widget if that script didn't load — added a plain-date-
  input fallback and error handling.
- **Sign-in could get permanently stuck if the OAuth consent tab was
  closed before finishing.**
  <details>
  <summary>Details</summary>

  The default sign-in flow had no timeout at all; the custom-port flow's
  own callback server had a 300s timeout, longer than the frontend's
  120s polling budget. Both paths are now unified onto the same
  manually-managed, 90-second-timeout callback server, and the sign-in
  endpoint surfaces a genuine failure instead of always reporting "still
  signing in" regardless of what actually happened. Also added: a live
  countdown on the sign-in button (time remaining, not elapsed time
  counting up), and sign-in errors now show as a toast instead of a
  blocking alert.
  </details>

- **Investigated, not conclusively resolved during this window: very
  small scans could occasionally undercount, independent of quota
  exhaustion.** A sender with 2 real messages sometimes showed only 1;
  repeated scans caught a different one of the two each time. Later
  attributed to the same class of Gmail-side rate-limiting behavior the
  quota-awareness work above addresses, rather than a separate bug.

## [1.0.0] - 2024-11-29

Initial release (pre-fork, upstream).

### Added
- Bulk unsubscribe functionality with one-click support.
- Delete emails by sender with bulk operations.
- Mark emails as read in bulk.
- Smart filtering options (age, size, category, sender, label).
- Docker support for all platforms.
- Gmail API integration with batch requests.
- Privacy-first architecture (runs 100% locally).
- Gmail-style user interface.
- Label management (create, apply, remove).
- Archive emails functionality.
- Mark emails as important/unimportant.
- Download emails as CSV export.

[0.1.0]: https://github.com/Jatin17Solanki/gmail-cleaner/releases/tag/v0.1.0
[0.1.1]: https://github.com/Jatin17Solanki/gmail-cleaner/releases/tag/v0.1.1
[0.1.2]: https://github.com/Jatin17Solanki/gmail-cleaner/releases/tag/v0.1.2
[Unreleased]: https://github.com/Jatin17Solanki/gmail-cleaner/compare/v0.1.2...HEAD
[1.0.0]: https://github.com/Gururagavendra/gmail-cleaner/releases/tag/v1.0.0
