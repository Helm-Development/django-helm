# Backend SDK: user emails endpoint and mailer contact sync

**Ticket:** HELM-383 (follows HELM-313)
**Status:** approved in conversation 2026-11-08 (Rob); builder check done, answers folded in (see "Calls left to me")
**Repos:** django-helm (SDK 0.3.0), Helm (consumer), TastySpread-Backend (wiring only)

## Intent

Helm's mailer owns feed subscriptions and the preferences page that TastySpread
embeds in the mobile app, so TastySpread never duplicates subscription logic.
The contact list itself, though, was seeded by hand from TastySpread's user
table. New signups and changed addresses never reach Helm, and the list drifts.

This adds a third Helm-facing endpoint to django-helm that reports which
verified accounts appeared or changed email in a time range, and teaches Helm
to pull it hourly and merge the result into its contact set. Helm keeps owning
subscriptions and preferences; TastySpread stays the source of truth for who
has an account and what their address is.

## Goals

- A TastySpread signup that confirms their email shows up as a Helm contact
  (on the default feed) within about an hour, with no human step.
- A user who changes their email in TastySpread keeps their Helm subscriptions
  and preferences under the new address.
- A wide-range pull imports the existing user base and is safe to repeat.
- The same `hwk_` key, request envelope, error family, settings category and
  docs page as the two HELM-313 endpoints. One mechanism per direction.

## Non-goals

- Account deletion. The endpoint does not report it and Helm does nothing
  about it. The mailer list is its own consent; the unsubscribe link is the
  exit. (Rob, 2026-11-08: "take no action on account delete".)
- Any "sorry to see you go" campaign, and any tombstone of deleted users.
- Pushing anything from Helm into TastySpread. Direction stays backend → Helm.
- Replacing the generic `api_sync` email source. It stays for non-SDK backends.
- Transactional email. Helm never sends activation, password or trigger mail.

## Anti-goals (would make this a failure even if it works)

- **PII next to a hash.** Helm must never be able to associate an email, name
  or user id with an analytics `user_hash`. The user-emails response carries
  no `hashed_id`, no primary key and no analytics field; the exclusion-list
  response carries no PII. Neither repo adds a hash column to a mailer table
  or an email column to an analytics table. Anonymized demographics may be
  allowed later; identity never.
- **Mailing unverified addresses.** Only accounts whose email is confirmed are
  reported. An unconfirmed address may be a typo or someone else's.
- **Modifying contacts Helm already has.** The sync creates and renames; it
  never changes names, subscriptions, suppression or metadata of an existing
  contact.

## Constraints

- django-helm supports Django 4.2+ and the standard `AbstractUser` fields
  (`email`, `first_name`, `last_name`, `is_active`, `date_joined`). Package
  version becomes `0.3.0`; a `CHANGELOG.md` entry and README section ship with it.
- Helm's SDK transport (`apps/sdk_endpoints/services.py`) caps a response at
  64 KB and 15 s total. The endpoint pages so an initial import of any size
  fits.
- Helm's hourly cadence comes from existing DigitalOcean scheduled jobs. The
  app spec is **not** edited (apps-update replaces the whole spec). The sync
  rides the existing 15-minute `send_scheduled_emails` job with an hourly gate.
- Emails never appear in Helm logs or in `last_error`; counts only.
- TastySpread convention: new SDK apps are installable; TastySpread adds one
  INSTALLED_APPS line, bumps the pin and migrates. No TastySpread code.

## The SDK side (django-helm 0.3.0)

### New installable app `django_helm.mailer`

Label `django_helm_mailer`, separate from `django_helm.analytics` so the PII
boundary is visible in the code layout. Customers add it to `INSTALLED_APPS`
and run migrations.

One table, `django_helm_user_email_event`:

| column | type | note |
|---|---|---|
| `kind` | `activated` \| `email_changed` | |
| `email` | EmailField | the account's current address at event time |
| `previous_email` | EmailField, blank | only for `email_changed` |
| `first_name`, `last_name` | CharField, blank | snapshot at event time |
| `occurred_at` | DateTimeField, indexed | server time |

Written by a `pre_save`/`post_save` receiver pair connected in
`MailerConfig.ready()` to `get_user_model()`. `pre_save` only reads the
stored `email` and `is_active` and stashes them on the instance; `post_save`
compares and writes the event, so a save that fails at the database records
nothing. Both skip `raw` saves (fixture loads), and `pre_save` skips the
lookup entirely when `update_fields` names neither `email` nor `is_active`.
All three receivers use the save's `using` alias. Rules:

