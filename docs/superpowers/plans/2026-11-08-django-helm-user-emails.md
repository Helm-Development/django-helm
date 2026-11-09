# django-helm 0.3.0: user emails endpoint — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the installable app `django_helm.mailer` and the Helm-facing endpoint `/helm/user_emails` that reports verified accounts created or activated, and email changes, in a `since`/`until` range, paged and free of ids and hashes.

**Architecture:** A new app with one event table filled by `pre_save`/`post_delete` receivers on the customer's user model; a pure query module that builds the paged response; a one-function view wrapped in the existing `helm_endpoint` decorator; a conditional route in `django_helm/urls.py` so backends without the app keep working. Nothing from `django_helm.analytics` is imported by the mailer app.

**Tech Stack:** Django 4.2+/5.x, pytest-django (`tests/manage_settings.py` shim, SQLite default, PostgreSQL via `HELM_TEST_DB_ENGINE=postgresql`).

**Spec:** `docs/superpowers/specs/2026-11-08-sdk-user-emails-design.md` (Helm repo; copied alongside this plan in the django-helm repo).

**Repo / worktree:** `django-helm/.worktrees/HELM-383`, branch `feature/HELM-383-user-emails`. Run tests with `../../.venv/bin/python -m pytest`.

## Global Constraints

- Package version `0.3.0`; `pyproject.toml` version bump, `CHANGELOG.md` entry, README section ship in this plan.
- The response never contains ids, `hashed_id`, `username`, staff flags or anything from `django_helm.analytics`. Exact top-level keys: `project, generated_at, since, until, accounts, email_changes, has_more, next_since`. Account keys: `email, first_name, last_name, changed_at`. Change keys: `previous_email, email, changed_at`.
- Only `is_active=True` users with a non-empty `email` are reported. Addresses whose domain ends in `.local`, `.invalid`, `.test`, `.example` or `.localhost` are never reported in either list.
- `ACCOUNTS_PAGE = 200`, `CHANGES_PAGE = 100`; a cut keeps every row sharing the last `changed_at`; `next_since` = min over cut lists of (last `changed_at` + 1 µs); `since` inclusive, `until` exclusive.
- Events older than `EVENT_RETENTION_DAYS = 90` are purged at the start of each endpoint call. Deleting a user deletes every event whose `email` equals that user's address, case-insensitively.
- Default path `/helm/user_emails`, override key `'user_emails'` in `HELM_ANALYTICS['paths']`. The route exists only when `django_helm.mailer` is installed.
- Module docstrings explain *why*; no emails in log messages.

## Review Focus

1. A merge sequence `real → placeholder` then `original → real` followed by deleting the placeholder account must leave exactly one change (`original → real`) in the response. (Task 2 test `test_deleting_user_removes_its_events`, Task 3 test `test_siwa_merge_sequence_reports_one_change`.)
2. `since` later than `until` must be a 400, not an empty 200. (Task 4 `test_since_after_until_is_400`.)
3. 300 users created in one bulk `bulk_create` with the same `date_joined` must come back in one over-sized page with `has_more` false, not loop forever. (Task 3 `test_ties_are_kept_together_and_cursor_advances`.)
4. A user whose email changes only in letter case must still produce one `email_changed` event, and a user re-saved with no change must produce none. (Task 2 `test_case_only_change_is_recorded`, `test_unchanged_save_records_nothing`.)
5. Upgrading a backend that has not added `django_helm.mailer` must not break its URLconf; `/helm/user_emails` simply does not resolve. (Task 4 `test_route_absent_without_mailer_app`.)

---

### Task 1: `django_helm.mailer` app and `UserEmailEvent` model

**Files:**
- Create: `django_helm/mailer/__init__.py` (docstring only: the PII boundary, why this is a separate app)
- Create: `django_helm/mailer/apps.py`
- Create: `django_helm/mailer/models.py`
- Create: `django_helm/mailer/migrations/__init__.py`, `django_helm/mailer/migrations/0001_initial.py`
- Modify: `tests/manage_settings.py` (add `'django_helm.mailer'` after `'django_helm.analytics'` in `INSTALLED_APPS`)
- Create: `tests/mailer/__init__.py`, `tests/mailer/test_models.py`, `tests/mailer/test_app_config.py`

