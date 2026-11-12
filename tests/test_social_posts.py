import json
from datetime import datetime, timedelta, timezone

import pytest
from django.test import RequestFactory

from django_helm.social_posts import (
    MAX_CAMPAIGN_LENGTH, MAX_MESSAGE_LENGTH, MAX_POSTS, MAX_RESPONSE_BYTES, InvalidSocialPost, SocialPost, facebook_link, serialize,
)

UTC = timezone.utc
T1 = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
T0 = datetime(2026, 11, 11, 18, 2, 11, tzinfo=UTC)
rf = RequestFactory()
SEEN = []


class _Any:
    def __eq__(self, other):
        return True


ANY = _Any()


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
    ({'campaign': 'x' * 81}, 'campaign'), ({'campaign': 'weekly\n'}, 'campaign'),
    ({'id': '   '}, 'id'),
    ({'requires_review': 'yes'}, 'requires_review'),
    ({'content': {'type': 'instagram_image'}}, 'content'),
    ({'content': facebook_link('  ', 'https://example.com/')}, 'message'),
    ({'content': facebook_link('x' * 2001, 'https://example.com/')}, 'message'),
    ({'content': facebook_link('Hi', 'javascript:alert(1)')}, 'link'),
    ({'content': facebook_link('Hi', 'example.com/a')}, 'link'),
    ({'content': facebook_link('Hi', 'https://')}, 'link'),
    ({'content': facebook_link('Hi', 'https:///path')}, 'link'),
    ({'content': facebook_link('Hi', 'https://example.com/a b')}, 'link'),
    ({'content': facebook_link('Hi', 'https://example.com/a\nb')}, 'link'),
    ({'content': facebook_link('Hi', 'https://example.com/' + 'a' * 2048)}, 'link'),
    ({'content': 'nope'}, 'content'),
])
def test_invalid_posts_raise(over, field):
    with pytest.raises(InvalidSocialPost, match=field):
        serialize(_post(**over))


def test_scheme_is_case_insensitive():
    out = serialize(_post(content=facebook_link('Hi', 'HTTPS://Example.com/a')))
    assert out['content']['link'] == 'HTTPS://Example.com/a'


def test_campaign_of_exactly_max_length_is_accepted():
    assert MAX_CAMPAIGN_LENGTH == 80
    assert serialize(_post(campaign='x' * 80))['campaign'] == 'x' * 80


def test_message_of_exactly_max_length_is_accepted():
    out = serialize(_post(content=facebook_link('x' * MAX_MESSAGE_LENGTH, 'https://example.com/')))
    assert len(out['content']['message']) == 2000


def test_non_socialpost_raises():
    with pytest.raises(InvalidSocialPost):
        serialize({'id': 'p1'})


# --- endpoint -------------------------------------------------------------

def two_posts_unsorted(since):
    return [_post(id='p2', updated_at=T0 + timedelta(hours=1)), _post(id='p1')]


def recording(since):
    SEEN.append(since)
    return []


def generator(since):
    yield _post(id='p1')
    yield _post(id='p2')


def too_many(since):
    return [_post(id=f'p{i:04d}', updated_at=T0 + timedelta(seconds=i))
            for i in range(MAX_POSTS + 3)]


def heavy(since):
    msg = '\U0001f373' * MAX_MESSAGE_LENGTH  # astral: 12 bytes escaped
    return [_post(id=f'h{i:02d}', updated_at=T0 + timedelta(seconds=i),
                  content=facebook_link(msg, 'https://example.com/'))
            for i in range(40)]


def same_time(n):
    def provider(since):
        return [_post(id=f'{i:03d}', updated_at=T0) for i in range(n)]
    return provider


def abc(since):
    return [_post(id=i, updated_at=T0) for i in 'cab']


def ties(since):
    return same_time(MAX_POSTS + 5)(since)


def one_bad(since):
    return [_post(), _post(id='bad', platform='instagram')]


def none(since):
    return []


def _raw(settings, provider, key='hwk_test', **extra):
    config = {**settings.HELM_ANALYTICS}
    if provider:
        config['social_posts_provider'] = provider
    settings.HELM_ANALYTICS = config
    body = {'query': 'social_posts', 'project': 'test-project', **extra}
    from django_helm.social_posts import social_posts
    headers = {'HTTP_AUTHORIZATION': f'Bearer {key}'} if key is not None else {}
    request = rf.post('/helm/social_posts', data=json.dumps(body),
                      content_type='application/json', **headers)
    return social_posts(request)


def _call(settings, provider, **extra):
    resp = _raw(settings, provider, **extra)
    assert resp.status_code == 200
    return json.loads(resp.content)


