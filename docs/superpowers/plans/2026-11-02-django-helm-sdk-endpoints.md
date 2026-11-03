# django-helm SDK Endpoints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `django_helm.analytics` an installable Django app that serves two Helm-polled endpoints, `analytics_exclusion_list` and `user_accounts`, under one shared auth envelope, with the exclusion decision editable in the customer's Django admin.

**Architecture:** A `@helm_endpoint('<query>')` decorator in `django_helm.endpoints` owns auth, envelope checks, CSRF exemption and cache headers; each endpoint is one decorated function in `django_helm.analytics.views`. Two SDK-owned tables (`django_helm_user_analytics`, `django_helm_exclusion_rule`) reference the customer's user model only through `settings.AUTH_USER_MODEL`. `django_helm.paths` holds the default `/helm/<path>` constants that `django_helm.urls` registers and that Helm mirrors as placeholders.

**Tech Stack:** Python >= 3.10, Django 5.x, pytest + pytest-django (existing `tests/analytics/conftest.py` shim, SQLite in memory).

**Spec:** `Helm/docs/superpowers/specs/2026-10-25-sdk-analytics-exclusion-list-design.md` (Part 1 and "The pattern"). The spec lives in the Helm repo at `/Users/ryanfiust-klink/repos/Helm/Helm`; this plan is executed in `/Users/ryanfiust-klink/repos/Helm/django-helm` on a branch off `main`.

## Global Constraints

- Every SDK table is named `django_helm_<name>` via `db_table`; no column is ever added to a customer table.
- Customer entities are referenced only through `settings.AUTH_USER_MODEL`; never a hard-coded app label.
- The middleware (`django_helm.analytics.middleware.ApiHitMiddleware`) must stay importable and functional when `django_helm.analytics` is **not** in `INSTALLED_APPS`. Model imports must therefore not happen at `django_helm.analytics` package import time.
- Default paths, exact: `USER_ACCOUNTS = '/helm/user_accounts'`, `ANALYTICS_EXCLUSION_LIST = '/helm/analytics_exclusion_list'`.
- Auth: key read from `Authorization: Bearer <key>` or `X-Helm-Api-Key: <key>`, compared with `hmac.compare_digest` to `settings.HELM_ANALYTICS['api_key']`. Missing or wrong key: 401, empty body. Key unset or empty: 404. Non-POST: 405. Body not `{"query": <view name>, "project": HELM_ANALYTICS['project'], ...}`: 400.
- Every endpoint response carries `Cache-Control: no-store`; views are `@csrf_exempt` and never read `request.user` or the session.
- `derive_user_hash(project_slug, user.pk, salt)` is unchanged; hash input is the user's primary key, as the middleware already does.
- Rule matching uses the ORM `__iregex` lookup (case-insensitive, database regex engine). Validation of a pattern runs the same lookup against the database inside a savepoint.
- The `inclusion` help text in the admin inline is this string, verbatim: `**Default** — Helm's exclusion rules decide (excluded only if an enabled rule matches this user). **Always include** — count this user in analytics even if a rule matches them. **Always exclude** — never count this user, regardless of rules. Changes reach Helm on its next hourly sync and apply retroactively.`
- `init()` still requires only `project`, `sas_token`, `api_base_url`, `user_hash_salt`; it warns via `logging.getLogger('django_helm.analytics')` (does not raise) when `django_helm.analytics` is in `INSTALLED_APPS` and `api_key` is empty.
- `pyproject.toml` version becomes `0.2.0`. No PyPI publish.
- Commit messages follow the repo's existing style (short imperative subject) and end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

Inputs the spec implies but which are easy to get wrong; each has a pinned test in the owning task.

1. A request whose body is valid JSON but not an object (`[]`, `"x"`, `null`) must get 400, not a 500 from `.get` on a list. (Task 4)
2. A `since` value that is missing, not a string, or not ISO 8601 must get 400 with a short message, never a 500. A `since` with no timezone is treated as UTC. (Task 7)
3. A rule whose `field_path` crosses a relation (`profile__company`) must validate and match through the ORM, and a path to a non-text field (`id`) must be rejected in `clean()` rather than failing at request time. (Task 3)
4. A user who matches two rules appears once, with the reason from the lowest-id rule. A user with `inclusion='exclude'` and a matching rule has reason `flag`. (Task 5)
5. The `user_accounts_counter` override returning a key outside the known field set, or a non-int value, must be rejected with a 500-safe `ValueError` at import/validation time rather than leaking a bad type to Helm (Helm rejects non-int fields as `InvalidResponse`). The SDK coerces nothing: `counts()` raises `ImproperlyConfigured`, which the decorator does not catch, so the request fails as a server error instead of sending Helm a bad field. (Task 7)

---

### Task 1: Installable app, paths module, version bump

**Files:**
- Create: `django_helm/analytics/apps.py`
- Create: `django_helm/paths.py`
- Modify: `django_helm/analytics/__init__.py`
- Modify: `pyproject.toml` (version `0.1.0` → `0.2.0`)
- Modify: `tests/analytics/conftest.py` (add `'django_helm.analytics'` to `INSTALLED_APPS`, add `'api_key': 'hwk_test'` and `'paths': {}` to `HELM_ANALYTICS`)
- Test: `tests/analytics/test_app_config.py`, `tests/test_paths.py`

