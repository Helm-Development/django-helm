# django-helm Social Posts Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a customer backend hand Helm social posts by registering one provider function: the SDK serves them at `/helm/social_posts`, validated against the post contract (HELM-414).

**Architecture:** A new module `django_helm/social_posts.py` holds the contract (a `SocialPost` dataclass, a `facebook_link` content helper, validation and serialisation) and one endpoint function wrapped with the existing `helm_endpoint` decorator, which already owns auth, body checks and cache headers. The host app points `HELM_ANALYTICS['social_posts_provider']` at a callable `(since) -> iterable[SocialPost]`. The SDK knows nothing about what the posts are.

**Tech Stack:** Django 5, pytest-django, Python ≥ 3.10, no new dependencies.

**Spec:** Helm repo, `docs/superpowers/specs/2026-11-11-content-driven-social-posts-design.md` (sections "The contract" and "django-helm"). Local copy: `/Users/ryanfiust-klink/repos/Helm/Helm/docs/superpowers/specs/2026-11-11-content-driven-social-posts-design.md`.

This is plan 2 of 4 for content-driven social posts (HELM-404). Helm's poller (HELM-409) is the caller and is planned separately.

## Global Constraints

- Follow the existing endpoint pattern exactly: `POST` with a JSON body carrying `query` and `project`, wrapped by `helm_endpoint`; no endpoint implements its own auth, body checks, CSRF handling or cache headers.
- The SDK owns the URL, auth, limits and contract types. The host app only supplies a provider. No app-specific concepts (recipes, reviews, groups) in the SDK, README examples excepted.
- Contract field names are fixed: `id`, `platform`, `desired_post_time`, `campaign`, `requires_review`, `updated_at`, `content`; content type `facebook_link` has `type`, `message`, `link`.
- First version supports platform `facebook` and content type `facebook_link` only.
- A malformed post from the provider must fail in the app (an exception, HTTP 500), never reach Helm.
- Timestamps go out as ISO 8601 in UTC.
- No new runtime dependencies. Python ≥ 3.10.
- Branch `feature/HELM-414-social-posts-endpoint`; commit subjects start `[HELM-414]`.
- Tests: `/Users/ryanfiust-klink/repos/Helm/django-helm/.venv/bin/python -m pytest -q` from the worktree root. Baseline: 185 passed.

## Rulings made while planning

- The spec says paging is "DRF `results` / `next`". Every existing SDK endpoint is a `POST` with a JSON body and no pagination URL, so this endpoint uses the same envelope with `since` in the body and a `has_more` flag instead of a `next` URL. Helm pages by sending the last `updated_at` it received as the next `since`; intake dedupes by ID, so the overlap is harmless.
- When no provider is configured the endpoint answers 400 `social posts are not configured`, because the shared decorator has no way for an endpoint function to return 404.

## Review Focus

1. **A provider that returns a naive datetime** (project with `USE_TZ = False`) — the post still goes out with a UTC timestamp, not a crash or a local time. → `test_naive_datetimes_are_sent_as_utc`.
2. **A provider that returns a generator or a queryset-like iterable**, not a list — works. → `test_provider_may_return_a_generator`.
3. **More posts than the cap** — the oldest `MAX_POSTS` are returned with `has_more` true, so nothing is silently dropped. → `test_caps_at_max_posts_and_sets_has_more`.
4. **A link that is not http(s), or a blank message** — rejected in the app. → `test_invalid_posts_raise`.
5. **`since` absent or null** — means "everything"; a malformed `since` is a 400. → `test_since_is_optional`, `test_bad_since_is_400`.

---

### Task 1: Social posts contract and endpoint

**Files:**
- Create: `django_helm/social_posts.py`
- Modify: `django_helm/paths.py` (add the default path)
- Modify: `django_helm/urls.py` (register the view)
- Modify: `README.md` (new subsection under "Backend endpoints for Helm"; add the row to "Default paths")
- Modify: `pyproject.toml` (`version = "0.3.0"`)
- Modify: `tests/test_readme.py` (new needles)
- Create: `tests/test_social_posts.py`
- Modify: `tests/test_urls.py`, `tests/test_paths.py`

