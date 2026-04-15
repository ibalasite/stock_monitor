"""Red-light TDD tests for TP-ADP-001 ~ TP-ADP-004.

These tests will FAIL until the following modules are implemented:
  stock_monitor/adapters/market_data_yahoo.py   -- YahooFinanceMarketDataProvider
  stock_monitor/adapters/market_data_composite.py -- CompositeMarketDataProvider

EDD §13.5 CR-ADP-01 ~ CR-ADP-04 / PDD FR-15 / BDD feature: market_data_composite.feature
"""

from __future__ import annotations

import json
import socket
from urllib import error

import pytest

from tests._contract import require_symbol


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

class _FakeHttpResponse:
    def __init__(self, *, body: str, status: int = 200):
        self._body = body.encode("utf-8")
        self.status = status

    def read(self, n=-1):
        if n == -1:
            return self._body
        return self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _yahoo_html_page(price: float, time: int, name: str = "Taiwan Semiconductor") -> str:
    """Minimal HTML page mimicking tw.stock.yahoo.com/quote/{stock_no}.
    Contains the same embedded JSON fields that the real page server-renders.
    """
    return (
        '<!DOCTYPE html><html><head></head><body>'
        '<script>var data = {"regularMarketPrice":' + str(price) + ','
        '"regularMarketTime":' + str(time) + ','
        '"longName":"' + name + '"};</script>'
        '</body></html>'
    )


# ===========================================================================
# TP-ADP-001  YahooFinanceMarketDataProvider 行為
# ===========================================================================

