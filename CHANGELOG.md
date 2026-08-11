# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- CodeRabbit AI code review integration with `.coderabbit.yaml` configuration
- Pre-commit hooks for code quality checks (ruff, bandit, trailing whitespace, etc.)
- Comprehensive type annotations throughout the codebase
- Restore-from-Trash: a permanent "Restore" sidebar tab backed by a local,
  app-scoped operation log (`./data/operations.json`), not a "restore
  everything in Gmail Trash" approach. Every successful delete, archive,
  mark-as-read, or label add/remove now writes a log entry recording exactly
  which messages it modified and how; restoring an entry reverses that exact
  change (e.g. delete → untrash, archive → re-add INBOX, mark-as-read →
  re-add UNREAD, label added → label removed) via one generic mechanism
  rather than per-action-type logic. Entries older than 30 days are pruned
  automatically, with a note on the Restore screen reflecting that window.
  Mark-important is not covered (not listed among Phase 2's target actions
  in the PRD), and the Restore screen uses the app's existing CSS rather
  than the wireframes' `design-system.css`, since adopting that system
  app-wide is Phase 3's job
- Phase 3: full UI/UX redesign onto the warm, minimal `design-system.css`
  used by the wireframes, and the target information architecture (PRD
  Section 5) - sidebar tabs Delete / Mark as read / Archive / Routines /
  Restore, a per-view slide-over filter drawer replacing the old single
  shared filter bar, and Label/Important as per-row inline icon actions on
  every sender row (Delete, Mark as read, and Archive alike) instead of a
  bulk multi-select "Organize" button group gated to the Delete tab. This
  was a real interaction rebuild, not a reskin:
  - **Unsubscribe is no longer its own tab.** It's merged into Delete: one
    scan (`scan_senders_for_delete`) now also detects each sender's
    unsubscribe link/type (widening its batch-request headers to include
    `List-Unsubscribe`/`List-Unsubscribe-Post`, same detection
    `get_unsubscribe_from_headers` already provided), surfaced as a status
    badge (`Auto`/`Open link`/`No unsubscribe link`) and an independent
    "Unsub" toggle per row, with "Unsubscribe selected" and "Delete
    selected" as two independent bottom-bar actions. The old standalone
    `scan_emails()`/`/api/scan`/`/api/results`/`scanner.js` are removed.
  - **Archive gets its own scan.** Previously `archive_emails_background`
    only ever operated on whatever senders the Delete tab had already
    scanned, using a bare `f"from:{sender} in:inbox"` query with no
    filters param at all - the #107/#104 gap CLAUDE.md's conventions call
    out, just never hit because Archive had no filter UI before now. New
    `scan_senders_for_archive()` mirrors `scan_senders_for_delete`, and
    `archive_emails_background` now takes an optional `filters` param
    routed through `build_gmail_query()`.
  - **Mark as read gets a real sender-row list.** Replaces the old
    aggregate unread-count card and blind "mark N most recent unread"
    picker with its own scan (`scan_senders_for_markread`, always scoped
    to `is:unread`) and a senders-scoped bulk action
    (`mark_emails_as_read_bulk_background`), matching Delete/Archive's
    shape. The old count-based `mark_emails_as_read`/`get_unread_count`
    and `/api/mark-read`/`/api/unread-count` are removed.
  - **Important also gets #107-scoped.** `mark_important_background` used
    a bare `f"from:{sender}"` query with no filters and no Inbox default
    at all (marking important could silently reach Trash/Spam/Archived
    mail from that sender) - now routed through `build_gmail_query()`
    like every other operation, with an optional `filters` param.
  - Two new filter keys resolve #99: `unread_only` (`is:unread`) and
    `has_attachment` (`has:attachment`), available in every view's drawer.
  - New `unsubscribe_link`/`unsubscribe_type` fields on delete-scan sender
    rows; the per-sender subject-preview cap widens from 3 to ~20 (no
    extra Gmail API calls - the batch metadata request already fetches
    these headers) to back the new expanded-row message list.
  - **Expand/collapse ships as a shell, not the full Phase 4c mechanism.**
    Per PRD Section 6 ("implement together if practical"), each row's
    chevron reveals real per-message subjects and a working copy-to-
    clipboard icon, but the eye ("view full email") icon is inert and
    bulk actions still operate on whole senders, not individual unchecked
    messages - Phase 4c adds the full-body-fetch endpoint and the
    message-ID-scoped bulk-action rework. Documented in `PROGRESS.md` so
    Phase 4c's remaining scope is explicit.
  - Old `base.css`/`layout.css`/`filters.css`/`components.css`/
    `responsive.css` are removed in favor of `design-system.css` (already
    used by `login.html`) plus a new `static/css/app.css` for what the
    wireframes don't define as reusable classes (the drawer, message
    sub-rows, the inline label-apply toolbar, toasts, responsive
    breakpoints). New `static/js/senderList.js` implements the shared
    sender-row-list shell (scan, render, expand/collapse, filter drawer,
    selection) once, used by rewritten `delete.js`/`markread.js` and new
    `archive.js`, instead of tripling near-identical code.
  - Routines appears as a real, always-visible sidebar item (present on
    every wireframe screen) showing a "coming in a future phase"
    placeholder view - Phase 4b builds the real thing.
  - Download's placement (PRD Section 10, previously unresolved) landed
    as a bulk icon button in the Delete view's bottom actions bar - the
    closest existing behavior, since it already operated on a multi-
    sender selection.
