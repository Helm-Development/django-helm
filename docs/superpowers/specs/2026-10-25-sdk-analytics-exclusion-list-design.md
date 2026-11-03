# django-helm backend endpoints: the universal SDK endpoint pattern, analytics exclusion list, user accounts

**Date:** 2026-10-25, revised 2026-10-30
**Repos:** `django-helm` (SDK side) and `Helm` (platform side). One feature, two PRs.
**Status:** design, builder-checked 2026-10-25; revised 2026-10-30 after the owner decided HELM-306's customer side moves into the SDK under this pattern. **Approved 2026-11-02** (auth scope: one mechanism per direction). Plans: `docs/superpowers/plans/2026-11-02-django-helm-sdk-endpoints.md`, `docs/superpowers/plans/2026-11-02-helm-sdk-exclusion-sync.md`.
**Tracking:** HELM-313 (this), HELM-306 / HELM-309 (user accounts webhook, Helm side shipped in PR #148)

## Intent

Helm already excludes internal traffic from analytics through `AnalyticsExclusion`
rows (kinds `user_hash`, `installation`, `app_version`), added one at a time through
MCP tools. Deciding *which accounts* are internal is a customer-side judgement that
lives in the customer's user table (staff flags, company email domains, test
accounts). Today that decision is made by hand: someone runs the TastySpread
`get_helm_user_hashes` tool, then pastes hashes into `add_analytics_exclusion`.

This feature moves the account-level decision into the customer's Django admin via
the django-helm SDK, and has Helm poll the customer's backend for the resulting list
of user hashes as part of the hourly user-day build. Marking a teammate's account in
the customer admin is enough; Helm picks it up within the hour and the numbers
rebuild retroactively.

## The pattern (decided 2026-10-30)

Three rules govern every endpoint django-helm exposes to Helm, now and later:

1. **The SDK registers the endpoint at `/helm/<path>`.** One constant per endpoint in
   `django_helm.paths`; `django_helm.urls` registers them all; a customer can override a
   path in settings and mirror the override in Helm project settings. Helm's project
   settings hold one backend base URL plus one path field per endpoint whose placeholder
   is the SDK default.
2. **Anything the SDK must store lives in isolated SDK-owned tables** (`django_helm_*`),
   never as columns on the customer's tables. Those tables reference the customer's
   entities only through settings-resolved references (`settings.AUTH_USER_MODEL` by
   default; a `HELM_ANALYTICS['models']` map for anything else), never hard-coded app
   labels. Dropping the SDK app leaves the customer's schema untouched.
3. **One auth flow for every Helm ↔ backend server call.** Same key, same headers, same
   constant-time check, same envelope, same error semantics. See "Auth" under 1.4: one
   mechanism per direction (`hwk_` outbound from Helm, `sas_` inbound to Helm).

HELM-306 shipped the Helm-side consumer of a hand-rolled *user accounts webhook*
(settings `USER_ACCOUNTS_WEBHOOK_URL` + `USER_ACCOUNTS_WEBHOOK_API_KEY`, POST envelope
`{"query","since","project"}`, `validate_public_http_url` guard, `project_generate_secret`).
That contract becomes the first instance of this pattern, unchanged on the wire, and the
SDK ships the endpoint so nobody hand-rolls it.

## Goals

- A customer admin can exclude (or force-include) one user from the user admin page.
- A customer admin can exclude whole classes of users by regex on a user-model field
  (default: email), with a per-rule enable switch, no code change.
- Helm fetches the list automatically on its existing hourly cadence and reconciles
  its `AnalyticsExclusion` rows with it. Removing a user from the list restores them.
- The customer's hash salt never leaves the customer backend. Helm only ever sees
  hashes.
- Adding future SDK endpoints is cheap and the pattern is universal: one shared path
  constant plus one view in the SDK, one registry entry plus one query name in Helm.
  No new keys, no new URL validators, no new transport code.
- The SDK ships the HELM-306 `user_accounts` report so customers do not hand-roll it,
  extended with staff and analytics-excluded counts.

## Non-goals

- Device (`installation`) and `app_version` exclusions. Helm owns those; the customer
  backend does not store installation ids. They stay as manual MCP actions.