class TestYahooFinanceMarketDataProvider:
    """TP-ADP-001a ~ 001e"""

    def _provider(self):
        cls = require_symbol(
            "stock_monitor.adapters.market_data_yahoo",
            "YahooFinanceMarketDataProvider",
            "TP-ADP-001",
        )
        return cls()

    # ------------------------------------------------------------------
    # TP-ADP-001a: 正常回傳時取得 price 與 tick_at
    # ------------------------------------------------------------------
    def test_tp_adp_001a_normal_response(self, monkeypatch):
        """[TP-ADP-001a] Yahoo TW 頁面正常時取得 regularMarketPrice 與 regularMarketTime。
        URL 為 tw.stock.yahoo.com/quote/{stock_no}，不加 .TW/.TWO suffix。"""
        provider = self._provider()

        def _fake(req, timeout):
            assert "2330" in req.full_url
            assert ".TW" not in req.full_url, "HTML scraping URL should not use .TW suffix"
            return _FakeHttpResponse(body=_yahoo_html_page(2035.0, 1776100020))

        monkeypatch.setattr(
            "stock_monitor.adapters.market_data_yahoo.request.urlopen", _fake
        )
        quotes = provider.get_realtime_quotes(["2330"], exchange_map={"2330": "tse"})
        assert "2330" in quotes
        assert quotes["2330"]["price"] == 2035.0
        assert quotes["2330"]["tick_at"] == 1776100020

    # ------------------------------------------------------------------
    # TP-ADP-001b: HTTP 4xx → WARN + 空 dict，不 raise
    # ------------------------------------------------------------------
    def test_tp_adp_001b_http_4xx_returns_empty(self, monkeypatch):
        """[TP-ADP-001b] Yahoo TW 頁面 HTTP 404 失敗時回傳空 dict，不 raise。"""
        provider = self._provider()

        def _fake(req, timeout):
            raise error.HTTPError(req.full_url, 404, "Not Found", {}, None)

        monkeypatch.setattr(
            "stock_monitor.adapters.market_data_yahoo.request.urlopen", _fake
        )
        quotes = provider.get_realtime_quotes(["2330"], exchange_map={"2330": "tse"})
        assert quotes == {}

    # ------------------------------------------------------------------
    # TP-ADP-001c: timeout → WARN + 空 dict，不 raise
    # ------------------------------------------------------------------
    def test_tp_adp_001c_timeout_returns_empty(self, monkeypatch):
        """[TP-ADP-001c] Yahoo TW 頁面 timeout 失敗時回傳空 dict，不 raise。"""
        provider = self._provider()

        def _fake(req, timeout):
            raise socket.timeout("timed out")

        monkeypatch.setattr(
            "stock_monitor.adapters.market_data_yahoo.request.urlopen", _fake
        )
        quotes = provider.get_realtime_quotes(["2330"], exchange_map={"2330": "tse"})
        assert quotes == {}

    # ------------------------------------------------------------------
    # TP-ADP-001d: TSE/OTC 都用 stock_no only，不加 suffix
    # ------------------------------------------------------------------
    def test_tp_adp_001d_otc_uses_stock_no_only(self, monkeypatch):
        """[TP-ADP-001d] OTC 股票 URL 只用 stock_no，不需 .TWO suffix。
        tw.stock.yahoo.com/quote/3293 對 TSE 與 OTC 都有效。"""
        provider = self._provider()
        captured_url: list[str] = []

        def _fake(req, timeout):
            captured_url.append(req.full_url)
            return _FakeHttpResponse(body=_yahoo_html_page(766.0, 1776100020))

        monkeypatch.setattr(
            "stock_monitor.adapters.market_data_yahoo.request.urlopen", _fake
        )
        quotes = provider.get_realtime_quotes(["3293"], exchange_map={"3293": "otc"})
        assert any("3293" in u for u in captured_url), "URL should include stock_no"
        assert all(".TWO" not in u for u in captured_url), "URL should NOT have .TWO suffix"
        assert quotes["3293"]["price"] == 766.0

    # ------------------------------------------------------------------
    # TP-ADP-001e: exchange_map=None/空 不影響查詢（不再需要 suffix）
    # ------------------------------------------------------------------
    def test_tp_adp_001e_exchange_map_ignored(self, monkeypatch):
        """[TP-ADP-001e] exchange_map 參數可為 None 或空 dict，URL 不受影響。"""
        provider = self._provider()
        captured_url: list[str] = []

        def _fake(req, timeout):
            captured_url.append(req.full_url)
            return _FakeHttpResponse(body=_yahoo_html_page(766.0, 1776100020))

        monkeypatch.setattr(
            "stock_monitor.adapters.market_data_yahoo.request.urlopen", _fake
        )
        quotes = provider.get_realtime_quotes(["3293"], exchange_map=None)
        assert any("3293" in u for u in captured_url)
        assert all(".TW" not in u for u in captured_url), "URL should not contain .TW when using HTML scraping"

    # ------------------------------------------------------------------
    # TP-ADP-001f: quote dict must NOT include name field (FR-18)
    # ------------------------------------------------------------------
    def test_tp_adp_001f_yahoo_quote_has_no_name_field(self, monkeypatch):
        """[TP-ADP-001f] FR-18: Yahoo adapter get_realtime_quotes must NOT return 'name' field.
        Stock names must come from DB (watchlist.stock_name), not from realtime scrape."""
        provider = self._provider()

        def _fake(req, timeout):
            return _FakeHttpResponse(body=_yahoo_html_page(2035.0, 1776100020, name="台積電_YAHOO"))

        monkeypatch.setattr(
            "stock_monitor.adapters.market_data_yahoo.request.urlopen", _fake
        )
        quotes = provider.get_realtime_quotes(["2330"])
        assert "2330" in quotes
        assert "name" not in quotes["2330"], (
            "[TP-ADP-001f] Yahoo quote must NOT contain 'name' field. "
            f"FR-18: stock names must come from DB only. Got keys: {list(quotes['2330'].keys())}"
        )


# ===========================================================================
# TP-ADP-002  CompositeMarketDataProvider Freshness-First
# ===========================================================================