- Phase 4a: multi-account switcher. Multiple Gmail accounts can be
  authorized against the same running instance, with one active at a time
  (not a merged view) and no re-authentication needed to switch between
  already-authorized accounts.
  - New `app/services/accounts.py`: tokens are now stored per account at
    `<data dir>/tokens/<email>.json`, keyed by the account's own email
    address, with a small `accounts.json` index tracking the registered
    account list and which one is active - replacing the single shared
    `token.json` this app previously assumed. A one-time migration moves
    an existing pre-4a `token.json` into this layout the next time its
    account's email is confirmed via a Gmail profile call (same call
    `get_gmail_service()`/`check_login_status()` already made), so
    upgrading needs no manual steps.
  - `get_gmail_service()` gained an `add_new_account` flag: the normal
    sign-in path resolves and uses the active account's stored token as
    before, but "Add another account" (`add_new_account=True`) always
    forces a fresh OAuth consent screen and saves the result under a new
    email-keyed slot, never touching whichever account was already active.
  - New `switch_active_account()` flips the active-account pointer (no
    re-auth) and clears all per-account scan/result state - the same
    reset `sign_out()` already did, now shared via
    `_reset_account_scoped_state()` since switching accounts needs it too.
  - `sign_out()` now removes only the active account's token; if other
    accounts remain registered, one becomes active automatically instead
    of the whole instance logging out.
  - **Operation log / Restore is now scoped per account** (decided
    explicitly for this phase, see PROGRESS.md's Phase 3 backlog): every
    `operation_log.append_entry()` call site now tags its entry with
    `state.current_user`'s email, `list_entries()`/`find_entry()` accept an
    `account_email` filter, and `restore_operation()` resolves the active
    account before looking up an entry - so restoring can never replay a
    `batchModify` against message IDs that belong to a different mailbox.
    Pre-4a entries with no account tag are backfilled with the migrated
    account's email at the same time its legacy token is migrated, and
    remain visible (unscoped) until that happens.
  - New `app/api/accounts.py`: `GET /api/accounts` (list + active flag),
    `POST /api/accounts/switch`, `POST /api/accounts/add`.
  - Frontend: the topbar's account slot (reserved by Phase 3, previously
    just an email + "Sign out" button) is now the wireframe's account
    switcher dropdown - active account with a checkmark, other accounts to
    switch to, "Add another account," and Sign out, closing on
    click-outside/Escape like the existing filter drawer.
  - **`.gitignore` gap fixed as part of this phase**: the new `tokens/`
    directory and `accounts.json` land at the repo root (sibling to the
    legacy `token.json`) in the local non-Docker layout, which the
    existing `data/` ignore rule doesn't cover - added explicit entries
    for both before opening this PR, per CLAUDE.md's requirement to
    re-check `.gitignore` on any auth-touching change.