- `is_active` flips False → True on an existing row → `activated`.
- `email` changes on a row that is active before and after → `email_changed`
  with `previous_email`. (TastySpread writes `email` only after the
  verification code is accepted, so this fires once per real change.)
- New rows produce no event; `date_joined` covers them.
- Addresses are compared case-insensitively after `strip()`; a case-only
  change records nothing (Helm lower-cases every address, so there would be
  nothing for it to do).

The pair issues one `values('email', 'is_active')` lookup per user save that
may touch either field. `QuerySet.update()`, `bulk_update`, raw SQL and
`loaddata` bypass the receivers and are not recorded, and nothing before the
app was installed is recorded.

A system check registered in `ready()` (`django_helm.E001`, tag `models`)
fails when the user model lacks a concrete `email`, `is_active` or
`date_joined`. `first_name`/`last_name` are optional and read with `getattr`.
Events older than **90 days** are deleted at the start of each endpoint call
(cheap, keeps the table bounded; Helm pulls hourly).

A `post_delete` receiver on the user model deletes every event whose `email`
equals the deleted row's address (case-insensitive). Deleted accounts are not
reported, and this keeps their trail out too. It matters for TastySpread's
Sign in with Apple merge (`siwa_merge.py`): the duplicate account is renamed
to a `@invalid.tastyspread.local` placeholder, its real address is moved onto
the original account, then it is deleted. Without the cleanup Helm would see
`real → placeholder` and rename a live contact to junk. With it, only
`original → real` survives, which is the rename Helm should apply. The
endpoint additionally never reports an address whose domain ends in
`.local`, `.invalid`, `.test`, `.example` or `.localhost` (RFC 2606/6761
reserved), in either list. Apple private-relay addresses are real and are
reported.

### Endpoint `user_emails`

Default path `/helm/user_emails` (`paths.USER_EMAILS`, `DEFAULTS['user_emails']`,
`urls.VIEWS` entry). Mirrored by Helm as `SDK_PATH_USER_EMAILS`. Same
`helm_endpoint('user_emails')` decorator: POST only, `hwk_` key, JSON object
body, `query` and `project` checks, `Cache-Control: no-store`.

Request body fields beyond the envelope:

| field | required | meaning |
|---|---|---|
| `since` | yes | ISO 8601; inclusive lower bound |
| `until` | no | ISO 8601; exclusive upper bound; default now |

`parse_since` from `user_accounts` is reused for both (naive = UTC). `since`
after `until` is a `BadRequest`.

Response:

```json
{
  "project": "tastyspread",
  "generated_at": "2026-11-08T15:03:00+00:00",
  "since": "...", "until": "...",
  "accounts": [
    {"email": "a@example.com", "first_name": "A", "last_name": "B", "changed_at": "..."}
  ],
  "email_changes": [
    {"previous_email": "old@example.com", "email": "new@example.com", "changed_at": "..."}
  ],
  "has_more": false,
  "next_since": null
}
```

- `accounts` is the union, de-duplicated by lower-cased email, of
  (a) users with `is_active=True`, non-empty `email`, `date_joined` in range,
  `changed_at = date_joined`; and (b) one entry per `activated` event in
  range, taken from the user's **current** row (skipped if the user is gone
  or inactive again), `changed_at = occurred_at`. Names and email are the
  current values, never the event snapshot, except where noted below.
- `email_changes` is every `email_changed` event in range, oldest first,
  whose addresses are both reportable. An event whose new address is
  reportable but whose previous address is blank or reserved goes to
  `accounts` instead (event snapshot names, `changed_at = occurred_at`):
  there is nothing for Helm to rename, so it is a new contact.
- Ordering: both lists by `changed_at` ascending. **Paging:** at most
  `ACCOUNTS_PAGE = 150` accounts and `CHANGES_PAGE = 75` email changes per
  response. A list that is cut is cut *after* the last row that shares the
  final `changed_at`, so a page may run a little over; this guarantees
  progress when many rows share one timestamp (bulk-created users) without
  putting an id in the cursor. The page is then held under
  `PAGE_BYTES = 56 * 1024` of UTF-8 JSON (the view sends
  `ensure_ascii=False`; Helm's cap is 64 KiB) by dropping whole tie groups
  from the end of `accounts`, then of `email_changes`, never the first group
  of either list. Limitation: one tie group larger than the budget (or the
  count) is returned whole, so such a page can exceed it.
  `has_more` is true when either list was cut or trimmed and rows remain. `next_since` is the smallest,
  over the lists that were cut, of (last returned `changed_at` + 1 µs); Helm
  re-asks with `since = next_since` and the same `until`. Rows of the other
  list at or after `next_since` are sent again; the merge is idempotent so
  this is harmless. When neither list is cut, `has_more` is false and
  `next_since` is null.
- Fields never present: ids, `hashed_id`, `username`, staff flags, anything
  from `django_helm.analytics`. A test asserts the exact key set.

### When `django_helm.mailer` is not installed

`django_helm/urls.py` adds the `user_emails` route only when
`apps.is_installed('django_helm.mailer')`, and imports the mailer views
lazily inside that branch, so upgrading to 0.3.0 never breaks a backend that
has not added the app. Helm then gets HTTP 404 from the backend; Helm's
mailer sync maps `BadStatus:404` on this endpoint to the friendly message
"Your backend does not serve the user emails endpoint yet. Add
django_helm.mailer to INSTALLED_APPS and run migrations." (the docs page
repeats it under Troubleshooting).

