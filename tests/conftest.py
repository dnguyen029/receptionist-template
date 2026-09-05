import pytest


@pytest.fixture(autouse=True)
def test_environment(monkeypatch):
    """Automatically set mock environment variables for offline test determinism.

    Guarantees that unit tests run on any developer or CI machine without needing
    a local .env file or live cloud credentials.
    """
    monkeypatch.setenv("EXA_API_KEY", "mock-test-exa-key")
    monkeypatch.setenv("WISMO_SPREADSHEET_ID", "mock-wismo-sheet-id")
    monkeypatch.setenv("SPREADSHEET_ID", "mock-leads-sheet-id")
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "example-retail")
    monkeypatch.setenv("ZENDESK_EMAIL", "agent@example.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "mock-zd-token")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "mock-gcp-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
