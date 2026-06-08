"""Process-local counters for analytics observability.

Exposed via snapshot() so a host can scrape into Sentry breadcrumbs,
a debug endpoint, or wherever else the operator wants signal."""
import threading


_lock = threading.Lock()
_counters = {
    'analytics_drops_total': 0,
}


def increment_drops(n: int = 1) -> None:
    """Increment the drop counter. Thread-safe."""
    with _lock:
        _counters['analytics_drops_total'] += n


def snapshot() -> dict:
    """Return a copy of all counters. Safe to mutate."""
    with _lock:
        return dict(_counters)


def reset() -> None:
    """Zero all counters. Intended for tests."""
    with _lock:
        for k in _counters:
            _counters[k] = 0
