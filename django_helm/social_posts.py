"""Social posts served at /helm/social_posts.

The host app registers a provider, ``HELM_ANALYTICS['social_posts_provider']``:
a dotted path to a callable ``(since) -> iterable[SocialPost]``. ``since`` is an
aware UTC datetime (a naive UTC one when ``USE_TZ`` is off) or ``None`` (meaning
all posts). The SDK validates every post
and serves it; it does not know what the posts are or where they come from.

Contract (field names are fixed): ``id``, ``platform``, ``desired_post_time``,
``campaign``, ``requires_review``, ``updated_at``, ``content``. First version:
platform ``facebook`` and content type ``facebook_link`` (``type``, ``message``,
``link``). Timestamps go out as ISO 8601 in UTC.

A post that breaks the rules raises ``InvalidSocialPost`` in the host app, so a
malformed post never reaches Helm.
"""
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from django.conf import settings
from django.utils.module_loading import import_string

from django_helm.analytics.user_accounts import parse_since
from django_helm.endpoints import BadRequest, helm_endpoint

MAX_POSTS = 50
MAX_MESSAGE_LENGTH = 2000
# Helm refuses SDK responses over 64 KB; stay well under it.
MAX_RESPONSE_BYTES = 48_000
PLATFORMS = ('facebook',)

_MAX_ID = 200
_MAX_CAMPAIGN = 100
_MAX_LINK = 2048
_CAMPAIGN_RE = re.compile(r'^[-a-zA-Z0-9_]+$')


class InvalidSocialPost(ValueError):
    """A post from the provider breaks the contract. Raised in the host app."""


def facebook_link(message: str, link: str) -> dict:
    """Return the ``facebook_link`` content for a post."""
    return {'type': 'facebook_link', 'message': message, 'link': link}


@dataclass(frozen=True)
class SocialPost:
    id: str
    platform: str
    desired_post_time: datetime
    updated_at: datetime
    content: dict
    campaign: str | None = None
    requires_review: bool = False


def _fail(post_id, field, problem):
    raise InvalidSocialPost(f'social post {post_id!r}: {field} {problem}')


def _utc_iso(post_id, field, value):
    if not isinstance(value, datetime):
        _fail(post_id, field, 'must be a datetime')
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _valid_link(link):
    if not isinstance(link, str) or len(link) > _MAX_LINK:
        return False
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in link):
        return False
    try:
        parts = urlsplit(link)
        return parts.scheme in ('http', 'https') and bool(parts.netloc and parts.hostname)
    except ValueError:
        return False


def _content(post_id, content):
    if not isinstance(content, dict) or content.get('type') != 'facebook_link':
        _fail(post_id, 'content', "must be a dict with type 'facebook_link'")
    message = content.get('message')
    if not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE_LENGTH:
        _fail(post_id, 'message', f'must be a non-blank string of at most {MAX_MESSAGE_LENGTH} characters')
    link = content.get('link')
    if not _valid_link(link):
        _fail(post_id, 'link', f'must be an http(s) URL with a host, no whitespace, '
                               f'of at most {_MAX_LINK} characters')
    return {'type': 'facebook_link', 'message': message, 'link': link}


def serialize(post: SocialPost) -> dict:
    """Validate one post and return its JSON-ready dict. Raises InvalidSocialPost."""
    if not isinstance(post, SocialPost):
        raise InvalidSocialPost(f'provider must return SocialPost objects, got {type(post).__name__}')
    pid = post.id
    if not isinstance(pid, str) or not pid.strip() or len(pid) > _MAX_ID:
        _fail(pid, 'id', f'must be a string of 1-{_MAX_ID} characters')
    if post.platform not in PLATFORMS:
        _fail(pid, 'platform', f'must be one of {PLATFORMS}')
    desired = _utc_iso(pid, 'desired_post_time', post.desired_post_time)
    updated = _utc_iso(pid, 'updated_at', post.updated_at)
    campaign = post.campaign
    if campaign is not None and not (
        isinstance(campaign, str) and len(campaign) <= _MAX_CAMPAIGN
        and _CAMPAIGN_RE.fullmatch(campaign)
    ):
        _fail(pid, 'campaign', f'must be None or 1-{_MAX_CAMPAIGN} characters of letters, digits, - and _')
    if not isinstance(post.requires_review, bool):
        _fail(pid, 'requires_review', 'must be a bool')
    return {
        'id': pid,
        'platform': post.platform,
        'desired_post_time': desired,
        'campaign': campaign,
        'requires_review': post.requires_review,
        'updated_at': updated,
        'content': _content(pid, post.content),
    }


def _utc(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _cursor(body):
    """Return (since, after_id) from the request body."""
    raw_since = body.get('since')
    since = None if raw_since is None else parse_since(raw_since)
    after_id = body.get('after_id')
    if after_id is None:
        return since, None
    if not isinstance(after_id, str) or not 1 <= len(after_id) <= _MAX_ID:
        raise BadRequest(f'after_id must be a string of at most {_MAX_ID} characters')
    if since is None:
        raise BadRequest('after_id requires since')
    return since, after_id


@helm_endpoint('social_posts')
def social_posts(request, body):
    config = settings.HELM_ANALYTICS
    dotted = config.get('social_posts_provider')
    if not dotted:
        raise BadRequest('social posts are not configured')
    since, after_id = _cursor(body)
    provider = import_string(dotted)
    keyed = [(datetime.fromisoformat(p['updated_at']), p['id'], p)
             for p in (serialize(post) for post in provider(since))]
    keyed.sort(key=lambda k: k[:2])
    if after_id is not None:
        seen_up_to = (_utc(since), after_id)
        keyed = [k for k in keyed if k[:2] > seen_up_to]
    serialized = [k[2] for k in keyed]
    posts, total = [], 0
    for post in serialized[:MAX_POSTS]:
        # Same escaping JsonResponse uses (ensure_ascii), so this is the wire size.
        total += len(json.dumps(post))
        if posts and total > MAX_RESPONSE_BYTES:
            break
        posts.append(post)
    has_more = len(posts) < len(serialized)
    return {
        'project': config['project'],
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'count': len(posts),
        'has_more': has_more,
        'next': {'since': posts[-1]['updated_at'], 'after_id': posts[-1]['id']} if has_more else None,
        'posts': posts,
    }
