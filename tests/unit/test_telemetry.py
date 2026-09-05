from app.app_utils.telemetry import setup_telemetry


def test_setup_telemetry_disabled(monkeypatch):
    monkeypatch.delenv("LOGS_BUCKET_NAME", raising=False)
    monkeypatch.setenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "true")

    res = setup_telemetry()
    assert res is None


def test_setup_telemetry_enabled(monkeypatch):
    monkeypatch.setenv("LOGS_BUCKET_NAME", "my-logs-bucket")
    monkeypatch.setenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "true")

    res = setup_telemetry()
    assert res == "my-logs-bucket"