def test_returns_posts_sorted_oldest_first(settings):
    data = _call(settings, 'tests.test_social_posts.two_posts_unsorted')
    assert [p['id'] for p in data['posts']] == ['p1', 'p2']
    assert data['count'] == 2 and data['has_more'] is False
    assert data['project'] == 'test-project' and 'generated_at' in data


def test_since_is_optional(settings):
    SEEN.clear()
    _call(settings, 'tests.test_social_posts.recording')
    assert SEEN == [None]
    _call(settings, 'tests.test_social_posts.recording', since=None)
    assert SEEN[-1] is None


def test_since_is_parsed_and_passed(settings):
    _call(settings, 'tests.test_social_posts.recording', since='2026-11-11T18:00:00Z')
    assert SEEN[-1] == datetime(2026, 11, 11, 18, 0, tzinfo=UTC)


def test_bad_since_is_400(settings):
    resp = _raw(settings, 'tests.test_social_posts.recording', since='yesterday')
    assert resp.status_code == 400


def test_provider_may_return_a_generator(settings):
    assert _call(settings, 'tests.test_social_posts.generator')['count'] == 2


def test_caps_at_max_posts_and_sets_has_more(settings):
    data = _call(settings, 'tests.test_social_posts.too_many')
    assert data['count'] == MAX_POSTS and data['has_more'] is True
    assert data['posts'][0]['id'] == 'p0000'
    assert data['posts'][-1]['id'] == f'p{MAX_POSTS - 1:04d}'
    assert data['next'] == {'since': data['posts'][-1]['updated_at'],
                            'after_id': f'p{MAX_POSTS - 1:04d}'}


def test_byte_budget_stops_early_and_sets_has_more(settings):
    resp = _raw(settings, 'tests.test_social_posts.heavy')
    assert resp.status_code == 200
    data = json.loads(resp.content)
    sizes = [len(json.dumps(p)) for p in map(serialize, heavy(None))]
    expected, total = 1, sizes[0]
    for size in sizes[1:]:
        if total + size > MAX_RESPONSE_BYTES:
            break
        total += size
        expected += 1
    assert expected in (1, 2)
    assert data['count'] == expected and data['has_more'] is True
    assert [p['id'] for p in data['posts']] == [f'h{i:02d}' for i in range(expected)]
    assert len(resp.content) < 64 * 1024
    assert data['next'] == {'since': data['posts'][-1]['updated_at'],
                            'after_id': data['posts'][-1]['id']}


def test_unconfigured_provider_is_400(settings):
    resp = _raw(settings, None)
    assert resp.status_code == 400
    assert json.loads(resp.content) == {'error': 'social posts are not configured'}


def test_invalid_post_from_provider_propagates(settings):
    with pytest.raises(InvalidSocialPost):
        _raw(settings, 'tests.test_social_posts.one_bad')


def test_empty_provider(settings):
    assert _call(settings, 'tests.test_social_posts.none') == {
        'project': 'test-project', 'generated_at': ANY, 'count': 0, 'has_more': False, 'next': None, 'posts': []}


def test_after_id_skips_posts_already_seen_at_the_same_timestamp(settings):
    data = _call(settings, 'tests.test_social_posts.abc',
                 since=T0.isoformat(), after_id='a')
    assert [p['id'] for p in data['posts']] == ['b', 'c']


def test_paging_through_ties_reaches_every_post(settings):
    seen, calls, extra = [], 0, {}
    while True:
        data = _call(settings, 'tests.test_social_posts.ties', **extra)
        calls += 1
        seen += [p['id'] for p in data['posts']]
        if not data['has_more']:
            break
        extra = data['next']
        assert calls < 10
    assert calls == 2
    assert sorted(seen) == [f'{i:03d}' for i in range(MAX_POSTS + 5)]


def test_next_is_null_when_nothing_left(settings):
    data = _call(settings, 'tests.test_social_posts.abc')
    assert data['has_more'] is False and data['next'] is None


def test_after_id_requires_since(settings):
    resp = _raw(settings, 'tests.test_social_posts.abc', after_id='a')
    assert resp.status_code == 400
    assert json.loads(resp.content) == {'error': 'after_id requires since'}


@pytest.mark.parametrize('bad', [5, '', 'x' * 201])
def test_bad_after_id_is_400(settings, bad):
    resp = _raw(settings, 'tests.test_social_posts.abc', since=T0.isoformat(), after_id=bad)
    assert resp.status_code == 400
    assert json.loads(resp.content) == {
        'error': 'after_id must be a string of at most 200 characters'}


def test_wrong_key_is_401(settings):
    resp = _raw(settings, 'tests.test_social_posts.none', key='wrong')
    assert resp.status_code == 401


def test_no_api_key_is_404(settings):
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'api_key': ''}
    resp = _raw(settings, 'tests.test_social_posts.none')
    assert resp.status_code == 404
