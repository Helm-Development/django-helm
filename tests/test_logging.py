import logging
import time
import traceback

from django_helm.logging import (
    _map_level,
    _record_to_otlp,
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
