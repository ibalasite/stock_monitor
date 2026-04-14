from __future__ import annotations

import io
import json
import socket
from urllib import error

import pytest

from stock_monitor.adapters.line_messaging import LinePushClient
from stock_monitor.adapters.market_data_twse import TwseRealtimeMarketDataProvider, _to_float


class _FakeHttpResponse:
    def __init__(self, *, body: str, status: int = 200):
        self._body = body.encode("utf-8")
        self.status = status

    def read(self, *args):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_to_float_handles_dash_invalid_and_numeric():
    assert _to_float(None) is None
    assert _to_float("-") is None
    assert _to_float(" abc ") is None
    assert _to_float("2000.5") == 2000.5


def test_twse_provider_build_url_and_quote_parsing(monkeypatch):
    provider = TwseRealtimeMarketDataProvider(base_url="https://example.test/api")

    rows = {
        "msgArray": [
            {"c": "2330", "a": "2000.0_2005.0_2010.0_2015.0_2020.0_", "z": "2000.0", "tlong": "1775802600000", "n": "台積電"},
            {"c": "2317", "z": "-", "tlong": "1775802600000", "n": "鴻海"},
        ]
    }

    def _fake_urlopen(req, timeout):
        assert timeout == provider.timeout_sec
        assert "tse_2330.tw" in req.full_url
        assert "otc_2330.tw" in req.full_url
        assert "tse_2317.tw" in req.full_url
        assert "otc_2317.tw" in req.full_url
        return _FakeHttpResponse(body=json.dumps(rows))

    monkeypatch.setattr("stock_monitor.adapters.market_data_twse.request.urlopen", _fake_urlopen)
    quotes = provider.get_realtime_quotes(["2330", "2317"])
    assert "2330" in quotes
    assert quotes["2330"]["price"] == 2000.0
    assert "2317" not in quotes


def test_twse_provider_supports_otc_and_prefers_latest_tick(monkeypatch):
    provider = TwseRealtimeMarketDataProvider(base_url="https://example.test/api")
    rows = {
        "msgArray": [
            {"c": "2330", "a": "1999.0_2004.0_2009.0_2014.0_2019.0_", "z": "1999.0", "tlong": "1775802000000", "n": "台積電"},
            {"c": "2330", "a": "2000.0_2005.0_2010.0_2015.0_2020.0_", "z": "2000.0", "tlong": "1775802600000", "n": "台積電"},
            {"c": "3293", "a": "688.0_689.0_690.0_691.0_692.0_", "z": "688.0", "tlong": "1775802600000", "n": "鈊象"},
            {"c": "9999", "z": "1.0", "tlong": "1775802600000", "n": "ignore-me"},
        ]
    }

    def _fake_urlopen(req, timeout):
        assert timeout == provider.timeout_sec
        assert "tse_3293.tw" in req.full_url
        assert "otc_3293.tw" in req.full_url
        return _FakeHttpResponse(body=json.dumps(rows))

    monkeypatch.setattr("stock_monitor.adapters.market_data_twse.request.urlopen", _fake_urlopen)
    quotes = provider.get_realtime_quotes(["2330", "3293"])

    assert quotes["2330"]["price"] == 2000.0
    assert quotes["2330"]["tick_at"] == 1775802600
    assert quotes["3293"]["price"] == 688.0
    assert "9999" not in quotes


def test_twse_provider_deduplicates_channels_and_skips_older_tick(monkeypatch):
    provider = TwseRealtimeMarketDataProvider(base_url="https://example.test/api")
    rows = {
        "msgArray": [
            {"c": "2330", "a": "2000.0_2005.0_2010.0_2015.0_2020.0_", "z": "2000.0", "tlong": "1775802600000", "n": "台積電"},
            {"c": "2330", "a": "1990.0_1995.0_2000.0_2005.0_2010.0_", "z": "1990.0", "tlong": "1775802000000", "n": "台積電(older)"},
        ]
    }

    def _fake_urlopen(req, timeout):
        assert timeout == provider.timeout_sec
        # duplicate/blank symbols should be normalized to one stock channel pair.
        assert req.full_url.count("tse_2330.tw") == 1
        assert req.full_url.count("otc_2330.tw") == 1
        return _FakeHttpResponse(body=json.dumps(rows))

    monkeypatch.setattr("stock_monitor.adapters.market_data_twse.request.urlopen", _fake_urlopen)
    quotes = provider.get_realtime_quotes(["2330", "2330", " ", ""])

    # older tick row should be ignored and not overwrite latest.
    assert quotes["2330"]["price"] == 2000.0
    assert quotes["2330"]["tick_at"] == 1775802600


