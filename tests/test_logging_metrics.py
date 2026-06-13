import queue as queue_module

from django_helm import logging as hlog


def setup_function():
    hlog.reset_metrics()


def test_snapshot_starts_at_zero():
    assert hlog.metrics_snapshot()['log_drops_total'] == 0


def test_emit_drops_oldest_when_full_and_counts():
    q = queue_module.Queue(maxsize=1)
    handler = hlog.HelmLogHandler(q)
    r1 = __import__('logging').LogRecord('n', 20, __file__, 1, 'first', None, None)
    r2 = __import__('logging').LogRecord('n', 20, __file__, 1, 'second', None, None)
    handler.emit(r1)   # fills the queue
    handler.emit(r2)   # queue full -> drop oldest, enqueue newest, count 1 drop
    assert hlog.metrics_snapshot()['log_drops_total'] == 1
    # Newest survived (oldest was dropped).
    remaining = q.get_nowait()
    assert remaining['body']['stringValue'] == 'second'


def test_multiple_drops_accumulate():
    import logging as _logging
    q = queue_module.Queue(maxsize=1)
    handler = hlog.HelmLogHandler(q)
    handler.emit(_logging.LogRecord('n', 20, __file__, 1, 'seed', None, None))  # fills queue
    for i in range(3):
        handler.emit(_logging.LogRecord('n', 20, __file__, 1, f'm{i}', None, None))
    assert hlog.metrics_snapshot()['log_drops_total'] == 3


def test_reset_clears_counters():
    hlog._increment_drops(5)
    assert hlog.metrics_snapshot()['log_drops_total'] == 5
    hlog.reset_metrics()
    assert hlog.metrics_snapshot()['log_drops_total'] == 0


def test_snapshot_is_a_copy():
    snap = hlog.metrics_snapshot()
    snap['log_drops_total'] = 999
    assert hlog.metrics_snapshot()['log_drops_total'] == 0
