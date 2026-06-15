"""Helm logging handler — ships Python logs to Helm's OTLP endpoint."""

import atexit
import json
import logging
import os
import queue as queue_module
import threading
import traceback

import requests


_PROD_ENV_NAMES = {'prod', 'production'}


def _looks_prod(environment):
    env = (environment or '').strip().lower()
    return env in _PROD_ENV_NAMES or env.startswith('prod-') or env.startswith('production')


# Python log level -> (OTel severity number, OTel severity text)
_LEVEL_MAP = {
    10: (5, 'DEBUG'),     # DEBUG
    20: (9, 'INFO'),      # INFO
    30: (13, 'WARN'),     # WARNING
    40: (17, 'ERROR'),    # ERROR
    50: (21, 'FATAL'),    # CRITICAL
}

_LEVEL_THRESHOLDS = sorted(_LEVEL_MAP.keys())

# Process-local drop metric. Named metrics_snapshot()/reset_metrics() rather than
# the bare snapshot()/reset() used by analytics/metrics.py: these live at the top
# level of the logging module (not a dedicated `metrics` submodule), so the longer
# names stay unambiguous at the call site, e.g. hlog.metrics_snapshot().
_metrics_lock = threading.Lock()
_metrics = {'log_drops_total': 0}


def _increment_drops(n=1):
    with _metrics_lock:
        _metrics['log_drops_total'] += n


def metrics_snapshot():
    """Return a copy of the log drop counters. Safe to mutate."""
    with _metrics_lock:
        return dict(_metrics)


def reset_metrics():
    """Zero all counters. Intended for tests."""
    with _metrics_lock:
        for k in _metrics:
            _metrics[k] = 0


def _map_level(level):
    """Map a Python log level to (OTel severity number, OTel severity text).

    Unknown levels fall to the nearest lower bracket. Levels below DEBUG
    map to TRACE (1).
    """
    if level in _LEVEL_MAP:
        return _LEVEL_MAP[level]

    # Find nearest lower bracket
    for threshold in reversed(_LEVEL_THRESHOLDS):
        if level >= threshold:
            return _LEVEL_MAP[threshold]

    # Below DEBUG
    return (1, 'TRACE')


def _record_to_otlp(record):
    """Convert a Python logging.LogRecord to an OTLP logRecord dict."""
    sev_num, sev_text = _map_level(record.levelno)

    # Build body — append traceback if present
    body = record.getMessage()
    if record.exc_info and record.exc_info[0] is not None:
        tb = traceback.format_exception(*record.exc_info)
        body = body + '\n' + ''.join(tb)

    # Timestamp: record.created is a float (seconds since epoch)
    time_unix_nano = str(int(record.created * 1_000_000_000))

    return {
        'timeUnixNano': time_unix_nano,
        'severityNumber': sev_num,
        'severityText': sev_text,
        'body': {'stringValue': body},
        'attributes': [
            {'key': 'logger', 'value': {'stringValue': record.name}},
            {'key': 'pathname', 'value': {'stringValue': record.pathname}},
            {'key': 'lineno', 'value': {'intValue': str(record.lineno)}},
            {'key': 'funcName', 'value': {'stringValue': record.funcName or ''}},
        ],
    }


def _build_otlp_payload(resource_attrs, otlp_records):
    """Build an OTLP/HTTP JSON payload from resource attributes and log records."""
    return {
        'resourceLogs': [
            {
                'resource': {
                    'attributes': resource_attrs,
                },
                'scopeLogs': [
                    {
                        'scope': {'name': 'django_helm'},
                        'logRecords': otlp_records,
                    }
                ],
            }
        ]
    }


class _RecursionGuard(threading.local):
    """Thread-local flag to prevent the handler from re-emitting its own logs."""
    emitting = False


class HelmLogHandler(logging.Handler):
    """Logging handler that converts records to OTLP format and puts them on a queue."""

    def __init__(self, log_queue, level=logging.DEBUG):
        super().__init__(level=level)
        self._queue = log_queue
        self._guard = _RecursionGuard()

    def emit(self, record):
        if self._guard.emitting:
            return
        self._guard.emitting = True
        try:
            otlp = _record_to_otlp(record)
            try:
                self._queue.put_nowait(otlp)
            except queue_module.Full:
                # Drop the oldest to make room for the newest, then count it.
                try:
                    self._queue.get_nowait()
                except queue_module.Empty:
                    pass
                _increment_drops()
                try:
                    self._queue.put_nowait(otlp)
                except queue_module.Full:
                    _increment_drops()
        except Exception:
            pass  # never disrupt the application
        finally:
            self._guard.emitting = False