**Interfaces:**
- Produces: `MailerConfig(name='django_helm.mailer', label='django_helm_mailer', default_auto_field='django.db.models.BigAutoField')`.
- Produces: `UserEmailEvent` with `KIND_ACTIVATED = 'activated'`, `KIND_EMAIL_CHANGED = 'email_changed'`, fields `kind: CharField(max_length=16, choices)`, `email: EmailField(max_length=254)`, `previous_email: EmailField(max_length=254, blank=True, default='')`, `first_name: CharField(max_length=150, blank=True, default='')`, `last_name: CharField(max_length=150, blank=True, default='')`, `occurred_at: DateTimeField(db_index=True)`; `Meta.db_table = 'django_helm_user_email_event'`, `ordering = ['occurred_at', 'pk']`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/mailer/test_app_config.py
from django.apps import apps

def test_app_label_and_name():
    cfg = apps.get_app_config('django_helm_mailer')
    assert cfg.name == 'django_helm.mailer'

def test_mailer_package_does_not_import_analytics():
    import inspect, django_helm.mailer, django_helm.mailer.models
    for mod in (django_helm.mailer, django_helm.mailer.models):
        assert 'django_helm.analytics' not in inspect.getsource(mod)
```

```python
# tests/mailer/test_models.py
import pytest
from django.utils import timezone
from django_helm.mailer.models import UserEmailEvent

pytestmark = pytest.mark.django_db

def test_table_name_and_defaults():
    ev = UserEmailEvent.objects.create(kind=UserEmailEvent.KIND_ACTIVATED, email='a@x.com', occurred_at=timezone.now())
    assert UserEmailEvent._meta.db_table == 'django_helm_user_email_event'
    assert ev.previous_email == '' and ev.first_name == '' and ev.last_name == ''

def test_occurred_at_is_indexed():
    assert UserEmailEvent._meta.get_field('occurred_at').db_index is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `../../.venv/bin/python -m pytest tests/mailer -q`
Expected: FAIL (`LookupError: No installed app with label 'django_helm_mailer'` or ImportError).

- [ ] **Step 3: Create the app, model and settings entry**

Write `apps.py`, `models.py` as specified in Interfaces, add the app to `tests/manage_settings.py`, then generate the migration:

Run: `DJANGO_SETTINGS_MODULE=tests.manage_settings ../../.venv/bin/django-admin makemigrations django_helm_mailer`
Expected: `0001_initial.py` created with `CreateModel UserEmailEvent`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `../../.venv/bin/python -m pytest tests/mailer -q`
Expected: 4 passed.

- [ ] **Step 5: Run the whole suite to confirm nothing else moved**

Run: `../../.venv/bin/python -m pytest -q`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add django_helm/mailer tests/mailer tests/manage_settings.py
git commit -m "[HELM-383] Add django_helm.mailer app with UserEmailEvent"
```

---

### Task 2: Signals: record activations and email changes, drop a deleted user's events

**Files:**
- Create: `django_helm/mailer/signals.py`
- Modify: `django_helm/mailer/apps.py` (connect in `ready()`)
- Create: `tests/mailer/test_signals.py`

**Interfaces:**
- Consumes: `UserEmailEvent` (Task 1).
- Produces: `record_user_change(sender, instance, **kwargs)` (pre_save receiver), `forget_deleted_user(sender, instance, **kwargs)` (post_delete receiver), both connected to `get_user_model()` in `MailerConfig.ready()` with `dispatch_uid='django_helm_mailer_pre_save'` / `'django_helm_mailer_post_delete'`. Helper `normalize(email: str) -> str` returns `email.strip().lower()`.

Rules (from the spec):
- No event for a new row (`instance._state.adding` or no existing pk).
- Previous row fetched with `sender._default_manager.filter(pk=instance.pk).values('email', 'is_active').first()`; missing → no event.
- `is_active` False → True: `activated` event with the row's current `email`, names, `occurred_at=timezone.now()`.
- `normalize(email)` differs and the row is active before and after: `email_changed` event with `previous_email` = old address, `email` = new, names snapshot. A case-only change counts.
- Both at once (activation and a new address in one save): only `activated` is recorded, and its `email` is the new address. `email_changed` requires the row to be active before and after.
- `post_delete`: `UserEmailEvent.objects.filter(email__iexact=instance.email).delete()`; a blank email deletes nothing.

- [ ] **Step 1: Write the failing tests**

```python
# tests/mailer/test_signals.py
import pytest
from django.contrib.auth import get_user_model
from django_helm.mailer.models import UserEmailEvent

