"""Helm logging handler — ships Python logs to Helm's OTLP endpoint."""

import logging
import threading
import traceback


# Python log level -> (OTel severity number, OTel severity text)
_LEVEL_MAP = {
    10: (5, 'DEBUG'),     # DEBUG
    20: (9, 'INFO'),      # INFO
    30: (13, 'WARN'),     # WARNING
    40: (17, 'ERROR'),    # ERROR
    50: (21, 'FATAL'),    # CRITICAL
}

_LEVEL_THRESHOLDS = sorted(_LEVEL_MAP.keys())


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
            self._queue.put_nowait(otlp)
        except Exception:
            pass  # Silently drop — never disrupt the application
        finally:
            self._guard.emitting = False
