import json
import os
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import app
import common
import data_health


class TempDbTestCase(unittest.TestCase):
    def setUp(self):
        tmp_root = Path.cwd() / ".test_tmp"
        tmp_root.mkdir(exist_ok=True)
        self._old_db_path = common.DB_PATH
        self._db_path = tmp_root / f"quality_gates_{os.getpid()}_{uuid.uuid4().hex}.db"
        # Patch both app and common so db_connect() (defined in common) uses the temp DB
        app.DB_PATH = self._db_path
        common.DB_PATH = self._db_path
        app.init_db()

    def tearDown(self):
        app.DB_PATH = self._old_db_path
        common.DB_PATH = self._old_db_path
        for path in [
            self._db_path,
            self._db_path.with_name(f"{self._db_path.name}-journal"),
            self._db_path.with_name(f"{self._db_path.name}-wal"),
            self._db_path.with_name(f"{self._db_path.name}-shm"),
        ]:
            try:
                path.unlink()
            except (FileNotFoundError, PermissionError):
                pass


class QualityGateTests(TempDbTestCase):
    def test_future_listing_no_quote_is_keep_excluded(self):
        now = app.utc_now()
        metadata = {
            "stock_id": "009821",
            "name": "Future ETF",
            "start_date": "2999-01-01",
        }
        with app.db_connect() as conn:
            conn.execute(
                """
                INSERT INTO universe (symbol, name, asset_type, currency, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                ("009821.TW", "Future ETF", "台股/ETF", "TWD", now, now),
            )
            conn.execute(
                """
                INSERT INTO universe_membership (
                    scope, symbol, market, instrument_type, source, source_symbol, included_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    app.TW_FULL_MARKET_SCOPE,
                    "009821.TW",
                    "twse",
                    "ETF",
                    "test",
                    "009821",
                    now,
                    json.dumps(metadata),
                ),
            )

        remediation = app.get_no_quote_remediation()
        item = remediation["items"][0]

        self.assertEqual(remediation["by_disposition"], {"keep_excluded": 1})
        self.assertEqual(item["reason"], "future_listing_no_quote")
        self.assertEqual(item["disposition"], "keep_excluded")
        self.assertTrue(item["exclude_from_ranking"])

    def test_missing_outcome_closes_when_due_snapshot_exists(self):
        start_at = "2026-06-01T00:00:00+00:00"
        due_at = "2026-06-06T00:00:00+00:00"
        captured_at = "2026-06-07T00:00:00+00:00"
        now = app.utc_now()
        with app.db_connect() as conn:
            conn.execute(
                """
                INSERT INTO universe (symbol, name, asset_type, currency, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                ("TEST.TW", "Test", "台股", "TWD", now, now),
            )
            scan_id = conn.execute(
                """
                INSERT INTO scan_runs (created_at, universe_count, scanned_count, opportunity_count, threshold, results_json)
                VALUES (?, 1, 1, 1, ?, ?)
                """,
                (start_at, 0.1, "{}"),
            ).lastrowid
            opportunity_id = conn.execute(
                """
                INSERT INTO opportunities (
                    scan_run_id, symbol, name, asset_type, priority, category, thesis,
                    price, score, created_at, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (scan_id, "TEST.TW", "Test", "台股", 50, "Watch", "test", 100, 60, start_at, "{}"),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO recommendation_outcomes (
                    opportunity_id, scan_run_id, symbol, horizon_days, start_price,
                    start_at, due_at, status, created_at, updated_at, raw_json
                )
                VALUES (?, ?, ?, 5, 100, ?, ?, 'missing_price', ?, ?, ?)
                """,
                (opportunity_id, scan_id, "TEST.TW", start_at, due_at, start_at, start_at, "{}"),
            )
            conn.execute(
                """
                INSERT INTO price_snapshots (symbol, captured_at, price, currency, raw_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("TEST.TW", captured_at, 110, "TWD", "{}"),
            )

        result = app.update_recommendation_outcomes(limit=10)
        missing = app.get_missing_recommendation_outcome_prices(limit=10)

        self.assertEqual(result["updated"], 1)
        self.assertEqual(missing["count"], 0)
        with app.db_connect() as conn:
            row = conn.execute("SELECT status, return_percent FROM recommendation_outcomes").fetchone()
        self.assertEqual(row["status"], "ready")
        self.assertAlmostEqual(row["return_percent"], 10.0)


class PureQualityGateTests(unittest.TestCase):
    def test_data_health_ranking_ready_with_only_contained_no_quote_warning(self):
        today = datetime.now(app.TAIPEI_TZ).strftime("%Y-%m-%d")
        with (
            patch("data_health.scheduler_state", return_value={"enabled": True, "thread_alive": True, "healthy": True, "scope": app.TW_FULL_MARKET_SCOPE}),
            patch("data_health.get_aegis_snapshot_status", return_value={"available": True, "stale": False, "copied_age_hours": 1}),
            patch("data_health.get_tw_full_market_status", return_value={"snapshot_coverage_percent": 99.14, "no_quote_count": 21}),
            patch("data_health.get_active_etf_source_status", return_value={"stale": False}),
            patch(
                "scan.read_daily_recommendation_log_history",
                return_value={
                    "records": [{"date": today, "generated_at": app.utc_now()}],
                    "path": "x",
                    "log_count": 1,
                    "parse_errors": 0,
                    "summary": {},
                },
            ),
            patch("data_health.update_recommendation_outcomes", return_value={"pending": 10, "missing_price": 0}),
            patch("data_health.get_no_quote_remediation", return_value={"count": 21, "by_disposition": {"keep_excluded": 21}}),
        ):
            health = app.get_data_health()

        self.assertEqual(health["overall_status"], "warn")
        self.assertFalse(health["readiness"]["strict_decision_readiness"])
        self.assertTrue(health["readiness"]["ranking_readiness"])
        self.assertEqual(health["readiness"]["contained_warnings"], ["full_market_coverage"])
        self.assertEqual(health["readiness"]["uncontained_warnings"], [])
        self.assertEqual(health["actions"][0]["by_disposition"], {"keep_excluded": 21})

    def test_pre_trade_checklist_blocks_low_liquidity(self):
        item = {
            "symbol": "2330.TW",
            "tier": "core_watch",
            "spike_risk": False,
            "change_5d": 1.5,
            "financial_audit_status": "pass",
            "volume": 100,
            "data_confidence": "high",
            "data_coverage": 0.9,
            "instrument_type": "股票",
            "data_policy": {},
        }
        health = {"overall_status": "ok"}

        checklist = app.pre_trade_checklist_for_candidate(item, health)

        self.assertEqual(checklist["overall_status"], "block")
        self.assertFalse(checklist["trade_ready"])
        statuses = {row["name"]: row["status"] for row in checklist["checks"]}
        self.assertEqual(statuses["liquidity"], "block")


if __name__ == "__main__":
    unittest.main()