**Interfaces:**
- Produces: `django_helm.paths.USER_ACCOUNTS: str`, `django_helm.paths.ANALYTICS_EXCLUSION_LIST: str`, `django_helm.paths.DEFAULTS: dict[str, str]` mapping lower-case constant name (`'user_accounts'`, `'analytics_exclusion_list'`) to the default path, and `django_helm.paths.resolve(name: str) -> str` returning `settings.HELM_ANALYTICS.get('paths', {}).get(name) or DEFAULTS[name]`.
- Produces: `django_helm.analytics.apps.HelmAnalyticsConfig` with `name = 'django_helm.analytics'`, `label = 'django_helm_analytics'`, `default_auto_field = 'django.db.models.BigAutoField'`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_paths.py
from django_helm import paths

def test_default_constants():
    assert paths.USER_ACCOUNTS == '/helm/user_accounts'
    assert paths.ANALYTICS_EXCLUSION_LIST == '/helm/analytics_exclusion_list'
    assert paths.DEFAULTS == {
        'user_accounts': '/helm/user_accounts',
        'analytics_exclusion_list': '/helm/analytics_exclusion_list',
    }

def test_resolve_uses_default_when_no_override(settings):
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'paths': {}}
    assert paths.resolve('user_accounts') == '/helm/user_accounts'

def test_resolve_uses_override(settings):
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'paths': {'analytics_exclusion_list': '/custom/list'}}
    assert paths.resolve('analytics_exclusion_list') == '/custom/list'

def test_resolve_unknown_name_raises():
    import pytest
    with pytest.raises(KeyError):
        paths.resolve('nope')
```

```python
# tests/analytics/test_app_config.py
from django.apps import apps

def test_app_is_installed_with_expected_label():
    cfg = apps.get_app_config('django_helm_analytics')
    assert cfg.name == 'django_helm.analytics'

def test_package_init_does_not_import_models():
    # Customers who only use the middleware never run migrate; the package must import without models.
    import inspect
    import django_helm.analytics
    lines = [l.strip() for l in inspect.getsource(django_helm.analytics).splitlines()]
    assert not any(l.startswith(('from .models', 'from . import models', 'from .views', 'from .admin')) for l in lines)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/ryanfiust-klink/repos/Helm/django-helm && .venv/bin/pytest tests/test_paths.py tests/analytics/test_app_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'django_helm.paths'` and `LookupError: No installed app with label 'django_helm_analytics'`.

- [ ] **Step 3: Implement**

`django_helm/paths.py`: the two constants, `DEFAULTS`, `resolve(name)`. Module docstring must contain the pointer comment: `Mirrored by Helm in apps/site_settings/registry.py (SDK_PATH_* placeholders). Change both.`

`django_helm/analytics/apps.py`: `HelmAnalyticsConfig(AppConfig)` with the attributes in Interfaces.

`django_helm/analytics/__init__.py`: add `default_app_config`-free discovery (Django 5 finds `apps.py` automatically). Do **not** import models here. Extend `init()`: after the existing required-keys check, `if 'django_helm.analytics' in settings.INSTALLED_APPS and not cfg.get('api_key'): _logger.warning('HELM_ANALYTICS["api_key"] is empty; every django_helm endpoint will answer 404 until it is set')`.

`tests/analytics/conftest.py`: add `'django_helm.analytics'` after `'django.contrib.auth'` in `INSTALLED_APPS`, add `'django.contrib.admin'`, `'django.contrib.messages'`, `'django.contrib.sessions'` (the admin tests in Task 8 need them); set `MIDDLEWARE` to `['django.contrib.sessions.middleware.SessionMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware']` and `TEMPLATES` to one `DjangoTemplates` backend with `APP_DIRS=True` and the `request`, `auth`, `messages` context processors; add `'api_key': 'hwk_test'`, `'paths': {}` to `HELM_ANALYTICS`. The existing middleware tests construct the middleware directly, so the new `MIDDLEWARE` list does not affect them. Set `ROOT_URLCONF = 'tests.urls'` and create `tests/urls.py` with `urlpatterns = [path('', include('django_helm.urls'))]`; until Task 6 creates `django_helm.urls`, make `tests/urls.py` `urlpatterns = []` and switch it in Task 6.

`pyproject.toml`: `version = "0.2.0"`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/ -v`
Expected: all PASS, including the pre-existing suite.

- [ ] **Step 5: Commit**

```bash
git checkout -b feature/HELM-313-sdk-endpoints
git add django_helm/paths.py django_helm/analytics/apps.py django_helm/analytics/__init__.py pyproject.toml tests/analytics/conftest.py tests/urls.py tests/test_paths.py tests/analytics/test_app_config.py
git commit -m "Make django_helm.analytics an installable app and add default endpoint paths

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `HelmUserAnalytics` model

**Files:**
- Create: `django_helm/analytics/models.py`
- Create: `django_helm/analytics/migrations/__init__.py`, `django_helm/analytics/migrations/0001_initial.py` (generated)
- Test: `tests/analytics/test_models.py`

**Interfaces:**
- Produces: `django_helm.analytics.models.HelmUserAnalytics` with fields `user` (`OneToOneField(settings.AUTH_USER_MODEL, on_delete=CASCADE, related_name='helm_analytics')`), `inclusion` (`CharField(max_length=16, choices=INCLUSION_CHOICES, default=INCLUDE_DEFAULT, help_text=INCLUSION_HELP_TEXT)` where `INCLUSION_HELP_TEXT` is the verbatim tooltip string from Global Constraints, defined as a module constant), `note` (`CharField(max_length=255, blank=True)`), `updated_at` (`DateTimeField(auto_now=True)`); constants `INCLUDE_DEFAULT = 'default'`, `INCLUDE_ALWAYS = 'include'`, `EXCLUDE_ALWAYS = 'exclude'`; `INCLUSION_CHOICES = [('default', 'Default (rules decide)'), ('include', 'Always include'), ('exclude', 'Always exclude')]`; `Meta.db_table = 'django_helm_user_analytics'`, `verbose_name = 'Helm analytics'`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/analytics/test_models.py
import pytest
from django.contrib.auth import get_user_model
from django.apps import apps

from django_helm.analytics.models import HelmUserAnalytics

pytestmark = pytest.mark.django_db
User = get_user_model()

def test_user_analytics_defaults_and_table():
    u = User.objects.create_user('a', 'a@x.com', 'p')
    row = HelmUserAnalytics.objects.create(user=u)
    assert row.inclusion == 'default'
    assert u.helm_analytics == row
    assert HelmUserAnalytics._meta.db_table == 'django_helm_user_analytics'

def test_inclusion_help_text_is_the_tooltip():
    from django_helm.analytics.models import INCLUSION_HELP_TEXT
    assert HelmUserAnalytics._meta.get_field('inclusion').help_text == INCLUSION_HELP_TEXT
    assert INCLUSION_HELP_TEXT.startswith('**Default** — ') and INCLUSION_HELP_TEXT.endswith('apply retroactively.')

def test_inclusion_choices_exact():
    assert HelmUserAnalytics.INCLUSION_CHOICES == [
        ('default', 'Default (rules decide)'),
        ('include', 'Always include'),
        ('exclude', 'Always exclude'),
    ]

def test_every_sdk_table_is_prefixed():
    for model in apps.get_app_config('django_helm_analytics').get_models():
        assert model._meta.db_table.startswith('django_helm_'), model
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/analytics/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'django_helm.analytics.models'`.

