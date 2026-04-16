"""Real valuation method implementations using FinMind financial data.

Implements the three baseline methods from EDD §9.1 with actual financial inputs
fetched from FinMind API. No synthetic data, no DB snapshots.

Each method implements: compute(stock_no, trade_date_local) -> dict
Return dict keys: status, fair_price, cheap_price, method_name, method_version

Status values (EDD §9.2):
    SUCCESS                - computation succeeded
    SKIP_INSUFFICIENT_DATA - required inputs missing or zero
    SKIP_PROVIDER_ERROR    - upstream API call failed
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _norm(fair: float, cheap: float) -> tuple[float, float]:
    """Normalize fair/cheap to sensible range."""
    f = round(max(float(fair), 0.01), 2)
    c = round(min(max(float(cheap), 0.01), f), 2)
    return f, c


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    return (s[n // 2 - 1] + s[n // 2]) / 2 if n % 2 == 0 else s[n // 2]


@dataclass
class EmilyCompositeV1:
    """艾蜜莉複合估值法 — emily_composite_v1 (EDD §9.1.1).

    Sub-methods (at least one required):
        1. Dividend:   fair = avg_div * 20,   cheap = avg_div * 15
        2. PE:         fair = base_eps * pe_mid_avg,  cheap = base_eps * pe_low_avg
        3. PB:         fair = bps * pb_mid_avg,       cheap = bps * pb_low_avg
        4. Hist price: fair = year_avg_10y,   cheap = year_low_10y

    Aggregation: mean of available sub-method fair/cheap, × safety_margin (0.9).
    """

    method_name: str = field(default="emily_composite")
    method_version: str = field(default="v1")
    provider: object = field(default=None)
    safety_margin: float = field(default=0.9)

    def compute(self, stock_no: str, trade_date_local: str) -> dict:  # noqa: ARG002
        _skip = {
            "status": "SKIP_INSUFFICIENT_DATA",
            "fair_price": None,
            "cheap_price": None,
            "method_name": self.method_name,
            "method_version": self.method_version,
        }
        if self.provider is None:
            return _skip

        try:
            sub_fairs: list[float] = []
            sub_cheaps: list[float] = []

            # ── 1. Dividend sub-method ──────────────────────────────────
            avg_div = self.provider.get_avg_dividend(stock_no)
            if avg_div and avg_div > 0:
                sub_fairs.append(avg_div * 20)
                sub_cheaps.append(avg_div * 15)

            # ── 2. PE sub-method ────────────────────────────────────────
            eps_data = self.provider.get_eps_data(stock_no)
            pe_pb = self.provider.get_pe_pb_stats(stock_no)
            if (
                eps_data
                and pe_pb
                and eps_data.get("eps_ttm") is not None
                and eps_data.get("eps_10y_avg") is not None
                and pe_pb.get("pe_low_avg")
                and pe_pb.get("pe_mid_avg")
            ):
                base_eps = (eps_data["eps_ttm"] + eps_data["eps_10y_avg"]) / 2
                if base_eps > 0:
                    sub_fairs.append(base_eps * pe_pb["pe_mid_avg"])
                    sub_cheaps.append(base_eps * pe_pb["pe_low_avg"])

            # ── 3. PB sub-method ────────────────────────────────────────
            if (
                pe_pb
                and pe_pb.get("bps_latest")
                and pe_pb.get("pb_low_avg")
                and pe_pb.get("pb_mid_avg")
                and pe_pb["bps_latest"] > 0
            ):
                bps = pe_pb["bps_latest"]
                sub_fairs.append(bps * pe_pb["pb_mid_avg"])
                sub_cheaps.append(bps * pe_pb["pb_low_avg"])

            # ── 4. Historical price sub-method ──────────────────────────
            price_stats = self.provider.get_price_annual_stats(stock_no)
            if (
                price_stats
                and price_stats.get("year_avg_10y")
                and price_stats.get("year_low_10y")
            ):
                sub_fairs.append(price_stats["year_avg_10y"])
                sub_cheaps.append(price_stats["year_low_10y"])

            if not sub_fairs:
                return _skip

            avg_fair = sum(sub_fairs) / len(sub_fairs) * self.safety_margin
            avg_cheap = sum(sub_cheaps) / len(sub_cheaps) * self.safety_margin
            fair, cheap = _norm(avg_fair, avg_cheap)

            return {
                "status": "SUCCESS",
                "fair_price": fair,
                "cheap_price": cheap,
                "method_name": self.method_name,
                "method_version": self.method_version,
            }

        except Exception:
            return {
                "status": "SKIP_PROVIDER_ERROR",
                "fair_price": None,
                "cheap_price": None,
                "method_name": self.method_name,
                "method_version": self.method_version,
            }


@dataclass
class OldbullDividendYieldV1:
    """股海老牛股利殖利率法 — oldbull_dividend_yield_v1 (EDD §9.1.2).

    fair  = avg_dividend / 0.05   (5% yield → fair value)
    cheap = avg_dividend / 0.06   (6% yield → cheap price)
    """

    method_name: str = field(default="oldbull_dividend_yield")
    method_version: str = field(default="v1")
    provider: object = field(default=None)

    def compute(self, stock_no: str, trade_date_local: str) -> dict:  # noqa: ARG002
        _skip = {
            "status": "SKIP_INSUFFICIENT_DATA",
            "fair_price": None,
            "cheap_price": None,
            "method_name": self.method_name,
            "method_version": self.method_version,
        }
        if self.provider is None:
            return _skip

        try:
            avg_div = self.provider.get_avg_dividend(stock_no)
            if not avg_div or avg_div <= 0:
                return _skip

            fair, cheap = _norm(avg_div / 0.05, avg_div / 0.06)
            return {
                "status": "SUCCESS",
                "fair_price": fair,
                "cheap_price": cheap,
                "method_name": self.method_name,
                "method_version": self.method_version,
            }

        except Exception:
            return {
                "status": "SKIP_PROVIDER_ERROR",
                "fair_price": None,
                "cheap_price": None,
                "method_name": self.method_name,
                "method_version": self.method_version,
            }


@dataclass
class RayskyBlendedMarginV1:
    """雷司紀融合安全邊際法 — raysky_blended_margin_v1 (EDD §9.1.3).

    Sub-methods (median blend):
        PE:       eps_ttm * pe_mid_avg
        Dividend: avg_dividend / 0.05
        PB:       bps_latest * pb_mid_avg
        NCAV:     (current_assets - total_liabilities) * 1000 / shares

    cheap = fair * margin_factor (default 0.9)
    Requires at least one sub-method to succeed.
    """

    method_name: str = field(default="raysky_blended_margin")
    method_version: str = field(default="v1")
    provider: object = field(default=None)
    margin_factor: float = field(default=0.9)

    def compute(self, stock_no: str, trade_date_local: str) -> dict:  # noqa: ARG002
        _skip = {
            "status": "SKIP_INSUFFICIENT_DATA",
            "fair_price": None,
            "cheap_price": None,
            "method_name": self.method_name,
            "method_version": self.method_version,
        }
        if self.provider is None:
            return _skip

        try:
            sub_fairs: list[float] = []

            eps_data = self.provider.get_eps_data(stock_no)
            pe_pb = self.provider.get_pe_pb_stats(stock_no)
            avg_div = self.provider.get_avg_dividend(stock_no)
            balance = self.provider.get_balance_sheet_data(stock_no)
            shares = self.provider.get_shares_outstanding(stock_no)

            # ── PE sub-method ────────────────────────────────────────────
            if (
                eps_data
                and pe_pb
                and eps_data.get("eps_ttm") is not None
                and pe_pb.get("pe_mid_avg")
            ):
                eps_ttm = eps_data["eps_ttm"]
                if eps_ttm > 0:
                    sub_fairs.append(eps_ttm * pe_pb["pe_mid_avg"])

            # ── Dividend sub-method ──────────────────────────────────────
            if avg_div and avg_div > 0:
                sub_fairs.append(avg_div / 0.05)

            # ── PB sub-method ────────────────────────────────────────────
            if (
                pe_pb
                and pe_pb.get("bps_latest")
                and pe_pb.get("pb_mid_avg")
                and pe_pb["bps_latest"] > 0
            ):
                sub_fairs.append(pe_pb["bps_latest"] * pe_pb["pb_mid_avg"])

            # ── NCAV sub-method ──────────────────────────────────────────
            # balance sheet values in NT$ thousands; shares in units
            if balance and shares and shares > 0:
                ca = balance.get("current_assets", 0.0)
                tl = balance.get("total_liabilities", 0.0)
                ncav = (ca - tl) * 1_000 / shares
                if ncav > 0:
                    sub_fairs.append(ncav)

            if not sub_fairs:
                return _skip

            fair_val = _median(sub_fairs)
            fair, cheap = _norm(fair_val, fair_val * self.margin_factor)

            return {
                "status": "SUCCESS",
                "fair_price": fair,
                "cheap_price": cheap,
                "method_name": self.method_name,
                "method_version": self.method_version,
            }

        except Exception:
            return {
                "status": "SKIP_PROVIDER_ERROR",
                "fair_price": None,
                "cheap_price": None,
                "method_name": self.method_name,
                "method_version": self.method_version,
            }
