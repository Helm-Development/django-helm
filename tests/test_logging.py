import json
import logging
import queue
import time
import traceback
from unittest.mock import patch, MagicMock

from django_helm.logging import (
    _map_level,
    _record_to_otlp,
    HelmLogHandler,
    _build_otlp_payload,
    HelmBatchWorker,
    init,
    _state,
)


class TestLevelMapping:
    def test_debug(self):
        sev_num, sev_text = _map_level(logging.DEBUG)
        assert sev_num == 5
        assert sev_text == 'DEBUG'

    def test_info(self):
        sev_num, sev_text = _map_level(logging.INFO)
        assert sev_num == 9
        assert sev_text == 'INFO'

    def test_warning(self):
        sev_num, sev_text = _map_level(logging.WARNING)
        assert sev_num == 13
        assert sev_text == 'WARN'

    def test_error(self):
        sev_num, sev_text = _map_level(logging.ERROR)
        assert sev_num == 17
        assert sev_text == 'ERROR'

    def test_critical(self):
        sev_num, sev_text = _map_level(logging.CRITICAL)
        assert sev_num == 21
        assert sev_text == 'FATAL'

    def test_unknown_level_falls_to_nearest_lower(self):
        sev_num, sev_text = _map_level(25)
        assert sev_num == 9
        assert sev_text == 'INFO'

    def test_below_debug(self):
        sev_num, sev_text = _map_level(5)
        assert sev_num == 1
        assert sev_text == 'TRACE'


class TestRecordToOtlp:
    def _make_record(self, msg='test message', level=logging.INFO, name='test.logger',
                     pathname='/app/test.py', lineno=42, func='test_func', exc_info=None):
        record = logging.LogRecord(
            name=name, level=level, pathname=pathname, lineno=lineno,
            msg=msg, args=(), exc_info=exc_info, func=func,
        )
        return record

    def test_basic_fields(self):
        record = self._make_record()
        otlp = _record_to_otlp(record)
        assert otlp['severityNumber'] == 9
        assert otlp['severityText'] == 'INFO'
        assert otlp['body'] == {'stringValue': 'test message'}
        assert 'timeUnixNano' in otlp

    def test_timestamp_is_nanoseconds_string(self):
        record = self._make_record()
        otlp = _record_to_otlp(record)
        nano = int(otlp['timeUnixNano'])
        seconds = nano / 1_000_000_000
        assert seconds > 1577836800
        assert seconds < 1893456000

    def test_attributes_contain_logger_info(self):
        record = self._make_record(name='django.request', pathname='/app/views.py',
                                   lineno=99, func='post')
        otlp = _record_to_otlp(record)
        attrs = {a['key']: a['value'] for a in otlp['attributes']}
        assert attrs['logger'] == {'stringValue': 'django.request'}
        assert attrs['pathname'] == {'stringValue': '/app/views.py'}
        assert attrs['lineno'] == {'intValue': '99'}
        assert attrs['funcName'] == {'stringValue': 'post'}

    def test_exception_appended_to_body(self):
        try:
            raise ValueError('boom')
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = self._make_record(msg='something failed', exc_info=exc_info)
        otlp = _record_to_otlp(record)
        body = otlp['body']['stringValue']
        assert body.startswith('something failed\n')
        assert 'ValueError: boom' in body
        assert 'Traceback' in body

    def test_no_exception(self):
        record = self._make_record(msg='clean log')
        otlp = _record_to_otlp(record)
        assert otlp['body']['stringValue'] == 'clean log'


class TestHelmLogHandler:
    def test_emit_puts_record_on_queue(self):
        q = queue.Queue()
        handler = HelmLogHandler(q)
        logger = logging.getLogger('test.handler')
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info('hello from handler')
        finally:
            logger.removeHandler(handler)

        assert not q.empty()
        otlp = q.get_nowait()
        assert otlp['severityNumber'] == 9
        assert otlp['body']['stringValue'] == 'hello from handler'

    def test_recursion_guard(self):
        q = queue.Queue()
        handler = HelmLogHandler(q)

        handler._guard.emitting = True
        record = logging.LogRecord(
            name='test', level=logging.INFO, pathname='', lineno=0,
            msg='should be dropped', args=(), exc_info=None,
        )
        handler.emit(record)

        assert q.empty()
        handler._guard.emitting = False

    def test_handler_does_not_propagate_errors(self):
        q = queue.Queue()
        handler = HelmLogHandler(q)

        record = logging.LogRecord(
            name='test', level=logging.INFO, pathname='', lineno=0,
            msg='normal message', args=(), exc_info=None,
        )
        handler.emit(record)
        assert not q.empty()