- [ ] **Step 3: Implement the model and generate the migration**

Write `django_helm/analytics/models.py` with the model in Interfaces. Then:

```bash
.venv/bin/python -c "
import django, os
from django.conf import settings
import tests.analytics.conftest as c; c.pytest_configure()
from django.core.management import call_command
call_command('makemigrations', 'django_helm_analytics')
"
```

If the shim does not configure cleanly from a script, write a tiny `tests/manage_settings.py` that mirrors the conftest `settings.configure(...)` call and run `DJANGO_SETTINGS_MODULE=tests.manage_settings .venv/bin/django-admin makemigrations django_helm_analytics`. Confirm the migration has `migrations.swappable_dependency(settings.AUTH_USER_MODEL)` in `dependencies`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/analytics/test_models.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add django_helm/analytics/models.py django_helm/analytics/migrations tests/analytics/test_models.py tests/manage_settings.py
git commit -m "Add HelmUserAnalytics per-user inclusion flag

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `AnalyticsExclusionRule` model with `clean()` validation

**Files:**
- Modify: `django_helm/analytics/models.py`
- Create: `django_helm/analytics/migrations/0002_analyticsexclusionrule.py` (generated)
- Test: `tests/analytics/test_models.py` (append)

**Interfaces:**
- Produces: `django_helm.analytics.models.AnalyticsExclusionRule` with fields `pattern` (`CharField(max_length=255)`), `field_path` (`CharField(max_length=128, default='email')`), `enabled` (`BooleanField(default=True)`), `note` (`CharField(max_length=255, blank=True)`), `created_at` (`auto_now_add`), `updated_at` (`auto_now`); `Meta.db_table = 'django_helm_exclusion_rule'`, `ordering = ['id']`.
- Produces: `AnalyticsExclusionRule.matching_users(self) -> QuerySet` = `get_user_model().objects.filter(**{f'{self.field_path}__iregex': self.pattern})`.
- Produces: `AnalyticsExclusionRule.clean(self) -> None` raising `django.core.exceptions.ValidationError` keyed by field: `{'pattern': ...}` when `re.compile(pattern)` fails or when the database rejects `filter(**{f'{field_path}__regex': pattern}).exists()` (run inside `transaction.atomic()` so the failed statement does not poison the outer transaction, catching `django.db.Error`); `{'field_path': ...}` when `filter(**{f'{field_path}__regex': '.'})` raises `django.core.exceptions.FieldError`, or when the resolved field is not a `CharField`/`TextField`/`EmailField`/`SlugField` (resolve via `queryset.query.resolve_ref` is overkill: walk `field_path.split('__')` through `model._meta.get_field` and `related_model`, catching `FieldDoesNotExist`).
- Help text (exact): `pattern`: `Case-insensitive regular expression in your database's syntax. Tested against the database when you save.`; `field_path`: `Django lookup path on your user model, e.g. email, username, profile__company.`

- [ ] **Step 1: Write the failing tests**

```python
# tests/analytics/test_models.py (append)
from django.core.exceptions import ValidationError
from django_helm.analytics.models import AnalyticsExclusionRule

def test_rule_defaults_and_table():
    r = AnalyticsExclusionRule(pattern='@acme\\.com$')
    r.full_clean()
    r.save()
    assert r.field_path == 'email' and r.enabled is True
    assert AnalyticsExclusionRule._meta.db_table == 'django_helm_exclusion_rule'

def test_rule_matching_is_case_insensitive():
    a = User.objects.create_user('a', 'Alice@ACME.com', 'p')
    User.objects.create_user('b', 'bob@other.com', 'p')
    r = AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$')
    assert list(r.matching_users()) == [a]

def test_clean_rejects_bad_regex():
    with pytest.raises(ValidationError) as exc:
        AnalyticsExclusionRule(pattern='(unclosed').full_clean()
    assert 'pattern' in exc.value.message_dict

def test_clean_rejects_unknown_field_path():
    with pytest.raises(ValidationError) as exc:
        AnalyticsExclusionRule(pattern='x', field_path='no_such_field').full_clean()
    assert 'field_path' in exc.value.message_dict

def test_clean_rejects_non_text_field_path():
    with pytest.raises(ValidationError) as exc:
        AnalyticsExclusionRule(pattern='1', field_path='id').full_clean()
    assert 'field_path' in exc.value.message_dict

def test_clean_accepts_relation_path():
    # auth.User has groups__name; a relation path must validate and match.
    from django.contrib.auth.models import Group
    g = Group.objects.create(name='Staff Testers')
    u = User.objects.create_user('a', 'a@x.com', 'p'); u.groups.add(g)
    User.objects.create_user('b', 'b@x.com', 'p')
    r = AnalyticsExclusionRule(pattern='^staff', field_path='groups__name')
    r.full_clean(); r.save()
    assert list(r.matching_users()) == [u]

def test_clean_survives_db_rejection_inside_transaction():
    # After a DB-rejected pattern, the connection must still be usable.
    with pytest.raises(ValidationError):
        AnalyticsExclusionRule(pattern='[z-a]').full_clean()   # invalid range for most engines
    assert User.objects.count() == 0
```