class TestCompositeMarketDataProvider:
    """TP-ADP-002a ~ 002g"""

    def _build(self, twse_quotes: dict, yahoo_quotes: dict, twse_exchange_cache: dict | None = None):
        """Build a Composite with stubbed primary/secondary."""
        CompositeCls = require_symbol(
            "stock_monitor.adapters.market_data_composite",
            "CompositeMarketDataProvider",
            "TP-ADP-002",
        )
        TwseCls = require_symbol(
            "stock_monitor.adapters.market_data_twse",
            "TwseRealtimeMarketDataProvider",
            "TP-ADP-002",
        )
        YahooCls = require_symbol(
            "stock_monitor.adapters.market_data_yahoo",
            "YahooFinanceMarketDataProvider",
            "TP-ADP-002",
        )

        primary = TwseCls.__new__(TwseCls)
        primary._price_cache = {}
        primary._exchange_cache = twse_exchange_cache or {}
        primary._tick_cache = {}

        secondary = YahooCls.__new__(YahooCls)

        # Stub get_realtime_quotes
        primary.get_realtime_quotes = lambda nos, **kw: twse_quotes
        secondary.get_realtime_quotes = lambda nos, exchange_map=None, **kw: yahoo_quotes

        return CompositeCls(primary=primary, secondary=secondary)

    # ------------------------------------------------------------------
    # TP-ADP-002a: TWSE tick_at 較新 → 採 TWSE
    # ------------------------------------------------------------------
    def test_tp_adp_002a_twse_newer_wins(self):
        """[TP-ADP-002a] TWSE tick_at 較新時採 TWSE 報價。"""
        comp = self._build(
            twse_quotes={"2330": {"stock_no": "2330", "price": 2045.0, "tick_at": 1776100060, "name": "台積電", "exchange": "tse"}},
            yahoo_quotes={"2330": {"stock_no": "2330", "price": 2035.0, "tick_at": 1776100020, "name": "台積電"}},
        )
        result = comp.get_realtime_quotes(["2330"])
        assert result["2330"]["price"] == 2045.0
        assert result["2330"]["tick_at"] == 1776100060

    # ------------------------------------------------------------------
    # TP-ADP-002b: Yahoo tick_at 較新 → 採 Yahoo
    # ------------------------------------------------------------------
    def test_tp_adp_002b_yahoo_newer_wins(self):
        """[TP-ADP-002b] Yahoo tick_at 較新時採 Yahoo 報價。"""
        comp = self._build(
            twse_quotes={"2330": {"stock_no": "2330", "price": 2000.0, "tick_at": 1776099960, "name": "台積電", "exchange": "tse"}},
            yahoo_quotes={"2330": {"stock_no": "2330", "price": 2045.0, "tick_at": 1776100020, "name": "台積電"}},
        )
        result = comp.get_realtime_quotes(["2330"])
        assert result["2330"]["price"] == 2045.0
        assert result["2330"]["tick_at"] == 1776100020

    # ------------------------------------------------------------------
    # TP-ADP-002c: tick_at 相同 → TWSE 優先
    # ------------------------------------------------------------------
    def test_tp_adp_002c_tie_twse_wins(self):
        """[TP-ADP-002c] tick_at 相同時以 TWSE 為準。"""
        tick = 1776100020
        comp = self._build(
            twse_quotes={"2330": {"stock_no": "2330", "price": 2045.0, "tick_at": tick, "name": "台積電", "exchange": "tse"}},
            yahoo_quotes={"2330": {"stock_no": "2330", "price": 2044.0, "tick_at": tick, "name": "台積電"}},
        )
        result = comp.get_realtime_quotes(["2330"])
        assert result["2330"]["price"] == 2045.0

    # ------------------------------------------------------------------
    # TP-ADP-002d: TWSE 完全無任何股票資料 + Yahoo 有 → Yahoo 補位
    # ------------------------------------------------------------------
    def test_tp_adp_002d_cold_start_uses_yahoo(self):
        """[TP-ADP-002d] TWSE 完全無資料（含 y='-'）時 Composite 以 Yahoo 報價補位。
        注意：Yahoo v8 chart API 為 ~20 分鐘延遲報價，僅作為 last-resort fallback；
        正常冷啟動應由 TWSE y（昨收）填充 _price_cache。"""
        comp = self._build(
            twse_quotes={},
            yahoo_quotes={"2330": {"stock_no": "2330", "price": 2035.0, "tick_at": 1776100020, "name": "台積電"}},
        )
        result = comp.get_realtime_quotes(["2330"])
        assert result["2330"]["price"] == 2035.0
        assert result["2330"]["tick_at"] == 1776100020

    # ------------------------------------------------------------------
    # TP-ADP-002e: 兩者均無 → 不加入結果
    # ------------------------------------------------------------------
    def test_tp_adp_002e_both_empty_not_in_result(self):
        """[TP-ADP-002e] 兩者均無法取得時不加入 result（呼叫端觸發 STALE_QUOTE）。"""
        comp = self._build(twse_quotes={}, yahoo_quotes={})
        result = comp.get_realtime_quotes(["2330"])
        assert "2330" not in result

    # ------------------------------------------------------------------
    # TP-ADP-002f: Yahoo 失敗時 Composite 仍能使用 TWSE cache 值
    # ------------------------------------------------------------------
    def test_tp_adp_002f_yahoo_fail_fallback_twse(self):
        """[TP-ADP-002f] Yahoo 失敗時 Composite 仍使用 TWSE cache 值。"""
        comp = self._build(
            twse_quotes={"2330": {"stock_no": "2330", "price": 2045.0, "tick_at": 1776100060, "name": "台積電", "exchange": "tse"}},
            yahoo_quotes={},  # Yahoo 失敗回傳空
        )
        result = comp.get_realtime_quotes(["2330"])
        assert result["2330"]["price"] == 2045.0

    # ------------------------------------------------------------------
    # TP-ADP-002g: get_market_snapshot delegate 給 primary
    # ------------------------------------------------------------------
    def test_tp_adp_002g_market_snapshot_delegates_to_primary(self):
        """[TP-ADP-002g] get_market_snapshot delegate 給 TWSE primary。"""
        expected = {
            "source": "twse_mis",
            "index_price": 21000.0,
            "index_tick_at": 1776100000,
            "fetched_at": 1776100000,
        }
        CompositeCls = require_symbol(
            "stock_monitor.adapters.market_data_composite",
            "CompositeMarketDataProvider",
            "TP-ADP-002g",
        )
        TwseCls = require_symbol(
            "stock_monitor.adapters.market_data_twse",
            "TwseRealtimeMarketDataProvider",
            "TP-ADP-002g",
        )
        YahooCls = require_symbol(
            "stock_monitor.adapters.market_data_yahoo",
            "YahooFinanceMarketDataProvider",
            "TP-ADP-002g",
        )
        primary = TwseCls.__new__(TwseCls)
        primary._price_cache = {}
        primary._exchange_cache = {}
        primary._tick_cache = {}
        primary.get_realtime_quotes = lambda nos, **kw: {}
        primary.get_market_snapshot = lambda now_epoch: expected

        secondary = YahooCls.__new__(YahooCls)
        secondary.get_realtime_quotes = lambda nos, exchange_map=None, **kw: {}

        comp = CompositeCls(primary=primary, secondary=secondary)
        snapshot = comp.get_market_snapshot(now_epoch=1776100000)
        assert snapshot["index_price"] == 21000.0
        assert snapshot["index_tick_at"] == 1776100000


