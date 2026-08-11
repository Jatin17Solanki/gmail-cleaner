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

### Changed
- Updated pre-commit hook versions to latest stable releases
- Improved code formatting consistency (double quotes, trailing commas, whitespace)
- Enhanced function signatures with multiline formatting for better readability
- Normalized code style across Python, JavaScript, CSS, and HTML files

### Fixed
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
- Scanning with a large limit (e.g. 5000) can return different results on
  repeated runs with identical filters. Root cause: Gmail API quota
  exhaustion - `messages.get()` costs 20 quota units and a 5000-email scan
  can issue up to 100,000 units of calls against a 6,000/minute/user limit,
  with no retry/backoff anywhere in the codebase, so which specific
  requests get rate-limited (and silently dropped from the count) varies
  run to run. Fixing this properly means building the quota-awareness
  system already specified in the PRD (Section 7): a rolling 60s usage
  counter, proactive blocking before exceeding the limit, and reactive
  429 backoff. Deferred as its own piece of work rather than folded into
  this phase

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
