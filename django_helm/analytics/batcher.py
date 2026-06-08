"""Daemon-thread batcher that ships ApiHits to Helm.

One worker per process, started lazily on first enqueue. Drops oldest on
backpressure, exponential backoff on failures (capped at BACKOFF_MAX_S),
respects 429 Retry-After.
"""
import atexit
import json
import queue as queue_module
import threading
import time

import requests

from . import metrics


class HelmAnalyticsBatchWorker(threading.Thread):
    QUEUE_MAX = 10_000
    BATCH_SIZE = 50
    BATCH_INTERVAL_S = 5.0
    BACKOFF_INITIAL_S = 1.0
    BACKOFF_MAX_S = 60.0

    def __init__(self, api_base_url, sas_token,
                 batch_size=BATCH_SIZE, batch_interval_s=BATCH_INTERVAL_S,
                 queue_max=QUEUE_MAX):
        super().__init__(daemon=True)
        self._api_base_url = api_base_url.rstrip('/')
        self._sas_token = sas_token
        self._batch_size = batch_size
        self._batch_interval_s = batch_interval_s
        self._queue = queue_module.Queue(maxsize=queue_max)
        self._stop_event = threading.Event()
        self._backoff_s = self.BACKOFF_INITIAL_S
        self._lock = threading.Lock()

    def enqueue(self, hit: dict) -> None:
        """Add a hit to the queue. Drops oldest if full."""
        try:
            self._queue.put_nowait(hit)
        except queue_module.Full:
            # Drop oldest to make room
            try:
                self._queue.get_nowait()
            except queue_module.Empty:
                pass
            metrics.increment_drops()
            try:
                self._queue.put_nowait(hit)
            except queue_module.Full:
                # Shouldn't happen — we just made room — but be defensive
                metrics.increment_drops()

    def run(self):
        while not self._stop_event.is_set():
            # Sleep for the configured interval OR the current backoff,
            # whichever is larger. This lets backoff naturally pace failures.
            sleep_s = max(self._batch_interval_s, self._backoff_s)
            self._stop_event.wait(timeout=sleep_s)
            self._flush()

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stop the worker and drain remaining items. Called via atexit."""
        self._stop_event.set()
        self._flush()
        if self.is_alive():
            self.join(timeout=timeout)

    def _flush(self) -> None:
        """Drain up to batch_size items and POST them. Updates backoff."""
        with self._lock:
            hits = []
            try:
                while len(hits) < self._batch_size:
                    hits.append(self._queue.get_nowait())
            except queue_module.Empty:
                pass

            if not hits:
                return

            url = f"{self._api_base_url}/api/v1/analytics/api-hits/"
            body = json.dumps({'hits': hits})
            try:
                resp = requests.post(
                    url,
                    data=body,
                    headers={
                        'Authorization': f'Bearer {self._sas_token}',
                        'Content-Type': 'application/json',
                    },
                    timeout=10,
                )
            except Exception:
                self._increase_backoff()
                return

            if 200 <= resp.status_code < 300:
                self._backoff_s = self.BACKOFF_INITIAL_S
            elif resp.status_code == 429:
                try:
                    retry_after = float(resp.headers.get('Retry-After', '1'))
                except ValueError:
                    retry_after = self._backoff_s * 2
                self._backoff_s = min(retry_after, self.BACKOFF_MAX_S)
            else:
                self._increase_backoff()

    def _increase_backoff(self) -> None:
        self._backoff_s = min(self._backoff_s * 2, self.BACKOFF_MAX_S)


_worker: HelmAnalyticsBatchWorker | None = None
_worker_lock = threading.Lock()


def get_worker() -> HelmAnalyticsBatchWorker | None:
    """Return the module-global worker, or None if not started."""
    return _worker


def enqueue(hit: dict) -> None:
    """Public enqueue entry point. Starts the worker lazily."""
    global _worker
    if _worker is None:
        with _worker_lock:
            if _worker is None:
                from django.conf import settings
                cfg = settings.HELM_ANALYTICS
                _worker = HelmAnalyticsBatchWorker(
                    api_base_url=cfg['api_base_url'],
                    sas_token=cfg['sas_token'],
                    batch_size=cfg.get('batch_size', HelmAnalyticsBatchWorker.BATCH_SIZE),
                    batch_interval_s=cfg.get('batch_interval_s', HelmAnalyticsBatchWorker.BATCH_INTERVAL_S),
                    queue_max=cfg.get('queue_max', HelmAnalyticsBatchWorker.QUEUE_MAX),
                )
                _worker.start()
                atexit.register(_worker.shutdown)
    _worker.enqueue(hit)


def _reset_worker_for_tests() -> None:
    """Test-only helper to tear down the worker between tests."""
    global _worker
    if _worker is not None:
        _worker._stop_event.set()
        _worker = None
