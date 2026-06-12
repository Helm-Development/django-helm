import logging

from django_helm import logging as hlog


def teardown_function():
    # Detach any handler init() attached so tests don't leak across runs.
    root = logging.getLogger()
    if hlog._state.get('handler'):
        root.removeHandler(hlog._state['handler'])
    if hlog._state.get('worker'):
        hlog._state['worker'].stop()
    hlog._state.clear()


def _set_env(monkeypatch, **kw):
    for key in ('HELM_INGEST_TOKEN', 'HELM_LOG_ENDPOINT', 'HELM_ENVIRONMENT', 'HELM_COMMIT_SHA'):
        monkeypatch.delenv(key, raising=False)
    for key, val in kw.items():
        monkeypatch.setenv(key, val)


def test_no_token_is_silent_noop(monkeypatch):
    _set_env(monkeypatch)  # no token
    hlog.init_from_env()
    assert hlog._state.get('handler') is None


def test_prod_like_env_warns_and_noops(monkeypatch, caplog):
    _set_env(monkeypatch, HELM_INGEST_TOKEN='hlit_x',
             HELM_LOG_ENDPOINT='https://h/api/v1/logs', HELM_ENVIRONMENT='production')
    with caplog.at_level(logging.WARNING):
        hlog.init_from_env()
    assert hlog._state.get('handler') is None
    assert any('production' in r.message.lower() for r in caplog.records)


def test_prod_prefixed_env_noops(monkeypatch):
    _set_env(monkeypatch, HELM_INGEST_TOKEN='hlit_x',
             HELM_LOG_ENDPOINT='https://h/api/v1/logs', HELM_ENVIRONMENT='prod-eu')
    hlog.init_from_env()
    assert hlog._state.get('handler') is None


def test_valid_nonprod_attaches_handler(monkeypatch):
    _set_env(monkeypatch, HELM_INGEST_TOKEN='hlit_x',
             HELM_LOG_ENDPOINT='https://h/api/v1/logs', HELM_ENVIRONMENT='pr-12',
             HELM_COMMIT_SHA='abc')
    hlog.init_from_env()
    assert hlog._state.get('handler') is not None


def test_explicit_init_also_prod_gated(monkeypatch):
    hlog.init(api_key='hlit_x', endpoint='https://h/api/v1/logs', environment='production')
    assert hlog._state.get('handler') is None