# ===========================================================================
# TP-ADP-003  TWSE exchange_cache 與 _tick_cache
# ===========================================================================

class TestTwseExchangeCache:
    """TP-ADP-003a ~ 003c  (TWSE quotes 含 exchange 欄位 + _exchange_cache 更新)"""

    def _provider(self, **kw):
        cls = require_symbol(
            "stock_monitor.adapters.market_data_twse",
            "TwseRealtimeMarketDataProvider",
            "TP-ADP-003",
        )
        return cls(**kw)

    def test_tp_adp_003a_quotes_contain_exchange_field(self, monkeypatch):
        """[TP-ADP-003a] TWSE z 有值時 quotes 含 exchange 欄位（tse/otc）。"""
        provider = self._provider(base_url="https://example.test/api")

        def _fake(req, timeout):
            payload = {"msgArray": [
                {"c": "2330", "a": "2045.0_2050.0_2055.0_2060.0_2065.0_", "tlong": "1776100000000", "n": "台積電", "ex": "tse"},
            ]}
            return type("R", (), {
                "read": lambda self, n=-1: json.dumps(payload).encode(),
                "__enter__": lambda self: self,
                "__exit__": lambda self, *_: False,
            })()

        monkeypatch.setattr(
            "stock_monitor.adapters.market_data_twse.request.urlopen", _fake
        )
        quotes = provider.get_realtime_quotes(["2330"])
        assert "2330" in quotes
        assert quotes["2330"].get("exchange") == "tse", (
            "TwseRealtimeMarketDataProvider.get_realtime_quotes 應在結果中包含 'exchange' 欄位"
        )

    def test_tp_adp_003b_exchange_cache_updated(self, monkeypatch):
        """[TP-ADP-003b] 呼叫後 _exchange_cache 更新為 ex 欄位值。"""
        provider = self._provider(base_url="https://example.test/api")

        def _fake(req, timeout):
            payload = {"msgArray": [
                {"c": "3293", "a": "766.0_767.0_768.0_769.0_770.0_", "tlong": "1776100000000", "n": "鈊象", "ex": "otc"},
            ]}
            return type("R", (), {
                "read": lambda self, n=-1: json.dumps(payload).encode(),
                "__enter__": lambda self: self,
                "__exit__": lambda self, *_: False,
            })()

        monkeypatch.setattr(
            "stock_monitor.adapters.market_data_twse.request.urlopen", _fake
        )
        provider.get_realtime_quotes(["3293"])
        assert hasattr(provider, "_exchange_cache"), "_exchange_cache attribute must exist"
        assert provider._exchange_cache.get("3293") == "otc", (
            "_exchange_cache['3293'] 應為 'otc'"
        )

    def test_tp_adp_003c_tick_cache_attribute_exists(self):
        """[TP-ADP-003c] TwseRealtimeMarketDataProvider 須有 _tick_cache 屬性（CLAUDE.md symbol contract）。"""
        provider = self._provider()
        assert hasattr(provider, "_tick_cache"), (
            "TwseRealtimeMarketDataProvider 須有 _tick_cache 屬性（per CLAUDE.md symbol contract）"
        )


