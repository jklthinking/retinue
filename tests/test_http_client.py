import ipaddress
import urllib.error
import urllib.request

import pytest

from server.http_client import InwardRequestError, RequestClass, open_url, proxy_bypass


class _FakeOpener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    def open(self, request, *, timeout):
        if self.error is not None:
            raise self.error
        return self.response


def test_inward_request_bypasses_configured_proxy(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:8080")
    response = object()
    seen_handlers = []

    def build_opener(handler):
        seen_handlers.append(handler)
        return _FakeOpener(response=response)

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)

    result = open_url(
        "http://localhost/api/tasks",
        timeout=15,
        request_class=RequestClass.INWARD,
    )

    assert result is response
    assert seen_handlers[0].proxies == {}


def test_outward_request_honours_configured_proxy(monkeypatch):
    proxy = "http://proxy.example:8080"
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", proxy)
    response = object()
    seen_handlers = []

    def build_opener(handler):
        seen_handlers.append(handler)
        return _FakeOpener(response=response)

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)

    result = open_url(
        "https://im.example/webhook",
        timeout=10,
        request_class=RequestClass.OUTWARD,
    )

    assert result is response
    assert seen_handlers[0].proxies["https"] == proxy


def test_proxy_bypass_understands_private_carrier_and_suffix_rules():
    private_host = str(ipaddress.ip_address(0x0A000001))
    carrier_host = str(ipaddress.ip_address(0x64400001))

    assert proxy_bypass(private_host, environ={})
    assert proxy_bypass(carrier_host, environ={})
    assert proxy_bypass("localhost", environ={})
    assert proxy_bypass(
        "service.internal.example",
        environ={"no_proxy": ".internal.example"},
    )
    assert not proxy_bypass("public.example", environ={})


def test_failed_inward_request_names_safe_bypass_cause(monkeypatch):
    user_info = "user-info:sensitive-value"
    failure = urllib.error.URLError(
        f"proxy http://{user_info}@proxy.example:8080 could not connect"
    )
    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda handler: _FakeOpener(error=failure),
    )

    with pytest.raises(InwardRequestError) as raised:
        open_url(
            "http://localhost/api/tasks",
            timeout=15,
            request_class=RequestClass.INWARD,
        )

    message = str(raised.value)
    assert "proxy bypass selected" in message
    assert "localhost" in message
    assert "proxy.example:8080" in message
    assert "user-info" not in message
    assert "sensitive-value" not in message