If SQLite accepts `[z-a]`, swap in a pattern the test DB rejects; `pytest -x` will tell you. The point of the last test is that a rejected statement inside `clean()` does not break the surrounding transaction.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/analytics/test_models.py -v`
Expected: the new tests FAIL with `ImportError: cannot import name 'AnalyticsExclusionRule'`.

- [ ] **Step 3: Implement the model, `matching_users`, `clean`; generate the migration**

Same `makemigrations django_helm_analytics` command as Task 2 Step 3.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/analytics/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add django_helm/analytics/models.py django_helm/analytics/migrations tests/analytics/test_models.py
git commit -m "Add AnalyticsExclusionRule with database-checked regex validation

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Universal endpoint decorator

**Files:**
- Create: `django_helm/endpoints.py`
- Test: `tests/test_endpoints.py`

**Interfaces:**
- Produces: `django_helm.endpoints.helm_endpoint(query: str)` decorator. The wrapped function has signature `fn(request, body: dict) -> dict` and the decorator returns a Django view `(request) -> JsonResponse`. Behavior, in order: non-POST → 405 (`HttpResponseNotAllowed(['POST'])`); `settings.HELM_ANALYTICS.get('api_key')` falsy → 404 empty; presented key (Bearer or `X-Helm-Api-Key`) missing or `not hmac.compare_digest(presented, configured)` → 401 empty; body not parseable as a JSON object → 400 `{"error": "body must be a JSON object"}`; `body.get('query') != query` → 400 `{"error": "query must be '<query>'"}`; `body.get('project') != settings.HELM_ANALYTICS['project']` → 400 `{"error": "unknown project"}`; otherwise `JsonResponse(fn(request, body))` with status 200. A `ValueError` raised by `fn` → 400 `{"error": str(exc)}`. Every response, including errors, has `Cache-Control: no-store`. The view is wrapped in `csrf_exempt`.
- Produces: `django_helm.endpoints.presented_key(request) -> str | None` (helper; Bearer wins over header when both present).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_endpoints.py
import json
import pytest
from django.test import RequestFactory

from django_helm.endpoints import helm_endpoint

rf = RequestFactory()

@helm_endpoint('ping')
def ping(request, body):
    if body.get('boom'):
        raise ValueError('boom requested')
    return {'ok': True, 'extra': body.get('extra')}

def _post(body, key='hwk_test', header='bearer', raw=None):
    headers = {}
    if key is not None:
        if header == 'bearer':
            headers['HTTP_AUTHORIZATION'] = f'Bearer {key}'
        else:
            headers['HTTP_X_HELM_API_KEY'] = key
    data = raw if raw is not None else json.dumps(body)
    return rf.post('/helm/ping', data=data, content_type='application/json', **headers)

GOOD = {'query': 'ping', 'project': 'test-project'}

def test_ok_via_bearer():
    resp = ping(_post({**GOOD, 'extra': 1}))
    assert resp.status_code == 200
    assert json.loads(resp.content) == {'ok': True, 'extra': 1}
    assert resp['Cache-Control'] == 'no-store'

def test_ok_via_x_helm_api_key():
    assert ping(_post(GOOD, header='x')).status_code == 200

@pytest.mark.parametrize('key', ['wrong', '', None])
def test_bad_or_missing_key_is_401_empty(key):
    resp = ping(_post(GOOD, key=key))
    assert resp.status_code == 401 and resp.content == b''
    assert resp['Cache-Control'] == 'no-store'

def test_unset_key_is_404(settings):
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'api_key': ''}
    assert ping(_post(GOOD)).status_code == 404

def test_get_is_405():
    assert ping(rf.get('/helm/ping')).status_code == 405

@pytest.mark.parametrize('raw', ['[]', '"x"', 'null', 'not json', ''])
def test_non_object_body_is_400(raw):
    resp = ping(_post(None, raw=raw))
    assert resp.status_code == 400
    assert json.loads(resp.content) == {'error': 'body must be a JSON object'}

def test_wrong_query_is_400():
    resp = ping(_post({**GOOD, 'query': 'other'}))
    assert resp.status_code == 400 and json.loads(resp.content) == {'error': "query must be 'ping'"}

def test_wrong_project_is_400():
    resp = ping(_post({**GOOD, 'project': 'other'}))
    assert resp.status_code == 400 and json.loads(resp.content) == {'error': 'unknown project'}

def test_value_error_from_view_is_400():
    resp = ping(_post({**GOOD, 'boom': True}))
    assert resp.status_code == 400 and json.loads(resp.content) == {'error': 'boom requested'}

def test_view_is_csrf_exempt():
    assert getattr(ping, 'csrf_exempt', False) is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_endpoints.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'django_helm.endpoints'`.

