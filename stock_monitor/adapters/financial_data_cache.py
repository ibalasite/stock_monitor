"""Shared SWR (stale-while-revalidate) cache base for financial data adapters.

Each concrete adapter (FinMind, MOPS, Goodinfo) inherits this base.
The cache is keyed by (provider, stock_no, dataset) so every adapter
maintains fully independent cache entries in the same SQLite file.

Cache table schema (auto-migrated if provider column is missing):
    financial_data_cache(provider TEXT, stock_no TEXT, dataset TEXT,
                         data_json TEXT, fetched_at INTEGER)
    PRIMARY KEY (provider, stock_no, dataset)

Fetch result conventions (used by all subclasses):
    list[dict]  — upstream returned status 200; may be [] (genuine no data).
    None        — transient failure (rate limit, network); do NOT cache.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from abc import abstractmethod

from stock_monitor.adapters.financial_data_port import ProviderUnavailableError

SWR_TTL_SECONDS = 15 * 86_400  # 15 days default stale threshold

_CACHE_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS financial_data_cache (
    provider   TEXT    NOT NULL,
    stock_no   TEXT    NOT NULL,
    dataset    TEXT    NOT NULL,
    data_json  TEXT    NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (provider, stock_no, dataset)
)
"""

_CACHE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_fdc_provider_stock
ON financial_data_cache (provider, stock_no)
"""


def _migrate_cache_table(conn: sqlite3.Connection) -> None:
    """Add provider column if it doesn't exist (backward-compat migration)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(financial_data_cache)").fetchall()}
    if "provider" not in cols:
        # Old schema had PRIMARY KEY (stock_no, dataset). Recreate with provider.
        conn.execute("ALTER TABLE financial_data_cache RENAME TO _fdc_old")
        conn.execute(_CACHE_CREATE_SQL)
        conn.execute(_CACHE_INDEX_SQL)
        conn.execute(
            "INSERT INTO financial_data_cache "
            "SELECT 'finmind', stock_no, dataset, data_json, fetched_at FROM _fdc_old"
        )
        conn.execute("DROP TABLE _fdc_old")


