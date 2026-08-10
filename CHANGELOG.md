# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- CodeRabbit AI code review integration with `.coderabbit.yaml` configuration
- Pre-commit hooks for code quality checks (ruff, bandit, trailing whitespace, etc.)
- Comprehensive type annotations throughout the codebase

### Changed
- Updated pre-commit hook versions to latest stable releases
- Improved code formatting consistency (double quotes, trailing commas, whitespace)
- Enhanced function signatures with multiline formatting for better readability
- Normalized code style across Python, JavaScript, CSS, and HTML files

### Fixed
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
  message was even time-bound. Added a live elapsed-time counter on the
  button, a status hint pointing at the browser tab, and made the
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