- [ ] **Step 3: Implement `helm_endpoint` and `presented_key` in `django_helm/endpoints.py`**

Use `functools.wraps`, `django.views.decorators.csrf.csrf_exempt`, `hmac.compare_digest` on UTF-8 bytes, `json.loads(request.body)` guarded for `ValueError`/`UnicodeDecodeError`. Set the header on every response through one `_no_store(response)` helper so no branch can forget it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_endpoints.py -v`
Expected: PASS (13 tests).

- [ ] **Step 5: Commit**

```bash
git add django_helm/endpoints.py tests/test_endpoints.py
git commit -m "Add helm_endpoint decorator: shared auth and envelope for SDK endpoints

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Exclusion resolution

**Files:**
- Create: `django_helm/analytics/exclusions.py`
- Test: `tests/analytics/test_exclusions.py`

**Interfaces:**
- Consumes: `HelmUserAnalytics`, `AnalyticsExclusionRule.matching_users()` (Tasks 2, 3); `derive_user_hash` (existing).
- Produces: `django_helm.analytics.exclusions.excluded_users() -> dict[Any, str]` mapping user pk → reason (`'flag'` or `f'rule:{rule.note or rule.pattern}'`), after removing `inclusion='include'` users.
- Produces: `django_helm.analytics.exclusions.excluded_user_hashes() -> list[dict]` = `[{'user_hash': derive_user_hash(cfg['project'], pk, cfg['user_hash_salt']), 'reason': reason}, ...]` sorted by `user_hash`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/analytics/test_exclusions.py
import pytest
from django.contrib.auth import get_user_model

from django_helm.analytics.exclusions import excluded_user_hashes, excluded_users
from django_helm.analytics.models import AnalyticsExclusionRule, HelmUserAnalytics
from django_helm.analytics.user_hash import derive_user_hash

pytestmark = pytest.mark.django_db
User = get_user_model()

def _u(name, email):
    return User.objects.create_user(name, email, 'p')

def _h(user):
    return derive_user_hash('test-project', user.pk, 'test-salt-do-not-use-in-prod')

def test_default_user_with_no_rule_is_not_excluded():
    _u('a', 'a@acme.com')
    assert excluded_users() == {}

def test_rule_excludes_matching_default_users():
    a = _u('a', 'a@acme.com'); _u('b', 'b@other.com')
    AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$', note='staff domain')
    assert excluded_users() == {a.pk: 'rule:staff domain'}

def test_rule_reason_falls_back_to_pattern_when_no_note():
    a = _u('a', 'a@acme.com')
    AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$')
    assert excluded_users() == {a.pk: 'rule:@acme\\.com$'}

def test_disabled_rule_is_ignored():
    _u('a', 'a@acme.com')
    AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$', enabled=False)
    assert excluded_users() == {}

def test_always_include_beats_rule():
    a = _u('a', 'a@acme.com'); b = _u('b', 'b@acme.com')
    HelmUserAnalytics.objects.create(user=a, inclusion='include')
    AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$')
    assert excluded_users() == {b.pk: 'rule:@acme\\.com$'}

def test_always_exclude_without_rule():
    a = _u('a', 'a@x.com')
    HelmUserAnalytics.objects.create(user=a, inclusion='exclude')
    assert excluded_users() == {a.pk: 'flag'}

def test_flag_reason_wins_over_rule_and_lowest_rule_wins_over_later():
    a = _u('a', 'a@acme.com'); b = _u('b', 'b@acme.com')
    HelmUserAnalytics.objects.create(user=a, inclusion='exclude')
    AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$', note='first')
    AnalyticsExclusionRule.objects.create(pattern='^b@', note='second')
    assert excluded_users() == {a.pk: 'flag', b.pk: 'rule:first'}

def test_custom_field_path():
    a = _u('qa_bot', 'a@x.com'); _u('real', 'r@x.com')
    AnalyticsExclusionRule.objects.create(pattern='^qa_', field_path='username')
    assert excluded_users() == {a.pk: 'rule:^qa_'}

def test_hashes_are_sorted_and_match_middleware_derivation():
    users = [_u(f'u{i}', f'u{i}@acme.com') for i in range(5)]
    AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$', note='n')
    out = excluded_user_hashes()
    assert [o['user_hash'] for o in out] == sorted(_h(u) for u in users)
    assert all(o['reason'] == 'rule:n' for o in out)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/analytics/test_exclusions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'django_helm.analytics.exclusions'`.

- [ ] **Step 3: Implement `excluded_users()` and `excluded_user_hashes()`**

Algorithm from spec 1.3: flagged `exclude` → rules in `id` order via `matching_users().values_list('pk', flat=True)` recording only new pks → drop `include` pks → derive and sort. Read `project` and `user_hash_salt` from `settings.HELM_ANALYTICS` at call time.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/analytics/test_exclusions.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add django_helm/analytics/exclusions.py tests/analytics/test_exclusions.py
git commit -m "Resolve excluded user hashes from flags and rules

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `analytics_exclusion_list` view, URL wiring, middleware deny

**Files:**
- Create: `django_helm/analytics/views.py`
- Create: `django_helm/urls.py`
- Modify: `django_helm/analytics/middleware.py` (`_DEFAULT_DENY` gains `r"^/helm/"`)
- Modify: `tests/urls.py` (switch to `include('django_helm.urls')`)
- Test: `tests/analytics/test_exclusion_list_view.py`, `tests/analytics/test_middleware.py` (append to `TestDenyList`), `tests/test_urls.py`

**Interfaces:**
- Consumes: `helm_endpoint` (Task 4), `excluded_user_hashes` (Task 5), `paths.resolve` (Task 1).
- Produces: `django_helm.analytics.views.exclusion_list` view (query name `analytics_exclusion_list`) returning `{'project': cfg['project'], 'generated_at': timezone.now().isoformat(), 'count': len(rows), 'exclusions': rows}`.
- Produces: `django_helm.urls.urlpatterns` built at import: `[path(paths.resolve(name).lstrip('/'), view, name=f'helm_{name}') for name, view in VIEWS]` where `VIEWS = [('analytics_exclusion_list', views.exclusion_list), ('user_accounts', views.user_accounts)]`. Until Task 7 lands, register only the first entry and add the second in Task 7.

- [ ] **Step 1: Write the failing tests**

```python
# tests/analytics/test_exclusion_list_view.py
import json
import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from django_helm.analytics.models import AnalyticsExclusionRule
from django_helm.analytics.user_hash import derive_user_hash