- Pushing the list from the SDK to Helm. The user chose pull.
- Changing the HELM-306 User Accounts page beyond showing the two extra counts when present.
- Exposing the SDK endpoint without a token (no anonymous mode).
- A Helm dashboard page for the exclusion list. Existing MCP tools (`list_analytics_exclusions`)
  remain the read surface; a `source` column is added so synced rows are
  distinguishable there.
- Changing how `derive_user_hash` works. It stays `blake2b(project_slug:user.id, key=salt)`.

## Anti-goals (would make this a failure even if it "works")

- Helm's hourly build failing or stalling because a customer backend is down.
- A manually-added exclusion being deleted by the sync.
- A user the admin force-included still being dropped because a regex matched.
- Two regex engines disagreeing: the pattern the admin tested is not the one that runs.

---

## Part 1: django-helm (SDK)

### 1.1 Installable app

`django_helm.analytics` becomes a real Django app with models and migrations. Customers
add `'django_helm.analytics'` to `INSTALLED_APPS` and run `migrate`. Today the SDK owns
zero tables; these are the first. Keep the middleware importable without the app
installed (customers who only want hits must not be forced to migrate).

All SDK tables use `db_table = 'django_helm_<name>'`. References to customer entities go
through `settings.AUTH_USER_MODEL` (or `HELM_ANALYTICS['models'][...]` for future
non-user entities) so the SDK never names a customer app label. Pattern rule 2.

Builder's call: an `AppConfig` named `HelmAnalyticsConfig`, label `django_helm_analytics`.

### 1.2 Models

**`HelmUserAnalytics`** — one row per user, created lazily (no row means "default").

| field | type | notes |
|---|---|---|
| `user` | `OneToOneField(settings.AUTH_USER_MODEL, on_delete=CASCADE, related_name='helm_analytics')` | |
| `inclusion` | `CharField(max_length=16, choices)` default `'default'` | three-way, see below |
| `note` | `CharField(max_length=255, blank=True)` | why |
| `updated_at` | `DateTimeField(auto_now=True)` | |