pytestmark = pytest.mark.django_db
User = get_user_model()

def _user(email, active=True, **kw):
    u = User.objects.create_user('u_' + email, email, 'p', **kw)
    if u.is_active != active:
        User.objects.filter(pk=u.pk).update(is_active=active)
        u.refresh_from_db()
    return u

def test_new_user_records_nothing():
    _user('a@x.com')
    assert UserEmailEvent.objects.count() == 0

def test_activation_records_activated_with_current_fields():
    u = _user('a@x.com', active=False, first_name='Ann')
    u.is_active = True; u.save()
    [ev] = UserEmailEvent.objects.all()
    assert (ev.kind, ev.email, ev.first_name, ev.previous_email) == ('activated', 'a@x.com', 'Ann', '')

def test_email_change_on_active_user_records_previous_and_new():
    u = _user('old@x.com')
    u.email = 'New@x.com'; u.save()
    [ev] = UserEmailEvent.objects.all()
    assert (ev.kind, ev.previous_email, ev.email) == ('email_changed', 'old@x.com', 'New@x.com')

def test_case_only_change_is_recorded():
    u = _user('a@x.com'); u.email = 'A@x.com'; u.save()
    assert UserEmailEvent.objects.filter(kind='email_changed').count() == 1

def test_unchanged_save_records_nothing():
    u = _user('a@x.com'); u.first_name = 'Zed'; u.save()
    assert UserEmailEvent.objects.count() == 0

def test_email_change_on_inactive_user_records_nothing():
    u = _user('a@x.com', active=False); u.email = 'b@x.com'; u.save()
    assert UserEmailEvent.objects.count() == 0

def test_activation_and_change_in_one_save_records_only_activated():
    u = _user('a@x.com', active=False); u.is_active = True; u.email = 'b@x.com'; u.save()
    [ev] = UserEmailEvent.objects.all()
    assert (ev.kind, ev.email) == ('activated', 'b@x.com')

def test_deleting_user_removes_its_events():
    u = _user('a@x.com', active=False); u.is_active = True; u.save()
    u.email = 'B@x.com'; u.save()
    assert UserEmailEvent.objects.count() == 2
    u.delete()
    assert UserEmailEvent.objects.count() == 1  # the activated event carries a@x.com, not b@x.com
    assert UserEmailEvent.objects.get().email == 'a@x.com'
```

- [ ] **Step 2: Run to verify they fail**

Run: `../../.venv/bin/python -m pytest tests/mailer/test_signals.py -q`
Expected: FAIL (events count 0 where 1 expected).

- [ ] **Step 3: Implement `signals.py` and connect it in `ready()`**

`ready()` imports `.signals` and connects both receivers to `get_user_model()` with the `dispatch_uid`s above (so a double `ready()` in tests connects once).

- [ ] **Step 4: Run tests to verify they pass**

Run: `../../.venv/bin/python -m pytest tests/mailer -q`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add django_helm/mailer/signals.py django_helm/mailer/apps.py tests/mailer/test_signals.py
git commit -m "[HELM-383] Record activations and email changes; forget deleted users"
```

---

### Task 3: Query module: paged accounts and email changes

