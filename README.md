# django-helm

Django integration for Helm — logging, analytics, and more.

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