def test_twse_provider_market_snapshot_and_error_paths(monkeypatch):
    provider = TwseRealtimeMarketDataProvider()

    def _ok_urlopen(req, timeout):
        payload = {"msgArray": [{"z": "35417.83", "tlong": "1775799180000"}]}
        return _FakeHttpResponse(body=json.dumps(payload))

    monkeypatch.setattr("stock_monitor.adapters.market_data_twse.request.urlopen", _ok_urlopen)
    snapshot = provider.get_market_snapshot(now_epoch=1_712_710_600)
    assert snapshot["index_tick_at"] == 1_775_799_180
    assert snapshot["index_price"] == 35417.83

    def _bad_tlong(req, timeout):
        payload = {"msgArray": [{"z": "100", "tlong": "-"}]}
        return _FakeHttpResponse(body=json.dumps(payload))

    monkeypatch.setattr("stock_monitor.adapters.market_data_twse.request.urlopen", _bad_tlong)
    with pytest.raises(RuntimeError):
        provider.get_market_snapshot(now_epoch=1_712_710_600)

    def _no_rows(req, timeout):
        return _FakeHttpResponse(body=json.dumps({"msgArray": []}))

    monkeypatch.setattr("stock_monitor.adapters.market_data_twse.request.urlopen", _no_rows)
    with pytest.raises(RuntimeError):
        provider.get_market_snapshot(now_epoch=1_712_710_600)

    def _timeout(req, timeout):
        raise socket.timeout("boom")

    monkeypatch.setattr("stock_monitor.adapters.market_data_twse.request.urlopen", _timeout)
    with pytest.raises(TimeoutError):
        provider.get_realtime_quotes(["2330"])

    def _urlerr(req, timeout):
        raise error.URLError("network down")

    monkeypatch.setattr("stock_monitor.adapters.market_data_twse.request.urlopen", _urlerr)
    with pytest.raises(RuntimeError):
        provider.get_realtime_quotes(["2330"])


def test_twse_provider_additional_branches(monkeypatch):
    provider = TwseRealtimeMarketDataProvider()

    assert provider.get_realtime_quotes([]) == {}

    def _invalid_msg_array(req, timeout):
        return _FakeHttpResponse(body=json.dumps({"msgArray": {}}))

    monkeypatch.setattr("stock_monitor.adapters.market_data_twse.request.urlopen", _invalid_msg_array)
    with pytest.raises(RuntimeError):
        provider.get_realtime_quotes(["2330"])

    def _row_with_missing_stock_and_bad_tlong(req, timeout):
        payload = {
            "msgArray": [
                {"c": "", "z": "100.0", "tlong": "1775802600000"},
                {"c": "2330", "a": "2000.0_2005.0_2010.0_2015.0_2020.0_", "z": "2000.0", "tlong": "bad-tlong"},
            ]
        }
        return _FakeHttpResponse(body=json.dumps(payload))

    monkeypatch.setattr("stock_monitor.adapters.market_data_twse.request.urlopen", _row_with_missing_stock_and_bad_tlong)
    quotes = provider.get_realtime_quotes(["2330"])
    assert quotes["2330"]["tick_at"] == 0

    def _url_timeout(req, timeout):
        raise error.URLError(socket.timeout("timed out"))

    monkeypatch.setattr("stock_monitor.adapters.market_data_twse.request.urlopen", _url_timeout)
    with pytest.raises(TimeoutError):
        provider.get_realtime_quotes(["2330"])

    # First call: a (best ask) has a valid price → gets cached
    def _with_price(req, timeout):
        payload = {"msgArray": [
            {"c": "2330", "a": "2045.0_2050.0_2055.0_2060.0_2065.0_", "tlong": "1775802600000", "n": "台積電"},
        ]}
        return _FakeHttpResponse(body=json.dumps(payload))

    monkeypatch.setattr("stock_monitor.adapters.market_data_twse.request.urlopen", _with_price)
    quotes = provider.get_realtime_quotes(["2330"])
    assert quotes["2330"]["price"] == 2045.0

    # Second call: a absent → should return cached best ask price, not None
    def _no_a(req, timeout):
        payload = {"msgArray": [
            {"c": "2330", "tlong": "1775802660000", "n": "台積電"},
        ]}
        return _FakeHttpResponse(body=json.dumps(payload))

    monkeypatch.setattr("stock_monitor.adapters.market_data_twse.request.urlopen", _no_a)
    quotes = provider.get_realtime_quotes(["2330"])
    assert quotes["2330"]["price"] == 2045.0, "should return last cached ask price when a is absent"


