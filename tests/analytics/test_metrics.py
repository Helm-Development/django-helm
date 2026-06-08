import pytest

from django_helm.analytics import metrics


@pytest.fixture(autouse=True)
def reset_metrics():
    """Each test starts with a zeroed counter."""
    metrics.reset()
    yield
    metrics.reset()


def test_initial_state():
    snap = metrics.snapshot()
    assert snap['analytics_drops_total'] == 0


def test_increment_drops():
    metrics.increment_drops(3)
    snap = metrics.snapshot()
    assert snap['analytics_drops_total'] == 3


def test_increment_drops_default_one():
    metrics.increment_drops()
    metrics.increment_drops()
    snap = metrics.snapshot()
    assert snap['analytics_drops_total'] == 2


def test_snapshot_is_a_copy():
    """Mutating the returned dict must not affect internal state."""
    metrics.increment_drops()
    snap = metrics.snapshot()
    snap['analytics_drops_total'] = 999
    snap2 = metrics.snapshot()
    assert snap2['analytics_drops_total'] == 1


def test_reset_clears_counters():
    metrics.increment_drops(5)
    metrics.reset()
    assert metrics.snapshot()['analytics_drops_total'] == 0