### Docs

README gains a "User emails endpoint" section under "Backend endpoints for
Helm" (install line, table row for the default path, the override key
`'user_emails'`, the PII rule, the 90-day purge). CHANGELOG 0.3.0.

## The Helm side

### Settings

`SDK_PATH_USER_EMAILS` joins `SDK_PATH_PLACEHOLDERS` and the `sdk` registry
category with the same label/description shape as the other two paths. No
data migration (no legacy setting to fold in).

### Models (`apps/mailer`)

- `EmailSource.SourceType.BACKEND_SDK = 'backend_sdk'`. One row per project,
  `name='Backend SDK'`, created on first sync with `get_or_create`. `api_url`
  and `api_auth_setting_key` stay empty; the SDK settings are the config.
- `MailerSdkSyncStatus` (one-to-one with Project): `last_attempt_at`,
  `last_success_at`, `last_error` (class name plus `:<status>`, never a body,
  never an address), `last_count` (accounts received), `cursor`
  (the `until` of the last successful sync, nullable).
- `SubscriptionEvent.ActionSource` gains `SDK_SYNC = 'sdk_sync'` so the
  history shows where a subscription came from.

### Service `apps/mailer/sdk_sync.py`

`sync_user_emails(project, *, since=None, until=None, now=None) -> SyncResult`

- `SyncResult(created, renamed, unchanged, skipped_changes, pages, skipped,
  error, message)` in the style of `apps.analytics.services.sdk_sync`.
- Range: `until = now`; `since` = explicit argument, else
  `status.cursor - OVERLAP` (`OVERLAP = 1 hour`), else `now - 2 days` for a
  project that has never synced (the initial import is an explicit wide pull,
  see below).
- Calls `call_sdk_endpoint(project, 'SDK_PATH_USER_EMAILS', 'user_emails',
  {'since': ..., 'until': ...})`, following `has_more`/`next_since` with the
  same `until`. After each page is merged and committed, `cursor` advances to
  that page's `next_since` (or to `until` on the last page), so an
  interrupted sync resumes where it stopped instead of starting over. A
  `max_pages` argument bounds one call: the job passes `JOB_MAX_PAGES = 20`
  (worst case about five minutes at the 15 s deadline) and leaves the rest
  for the next run with `error=''` and `has_more=True` in the result; the
  command and the MCP tool default to `MAX_PAGES = 400` and record
  `error='TooManyPages'` beyond that.
- Merge, inside one transaction per page:
  1. **Email changes first.** For each `(previous_email, email)` lower-cased,
     after checking the new address with Django's `EmailValidator` and the
     reserved-domain rule above (a failure counts as `skipped_changes`):
     contact with `previous_email` exists and none with `email` → rename the
     contact (`email` field only; `updated_at` moves; nothing else changes),
     count `renamed`. Neither exists → create a contact under `email` with
     the event's name snapshot, exactly as an account entry would, count
     `created`. Both exist → `skipped_changes`, log at info with counts only
     (merging two subscription histories is out of scope).
  2. **Accounts.** For each entry: `Contact.objects.get_or_create(project,
     email=lower)`; created → names from the entry, subscribe to the default
     feed with `SubscriptionEvent(action_source='sdk_sync')`, count `created`;
     existing → count `unchanged`, touch nothing on the contact. Either way
     `ContactSource.get_or_create(contact, source=backend_sdk_source)` so the
     contact detail page shows the provenance.
- Status: `last_attempt_at = now` is written **before** the first request so
  a slow sync is not started twice by overlapping job runs. A completed sync
  writes `last_success_at = now`, `last_count` (accounts received this sync),
  `cursor = until`, `last_error=''`. An `SdkEndpointError` writes
  `last_error` and leaves `cursor` at the last committed page (the next run
  re-covers the gap). `NotConfigured` returns `skipped=True` and writes no
  row, as the analytics sync does.