# ===========================================================================
# TP-ADP-004  Yahoo HTTP 回應大小上限
# ===========================================================================

class TestYahooResponseSizeLimit:
    """TP-ADP-004  HTTP 回應受 MAX_RESPONSE_BYTES 限制"""

    def test_tp_adp_004_max_response_bytes_exists(self):
        """[TP-ADP-004] market_data_yahoo 模組須定義 MAX_RESPONSE_BYTES 常數。"""
        mod = require_symbol(
            "stock_monitor.adapters.market_data_yahoo",
            "MAX_RESPONSE_BYTES",
            "TP-ADP-004",
        )
        assert isinstance(mod, int) and mod >= 1_048_576, (
            "MAX_RESPONSE_BYTES 應為整數且 >= 1 MB"
        )

    def test_tp_adp_004_read_uses_size_limit(self, monkeypatch):
        """[TP-ADP-004] YahooFinanceMarketDataProvider HTTP 讀取使用 MAX_RESPONSE_BYTES 上限，而非無邊界 read()。"""
        cls = require_symbol(
            "stock_monitor.adapters.market_data_yahoo",
            "YahooFinanceMarketDataProvider",
            "TP-ADP-004",
        )
        provider = cls()
        read_args: list = []

        class _FakeResp:
            def read(self, n=-1):
                read_args.append(n)
                return _yahoo_html_page(2035.0, 1776100020).encode()

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        monkeypatch.setattr(
            "stock_monitor.adapters.market_data_yahoo.request.urlopen",
            lambda req, timeout: _FakeResp(),
        )
        provider.get_realtime_quotes(["2330"], exchange_map={"2330": "tse"})
        assert read_args, "read() should have been called"
        assert all(n != -1 and n > 0 for n in read_args), (
            "read() 必須傳入正整數上限（MAX_RESPONSE_BYTES），不可無邊界讀取"
        )