**Files:**
- Create: `django_helm/mailer/user_emails.py`
- Create: `tests/mailer/test_user_emails.py`

**Interfaces:**
- Consumes: `UserEmailEvent`, `normalize` (Tasks 1–2).
- Produces, all in `user_emails.py`:
  - `ACCOUNTS_PAGE = 200`, `CHANGES_PAGE = 100`, `EVENT_RETENTION_DAYS = 90`, `RESERVED_SUFFIXES = ('.local', '.invalid', '.test', '.example', '.localhost')`.
  - `is_reportable(email: str) -> bool`: non-empty, contains `@`, domain (after the last `@`, lower-cased) does not end with a reserved suffix.
  - `purge_old_events(now) -> int`: deletes events with `occurred_at < now - 90 days`, returns the count.
  - `collect(since, until) -> dict` returning `{'accounts': [...], 'email_changes': [...], 'has_more': bool, 'next_since': datetime | None}` where each account is `{'email', 'first_name', 'last_name', 'changed_at': datetime}` and each change `{'previous_email', 'email', 'changed_at': datetime}`. Datetimes are returned as `datetime` objects; the view serialises.
  - `_cut(rows, page) -> tuple[list, bool]`: rows sorted by `changed_at`; if `len(rows) <= page` return `(rows, False)`; else keep the first `page` rows plus every following row whose `changed_at` equals the `page`-th row's, return `(kept, True)`.

