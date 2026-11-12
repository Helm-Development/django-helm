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

Helm can ask your backend three things: how many user accounts you have, which users to leave out of analytics, and which social posts are ready to publish. This section adds all three as endpoints in your Django project.

### Install

Add the app and run its migrations:

```python
INSTALLED_APPS = [
    # ...
    'django_helm.analytics',
]
```

```bash
python manage.py migrate
```

This creates two tables, `django_helm_user_analytics` and `django_helm_exclusion_rule`.

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
| Social posts | `/helm/social_posts` |

These match the placeholders in Helm's Backend SDK tab. Leave them alone unless you must move them.

### Overriding a path

To serve an endpoint elsewhere, set `'paths'` and enter the same path in Helm:

```python
HELM_ANALYTICS = {
    # ... existing keys
    'paths': {
        'user_accounts': '/api/internal/helm/accounts',
        'analytics_exclusion_list': '/api/internal/helm/exclusions',
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

### Social posts endpoint

`/helm/social_posts` is how Helm learns about the social posts your app has generated. Helm polls it, puts a tracking link on each post, holds posts that need review, and publishes at the desired time.

**The provider.** You supply a function that returns the posts; the SDK validates and serves them.

```python
# myapp/helm.py
from django_helm.social_posts import SocialPost, facebook_link

def social_posts(since):
    """Return the posts created or changed at or after `since` (None means all)."""
    rows = GeneratedPost.objects.all()
    if since is not None:
        rows = rows.filter(updated_at__gte=since)
    return [
        SocialPost(
            id=row.public_id,
            platform='facebook',
            desired_post_time=row.post_at,
            updated_at=row.updated_at,
            campaign=row.campaign or None,
            requires_review=row.needs_review,
            content=facebook_link(row.caption, row.link),
        )
        for row in rows
    ]
```

```python
HELM_ANALYTICS = {
    # ... existing keys
    'social_posts_provider': 'myapp.helm.social_posts',
}
```

**Fields.**

| Field | Meaning |
|---|---|
| `id` | Yours. Stable and unique in the project, up to 200 characters. Helm ignores a post ID it has already accepted. |
| `platform` | `facebook`. |
| `desired_post_time` | When the post should go out. Naive datetimes are taken as UTC; Helm receives UTC. |
| `updated_at` | When the post last changed. Used for `since` and ordering. |
| `campaign` | Optional slug (letters, digits, `-`, `_`, up to 80 characters). Posts without one use the platform's one-off tracking link. |
| `requires_review` | `True` holds the post in Helm until someone approves it. Defaults to `False`. |
| `content` | `facebook_link(message, link)`. The message is at most 2,000 characters; your app must shorten longer text itself, or `InvalidSocialPost` is raised. `link` is the one URL Helm replaces with a tracking link. |

**Limits and failure.** At most 50 posts per response, oldest `updated_at` first, and the response is also cut to stay under Helm's 64 KB limit. `has_more` is set whenever posts were left out for either reason, so Helm asks again. Helm pages with `since` plus an optional `after_id` and reads the `next` cursor from the response, so many posts sharing one `updated_at` are still all delivered; your provider only ever sees `since`.

**Time zones.** `since` is an aware UTC datetime. Projects with `USE_TZ = False` get a naive UTC `since` and must return times converted to UTC, because a naive value is read as UTC.

**Rule breaks.** A post that breaks the rules raises `InvalidSocialPost` in your app. The whole response fails and no post is sent until it is fixed. Text containing a NUL character or broken Unicode (such as a lone surrogate) is refused, because Helm cannot store it.

**Changes after acceptance.** Once Helm has accepted a post, later changes to it are not picked up. Issue a new `id` for different content.

**Not configured.** The endpoint answers 400 until `social_posts_provider` is set.

### `hashed_id` is not the Helm hash

If your backend already exposes a `hashed_id` (TastySpread does), do not send it as the Helm hash. The Helm hash is `derive_user_hash(project_slug, user.pk, salt)`, with the salt from `HELM_USER_HASH_SALT`. The exclusion list uses that value.