`inclusion` choices and exact semantics (user's decision):

| value | label | meaning |
|---|---|---|
| `default` | Default (rules decide) | excluded only if an enabled rule matches |
| `include` | Always include | counted even if a rule matches |
| `exclude` | Always exclude | excluded regardless of rules |

**`AnalyticsExclusionRule`**

| field | type | notes |
|---|---|---|
| `pattern` | `CharField(max_length=255)` | regex |
| `field_path` | `CharField(max_length=128)` default `'email'` | Django lookup path on the user model, e.g. `email`, `username`, `profile__company` |
| `enabled` | `BooleanField(default=True)` | |
| `note` | `CharField(max_length=255, blank=True)` | shown in the Helm reason string |
| `created_at`, `updated_at` | | |

Validation in `clean()` (so the admin surfaces errors inline):

- `pattern` must compile with Python `re` AND must be accepted by the database. The
  check runs `User.objects.filter(**{f'{field_path}__regex': pattern}).exists()`
  inside a savepoint and surfaces a `ValidationError` if the DB rejects it. This closes
  the "two regex engines" anti-goal: a rule cannot be saved unless the engine that will
  run it accepts it.
- `field_path` must resolve against the user model. Use the ORM to validate: build the
  same `filter(**{f'{field_path}__regex': '.'})` and catch `FieldError`.
- Matching is case-insensitive (`__iregex`). State this in the field help text.

### 1.3 Resolution

`django_helm.analytics.exclusions.excluded_user_hashes() -> list[dict]`:

1. `excluded = {user_id: 'flag' for HelmUserAnalytics where inclusion == 'exclude'}`
2. For each enabled rule, in `id` order: `User.objects.filter(**{f'{rule.field_path}__iregex': rule.pattern})`.
   For each match not already in `excluded`, record reason `f'rule:{rule.note or rule.pattern}'`.
3. Remove every user with `inclusion == 'include'`.
4. Return `[{'user_hash': derive_user_hash(project, uid, salt), 'reason': reason}, ...]`
   sorted by `user_hash` (stable output; makes diffing in logs easy).

Users are identified by `User.pk`. The hash input is `user.id` exactly as the login
response and middleware already use it, so a hash from this endpoint equals the one Helm
has on the installation.

Rules are evaluated live on every request. No caching. Lists are small (hundreds).

### 1.4 Universal endpoint envelope (applies to every SDK endpoint)

Every SDK endpoint is a POST JSON view that:

- **Auth.** Reads the key from `Authorization: Bearer <key>` or `X-Helm-Api-Key: <key>`
  and compares it with `hmac.compare_digest` to `settings.HELM_ANALYTICS['api_key']`.
  Missing/wrong → 401, empty body. Key unset → 404 (endpoint off); `init()` warns once.
  This is HELM-306's `hwk_` key, generated in Helm project settings.

  **Decided (owner, 2026-11-02):** one *mechanism* per direction. The `hwk_` key is used for every
  Helm → backend call and the existing `sas_` token for every backend → Helm call, both set once
  in `HELM_ANALYTICS`. A single credential for both directions was considered and rejected for now
  because it would touch `apps.client_api` inbound auth.
- **Envelope.** Requires body `{"query": "<name>", "project": "<slug>"}`. `query` must
  equal the view's name and `project` must equal `HELM_ANALYTICS['project']`, else 400.
  Extra fields are per-endpoint (`since` for `user_accounts`).
- Is `@csrf_exempt`, never touches `request.user` or sessions, sets `Cache-Control: no-store`.
- Is implemented with a small decorator `@helm_endpoint('<query name>')` in
  `django_helm.endpoints` so a new endpoint is one decorated function.

`_DEFAULT_DENY` in the middleware gains `^/helm/` so Helm's own polling never shows up as
an ApiHit. (This is also why the default paths live under `/helm/`.)

**Default paths.** New module `django_helm.paths`, one constant per endpoint:

```python
USER_ACCOUNTS = '/helm/user_accounts'
ANALYTICS_EXCLUSION_LIST = '/helm/analytics_exclusion_list'
```

The Helm registry (Part 2) mirrors these as placeholders. Neither repo depends on the
other: django-helm is not distributed through public channels (TastySpread pins a GitHub
tarball), and Helm has no CI, so a cross-repo test would never run. Instead each file
carries a comment pointing at the other (`django_helm/paths.py` ↔
`apps/site_settings/registry.py`), and the SDK README lists the defaults. Design call.

**URL wiring.** `django_helm.urls` exposes `urlpatterns` built from the constants with the
leading slash stripped, so the customer's one-liner is:

```python
path('', include('django_helm.urls')),
```

Override: `HELM_ANALYTICS['paths'] = {'analytics_exclusion_list': '/custom/path'}`. The
`urls` module reads the override; the customer then enters the same custom path in
Helm's project settings. Customers may also ignore `django_helm.urls` and route the views
themselves.

### 1.4a `analytics_exclusion_list` endpoint

View `django_helm.analytics.views.exclusion_list`, query name `analytics_exclusion_list`.
Auth, envelope, CSRF and caching per 1.4. Returns:

```json
{"project": "<slug>", "generated_at": "<iso>", "count": N,
 "exclusions": [{"user_hash": "<32 hex>", "reason": "flag" | "rule:<note>"}]}
```

### 1.4b `user_accounts` endpoint (HELM-306's contract, SDK-implemented)

- View `django_helm.analytics.views.user_accounts`, query name `user_accounts`. Body
  carries `since` (ISO 8601 UTC) exactly as the HELM-306 docs page specifies. Returns the
  eight HELM-306 integers **plus**:

  | field | meaning |
  |---|---|
  | `staff_users` | `is_staff=True` |
  | `analytics_excluded_users` | `len(excluded_user_hashes())` (1.3), i.e. accounts Helm will not count |

  Helm ignores unknown fields today, so this is backward compatible with a hand-rolled
  consumer; Helm's page shows the two extra counts when present (2.7). Whether they become
  required in the documented contract is Rob's call; default here is optional.
- Default definitions (match the HELM-306 docs example): `active` = `is_active`;
  `activated` = `last_login` not null; `created_since` = `date_joined >= since`;
  `logged_in_since` = `last_login >= since`; `activated_since` = `last_login >= since`
  (identical to logged_in_since under the default definition; a product with a real
  activation timestamp overrides it).
- Override hook: `HELM_ANALYTICS['user_accounts_counter'] = 'dotted.path'` to a callable
  `(since: datetime) -> dict` returning any subset of the fields; the SDK fills the rest
  with defaults. This is how TastySpread will define "activated" by email confirmation.
- Stores nothing. If a future endpoint needs state, pattern rule 2 applies.

### 1.5 Admin

`django_helm.analytics.admin` provides:

- `HelmUserAnalyticsInline` (`StackedInline`, `max_num=1`, `can_delete=False`) for the
  customer to add to their `UserAdmin.inlines`. The `inclusion` field's `help_text` is
  the tooltip the user asked for, verbatim:

  > **Default** — Helm's exclusion rules decide (excluded only if an enabled rule matches
  > this user). **Always include** — count this user in analytics even if a rule matches
  > them. **Always exclude** — never count this user, regardless of rules. Changes reach
  > Helm on its next hourly sync and apply retroactively.