class HelmBatchWorker(threading.Thread):
    """Daemon thread that drains a queue and ships batched OTLP payloads to Helm."""

    def __init__(self, log_queue, resource_attrs, api_key, endpoint,
                 batch_size=50, flush_interval=5.0):
        super().__init__(daemon=True)
        self._queue = log_queue
        self._resource_attrs = resource_attrs
        self._api_key = api_key
        self._endpoint = endpoint
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._stop_event = threading.Event()

    def run(self):
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._flush_interval)
            self._flush_batch()

    def stop(self):
        self._stop_event.set()

    def flush(self):
        """Drain remaining queue items and send a final batch."""
        self._flush_batch()

    def _flush_batch(self):
        records = []
        try:
            while len(records) < self._batch_size:
                records.append(self._queue.get_nowait())
        except queue_module.Empty:
            pass

        if not records:
            return

        payload = _build_otlp_payload(self._resource_attrs, records)
        try:
            requests.post(
                self._endpoint,
                data=json.dumps(payload),
                headers={
                    'Authorization': f'Bearer {self._api_key}',
                    'Content-Type': 'application/json',
                },
                timeout=10,
            )
        except Exception:
            pass  # Silently drop — ephemeral logs


# Module-level state for idempotent init
_state = {}


def _build_resource_attrs(environment, commit_sha, service_name):
    """Build the OTLP resource attributes list."""
    attrs = [
        {'key': 'helm.environment', 'value': {'stringValue': environment}},
        {'key': 'service.name', 'value': {'stringValue': service_name}},
    ]
    if commit_sha:
        attrs.append({'key': 'helm.commit.sha', 'value': {'stringValue': commit_sha}})
    return attrs


def init(api_key, endpoint, environment, commit_sha='', service_name='django',
         level='DEBUG', batch_size=50, flush_interval=5.0, queue_max=10_000,
         loggers=None):
    """Initialize Helm logging. Call once in settings.py.

    Attaches a handler to the root logger that ships logs to Helm's OTLP
    endpoint via a background thread.

    Optionally attach the handler to additional named loggers (besides root)
    via the ``loggers`` argument (a list of logger name strings).
    """
    # Tear down any previous init FIRST, so a re-init (incl. one that aborts on
    # the prod-gate below) detaches the previously-attached handler from root
    # and every named logger.
    if _state.get('handler'):
        old = _state['handler']
        logging.getLogger().removeHandler(old)
        for name in _state.get('attached_loggers', []):
            logging.getLogger(name).removeHandler(old)
    if _state.get('worker'):
        _state['worker'].stop()
    _state.clear()

    if _looks_prod(environment):
        logging.getLogger(__name__).warning(
            'Helm logging disabled: refusing to ship from a production-like '
            'environment (%s). Use Sentry for production.', environment,
        )
        return

    # Build resource attributes
    resource_attrs = _build_resource_attrs(environment, commit_sha, service_name)

    # Create queue, handler, and worker
    log_queue = queue_module.Queue(maxsize=queue_max)
    log_level = getattr(logging, level.upper(), logging.DEBUG)

    handler = HelmLogHandler(log_queue, level=log_level)
    worker = HelmBatchWorker(
        log_queue, resource_attrs,
        api_key=api_key,
        endpoint=endpoint,
        batch_size=batch_size,
        flush_interval=flush_interval,
    )

    # Attach handler to root logger, then to any named loggers
    logging.getLogger().addHandler(handler)
    attached = []
    for name in (loggers or []):
        logging.getLogger(name).addHandler(handler)
        attached.append(name)

    worker.start()

    # Register atexit flush
    atexit.register(worker.flush)

    # Store state for idempotent re-init
    _state['handler'] = handler
    _state['worker'] = worker
    _state['queue'] = log_queue
    _state['attached_loggers'] = attached


def init_from_env(loggers=None):
    """Initialize Helm logging from the HELM_* env vars Helm injects.

    No-ops silently when HELM_INGEST_TOKEN is absent (the normal production
    path -- no token is ever minted for prod). Warns and no-ops if the
    environment looks production-like even when a token is present.

    Optionally attach the handler to additional named loggers (besides root)
    via the ``loggers`` arg or the comma-separated ``HELM_LOG_LOGGERS`` env var.
    When both are provided the explicit ``loggers`` argument wins.
    """
    token = os.environ.get('HELM_INGEST_TOKEN')
    if not token:
        return  # no token minted: silent no-op (production path)

    environment = os.environ.get('HELM_ENVIRONMENT', '')
    endpoint = os.environ.get('HELM_LOG_ENDPOINT')
    if not endpoint:
        logging.getLogger(__name__).warning(
            'Helm logging disabled: HELM_LOG_ENDPOINT is not set.',
        )
        return

    if loggers is None:
        raw = os.environ.get('HELM_LOG_LOGGERS', '')
        loggers = [n.strip() for n in raw.split(',') if n.strip()] or None

    init(
        api_key=token,
        endpoint=endpoint,
        environment=environment,
        commit_sha=os.environ.get('HELM_COMMIT_SHA', ''),
        loggers=loggers,
    )
