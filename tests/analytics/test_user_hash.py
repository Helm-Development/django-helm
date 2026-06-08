import pytest

from django_helm.analytics.user_hash import derive_user_hash


def test_returns_32_char_lowercase_hex():
    result = derive_user_hash('proj', 1, 'salt')
    assert len(result) == 32
    assert all(c in '0123456789abcdef' for c in result)


def test_deterministic_same_inputs_same_output():
    a = derive_user_hash('proj', 42, 'my-salt')
    b = derive_user_hash('proj', 42, 'my-salt')
    assert a == b


def test_different_project_different_hash():
    a = derive_user_hash('proj_a', 42, 'my-salt')
    b = derive_user_hash('proj_b', 42, 'my-salt')
    assert a != b


def test_different_user_id_different_hash():
    a = derive_user_hash('proj', 1, 'my-salt')
    b = derive_user_hash('proj', 2, 'my-salt')
    assert a != b


def test_different_salt_different_hash():
    a = derive_user_hash('proj', 1, 'salt-a')
    b = derive_user_hash('proj', 1, 'salt-b')
    assert a != b


def test_user_id_can_be_int_or_str():
    a = derive_user_hash('proj', 42, 'salt')
    b = derive_user_hash('proj', '42', 'salt')
    # Both stringify identically, so should produce the same hash
    assert a == b


def test_empty_salt_raises():
    with pytest.raises(ValueError):
        derive_user_hash('proj', 1, '')


def test_none_salt_raises():
    with pytest.raises(ValueError):
        derive_user_hash('proj', 1, None)  # type: ignore[arg-type]