class TestBuildOtlpPayload:
    def _make_otlp_record(self, msg='test', severity_number=9, severity_text='INFO'):
        return {
            'timeUnixNano': '1714000000000000000',
            'severityNumber': severity_number,
            'severityText': severity_text,
            'body': {'stringValue': msg},
            'attributes': [
                {'key': 'logger', 'value': {'stringValue': 'test'}},
            ],
        }

    def test_single_record(self):
        resource_attrs = [
            {'key': 'helm.environment', 'value': {'stringValue': 'staging'}},
            {'key': 'service.name', 'value': {'stringValue': 'backend'}},
        ]
        records = [self._make_otlp_record()]
        payload = _build_otlp_payload(resource_attrs, records)

        assert 'resourceLogs' in payload
        assert len(payload['resourceLogs']) == 1

        rl = payload['resourceLogs'][0]
        assert rl['resource']['attributes'] == resource_attrs
        assert rl['scopeLogs'][0]['scope']['name'] == 'django_helm'
        assert len(rl['scopeLogs'][0]['logRecords']) == 1

    def test_multiple_records(self):
        resource_attrs = [
            {'key': 'helm.environment', 'value': {'stringValue': 'prod'}},
        ]
        records = [self._make_otlp_record(msg='first'), self._make_otlp_record(msg='second')]
        payload = _build_otlp_payload(resource_attrs, records)

        log_records = payload['resourceLogs'][0]['scopeLogs'][0]['logRecords']
        assert len(log_records) == 2
        assert log_records[0]['body']['stringValue'] == 'first'
        assert log_records[1]['body']['stringValue'] == 'second'

    def test_empty_records(self):
        payload = _build_otlp_payload([], [])
        log_records = payload['resourceLogs'][0]['scopeLogs'][0]['logRecords']
        assert len(log_records) == 0


class TestHelmBatchWorker:
    def _make_resource_attrs(self):
        return [
            {'key': 'helm.environment', 'value': {'stringValue': 'test'}},
        ]

    def _make_otlp_record(self, msg='test'):
        return {
            'timeUnixNano': '1714000000000000000',
            'severityNumber': 9,
            'severityText': 'INFO',
            'body': {'stringValue': msg},
            'attributes': [],
        }

    def test_flushes_on_batch_size(self):
        q = queue.Queue()
        resource_attrs = self._make_resource_attrs()
        worker = HelmBatchWorker(
            q, resource_attrs,
            api_key='test-key',
            endpoint='https://example.com/api/v1/logs',
            batch_size=3,
            flush_interval=60.0,
        )

        for i in range(3):
            q.put(self._make_otlp_record(msg=f'msg-{i}'))

        with patch('django_helm.logging.requests') as mock_requests:
            mock_requests.post.return_value = MagicMock(status_code=200)
            worker._flush_batch()

        mock_requests.post.assert_called_once()
        call_kwargs = mock_requests.post.call_args
        payload = json.loads(call_kwargs.kwargs['data'])
        records = payload['resourceLogs'][0]['scopeLogs'][0]['logRecords']
        assert len(records) == 3

    def test_sends_correct_headers(self):
        q = queue.Queue()
        resource_attrs = self._make_resource_attrs()
        worker = HelmBatchWorker(
            q, resource_attrs,
            api_key='my-api-key',
            endpoint='https://helm.example.com/api/v1/logs',
            batch_size=50,
            flush_interval=5.0,
        )
        q.put(self._make_otlp_record())

        with patch('django_helm.logging.requests') as mock_requests:
            mock_requests.post.return_value = MagicMock(status_code=200)
            worker._flush_batch()

        call_kwargs = mock_requests.post.call_args
        assert call_kwargs.kwargs['headers']['Authorization'] == 'Bearer my-api-key'
        assert call_kwargs.kwargs['headers']['Content-Type'] == 'application/json'
        assert call_kwargs.args[0] == 'https://helm.example.com/api/v1/logs'

    def test_silently_drops_on_network_error(self):
        q = queue.Queue()
        resource_attrs = self._make_resource_attrs()
        worker = HelmBatchWorker(
            q, resource_attrs,
            api_key='test-key',
            endpoint='https://example.com/api/v1/logs',
            batch_size=50,
            flush_interval=5.0,
        )
        q.put(self._make_otlp_record())

        with patch('django_helm.logging.requests') as mock_requests:
            mock_requests.post.side_effect = Exception('connection refused')
            worker._flush_batch()

        assert q.empty()

    def test_empty_queue_does_not_send(self):
        q = queue.Queue()
        resource_attrs = self._make_resource_attrs()
        worker = HelmBatchWorker(
            q, resource_attrs,
            api_key='test-key',
            endpoint='https://example.com/api/v1/logs',
            batch_size=50,
            flush_interval=5.0,
        )

        with patch('django_helm.logging.requests') as mock_requests:
            worker._flush_batch()

        mock_requests.post.assert_not_called()


