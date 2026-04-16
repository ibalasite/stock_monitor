"""FR-19 scan-market valuation method injection helpers.

Methods are backed by real rows in valuation_snapshots (latest snapshot <= scan date),
not synthetic formulas.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SnapshotBackedScanValuationMethod:
    """scan-market method adapter backed by valuation_snapshots."""

    method_name: str
    method_version: str
    conn: object
    as_of_date: str

    def compute(self, stock_no: str, trade_date_local: str) -> dict:
        row = self.conn.execute(
            """
            SELECT fair_price, cheap_price
            FROM valuation_snapshots
            WHERE stock_no = ?
              AND method_name = ?
              AND method_version = ?
              AND trade_date <= ?
            ORDER BY trade_date DESC
            LIMIT 1
            """,
            (
                str(stock_no),
                str(self.method_name),
                str(self.method_version),
                str(self.as_of_date),
            ),
        ).fetchone()

        if row is None:
            return {
                "status": "SKIP_INSUFFICIENT_DATA",
                "fair_price": None,
                "cheap_price": None,
                "method_name": self.method_name,
                "method_version": self.method_version,
            }

        try:
            fair = float(row[0])
            cheap = float(row[1])
        except (TypeError, ValueError):
            return {
                "status": "SKIP_INSUFFICIENT_DATA",
                "fair_price": None,
                "cheap_price": None,
                "method_name": self.method_name,
                "method_version": self.method_version,
            }

        return {
            "status": "SUCCESS",
            "fair_price": round(max(fair, 0.01), 2),
            "cheap_price": round(min(max(cheap, 0.01), max(fair, 0.01)), 2),
            "method_name": self.method_name,
            "method_version": self.method_version,
        }


def load_enabled_scan_methods(conn, as_of_date: str) -> list[SnapshotBackedScanValuationMethod]:
    """Load enabled valuation methods from DB for scan-market.

    Raises RuntimeError("MARKET_SCAN_METHODS_EMPTY") when no enabled methods exist.
    """

    rows = conn.execute(
        """
        SELECT method_name, method_version
        FROM valuation_methods
        WHERE enabled = 1
        ORDER BY method_name, method_version
        """
    ).fetchall()

    if not rows:
        raise RuntimeError("MARKET_SCAN_METHODS_EMPTY")

    return [
        SnapshotBackedScanValuationMethod(
            method_name=str(row[0]),
            method_version=str(row[1]),
            conn=conn,
            as_of_date=str(as_of_date),
        )
        for row in rows
    ]
