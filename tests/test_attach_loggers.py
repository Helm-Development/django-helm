import logging

from django_helm import logging as hlog


def teardown_function():
    root = logging.getLogger()
    h = hlog._state.get('handler')
    if h:
        root.removeHandler(h)
        for name in hlog._state.get('attached_loggers', []):
            logging.getLogger(name).removeHandler(h)
    if hlog._state.get('worker'):
        hlog._state['worker'].stop()
    hlog._state.clear()


def _init(**kw):
    hlog.init(api_key='hlit_x', endpoint='https://h/api/v1/logs', environment='pr-1', **kw)


def test_attaches_to_named_loggers_and_root():
    _init(loggers=['svc.a', 'svc.b'])
    h = hlog._state['handler']
    assert h in logging.getLogger().handlers
    assert h in logging.getLogger('svc.a').handlers
    assert h in logging.getLogger('svc.b').handlers
    assert hlog._state['attached_loggers'] == ['svc.a', 'svc.b']


def test_default_root_only():
    _init()
    h = hlog._state['handler']
    assert h in logging.getLogger().handlers
    assert hlog._state.get('attached_loggers') == []


def test_reinit_detaches_old_named_loggers():
    _init(loggers=['svc.a'])
    h1 = hlog._state['handler']
    assert h1 in logging.getLogger('svc.a').handlers
    _init(loggers=['svc.b'])
    h2 = hlog._state['handler']
    assert h1 not in logging.getLogger('svc.a').handlers   # old handler detached
    assert h2 in logging.getLogger('svc.b').handlers
    assert h2 not in logging.getLogger('svc.a').handlers


def test_reinit_into_prod_detaches_named_loggers():
    _init(loggers=['svc.a'])
    h1 = hlog._state['handler']
    hlog.init(api_key='hlit_x', endpoint='https://h/api/v1/logs',
              environment='production', loggers=['svc.a'])
    assert hlog._state.get('handler') is None
    assert h1 not in logging.getLogger('svc.a').handlers
    assert h1 not in logging.getLogger().handlers


def _set_env(monkeypatch, **kw):
    for k in ('HELM_INGEST_TOKEN', 'HELM_LOG_ENDPOINT', 'HELM_ENVIRONMENT',
              'HELM_COMMIT_SHA', 'HELM_LOG_LOGGERS'):
        monkeypatch.delenv(k, raising=False)
    for k, v in kw.items():
        monkeypatch.setenv(k, v)


def test_init_from_env_reads_logger_env(monkeypatch):
    _set_env(monkeypatch, HELM_INGEST_TOKEN='hlit_x',
             HELM_LOG_ENDPOINT='https://h/api/v1/logs', HELM_ENVIRONMENT='pr-1',
             HELM_LOG_LOGGERS='svc.a, svc.b')
    hlog.init_from_env()
    h = hlog._state['handler']
    assert h in logging.getLogger('svc.a').handlers
    assert h in logging.getLogger('svc.b').handlers


def test_init_from_env_arg_overrides_env(monkeypatch):
    _set_env(monkeypatch, HELM_INGEST_TOKEN='hlit_x',
             HELM_LOG_ENDPOINT='https://h/api/v1/logs', HELM_ENVIRONMENT='pr-1',
             HELM_LOG_LOGGERS='env.logger')
    hlog.init_from_env(loggers=['arg.logger'])
    h = hlog._state['handler']
    assert h in logging.getLogger('arg.logger').handlers
    assert h not in logging.getLogger('env.logger').handlers