pytestmark = pytest.mark.django_db
User = get_user_model()

def _call(client, body, key='hwk_test'):
    return client.post('/helm/analytics_exclusion_list', data=json.dumps(body),
                       content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {key}')

def test_returns_hashes_with_reasons():
    a = User.objects.create_user('a', 'a@acme.com', 'p')
    AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$', note='staff')
    resp = _call(Client(), {'query': 'analytics_exclusion_list', 'project': 'test-project'})
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert data['project'] == 'test-project' and data['count'] == 1
    assert data['exclusions'] == [{'user_hash': derive_user_hash('test-project', a.pk, 'test-salt-do-not-use-in-prod'), 'reason': 'rule:staff'}]
    assert 'generated_at' in data and resp['Cache-Control'] == 'no-store'

def test_wrong_key_is_401():
    assert _call(Client(), {'query': 'analytics_exclusion_list', 'project': 'test-project'}, key='nope').status_code == 401

def test_wrong_query_is_400():
    assert _call(Client(), {'query': 'user_accounts', 'project': 'test-project'}).status_code == 400
```

```python
# tests/test_urls.py
from django.urls import reverse, resolve

def test_default_path_is_registered():
    assert reverse('helm_analytics_exclusion_list') == '/helm/analytics_exclusion_list'

def test_override_changes_registered_path(settings):
    import importlib, django_helm.urls as u
    from django.urls import clear_url_caches
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'paths': {'analytics_exclusion_list': '/custom/list'}}
    importlib.reload(u); clear_url_caches()
    try:
        assert resolve('/custom/list').url_name == 'helm_analytics_exclusion_list'
    finally:
        settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'paths': {}}
        importlib.reload(u); clear_url_caches()
```

```python
# tests/analytics/test_middleware.py — append inside class TestDenyList
    def test_helm_endpoints_are_denied_by_default(self, mw, rf):
        # Use the same request/assert pattern as the sibling tests in this class.
        req = rf.post('/helm/analytics_exclusion_list')
        assert mw._should_skip(req) is True
```

Match the fixture names actually used in `TestDenyList` (open the file and copy a sibling test's shape).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/analytics/test_exclusion_list_view.py tests/test_urls.py tests/analytics/test_middleware.py -v`
Expected: view and URL tests FAIL with 404 / `NoReverseMatch`; the deny test FAILS with `assert False is True`.

- [ ] **Step 3: Implement the view, `django_helm/urls.py`, the deny entry, and point `tests/urls.py` at `django_helm.urls`**

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add django_helm/analytics/views.py django_helm/urls.py django_helm/analytics/middleware.py tests/urls.py tests/analytics/test_exclusion_list_view.py tests/test_urls.py tests/analytics/test_middleware.py
git commit -m "Serve /helm/analytics_exclusion_list and deny /helm/ in the hit middleware

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: `user_accounts` view with counter override

**Files:**
- Create: `django_helm/analytics/user_accounts.py`
- Modify: `django_helm/analytics/views.py` (add `user_accounts` view)
- Modify: `django_helm/urls.py` (register second entry)
- Test: `tests/analytics/test_user_accounts.py`

**Interfaces:**
- Consumes: `helm_endpoint`, `excluded_user_hashes`.
- Produces: `django_helm.analytics.user_accounts.FIELDS: tuple[str, ...]` = `('total_users','active_users','inactive_users','activated_users','not_activated_users','created_since','logged_in_since','activated_since','staff_users','analytics_excluded_users')`.
- Produces: `default_counts(since: datetime) -> dict[str, int]` with the spec's default definitions (`active` = `is_active`; `activated` = `last_login__isnull=False`; `created_since` = `date_joined__gte`; `logged_in_since` = `last_login__gte`; `activated_since` = `last_login__gte`; `staff_users` = `is_staff=True`; `analytics_excluded_users` = `len(excluded_user_hashes())`).
- Produces: `counts(since: datetime) -> dict[str, int]`: start from `default_counts(since)`, then if `settings.HELM_ANALYTICS.get('user_accounts_counter')` is set, `import_string` it, call `(since)`, and merge. Raise `django.core.exceptions.ImproperlyConfigured` if the override returns a non-dict, a key not in `FIELDS`, or a value that is not an `int` (bools excluded: `type(v) is int`) or is negative.
- Produces: `parse_since(value) -> datetime`: `value` must be a `str` parseable by `datetime.fromisoformat` after replacing a trailing `Z` with `+00:00`; naive results get `tzinfo=timezone.utc`; anything else raises `ValueError('since must be an ISO 8601 timestamp')`.
- Produces: `django_helm.analytics.views.user_accounts` view (query name `user_accounts`) returning `counts(parse_since(body.get('since')))`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/analytics/test_user_accounts.py
import json
from datetime import datetime, timezone
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import Client