- `AnalyticsExclusionRuleAdmin`, registered automatically. `list_display`: pattern,
  field_path, enabled, matches (count, computed per row with the same `__iregex` query),
  note, updated_at. `list_editable`: enabled. Help text on `pattern`: case-insensitive,
  database regex syntax, tested against the database on save. Help text on `field_path`:
  "Django lookup path on your user model, e.g. `email`, `username`, `profile__company`."

- An admin action on the rule list: **Preview matches** → renders the first 50 matching
  users' `field_path` values so an admin can see what a pattern catches before enabling it.
  Builder's call on template details.

### 1.6 Settings

`HELM_ANALYTICS` gains:

| key | required | notes |
|---|---|---|
| `api_key` | no | the `hwk_` key generated in Helm project settings. Absent → every SDK endpoint 404s |
| `paths` | no | dict of overrides keyed by the constant's lower-case name |
| `user_accounts_counter` | no | dotted path, see 1.4b |
| `models` | no | reserved: map of entity name → dotted model label for future SDK tables that reference non-user entities |

`init()` continues to require only the existing four keys. It warns (not raises) when
`django_helm.analytics` is in `INSTALLED_APPS` but `api_key` is empty.

### 1.7 README

New section "Backend endpoints for Helm" covering: INSTALLED_APPS + migrate, urls include,
the API key from Helm project settings, the default paths table, overriding a path, the
`/helm/` deny default, then one subsection per endpoint: user accounts (fields, defaults,
counter override) and analytics exclusion list (the inline, the three-way flag semantics,
rules). Mention TastySpread's `hashed_id` is **not** the Helm hash.

### 1.8 Tests (pytest-django, existing conftest)

Add `'django_helm.analytics'` to the test `INSTALLED_APPS`. Cover: envelope (401 on bad
key via either header, 404 when key unset, 400 on wrong `query`/`project`, GET rejected),
three-way semantics (include beats rule, exclude without rule, default + rule), disabled
rule ignored, custom `field_path`, invalid pattern/path rejected in `clean()`, stable sort,
deny-list skips `/helm/` hits, URL override, user_accounts default counts and counter
override merging, every SDK table name starts with `django_helm_`.

---

## Part 2: Helm

### 2.1 Project settings: generalize HELM-306's pair into the SDK category

Rename category `user_accounts_webhook` → `sdk` ("Backend SDK") and replace its two keys:

| key | sensitive | notes |
|---|---|---|
| `SDK_BACKEND_BASE_URL` | no | e.g. `https://app.tastyspread.com`. Validator: the existing `validate_webhook_url` (https only unless DEBUG, `validate_public_http_url` guard), applied to the base. Trailing slash stripped on read. |
| `SDK_API_KEY` | yes | existing `generate_action` + `generate_prefix: 'hwk_'`. The key Helm sends on every SDK request. |
| `SDK_PATH_USER_ACCOUNTS` | no | `placeholder` = `/helm/user_accounts`. Empty → default. |
| `SDK_PATH_ANALYTICS_EXCLUSION_LIST` | no | `placeholder` = `/helm/analytics_exclusion_list`. Empty → default. |