class TestInit:
    def setup_method(self):
        """Clean up any previous init state."""
        if _state.get('handler'):
            logging.getLogger().removeHandler(_state['handler'])
        if _state.get('worker'):
            _state['worker'].stop()
        _state.clear()

    def teardown_method(self):
        """Clean up after test."""
        if _state.get('handler'):
            logging.getLogger().removeHandler(_state['handler'])
        if _state.get('worker'):
            _state['worker'].stop()
        _state.clear()

    def test_attaches_handler_to_root_logger(self):
        with patch('django_helm.logging.requests'):
            init(
                api_key='test-key',
                endpoint='https://example.com/api/v1/logs',
                environment='test',
            )

        root = logging.getLogger()
        assert any(isinstance(h, HelmLogHandler) for h in root.handlers)

    def test_starts_worker_thread(self):
        with patch('django_helm.logging.requests'):
            init(
                api_key='test-key',
                endpoint='https://example.com/api/v1/logs',
                environment='test',
            )

        assert _state['worker'].is_alive()

    def test_idempotent_replaces_handler(self):
        with patch('django_helm.logging.requests'):
            init(
                api_key='key-1',
                endpoint='https://example.com/api/v1/logs',
                environment='test',
            )
            first_handler = _state['handler']
            first_worker = _state['worker']

            init(
                api_key='key-2',
                endpoint='https://example.com/api/v1/logs',
                environment='test',
            )
            second_handler = _state['handler']

        root = logging.getLogger()
        assert first_handler not in root.handlers
        assert second_handler in root.handlers
        assert not first_worker.is_alive() or first_worker._stop_event.is_set()

    def test_default_values(self):
        with patch('django_helm.logging.requests'):
            init(
                api_key='test-key',
                endpoint='https://example.com/api/v1/logs',
                environment='staging',
            )

        worker = _state['worker']
        assert worker._batch_size == 50
        assert worker._flush_interval == 5.0

    def test_custom_service_name_in_resource_attrs(self):
        with patch('django_helm.logging.requests'):
            init(
                api_key='test-key',
                endpoint='https://example.com/api/v1/logs',
                environment='staging',
                service_name='celery-worker',
                commit_sha='deadbeef',
            )

        worker = _state['worker']
        attr_dict = {a['key']: a['value']['stringValue'] for a in worker._resource_attrs}
        assert attr_dict['helm.environment'] == 'staging'
        assert attr_dict['service.name'] == 'celery-worker'
        assert attr_dict['helm.commit.sha'] == 'deadbeef'

    def test_end_to_end_log_reaches_worker(self):
        with patch('django_helm.logging.requests') as mock_requests:
            mock_requests.post.return_value = MagicMock(status_code=200)
            init(
                api_key='test-key',
                endpoint='https://example.com/api/v1/logs',
                environment='test',
                batch_size=1,
                flush_interval=0.1,
            )

            root = logging.getLogger()
            old_level = root.level
            root.setLevel(logging.DEBUG)

            test_logger = logging.getLogger('test.e2e')
            test_logger.info('end-to-end test')

            time.sleep(0.5)

            assert mock_requests.post.called
            call_kwargs = mock_requests.post.call_args
            payload = json.loads(call_kwargs.kwargs['data'])
            records = payload['resourceLogs'][0]['scopeLogs'][0]['logRecords']
            found = any(r['body']['stringValue'] == 'end-to-end test' for r in records)
            assert found

            root.setLevel(old_level)