from django_helm.analytics import user_accounts as ua
from django_helm.analytics.models import AnalyticsExclusionRule

pytestmark = pytest.mark.django_db
User = get_user_model()
SINCE = datetime(2026, 10, 1, tzinfo=timezone.utc)

def _mk(name, email='x@x.com', **kw):
    u = User.objects.create_user(name, email, 'p')
    for k, v in kw.items():
        setattr(u, k, v)
    u.save()
    return u

def test_default_counts():
    _mk('old_active', date_joined=datetime(2026, 1, 1, tzinfo=timezone.utc), last_login=datetime(2026, 10, 5, tzinfo=timezone.utc))
    _mk('new_never', date_joined=datetime(2026, 10, 2, tzinfo=timezone.utc))
    _mk('inactive_staff', is_active=False, is_staff=True, date_joined=datetime(2026, 1, 1, tzinfo=timezone.utc))
    _mk('excluded', email='e@acme.com', date_joined=datetime(2026, 1, 1, tzinfo=timezone.utc))
    AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$')
    assert ua.default_counts(SINCE) == {
        'total_users': 4, 'active_users': 3, 'inactive_users': 1,
        'activated_users': 1, 'not_activated_users': 3,
        'created_since': 1, 'logged_in_since': 1, 'activated_since': 1,
        'staff_users': 1, 'analytics_excluded_users': 1,
    }

def my_counter(since):
    return {'activated_users': 42}

def bad_key_counter(since):
    return {'nope': 1}

def bad_value_counter(since):
    return {'activated_users': True}

def test_counter_override_merges(settings):
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'user_accounts_counter': f'{__name__}.my_counter'}
    out = ua.counts(SINCE)
    assert out['activated_users'] == 42 and out['total_users'] == 0

@pytest.mark.parametrize('name', ['bad_key_counter', 'bad_value_counter'])
def test_counter_override_rejects_bad_output(settings, name):
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'user_accounts_counter': f'{__name__}.{name}'}
    with pytest.raises(ImproperlyConfigured):
        ua.counts(SINCE)

@pytest.mark.parametrize('raw', ['2026-10-01T00:00:00Z', '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00'])
def test_parse_since_accepts_iso_and_assumes_utc(raw):
    assert ua.parse_since(raw) == SINCE

@pytest.mark.parametrize('raw', [None, 123, 'yesterday', ''])
def test_parse_since_rejects(raw):
    with pytest.raises(ValueError):
        ua.parse_since(raw)

def _call(body):
    return Client().post('/helm/user_accounts', data=json.dumps(body), content_type='application/json',
                         HTTP_AUTHORIZATION='Bearer hwk_test')

def test_view_returns_all_ten_fields():
    resp = _call({'query': 'user_accounts', 'project': 'test-project', 'since': '2026-10-01T00:00:00Z'})
    assert resp.status_code == 200
    assert set(json.loads(resp.content)) == set(ua.FIELDS)

def test_view_missing_since_is_400():
    resp = _call({'query': 'user_accounts', 'project': 'test-project'})
    assert resp.status_code == 400
    assert json.loads(resp.content) == {'error': 'since must be an ISO 8601 timestamp'}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/analytics/test_user_accounts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'django_helm.analytics.user_accounts'`.

- [ ] **Step 3: Implement `user_accounts.py`, the view, and register the URL**

Use `django.utils.module_loading.import_string` for the override.

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add django_helm/analytics/user_accounts.py django_helm/analytics/views.py django_helm/urls.py tests/analytics/test_user_accounts.py
git commit -m "Serve /helm/user_accounts with default counts and a counter override hook

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Admin: inline, rule admin, preview action

**Files:**
- Create: `django_helm/analytics/admin.py`
- Create: `django_helm/analytics/templates/django_helm/preview_matches.html`
- Test: `tests/analytics/test_admin.py`

**Interfaces:**
- Consumes: both models, `AnalyticsExclusionRule.matching_users()`.
- Produces: `django_helm.analytics.admin.HelmUserAnalyticsInline(admin.StackedInline)` with `model = HelmUserAnalytics`, `max_num = 1`, `can_delete = False`, `fields = ('inclusion', 'note')`, `verbose_name_plural = 'Helm analytics'`; the `inclusion` field's tooltip comes from the model field's `help_text` (Task 2), so the inline needs no form customization.
- Produces: `AnalyticsExclusionRuleAdmin(admin.ModelAdmin)` registered for `AnalyticsExclusionRule`: `list_display = ('pattern', 'field_path', 'enabled', 'matches', 'note', 'updated_at')`, `list_editable = ('enabled',)`, `actions = ['preview_matches']`; `matches(self, obj) -> int` = `obj.matching_users().count()`; `preview_matches(self, request, queryset)` renders `django_helm/preview_matches.html` with, per rule, the first 50 values of `field_path` from `matching_users().values_list(field_path, flat=True)[:50]` plus the total count.
- Template must `{% extends "admin/base_site.html" %}` and show each rule's pattern, total, and the values list.

- [ ] **Step 1: Write the failing tests**

```python
# tests/analytics/test_admin.py
import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from django_helm.analytics.admin import AnalyticsExclusionRuleAdmin, HelmUserAnalyticsInline
from django_helm.analytics.models import AnalyticsExclusionRule, HelmUserAnalytics

pytestmark = pytest.mark.django_db
User = get_user_model()
TOOLTIP = ("**Default** — Helm's exclusion rules decide (excluded only if an enabled rule matches this user). "
           "**Always include** — count this user in analytics even if a rule matches them. "
           "**Always exclude** — never count this user, regardless of rules. "
           "Changes reach Helm on its next hourly sync and apply retroactively.")