**Interfaces:**
- Consumes: `django_helm.endpoints.helm_endpoint`, `django_helm.endpoints.BadRequest`, `django_helm.analytics.user_accounts.parse_since`.
- Produces, in `django_helm/social_posts.py`:

```python
MAX_POSTS = 500
PLATFORMS = ('facebook',)

class InvalidSocialPost(ValueError): ...

def facebook_link(message: str, link: str) -> dict:
    """Return {'type': 'facebook_link', 'message': message, 'link': link}."""

@dataclass(frozen=True)
class SocialPost:
    id: str
    platform: str
    desired_post_time: datetime
    updated_at: datetime
    content: dict
    campaign: str | None = None
    requires_review: bool = False

def serialize(post: SocialPost) -> dict:
    """Validate one post and return its JSON-ready dict. Raises InvalidSocialPost."""

@helm_endpoint('social_posts')
def social_posts(request, body) -> dict: ...
```

- Validation rules in `serialize` (each failure raises `InvalidSocialPost` whose message names the post `id` and the field):
  - `id`: non-empty `str`, at most 200 characters.
  - `platform`: one of `PLATFORMS`.
  - `desired_post_time`, `updated_at`: `datetime`. A naive value is taken as UTC. Output is `astimezone(timezone.utc).isoformat()`.
  - `campaign`: `None`, or a `str` of 1–100 characters matching `^[-a-zA-Z0-9_]+$`. Serialised as the string or `null`.
  - `requires_review`: `bool`.
  - `content`: a `dict` with `type == 'facebook_link'`, `message` a non-blank `str`, `link` a `str` of at most 2048 characters starting with `http://` or `https://`. Output contains exactly `type`, `message`, `link`.
  - Anything that is not a `SocialPost` instance raises `InvalidSocialPost`.
- Endpoint behaviour:
  - Provider: `settings.HELM_ANALYTICS.get('social_posts_provider')`, a dotted path loaded with `import_string`. Unset → `BadRequest('social posts are not configured')`.
  - `since`: `body.get('since')`; `None` or absent → the provider is called with `None`; otherwise `parse_since(value)` (which raises `BadRequest` for a bad value).
  - Calls `provider(since)`, accepts any iterable, serialises every post, sorts by `(updated_at, id)`, and returns at most `MAX_POSTS`.
  - Response:

```json
{
  "project": "<HELM_ANALYTICS['project']>",
  "generated_at": "<now, ISO 8601>",
  "count": 2,
  "has_more": false,
  "posts": [ { "id": "...", "platform": "facebook", "desired_post_time": "...",
               "campaign": "weekly-roundup", "requires_review": false,
               "updated_at": "...", "content": {"type": "facebook_link", "message": "...", "link": "..."} } ]
}
```

- `django_helm/paths.py`: `SOCIAL_POSTS = '/helm/social_posts'`, added to `DEFAULTS` under `'social_posts'`.
- `django_helm/urls.py`: `('social_posts', social_posts.social_posts)` added to `VIEWS`; URL name `helm_social_posts`.

- [ ] **Step 1: Write the failing contract tests** in `tests/test_social_posts.py`.