# ===========================================================================
# Symbol contract 驗證（per CLAUDE.md §7）
# ===========================================================================

def test_symbol_contract_yahoo_provider():
    """CLAUDE.md §7 symbol contract: YahooFinanceMarketDataProvider 必須可 import."""
    require_symbol(
        "stock_monitor.adapters.market_data_yahoo",
        "YahooFinanceMarketDataProvider",
        "symbol-contract-yahoo",
    )


def test_symbol_contract_composite_provider():
    """CLAUDE.md §7 symbol contract: CompositeMarketDataProvider 必須可 import."""
    require_symbol(
        "stock_monitor.adapters.market_data_composite",
        "CompositeMarketDataProvider",
        "symbol-contract-composite",
    )


# ===========================================================================
# Coverage gap: branch / edge paths
# ===========================================================================

def test_yahoo_empty_stock_nos_returns_empty():
    """yahoo.py:45 — get_realtime_quotes([]) 應立即回傳空 dict。"""
    cls = require_symbol(
        "stock_monitor.adapters.market_data_yahoo",
        "YahooFinanceMarketDataProvider",
        "cov-yahoo-empty",
    )
    provider = cls()
    assert provider.get_realtime_quotes([]) == {}


def test_yahoo_none_exchange_map_accepted(monkeypatch):
    """exchange_map=None 時正常查詢，不 raise（exchange_map 在 HTML 模式下被忽略）。"""
    cls = require_symbol(
        "stock_monitor.adapters.market_data_yahoo",
        "YahooFinanceMarketDataProvider",
        "cov-yahoo-none-map",
    )
    provider = cls()
    monkeypatch.setattr(
        "stock_monitor.adapters.market_data_yahoo.request.urlopen",
        lambda req, timeout: _FakeHttpResponse(body=_yahoo_html_page(2035.0, 1776100020)),
    )
    quotes = provider.get_realtime_quotes(["2330"], exchange_map=None)
    assert "2330" in quotes


def test_yahoo_page_missing_price_field_skips(monkeypatch):
    """HTML 頁面不含 regularMarketPrice/regularMarketTime 時跳過，回傳空 dict。"""
    cls = require_symbol(
        "stock_monitor.adapters.market_data_yahoo",
        "YahooFinanceMarketDataProvider",
        "cov-yahoo-missing-price",
    )
    provider = cls()
    monkeypatch.setattr(
        "stock_monitor.adapters.market_data_yahoo.request.urlopen",
        lambda req, timeout: _FakeHttpResponse(body="<html><body>no price here</body></html>"),
    )
    quotes = provider.get_realtime_quotes(["2330"], exchange_map={})
    assert quotes == {}


def test_yahoo_page_no_longname_returns_empty_name(monkeypatch):
    """HTML 含 price/time 但無 longName 時，quote 不含 name 欄位、不 raise，get_stock_names 回傳空。"""
    cls = require_symbol(
        "stock_monitor.adapters.market_data_yahoo",
        "YahooFinanceMarketDataProvider",
        "cov-yahoo-no-name",
    )
    provider = cls()
    html_no_name = (
        '<!DOCTYPE html><html><body>'
        '<script>{"regularMarketPrice":2035,"regularMarketTime":1776100020}</script>'
        '</body></html>'
    )
    monkeypatch.setattr(
        "stock_monitor.adapters.market_data_yahoo.request.urlopen",
        lambda req, timeout: _FakeHttpResponse(body=html_no_name),
    )
    quotes = provider.get_realtime_quotes(["2330"], exchange_map={})
    assert "2330" in quotes
    assert "name" not in quotes["2330"]
    assert provider.get_stock_names(["2330"]) == {}