Algorithm for `collect` (the part the signature does not determine):
1. `users = get_user_model()._default_manager.filter(is_active=True).exclude(email='')`.
2. Accounts from `users.filter(date_joined__gte=since, date_joined__lt=until)` with `changed_at = date_joined`.
3. For each `activated` event in range, look up the user by `email__iexact=event.email` in `users`; if found, add an account with the user's current `email`, names and `changed_at = event.occurred_at`; if not found, skip.
4. De-duplicate accounts by `normalize(email)`, keeping the entry with the earliest `changed_at`; drop entries that fail `is_reportable`; sort by `(changed_at, email)`.
5. Changes: `email_changed` events in range, sorted by `occurred_at`, dropping any whose `email` or `previous_email` fails `is_reportable`.
6. Apply `_cut` to each list. `has_more` is the OR of the two flags. `next_since` is the minimum, over the lists that were cut, of (last kept `changed_at` + `timedelta(microseconds=1)`), else `None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/mailer/test_user_emails.py
from datetime import datetime, timedelta, timezone as tz
import pytest
from django.contrib.auth import get_user_model
from django_helm.mailer import user_emails as ue
from django_helm.mailer.models import UserEmailEvent

pytestmark = pytest.mark.django_db
User = get_user_model()
T0 = datetime(2026, 11, 1, tzinfo=tz.utc)
T1 = T0 + timedelta(days=1)

def _joined(email, when, active=True, **kw):
    u = User.objects.create_user('u_' + email, email, 'p', **kw)
    User.objects.filter(pk=u.pk).update(date_joined=when, is_active=active)
    return u

def test_accounts_from_date_joined_only_active_with_email():
    _joined('a@x.com', T0 + timedelta(hours=1), first_name='A')
    _joined('b@x.com', T0 + timedelta(hours=2), active=False)
    _joined('', T0 + timedelta(hours=3))
    _joined('c@x.com', T1 + timedelta(hours=1))
    out = ue.collect(T0, T1)
    assert [a['email'] for a in out['accounts']] == ['a@x.com']
    assert out['accounts'][0] == {'email': 'a@x.com', 'first_name': 'A', 'last_name': '', 'changed_at': T0 + timedelta(hours=1)}
    assert out['has_more'] is False and out['next_since'] is None

def test_activated_event_adds_current_user_row_and_dedupes():
    u = _joined('a@x.com', T0 - timedelta(days=30))
    UserEmailEvent.objects.create(kind='activated', email='A@X.COM', occurred_at=T0 + timedelta(hours=5))
    UserEmailEvent.objects.create(kind='activated', email='gone@x.com', occurred_at=T0 + timedelta(hours=6))
    out = ue.collect(T0, T1)
    assert [a['email'] for a in out['accounts']] == ['a@x.com']
    assert out['accounts'][0]['changed_at'] == T0 + timedelta(hours=5)

def test_reserved_domains_are_never_reported():
    _joined('x@siwa.invalid.tastyspread.local', T0 + timedelta(hours=1))
    UserEmailEvent.objects.create(kind='email_changed', previous_email='a@x.com', email='b@foo.invalid', occurred_at=T0 + timedelta(hours=2))
    out = ue.collect(T0, T1)
    assert out['accounts'] == [] and out['email_changes'] == []

def test_siwa_merge_sequence_reports_one_change():
    dup = _joined('real@x.com', T0 - timedelta(days=5)); orig = _joined('orig@x.com', T0 - timedelta(days=9))
    dup.email = 'siwa-merged-1@invalid.tastyspread.local'; dup.save()
    orig.email = 'real@x.com'; orig.save()
    dup.delete()
    out = ue.collect(T0 - timedelta(days=1), T1)
    assert [(c['previous_email'], c['email']) for c in out['email_changes']] == [('orig@x.com', 'real@x.com')]

def test_ties_are_kept_together_and_cursor_advances():
    same = T0 + timedelta(hours=1)
    User.objects.bulk_create([User(username=f'u{i}', email=f'u{i}@x.com', date_joined=same, is_active=True) for i in range(300)])
    _joined('late@x.com', T0 + timedelta(hours=2))
    out = ue.collect(T0, T1)
    assert len(out['accounts']) == 300 and out['has_more'] is True
    assert out['next_since'] == same + timedelta(microseconds=1)
    out2 = ue.collect(out['next_since'], T1)
    assert [a['email'] for a in out2['accounts']] == ['late@x.com'] and out2['has_more'] is False

def test_changes_page_cut_sets_next_since_to_min_over_cut_lists():
    for i in range(101):
        UserEmailEvent.objects.create(kind='email_changed', previous_email=f'o{i}@x.com', email=f'n{i}@x.com', occurred_at=T0 + timedelta(minutes=i))
    _joined('acct@x.com', T0 + timedelta(hours=10))
    out = ue.collect(T0, T1)
    assert len(out['email_changes']) == 100 and out['has_more'] is True
    assert out['next_since'] == T0 + timedelta(minutes=99, microseconds=1)
    assert [a['email'] for a in out['accounts']] == ['acct@x.com']  # the uncut list is returned in full

def test_purge_old_events():
    UserEmailEvent.objects.create(kind='activated', email='a@x.com', occurred_at=T0 - timedelta(days=91))
    UserEmailEvent.objects.create(kind='activated', email='b@x.com', occurred_at=T0 - timedelta(days=89))
    assert ue.purge_old_events(T0) == 1 and UserEmailEvent.objects.get().email == 'b@x.com'

@pytest.mark.parametrize('email,ok', [('a@x.com', True), ('', False), ('nope', False), ('a@b.local', False), ('a@b.LOCALHOST', False), ('a@privaterelay.appleid.com', True)])
def test_is_reportable(email, ok):
    assert ue.is_reportable(email) is ok
```

- [ ] **Step 2: Run to verify they fail**

Run: `../../.venv/bin/python -m pytest tests/mailer/test_user_emails.py -q`
Expected: FAIL with `ModuleNotFoundError: django_helm.mailer.user_emails`.

