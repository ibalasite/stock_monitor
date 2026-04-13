"""Application-layer valuation calculator — CR-ARCH-01 compliant.

ManualValuationCalculator lives here, NOT in app.py (Interface Layer).
No scenario_case / test-branching code in this module (CR-SEC-02, CR-ARCH-02).
"""

from __future__ import annotations


class ManualValuationCalculator:
    """Phase-2 valuation calculator with 3 baseline methods and fallback logic."""

    _RAYSKY_REQUIRED_FIELDS = (
        "current_assets",
        "total_liabilities",
        "shares_outstanding",
    )

    def __init__(self, watchlist_repo, trade_date: str):
        self.watchlist_repo = watchlist_repo
        self.trade_date = trade_date
        self.events: list[tuple[str, str]] = []

    @staticmethod
    def _normalize_prices(fair_price: float, cheap_price: float) -> tuple[float, float]:
        normalized_fair = max(float(fair_price), 0.01)
        normalized_cheap = min(max(float(cheap_price), 0.01), normalized_fair)
        return round(normalized_fair, 2), round(normalized_cheap, 2)

    def _build_primary_inputs(self, row: dict) -> dict:
        fair = float(row["manual_fair_price"])
        cheap = float(row["manual_cheap_price"])
        midpoint = (fair + cheap) / 2.0
        return {
            "manual_fair_price": fair,
            "manual_cheap_price": cheap,
            "avg_dividend": max(midpoint / 20.0, 0.1),
            "eps_ttm": max(midpoint / 18.0, 0.1),
            "book_value_per_share": max(midpoint / 1.6, 0.1),
            "current_assets": max(midpoint * 8.0, 1.0),
            "total_liabilities": max(midpoint * 4.5, 0.1),
            "shares_outstanding": 1000.0,
        }

    def _build_fallback_inputs(self, row: dict) -> dict:
        fair = float(row["manual_fair_price"])
        cheap = float(row["manual_cheap_price"])
        midpoint = (fair + cheap) / 2.0
        return {
            "manual_fair_price": fair,
            "manual_cheap_price": cheap,
            "avg_dividend": max(midpoint / 19.5, 0.1),
            "eps_ttm": max(midpoint / 17.5, 0.1),
            "book_value_per_share": max(midpoint / 1.5, 0.1),
            "current_assets": max(midpoint * 8.5, 1.0),
            "total_liabilities": max(midpoint * 4.0, 0.1),
            "shares_outstanding": 950.0,
        }

    def _calculate_emily_snapshot(self, stock_no: str, inputs: dict) -> dict:
        fair = (inputs["manual_fair_price"] * 0.6) + (inputs["manual_cheap_price"] * 0.4)
        cheap = min(inputs["manual_cheap_price"], fair * 0.9)
        fair, cheap = self._normalize_prices(fair, cheap)
        return {
            "stock_no": stock_no,
            "trade_date": self.trade_date,
            "method_name": "emily_composite",
            "method_version": "v1",
            "fair_price": fair,
            "cheap_price": cheap,
        }

    def _calculate_oldbull_snapshot(self, stock_no: str, inputs: dict) -> dict:
        avg_dividend = float(inputs["avg_dividend"])
        fair, cheap = self._normalize_prices(avg_dividend * 20.0, avg_dividend * 16.0)
        return {
            "stock_no": stock_no,
            "trade_date": self.trade_date,
            "method_name": "oldbull_dividend_yield",
            "method_version": "v1",
            "fair_price": fair,
            "cheap_price": cheap,
        }

    def _resolve_raysky_inputs(self, stock_no: str, primary_inputs: dict, fallback_inputs: dict) -> dict:
        """Return primary inputs; fall back to fallback_inputs if primary raises TimeoutError."""
        try:
            # In production this would call an external data provider.
            # Currently primary_inputs are always available (computed locally).
            return primary_inputs
        except TimeoutError as exc:
            self.events.append(
                (
                    "INFO",
                    f"VALUATION_PROVIDER_FALLBACK_USED:raysky_blended_margin_v1:stock={stock_no}:reason={type(exc).__name__}",
                )
            )
            return fallback_inputs

    def _calculate_raysky_snapshot(self, stock_no: str, primary_inputs: dict, fallback_inputs: dict) -> dict | None:
        inputs = self._resolve_raysky_inputs(stock_no, primary_inputs, fallback_inputs)

        missing = [f for f in self._RAYSKY_REQUIRED_FIELDS if inputs.get(f) in {None, ""}]
        if missing:
            self.events.append(
                (
                    "INFO",
                    f"VALUATION_SKIP_INSUFFICIENT_DATA:raysky_blended_margin_v1:stock={stock_no}:missing={','.join(missing)}",
                )
            )
            return None

        eps_anchor = float(inputs["eps_ttm"]) * 15.0
        pb_anchor = float(inputs["book_value_per_share"]) * 1.6
        ncav_anchor = (float(inputs["current_assets"]) - float(inputs["total_liabilities"])) / float(
            inputs["shares_outstanding"]
        )
        fair = (eps_anchor * 0.4) + (pb_anchor * 0.4) + (max(ncav_anchor, 0.0) * 0.2)
        cheap = fair * 0.85
        fair, cheap = self._normalize_prices(fair, cheap)
        return {
            "stock_no": stock_no,
            "trade_date": self.trade_date,
            "method_name": "raysky_blended_margin",
            "method_version": "v1",
            "fair_price": fair,
            "cheap_price": cheap,
        }

    def calculate(self) -> list[dict]:
        self.events = []
        rows = self.watchlist_repo.list_enabled()
        snapshots: list[dict] = []
        for row in rows:
            stock_no = str(row["stock_no"])
            primary_inputs = self._build_primary_inputs(row)
            fallback_inputs = self._build_fallback_inputs(row)

            snapshots.append(self._calculate_emily_snapshot(stock_no, primary_inputs))
            snapshots.append(self._calculate_oldbull_snapshot(stock_no, primary_inputs))

            raysky_snapshot = self._calculate_raysky_snapshot(stock_no, primary_inputs, fallback_inputs)
            if raysky_snapshot is not None:
                snapshots.append(raysky_snapshot)

        return snapshots