**Data migration.** For each project with `USER_ACCOUNTS_WEBHOOK_URL` set, split it into
`SDK_BACKEND_BASE_URL` (scheme + host) and `SDK_PATH_USER_ACCOUNTS` (path, only if it
differs from the placeholder); rename `USER_ACCOUNTS_WEBHOOK_API_KEY` → `SDK_API_KEY` in
place (ciphertext unchanged). The settings shipped 2026-10-25 and no project has them
configured yet, so this is cheap; it keeps any hand-rolled endpoint working.

**SSRF guard.** Already shipped in HELM-306 as `validate_webhook_url` +
`apps.project_assets.url_safety.validate_public_http_url` (https only unless DEBUG;
rejects private, loopback, link-local, multicast, reserved; re-validated before every
request; no redirects). Reused unchanged for the base URL. `SdkSyncStatus.last_error`
stores the error class name only (`TimedOut`, `BadStatus:503`, `InvalidResponse`,
`URLRejected`), never response bodies.

**Generate action.** `project_generate_secret` shipped in HELM-306; nothing to build.

**Registry extension:** entries may carry `'placeholder': '<str>'`. The project settings
form renders it as the input's `placeholder` attribute; `get_config` is untouched (empty
still means unset).

**URL resolver and transport.** `apps/sdk_endpoints/services.py` (new app, or a module
under `apps/analytics`; builder's call) provides:

```python
def sdk_endpoint_url(project, path_key) -> str | None   # None when base URL unset
def call_sdk_endpoint(project, path_key, query, payload: dict) -> dict  # raises SdkEndpointError subclasses
```

`sdk_endpoint_url`: base = `get_config('SDK_BACKEND_BASE_URL')`; path = `get_config(path_key) or PLACEHOLDER[path_key]`;
normalize exactly one leading slash; return `base.rstrip('/') + path`.

`call_sdk_endpoint` is HELM-306's `query_user_accounts` transport, lifted out and made
generic: re-validates the resolved URL with `validate_webhook_url` at call time (DNS can
change), sends the key in both headers, `User-Agent: Helm-Webhook/1.0`, 10 s timeout, no
redirects, 64 KB cap, body `{"query": query, "project": project.slug, **payload}`, and the
same error taxonomy (`NotConfigured`, `URLRejected`, `ConnectionFailed`, `TimedOut`,
`AuthRejected`, `BadStatus`, `InvalidResponse`). `query_user_accounts` becomes a thin
wrapper that validates the integer fields (eight required, two optional).

### 2.2 `AnalyticsExclusion.source`

New column `source = CharField(max_length=16, choices=[('manual','manual'),('sdk','sdk')], default='manual')`.
Migration default `manual` covers existing rows. `list_analytics_exclusions` returns it.
`remove_analytics_exclusion` on an `sdk` row still works (manual override) but the row
returns on the next sync if the SDK still lists it; the tool's response says so when it
removes an `sdk` row.

**Manual add over a synced row (design call).** `add_exclusion` today is `get_or_create`.
When the existing row has `source='sdk'`, the call now sets `source='manual'`, overwrites
`reason` and `created_by`, and returns `created=False, pinned=True` with a note that the
row was SDK-synced and is now manual, so the sync will no longer remove it. Without this,
disabling the matching SDK rule would silently delete an exclusion a Helm admin asked for
(anti-goal 2). `source` is therefore mutable in exactly one direction: `sdk → manual`.

### 2.3 Sync service

`apps/analytics/services/sdk_sync.py`:

```python
def sync_exclusions(project, *, now=None) -> SyncResult  # added, removed, unchanged, error
```

1. `call_sdk_endpoint(project, 'SDK_PATH_ANALYTICS_EXCLUSION_LIST', 'analytics_exclusion_list', {})`.
   `NotConfigured` → `SyncResult(skipped=True)`, no log noise.
2. Any other `SdkEndpointError`, or any hash failing the existing 32-hex check →
   `SyncResult(error=<class name>)`, one `logger.warning`, no DB change.
3. Desired = `{hash: reason}` from the response. Current = `AnalyticsExclusion` rows for the
   project with `kind='user_hash', source='sdk'`.
4. Create missing rows with `source='sdk'`, `reason=f'sdk: {reason}'`, `created_by=None`.
   Delete `sdk` rows whose hash is absent. Update `reason` in place when it changed.
   Rows with `source='manual'` are never read or touched, even if the same hash appears
   in the response (the manual row simply stays; no duplicate is created because of the
   unique constraint — check existence across both sources before creating).
5. If anything was created or deleted, call `queue_backfill(project, BACKFILL_DAYS)` once
   (reuse `add_exclusion`/`remove_exclusion` is tempting, but they queue a backfill per
   row; do the writes directly and queue once).
6. Record the outcome on the project for observability: a new small model
   `SdkSyncStatus(project OneToOne, last_attempt_at, last_success_at, last_error, last_count)`.

### 2.4 Hook into the hourly build

In `analytics_build_user_days.Command.handle`, per project, call `sync_exclusions(project)`
before `rebuild_dirty_days`, inside the existing per-project try/except so a sync failure
is logged and the rebuild proceeds. Print one line per project: `synced +a -r (n total)`
or `sync skipped` / `sync failed: <err>`.

Also iterate projects that have SDK settings but no installations yet? No: keep the
existing project filter. A project with no installs has nothing to exclude.

### 2.5 MCP tools

- `sync_analytics_exclusions(project)` — runs `sync_exclusions` now and returns the
  `SyncResult` plus the same backfill note `add_analytics_exclusion` uses. Module `build`,
  requires a user. This is how an admin confirms the token and URL work without waiting an hour.
- `list_analytics_exclusions` gains `source` per row and a top-level `sdk_sync` block from
  `SdkSyncStatus` (last success, last error, configured url or null).

### 2.7 User Accounts page and docs

`apps/user_accounts` reads through `call_sdk_endpoint`. The counts partial shows
`staff_users` and `analytics_excluded_users` when the response includes them (hand-rolled
endpoints that omit them still render). The docs page `integrations/user-accounts-webhook`
gets a "Using django-helm" section at the top (install, include URLs, set the key, done);
the hand-rolled example stays for other stacks.

### 2.6 Tests

- Registry: placeholder renders; `sdk_endpoint_url` normalizes slashes and returns `None`
  without a base URL.
- Sync (mock `call_sdk_endpoint`): adds, removes, updates reason, leaves manual rows alone,
  does not create a duplicate when a manual row has the same hash, queues exactly one
  backfill per changed sync and none for an unchanged one, 500/timeout/bad-hash → no DB
  change + status recorded, skipped when unconfigured.
- Build command: a failing sync does not stop the rebuild.
- Data migration: a project with the old URL/key settings ends up with base URL, path
  override only when non-default, and the same ciphertext under `SDK_API_KEY`.
- `call_sdk_endpoint`: existing HELM-306 transport tests pass against the generic function;
  `URLRejected` when DNS resolves private at call time; no request made.
- `add_exclusion` over an `sdk` row promotes it to `manual`, returns `pinned=True`, and
  the next sync leaves it in place even when the hash is absent from the response.
- Settings form renders the placeholder; the `sdk` category appears in the sidebar.

---

## Rollout

1. django-helm PR; bump `pyproject.toml` to `0.2.0` and tag `v0.2.0`. No PyPI publish
   (the SDK is not distributed publicly yet); consumers pin the tag's GitHub tarball.
2. Helm PR (registry, source column, sync, command hook, MCP tools). Deploys on merge to main.
3. TastySpread backend: bump django-helm, add app + migrate, add the urls include and the
   admin inline, wire `HELM_WEBHOOK_API_KEY` env into `HELM_ANALYTICS['api_key']`, set a
   `user_accounts_counter` that defines activated by email confirmation. Add a first rule
   `@technecoding\.com$` on `email`. Not in this spec's scope but listed so nobody forgets it.
4. In Helm project settings for TastySpread: set base URL, generate the key, paste it into
   the TastySpread env, open the User Accounts page and run `sync_analytics_exclusions` to
   verify both endpoints.
5. Existing manual `user_hash` rows for staff can then be removed by hand; the synced rows
   replace them.

## Left to the builder

- Exact `AppConfig`/module layout inside `django_helm.analytics`.
- Preview-matches admin action template.
- Whether `SdkSyncStatus` lives in `apps/analytics/models.py` or a sibling module.
- Logging/metrics counters, as long as the anti-goals above hold.