- Phase 4a2 (inserted before Phase 4b, per PRD Section 7): Gmail API quota
  awareness. A default-sized scan (`limit=1000`) fetches per-message
  metadata via `messages().get()` for every result - ~20,000 quota units in
  a few seconds against Gmail's 6,000-units/minute/user cap, with zero
  retry/backoff anywhere in the codebase. When Gmail started rate-limiting
  a batch mid-scan, the affected messages were silently dropped
  (`if exception: return`), producing incomplete, non-deterministic sender
  counts with no indication anything went wrong.
  - New `app/services/gmail/quota.py`: `QuotaTracker` tracks a rolling
    60-second usage window (its own `threading.Lock`, since
    `app.core.state` has no locking for a counter multiple background
    tasks can hit concurrently). `gate()` proactively blocks before a call
    would exceed the cap, surfacing a live "waiting Ns" message through
    the same `status_dict["message"]` channel every scan already polls,
    and logs a one-time warning past 50% usage (bug-detection aid, not a
    UI counter, per PRD). `execute_with_backoff()` retries a single
    `.execute()` call on a real 429 or quota-shaped 403 with exponential
    backoff - first place in the repo importing
    `googleapiclient.errors.HttpError`. `run_batched_gets()` replaces the
    batch-of-`messages().get()` loop duplicated across the delete/archive/
    mark-read scans and the CSV download - a sub-request that fails with a
    429/quota-403 is now retried once (via `request_id`-tagged
    `batch.add()`) instead of being dropped.
  - Wired into all Gmail API call sites across `app/services/gmail/*.py`
    and `auth.py`'s four `getProfile()` calls (function-local imports
    there, to avoid a circular import with `app.services.gmail`'s package
    `__init__.py`, which imports from `auth.py`). Removed the flat,
    non-adaptive `time.sleep()` "rate limiting" placeholders real gating
    now replaces.
  - Cost table follows PRD Section 7's own figures where given
    (`messages.list`=5, `messages.get`=20, `messages.batchModify`=50,
    flat); `getProfile`/`labels.*` costs are a documented assumption
    since the PRD doesn't price them and they're cheap/rare, not the
    source of exhaustion.
  - New opt-in `QUOTA_TRACE_LOGGING` env var (off by default, per PRD
    Section 7's "silent by default" design) logs a timestamped line for
    every Gmail API call the quota tracker charges - account, cost, and
    cumulative usage at that moment - so scan pacing can be diagnosed
    from server logs instead of manually counting "waiting Ns" messages in
    the UI. Uses a dedicated, self-contained logger with its own handler
    and formatter (`app/services/gmail/quota.py`'s `_trace_logger`), since
    the app has no `logging.basicConfig` anywhere and a plain
    `logger.info()` call would otherwise silently vanish (Python's
    fallback handler only shows WARNING+).
  - **Scan time estimate + quota explainer, requested by the human once
    the quota-tracking investigation above was confirmed resolved** (this
    was originally scoped as a deferred future-phase item - backlog item 8
    - but the human asked for the time-estimate piece specifically to be
    built now, in this same PR, since it directly explains the mechanism
    this phase just built). New `quota.estimate_scan_seconds(message_count)`
    computes a wall-clock estimate from the same cost model `gate()`
    already enforces (empirically matched real scans to within ~5% during
    this phase's investigation - see the confirmed-resolved entry above).
    Wired into `scan_senders_for_delete`/`_for_archive`/`_for_markread` as
    a new `estimated_seconds` field on each scan's status dict, set once
    the true message count is known (post-`list()`, not a pre-scan guess
    off the raw `limit`). Frontend (`senderList.js`) shows it as "This scan
    will take about N minutes to complete — come back around H:MM to see
    results" once a scan's estimate exceeds 30 seconds (not shown for fast
    scans), with a Tabler `ti-info-circle` icon (native `title` tooltip,
    matching the existing tooltip pattern used elsewhere in this app - no
    new tooltip component introduced) explaining the underlying Gmail
    quota math with a concrete example. The same icon/tooltip is also
    placed persistently next to the scan controls (not just tied to an
    active scan), per explicit request to "communicate as much as
    possible." Shared across Delete/Archive/Mark-as-read since all three
    use the same `sender_view` Jinja macro. New tests
    `TestEstimateScanSeconds`.

### Changed
- Updated pre-commit hook versions to latest stable releases
- Improved code formatting consistency (double quotes, trailing commas, whitespace)
- Enhanced function signatures with multiline formatting for better readability
- Normalized code style across Python, JavaScript, CSS, and HTML files

### Fixed
- **Confirmed resolved (sixth round)**: rerunning the same limit=1000 scan
  against the >1000-message inbox that originally showed 837/1000 counted
  now shows `run_batched_gets summary: requested=1000 succeeded=1000
  failed=0` - zero loss, exact match, verified with real evidence rather
  than unit tests alone. Closes the multi-round investigation into scans
  silently under-reporting; root cause was Gmail's 50-concurrent-requests-
  per-account limit (see the two rounds below), a separate constraint from
  the 6,000-units/minute budget this phase was originally built around.
- **Fifth round: `batch_size=25` produced zero failure warnings, but the
  final count (992) still didn't obviously reconcile against the
  limit=1000 requested against a >1000-message inbox.** Hand-counting
  individual chunk-fire trace lines to check for a gap turned out to be
  unreliable (retried-then-succeeded messages fire extra trace lines with
  no corresponding warning, inflating a manual count without indicating a
  real problem) - added one authoritative summary line to
  `run_batched_gets` instead (`requested=N succeeded=N failed=N`), so this
  never needs hand-counting again. Building it surfaced (and fixed) a real
  bug in the process: the summary's failure count only tracked messages
  that exhausted retries (`len(pending)`), missing messages that failed
  *immediately* as non-retryable (e.g. a genuine 403) - added a
  `permanent_failures` counter incremented at both failure paths. Caught
  by a new test (`test_logs_summary_line_with_requested_succeeded_failed_counts`)
  before it ever shipped
- **Fourth round: `batch_size=50` reduced but didn't eliminate the
  "Too many concurrent requests" 429s.** A repeat scan at the new clamp
  still showed 2 messages permanently failing with the identical error.
  Root cause: Gmail's 50-concurrent-requests limit is per **account**, not
  per application - anything else concurrently touching the same mailbox
  (another device, another browser tab, background sync) shares the same
  budget, invisibly to this app. Requesting exactly 50 left zero margin
  for that. Reduced `MAX_CONCURRENT_BATCH_SIZE` from 50 to 25, and raised
  `run_batched_gets`'s default `max_retry_passes` from 1 to 2 (3 total
  attempts) as additional resilience against activity this app can't see
  or control. New test `test_default_retries_twice_before_giving_up`;
  existing `test_still_failing_after_retry_pass_reaches_callback_as_terminal_exception`
  updated to pass `max_retry_passes=1` explicitly, since it's testing the
  terminal-failure contract, not the new default's retry count
- **Root cause of the third round's undercounting confirmed and fixed**,
  using evidence from the opt-in `QUOTA_TRACE_LOGGING` added to
  investigate it: the actual failures were real `HttpError 429`s reading
  `"Too many concurrent requests for user."` (`reason: rateLimitExceeded`)
  - **not** the 6,000-units/minute budget the previous round's fix
  targeted. Gmail enforces a *separate* limit, confirmed via Google's own
  error-handling docs: max 50 concurrent requests per user, independent of
  total unit cost. `run_batched_gets`'s `batch_size = 100` (inherited
  unchanged from the pre-Phase-4a2 code) fires 100 sub-requests
  essentially simultaneously per batch - double that ceiling - so both the
  original attempt and its retry (which reuses the same batch_size) hit
  the same wall, explaining why messages survived a retry pass and were
  still dropped. This was almost certainly a contributing cause of scan
  undercounting long before this phase existed, just never diagnosable
  without the logging this phase added. Fixed by clamping `batch_size` to
  a new `MAX_CONCURRENT_BATCH_SIZE = 50` constant - enforced as a hard
  clamp inside `run_batched_gets` itself (not just a caller convention),
  and the delete/archive/mark-read scan functions' local `batch_size`
  values updated to match explicitly. `download.py`'s existing
  `batch_size = 50` already happened to sit at the ceiling; now references
  the shared constant instead of a coincidental magic number. New test
  `TestRunBatchedGetsConcurrencyClamp` proves the clamp holds even if a
  caller asks for more