def test_rule_admin_registered_with_expected_columns():
    ma = admin.site._registry[AnalyticsExclusionRule]
    assert isinstance(ma, AnalyticsExclusionRuleAdmin)
    assert ma.list_display == ('pattern', 'field_path', 'enabled', 'matches', 'note', 'updated_at')
    assert ma.list_editable == ('enabled',)
    assert 'preview_matches' in ma.actions

def test_matches_column_counts():
    User.objects.create_user('a', 'a@acme.com', 'p'); User.objects.create_user('b', 'b@x.com', 'p')
    r = AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$')
    assert AnalyticsExclusionRuleAdmin(AnalyticsExclusionRule, admin.site).matches(r) == 1

def test_inline_help_text_is_the_tooltip():
    assert HelmUserAnalytics._meta.get_field('inclusion').help_text == TOOLTIP
    assert HelmUserAnalyticsInline.max_num == 1 and HelmUserAnalyticsInline.can_delete is False

def test_preview_action_lists_values():
    su = User.objects.create_superuser('root', 'root@x.com', 'p')
    for i in range(60):
        User.objects.create_user(f'u{i}', f'u{i}@acme.com', 'p')
    r = AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$')
    c = Client(); c.force_login(su)
    url = reverse('admin:django_helm_analytics_analyticsexclusionrule_changelist')
    resp = c.post(url, {'action': 'preview_matches', '_selected_action': [r.pk]})
    html = resp.content.decode()
    assert resp.status_code == 200
    assert 'u0@acme.com' in html and '60' in html
    assert html.count('@acme.com') == 50
```

`tests/urls.py` must also include `path('admin/', admin.site.urls)`; add it in this task. The conftest from Task 1 already configures admin, sessions, messages, middleware and templates.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/analytics/test_admin.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'django_helm.analytics.admin'`.

- [ ] **Step 3: Implement the admin module and template**

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add django_helm/analytics/admin.py django_helm/analytics/templates tests/analytics/test_admin.py tests/urls.py
git commit -m "Add admin inline, rule admin and preview-matches action

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: README section and `init()` warning test

**Files:**
- Modify: `README.md` (new `## Backend endpoints for Helm` section after `## Analytics`)
- Test: `tests/analytics/test_init.py` (append), `tests/test_readme.py`

**Interfaces:** none new.

- [ ] **Step 1: Write the failing tests**

```python
# tests/analytics/test_init.py (append)
def test_init_warns_when_app_installed_and_api_key_empty(settings, caplog):
    import logging
    import django_helm.analytics as ha
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'api_key': ''}
    with caplog.at_level(logging.WARNING, logger='django_helm.analytics'):
        ha.init()
    assert 'api_key' in caplog.text and '404' in caplog.text
```

```python
# tests/test_readme.py
from pathlib import Path

def test_readme_documents_endpoints():
    text = Path(__file__).resolve().parents[1].joinpath('README.md').read_text()
    for needle in ('## Backend endpoints for Helm', '/helm/user_accounts', '/helm/analytics_exclusion_list',
                   "include('django_helm.urls')", 'HelmUserAnalyticsInline', 'user_accounts_counter',
                   'hashed_id', 'Always include', 'Always exclude', "'paths'"):
        assert needle in text, needle
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/analytics/test_init.py tests/test_readme.py -v`
Expected: README test FAILS on the first needle; the init test passes already if Task 1 implemented the warning (that is fine; keep it as the pin).

- [ ] **Step 3: Write the README section**

Subsections in this order: Install (`INSTALLED_APPS`, `migrate`); URLs (`path('', include('django_helm.urls'))`); API key (`HELM_ANALYTICS['api_key']` from Helm project settings, Backend SDK tab, Generate); Default paths (table of both constants, matching Helm placeholders); Overriding a path (`'paths'` dict, then enter the same path in Helm); The `/helm/` deny default; User accounts endpoint (ten fields, default definitions, `user_accounts_counter` example defining activated by an email-confirmed timestamp); Analytics exclusion list endpoint (the inline added to `UserAdmin.inlines`, the three-way flag semantics using the exact labels, rules with `field_path`, the preview action); a note that TastySpread's `hashed_id` is **not** the Helm hash.

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md tests/analytics/test_init.py tests/test_readme.py
git commit -m "Document backend endpoints for Helm

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Final verification and tag

- [ ] **Step 1: Fresh install check**

```bash
cd /Users/ryanfiust-klink/repos/Helm/django-helm && .venv/bin/pip install -e . -q && .venv/bin/pytest tests/ -q
```
Expected: all tests pass; count printed.

- [ ] **Step 2: Migration check**

Run: `DJANGO_SETTINGS_MODULE=tests.manage_settings .venv/bin/django-admin makemigrations django_helm_analytics --check --dry-run`
Expected: `No changes detected in app 'django_helm_analytics'`.

- [ ] **Step 3: Push the branch and open the PR**

```bash
git push -u origin feature/HELM-313-sdk-endpoints
gh pr create --title "[HELM-313] Backend endpoints for Helm: exclusion list and user accounts" --body "$(cat <<'EOF'
Implements Part 1 of the HELM-313 design (universal SDK endpoint pattern).

- django_helm.analytics is now an installable app with two SDK-owned tables
- /helm/analytics_exclusion_list and /helm/user_accounts behind one auth envelope
- Admin inline (three-way flag) and regex rule admin with preview
- Version 0.2.0; tag v0.2.0 after merge

Design: Helm/docs/superpowers/specs/2026-10-25-sdk-analytics-exclusion-list-design.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

After merge (human step): `git tag v0.2.0 && git push origin v0.2.0`.