`sync_due(now=None) -> list[tuple[slug, SyncResult]]` runs
`sync_user_emails` for every project whose `MailerSdkSyncStatus` is missing
or has `last_attempt_at` older than `DUE_AFTER = 55 minutes`, skipping
projects whose SDK URL/key are unset without writing status. Each project's
exceptions are caught and logged so one backend cannot block the rest.

### Where it runs

- `send_scheduled_emails` (every 15 min on DigitalOcean) calls `sync_due()`
  **before** `process_scheduled_emails()`, each in its own try/except. The
  55-minute gate makes the sync hourly without touching the app spec. The
  `JOB_MAX_PAGES` budget caps the delay to scheduled sends at about five
  minutes in the worst case; a routine hourly delta is one page.
- New management command `mailer_sync_user_emails [--project SLUG] [--since
  ISO] [--until ISO]` for the initial import and manual backfills. Prints one
  line per project in the `_sync_line` style.

### MCP

- New tool `sync_user_emails(project, since=None, until=None)` (module
  `marketing`): runs the sync now and returns the `SyncResult` as a dict plus
  the status block.
- `get_sources` adds a `sdk_sync` block (`configured_url`, `last_attempt_at`,
  `last_success_at`, `last_error`, `last_count`, `cursor`) when the project
  has an SDK URL configured, mirroring `list_analytics_exclusions`.

### Dashboard

The contacts list page shows one status line under the header when the
project has an SDK URL: "Synced from your backend 12 minutes ago · 412
contacts" or "Backend sync failed: <friendly message from the error family>".
No new page.

### Docs page

`docs/integrations/user_accounts_webhook.html` gains an H2 "User emails
endpoint" after the exclusion-list material: what Helm asks for, the response
shape, the PII rule in a Warning callout, the 90-day event purge, the import
command, and two Troubleshooting entries (no contacts arriving → the app is
not in INSTALLED_APPS or no one has activated yet; `TooManyPages`).

## TastySpread wiring (TAS ticket, after both PRs merge)

- Pin `django-helm` to `v0.3.0`; add `'django_helm.mailer'` to
  `INSTALLED_APPS`; migrate (pre-deploy job). No code.
- Set nothing new in Helm; the default path applies.
- Initial import from Helm: `sync_user_emails(project='tastyspread',
  since='2020-01-01T00:00:00Z')` via MCP (or the command). Existing
  hand-seeded contacts come back `unchanged` and gain a Backend SDK provenance
  link; everyone else is created on the default feed.
- Verify: `get_sources` shows the Backend SDK source and a fresh
  `last_success_at`; the contacts page status line is green; the next hourly
  run reports a small delta.

## Decisions made in conversation

- Changes-since with an explicit range, so Helm can widen the window on
  demand (Rob).
- Helm merges into a set: no duplicates, existing contacts untouched (Rob).
- No action on deletion; deletions not reported (Rob, after discussion).
- No `hashed_id` or any PII-to-hash link, ever (Rob).

## Calls left to me (change if wrong)

- Only **verified** (`is_active=True`) accounts are reported. Earlier I had
  guessed "include unactivated"; deliverability and consent argue otherwise.
- New contacts land on the default feed, exactly as the hand seeding did.
- 90-day event retention in the SDK, 1-hour overlap, 55-minute due gate,
  200 accounts and 100 changes per page, tie rows kept together and
  `next_since` = last + 1 µs (no ids in the cursor).
- Deleted users take their events with them (`post_delete`), which is what
  makes the Sign in with Apple merge come out right; reserved domains
  (`.local` etc.) are never reported; Apple private-relay addresses are.
- Missing `django_helm.mailer` means no route (404) and a friendly Helm
  message, never a broken backend.
- Email changes where neither address is known create the contact; where
  both are known, skip.
- The job-driven sync spends at most 20 pages per run and resumes from the
  committed cursor; the command and MCP tool may run 400.
- Riding the 15-minute email job rather than editing the DigitalOcean spec.
- Email-change entries where both or neither address exists in Helm are
  skipped, not merged. Merging two contacts' subscription histories is a
  separate design if it ever matters.

## Left to the builder

- Exact dataclass field names beyond those listed; test file layout
  (`tests/mailer/` in the SDK, `apps/mailer/tests/test_sdk_sync.py` in Helm).
- Friendly-message mapping for the dashboard status line (reuse the
  `user_accounts` tile's wording where it fits).
- Whether `sync_due` lives in `sdk_sync.py` or the command module.