- Third human manual-testing round on the Phase 4a2 PR: after the timeout
  fix below, a limit=1000 scan against a ~1800-email inbox completed but
  only counted 837 of the 1000 fetched messages (6 wait cycles totaling
  ~4 minutes - more than the clean per-minute-budget math alone predicts).
  Root cause turned out to be the concurrent-request limit above, not the
  two things fixed while investigating (both real, independently
  justified improvements, kept regardless): (1) `run_batched_gets` had
  zero logging when a message permanently failed - added
  `logger.warning()` for both the immediate-non-retryable case and the
  exhausted-retries case; (2) `_is_retryable_http_error` only retried
  429/quota-403 - broadened to include Gmail's transient 5xx statuses
  (500/502/503/504, standard practice per Google's API client guidance).
  New test `test_transient_5xx_retries_then_succeeds`
- Second human manual-testing round on the Phase 4a2 PR: a scan with the
  default limit (1000) went through several "approaching rate limit" wait
  cycles and then failed with "Scan timed out" instead of completing.
  Root cause: `senderList.js`'s `_pollScanStatus` (and the identical
  pattern duplicated in `delete.js`'s download/bulk-delete pollers,
  `markread.js`'s bulk mark-as-read poller, and `archive.js`'s archive
  poller) gives up after `attempts > 600` polls at a 300ms interval -
  exactly 180 seconds - regardless of whether the backend operation is
  still legitimately running. A 1000-message scan from a cold quota budget
  needs ~3 wait cycles (~180s) by design once quota-aware pacing (this
  phase) replaces the old silent-drop behavior, and real network latency
  for the underlying `list()`/`batch.execute()` calls pushes that past the
  180s ceiling - so the UI gives up right around when the scan is
  legitimately still finishing. Worse, since the scan runs as a FastAPI
  background task, it keeps running server-side after the frontend gives
  up and errors, so the completed results were silently discarded the next
  time a scan was started. The UI's own limit dropdown already offers up
  to "Scan 5000" (~17 minutes under the confirmed 6,000-units/minute cap),
  so this wasn't specific to the 1000 case. Fixed by raising all five
  pollers' ceiling to a generous 30-minute sanity bound (`maxAttempts =
  6000`) rather than a tight estimate - precise per-scan time estimation
  is deferred to a future phase (see PROGRESS.md's backlog)
- Human manual-testing round on the Phase 4a2 PR: the quota tracker was a
  single global instance shared across every signed-in account, even
  though Gmail's 6,000-units/minute cap is tracked per Google account, not
  per process. Switching accounts (Phase 4a's multi-account switcher) made
  the newly-active account inherit whatever quota usage the previously-
  active account had just run up - reproduced by scanning account 2, then
  immediately hitting a false "approaching rate limit" block on switching
  to account 1, with the displayed wait time jumping unpredictably (each
  scan batch racing against the other account's leftover usage in the same
  shared window). Fixed by keying `QuotaTracker` instances per account
  email (`app/services/gmail/quota.py`'s `_tracker_for_account()`,
  resolved via `accounts.get_active_account()`) instead of one process-wide
  singleton - every call site already goes through the module-level
  `gate()`/`execute_with_backoff()`/`run_batched_gets()` wrappers, so no
  call-site changes were needed, just the routing underneath them
- Human manual-testing round on the Phase 4a PR: the topbar account chip
  stayed on the previous account's email after "Add another account"
  completed (the dropdown showed the new account checkmarked correctly,
  since it re-fetches `GET /api/accounts` fresh each time it's opened).
  Root cause: `pollStatus()` resolved as soon as `/api/auth-status`
  reported `logged_in: true`, but for this flow the *previous* account
  stays validly signed in the entire time the new consent screen is open
  in the browser - the very first poll already satisfied that condition
  with the old email, so the UI locked in there and never re-checked once
  the new account actually became active in the background. Fixed by
  having `pollStatus()` optionally wait for the active account's email to
  actually change, not just for `logged_in` to be true.
- Phase 3 manual testing against a real inbox (not synthetic data - just
  browsing, no destructive actions taken) surfaced a subject line
  containing literal `"` characters (e.g. `"the doom of ai has started"`)
  breaking out of the expanded-row message list's `data-subject` HTML
  attribute, since `escapeHtml()` only escapes what's safe for a text
  node (`<`/`>`/`&`), not attribute-value context (which also needs
  quotes escaped). Fixed by building message rows via `createElement`/
  `textContent`/property assignment throughout (`senderList.js`) instead
  of string-templating sender-controlled subject text into HTML, which
  removes the whole class of attribute-escaping bugs rather than just
  this instance