- [ ] **Step 3: Implement `user_emails.py`** per Interfaces and the algorithm above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `../../.venv/bin/python -m pytest tests/mailer -q`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add django_helm/mailer/user_emails.py tests/mailer/test_user_emails.py
git commit -m "[HELM-383] Paged user-emails query with tie-safe cursor and reserved-domain filter"
```

---

### Task 4: Endpoint view, path, conditional route, shared timestamp parser

**Files:**
- Modify: `django_helm/endpoints.py` (add `parse_timestamp(value, field: str)`; body of today's `user_accounts.parse_since` with the message `f'{field} must be an ISO 8601 timestamp'`)
- Modify: `django_helm/analytics/user_accounts.py` (`parse_since = lambda value: parse_timestamp(value, 'since')` kept as a public name; remove the duplicated body)
- Create: `django_helm/mailer/views.py`
- Modify: `django_helm/paths.py` (`USER_EMAILS = '/helm/user_emails'`, `DEFAULTS['user_emails']`)
- Modify: `django_helm/urls.py`
- Create: `tests/mailer/test_user_emails_view.py`
- Modify: `tests/test_urls.py`, `tests/test_paths.py`

**Interfaces:**
- Consumes: `collect`, `purge_old_events` (Task 3); `helm_endpoint`, `BadRequest` (existing).
- Produces: view `user_emails(request, body) -> dict` decorated `@helm_endpoint('user_emails')`; `paths.USER_EMAILS`; URL name `helm_user_emails`.

Behaviour of the view: `since = parse_timestamp(body.get('since'), 'since')`; `until = parse_timestamp(body['until'], 'until') if body.get('until') is not None else timezone.now()`; `since >= until` → `BadRequest('since must be before until')`; `purge_old_events(timezone.now())`; `data = collect(since, until)`; return `{'project': settings.HELM_ANALYTICS['project'], 'generated_at': now.isoformat(), 'since': since.isoformat(), 'until': until.isoformat(), 'accounts': [...isoformat changed_at...], 'email_changes': [...], 'has_more': ..., 'next_since': iso or None}`.

`urls.py`: keep `VIEWS` for analytics; add

```python
def _mailer_views():
    from django.apps import apps
    if not apps.is_installed('django_helm.mailer'):
        return []
    from django_helm.mailer import views as mailer_views
    return [('user_emails', mailer_views.user_emails)]
```

and have `build_urlpatterns()` iterate `VIEWS + _mailer_views()`. The middleware deny list already iterates `paths.DEFAULTS`, so the new default path is denied for recording with no change there.

- [ ] **Step 1: Write the failing tests**

```python
# tests/mailer/test_user_emails_view.py
import json
from datetime import datetime, timedelta, timezone as tz
import pytest
from django.contrib.auth import get_user_model
from django.test import Client

pytestmark = pytest.mark.django_db
User = get_user_model()
T0 = datetime(2026, 11, 1, tzinfo=tz.utc)
BODY = {'query': 'user_emails', 'project': 'test-project'}

def _call(body, key='hwk_test'):
    return Client().post('/helm/user_emails', data=json.dumps(body), content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {key}')

def test_response_shape_has_no_identifiers():
    u = User.objects.create_user('u', 'a@x.com', 'p', first_name='A', last_name='B')
    User.objects.filter(pk=u.pk).update(date_joined=T0 + timedelta(hours=1))
    resp = _call({**BODY, 'since': T0.isoformat(), 'until': (T0 + timedelta(days=1)).isoformat()})
    assert resp.status_code == 200 and resp['Cache-Control'] == 'no-store'
    data = json.loads(resp.content)
    assert set(data) == {'project', 'generated_at', 'since', 'until', 'accounts', 'email_changes', 'has_more', 'next_since'}
    assert data['accounts'] == [{'email': 'a@x.com', 'first_name': 'A', 'last_name': 'B', 'changed_at': (T0 + timedelta(hours=1)).isoformat()}]
    assert data['has_more'] is False and data['next_since'] is None
    assert 'hashed_id' not in resp.content.decode() and str(u.pk) not in json.dumps(data['accounts'])

def test_until_defaults_to_now():
    resp = _call({**BODY, 'since': T0.isoformat()})
    assert resp.status_code == 200 and json.loads(resp.content)['until'] > T0.isoformat()

def test_missing_since_is_400():
    assert _call(BODY).status_code == 400

def test_since_after_until_is_400():
    resp = _call({**BODY, 'since': (T0 + timedelta(days=1)).isoformat(), 'until': T0.isoformat()})
    assert resp.status_code == 400 and json.loads(resp.content) == {'error': 'since must be before until'}

def test_wrong_key_is_401_and_wrong_query_is_400():
    assert _call({**BODY, 'since': T0.isoformat()}, key='nope').status_code == 401
    assert _call({**BODY, 'query': 'user_accounts', 'since': T0.isoformat()}).status_code == 400
```

```python
# additions to tests/test_urls.py
def test_user_emails_default_path_is_registered():
    assert reverse('helm_user_emails') == '/helm/user_emails'

def test_route_absent_without_mailer_app(monkeypatch):
    from django.apps import apps as django_apps
    from django_helm.urls import build_urlpatterns
    real = django_apps.is_installed
    monkeypatch.setattr(django_apps, 'is_installed', lambda name: False if name == 'django_helm.mailer' else real(name))
    assert not [p for p in build_urlpatterns() if p.name == 'helm_user_emails']
```

```python
# addition to tests/test_paths.py
def test_user_emails_default_and_override(settings):
    from django_helm import paths
    assert paths.resolve('user_emails') == '/helm/user_emails'
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'paths': {'user_emails': 'internal/emails'}}
    assert paths.resolve('user_emails') == '/internal/emails'