class SWRCacheBase:
    """Mixin providing SWR cache read/write/refresh for a named provider.

    Subclasses must set `provider_name: str` and implement `_fetch_raw`.
    """

    provider_name: str  # overridden in each subclass

    def __init__(
        self,
        db_path: str | None = None,
        stale_days: int = 15,
    ) -> None:
        import os
        self._db_path = db_path or os.getenv("FINMIND_CACHE_DB_PATH", "data/stock_monitor.db")
        self._stale_sec = stale_days * 86_400
        # Run-level in-memory cache: avoids duplicate calls within one scan.
        self._mem: dict[tuple[str, str], list[dict]] = {}
        self._refreshing: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        self._ensure_cache_table()

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------

    def _ensure_cache_table(self) -> None:
        try:
            with sqlite3.connect(self._db_path, timeout=5) as conn:
                # Check if table exists first
                exists = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='financial_data_cache'"
                ).fetchone()
                if exists:
                    _migrate_cache_table(conn)
                else:
                    conn.execute(_CACHE_CREATE_SQL)
                    conn.execute(_CACHE_INDEX_SQL)
                conn.commit()
        except sqlite3.Error:
            pass  # degrade gracefully

    # ------------------------------------------------------------------
    # DB cache helpers
    # ------------------------------------------------------------------

    def _db_get(self, stock_no: str, dataset: str) -> tuple[list[dict], int] | None:
        """Return (rows, fetched_at) from this provider's cache, or None."""
        try:
            with sqlite3.connect(self._db_path, timeout=5) as conn:
                row = conn.execute(
                    "SELECT data_json, fetched_at FROM financial_data_cache "
                    "WHERE provider=? AND stock_no=? AND dataset=?",
                    (self.provider_name, str(stock_no), dataset),
                ).fetchone()
            if row is None:
                return None
            return json.loads(row[0]), int(row[1])
        except (sqlite3.Error, json.JSONDecodeError, ValueError):
            return None

    def _db_put(self, stock_no: str, dataset: str, rows: list[dict]) -> None:
        """Upsert rows into this provider's cache with current timestamp."""
        try:
            with sqlite3.connect(self._db_path, timeout=5) as conn:
                conn.execute(
                    """
                    INSERT INTO financial_data_cache
                        (provider, stock_no, dataset, data_json, fetched_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(provider, stock_no, dataset) DO UPDATE SET
                        data_json  = excluded.data_json,
                        fetched_at = excluded.fetched_at
                    """,
                    (
                        self.provider_name,
                        str(stock_no),
                        dataset,
                        json.dumps(rows, ensure_ascii=False),
                        int(time.time()),
                    ),
                )
                conn.commit()
        except sqlite3.Error:
            pass  # non-fatal

    def _db_put_many(self, entries: list[tuple[str, str, list[dict]]]) -> None:
        """Bulk upsert multiple (stock_no, dataset, rows) entries at once.

        Used by adapters that fetch all stocks in one HTTP call (e.g. MOPS quarterly).
        """
        if not entries:
            return
        now = int(time.time())
        try:
            with sqlite3.connect(self._db_path, timeout=30) as conn:
                conn.executemany(
                    """
                    INSERT INTO financial_data_cache
                        (provider, stock_no, dataset, data_json, fetched_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(provider, stock_no, dataset) DO UPDATE SET
                        data_json  = excluded.data_json,
                        fetched_at = excluded.fetched_at
                    """,
                    [
                        (
                            self.provider_name,
                            str(stock_no),
                            dataset,
                            json.dumps(rows, ensure_ascii=False),
                            now,
                        )
                        for stock_no, dataset, rows in entries
                    ],
                )
                conn.commit()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------------
    # Background refresh
    # ------------------------------------------------------------------

    def _spawn_refresh(self, stock_no: str, dataset: str) -> None:
        """Spawn a daemon thread to refresh a stale cache entry."""
        key = (str(stock_no), dataset)
        with self._lock:
            if key in self._refreshing:
                return
            self._refreshing.add(key)

        def _do() -> None:
            try:
                rows = self._fetch_raw(dataset, stock_no)
                if rows is not None:
                    self._db_put(stock_no, dataset, rows)
                    with self._lock:
                        self._mem[key] = rows
            finally:
                with self._lock:
                    self._refreshing.discard(key)

        t = threading.Thread(
            target=_do,
            daemon=True,
            name=f"{self.provider_name}-refresh-{dataset}-{stock_no}",
        )
        t.start()

    # ------------------------------------------------------------------
    # Core SWR fetch
    # ------------------------------------------------------------------

    def _fetch(self, dataset: str, stock_no: str) -> list[dict]:
        """SWR fetch with ProviderUnavailableError on transient failure.

        1. In-memory hit → return immediately.
        2. DB fresh hit  → return, promote to mem cache.
        3. DB stale hit  → return stale, spawn background refresh.
        4. Cache miss    → call _fetch_raw:
               rows      → store in cache, return.
               None      → raise ProviderUnavailableError (do NOT cache).
        """
        key = (str(stock_no), dataset)

        # 1. In-memory
        with self._lock:
            if key in self._mem:
                return self._mem[key]

        # 2 & 3. DB cache
        cached = self._db_get(stock_no, dataset)
        if cached is not None:
            rows, fetched_at = cached
            age = int(time.time()) - fetched_at
            with self._lock:
                self._mem[key] = rows
            if age >= self._stale_sec:
                self._spawn_refresh(stock_no, dataset)
            return rows

        # 4. Cache miss
        rows = self._fetch_raw(dataset, stock_no)
        if rows is None:
            raise ProviderUnavailableError(
                f"{self.provider_name}: transient failure for {dataset}/{stock_no}"
            )
        self._db_put(stock_no, dataset, rows)
        with self._lock:
            self._mem[key] = rows
        return rows

    # ------------------------------------------------------------------
    # Abstract
    # ------------------------------------------------------------------

    @abstractmethod
    def _fetch_raw(self, dataset: str, stock_no: str) -> list[dict] | None:
        """Fetch from upstream without caching.

        Must return:
            list[dict]  — success (status 200); may be [] if no data exists.
            None        — transient failure; caller will raise ProviderUnavailableError.
        """
