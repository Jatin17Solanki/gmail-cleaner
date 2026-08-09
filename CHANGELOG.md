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
