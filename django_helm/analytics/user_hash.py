"""Customer-owned one-way hash of (project_slug, user_id) keyed by a salt.

The salt is the customer's secret. Helm never sees it. Properties:
- Per-project — same user_id in different projects produces different hashes.
- Deterministic within a project — cross-install joins work.
- Irreversible — no way to recover user_id from hash without the salt.
"""
import hashlib


def derive_user_hash(project_slug: str, user_id, salt: str) -> str:
    """Return a 32-char lowercase hex hash of (project_slug, user_id).

    Raises ValueError if salt is empty/None.
    """
    if not salt:
        raise ValueError("salt is required and must be non-empty")
    payload = f"{project_slug}:{user_id}".encode()
    return hashlib.blake2b(payload, key=salt.encode(), digest_size=16).hexdigest()