```python
UTC = timezone.utc
T1 = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
T0 = datetime(2026, 11, 11, 18, 2, 11, tzinfo=UTC)

def _post(**over):
    base = dict(id='p1', platform='facebook', desired_post_time=T1, updated_at=T0,
                content=facebook_link('Hello', 'https://example.com/a/'))
    return SocialPost(**{**base, **over})

def test_facebook_link_shape():
    assert facebook_link('Hi', 'https://example.com/') == {
        'type': 'facebook_link', 'message': 'Hi', 'link': 'https://example.com/'}

def test_serialize_full_post():
    assert serialize(_post(campaign='weekly-roundup', requires_review=True)) == {
        'id': 'p1', 'platform': 'facebook',
        'desired_post_time': '2026-11-16T15:00:00+00:00',
        'campaign': 'weekly-roundup', 'requires_review': True,
        'updated_at': '2026-11-11T18:02:11+00:00',
        'content': {'type': 'facebook_link', 'message': 'Hello',
                    'link': 'https://example.com/a/'},
    }

def test_campaign_defaults_to_null_and_review_to_false():
    out = serialize(_post())
    assert out['campaign'] is None and out['requires_review'] is False

def test_naive_datetimes_are_sent_as_utc():
    out = serialize(_post(desired_post_time=datetime(2026, 11, 16, 15, 0)))
    assert out['desired_post_time'] == '2026-11-16T15:00:00+00:00'

def test_other_timezones_are_converted_to_utc():
    eastern = timezone(timedelta(hours=-5))
    out = serialize(_post(desired_post_time=datetime(2026, 11, 16, 9, 0, tzinfo=eastern)))
    assert out['desired_post_time'] == '2026-11-16T14:00:00+00:00'

def test_content_extra_keys_are_dropped():
    out = serialize(_post(content={**facebook_link('Hello', 'https://example.com/'), 'x': 1}))
    assert set(out['content']) == {'type', 'message', 'link'}

@pytest.mark.parametrize('over, field', [
    ({'id': ''}, 'id'), ({'id': 'x' * 201}, 'id'), ({'id': 5}, 'id'),
    ({'platform': 'instagram'}, 'platform'),
    ({'desired_post_time': '2026-11-16'}, 'desired_post_time'),
    ({'updated_at': None}, 'updated_at'),
    ({'campaign': ''}, 'campaign'), ({'campaign': 'has space'}, 'campaign'),
    ({'campaign': 'x' * 101}, 'campaign'),
    ({'requires_review': 'yes'}, 'requires_review'),
    ({'content': {'type': 'instagram_image'}}, 'content'),
    ({'content': facebook_link('  ', 'https://example.com/')}, 'message'),
    ({'content': facebook_link('Hi', 'javascript:alert(1)')}, 'link'),
    ({'content': facebook_link('Hi', 'example.com/a')}, 'link'),
    ({'content': facebook_link('Hi', 'https://example.com/' + 'a' * 2048)}, 'link'),
    ({'content': 'nope'}, 'content'),
])
def test_invalid_posts_raise(over, field):
    with pytest.raises(InvalidSocialPost, match=field):
        serialize(_post(**over))

def test_non_socialpost_raises():
    with pytest.raises(InvalidSocialPost):
        serialize({'id': 'p1'})
```

- [ ] **Step 2: Write the failing endpoint tests** in the same file. Build requests the way `tests/test_endpoints.py` does (`RequestFactory`, `Authorization: Bearer hwk_test`, body `{'query': 'social_posts', 'project': 'test-project'}`); set the provider with the `settings` fixture, pointing at module-level provider functions defined in the test file (e.g. `'tests.test_social_posts.two_posts'`).

