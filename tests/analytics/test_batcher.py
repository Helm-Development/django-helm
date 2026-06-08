import json
import queue as queue_module
import time
from unittest.mock import MagicMock, patch

import pytest

from django_helm.analytics import metrics
from django_helm.analytics.batcher import (
    HelmAnalyticsBatchWorker,
    enqueue,
    get_worker,
    _reset_worker_for_tests,
)


@pytest.fixture(autouse=True)
def reset_state():
    """Each test starts with no worker and zero metrics."""
    _reset_worker_for_tests()
    metrics.reset()
    yield
    _reset_worker_for_tests()
    metrics.reset()


class TestEnqueue:
    def test_enqueue_starts_worker_lazily(self):
        assert get_worker() is None
        enqueue({'method': 'GET'})
        assert get_worker() is not None

    def test_enqueue_drops_oldest_when_queue_full(self):
        worker = HelmAnalyticsBatchWorker(
            api_base_url='https://helm.test',
            sas_token='sas_test',
            batch_size=50,
            batch_interval_s=5.0,
            queue_max=3,
        )
        # Don't start the worker; just exercise the queue logic
        worker.enqueue({'i': 1})
        worker.enqueue({'i': 2})
        worker.enqueue({'i': 3})
        worker.enqueue({'i': 4})
        # Queue should still be at size 3, oldest dropped
        assert worker._queue.qsize() == 3
        assert metrics.snapshot()['analytics_drops_total'] == 1


class TestBatchFlush:
    def test_flushes_when_batch_size_reached(self):
        with patch('django_helm.analytics.batcher.requests.post') as mock_post:
            mock_post.return_value = MagicMock(status_code=202)
            worker = HelmAnalyticsBatchWorker(
                api_base_url='https://helm.test', sas_token='sas_test',
                batch_size=3, batch_interval_s=60.0, queue_max=100,
            )
            for i in range(3):
                worker.enqueue({'i': i})
            worker._flush()
            assert mock_post.called
            args, kwargs = mock_post.call_args
            assert args[0] == 'https://helm.test/api/v1/analytics/api-hits/'
            assert kwargs['headers']['Authorization'] == 'Bearer sas_test'
            assert json.loads(kwargs['data']) == {'hits': [{'i': 0}, {'i': 1}, {'i': 2}]}

    def test_flushes_empty_queue_is_noop(self):
        with patch('django_helm.analytics.batcher.requests.post') as mock_post:
            worker = HelmAnalyticsBatchWorker(
                api_base_url='https://helm.test', sas_token='sas_test',
                batch_size=50, batch_interval_s=5.0, queue_max=100,
            )
            worker._flush()
            assert not mock_post.called


class TestRetryBackoff:
    def test_increments_backoff_on_failure(self):
        with patch('django_helm.analytics.batcher.requests.post') as mock_post:
            mock_post.side_effect = Exception('network down')
            worker = HelmAnalyticsBatchWorker(
                api_base_url='https://helm.test', sas_token='sas_test',
                batch_size=50, batch_interval_s=5.0, queue_max=100,
            )
            worker.enqueue({'i': 1})
            initial_backoff = worker._backoff_s
            worker._flush()
            assert worker._backoff_s > initial_backoff

    def test_resets_backoff_on_success(self):
        with patch('django_helm.analytics.batcher.requests.post') as mock_post:
            mock_post.return_value = MagicMock(status_code=202)
            worker = HelmAnalyticsBatchWorker(
                api_base_url='https://helm.test', sas_token='sas_test',
                batch_size=50, batch_interval_s=5.0, queue_max=100,
            )
            worker._backoff_s = 30.0  # simulate prior failure
            worker.enqueue({'i': 1})
            worker._flush()
            assert worker._backoff_s == HelmAnalyticsBatchWorker.BACKOFF_INITIAL_S

    def test_429_honors_retry_after(self):
        with patch('django_helm.analytics.batcher.requests.post') as mock_post:
            response = MagicMock(status_code=429)
            response.headers = {'Retry-After': '7'}
            mock_post.return_value = response
            worker = HelmAnalyticsBatchWorker(
                api_base_url='https://helm.test', sas_token='sas_test',
                batch_size=50, batch_interval_s=5.0, queue_max=100,
            )
            worker.enqueue({'i': 1})
            worker._flush()
            assert worker._backoff_s == 7.0

    def test_backoff_capped(self):
        with patch('django_helm.analytics.batcher.requests.post') as mock_post:
            mock_post.side_effect = Exception('still down')
            worker = HelmAnalyticsBatchWorker(
                api_base_url='https://helm.test', sas_token='sas_test',
                batch_size=50, batch_interval_s=5.0, queue_max=100,
            )
            worker._backoff_s = HelmAnalyticsBatchWorker.BACKOFF_MAX_S
            worker.enqueue({'i': 1})
            worker._flush()
            assert worker._backoff_s == HelmAnalyticsBatchWorker.BACKOFF_MAX_S


class TestWorkerLifecycle:
    def test_get_worker_returns_same_instance(self):
        enqueue({'method': 'GET'})
        w1 = get_worker()
        w2 = get_worker()
        assert w1 is w2

    def test_shutdown_drains_queue(self):
        with patch('django_helm.analytics.batcher.requests.post') as mock_post:
            mock_post.return_value = MagicMock(status_code=202)
            worker = HelmAnalyticsBatchWorker(
                api_base_url='https://helm.test', sas_token='sas_test',
                batch_size=50, batch_interval_s=5.0, queue_max=100,
            )
            worker.enqueue({'i': 1})
            worker.enqueue({'i': 2})
            worker.shutdown(timeout=2.0)
            # Both items should have been flushed
            assert mock_post.called
            args, kwargs = mock_post.call_args
            assert json.loads(kwargs['data']) == {'hits': [{'i': 1}, {'i': 2}]}