- Human manual-testing round on the Phase 3 PR found several more issues,
  fixed in the same branch per the established Phase 1/2 precedent:
  - **Root cause behind several reported-broken interactions**: the
    Tabler icon webfont was loaded from a CDN (`cdn.jsdelivr.net`), which
    was unreliable in practice - when it failed to load, every icon-only
    control (row expand/collapse chevron, per-row Label/Important icons,
    the Download button, the filter drawer's close icon) rendered with no
    visible glyph and effectively no usable click target, making them
    look broken/absent rather than just unstyled. Fixed by vendoring
    `@tabler/icons-webfont` into `static/vendor/tabler-icons/` and
    serving it locally instead of depending on a third-party CDN at
    runtime - consistent with the app's own "runs 100% locally" premise.
    Applied to both `index.html` and `login.html` (the latter had the
    same CDN dependency, predating Phase 3).
  - On the Delete view, toggling only a row's "Unsub" checkbox (without
    also checking the row's main delete checkbox) left the bottom
    actions bar hidden, making "Unsubscribe selected" unreachable for
    that selection. `_updateSelectionBar()` in `senderList.js` now shows
    the actions bar for either kind of selection, and the summary text
    distinguishes "N emails selected across M senders" from "M senders
    queued to unsubscribe" (or both, combined) depending on what's
    actually selected
  - The filter drawer had no way to close it without either applying or
    clearing filters - the only affordance was the (previously invisible,
    per the icon issue above) close icon. Added click-outside-the-drawer-
    to-dismiss and Escape-to-dismiss, neither of which touch filter state
  - Buttons and icon-only row actions had no hover/active/focus-visible
    styling at all (`design-system.css` only defines resting states) -
    added interactive feedback across `.btn`/`.btn-primary`/`.btn-danger`
    and the per-row action icons
  - Added a `title` tooltip to the unsubscribe status badge explaining
    what `Auto`/`Open link`/`No unsubscribe link` each mean - this
    explanatory copy existed on the old standalone Unsubscribe view (an
    info-note card) but was dropped when that view merged into Delete
  - An expanded row's per-message checkboxes were fully interactive but
    silently ignored by every bulk action (Phase 4c ships the mechanism
    that actually scopes actions to individually-included messages) -
    left as-is, this implied unchecking a message would exclude it, which
    wasn't true yet. Disabled them (matching the eye icon's existing
    inert treatment) with a tooltip explaining why, so the UI doesn't
    promise something it can't do yet