```

- [ ] **Step 2: Run to verify they fail**

Run: `../../.venv/bin/python -m pytest tests/mailer/test_user_emails_view.py tests/test_urls.py tests/test_paths.py -q`
Expected: FAIL (404s / NoReverseMatch / KeyError 'user_emails').

- [ ] **Step 3: Implement** `parse_timestamp`, the view, the path constant and the conditional route.

- [ ] **Step 4: Run the full suite**

Run: `../../.venv/bin/python -m pytest -q`
Expected: all passed (including the existing `user_accounts` tests through the `parse_since` alias).

- [ ] **Step 5: Commit**

```bash
git add django_helm/endpoints.py django_helm/analytics/user_accounts.py django_helm/mailer/views.py django_helm/paths.py django_helm/urls.py tests
git commit -m "[HELM-383] Serve /helm/user_emails when django_helm.mailer is installed"
```

---

### Task 5: README, CHANGELOG, version 0.3.0

**Files:**
- Modify: `README.md` (section `## Backend endpoints for Helm`: intro sentence now says three questions; `### Install` gains `'django_helm.mailer'` and names the third table; `### Default paths` table gains `| User emails | /helm/user_emails |`; override example gains `'user_emails'`; new `### User emails endpoint` after `### Analytics exclusion list endpoint` and before `### hashed_id is not the Helm hash`)
- Create: `CHANGELOG.md` (`## 0.3.0 — 2026-11-08` with the new app, endpoint, purge, and the no-PII-with-hash rule; `## 0.2.0` one-line back-reference)
- Modify: `pyproject.toml` (`version = "0.3.0"`)
- Modify: `tests/test_readme.py` (add needles `'/helm/user_emails'`, `'django_helm.mailer'`, `'email_changes'`, `'90 days'`)

The `### User emails endpoint` section states, in this order: what it returns (verified accounts created/activated and email changes in `since`/`until`), the request body, the response shape (one JSON example), paging (`has_more`, `next_since`), the two receivers and the 90-day purge, the reserved-domain rule, and a Warning-style paragraph: the response carries no user id and no Helm hash on purpose, and customers must not add either.

- [ ] **Step 1: Extend `tests/test_readme.py` and run it**

Run: `../../.venv/bin/python -m pytest tests/test_readme.py -q`
Expected: FAIL on the new needles.

- [ ] **Step 2: Write the README section, CHANGELOG and bump the version**

- [ ] **Step 3: Run the full suite**

Run: `../../.venv/bin/python -m pytest -q`
Expected: all passed.

- [ ] **Step 4: Commit**

```bash
git add README.md CHANGELOG.md pyproject.toml tests/test_readme.py
git commit -m "[HELM-383] Document the user emails endpoint; release 0.3.0"
```