def test_twse_cold_start_seeds_price_cache_from_yesterday_close(monkeypatch):
    """a absent + cache cold → seed _price_cache from y (yesterday's close) so cold-start
    never needs to fallback to delayed external sources (Yahoo ~20min delay)."""
    provider = TwseRealtimeMarketDataProvider(base_url="https://example.test/api")

    def _only_y(req, timeout):
        payload = {"msgArray": [
            {"c": "2330", "z": "-", "y": "1990.0", "tlong": "1776128700000", "n": "台積電", "ex": "tse"},
        ]}
        return _FakeHttpResponse(body=json.dumps(payload))

    monkeypatch.setattr("stock_monitor.adapters.market_data_twse.request.urlopen", _only_y)
    quotes = provider.get_realtime_quotes(["2330"])

    # Should return yesterday's close as price when cache is cold
    assert "2330" in quotes, "cold-start with only y field should still produce a quote"
    assert quotes["2330"]["price"] == 1990.0, "cold-start price should be yesterday's close (y)"
    assert provider._price_cache.get("2330") == 1990.0, "_price_cache should be seeded from y"


def test_twse_cold_start_no_y_returns_no_quote(monkeypatch):
    """z='-' + cache cold + no y → no quote returned (genuine stale)."""
    provider = TwseRealtimeMarketDataProvider(base_url="https://example.test/api")

    def _neither(req, timeout):
        payload = {"msgArray": [
            {"c": "2330", "z": "-", "y": "-", "tlong": "1776128700000", "n": "台積電", "ex": "tse"},
        ]}
        return _FakeHttpResponse(body=json.dumps(payload))

    monkeypatch.setattr("stock_monitor.adapters.market_data_twse.request.urlopen", _neither)
    quotes = provider.get_realtime_quotes(["2330"])
    assert "2330" not in quotes, "no quote when both z and y are missing"


def test_line_push_client_success_and_failures(monkeypatch):
    client = LinePushClient(channel_access_token="token", to_group_id="C1234567890")

    captured = {}

    def _ok_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["authorization"] = req.headers.get("Authorization")
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeHttpResponse(body='{"ok":true}', status=200)

    monkeypatch.setattr("stock_monitor.adapters.line_messaging.request.urlopen", _ok_urlopen)
    result = client.send("hello")
    assert result["ok"] is True
    assert captured["url"] == client.endpoint
    assert "Bearer token" in captured["authorization"]
    assert captured["payload"]["to"] == "C1234567890"

    with pytest.raises(ValueError):
        client.send(" ")

    def _timeout(req, timeout):
        raise socket.timeout("line timeout")

    monkeypatch.setattr("stock_monitor.adapters.line_messaging.request.urlopen", _timeout)
    with pytest.raises(TimeoutError):
        client.send("hello")

    http_error = error.HTTPError(
        url=client.endpoint,
        code=400,
        msg="bad request",
        hdrs=None,
        fp=io.BytesIO(b'{"message":"bad"}'),
    )

    def _http_error(req, timeout):
        raise http_error

    monkeypatch.setattr("stock_monitor.adapters.line_messaging.request.urlopen", _http_error)
    with pytest.raises(RuntimeError):
        client.send("hello")

    def _urlerr(req, timeout):
        raise error.URLError("dns fail")

    monkeypatch.setattr("stock_monitor.adapters.line_messaging.request.urlopen", _urlerr)
    with pytest.raises(RuntimeError):
        client.send("hello")

    def _url_timeout(req, timeout):
        raise error.URLError(socket.timeout("line timed out"))

    monkeypatch.setattr("stock_monitor.adapters.line_messaging.request.urlopen", _url_timeout)
    with pytest.raises(TimeoutError):
        client.send("hello")

    def _non_2xx(req, timeout):
        return _FakeHttpResponse(body='{"ok":false}', status=500)

    monkeypatch.setattr("stock_monitor.adapters.line_messaging.request.urlopen", _non_2xx)
    with pytest.raises(RuntimeError):
        client.send("hello")