```python
def test_returns_posts_sorted_oldest_first(settings):
    # provider returns p2 (updated T0+1h) then p1 (updated T0)
    data = _call(settings, 'tests.test_social_posts.two_posts_unsorted')
    assert [p['id'] for p in data['posts']] == ['p1', 'p2']
    assert data['count'] == 2 and data['has_more'] is False
    assert data['project'] == 'test-project' and 'generated_at' in data

def test_since_is_optional(settings):
    # provider records the `since` it was called with
    _call(settings, 'tests.test_social_posts.recording'); assert SEEN == [None]
    _call(settings, 'tests.test_social_posts.recording', since=None); assert SEEN[-1] is None

def test_since_is_parsed_and_passed(settings):
    _call(settings, 'tests.test_social_posts.recording', since='2026-11-11T18:00:00Z')
    assert SEEN[-1] == datetime(2026, 11, 11, 18, 0, tzinfo=UTC)

def test_bad_since_is_400(settings):
    resp = _raw(settings, 'tests.test_social_posts.recording', since='yesterday')
    assert resp.status_code == 400

def test_provider_may_return_a_generator(settings):
    assert _call(settings, 'tests.test_social_posts.generator')['count'] == 2

def test_caps_at_max_posts_and_sets_has_more(settings):
    # provider returns MAX_POSTS + 3 posts with increasing updated_at
    data = _call(settings, 'tests.test_social_posts.too_many')
    assert data['count'] == MAX_POSTS and data['has_more'] is True
    assert data['posts'][0]['id'] == 'p0000'
    assert data['posts'][-1]['id'] == f'p{MAX_POSTS - 1:04d}'

def test_unconfigured_provider_is_400(settings):
    resp = _raw(settings, None)
    assert resp.status_code == 400
    assert json.loads(resp.content) == {'error': 'social posts are not configured'}

def test_invalid_post_from_provider_propagates(settings):
    with pytest.raises(InvalidSocialPost):
        _raw(settings, 'tests.test_social_posts.one_bad')

def test_empty_provider(settings):
    assert _call(settings, 'tests.test_social_posts.none') == {
        'project': 'test-project', 'generated_at': ANY, 'count': 0, 'has_more': False, 'posts': []}

def test_wrong_key_is_401(settings): ...
def test_no_api_key_is_404(settings): ...
```

Add to `tests/test_urls.py`: `reverse('helm_social_posts') == '/helm/social_posts'`. Add to `tests/test_paths.py`: `paths.resolve('social_posts') == '/helm/social_posts'` and that an override is honoured, following the file's existing tests. Add to the needle list in `tests/test_readme.py`: `'/helm/social_posts'`, `'social_posts_provider'`, `'SocialPost'`, `'facebook_link'`, `'requires_review'`.

- [ ] **Step 3: Run and confirm they fail**

Run: `/Users/ryanfiust-klink/repos/Helm/django-helm/.venv/bin/python -m pytest tests/test_social_posts.py tests/test_urls.py tests/test_paths.py tests/test_readme.py -q`
Expected: FAIL (module `django_helm.social_posts` does not exist; missing URL name; missing README text).

- [ ] **Step 4: Implement `django_helm/social_posts.py`, the path and the URL** per the Interfaces block. Module docstring states the contract and that the SDK does not know what the posts are.

- [ ] **Step 5: README.** Under "Backend endpoints for Helm": change the intro sentence to cover three endpoints; add the row `| Social posts | /helm/social_posts |` to "Default paths"; add `### Social posts endpoint` after "Analytics exclusion list endpoint" (before "`hashed_id` is not the Helm hash") covering, in this order:
  1. What it is for: Helm polls it, puts a tracking link on each post, holds posts that need review, and publishes at the desired time.
  2. The provider, with this example:

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

  3. A field table: `id` (yours, stable, unique in the project, up to 200 characters; Helm ignores a post ID it has already accepted), `platform` (`facebook`), `desired_post_time`, `updated_at`, `campaign` (optional slug; posts without one use the platform's one-off tracking link), `requires_review`, `content` (`facebook_link(message, link)`; `link` is the one URL Helm replaces with a tracking link).
  4. Limits and failure: at most 500 posts per response, oldest first, with `has_more`; a post that breaks the rules raises `InvalidSocialPost` in your app and nothing is sent.
  5. That the endpoint answers 400 until `social_posts_provider` is set.

- [ ] **Step 6: Bump `version` to `0.3.0`** in `pyproject.toml`.

- [ ] **Step 7: Run the whole suite**

Run: `/Users/ryanfiust-klink/repos/Helm/django-helm/.venv/bin/python -m pytest -q`
Expected: all PASS (185 baseline plus the new tests), no warnings.

- [ ] **Step 8: Commit** (include this plan file)

```bash
git add django_helm tests README.md pyproject.toml docs/superpowers/plans/2026-11-12-social-posts-endpoint.md
git commit -m "[HELM-414] Social posts endpoint: contract types, provider hook, /helm/social_posts"
```
