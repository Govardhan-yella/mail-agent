"""Tests for mail_agent send retry and related helpers."""

import urllib.error
import mail_agent


def test_send_with_retry_succeeds_on_first_try(monkeypatch):
    called = []

    def fake_urlopen(request, timeout):
        called.append(request.data.decode())
        class FakeResp:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        return FakeResp()

    monkeypatch.setattr(mail_agent.urllib.request, "urlopen", fake_urlopen)
    mail_agent.send_with_retry(
        "https://example.invalid/test",
        b"body=hello",
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert len(called) == 1
    assert called[0] == "body=hello"


def test_send_with_retry_retries_then_succeeds(monkeypatch):
    attempts = []

    def fake_urlopen(request, timeout):
        attempts.append(request.data.decode())
        if len(attempts) < 2:
            raise urllib.error.URLError("transient")
        class FakeResp:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        return FakeResp()

    monkeypatch.setattr(mail_agent.urllib.request, "urlopen", fake_urlopen)
    mail_agent.send_with_retry(
        "https://example.invalid/test",
        b"body=hello",
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert len(attempts) == 2
    assert attempts[0] == "body=hello"
    assert attempts[1] == "body=hello"


def test_send_with_retry_raises_after_max_attempts(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("always")

    monkeypatch.setattr(mail_agent.urllib.request, "urlopen", fake_urlopen)
    try:
        mail_agent.send_with_retry(
            "https://example.invalid/test",
            b"body=hello",
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
    except RuntimeError as e:
        assert "after 3 attempts" in str(e)
    else:
        raise AssertionError("expected RuntimeError")