- Restoring a deleted email moved it out of Trash but not back into the
  Inbox (only visible in "All Mail" afterward). Root cause: the delete
  path added the `TRASH` label but never explicitly removed `INBOX`, so
  the operation log's recorded diff had nothing to re-add on restore.
  `delete_emails_by_sender` and `delete_emails_bulk_background` now
  explicitly remove `INBOX` alongside adding `TRASH`, and log that removal,
  so restoring correctly re-adds `INBOX`
- The Restore screen's summary for a bulk/multi-sender action only showed a
  sender *count* ("Deleted 2 emails · 2 senders"), not which senders -
  now lists up to 3 sender addresses inline (with a "+N more" suffix for
  larger batches)
- The shared filter bar (used by Unsubscribe/Delete to scope their scans)
  was rendered globally and stayed visible on every view, including the
  new Restore tab where it has no function. Filter bar visibility is now
  derived from the active view instead of only being toggled at
  sign-in/sign-out
- Removed the filter bar from Mark as Read specifically as well (per
  human product feedback during Phase 2 testing: filtering which unread
  emails get marked read wasn't providing meaningful value). Mark as Read
  no longer sends filter values to `/api/mark-read` - it now always acts
  on the plain unread set, scoped only by the existing count selector
- Timezone handling in CSV filename generation (now uses UTC)
- Missing return type annotations in multiple functions
- Closure variable binding in batch callback functions
- Test coverage improvements with proper mock assertions
- Boolean positional argument pattern in `mark_important_background`
- Delete and label operations ignored the date/category filters used to find
  a sender, deleting/labeling more than the filtered subset shown (#107)
- Scans defaulted to "all mail" instead of Inbox when no category filter was
  set, inflating counts with archived mail (#104)
- No authentication existed on any action endpoint or the UI itself; added a
  shared-password login gate (#109, #108) and bound Docker ports to
  `127.0.0.1` instead of all interfaces (#111)
- Signing out left the "Sign in" button permanently stuck showing
  "Signing in..." and disabled, because a *successful* sign-in never reset
  the button state set by the previous click - only certain failure paths did
- The custom date-range picker (Litepicker, loaded from a CDN) failed
  silently with no calendar widget and no error if that script didn't load;
  added a plain-date-input fallback and error handling around initialization
- The Unsubscribe scan's sender counts/date-ranges only reflect emails with
  a detected unsubscribe link (not a sender's total mail), and the Delete
  scan samples the most recent N emails across the whole mailbox rather than
  per sender - both could look inconsistent with each other or with a
  sender's true total. Added explanatory notes in the UI rather than
  changing the underlying scan behavior, which needs further investigation
- Sign-in could get permanently stuck if the OAuth consent tab was closed
  before finishing. The default (no custom port mapping) flow used
  google-auth-oauthlib's `Flow.run_local_server()`, which has no timeout at
  all; the custom-port flow had its own manually-managed callback server
  with a 300s timeout, longer than the frontend's 120s polling budget. Both
  paths are now unified onto the same manually-managed, 90-second-timeout
  callback server, and `/api/sign-in` surfaces a genuine failure (e.g. "a
  previous sign-in attempt is still pending") instead of always reporting
  `{"status": "signing_in"}` regardless of what actually happened
- The sign-in wait gave no feedback: a static "Signing in..." button with
  no indication of how long to expect, or that a "still pending" retry
  message was even time-bound. Added a live countdown on the button (time
  remaining before polling gives up, not elapsed time counting up - an
  increasing number reads as "still stuck", not "still on track"), a
  status hint pointing at the browser tab, and made the
  "previous attempt still pending" message report the actual remaining
  time (based on when that attempt started) instead of a fixed number
  regardless of how much of the window had already passed. Sign-in errors
  now show as a toast instead of a blocking `alert()`

### Known issue (documented, not fixed in this round)
- **RESOLVED by Phase 4a2** (pending that phase's PR merge - see `### Added`
  above). Scanning with a large limit (e.g. 5000) could return different
  results on repeated runs with identical filters, due to Gmail API quota
  exhaustion with no retry/backoff anywhere in the codebase. Kept here for
  history/context.
- **Distinct from the above, found during Phase 4a's PR review**: scan
  undercounting/non-determinism also happens on very small scans, where
  quota exhaustion can't be the cause. A sender with 2 real messages
  showed only 1 on a Delete-tab scan; repeated scans (no restore or other
  write action in between - ruled out by the human directly) caught a
  different one of the 2 messages each time. `git diff main` confirmed
  `scan_senders_for_delete()` (and the rest of `delete.py`'s scan/query
  logic) is untouched by Phase 4a, so this predates that phase. Root
  cause not confirmed - the leading theory is Gmail's search index
  (`messages.list(q=...)`) not being strongly consistent even independent
  of recent writes, but that's unverified. Documented per the human's
  explicit direction rather than guess-fixed, since a wrong fix here
  risks masking a real correctness issue rather than a cosmetic one

## [1.0.0] - 2024-11-29

### Added
- Initial release
- Bulk unsubscribe functionality with one-click support
- Delete emails by sender with bulk operations
- Mark emails as read in bulk
- Smart filtering options (age, size, category, sender, label)
- Docker support for all platforms
- Gmail API integration with batch requests
- Privacy-first architecture (runs 100% locally)
- Gmail-style user interface
- Label management (create, apply, remove)
- Archive emails functionality
- Mark emails as important/unimportant
- Download emails as CSV export

[Unreleased]: https://github.com/Gururagavendra/gmail-cleaner/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Gururagavendra/gmail-cleaner/releases/tag/v1.0.0
