import sys
import types

from ouroboros import phoenix_tracing


def test_phoenix_tracing_disabled_by_default(monkeypatch):
    monkeypatch.delenv("PHOENIX_ENABLED", raising=False)

    assert phoenix_tracing.setup_phoenix_tracing() is False


def test_phoenix_tracing_registers_http_collector(monkeypatch):
    calls = []
    fake_module = types.ModuleType("phoenix.otel")
    fake_module.register = lambda **kwargs: calls.append(kwargs)
    monkeypatch.setitem(sys.modules, "phoenix.otel", fake_module)
    monkeypatch.setenv("PHOENIX_ENABLED", "true")
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://phoenix:6006")
    monkeypatch.setenv("PHOENIX_PROJECT_NAME", "ouroboros-test")
    monkeypatch.setenv("PHOENIX_PROTOCOL", "http/protobuf")
    monkeypatch.setenv("PHOENIX_BATCH", "false")

    assert phoenix_tracing.setup_phoenix_tracing() is True
    assert calls == [
        {
            "project_name": "ouroboros-test",
            "endpoint": "http://phoenix:6006/v1/traces",
            "protocol": "http/protobuf",
            "batch": False,
            "auto_instrument": True,
        }
    ]


def test_phoenix_tracing_failure_does_not_stop_server(monkeypatch):
    fake_module = types.ModuleType("phoenix.otel")

    def fail_register(**_kwargs):
        raise RuntimeError("collector unavailable")

    fake_module.register = fail_register
    monkeypatch.setitem(sys.modules, "phoenix.otel", fake_module)
    monkeypatch.setenv("PHOENIX_ENABLED", "true")

    assert phoenix_tracing.setup_phoenix_tracing() is False
