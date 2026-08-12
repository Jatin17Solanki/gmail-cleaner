# OAuth Flow & Gmail Cleaner Architecture Guide

This document explains how OAuth 2.0 works and how this fork of Gmail Cleaner
uses it to access Gmail, plus a tour of the current backend/frontend
architecture. It's aimed at contributors reading the code for the first
time, not end users setting the app up (see [`README.md`](README.md) for
setup instructions).

> This file describes the app as it exists today, after several rounds of
> fixes and features on top of the original
> [Gururagavendra/gmail-cleaner](https://github.com/Gururagavendra/gmail-cleaner)
> fork base — multi-account support, a login gate, quota-aware batching,
> Restore-from-Trash, Routines, and per-email preview. If you're reading an
> older mirror of this file, some of the code snippets below (single-account
> `token.json`, `scan_emails()`, `/api/scan`) describe the pre-fork app and
> no longer match this repo.

## Table of Contents
1. [OAuth 2.0 Flow Explained](#oauth-20-flow-explained)
2. [How This App Uses OAuth](#how-this-app-uses-oauth)
3. [The Login Gate (a Separate Layer)](#the-login-gate-a-separate-layer)
4. [Application Architecture](#application-architecture)
5. [Gmail API Operations & Quota Awareness](#gmail-api-operations--quota-awareness)
6. [Feature Walkthrough](#feature-walkthrough)

---

## OAuth 2.0 Flow Explained

### What is OAuth 2.0?

OAuth 2.0 is an **authorization framework** that lets an application access
a user's data (here, Gmail) **without** ever seeing the user's Google
password. The user grants permission through Google's own login/consent
page; Google then hands the app a token it can use for API calls.

### The OAuth 2.0 Flow (Step by Step)

```
┌─────────┐         ┌──────────┐         ┌──────────┐         ┌─────────┐
│  User   │         │  App     │         │  Google  │         │  Gmail  │
│ Browser │         │ (Server) │         │  OAuth   │         │   API   │
└────┬────┘         └────┬─────┘         └────┬─────┘         └────┬────┘
     │ 1. Click "Sign In" │                    │                    │
     │──────────────────>│                    │                    │
     │                   │ 2. Build OAuth URL  │                    │
     │                   │    (client_id,      │                    │
     │                   │     redirect_uri,   │                    │
     │                   │     scopes)         │                    │
     │ 3. Redirect/open   │                    │                    │
     │    Google login    │<───────────────────                    │
     │<───────────────────                    │                    │
     │ 4. User logs in &  │                    │                    │
     │    grants access   │                    │                    │
     │───────────────────────────────────────>│                    │
     │                   │                    │ 5. Auth code issued │
     │ 6. Redirect w/ code │                    │                    │
     │<───────────────────────────────────────│                    │
     │ 7. Browser hits    │                    │                    │
     │    callback URL    │                    │                    │
     │──────────────────>│                    │                    │
     │                   │ 8. Exchange code    │                    │
     │                   │    for tokens       │                    │
     │                   │───────────────────>│                    │
     │                   │ 9. access_token +   │                    │
     │                   │    refresh_token    │                    │
     │                   │<────────────────────│                    │
     │                   │ 10. Save token,     │                    │
     │                   │     keyed by email  │                    │
     │                   │ 11. Call Gmail API  │                    │
     │                   │─────────────────────────────────────────>│
     │                   │ 12. Email data      │                    │
     │                   │<─────────────────────────────────────────│
     │ 13. UI updates     │                    │                    │
     │<───────────────────                    │                    │
```

### Key OAuth Concepts

#### 1. `credentials.json` — your Google Cloud OAuth client

Downloaded from **your own** Google Cloud project (README's "Get Google
OAuth Credentials" section) — this repo never ships one. It comes in one of
two shapes depending on which client type you created:

```json
// Desktop app credentials (local Python, no Docker)
{ "installed": {
    "client_id": "...apps.googleusercontent.com",
    "client_secret": "...",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "redirect_uris": ["http://localhost"]
} }
```

```json
// Web application credentials (Docker / remote server)
{ "web": {
    "client_id": "...apps.googleusercontent.com",
    "client_secret": "...",
    "redirect_uris": ["http://localhost:8767/"]
} }
```

`app/services/auth.py::_get_credentials_path()` reads whichever shape is
present; `is_web_auth_mode()` picks the OAuth flow variant based on which
key (`installed` vs `web`) is in the file, not on whether Docker is
detected — a "Web application" credential forces the manual-URL flow even
when run locally, and vice versa.

**This file is what links a running instance of the app to a specific
Google Cloud project** — nothing about which git remote you cloned or
forked from matters here. It's gitignored and never touched by git history.

#### 2. Scopes (Permissions)
```python
scopes = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",  # delete/archive/label/mark-read all use this
]
```

#### 3. Tokens

- **Access token**: short-lived (~1 hour), used for API calls.
- **Refresh token**: long-lived, used to silently mint new access tokens
  (`app/services/auth.py::_try_refresh_creds()`).
- **Storage**: **per account**, not a single shared `token.json` — see
  next section.

---

## How This App Uses OAuth

### Multi-account token storage

Phase 4a replaced the original single `token.json` with per-account
storage:

- `app/services/accounts.py`: `<data dir>/tokens/<email>.json` holds each
  account's token; `<data dir>/accounts.json` is a small index of
  registered accounts + which one is active (`get_active_account()`/
  `set_active_account()`).
- A pre-multi-account `token.json`, if found, is migrated in place the
  first time its owning email is confirmed via a Gmail profile call
  (`accounts.migrate_legacy_token()`), not as a separate manual step.
- `app/services/auth.py::get_gmail_service(add_new_account=False)` is the
  single entry point everything else calls: with the default `False` it
  uses the active account's token (refreshing if expired); with
  `add_new_account=True` (the "Add another account" button) it always runs
  a fresh consent flow and saves the result under a *new* email-keyed slot,
  never overwriting the currently active account's token.
- `switch_active_account(email)` just flips the active pointer in
  `accounts.json` — no new OAuth round-trip, no re-consent.
- `sign_out()` removes only the active account's token and promotes another
  registered account if one exists, instead of always fully logging out.

### Two OAuth transports (Desktop vs. Docker/remote)

Which one runs is decided by `is_web_auth_mode()` (based on the
`credentials.json` shape above, see `app/services/auth.py`):

- **Desktop-style** (`_perform_oauth_flow`, `open_browser=True`): a local
  callback server opens automatically in the user's default browser.
- **Web-app-style** (Docker/remote): the same local callback server starts,
  but the browser isn't auto-opened. The authorization URL is printed to
  stdout (`docker logs ...`) **and** stashed in
  `state.pending_auth_url["url"]`, exposed via `GET /api/auth-status`'s
  `pending_auth_url` field — today the frontend (`static/js/auth.js`) only
  shows an alert telling the user to check Docker logs and doesn't render
  `pending_auth_url` as a clickable link, even though the data is already
  there. A real gap, not a hard platform limitation — worth picking up if
  the manual copy-paste step becomes a recurring pain point.

Both transports share one 90-second-timeout callback server
(`_run_oauth_callback_server`/`_wait_for_callback`) — a fix from this
fork's Phase 1, since the upstream flow could hang forever if the consent
tab was closed before finishing.

### The authentication process in code (current)

```python
# app/services/auth.py (simplified — see the real file for full detail)

def get_gmail_service(add_new_account: bool = False):
    if not add_new_account:
        token_path = accounts.resolve_active_token_path()
        if token_path and os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
            creds = _try_refresh_creds(creds, token_path) or creds
            if creds and creds.valid:
                return build("gmail", "v1", credentials=creds), None

    # No valid token for the active account (or add_new_account=True):
    # run OAuth, save under this account's own token file, register it.
    _run_oauth_and_save_token(creds_path, add_new_account=add_new_account)
    ...
```

Frontend (`static/js/auth.js::signIn()`) → `POST /api/sign-in` (runs OAuth
in a background thread) → polls `GET /api/auth-status` until
`logged_in: true` (and, for "Add another account", until the *active
email actually changes* — the previously-active account stays
`logged_in: true` the whole time a new consent screen is open).

---

## The Login Gate (a Separate Layer)

**Don't confuse this with Gmail OAuth above** — it's a completely different
mechanism, added in Phase 1:

- `app/core/security.py` + `app/core/middleware.py`: a single shared
  password (`APP_PASSWORD` env var), hashed and checked against a session
  cookie, gating **every route in the app** (including `/docs`/`/redoc`).
- This protects the *UI itself* (so the app isn't wide open to anyone who
  can reach the port it's listening on) — it has nothing to do with which
  Google account is signed in via OAuth. A single-user instance can have
  one shared app password and multiple Gmail accounts signed in and
  switchable behind it.
- `app/api/auth_gate.py`: `POST /api/login`, `POST /api/logout`.

---

## Application Architecture

### High-Level Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│                          Frontend (Browser)                            │
│  auth.js  senderList.js  delete.js  markread.js  archive.js  labels.js │
│  restore.js  routines.js  ui.js  main.js                               │
│                              │  HTTP/JSON                              │
└──────────────────────────────┼──────────────────────────────────────── ┘
                                │
┌───────────────────────────────┼─────────────────────────────────────────┐
│                         FastAPI Backend (app/main.py)                   │
│  ┌────────────┐ ┌───────────┐ ┌──────────┐ ┌───────────┐ ┌───────────┐ │
│  │ actions.py │ │ status.py │ │restore.py│ │accounts.py│ │routines.py│ │
│  │ (POST)     │ │ (GET)     │ │          │ │           │ │           │ │
│  └─────┬──────┘ └────┬──────┘ └────┬─────┘ └─────┬─────┘ └─────┬─────┘ │
│        │             │             │             │             │        │
│  auth_gate.py (login-gate endpoints) · app/core/middleware.py (session  │
│  check on every request)                                                │
│        └─────────────┴─────────────┴─────────────┴─────────────┘        │
│                                │                                        │
│  ┌─────────────────────────────┼──────────────────────────────────┐    │
│  │                      Services layer                            │    │
│  │  auth.py · accounts.py · operation_log.py · routines.py        │    │
│  │  gmail/ ─ helpers.py (query builder) · quota.py (rate limiting)│    │
│  │        ─ delete.py · archive.py · mark_read.py · labels.py     │    │
│  │        ─ important.py · unsubscribe.py · download.py           │    │
│  │        ─ restore.py · routines.py                               │    │
│  └──────────────┬──────────────────────┬──────────────┬───────────┘    │
└─────────────────┼──────────────────────┼──────────────┼────────────────┘
                   │                      │              │
          ┌────────┴────────┐   ┌─────────┴────────┐  ┌──┴───────────┐
          │ OAuth tokens     │   │ Gmail API (REST) │  │ Local state  │
          │ tokens/<email>   │   │ via googleapi-    │  │ + JSON files │
          │ .json,           │   │ client + quota.py │  │ (state.py:   │
          │ accounts.json    │   │ pacing/backoff    │  │ in-memory;   │
          └──────────────────┘   └───────────────────┘  │ operations.  │
                                                          │ json,        │
                                                          │ routines.json│
                                                          │ on disk)     │
                                                          └──────────────┘
```

### Component Breakdown

#### 1. Frontend (`static/js/`, `static/css/`, `templates/`)

Vanilla JS, server-rendered Jinja templates, no build step. `senderList.js`
is the shared shell (scan trigger/poll, sender-row render, expand/collapse,
per-message checkboxes, filter drawer, select-all) that `delete.js`/
`markread.js`/`archive.js` each configure and extend rather than
duplicating — see its module doc comment for the shared contract.
`restore.js` and `routines.js` are self-contained per-tab modules.
`ui.js` handles cross-cutting UI (tab switching, toasts).

#### 2. Backend API (`app/api/`)

| Router | Prefix | Covers |
|---|---|---|
| `actions.py` | `/api` | sign-in/out, unsubscribe, scan+bulk delete/archive/mark-read, labels, download, mark-important |
| `status.py` | `/api` | polling endpoints for every background task above |
| `auth_gate.py` | `/api` | login gate `login`/`logout` |
| `restore.py` | `/api` | list/restore operation-log entries |
| `accounts.py` | `/api/accounts` | list/switch/add accounts |
| `routines.py` | `/api/routines` | CRUD + preview/run a Routine |

#### 3. Services layer (`app/services/`)

- **`auth.py`** — OAuth flow (see above).
- **`accounts.py`** — per-account token storage/index.
- **`operation_log.py`** — the Restore-from-Trash log
  (`./data/operations.json`): every delete/archive/mark-read/label action
  appends an entry recording the exact `batchModify` diff applied, scoped
  by `account_email`; entries auto-prune after 30 days.
- **`routines.py`** — Routine CRUD (`./data/routines.json`), also
  account-scoped.
- **`gmail/helpers.py`** — `build_gmail_query()`, the **single** place
  every Gmail search query gets built (defaults to `label:INBOX` unless a
  category filter says otherwise) — see `CLAUDE.md` for why this matters
  (a pre-fork bug let delete/label actions ignore active filters and
  operate un-scoped).
- **`gmail/quota.py`** — rolling 60s usage tracker against Gmail's
  6,000-units/minute/user cap, proactive gating, retry/backoff on
  429/quota-shaped errors, and a hard 25-concurrent-request clamp on batch
  calls (see [Gmail API Operations](#gmail-api-operations--quota-awareness)
  below — this replaced flat `time.sleep()` calls that used to stand in for
  real rate limiting).
- **`gmail/delete.py` / `archive.py` / `mark_read.py`** — per-view scan
  (`scan_senders_for_*`) + bulk action functions, all accepting an
  `excluded_message_ids` param so a sender-level bulk action can skip
  individually-unchecked messages from the per-email preview.
- **`gmail/labels.py`** — label CRUD + apply/remove-to-senders.
- **`gmail/important.py`, `unsubscribe.py`, `download.py`** — smaller,
  single-purpose modules.
- **`gmail/restore.py`** — reverses one operation-log entry via a swapped
  `batchModify` call.
- **`gmail/routines.py`** — `preview_routine()` (sync, count-only) and
  `run_routine_background()` (combines every selected action into one
  `batchModify` diff, logs one operation-log entry per run).

#### 4. State (`app/core/state.py`)

In-memory (resets on restart): active scan/bulk-op status dicts polled by
the frontend, `delete_scan_filters` (the last delete-scan's filters, reused
by delete/label calls so the frontend doesn't have to resend them),
`pending_auth_url`. Durable state (tokens, operation log, routines,
account index) lives in JSON files under the data directory instead, via
the services listed above — `state.py` is not the source of truth for
anything that needs to survive a restart.

---

## Gmail API Operations & Quota Awareness

### Core call shapes

```python
# List message IDs matching a query (5 units, flat, regardless of page size)
service.users().messages().list(userId="me", maxResults=500, q=query).execute()

# Get one message's metadata (20 units, flat)
service.users().messages().get(userId="me", id=msg_id, format="metadata").execute()

# Batch many .get() calls into one HTTP request
batch = service.new_batch_http_request()
for msg_id in message_ids:
    batch.add(service.users().messages().get(userId="me", id=msg_id, format="metadata"),
              request_id=msg_id)
batch.execute()

# Apply a label diff to many messages at once (50 units, flat)
service.users().messages().batchModify(
    userId="me", body={"ids": ids, "addLabelIds": [...], "removeLabelIds": [...]}
).execute()
```

### Why quota-aware batching exists

Gmail enforces **two separate limits**, discovered the hard way during this
fork's Phase 4a2 (full investigation trail in `PROGRESS.md`):

1. **6,000 units/minute/user** — the documented cap. `quota.gate()` tracks
   a rolling 60s window and proactively waits before a call would exceed
   it, instead of firing and hoping.
2. **~50 concurrent requests/account** — separate, undocumented in most
   places, and shared with *any other* concurrent activity on that Gmail
   account (another device, tab, sync client), not just this app. One
   batch HTTP request can still bundle many sub-requests, but firing 100 of
   them at once (the pre-fix behavior) blew straight through this limit and
   silently dropped rate-limited messages from scan results with no error.
   `quota.py::MAX_CONCURRENT_BATCH_SIZE = 25` now hard-clamps every batch's
   sub-request count, with 2 retry passes for anything that still fails
   transiently. **`run_batched_gets()` processes 25 messages per HTTP call
   today, not 100** — if you're reading older docs/README wording that
   still says "100 emails per API call," that's this fix superseding it.

`quota.estimate_scan_seconds()` uses the same cost model to show a
"this scan will take about N minutes" estimate up front, instead of the UI
silently pacing through invisible waits.

---

## Feature Walkthrough

A few end-to-end traces through the current code, one per major feature —
not exhaustive, meant as a map for finding the real logic rather than a
copy you'd expect to compile.

### Scan → bulk delete → restore

1. `POST /api/delete-scan` (`app/api/actions.py`) → background task →
   `gmail/delete.py::scan_senders_for_delete()`: builds a query via
   `build_gmail_query()`, lists matching messages, fetches metadata in
   quota-paced batches, groups by sender, and (since Phase 4c) fetches an
   exact true per-sender total via `quota.fetch_true_sender_totals()`.
2. Frontend polls `GET /api/delete-scan-status` /
   `GET /api/delete-scan-results` (`senderList.js`) and renders sender
   rows; expanding a row reveals per-message subjects
   (`message_ids`/`subjects`, 1:1, paginated client-side via "Load more").
3. `POST /api/delete-emails-bulk` → `delete.py::delete_emails_bulk_background()`:
   re-queries Gmail fresh for sender + the scan's own filters (not just
   what was displayed), subtracts any `excluded_message_ids`, and issues
   `batchModify` adding `TRASH`/removing `INBOX`. On success,
   `operation_log.append_entry()` records the exact ID/label diff.
4. `GET /api/restore` / `POST /api/restore/{entry_id}` (`app/api/restore.py`)
   → `gmail/restore.py::restore_operation()` swaps the logged diff
   (`removeLabelIds`↔`addLabelIds`) and re-applies it, only deleting the
   log entry once Gmail confirms success.

### Routines

`POST /api/routines/{id}/run` → `gmail/routines.py::run_routine_background()`
combines every selected action (delete/archive/label/mark-read) into a
*single* `batchModify` diff applied once across all matched messages from
all the routine's senders — cheaper than running each action as its own
pass, and produces exactly one operation-log entry (tagged with the
routine's name as `source`), which is what makes a run undoable via
Restore with no separate undo mechanism needed.

### Multi-account switching

`GET /api/accounts` → `accounts.list_accounts()` (from `accounts.json`'s
index) → topbar dropdown. `POST /api/accounts/switch` just updates the
active pointer; nothing else in the app needs to change behavior beyond
that, since every service function resolves the active account fresh via
`accounts.get_active_account()` on each call — there's no cached
per-request "current user" object threaded through.

---

## Key Design Decisions

1. **Background tasks + polling** — scans/bulk actions can take from
   seconds to several minutes (see quota pacing above); FastAPI
   `BackgroundTasks` return `{"status": "started"}` immediately and the
   frontend polls a `/api/*-status` endpoint rather than holding one HTTP
   request open.
2. **Batch requests, quota-clamped** — one HTTP call still processes many
   messages (25 today, not 100 — see above), which matters far more for
   latency than for the unit-cost math (`messages.get` costs the same 20
   units whether batched or not; batching saves HTTP round-trips).
3. **JSON files, not a database** — operation log, routines, and the
   account index are all flat JSON under a data directory. No SQL/ORM
   anywhere; consistent with this being a single-user, locally-run tool
   where a database would be pure overhead.
4. **Per-account scoping everywhere** — token storage, the operation log,
   and Routines are all keyed by account email specifically so switching
   accounts can never replay an action (e.g. a Restore) against the wrong
   mailbox.

---

## Security Considerations

1. **Credentials storage** — `credentials.json` (OAuth client secret) and
   everything under `tokens/`/`accounts.json` (refresh tokens) are
   gitignored; never commit them. `CLAUDE.md` requires re-checking
   `.gitignore` on any PR touching auth.
2. **Token refresh** — automatic via the refresh token; no user
   interaction needed after the first sign-in for a given account.
3. **Scopes** — only `gmail.readonly` + `gmail.modify`; nothing else.
   Revocable anytime from the user's Google Account settings.
4. **The login gate is separate from Gmail OAuth** — see
   [above](#the-login-gate-a-separate-layer). Don't assume one implies the
   other when reading auth-related code.

---

## Summary

1. **OAuth**: per-account tokens under `tokens/<email>.json`, one active
   account at a time, switchable without re-consent.
2. **Login gate**: a separate shared-password layer protecting the UI
   itself, unrelated to which Gmail account is signed in.
3. **Architecture**: vanilla-JS frontend → FastAPI → a services layer
   (Gmail operations, quota pacing, operation log, routines) → Gmail API +
   local JSON state.
4. **Quota awareness**: two real Gmail limits (6,000 units/min, ~50
   concurrent requests/account) are both actively paced/clamped, not just
   assumed away.
5. **Privacy**: your own OAuth app, all processing local, nothing sent
   anywhere but Google's own API.
