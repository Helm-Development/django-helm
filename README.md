# django-helm

Django integration for Helm — logging, analytics, and more.

## Logging

Ships Python logs to Helm's ephemeral OTLP sink for **non-production** environments
(PR builds, staging). Production should use Sentry — Helm logging deliberately
no-ops there.

### Setup

In a Helm-provisioned environment the four `HELM_*` vars are injected for you, so
setup is one line in `settings.py`:

```python
from django_helm.logging import init_from_env
init_from_env()
```

`init_from_env()` reads:

| Env var | Purpose |
|---------|---------|
| `HELM_INGEST_TOKEN` | Ephemeral ingest credential (`hlit_…`). Absent ⇒ logging is off. |
| `HELM_LOG_ENDPOINT` | Full URL of Helm's `/api/v1/logs`. |
| `HELM_ENVIRONMENT` | Environment label, e.g. `pr-142`, `staging`. |
| `HELM_COMMIT_SHA` | Commit identifying the build (optional). |
| `HELM_LOG_LOGGERS` | Optional comma-separated logger names to attach to (see below). |

### Capturing non-propagating loggers

By default the handler attaches to the **root** logger. That misses any logger
configured with `propagate: False` (a common Django pattern, e.g. a dedicated
`apps.authentication` logger) — its records never reach root. Attach the handler
directly to those loggers via the `loggers` argument (or the `HELM_LOG_LOGGERS`
env var, comma-separated; the argument wins):

```python
init_from_env(loggers=['apps.authentication', 'apps.api', 'django'])
```

The same handler instance is attached to root plus each named logger, and is
cleanly detached from all of them on re-init.

### Prod-gate

Logging is gated twice. Helm never mints a token for production, so
`init_from_env()` silently no-ops there. As a second line of defense, both
`init_from_env()` and the explicit `init(...)` refuse to ship — and emit one
warning — when `HELM_ENVIRONMENT` looks production-like (`prod`, `production`,
`prod-*`, or any value starting with `production`).

### Observability

```python
from django_helm.logging import metrics_snapshot
print(metrics_snapshot())   # {'log_drops_total': N}
```

The log queue is bounded (default 10,000); under pressure it drops the oldest
record and increments `log_drops_total`. Surface it as a Sentry breadcrumb or a
debug endpoint to spot back-pressure.

## Analytics

Ships passive per-request `ApiHit` records to Helm's analytics endpoints. One middleware, one daemon thread, zero database tables on your side.

### Setup

1. Generate a customer-owned hashing salt (do this once per project, then never again):

   ```bash
   openssl rand -hex 32
   ```

2. Set environment variables on your backend:

   ```bash
   export HELM_API_BASE_URL=https://helmcode.dev
   export HELM_SAS_TOKEN=sas_...
   export HELM_USER_HASH_SALT=<the hex from step 1>
   export HELM_PUBLISHABLE_KEY=pk_...    # optional, used by login responses
   export HELM_PROJECT_SLUG=<your project>
   ```

3. Add the middleware to your Django settings:

   ```python
   from decouple import config

   HELM_ANALYTICS = {
       'project': config('HELM_PROJECT_SLUG'),
       'sas_token': config('HELM_SAS_TOKEN'),
       'api_base_url': config('HELM_API_BASE_URL', default='https://helmcode.dev'),
       'user_hash_salt': config('HELM_USER_HASH_SALT'),
       'deny_paths': [],
       'allow_paths': None,
       'ip_precision': 'full',
       'batch_size': 50,
       'batch_interval_s': 5.0,
       'queue_max': 10_000,
   }

   MIDDLEWARE = [
       # ... your existing middleware
       'django.contrib.auth.middleware.AuthenticationMiddleware',
       # Helm middleware MUST come AFTER auth so request.user is resolved:
       'django_helm.analytics.middleware.ApiHitMiddleware',
   ]

   # Optional: validate config at startup
   import django_helm.analytics as helm_analytics
   helm_analytics.init()
   ```

### Salt management

- Generate once with `openssl rand -hex 32` (or any 256-bit random source).
- Store in env, never commit.
- Lose it → all historical `user_hash` joins are permanently broken. By design.
- Leak it → rotate; lose historical joins, gain a clean future.
- Salt rotation requires a process restart.

### Opt-out

The middleware honors both standard `DNT: 1` and a custom `X-Helm-OptOut: 1` header. Either skips the hit.

### Login response (optional)

To enable cross-install identity, derive a `helm_user_hash` from the user's ID on login and include it in your response:

```python
from django_helm.analytics import derive_user_hash

def login_response_payload(user):
    payload = {...}
    if settings.HELM_USER_HASH_SALT and settings.HELM_PUBLISHABLE_KEY:
        payload['helm_user_hash'] = derive_user_hash(
            settings.HELM_PROJECT_SLUG, user.id, settings.HELM_USER_HASH_SALT,
        )
        payload['helm_publishable_key'] = settings.HELM_PUBLISHABLE_KEY
    return payload
```

Mobile clients store this and send `X-Helm-User-Hash` on every direct Helm call. The salt never leaves your backend.

### Metrics

```python
from django_helm.analytics import metrics
print(metrics.snapshot())   # {'analytics_drops_total': N}
```

Useful as a Sentry breadcrumb or a debug endpoint to spot queue pressure.

## Backend endpoints for Helm

Helm can ask your backend three questions: how many user accounts you have, which users to leave out of analytics, and which verified users have new or changed email addresses. This section adds all three as endpoints in your Django project.

### Install

Add the app and run its migrations:

```python
INSTALLED_APPS = [
    # ...
    'django_helm.analytics',
    'django_helm.mailer',  # only if you want the user emails endpoint
]
```

```bash
python manage.py migrate
```

This creates three tables: `django_helm_user_analytics`, `django_helm_exclusion_rule`, and `django_helm_user_email_event` (the last only when `django_helm.mailer` is installed).

### URLs

Include the SDK's URLs once in your root `urls.py`:

```python
from django.urls import include, path

urlpatterns = [
    # ...
    path('', include('django_helm.urls')),
]
```

### API key

In Helm, open your project settings, go to the **Backend SDK** tab, and press **Generate**. Copy the `hwk_` key into your settings:

```python
HELM_ANALYTICS = {
    # ... existing keys
    'api_key': config('HELM_API_KEY'),
}
```

While `api_key` is empty, every endpoint answers 404 and `init()` logs a warning.

### Default paths

| Endpoint | Default path |
|---|---|
| User accounts | `/helm/user_accounts` |
| Analytics exclusion list | `/helm/analytics_exclusion_list` |
| User emails | `/helm/user_emails` |

These match the placeholders in Helm's Backend SDK tab. Leave them alone unless you must move them.

### Overriding a path

To serve an endpoint elsewhere, set `'paths'` and enter the same path in Helm:

```python
HELM_ANALYTICS = {
    # ... existing keys
    'paths': {
        'user_accounts': '/api/internal/helm/accounts',
        'analytics_exclusion_list': '/api/internal/helm/exclusions',
        'user_emails': '/api/internal/helm/emails',
    },
}
```

Keys you leave out keep their default path.

### The `/helm/` deny default

The middleware never records `ApiHit` rows for paths that start with `/helm/`, nor for the resolved endpoint paths. If you override a path to one under your own API, that path stops being recorded too.

### User accounts endpoint

`/helm/user_accounts` returns ten integer counts:

- `total_users`, `active_users`, `inactive_users`
- `activated_users`, `not_activated_users`
- `created_since`, `logged_in_since`, `activated_since`
- `staff_users`, `analytics_excluded_users`

By default, active means `is_active`. Activated means `last_login` is set. The `*_since` counts use `date_joined` and `last_login`.

If your product defines these differently, point `user_accounts_counter` at a callable. It receives `since` and returns a dict with any subset of the fields. Values must be non-negative integers; fields you omit keep the defaults.

```python
# myapp/helm.py
from django.contrib.auth import get_user_model

def count_accounts(since):
    User = get_user_model()
    confirmed = User.objects.filter(email_confirmed_at__isnull=False)
    result = {'activated_users': confirmed.count()}
    result['not_activated_users'] = User.objects.count() - result['activated_users']
    result['activated_since'] = confirmed.filter(email_confirmed_at__gte=since).count()
    return result
```

```python
HELM_ANALYTICS = {
    # ... existing keys
    'user_accounts_counter': 'myapp.helm.count_accounts',
}
```

### Analytics exclusion list endpoint

`/helm/analytics_exclusion_list` tells Helm which users to leave out of analytics, such as staff, testers, and demo accounts. Each entry carries the Helm `user_hash` and a reason.

**Per-user flag.** If you already have a `UserAdmin`, add the inline to it:

```python
from django_helm.analytics.admin import HelmUserAnalyticsInline

# After your existing UserAdmin class (YourUserAdmin) is defined:
YourUserAdmin.inlines = [*YourUserAdmin.inlines, HelmUserAnalyticsInline]
```

With Django's stock admin, re-register the user model with the inline:

```python
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django_helm.analytics.admin import HelmUserAnalyticsInline

User = get_user_model()
admin.site.unregister(User)

@admin.register(User)
class MyUserAdmin(UserAdmin):
    inlines = [*UserAdmin.inlines, HelmUserAnalyticsInline]
```

The flag has three values:

- `Default (rules decide)`: exclusion rules apply.
- `Always include`: the user is counted, even if a rule matches.
- `Always exclude`: the user is left out, whatever the rules say.

**Rules.** The `AnalyticsExclusionRule` admin is registered automatically. A rule has:

- `pattern`: a regular expression, matched case-insensitively. It is tested against your database when you save, so use your database's regex syntax.
- `field_path`: the user field to match. Defaults to `email`. Related fields work, for example `profile__company`.
- `enabled`: switch a rule off without deleting it.

Helm reads at most 64 KB per response, roughly 900 exclusions, so keep rules targeted. Each reason is cut to 64 characters.

Select rules in the list and run **Preview matches** to see the first 50 matching values before you rely on them.

`HELM_ANALYTICS['models']` is reserved for future tables that reference non-user entities. Leave it unset.

### User emails endpoint

`/helm/user_emails` tells Helm which verified accounts were created or activated, and which accounts changed their email address, in a time window. Helm uses it to keep your mailing list current. It is served only when `django_helm.mailer` is in `INSTALLED_APPS`; otherwise it answers 404.

**"Verified" means `is_active=True`.** The endpoint reports an account as soon as it is active. That is only correct if your accounts become active only after the user confirms their email address. If you activate accounts at signup, before confirmation, unconfirmed addresses will reach your mailing list.

**User model requirements.** Your user model must have the concrete fields `email`, `is_active` and `date_joined`. Django's default `User` and `AbstractUser` have all three. If one is missing, `manage.py check` fails with `django_helm.E001`. `first_name` and `last_name` are optional; missing names are sent as empty strings.

Helm sends a JSON body with the usual envelope plus a time window. `since` is required and inclusive. `until` is optional, exclusive, and defaults to now. Both are ISO 8601 timestamps. A request where `since` is not before `until` gets HTTP 400.

```json
{"query": "user_emails", "project": "myapp", "since": "2026-11-01T00:00:00Z", "until": "2026-11-08T00:00:00Z"}
```

The response:

```json
{
  "project": "myapp",
  "generated_at": "2026-11-08T12:00:00+00:00",
  "since": "2026-11-01T00:00:00+00:00",
  "until": "2026-11-08T00:00:00+00:00",
  "accounts": [
    {"email": "ada@example.org", "first_name": "Ada", "last_name": "Lovelace", "changed_at": "2026-11-02T09:30:00+00:00"}
  ],
  "email_changes": [
    {"previous_email": "old@example.org", "email": "new@example.org", "changed_at": "2026-11-03T14:05:00+00:00"}
  ],
  "has_more": false,
  "next_since": null
}
```

The body is UTF-8 JSON with non-ASCII characters sent as-is, not as `\u` escapes.

**Paging.** A response holds up to 150 accounts and 75 email changes, and is kept under a 56 KiB byte budget. When more remain, `has_more` is `true` and `next_since` is the `since` for the next request. Rows that share a timestamp are never split across pages. To stay under the budget, whole groups of same-timestamp rows are dropped from the end of a page and sent on the next one. One limitation: a single group of rows sharing one timestamp is always sent whole, so a page can exceed the budget, or the count limits, when many accounts share a timestamp (for example, users created in one `bulk_create`).

**How changes are recorded.** A `pre_save`/`post_save` receiver pair on your user model records events in the `django_helm_user_email_event` table. `pre_save` reads the stored row; `post_save` writes the event, so a save that fails records nothing. An `activated` event is recorded when an inactive user becomes active. An `email_changed` event is recorded when a user who is active before and after the save changes address. A change that only alters letter case records nothing. A `post_delete` receiver deletes a user's events when the user is deleted. New users are found from `date_joined`, so signup needs no event. Events older than 90 days are purged each time Helm calls the endpoint.

Only changes that go through `Model.save()` after `django_helm.mailer` is installed are recorded. `QuerySet.update()`, `bulk_update`, raw SQL and `loaddata` fixtures bypass the receivers and are not recorded. Accounts that existed before you installed the app are still reported by `date_joined` when it falls in the window, but older activations and address changes are not.

**Reserved domains.** Addresses ending in `.local`, `.invalid`, `.test`, `.example`, or `.localhost` are never returned. A change to a reserved address is skipped. A change from a blank or reserved address to a real one is reported as a new account, not as an email change.

**Privacy:** the response carries no user id and no Helm hash, on purpose. This keeps Helm from ever tying an email address to analytics, which are pseudonymous. Do not add either field, and do not join this data with the analytics tables.

### `hashed_id` is not the Helm hash

If your backend already exposes a `hashed_id` (TastySpread does), do not send it as the Helm hash. The Helm hash is `derive_user_hash(project_slug, user.pk, salt)`, with the salt from `HELM_USER_HASH_SALT`. The exclusion list uses that value.