def test_yahoo_page_value_error_skips(monkeypatch):
    """HTML regex match 回傳非數字字串時觸發 ValueError，跳過、不 raise。"""
    import re as _re
    import stock_monitor.adapters.market_data_yahoo as _mod

    provider = _mod.YahooFinanceMarketDataProvider()
    # Use HTML that contains the custom marker so our overridden regex can match it
    html_with_marker = '<html><body>"rmp":notanumber "rmt":alsonotanumber</body></html>'
    monkeypatch.setattr(
        "stock_monitor.adapters.market_data_yahoo.request.urlopen",
        lambda req, timeout: _FakeHttpResponse(body=html_with_marker),
    )
    # Override regexes to match non-numeric text — float()/int() will raise ValueError
    monkeypatch.setattr(_mod, "_RE_PRICE", _re.compile(r'"rmp":([a-z]+)'))
    monkeypatch.setattr(_mod, "_RE_TIME",  _re.compile(r'"rmt":([a-z]+)'))
    quotes = provider.get_realtime_quotes(["2330"], exchange_map={})
    assert quotes == {}


def test_yahoo_ask_price_extracted_from_order_book_table(monkeypatch):
    """Yahoo TW \u9801\u9762\u542b\u6709\u59d4\u8ce3\u50f9\u5340\u584a\u6642\uff0c\u61c9\u63a1\u7528\u59d4\u8ce3\u4e00\uff08\u6700\u4f73\u59d4\u8ce3\uff09\u4f5c\u70ba price\uff0c\u800c\u975e regularMarketPrice\u3002"""
    cls = require_symbol(
        "stock_monitor.adapters.market_data_yahoo",
        "YahooFinanceMarketDataProvider",
        "cov-yahoo-ask-price",
    )
    provider = cls()
    # Minimal HTML with both ask section (\u59d4\u8ce3\u4e00 = 2050) and regularMarketPrice = 2045
    html_with_ask = (
        '<!DOCTYPE html><html><body>'
        '<script>{"regularMarketPrice":2045,"regularMarketTime":1776100020,'
        '"longName":"Taiwan Semiconductor"}</script>'
        '<div><span>\u59d4\u8ce3\u50f9</span><span>\u91cf</span></div>'
        '<span class="Fw(n) Fz(16px)--mobile Fz(14px) D(f) Ai(c)">2,050</span>'
        '</body></html>'
    )
    monkeypatch.setattr(
        "stock_monitor.adapters.market_data_yahoo.request.urlopen",
        lambda req, timeout: _FakeHttpResponse(body=html_with_ask),
    )
    quotes = provider.get_realtime_quotes(["2330"], exchange_map={"2330": "tse"})
    assert "2330" in quotes
    assert quotes["2330"]["price"] == 2050.0, "\u6700\u4f73\u59d4\u8ce3\u50f9\uff08\u59d4\u8ce3\u4e00\uff09\u61c9\u512a\u5148\u65bc regularMarketPrice"
    assert quotes["2330"]["tick_at"] == 1776100020


def test_composite_empty_stock_nos_returns_empty():
    """composite.py:26 — get_realtime_quotes([]) 應立即回傳空 dict。"""
    CompositeCls = require_symbol(
        "stock_monitor.adapters.market_data_composite",
        "CompositeMarketDataProvider",
        "cov-composite-empty",
    )
    TwseCls = require_symbol(
        "stock_monitor.adapters.market_data_twse",
        "TwseRealtimeMarketDataProvider",
        "cov-composite-empty",
    )
    YahooCls = require_symbol(
        "stock_monitor.adapters.market_data_yahoo",
        "YahooFinanceMarketDataProvider",
        "cov-composite-empty",
    )
    primary = TwseCls.__new__(TwseCls)
    primary._price_cache = {}
    primary._exchange_cache = {}
    primary._tick_cache = {}
    primary.get_realtime_quotes = lambda nos, **kw: {}
    secondary = YahooCls.__new__(YahooCls)
    secondary.get_realtime_quotes = lambda nos, exchange_map=None, **kw: {}
    comp = CompositeCls(primary=primary, secondary=secondary)
    assert comp.get_realtime_quotes([]) == {}
