from pathlib import Path


def test_readme_documents_endpoints():
    text = Path(__file__).resolve().parents[1].joinpath('README.md').read_text()
    for needle in ('## Backend endpoints for Helm', '/helm/user_accounts', '/helm/analytics_exclusion_list',
                   "include('django_helm.urls')", 'HelmUserAnalyticsInline', 'user_accounts_counter',
                   'hashed_id', 'Always include', 'Always exclude', "'paths'"):
        assert needle in text, needle
