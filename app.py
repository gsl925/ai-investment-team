from __future__ import annotations

import csv
import json
import math
import re
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse

import requests

from common import (
    ROOT, STATIC_DIR, DB_PATH, AEGIS_SNAPSHOT_DB, AEGIS_SNAPSHOT_METADATA,
    AEGIS_SOURCE_DB, AEGIS_REFRESH_SCRIPT, EXPORT_DIR, PORT, HTTP_TIMEOUT,
    SCHEDULER_LOCK, SCHEDULER_STOP, SCHEDULER_STATE,
    WATCHLIST_LOCK, WATCHLIST_STOP, WATCHLIST_STATE, WATCHLIST_BASELINES,
    TAIPEI_TZ, ACTIVE_ETF_IMPORT_SLOTS, DAILY_RECOMMENDATION_LOG_TIME,
    ACTIVE_ETF_IMPORT_STATE,
    FACTOR_WEIGHTS, FACTOR_LABELS, FACTOR_REQUIRED_FIELDS,
    FINANCIAL_AUDIT_RULES, ASSET_PRESETS, EXPANDED_UNIVERSE,
    YAHOO_SCREENER_IDS, NEWS_KEYWORDS,
    utc_now, timestamp_slug, ensure_export_dirs, db_connect,
    safe_float, market_number, parse_utc_timestamp,
    classify_asset, aegis_connect, yahoo_get, yahoo_get_query2,
    export_data_audit_markdown, record_data_import_audit,
    taiwan_stock_id, is_fund_like_symbol, write_text_file,
)
from data_health import (
    yyyymm_age_months,
    quarter_age_quarters,
    read_aegis_snapshot_metadata,
    should_refresh_aegis_snapshot,
    get_aegis_snapshot_status,
    refresh_aegis_snapshot_if_needed,
    force_refresh_aegis_snapshot,
    get_db_status,
    latest_scan_errors_by_symbol,
    aegis_symbol_coverage,
    classify_unsnapped_gap,
    export_unsnapped_universe_report,
    get_unsnapped_universe_report,
    audit_financial_payload,
    get_financial_data_audit,
    get_data_quality_history,
    health_status_rank,
    get_no_quote_remediation,
    data_health_actions,
    get_data_health,
    pre_trade_checklist_for_candidate,
    get_pre_trade_checklist,
)
from outcomes import (
    OUTCOME_HORIZONS,
    add_recommendation_outcomes,
    update_recommendation_outcomes,
    get_recommendation_outcomes,
    get_missing_recommendation_outcome_prices,
    outcome_group_labels,
    summarize_outcome_group,
    backfill_recommendation_outcomes,
)
from tw_universe import (
    TW_FULL_MARKET_SCOPE, TW_FULL_MARKET_POLICY,
    TW_FULL_MARKET_MARKETS, TW_FULL_MARKET_INSTRUMENTS,
    expand_universe, import_universe_csv, asset_type_from_yahoo_quote,
    sync_yahoo_max_universe, import_universe_from_active_etf_holdings,
    import_twse_universe_from_openapi, tw_market_label, tw_full_market_symbol,
    tw_full_market_asset_type, get_tw_full_market_rows_from_aegis,
    sync_tw_full_market_universe_from_aegis, get_tw_full_market_status,
    get_universe_scope_definitions, get_tw_full_market_quote_gaps,
    normalize_tw_stock_symbol,
)
from scheduler import (
    scheduler_state, start_scheduler, stop_scheduler,
    scheduler_health_fields, scheduler_discovery_limit, cursor_after_scan_payload,
    scheduler_profile_key, read_scheduler_state_store, write_scheduler_state_store,
    infer_scheduler_cursor_offset, update_scheduler_state, record_scheduler_run,
    run_scheduler_task, scheduler_loop,
)
from active_etf import (
    active_etf_signal_score,
    active_etf_evidence,
    active_etf_change_type,
    active_etf_change_label,
    active_etf_source_value,
    html_text,
    parse_numeric_text,
    parse_market_value_text,
    parse_etf_option_list,
    parse_html_table_rows,
    parse_zdsetf_detail,
    import_active_etf_zdsetf,
    validate_active_etf_holding_row,
    rebuild_active_etf_changes_from_holdings,
    import_active_etf_holdings_csv,
    get_active_etf_holdings,
    import_active_etf_changes_csv,
    active_etf_completed_slots,
    active_etf_import_due_slot,
    record_active_etf_import_run,
    active_etf_csv_has_rows,
    get_active_etf_source_status,
    get_active_etf_official_source_candidates,
    sync_active_etf_sources,
    run_due_active_etf_import,
    get_active_etf_changes,
    get_active_etf_audit,
    active_etf_flow_score,
)
from technical_indicators import (
    percent_change, moving_average, ema_series, ema_value, macd_metrics,
    rsi, stochastic_kd, atr, bollinger_metrics, directional_movement_metrics,
    obv_slope, rolling_vwap, average_volume, hurst_exponent, volatility,
    percentile_rank, volatility_regime_percentile, relative_strength_percentiles,
    max_drawdown,
)
from scoring import (
    add_score, empty_factor_state, add_factor_score, set_factor_score,
    applicable_factor_weights, weighted_factor_score, confidence_from_coverage,
    data_quality_entry, factor_payload,
    GRADE_RANK, GRADE_BY_RANK, cap_grade,
    preliminary_grade, financial_status_from_quality, opportunity_grade,
    requires_equity_fundamentals, build_factor_data_quality, build_data_policy,
)
from models import Quote, AegisFundamentals
from data_sources import (
    roc_date_to_datetime, get_universe_symbol_metadata,
    get_aegis_fundamentals, get_aegis_chart, get_aegis_quote,
    tpex_get_json, tpex_mainboard_quotes, tpex_esb_quotes,
    get_tpex_quote_row, tpex_chart_point, get_tpex_chart, get_tpex_quote,
    latest_yahoo_timeseries_rows, yahoo_reported_raw, quarter_from_date,
    get_yahoo_equity_fundamentals, get_search_profile, get_quote,
    get_chart, chart_range_for_backtest,
    get_news, get_news_for_query, get_taiwan_stock_news, dedupe_news, news_sentiment,
    get_industry_comparison,
    get_peer_daily_changes, get_benchmark_daily_change,
)
from scan import (
    daily_recommendation_log_paths, recommendation_log_row,
    write_daily_recommendation_log, read_daily_recommendation_log_history,
    RECOMMENDATION_PERSISTENCE_RULES, compute_recommendation_persistence,
    backfill_daily_recommendation_persistence,
    get_daily_recommendation_performance, run_due_daily_recommendation_log,
    build_opportunity, save_scan_run, append_jsonl, export_scan_markdown,
    scan_universe_rows, scan_market,
    save_backtest_run, run_backtests,
    pct_text, report_section,
    build_data_retrieval_report, build_technical_agent_report,
    build_macro_news_report, build_flow_agent_report,
    build_risk_report, build_chief_strategist_report,
    save_agent_reports, export_research_markdown, run_research_team,
)

_market_regime_cache: dict[str, Any] = {}

def get_market_regime(symbol: str = "0050.TW") -> dict[str, Any]:
    cached = _market_regime_cache.get(symbol)
    if cached and (time.time() - cached["ts"]) < 3600:
        return cached["data"]
    try:
        chart = get_chart(symbol, period="3mo")
        if not chart or len(chart) < 20:
            raise ValueError("insufficient chart data")
        ma20 = moving_average(chart, 20)
        ma60 = moving_average(chart, 60)
        price = safe_float(chart[-1].get("close") or chart[-1].get("price"))
        if price and ma20 and ma60:
            if price > ma20 and ma20 > ma60:
                regime = "bull"
            elif price < ma20 and ma20 < ma60:
                regime = "bear"
            else:
                regime = "neutral"
        elif price and ma20:
            regime = "bull" if price > ma20 else "bear"
        else:
            regime = "unknown"
        data: dict[str, Any] = {"regime": regime, "symbol": symbol, "price": price, "ma20": ma20, "ma60": ma60}
    except Exception:
        data = {"regime": "unknown", "symbol": symbol}
    _market_regime_cache[symbol] = {"ts": time.time(), "data": data}
    return data


def init_db() -> None:
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS universe (
                symbol TEXT PRIMARY KEY,
                name TEXT,
                asset_type TEXT NOT NULL,
                currency TEXT,
                sector TEXT,
                industry TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS universe_membership (
                scope TEXT NOT NULL,
                symbol TEXT NOT NULL,
                market TEXT,
                instrument_type TEXT,
                source TEXT NOT NULL,
                source_symbol TEXT,
                included_at TEXT NOT NULL,
                metadata_json TEXT,
                PRIMARY KEY (scope, symbol),
                FOREIGN KEY (symbol) REFERENCES universe(symbol) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_universe_membership_scope_market
                ON universe_membership(scope, market, instrument_type);

            CREATE TABLE IF NOT EXISTS price_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                price REAL,
                currency TEXT,
                change_1d REAL,
                change_5d REAL,
                change_1m REAL,
                change_3m REAL,
                volume REAL,
                avg_volume REAL,
                ma_20 REAL,
                ma_60 REAL,
                volatility_annual REAL,
                daily_volatility REAL,
                movement_threshold REAL,
                score INTEGER,
                action TEXT,
                stance TEXT,
                raw_json TEXT NOT NULL,
                FOREIGN KEY (symbol) REFERENCES universe(symbol)
            );

            CREATE INDEX IF NOT EXISTS idx_price_snapshots_symbol_time
                ON price_snapshots(symbol, captured_at DESC);

            CREATE TABLE IF NOT EXISTS technical_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                category TEXT NOT NULL,
                label TEXT NOT NULL,
                impact INTEGER NOT NULL DEFAULT 0,
                detail TEXT,
                FOREIGN KEY (snapshot_id) REFERENCES price_snapshots(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                snapshot_id INTEGER,
                title TEXT NOT NULL,
                link TEXT,
                source TEXT,
                published TEXT,
                sentiment TEXT,
                is_catalyst INTEGER NOT NULL DEFAULT 0,
                captured_at TEXT NOT NULL,
                FOREIGN KEY (snapshot_id) REFERENCES price_snapshots(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_news_items_symbol_time
                ON news_items(symbol, captured_at DESC);

            CREATE TABLE IF NOT EXISTS event_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT,
                FOREIGN KEY (snapshot_id) REFERENCES price_snapshots(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_event_alerts_symbol_time
                ON event_alerts(symbol, captured_at DESC);

            CREATE TABLE IF NOT EXISTS watchlist_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                triggered_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                change_pct REAL NOT NULL,
                price REAL,
                baseline_price REAL,
                tier TEXT,
                threshold_pct REAL NOT NULL,
                title TEXT NOT NULL,
                detail TEXT,
                snapshot_id INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_watchlist_alerts_triggered
                ON watchlist_alerts(triggered_at DESC);

            CREATE TABLE IF NOT EXISTS agent_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                report_type TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                source_snapshot_ids TEXT,
                metadata_json TEXT
            );

            CREATE TABLE IF NOT EXISTS recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                score INTEGER,
                action TEXT,
                stance TEXT,
                risks_json TEXT,
                metrics_json TEXT,
                FOREIGN KEY (snapshot_id) REFERENCES price_snapshots(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS data_quality_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                factor TEXT NOT NULL,
                factor_label TEXT,
                coverage REAL NOT NULL DEFAULT 0,
                confidence TEXT NOT NULL,
                source_status TEXT NOT NULL,
                required_fields_json TEXT NOT NULL,
                used_fields_json TEXT NOT NULL,
                missing_fields_json TEXT NOT NULL,
                evidence_json TEXT,
                FOREIGN KEY (snapshot_id) REFERENCES price_snapshots(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_data_quality_symbol_time
                ON data_quality_snapshots(symbol, captured_at DESC);

            CREATE TABLE IF NOT EXISTS user_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                decided_at TEXT NOT NULL,
                decision TEXT NOT NULL,
                rationale TEXT,
                related_recommendation_id INTEGER,
                FOREIGN KEY (related_recommendation_id) REFERENCES recommendations(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS backtest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                symbols TEXT NOT NULL,
                days INTEGER NOT NULL,
                strategy TEXT NOT NULL,
                cost_bps REAL NOT NULL DEFAULT 0,
                results_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                universe_count INTEGER NOT NULL,
                scanned_count INTEGER NOT NULL,
                opportunity_count INTEGER NOT NULL,
                threshold REAL,
                results_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_run_id INTEGER NOT NULL,
                snapshot_id INTEGER,
                symbol TEXT NOT NULL,
                name TEXT,
                asset_type TEXT,
                priority INTEGER NOT NULL,
                category TEXT NOT NULL,
                thesis TEXT NOT NULL,
                price REAL,
                change_1d REAL,
                change_5d REAL,
                score INTEGER,
                action TEXT,
                created_at TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                FOREIGN KEY (scan_run_id) REFERENCES scan_runs(id) ON DELETE CASCADE,
                FOREIGN KEY (snapshot_id) REFERENCES price_snapshots(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_opportunities_scan_priority
                ON opportunities(scan_run_id, priority DESC);

            CREATE TABLE IF NOT EXISTS recommendation_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opportunity_id INTEGER NOT NULL,
                scan_run_id INTEGER NOT NULL,
                snapshot_id INTEGER,
                symbol TEXT NOT NULL,
                horizon_days INTEGER NOT NULL,
                start_price REAL,
                start_at TEXT NOT NULL,
                due_at TEXT NOT NULL,
                outcome_snapshot_id INTEGER,
                outcome_price REAL,
                outcome_at TEXT,
                return_percent REAL,
                status TEXT NOT NULL DEFAULT 'pending',
                grade TEXT,
                action_bucket TEXT,
                score INTEGER,
                priority INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                UNIQUE(opportunity_id, horizon_days),
                FOREIGN KEY (opportunity_id) REFERENCES opportunities(id) ON DELETE CASCADE,
                FOREIGN KEY (scan_run_id) REFERENCES scan_runs(id) ON DELETE CASCADE,
                FOREIGN KEY (snapshot_id) REFERENCES price_snapshots(id) ON DELETE SET NULL,
                FOREIGN KEY (outcome_snapshot_id) REFERENCES price_snapshots(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_recommendation_outcomes_status_due
                ON recommendation_outcomes(status, due_at);

            CREATE INDEX IF NOT EXISTS idx_recommendation_outcomes_symbol_horizon
                ON recommendation_outcomes(symbol, horizon_days, start_at DESC);

            CREATE TABLE IF NOT EXISTS daily_recommendation_persistence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT,
                tier TEXT,
                price REAL,
                score INTEGER,
                appearances INTEGER,
                logged_days INTEGER,
                coverage_percent REAL,
                streak INTEGER,
                persistence_score REAL,
                qualified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(date, symbol)
            );

            CREATE INDEX IF NOT EXISTS idx_daily_recommendation_persistence_symbol_date
                ON daily_recommendation_persistence(symbol, date DESC);

            CREATE TABLE IF NOT EXISTS scheduler_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                interval_minutes INTEGER,
                batch_size INTEGER,
                refresh_minutes INTEGER,
                min_priority INTEGER,
                asset_type TEXT,
                scope TEXT,
                scan_run_id INTEGER,
                result_json TEXT,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS scheduler_state_store (
                profile_key TEXT PRIMARY KEY,
                asset_type TEXT,
                scope TEXT,
                cursor_offset INTEGER NOT NULL DEFAULT 0,
                interval_minutes INTEGER,
                batch_size INTEGER,
                refresh_minutes INTEGER,
                min_priority INTEGER,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_scan_run_id INTEGER,
                last_started_at TEXT,
                last_finished_at TEXT
            );

            CREATE TABLE IF NOT EXISTS active_etf_funds (
                etf_symbol TEXT PRIMARY KEY,
                etf_name TEXT,
                issuer TEXT,
                market TEXT NOT NULL DEFAULT 'TW',
                enabled INTEGER NOT NULL DEFAULT 1,
                source_url TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS active_etf_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                etf_symbol TEXT NOT NULL,
                etf_name TEXT,
                stock_symbol TEXT NOT NULL,
                stock_name TEXT,
                change_type TEXT NOT NULL,
                previous_shares REAL,
                current_shares REAL,
                share_delta REAL,
                weight REAL,
                estimated_price REAL,
                estimated_value REAL,
                source TEXT,
                captured_at TEXT NOT NULL,
                signal_score INTEGER NOT NULL DEFAULT 0,
                thesis TEXT,
                raw_json TEXT NOT NULL,
                UNIQUE(trade_date, etf_symbol, stock_symbol, change_type),
                FOREIGN KEY (etf_symbol) REFERENCES active_etf_funds(etf_symbol) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_active_etf_changes_date_score
                ON active_etf_changes(trade_date DESC, signal_score DESC);

            CREATE INDEX IF NOT EXISTS idx_active_etf_changes_stock_date
                ON active_etf_changes(stock_symbol, trade_date DESC);

            CREATE TABLE IF NOT EXISTS active_etf_holdings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                etf_symbol TEXT NOT NULL,
                etf_name TEXT,
                issuer TEXT,
                stock_symbol TEXT NOT NULL,
                stock_name TEXT,
                shares REAL,
                weight REAL,
                market_value REAL,
                source TEXT,
                source_url TEXT,
                captured_at TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                UNIQUE(trade_date, etf_symbol, stock_symbol),
                FOREIGN KEY (etf_symbol) REFERENCES active_etf_funds(etf_symbol) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_active_etf_holdings_date_etf
                ON active_etf_holdings(trade_date DESC, etf_symbol, stock_symbol);

            CREATE TABLE IF NOT EXISTS data_import_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset TEXT NOT NULL,
                source_file TEXT,
                source_url TEXT,
                source_label TEXT,
                imported_at TEXT NOT NULL,
                status TEXT NOT NULL,
                row_count INTEGER NOT NULL DEFAULT 0,
                inserted INTEGER NOT NULL DEFAULT 0,
                updated INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                warning_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                fields_json TEXT,
                warnings_json TEXT,
                errors_json TEXT,
                stats_json TEXT,
                result_json TEXT,
                markdown_path TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_data_import_audits_dataset_time
                ON data_import_audits(dataset, imported_at DESC);

            CREATE TABLE IF NOT EXISTS active_etf_import_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scheduled_date TEXT NOT NULL,
                scheduled_slot TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                source_file TEXT,
                inserted INTEGER NOT NULL DEFAULT 0,
                updated INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                result_json TEXT,
                error TEXT,
                UNIQUE(scheduled_date, scheduled_slot)
            );
            """
        )
        for table, column in [
            ("scheduler_runs", "scope"),
            ("scheduler_state_store", "scope"),
        ]:
            columns = {
                row["name"]
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if column not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
        now = utc_now()
        for symbols in ASSET_PRESETS.values():
            for symbol in symbols:
                conn.execute(
                    """
                    INSERT INTO universe (symbol, asset_type, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(symbol) DO NOTHING
                    """,
                    (symbol, classify_asset(symbol), now, now),
                )


def get_data_import_audits(limit: int = 50, dataset: str | None = None) -> dict[str, Any]:
    limit = max(1, min(limit, 500))
    where = []
    params: list[Any] = []
    if dataset:
        where.append("dataset = ?")
        params.append(dataset)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)
    with db_connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, dataset, source_file, source_url, source_label, imported_at, status,
                   row_count, inserted, updated, skipped, warning_count, error_count,
                   stats_json, markdown_path
            FROM data_import_audits
            {clause}
            ORDER BY imported_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    audits = []
    for row in rows:
        item = dict(row)
        item["stats"] = json.loads(item.pop("stats_json") or "{}")
        audits.append(item)
    return {"audits": audits}


def get_latest_dashboard() -> dict[str, Any]:
    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    with db_connect() as conn:
        latest_scan = conn.execute(
            """
            SELECT id, created_at, universe_count, scanned_count, opportunity_count, results_json
            FROM scan_runs
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        opportunity_rows = conn.execute(
            """
            SELECT raw_json
            FROM opportunities
            WHERE scan_run_id = COALESCE((SELECT id FROM scan_runs ORDER BY created_at DESC LIMIT 1), -1)
            ORDER BY priority DESC, ABS(COALESCE(change_1d, 0)) DESC
            LIMIT 24
            """
        ).fetchall()
        total_universe = conn.execute("SELECT COUNT(*) FROM universe WHERE enabled = 1").fetchone()[0]
        snapshot_symbols = conn.execute("SELECT COUNT(DISTINCT symbol) FROM price_snapshots").fetchone()[0]
        updated_24h = conn.execute(
            "SELECT COUNT(DISTINCT symbol) FROM price_snapshots WHERE captured_at >= ?",
            (cutoff_24h,),
        ).fetchone()[0]
        updated_7d = conn.execute(
            "SELECT COUNT(DISTINCT symbol) FROM price_snapshots WHERE captured_at >= ?",
            (cutoff_7d,),
        ).fetchone()[0]
        latest_snapshots = conn.execute(
            """
            SELECT symbol, captured_at, price, change_1d, score, action
            FROM price_snapshots
            ORDER BY captured_at DESC
            LIMIT 10
            """
        ).fetchall()

    latest_scan_payload = dict(latest_scan) if latest_scan else None
    if latest_scan_payload:
        try:
            parsed = json.loads(latest_scan_payload.pop("results_json") or "{}")
        except json.JSONDecodeError:
            parsed = {}
        latest_scan_payload["cache_hits"] = parsed.get("cache_hits")
        latest_scan_payload["refreshed_count"] = parsed.get("refreshed_count")
        latest_scan_payload["elapsed_seconds"] = parsed.get("elapsed_seconds")
        latest_scan_payload["available_universe_count"] = parsed.get("available_universe_count")
        latest_scan_payload["offset"] = parsed.get("offset")

    opportunities = []
    for row in opportunity_rows:
        try:
            opportunities.append(json.loads(row["raw_json"]))
        except json.JSONDecodeError:
            continue

    active_etf = get_active_etf_changes(12)
    active_etf_source_status = get_active_etf_source_status()
    today_taipei = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    active_etf_schedule = dict(ACTIVE_ETF_IMPORT_STATE)
    active_etf_schedule["today"] = today_taipei
    active_etf_schedule["completed_slots"] = active_etf_completed_slots(today_taipei)
    coverage = {
        "universe_count": total_universe,
        "snapshot_symbol_count": snapshot_symbols,
        "unsnapped_count": max(0, total_universe - snapshot_symbols),
        "updated_24h_count": updated_24h,
        "updated_7d_count": updated_7d,
        "snapshot_coverage_percent": round(snapshot_symbols / total_universe * 100, 2) if total_universe else 0,
    }
    scheduler = scheduler_state(repair=True)
    data_health = get_data_health()
    return {
        "generated_at": utc_now(),
        "scheduler": scheduler,
        "data_health": data_health,
        "coverage": coverage,
        "tw_full_market": get_tw_full_market_status(),
        "aegis_snapshot": get_aegis_snapshot_status(),
        "latest_scan": latest_scan_payload,
        "opportunities": opportunities,
        "active_etf": {
            "changes": active_etf["changes"][:8],
            "stock_summary": active_etf["stock_summary"][:8],
            "source_status": active_etf_source_status,
        },
        "active_etf_schedule": active_etf_schedule,
        "latest_snapshots": [dict(row) for row in latest_snapshots],
        "disclaimer": "最新進度整合市場雷達、背景輪巡與主動式 ETF 異動；所有內容都是研究線索，不是自動下單建議。",
    }



def json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler: BaseHTTPRequestHandler, body: bytes, content_type: str) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def technical_score_at(chart: list[dict[str, float]]) -> tuple[int, list[dict[str, Any]]]:
    score = 50
    signals: list[dict[str, Any]] = []
    change_1m = percent_change(chart, 21)
    ma_20 = moving_average(chart, 20)
    ma_60 = moving_average(chart, 60)
    vol = volatility(chart)
    price = chart[-1]["close"] if chart else None

    if change_1m is not None:
        if 2 <= change_1m <= 18:
            score = add_score(score, signals, "技術面", "近一月動能正向", 8, f"近 21 個交易日約 {change_1m:.1f}%。")
        elif change_1m < -8:
            score = add_score(score, signals, "技術面", "短線轉弱", -8, f"近 21 個交易日約 {change_1m:.1f}%。")
        elif change_1m > 25:
            score = add_score(score, signals, "技術面", "短線過熱", -4, f"近 21 個交易日約 {change_1m:.1f}%。")

    if ma_20 and ma_60 and price:
        if price > ma_20 > ma_60:
            score = add_score(score, signals, "技術面", "均線排列偏多", 8, "現價高於 20 日均線，20 日均線高於 60 日均線。")
        elif price < ma_20 < ma_60:
            score = add_score(score, signals, "技術面", "均線排列偏空", -8, "現價低於 20 日均線，20 日均線低於 60 日均線。")

    if vol is not None:
        if vol >= 55:
            score = add_score(score, signals, "風險", "波動偏高", -7, f"近三個月年化波動約 {vol:.1f}%。")
        elif vol <= 22:
            score = add_score(score, signals, "風險", "波動相對可控", 3, f"近三個月年化波動約 {vol:.1f}%。")

    return max(0, min(100, score)), signals


def score_to_position(score: int) -> float:
    if score >= 72:
        return 1.0
    if score >= 58:
        return 0.5
    if score <= 42:
        return 0.0
    return 0.25


def run_symbol_backtest(symbol: str, days: int = 180, cost_bps: float = 5.0) -> dict[str, Any]:
    normalized = symbol.strip().upper()
    days = max(30, min(days, 2500))
    cost_bps = max(0.0, min(cost_bps, 100.0))
    chart = get_chart(normalized, chart_range_for_backtest(days))
    min_lookback = 64
    if len(chart) < min_lookback + 2:
        raise ValueError(f"{normalized} 歷史資料不足，至少需要 {min_lookback + 2} 筆日線。")

    start_index = max(min_lookback, len(chart) - days - 1)
    equity = 1.0
    benchmark = 1.0
    position = 0.0
    trades = 0
    equity_curve = [{"time": chart[start_index]["time"], "value": equity, "benchmark": benchmark}]
    observations: list[dict[str, Any]] = []

    for idx in range(start_index, len(chart) - 1):
        history = chart[: idx + 1]
        score, signals = technical_score_at(history)
        target_position = score_to_position(score)
        turnover = abs(target_position - position)
        if turnover > 0:
            trades += 1
            equity *= 1 - (turnover * cost_bps / 10000)
        position = target_position

        current_close = chart[idx]["close"]
        next_close = chart[idx + 1]["close"]
        daily_return = next_close / current_close - 1 if current_close > 0 else 0
        equity *= 1 + position * daily_return
        benchmark *= 1 + daily_return
        observations.append(
            {
                "time": chart[idx]["time"],
                "score": score,
                "position": position,
                "close": current_close,
                "next_return": daily_return * 100,
                "signals": signals[:3],
            }
        )
        equity_curve.append({"time": chart[idx + 1]["time"], "value": equity, "benchmark": benchmark})

    returns = [
        equity_curve[i]["value"] / equity_curve[i - 1]["value"] - 1
        for i in range(1, len(equity_curve))
        if equity_curve[i - 1]["value"] > 0
    ]
    annualized_return = None
    if len(equity_curve) > 1:
        annualized_return = (equity_curve[-1]["value"] ** (252 / (len(equity_curve) - 1)) - 1) * 100
    annualized_vol = statistics.stdev(returns) * math.sqrt(252) * 100 if len(returns) > 2 else None
    sharpe = annualized_return / annualized_vol if annualized_return is not None and annualized_vol else None

    return {
        "symbol": normalized,
        "days_requested": days,
        "data_points": len(chart),
        "tested_days": len(equity_curve) - 1,
        "start_time": chart[start_index]["time"],
        "end_time": chart[-1]["time"],
        "strategy": "technical_score_position_v1",
        "cost_bps": cost_bps,
        "total_return": (equity - 1) * 100,
        "benchmark_return": (benchmark - 1) * 100,
        "excess_return": (equity - benchmark) * 100,
        "annualized_return": annualized_return,
        "annualized_volatility": annualized_vol,
        "sharpe_like": sharpe,
        "max_drawdown": max_drawdown([point["value"] for point in equity_curve]),
        "benchmark_max_drawdown": max_drawdown([point["benchmark"] for point in equity_curve]),
        "trades": trades,
        "final_position": position,
        "latest_observation": observations[-1] if observations else None,
        "equity_curve": equity_curve[-120:],
        "notes": [
            "此回測只使用歷史收盤價與技術規則，不包含新聞、財報、滑價、稅費與盤中成交限制。",
            "分數於第 N 日收盤後計算，持倉套用到第 N+1 日報酬，避免使用未來資料。",
        ],
    }


def recent_daily_volatility(chart: list[dict[str, float]], window: int = 21) -> float | None:
    if len(chart) < window + 1:
        return None
    returns = []
    for prev, current in zip(chart[-window - 1 : -1], chart[-window:], strict=False):
        if prev["close"] > 0:
            returns.append((current["close"] / prev["close"] - 1) * 100)
    if len(returns) < 10:
        return None
    return statistics.stdev(returns)


def movement_threshold(asset_type: str, override: float | None = None) -> float:
    if override is not None and override > 0:
        return override
    if asset_type == "虛擬貨幣":
        return 5.0
    if asset_type == "原物料":
        return 2.5
    return 3.0


def build_market_alerts(
    quote: Quote,
    chart: list[dict[str, float]],
    news: list[dict[str, str]],
    threshold_override: float | None = None,
) -> dict[str, Any]:
    alerts: list[dict[str, Any]] = []
    catalyst_news: list[dict[str, str]] = []
    change_1d = quote.change_percent
    change_5d = percent_change(chart, 5)
    daily_vol = recent_daily_volatility(chart)
    base_threshold = movement_threshold(quote.asset_type, threshold_override)
    dynamic_threshold = base_threshold
    if threshold_override is None and daily_vol is not None:
        dynamic_threshold = max(base_threshold, daily_vol * 1.8)

    def add_alert(kind: str, severity: str, title: str, detail: str) -> None:
        alerts.append(
            {
                "kind": kind,
                "severity": severity,
                "title": title,
                "detail": detail,
            }
        )

    triggered = False
    if change_1d is not None and abs(change_1d) >= dynamic_threshold:
        direction = "上漲" if change_1d > 0 else "下跌"
        triggered = True
        add_alert(
            "price_move",
            "high" if abs(change_1d) >= dynamic_threshold * 1.5 else "medium",
            f"{quote.symbol} 單日{direction} {change_1d:.1f}%",
            f"已超過目前門檻 {dynamic_threshold:.1f}%。系統會優先查看是否有財報、法說、監管、產品或總經新聞可解釋。",
        )

    if change_5d is not None and abs(change_5d) >= base_threshold * 2:
        direction = "上漲" if change_5d > 0 else "下跌"
        triggered = True
        add_alert(
            "trend_move",
            "medium",
            f"{quote.symbol} 5 日累計{direction} {change_5d:.1f}%",
            "短期趨勢變化明顯，需確認是事件驅動、產業輪動，還是單純技術面延伸。",
        )

    negative_news = sum(1 for item in news if item["sentiment"] == "negative")
    positive_news = sum(1 for item in news if item["sentiment"] == "positive")
    if negative_news >= 2:
        add_alert(
            "news_tone",
            "medium",
            f"{quote.symbol} 新聞語氣偏負面",
            f"近期新聞標題負向 {negative_news} 則、正向 {positive_news} 則，需人工閱讀來源確認。",
        )

    if triggered:
        movement = "up" if change_1d is not None and change_1d > 0 else "down"
        catalyst_queries = [
            f"{quote.symbol} {quote.name} stock {movement} why",
            f"{quote.symbol} {quote.name} earnings guidance analyst news",
            f"{quote.symbol} {quote.name} market news today",
        ]
        fetched: list[dict[str, str]] = []
        for query in catalyst_queries:
            fetched.extend(get_news_for_query(query, limit=5))
        catalyst_news = dedupe_news(fetched)[:8]
        if catalyst_news:
            add_alert(
                "catalyst_news",
                "info",
                f"{quote.symbol} 已找到 {len(catalyst_news)} 則可能佐證新聞",
                "新聞只代表候選原因，仍需比對發布時間是否早於價格波動，避免把事後評論當成原因。",
            )
        else:
            add_alert(
                "catalyst_news",
                "medium",
                f"{quote.symbol} 波動明顯但未找到足夠新聞",
                "可能是盤中籌碼、總經、同族群連動或資料來源限制；需改查交易所公告、公司 IR 或專業新聞源。",
            )

    return {
        "alerts": alerts,
        "catalyst_news": catalyst_news,
        "metrics": {
            "change_5d": change_5d,
            "daily_volatility": daily_vol,
            "movement_threshold": dynamic_threshold,
        },
    }


def build_movement_evidence(
    symbol: str,
    quote: Quote,
    change_1d: float | None,
    change_5d: float | None,
    news: list[dict[str, Any]],
    fundamentals: Any,
    rec_metrics: dict[str, Any],
    dynamic_threshold: float,
) -> dict[str, Any] | None:
    """
    When a significant price movement is detected, gather three-layer evidence:
    market-level, sector-level, and stock-specific (technical + news).
    Returns None if no significant movement.
    """
    if change_1d is None or abs(change_1d) < dynamic_threshold:
        return None

    direction = "up" if change_1d > 0 else "down"
    is_tw = quote.asset_type in {"台股", "台股/ETF"}
    industry_group = fundamentals.industry_group if fundamentals else None
    stock_id = fundamentals.stock_id if fundamentals else None

    # --- Market layer ---
    market_ev: dict[str, Any] | None = None
    benchmark_data = get_benchmark_daily_change(quote.asset_type)
    if benchmark_data:
        bm_chg = benchmark_data["change"]
        bm_pct_of_stock = abs(bm_chg) / abs(change_1d / 100) if change_1d else 0
        same_dir = (bm_chg > 0) == (change_1d > 0)
        market_ev = {
            "benchmark": benchmark_data["benchmark"],
            "benchmark_label": benchmark_data["benchmark_label"],
            "benchmark_change": bm_chg,
            "date": benchmark_data["date"],
            "same_direction": same_dir,
            "alignment": "strong" if same_dir and abs(bm_chg) >= 0.01 else ("partial" if same_dir else "diverge"),
        }
        if same_dir and abs(bm_chg) >= 0.015:
            market_ev["note"] = f"大盤（{benchmark_data['benchmark_label']}）昨日同向{'+' if bm_chg > 0 else ''}{bm_chg:.1%}，市場整體環境相符"
        elif same_dir:
            market_ev["note"] = f"大盤昨日{'+' if bm_chg > 0 else ''}{bm_chg:.1%}，方向相同但幅度小，本股異動幅度偏大"
        else:
            market_ev["note"] = f"大盤昨日{'+' if bm_chg > 0 else ''}{bm_chg:.1%}，與本股方向相反，非大盤因素"

    # --- Sector layer ---
    sector_ev: dict[str, Any] | None = None
    if industry_group and is_tw and stock_id:
        peer_data = get_peer_daily_changes(industry_group, exclude_stock_id=stock_id)
        if peer_data:
            same_dir_count = peer_data["up_count"] if direction == "up" else peer_data["down_count"]
            same_dir_pct = same_dir_count / peer_data["peer_count"] if peer_data["peer_count"] else 0
            med = peer_data["median_change"]
            sector_ev = {
                "industry_group": industry_group,
                "peer_count": peer_data["peer_count"],
                "same_direction_count": same_dir_count,
                "same_direction_pct": round(same_dir_pct, 3),
                "median_peer_change": med,
                "data_date": peer_data["latest_date"],
            }
            if same_dir_pct >= 0.60:
                sector_ev["note"] = (
                    f"{industry_group} {same_dir_count}/{peer_data['peer_count']} 同業同步"
                    f"{'上漲' if direction == 'up' else '下跌'}（昨日，中位數 {med:+.1%}），族群性波動明顯"
                )
            elif same_dir_pct >= 0.35:
                sector_ev["note"] = (
                    f"{industry_group} 約半數同業同向（{same_dir_count}/{peer_data['peer_count']}，中位數 {med:+.1%}）"
                )
            else:
                sector_ev["note"] = (
                    f"{industry_group} 同業大多平穩（{same_dir_count}/{peer_data['peer_count']} 同向），"
                    "本股異動較為獨立"
                )

    # --- Technical layer ---
    rsi = rec_metrics.get("rsi_14")
    vol_ratio = rec_metrics.get("volume_ratio")
    ma_20 = rec_metrics.get("ma_20")
    ma_60 = rec_metrics.get("ma_60")
    price = quote.price
    tech_notes: list[str] = []

    volume_spike = vol_ratio is not None and vol_ratio >= 1.8
    if volume_spike:
        tech_notes.append(f"成交量放大 {vol_ratio:.1f}x（均量），籌碼異動明顯")
    elif vol_ratio is not None and vol_ratio < 0.6:
        tech_notes.append(f"成交量萎縮（均量 {vol_ratio:.1f}x），波動可信度偏低")

    if rsi is not None:
        if rsi >= 75:
            tech_notes.append(f"RSI14={rsi:.0f}，技術面超買，注意短線壓力")
        elif rsi <= 30:
            tech_notes.append(f"RSI14={rsi:.0f}，技術面超賣，可能出現反彈")

    if price and ma_20 and ma_60:
        if direction == "down" and price < ma_20 and ma_20 < ma_60:
          tech_notes.append("已跌破 MA20 且站在 MA60 之下，趨勢偏空確認")
        elif direction == "up" and price > ma_20 and ma_20 > ma_60:
            tech_notes.append("站上 MA20 且 MA60 多頭排列，趨勢偏多確認")
        elif direction == "down" and price < ma_20:
            tech_notes.append("跌破 MA20，短期支撐失守")
        elif direction == "up" and price > ma_20:
            tech_notes.append("突破 MA20，短期阻力轉支撐")

    mops_news = [n for n in news if n.get("source") == "MOPS"]
    if mops_news:
        tech_notes.append(f"近期有 {len(mops_news)} 則 MOPS 重大訊息，建議閱讀確認")

    tech_ev: dict[str, Any] = {
        "rsi_14": rsi,
        "volume_ratio": vol_ratio,
        "volume_spike": volume_spike,
        "mops_news_count": len(mops_news),
        "notes": tech_notes,
    }

    # --- Classification ---
    market_aligned = market_ev and market_ev.get("alignment") in ("strong", "partial")
    sector_strong = sector_ev and sector_ev.get("same_direction_pct", 0) >= 0.50
    sector_partial = sector_ev and sector_ev.get("same_direction_pct", 0) >= 0.35
    has_mops = len(mops_news) > 0

    if market_aligned and sector_strong:
        classification = "mixed"
        classification_label = "大盤 + 族群"
        confidence = "high"
        summary_prefix = "大盤與族群同步波動"
    elif market_aligned and not sector_ev:
        classification = "market_wide"
        classification_label = "大盤帶動"
        confidence = "medium"
        summary_prefix = "大盤整體環境相符"
    elif sector_strong:
        classification = "sector_rotation"
        classification_label = "族群輪動"
        confidence = "high"
        summary_prefix = f"{industry_group}族群性波動"
    elif sector_partial:
        classification = "sector_partial"
        classification_label = "族群部分同步"
        confidence = "low"
        summary_prefix = "部分同業同向，原因可能混合"
    elif has_mops:
        classification = "stock_specific"
        classification_label = "個股事件"
        confidence = "medium"
        summary_prefix = "同業大多平穩，存在 MOPS 公告"
    elif volume_spike:
        classification = "stock_specific"
        classification_label = "個股事件"
        confidence = "low"
        summary_prefix = "同業大多平穩，成交量異常放大"
    else:
        classification = "unknown"
        classification_label = "原因待查"
        confidence = "low"
        summary_prefix = "目前資料不足以確認原因"

    # Build summary
    parts = [f"{symbol} 單日{'上漲' if direction == 'up' else '下跌'} {abs(change_1d):.1f}%。{summary_prefix}。"]
    if market_ev:
        parts.append(market_ev["note"])
    if sector_ev:
        parts.append(sector_ev["note"])
    if tech_notes:
        parts.append("技術面：" + "；".join(tech_notes[:2]))
    parts.append("注意：同業資料為前一交易日 Aegis 日線，非即時比對。")
    summary = " ".join(parts)

    return {
        "triggered": True,
        "direction": direction,
        "change_1d": change_1d,
        "change_5d": change_5d,
        "classification": classification,
        "classification_label": classification_label,
        "confidence": confidence,
        "evidence": {
            "market": market_ev,
            "sector": sector_ev,
            "technical": tech_ev,
        },
        "summary": summary,
    }


def build_recommendation(
    quote: Quote,
    chart: list[dict[str, float]],
    news: list[dict[str, str]],
    fundamentals: AegisFundamentals | None = None,
    chart_source: str = "Yahoo Finance chart",
) -> dict[str, Any]:
    factors = empty_factor_state()
    signals: list[dict[str, Any]] = []
    risks: list[str] = []

    industry_group = fundamentals.industry_group if fundamentals else None
    if quote.sector or quote.industry or industry_group:
        industry_text = " / ".join(part for part in [quote.sector, quote.industry, industry_group] if part)
        add_factor_score(factors, signals, "quality", "產業面", "已取得產業分類", 2, f"Yahoo Finance 分類：{industry_text}。")
    else:
        risks.append("未取得產業分類，產業面需自行補充供應鏈、景氣循環與競爭格局。")

    change_1m = percent_change(chart, 21)
    change_3m = percent_change(chart, 63)
    ma_20 = moving_average(chart, 20)
    ma_60 = moving_average(chart, 60)
    ema_12 = ema_value(chart, 12)
    ema_26 = ema_value(chart, 26)
    macd = macd_metrics(chart)
    rsi_14 = rsi(chart, 14)
    kd = stochastic_kd(chart)
    vol = volatility(chart)
    atr_14 = atr(chart, 14)
    atr_percent = atr_14 / quote.price * 100 if atr_14 is not None and quote.price else None
    bollinger = bollinger_metrics(chart)
    hurst_60 = hurst_exponent(chart, 60)
    obv = obv_slope(chart, 20)
    vwap_20 = rolling_vwap(chart, 20)
    avg_volume_60 = average_volume(chart, 60)
    directional = directional_movement_metrics(chart, 14)
    adx_14 = directional.get("adx")
    plus_di = directional.get("plus_di")
    minus_di = directional.get("minus_di")
    rs_metrics = relative_strength_percentiles(quote.symbol, quote.asset_type, change_1m, change_3m)
    rs_1m = rs_metrics.get("rs_1m_percentile")
    rs_3m = rs_metrics.get("rs_3m_percentile")
    rs_peer_count = rs_metrics.get("rs_peer_count")
    vol_regime = volatility_regime_percentile(chart, 20, 120)
    if quote.avg_volume is None and avg_volume_60 is not None:
        quote.avg_volume = avg_volume_60
    if quote.volume is None and chart and chart[-1].get("volume") is not None:
        quote.volume = chart[-1].get("volume")
    if quote.pe is None and fundamentals and fundamentals.eps_ttm and fundamentals.eps_ttm > 0 and quote.price:
        quote.pe = quote.price / fundamentals.eps_ttm

    if ma_20 and ma_60 and quote.price:
        if quote.price > ma_20 > ma_60:
            add_factor_score(factors, signals, "trend", "趨勢", "均線排列偏多", 8, "現價高於 20 日均線，20 日均線高於 60 日均線。")
        elif quote.price < ma_20 < ma_60:
            add_factor_score(factors, signals, "trend", "趨勢", "均線排列偏空", -8, "現價低於 20 日均線，20 日均線低於 60 日均線。")

    if ema_12 is not None and ema_26 is not None:
        if ema_12 > ema_26:
            add_factor_score(factors, signals, "trend", "趨勢", "EMA 趨勢偏多", 5, f"EMA12 {ema_12:.2f} 高於 EMA26 {ema_26:.2f}。")
        elif ema_12 < ema_26:
            add_factor_score(factors, signals, "trend", "趨勢", "EMA 趨勢偏空", -5, f"EMA12 {ema_12:.2f} 低於 EMA26 {ema_26:.2f}。")

    macd_hist = macd.get("histogram")
    macd_slope = macd.get("histogram_slope")
    if macd_hist is not None and macd_slope is not None:
        if macd_hist > 0 and macd_slope > 0:
            add_factor_score(factors, signals, "trend", "趨勢", "MACD 柱狀圖擴張", 5, f"Histogram {macd_hist:.2f}，斜率 {macd_slope:.2f}。")
        elif macd_hist > 0 and macd_slope < 0:
            add_factor_score(factors, signals, "trend", "趨勢", "MACD 動能減速", -3, f"Histogram 仍為正但斜率 {macd_slope:.2f}。")
        elif macd_hist < 0 and macd_slope < 0:
            add_factor_score(factors, signals, "trend", "趨勢", "MACD 空方擴張", -5, f"Histogram {macd_hist:.2f}，斜率 {macd_slope:.2f}。")

    if change_1m is not None:
        if 2 <= change_1m <= 18:
            add_factor_score(factors, signals, "momentum", "動能", "近一月動能正向", 6, f"近 21 個交易日約 {change_1m:.1f}%。")
        elif change_1m < -8:
            add_factor_score(factors, signals, "momentum", "動能", "短線轉弱", -6, f"近 21 個交易日約 {change_1m:.1f}%。")
        elif change_1m > 25:
            add_factor_score(factors, signals, "momentum", "動能", "短線過熱", -4, f"近 21 個交易日約 {change_1m:.1f}%，追價風險升高。")

    if rsi_14 is not None:
        if 45 <= rsi_14 <= 65:
            add_factor_score(factors, signals, "momentum", "動能", "RSI 動能健康", 4, f"RSI14 約 {rsi_14:.1f}。")
        elif rsi_14 > 78:
            add_factor_score(factors, signals, "momentum", "動能", "RSI 過熱", -5, f"RSI14 約 {rsi_14:.1f}。")
        elif rsi_14 < 32:
            add_factor_score(factors, signals, "momentum", "動能", "RSI 弱勢/超賣", -4, f"RSI14 約 {rsi_14:.1f}。")

    stoch_spread = kd.get("spread")
    if stoch_spread is not None:
        if stoch_spread > 8:
            add_factor_score(factors, signals, "momentum", "動能", "KD 短線加速", 4, f"K-D spread 約 {stoch_spread:.1f}。")
        elif stoch_spread < -8:
            add_factor_score(factors, signals, "momentum", "動能", "KD 短線轉弱", -4, f"K-D spread 約 {stoch_spread:.1f}。")

    if isinstance(rs_3m, (int, float)):
        if rs_3m >= 75:
            add_factor_score(factors, signals, "momentum", "Quant", "Relative strength leadership", 5, f"3M relative strength percentile {rs_3m:.1f} across {rs_peer_count} peers.")
        elif rs_3m <= 25:
            add_factor_score(factors, signals, "momentum", "Quant", "Relative strength lagging", -5, f"3M relative strength percentile {rs_3m:.1f} across {rs_peer_count} peers.")
    if isinstance(rs_1m, (int, float)):
        if rs_1m >= 80:
            add_factor_score(factors, signals, "momentum", "Quant", "Short-term relative strength", 3, f"1M relative strength percentile {rs_1m:.1f} across {rs_peer_count} peers.")
        elif rs_1m <= 20:
            add_factor_score(factors, signals, "momentum", "Quant", "Short-term relative weakness", -3, f"1M relative strength percentile {rs_1m:.1f} across {rs_peer_count} peers.")

    effective_eps = quote.eps
    if effective_eps is None and fundamentals and fundamentals.latest_eps is not None:
        effective_eps = fundamentals.latest_eps

    needs_fundamentals = requires_equity_fundamentals(quote)
    if needs_fundamentals:
        if quote.pe is not None:
            if 0 < quote.pe <= 18:
                source_text = f"{fundamentals.source} EPS TTM 推算" if fundamentals and fundamentals.eps_ttm and quote.eps is None else "Yahoo Finance"
                add_factor_score(factors, signals, "value", "基本面", "本益比相對保守", 7, f"目前可得本益比約 {quote.pe:.1f}，來源：{source_text}。")
            elif quote.pe > 45:
                source_text = f"{fundamentals.source} EPS TTM 推算" if fundamentals and fundamentals.eps_ttm and quote.eps is None else "Yahoo Finance"
                add_factor_score(factors, signals, "value", "基本面", "估值偏高", -7, f"目前可得本益比約 {quote.pe:.1f}，來源：{source_text}，需用成長性驗證。")
        else:
            risks.append("Yahoo Finance 未回傳本益比，基本面判斷權重降低。")

        if effective_eps is not None and effective_eps > 0:
            source_text = fundamentals.source if fundamentals and quote.eps is None else "Yahoo Finance"
            add_factor_score(factors, signals, "quality", "基本面", "EPS 為正", 5, f"最新 EPS 約 {effective_eps:.2f}，來源：{source_text}。")
        elif effective_eps is not None and effective_eps <= 0:
            add_factor_score(factors, signals, "quality", "基本面", "EPS 未轉正", -6, f"最新 EPS 約 {effective_eps:.2f}。")

        if fundamentals and fundamentals.eps_qoq is not None:
            if fundamentals.eps_qoq > 0.1:
                add_factor_score(factors, signals, "quality", "基本面", "EPS QoQ 成長", 5, f"EPS QoQ 約 {fundamentals.eps_qoq:.1%}。")
            elif fundamentals.eps_qoq < -0.1:
                add_factor_score(factors, signals, "quality", "基本面", "EPS QoQ 衰退", -5, f"EPS QoQ 約 {fundamentals.eps_qoq:.1%}。")

        if fundamentals and fundamentals.revenue_yoy is not None:
            if fundamentals.revenue_yoy > 0.1:
                add_factor_score(factors, signals, "quality", "營收", "月營收 YoY 成長", 5, f"{fundamentals.revenue_yyyymm} 月營收 YoY 約 {fundamentals.revenue_yoy:.1%}。")
            elif fundamentals.revenue_yoy < -0.1:
                add_factor_score(factors, signals, "quality", "營收", "月營收 YoY 衰退", -5, f"{fundamentals.revenue_yyyymm} 月營收 YoY 約 {fundamentals.revenue_yoy:.1%}。")

        if fundamentals and fundamentals.revenue_mom is not None:
            if fundamentals.revenue_mom > 0.1:
                add_factor_score(factors, signals, "quality", "營收", "月營收 MoM 成長", 3, f"{fundamentals.revenue_yyyymm} 月營收 MoM 約 {fundamentals.revenue_mom:.1%}。")
            elif fundamentals.revenue_mom < -0.1:
                add_factor_score(factors, signals, "quality", "營收", "月營收 MoM 衰退", -3, f"{fundamentals.revenue_yyyymm} 月營收 MoM 約 {fundamentals.revenue_mom:.1%}。")

        if fundamentals and fundamentals.gross_margin is not None:
            stmt_tag = f"（{fundamentals.financial_stmt_date}）" if fundamentals.financial_stmt_date else ""
            if fundamentals.gross_margin >= 0.40:
                add_factor_score(factors, signals, "quality", "獲利能力", "高毛利率", 5, f"毛利率 {fundamentals.gross_margin:.1%}{stmt_tag}，屬高毛利業務。")
            elif fundamentals.gross_margin < 0.10:
                add_factor_score(factors, signals, "quality", "獲利能力", "低毛利率", -3, f"毛利率 {fundamentals.gross_margin:.1%}{stmt_tag}，毛利偏薄。")

        if fundamentals and fundamentals.operating_margin is not None:
            stmt_tag = f"（{fundamentals.financial_stmt_date}）" if fundamentals.financial_stmt_date else ""
            if fundamentals.operating_margin >= 0.15:
                add_factor_score(factors, signals, "quality", "獲利能力", "高營業利益率", 4, f"營業利益率 {fundamentals.operating_margin:.1%}{stmt_tag}。")
            elif fundamentals.operating_margin < 0:
                add_factor_score(factors, signals, "quality", "獲利能力", "營業虧損", -5, f"營業利益率 {fundamentals.operating_margin:.1%}{stmt_tag}，本業虧損。")
    elif is_fund_like_symbol(quote.symbol, quote.quote_type):
        risks.append("ETF/基金不使用單一公司 EPS/本益比判斷，應改看成分股、費用率、折溢價與資金流。")
    else:
        risks.append(f"{quote.asset_type}通常沒有傳統 EPS/本益比，建議搭配供需、利率、美元與監管變化判讀。")

    if quote.volume and quote.avg_volume and quote.avg_volume > 0:
        volume_ratio = quote.volume / quote.avg_volume
        if volume_ratio >= 1.8 and quote.change_percent and quote.change_percent > 0:
            add_factor_score(factors, signals, "liquidity", "籌碼/流動性", "放量上漲", 5, f"成交量約為三個月均量 {volume_ratio:.1f} 倍。")
        elif volume_ratio >= 1.8 and quote.change_percent and quote.change_percent < 0:
            add_factor_score(factors, signals, "liquidity", "籌碼/流動性", "放量下跌", -6, f"成交量約為三個月均量 {volume_ratio:.1f} 倍。")

    positive_news = sum(1 for item in news if item["sentiment"] == "positive")
    negative_news = sum(1 for item in news if item["sentiment"] == "negative")
    if positive_news > negative_news:
        add_factor_score(factors, signals, "sentiment", "即時新聞", "新聞語氣偏正面", 5, f"近 14 天新聞標題正向 {positive_news} 則、負向 {negative_news} 則。")
    elif negative_news > positive_news:
        add_factor_score(factors, signals, "sentiment", "即時新聞", "新聞語氣偏負面", -5, f"近 14 天新聞標題正向 {positive_news} 則、負向 {negative_news} 則。")

    if vol is not None:
        if vol >= 55:
            add_factor_score(factors, signals, "risk", "風險", "波動偏高", -7, f"近三個月年化波動約 {vol:.1f}%。")
            risks.append("波動度偏高，部位應小於一般股票或 ETF。")
        elif vol <= 22:
            add_factor_score(factors, signals, "risk", "風險", "波動相對可控", 3, f"近三個月年化波動約 {vol:.1f}%。")

    if vol_regime is not None:
        if vol_regime >= 85:
            add_factor_score(factors, signals, "risk", "Quant", "High volatility regime", -5, f"20D volatility is at the {vol_regime:.1f} percentile of recent history.")
            risks.append("Volatility regime is elevated versus its recent history.")
        elif vol_regime <= 35:
            add_factor_score(factors, signals, "risk", "Quant", "Calm volatility regime", 2, f"20D volatility is at the {vol_regime:.1f} percentile of recent history.")

    if atr_percent is not None:
        if atr_percent >= 5:
            add_factor_score(factors, signals, "risk", "風險", "ATR 停損距離偏大", -5, f"ATR14 約 {atr_14:.2f}，佔價格 {atr_percent:.1f}%。")
        elif atr_percent <= 2:
            add_factor_score(factors, signals, "risk", "風險", "ATR 風險距離可控", 3, f"ATR14 約 {atr_14:.2f}，佔價格 {atr_percent:.1f}%。")

    boll_width = bollinger.get("width")
    boll_z = bollinger.get("zscore")
    if boll_width is not None and boll_z is not None:
        if boll_width <= 8:
            add_factor_score(factors, signals, "risk", "波動", "布林通道擠壓", 3, f"通道寬度約 {boll_width:.1f}%，可能接近突破準備期。")
        if abs(boll_z) >= 2:
            add_factor_score(factors, signals, "risk", "波動", "價格偏離布林均值", -3, f"Bollinger Z-score 約 {boll_z:.2f}。")

    if hurst_60 is not None:
        if hurst_60 > 0.55:
            add_factor_score(factors, signals, "trend", "市場狀態", "Hurst 顯示趨勢延續", 3, f"Hurst60 約 {hurst_60:.2f}。")
        elif hurst_60 < 0.45:
            add_factor_score(factors, signals, "risk", "市場狀態", "Hurst 顯示反持續", 2, f"Hurst60 約 {hurst_60:.2f}，較適合均值回歸思維。")

    if change_3m is not None and change_3m < -20:
        risks.append("三個月跌幅較深，需確認不是基本面或產業趨勢惡化。")

    if obv is not None:
        if obv > 0 and quote.change_percent is not None and quote.change_percent >= 0:
            add_factor_score(factors, signals, "liquidity", "量價", "OBV 資金流向偏正", 4, f"近 20 日 OBV slope 約 {obv:.0f}。")
        elif obv < 0 and quote.change_percent is not None and quote.change_percent < 0:
            add_factor_score(factors, signals, "liquidity", "量價", "OBV 資金流向偏弱", -4, f"近 20 日 OBV slope 約 {obv:.0f}。")

    if vwap_20 is not None and quote.price:
        if quote.price > vwap_20:
            add_factor_score(factors, signals, "liquidity", "量價", "價格站上近似 VWAP", 3, f"20 日近似 VWAP 約 {vwap_20:.2f}。")
        elif quote.price < vwap_20:
            add_factor_score(factors, signals, "liquidity", "量價", "價格低於近似 VWAP", -3, f"20 日近似 VWAP 約 {vwap_20:.2f}。")

    if quote.asset_type == "台股" and not is_fund_like_symbol(quote.symbol, quote.quote_type):
        flow_score, flow_signals, flow_evidence = active_etf_flow_score(quote.symbol)
    else:
        flow_score, flow_signals, flow_evidence = None, [], {
            "change_count": 0,
            "source_status": "not_applicable",
            "source": "Taiwan active ETF flow",
        }
    if flow_score is not None:
        detail = flow_signals[0]["detail"] if flow_signals else "主動式 ETF 異動資料已納入。"
        set_factor_score(factors, signals, "active_etf_flow", flow_score, "主動ETF", "主動式 ETF 異動線索", detail)

    metrics = {
        "change_1m": change_1m,
        "change_3m": change_3m,
        "ma_20": ma_20,
        "ma_60": ma_60,
        "ema_12": ema_12,
        "ema_26": ema_26,
        "macd": macd.get("macd"),
        "macd_signal": macd.get("signal"),
        "macd_histogram": macd_hist,
        "macd_histogram_slope": macd_slope,
        "adx_14": adx_14,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "rsi_14": rsi_14,
        "stoch_k": kd.get("k"),
        "stoch_d": kd.get("d"),
        "stoch_spread": stoch_spread,
        "rs_1m_percentile": rs_1m,
        "rs_3m_percentile": rs_3m,
        "rs_peer_count": rs_peer_count,
        "atr_14": atr_14,
        "atr_percent": atr_percent,
        "bollinger_width": boll_width,
        "bollinger_zscore": boll_z,
        "hurst_60": hurst_60,
        "volatility_regime_percentile": vol_regime,
        "obv_slope": obv,
        "vwap_20": vwap_20,
        "avg_volume_60": avg_volume_60,
        "volume_ratio": quote.volume / quote.avg_volume if quote.volume and quote.avg_volume else None,
        "volatility": vol,
    }
    data_quality = build_factor_data_quality(quote, chart, news, metrics, flow_evidence, fundamentals, chart_source)
    coverage_values = [
        row["coverage"] for row in data_quality.values()
        if row.get("confidence") != "not_applicable"
    ]
    data_coverage = round(sum(coverage_values) / len(coverage_values), 4) if coverage_values else 0
    if data_coverage >= 0.75:
        data_confidence = "high"
    elif data_coverage >= 0.4:
        data_confidence = "medium"
    elif data_coverage > 0:
        data_confidence = "low"
    else:
        data_confidence = "none"

    effective_weights = applicable_factor_weights(data_quality)
    score = weighted_factor_score(factors, data_quality)
    financial_status = financial_status_from_quality(
        quote.asset_type,
        quote.symbol,
        quote.quote_type,
        data_quality,
        fundamentals,
    )
    gate = preliminary_grade(score, data_quality, quote.asset_type, quote.symbol, quote.quote_type, financial_status)
    data_policy = build_data_policy(quote, chart, news, fundamentals, chart_source, data_quality, flow_evidence)
    if score >= 72:
        action = "偏多觀察/分批布局"
        stance = "適合放入候選清單，但仍需分批與設定停損。"
    elif score >= 58:
        action = "中性偏多/等待回檔"
        stance = "資料面略偏正向，較適合等價格或新聞確認。"
    elif score >= 43:
        action = "中性觀望"
        stance = "多空訊號混合，暫不適合重倉。"
    else:
        action = "偏空/暫避"
        stance = "風險或弱勢訊號較多，除非有明確反轉依據。"

    # Market regime overlay: apply only to Taiwan stocks to avoid irrelevant 0050 proxy.
    if quote.asset_type == "台股":
        try:
            regime_data = get_market_regime("0050.TW")
            regime = regime_data.get("regime", "unknown")
        except Exception:
            regime = "unknown"
        if regime == "bear":
            if score >= 72:
                action = "偏多觀察（大盤偏弱，建議小部位）"
                stance = "個股技術面偏強，但大盤（0050）均線空頭排列，建議縮小部位或等待大盤止跌確認。"
            elif score >= 58:
                action = "中性觀望（大盤偏弱）"
                stance = "大盤空頭環境下個股偏多信號可靠度降低，暫以觀望為主。"
            signals.append({
                "category": "風險",
                "label": "大盤空頭",
                "impact": -5,
                "detail": f"0050.TW 均線空頭排列（price {regime_data.get('price'):.2f} < MA20 {regime_data.get('ma20'):.2f} < MA60 {regime_data.get('ma60'):.2f}），整體市場偏弱。"
                if all(regime_data.get(k) for k in ("price", "ma20", "ma60")) else "0050.TW 均線空頭排列，整體市場偏弱。",
            })

    if not signals:
        signals.append(
            {
                "category": "資料",
                "label": "可用資料不足",
                "impact": 0,
                "detail": "行情或新聞資料不足，無法建立高信心結論。",
            }
        )
        risks.append("資料不足時不應依賴單一建議。")

    return {
        "score": score,
        "action": action,
        "stance": stance,
        "signals": signals,
        "risks": risks,
        "factors": factor_payload(factors, data_quality),
        "factor_weights": FACTOR_WEIGHTS,
        "effective_factor_weights": effective_weights,
        "grade": gate["grade"],
        "raw_grade": gate["raw_grade"],
        "action_bucket": "Research Candidate" if gate["grade"] in {"A", "B"} else "Watch",
        "data_gate": gate,
        "financial_audit_status": financial_status,
        "data_policy": data_policy,
        "data_quality": {
            "coverage": data_coverage,
            "confidence": data_confidence,
            "factors": data_quality,
        },
        "fundamentals": fundamentals.to_metrics() if fundamentals else None,
        "metrics": metrics,
    }


def analyze_symbol(symbol: str, threshold_override: float | None = None) -> dict[str, Any]:
    normalized = symbol.strip().upper()
    # Quote (即時報價): Yahoo Finance regularMarketPrice 最即時，Aegis 只有日線收盤不是即時
    quote: Quote | None = None
    try:
        quotes = get_quote([normalized])
        quote = quotes.get(normalized)
    except requests.RequestException:
        pass
    if quote is None:
        quote = get_tpex_quote(normalized)      # 上櫃即時
    if quote is None:
        quote = get_aegis_quote(normalized)     # 最後備援（靜態日線收盤）
    if quote is None:
        raise ValueError(f"找不到代號：{symbol}")
    # Chart (歷史K線): Aegis 最完整，再 fallback Yahoo
    chart_source = "Yahoo Finance chart"
    chart = get_aegis_chart(normalized)
    if chart:
        chart_source = "AegisTrader snapshot price_daily"
    else:
        chart = get_tpex_chart(normalized)
        if chart:
            chart_source = "TPEx official quote"
        else:
            chart = get_chart(normalized)
    fundamentals = get_aegis_fundamentals(normalized)
    if fundamentals is None:
        fundamentals = get_yahoo_equity_fundamentals(quote)
    # Prefer Chinese name from Aegis when Yahoo returns symbol-as-name or empty
    if fundamentals and fundamentals.name and (not quote.name or quote.name == normalized):
        quote.name = fundamentals.name
    is_tw = quote.asset_type in {"台股", "台股/ETF"}
    news = get_taiwan_stock_news(normalized, quote.name) if is_tw else get_news(normalized, quote.name)
    recommendation = build_recommendation(quote, chart, news, fundamentals, chart_source)
    monitoring = build_market_alerts(quote, chart, news, threshold_override)
    rec_metrics = recommendation.get("metrics", {})
    dyn_threshold = monitoring["metrics"].get("movement_threshold", 3.0)
    movement_evidence = build_movement_evidence(
        normalized, quote,
        quote.change_percent, monitoring["metrics"].get("change_5d"),
        news, fundamentals, rec_metrics, dyn_threshold,
    )
    if movement_evidence:
        monitoring["movement_evidence"] = movement_evidence
    return {
        "symbol": quote.symbol,
        "name": quote.name,
        "asset_type": quote.asset_type,
        "quote_type": quote.quote_type,
        "sector": quote.sector,
        "industry": quote.industry,
        "price": quote.price,
        "currency": quote.currency,
        "change_percent": quote.change_percent,
        "market_cap": quote.market_cap,
        "pe": quote.pe,
        "eps": quote.eps,
        "dividend_yield": quote.dividend_yield,
        "volume": quote.volume,
        "avg_volume": quote.avg_volume,
        "chart": chart[-90:],
        "chart_source": chart_source,
        "news": news,
        "monitoring": monitoring,
        "recommendation": recommendation,
        "fundamentals": fundamentals.to_metrics() if fundamentals else None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def save_analysis_result(item: dict[str, Any]) -> int:
    captured_at = item["generated_at"]
    recommendation = item["recommendation"]
    monitoring = item["monitoring"]
    rec_metrics = recommendation["metrics"]
    monitor_metrics = monitoring["metrics"]
    symbol = item["symbol"]

    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO universe (
                symbol, name, asset_type, currency, sector, industry, enabled, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                name = excluded.name,
                asset_type = excluded.asset_type,
                currency = excluded.currency,
                sector = excluded.sector,
                industry = excluded.industry,
                updated_at = excluded.updated_at
            """,
            (
                symbol,
                item.get("name"),
                item.get("asset_type"),
                item.get("currency"),
                item.get("sector"),
                item.get("industry"),
                captured_at,
                captured_at,
            ),
        )
        cursor = conn.execute(
            """
            INSERT INTO price_snapshots (
                symbol, captured_at, price, currency, change_1d, change_5d, change_1m, change_3m,
                volume, avg_volume, ma_20, ma_60, volatility_annual, daily_volatility,
                movement_threshold, score, action, stance, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                captured_at,
                item.get("price"),
                item.get("currency"),
                item.get("change_percent"),
                monitor_metrics.get("change_5d"),
                rec_metrics.get("change_1m"),
                rec_metrics.get("change_3m"),
                item.get("volume"),
                item.get("avg_volume"),
                rec_metrics.get("ma_20"),
                rec_metrics.get("ma_60"),
                rec_metrics.get("volatility"),
                monitor_metrics.get("daily_volatility"),
                monitor_metrics.get("movement_threshold"),
                recommendation.get("score"),
                recommendation.get("action"),
                recommendation.get("stance"),
                json.dumps(item, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        snapshot_id = int(cursor.lastrowid)

        for signal in recommendation.get("signals", []):
            conn.execute(
                """
                INSERT INTO technical_signals (
                    snapshot_id, symbol, captured_at, category, label, impact, detail
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    symbol,
                    captured_at,
                    signal.get("category") or "未分類",
                    signal.get("label") or "",
                    int(signal.get("impact") or 0),
                    signal.get("detail"),
                ),
            )

        for row in item.get("news", []):
            conn.execute(
                """
                INSERT INTO news_items (
                    symbol, snapshot_id, title, link, source, published, sentiment, is_catalyst, captured_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    symbol,
                    snapshot_id,
                    row.get("title") or "",
                    row.get("link"),
                    row.get("source"),
                    row.get("published"),
                    row.get("sentiment"),
                    captured_at,
                ),
            )

        for row in monitoring.get("catalyst_news", []):
            conn.execute(
                """
                INSERT INTO news_items (
                    symbol, snapshot_id, title, link, source, published, sentiment, is_catalyst, captured_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    symbol,
                    snapshot_id,
                    row.get("title") or "",
                    row.get("link"),
                    row.get("source"),
                    row.get("published"),
                    row.get("sentiment"),
                    captured_at,
                ),
            )

        for alert in monitoring.get("alerts", []):
            conn.execute(
                """
                INSERT INTO event_alerts (
                    snapshot_id, symbol, captured_at, kind, severity, title, detail
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    symbol,
                    captured_at,
                    alert.get("kind") or "unknown",
                    alert.get("severity") or "info",
                    alert.get("title") or "",
                    alert.get("detail"),
                ),
            )

        for factor_key, factor_quality in recommendation.get("data_quality", {}).get("factors", {}).items():
            conn.execute(
                """
                INSERT INTO data_quality_snapshots (
                    snapshot_id, symbol, captured_at, factor, factor_label, coverage, confidence,
                    source_status, required_fields_json, used_fields_json, missing_fields_json, evidence_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    symbol,
                    captured_at,
                    factor_key,
                    factor_quality.get("factor_label") or FACTOR_LABELS.get(factor_key, factor_key),
                    factor_quality.get("coverage") or 0,
                    factor_quality.get("confidence") or "none",
                    factor_quality.get("source_status") or "unknown",
                    json.dumps(factor_quality.get("required_fields", []), ensure_ascii=False, separators=(",", ":")),
                    json.dumps(factor_quality.get("used_fields", {}), ensure_ascii=False, separators=(",", ":")),
                    json.dumps(factor_quality.get("missing_fields", []), ensure_ascii=False, separators=(",", ":")),
                    json.dumps(factor_quality.get("evidence", {}), ensure_ascii=False, separators=(",", ":")),
                ),
            )

        conn.execute(
            """
            INSERT INTO recommendations (
                snapshot_id, symbol, generated_at, score, action, stance, risks_json, metrics_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                symbol,
                captured_at,
                recommendation.get("score"),
                recommendation.get("action"),
                recommendation.get("stance"),
                json.dumps(recommendation.get("risks", []), ensure_ascii=False, separators=(",", ":")),
                json.dumps(recommendation.get("metrics", {}), ensure_ascii=False, separators=(",", ":")),
            ),
        )

    return snapshot_id


def save_analysis_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    saved = []
    for item in results:
        try:
            snapshot_id = save_analysis_result(item)
            item["snapshot_id"] = snapshot_id
            saved.append({"symbol": item["symbol"], "snapshot_id": snapshot_id})
        except sqlite3.Error as exc:
            saved.append({"symbol": item.get("symbol", ""), "error": str(exc)})
    return saved


def get_history(symbol: str, limit: int = 30) -> dict[str, Any]:
    normalized = symbol.strip().upper()
    limit = max(1, min(limit, 200))
    with db_connect() as conn:
        snapshots = conn.execute(
            """
            SELECT id, symbol, captured_at, price, currency, change_1d, change_5d,
                   change_1m, change_3m, volatility_annual, daily_volatility,
                   movement_threshold, score, action
            FROM price_snapshots
            WHERE symbol = ?
            ORDER BY captured_at DESC
            LIMIT ?
            """,
            (normalized, limit),
        ).fetchall()
        alerts = conn.execute(
            """
            SELECT captured_at, kind, severity, title, detail
            FROM event_alerts
            WHERE symbol = ?
            ORDER BY captured_at DESC
            LIMIT ?
            """,
            (normalized, limit),
        ).fetchall()
    return {
        "symbol": normalized,
        "snapshots": [dict(row) for row in snapshots],
        "alerts": [dict(row) for row in alerts],
    }


TREND_CONTEXT_MAX_LOOKBACK_DAYS = 14


def get_symbol_trend_context(symbol: str, limit: int = 10) -> dict[str, Any]:
    history = get_history(symbol, limit)
    snapshots = list(reversed(history["snapshots"]))
    if not snapshots:
        return {
            "snapshot_count": 0,
            "score_change": None,
            "price_change": None,
            "latest_score": None,
            "latest_price": None,
        }
    latest = snapshots[-1]
    # "last N snapshots" can span months when a symbol's scan cadence was sparse
    # (e.g. during a scheduler outage), so restrict the comparison baseline to
    # snapshots within a recent lookback window rather than whichever snapshot
    # happens to be oldest among the last `limit` rows.
    first = latest
    latest_captured_at = parse_utc_timestamp(latest.get("captured_at"))
    cutoff = latest_captured_at - timedelta(days=TREND_CONTEXT_MAX_LOOKBACK_DAYS) if latest_captured_at else None
    for snap in snapshots:
        captured_at = parse_utc_timestamp(snap.get("captured_at"))
        if cutoff is not None and captured_at is not None and captured_at < cutoff:
            continue
        first = snap
        break
    first_score = safe_float(first.get("score"))
    latest_score = safe_float(latest.get("score"))
    first_price = safe_float(first.get("price"))
    latest_price = safe_float(latest.get("price"))
    score_change = None
    if first_score is not None and latest_score is not None:
        score_change = latest_score - first_score
    price_change = None
    if first_price and latest_price:
        price_change = (latest_price / first_price - 1) * 100
    return {
        "snapshot_count": len(snapshots),
        "score_change": score_change,
        "price_change": price_change,
        "latest_score": latest_score,
        "latest_price": latest_price,
        "first_captured_at": first.get("captured_at"),
        "latest_captured_at": latest.get("captured_at"),
    }


def get_recent_analysis_result(symbol: str, max_age_minutes: int) -> dict[str, Any] | None:
    if max_age_minutes <= 0:
        return None
    normalized = symbol.strip().upper()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT id, captured_at, raw_json
            FROM price_snapshots
            WHERE symbol = ?
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            (normalized,),
        ).fetchone()
    if not row:
        return None
    captured_at = parse_utc_timestamp(row["captured_at"])
    if captured_at is None or captured_at < cutoff:
        return None
    try:
        item = json.loads(row["raw_json"])
    except json.JSONDecodeError:
        return None
    item["snapshot_id"] = row["id"]
    item["cache"] = {
        "hit": True,
        "captured_at": row["captured_at"],
        "max_age_minutes": max_age_minutes,
    }
    return item


def get_universe_count(asset_type: str | None = None) -> int:
    with db_connect() as conn:
        if asset_type:
            row = conn.execute(
                "SELECT COUNT(*) FROM universe WHERE enabled = 1 AND asset_type = ?",
                (asset_type,),
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) FROM universe WHERE enabled = 1").fetchone()
    return int(row[0] if row else 0)


def get_scoped_universe_count(scope: str, asset_type: str | None = None) -> int:
    params: list[Any] = [scope]
    asset_filter = ""
    if asset_type:
        asset_filter = "AND u.asset_type = ?"
        params.append(asset_type)
    with db_connect() as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM universe_membership um
            JOIN universe u ON u.symbol = um.symbol
            WHERE um.scope = ?
              AND u.enabled = 1
              {asset_filter}
            """,
            params,
        ).fetchone()
    return int(row[0] if row else 0)


def get_universe(asset_type: str | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    with db_connect() as conn:
        if asset_type:
            rows = conn.execute(
                """
                SELECT symbol, name, asset_type, currency, sector, industry, enabled
                FROM universe
                WHERE enabled = 1 AND asset_type = ?
                ORDER BY symbol
                LIMIT ? OFFSET ?
                """,
                (asset_type, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT symbol, name, asset_type, currency, sector, industry, enabled
                FROM universe
                WHERE enabled = 1
                ORDER BY asset_type, symbol
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
    return [dict(row) for row in rows]


def get_scoped_universe(
    scope: str,
    asset_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    params: list[Any] = [scope]
    asset_filter = ""
    if asset_type:
        asset_filter = "AND u.asset_type = ?"
        params.append(asset_type)
    params.extend([limit, offset])
    with db_connect() as conn:
        rows = conn.execute(
            f"""
            SELECT u.symbol, u.name, u.asset_type, u.currency, u.sector, u.industry, u.enabled,
                   um.market, um.instrument_type
            FROM universe_membership um
            JOIN universe u ON u.symbol = um.symbol
            WHERE um.scope = ?
              AND u.enabled = 1
              {asset_filter}
            ORDER BY
                CASE um.market WHEN 'twse' THEN 1 WHEN 'tpex' THEN 2 WHEN 'emerging' THEN 3 ELSE 9 END,
                CASE um.instrument_type WHEN '股票' THEN 1 WHEN 'ETF' THEN 2 ELSE 9 END,
                u.symbol
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def get_priority_universe(asset_type: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    limit = max(0, min(limit, 100))
    if limit <= 0:
        return []
    asset_filter = "AND u.asset_type = ?" if asset_type else ""
    params: list[Any] = []
    if asset_type:
        params.append(asset_type)
    params.append(limit)
    with db_connect() as conn:
        rows = conn.execute(
            f"""
            WITH signals AS (
                SELECT symbol, MAX(captured_at) AS last_seen FROM price_snapshots GROUP BY symbol
                UNION ALL
                SELECT symbol, MAX(created_at) AS last_seen FROM opportunities GROUP BY symbol
            ),
            ranked AS (
                SELECT symbol, MAX(last_seen) AS last_seen FROM signals GROUP BY symbol
            )
            SELECT u.symbol, u.name, u.asset_type, u.currency, u.sector, u.industry, u.enabled
            FROM universe u
            JOIN ranked r ON r.symbol = u.symbol
            WHERE u.enabled = 1 {asset_filter}
            ORDER BY r.last_seen DESC, u.symbol
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def get_priority_scoped_universe(
    scope: str,
    asset_type: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    limit = max(0, min(limit, 100))
    if limit <= 0:
        return []
    params: list[Any] = [scope]
    asset_filter = ""
    if asset_type:
        asset_filter = "AND u.asset_type = ?"
        params.append(asset_type)
    params.append(limit)
    with db_connect() as conn:
        rows = conn.execute(
            f"""
            WITH signals AS (
                SELECT symbol, MAX(captured_at) AS last_seen FROM price_snapshots GROUP BY symbol
                UNION ALL
                SELECT symbol, MAX(created_at) AS last_seen FROM opportunities GROUP BY symbol
            ),
            ranked AS (
                SELECT symbol, MAX(last_seen) AS last_seen FROM signals GROUP BY symbol
            )
            SELECT u.symbol, u.name, u.asset_type, u.currency, u.sector, u.industry, u.enabled,
                   um.market, um.instrument_type
            FROM universe_membership um
            JOIN universe u ON u.symbol = um.symbol
            JOIN ranked r ON r.symbol = u.symbol
            WHERE um.scope = ?
              AND u.enabled = 1
              {asset_filter}
            ORDER BY r.last_seen DESC, u.symbol
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def quant_metrics_from_outcome_payload(payload: dict[str, Any], snapshot_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    metrics = payload.get("quant_metrics")
    if isinstance(metrics, dict):
        return metrics
    metrics = payload.get("metrics")
    if isinstance(metrics, dict):
        return metrics
    if isinstance(snapshot_payload, dict):
        recommendation = snapshot_payload.get("recommendation") or {}
        metrics = recommendation.get("metrics")
        if isinstance(metrics, dict):
            quant_metrics = dict(metrics)
            chart = snapshot_payload.get("chart") or []
            if isinstance(chart, list):
                quant_metrics.update(derive_quant_metrics_from_chart(chart, quant_metrics))
            return quant_metrics
    return {}


def derive_quant_metrics_from_chart(chart: list[dict[str, Any]], existing_metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    if not chart:
        return {}
    clean_chart: list[dict[str, float]] = []
    for point in chart:
        if not isinstance(point, dict):
            continue
        close = safe_float(point.get("close"))
        if close is None:
            continue
        clean_chart.append(
            {
                "close": close,
                "high": safe_float(point.get("high")) or close,
                "low": safe_float(point.get("low")) or close,
                "volume": safe_float(point.get("volume")),
            }
        )
    if not clean_chart:
        return {}
    derived = dict(existing_metrics or {})
    if derived.get("adx_14") is None or derived.get("plus_di") is None or derived.get("minus_di") is None:
        directional = directional_movement_metrics(clean_chart, 14)
        derived["adx_14"] = directional.get("adx")
        derived["plus_di"] = directional.get("plus_di")
        derived["minus_di"] = directional.get("minus_di")
    if derived.get("volatility_regime_percentile") is None:
        derived["volatility_regime_percentile"] = volatility_regime_percentile(clean_chart, 20, 120)
    if derived.get("change_1m") is None:
        derived["change_1m"] = percent_change(clean_chart, 21)
    if derived.get("change_3m") is None:
        derived["change_3m"] = percent_change(clean_chart, 63)
    return derived


def historical_relative_strength_percentiles(
    symbol: str,
    asset_type: str | None,
    captured_at: str | None,
    change_1m: float | None,
    change_3m: float | None,
) -> dict[str, float | int | None]:
    if not asset_type or not captured_at:
        return {"rs_1m_percentile": None, "rs_3m_percentile": None, "rs_peer_count": 0}
    rows: list[sqlite3.Row] = []
    try:
        with db_connect() as conn:
            rows = conn.execute(
                """
                WITH latest AS (
                    SELECT ps.symbol, MAX(ps.captured_at) AS captured_at
                    FROM price_snapshots ps
                    JOIN universe u ON u.symbol = ps.symbol
                    WHERE u.enabled = 1
                      AND u.asset_type = ?
                      AND ps.captured_at <= ?
                    GROUP BY ps.symbol
                )
                SELECT ps.symbol, ps.change_1m, ps.change_3m
                FROM price_snapshots ps
                JOIN latest l ON l.symbol = ps.symbol AND l.captured_at = ps.captured_at
                """,
                (asset_type, captured_at),
            ).fetchall()
    except sqlite3.Error:
        rows = []

    change_1m_values: list[float] = []
    change_3m_values: list[float] = []
    seen = set()
    for row in rows:
        row_symbol = row["symbol"]
        seen.add(row_symbol)
        row_1m = change_1m if row_symbol == symbol and change_1m is not None else safe_float(row["change_1m"])
        row_3m = change_3m if row_symbol == symbol and change_3m is not None else safe_float(row["change_3m"])
        if row_1m is not None:
            change_1m_values.append(row_1m)
        if row_3m is not None:
            change_3m_values.append(row_3m)
    if symbol not in seen:
        if change_1m is not None:
            change_1m_values.append(change_1m)
        if change_3m is not None:
            change_3m_values.append(change_3m)
    return {
        "rs_1m_percentile": percentile_rank(change_1m_values, change_1m),
        "rs_3m_percentile": percentile_rank(change_3m_values, change_3m),
        "rs_peer_count": max(len(change_1m_values), len(change_3m_values)),
    }


def quant_metric_bucket_labels(metrics: dict[str, Any]) -> list[tuple[str, str]]:
    labels: list[tuple[str, str]] = []
    adx_14 = safe_float(metrics.get("adx_14"))
    plus_di = safe_float(metrics.get("plus_di"))
    minus_di = safe_float(metrics.get("minus_di"))
    if adx_14 is not None:
        if adx_14 >= 25 and plus_di is not None and minus_di is not None and plus_di > minus_di:
            labels.append(("adx_direction", "strong_bullish"))
        elif adx_14 >= 25 and plus_di is not None and minus_di is not None and minus_di > plus_di:
            labels.append(("adx_direction", "strong_bearish"))
        elif adx_14 < 15:
            labels.append(("adx_direction", "weak_trend"))
        else:
            labels.append(("adx_direction", "neutral_trend"))
        if adx_14 >= 35:
            labels.append(("adx_strength", "adx_35_plus"))
        elif adx_14 >= 25:
            labels.append(("adx_strength", "adx_25_35"))
        elif adx_14 >= 15:
            labels.append(("adx_strength", "adx_15_25"))
        else:
            labels.append(("adx_strength", "adx_below_15"))

    rs_3m = safe_float(metrics.get("rs_3m_percentile"))
    if rs_3m is not None:
        if rs_3m >= 75:
            labels.append(("rs_3m", "top_quartile"))
        elif rs_3m <= 25:
            labels.append(("rs_3m", "bottom_quartile"))
        else:
            labels.append(("rs_3m", "middle_50"))

    rs_1m = safe_float(metrics.get("rs_1m_percentile"))
    if rs_1m is not None:
        if rs_1m >= 80:
            labels.append(("rs_1m", "top_20"))
        elif rs_1m <= 20:
            labels.append(("rs_1m", "bottom_20"))
        else:
            labels.append(("rs_1m", "middle_60"))

    vol_regime = safe_float(metrics.get("volatility_regime_percentile"))
    if vol_regime is not None:
        if vol_regime >= 85:
            labels.append(("volatility_regime", "high_85_plus"))
        elif vol_regime <= 35:
            labels.append(("volatility_regime", "calm_35_minus"))
        else:
            labels.append(("volatility_regime", "normal_35_85"))
    return labels


def get_quant_outcome_attribution(horizon_days: int = 5, min_count: int = 3) -> dict[str, Any]:
    update_result = update_recommendation_outcomes(limit=5000)
    horizon_days = min(365, max(1, int(horizon_days)))
    min_count = min(100, max(1, int(min_count)))
    grouped: dict[tuple[str, str], list[float]] = {}
    parsed_count = 0
    metrics_count = 0
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT ro.*, ps.raw_json AS snapshot_raw_json
            FROM recommendation_outcomes ro
            LEFT JOIN price_snapshots ps ON ps.id = ro.snapshot_id
            WHERE ro.status = 'ready'
              AND ro.horizon_days = ?
              AND ro.return_percent IS NOT NULL
            """,
            (horizon_days,),
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["raw_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        try:
            snapshot_payload = json.loads(row["snapshot_raw_json"] or "{}") if row["snapshot_raw_json"] else {}
        except json.JSONDecodeError:
            snapshot_payload = {}
        parsed_count += 1
        return_percent = safe_float(row["return_percent"])
        if return_percent is None:
            continue
        metrics = quant_metrics_from_outcome_payload(payload, snapshot_payload)
        if metrics and (metrics.get("rs_1m_percentile") is None or metrics.get("rs_3m_percentile") is None):
            rs_metrics = historical_relative_strength_percentiles(
                str(row["symbol"]),
                payload.get("asset_type") or snapshot_payload.get("asset_type"),
                snapshot_payload.get("generated_at") or row["start_at"],
                safe_float(metrics.get("change_1m")),
                safe_float(metrics.get("change_3m")),
            )
            for key, value in rs_metrics.items():
                if metrics.get(key) is None:
                    metrics[key] = value
        labels = quant_metric_bucket_labels(metrics)
        if labels:
            metrics_count += 1
        for indicator, bucket in labels:
            grouped.setdefault((indicator, bucket), []).append(return_percent)

    groups = [
        summarize_outcome_group(indicator, bucket, values)
        for (indicator, bucket), values in grouped.items()
        if len(values) >= min_count
    ]
    groups.sort(key=lambda item: (item["group_type"], item["label"]))
    by_indicator: dict[str, list[dict[str, Any]]] = {}
    for group in groups:
        by_indicator.setdefault(group["group_type"], []).append(group)
    for indicator_groups in by_indicator.values():
        indicator_groups.sort(key=lambda item: item["avg_return_percent"], reverse=True)
    return {
        "horizon_days": horizon_days,
        "min_count": min_count,
        "ready_count": len(rows),
        "parsed_count": parsed_count,
        "metrics_count": metrics_count,
        "update": update_result,
        "indicators": by_indicator,
        "groups": groups,
    }


def get_recommendation_outcome_attribution(horizon_days: int = 5, min_count: int = 5) -> dict[str, Any]:
    update_result = update_recommendation_outcomes(limit=5000)
    horizon_days = min(365, max(1, int(horizon_days)))
    min_count = min(100, max(1, int(min_count)))
    grouped: dict[tuple[str, str], list[float]] = {}
    parsed_count = 0
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM recommendation_outcomes
            WHERE status = 'ready'
              AND horizon_days = ?
              AND return_percent IS NOT NULL
            """,
            (horizon_days,),
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["raw_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        parsed_count += 1
        return_percent = safe_float(row["return_percent"])
        if return_percent is None:
            continue
        for group_type, label in outcome_group_labels(row, payload):
            grouped.setdefault((group_type, label), []).append(return_percent)

    groups = [
        summarize_outcome_group(group_type, label, values)
        for (group_type, label), values in grouped.items()
        if len(values) >= min_count
    ]
    groups.sort(key=lambda item: (item["avg_return_percent"], item["win_rate"] or 0, item["count"]))
    return {
        "horizon_days": horizon_days,
        "min_count": min_count,
        "ready_count": len(rows),
        "parsed_count": parsed_count,
        "update": update_result,
        "worst": groups[:5],
        "best": list(reversed(groups[-5:])),
        "groups": groups,
    }


def latest_scope_snapshot_items(scope: str, limit: int = 5000) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 10000))
    with db_connect() as conn:
        rows = conn.execute(
            """
            WITH latest AS (
                SELECT ps.*
                FROM price_snapshots ps
                JOIN (
                    SELECT symbol, MAX(captured_at) AS captured_at
                    FROM price_snapshots
                    GROUP BY symbol
                ) pick
                  ON pick.symbol = ps.symbol AND pick.captured_at = ps.captured_at
            )
            SELECT latest.id AS snapshot_id, latest.symbol, latest.captured_at, latest.raw_json,
                   um.market, um.instrument_type
            FROM universe_membership um
            JOIN latest ON latest.symbol = um.symbol
            WHERE um.scope = ?
            ORDER BY latest.captured_at DESC
            LIMIT ?
            """,
            (scope, limit),
        ).fetchall()
    items = []
    for row in rows:
        try:
            item = json.loads(row["raw_json"] or "{}")
        except json.JSONDecodeError:
            continue
        item["snapshot_id"] = row["snapshot_id"]
        item["market"] = row["market"]
        item["instrument_type"] = row["instrument_type"]
        item["captured_at"] = row["captured_at"]
        items.append(item)
    return items


def get_tw_full_market_opportunity_candidates(limit: int = 50, min_priority: int = 25) -> dict[str, Any]:
    limit = max(1, min(limit, 300))
    min_priority = max(0, min(min_priority, 100))
    status = get_tw_full_market_status()
    opportunities = []
    skipped = {"below_priority": 0, "parse_error": 0}
    for item in latest_scope_snapshot_items(TW_FULL_MARKET_SCOPE):
        try:
            opportunity = build_opportunity(item)
        except Exception:
            skipped["parse_error"] += 1
            continue
        if opportunity["priority"] < min_priority:
            skipped["below_priority"] += 1
            continue
        opportunity["market"] = item.get("market")
        opportunity["instrument_type"] = item.get("instrument_type")
        opportunity["captured_at"] = item.get("captured_at") or item.get("generated_at")
        opportunity["chart_source"] = item.get("chart_source")
        data_quality = (item.get("recommendation") or {}).get("data_quality") or {}
        opportunity["data_confidence"] = data_quality.get("confidence")
        opportunity["data_coverage"] = data_quality.get("coverage")
        opportunities.append(opportunity)
    opportunities.sort(
        key=lambda row: (
            GRADE_RANK.get(row.get("grade"), 0),
            row.get("priority") or 0,
            row.get("score") or 0,
            row.get("data_coverage") or 0,
        ),
        reverse=True,
    )
    selected = opportunities[:limit]
    by_grade: dict[str, int] = {}
    by_market: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for row in opportunities:
        by_grade[row.get("grade") or "unknown"] = by_grade.get(row.get("grade") or "unknown", 0) + 1
        by_market[row.get("market") or "unknown"] = by_market.get(row.get("market") or "unknown", 0) + 1
        by_category[row.get("category") or "unknown"] = by_category.get(row.get("category") or "unknown", 0) + 1
    return {
        "generated_at": utc_now(),
        "scope": TW_FULL_MARKET_SCOPE,
        "definition": TW_FULL_MARKET_POLICY,
        "status": status,
        "min_priority": min_priority,
        "available_candidate_count": len(opportunities),
        "returned_count": len(selected),
        "skipped": skipped,
        "summary": {
            "by_grade": [{"grade": key, "count": value} for key, value in sorted(by_grade.items())],
            "by_market": [{"market": key, "count": value} for key, value in sorted(by_market.items())],
            "by_category": [{"category": key, "count": value} for key, value in sorted(by_category.items())],
        },
        "opportunities": selected,
        "note": "Candidates are research leads, not trade instructions. One-point TPEx quote snapshots carry lower technical-history confidence.",
    }


def export_tw_full_market_opportunity_report(payload: dict[str, Any]) -> dict[str, str]:
    ensure_export_dirs()
    created_at = utc_now()
    slug = f"tw_full_market_opportunities_{timestamp_slug(created_at)}"
    md_path = EXPORT_DIR / "reports" / f"{slug}.md"
    latest_path = EXPORT_DIR / "reports" / "latest_tw_full_market_opportunities.md"
    status = payload.get("status") or {}
    lines = [
        "# Taiwan Full-Market Opportunity Candidates",
        "",
        f"- generated_at: `{created_at}`",
        f"- scope: `{payload.get('scope')}`",
        f"- universe_count: `{status.get('universe_count')}`",
        f"- snapshot_symbol_count: `{status.get('snapshot_symbol_count')}`",
        f"- no_quote_count: `{status.get('no_quote_count')}`",
        f"- snapshot_coverage_percent: `{status.get('snapshot_coverage_percent')}`",
        f"- min_priority: `{payload.get('min_priority')}`",
        f"- available_candidate_count: `{payload.get('available_candidate_count')}`",
        f"- returned_count: `{payload.get('returned_count')}`",
        "",
        "## Candidates",
        "",
    ]
    for index, item in enumerate(payload.get("opportunities") or [], 1):
        lines.extend(
            [
                f"### {index}. {item.get('symbol')} - {item.get('name') or ''}",
                "",
                f"- market: `{item.get('market')}`",
                f"- grade: `{item.get('grade')}`",
                f"- priority: `{item.get('priority')}`",
                f"- score: `{item.get('score')}`",
                f"- category: `{item.get('category')}`",
                f"- price: `{item.get('price')}`",
                f"- change_1d: `{item.get('change_1d')}`",
                f"- change_5d: `{item.get('change_5d')}`",
                f"- data_confidence: `{item.get('data_confidence')}`",
                f"- chart_source: `{item.get('chart_source')}`",
                f"- snapshot_id: `{item.get('snapshot_id')}`",
                "",
                f"Thesis: {item.get('thesis')}",
                "",
            ]
        )
    lines.extend(
        [
            "## Note",
            "",
            "These are research leads, not trade instructions. Validate liquidity, fundamentals, catalyst, and risk before any decision.",
            "",
        ]
    )
    content = "\n".join(lines).rstrip() + "\n"
    write_text_file(md_path, content)
    write_text_file(latest_path, content)
    return {"markdown": str(md_path), "latest_markdown": str(latest_path)}


def tw_full_market_strict_candidate_row(item: dict[str, Any]) -> dict[str, Any] | None:
    rec = item.get("recommendation") or {}
    data_quality = rec.get("data_quality") or {}
    metrics = rec.get("metrics") or {}
    monitoring = item.get("monitoring") or {}
    monitoring_metrics = monitoring.get("metrics") or {}
    if item.get("chart_source") == "TPEx official quote":
        return None
    if data_quality.get("confidence") != "high" or safe_float(data_quality.get("coverage")) is None:
        return None
    if (safe_float(data_quality.get("coverage")) or 0) < 0.75:
        return None
    volume = safe_float(item.get("volume"))
    if volume is None or volume < 500_000:
        return None
    score = safe_float(rec.get("score")) or 0
    if score < 58:
        return None
    rs_3m = safe_float(metrics.get("rs_3m_percentile"))
    if rs_3m is None or rs_3m < 70:
        return None
    plus_di = safe_float(metrics.get("plus_di"))
    minus_di = safe_float(metrics.get("minus_di"))
    if plus_di is not None and minus_di is not None and plus_di <= minus_di:
        return None
    change_1d = safe_float(item.get("change_percent"))
    spike_risk = change_1d is not None and abs(change_1d) >= 8
    financial_status = rec.get("financial_audit_status")
    tier = "core_watch" if financial_status in {"pass", "not_applicable"} and not spike_risk else "research_watch"
    if financial_status == "block":
        return None
    opportunity = build_opportunity(item)
    return {
        **opportunity,
        "tier": tier,
        "market": item.get("market"),
        "instrument_type": item.get("instrument_type"),
        "captured_at": item.get("captured_at") or item.get("generated_at"),
        "chart_source": item.get("chart_source"),
        "data_confidence": data_quality.get("confidence"),
        "data_coverage": data_quality.get("coverage"),
        "financial_audit_status": financial_status,
        "volume": volume,
        "avg_volume": item.get("avg_volume") or metrics.get("avg_volume_60"),
        "change_1d": change_1d,
        "change_5d": safe_float(monitoring_metrics.get("change_5d")),
        "adx_14": safe_float(metrics.get("adx_14")),
        "plus_di": plus_di,
        "minus_di": minus_di,
        "rs_1m_percentile": safe_float(metrics.get("rs_1m_percentile")),
        "rs_3m_percentile": rs_3m,
        "volatility_regime_percentile": safe_float(metrics.get("volatility_regime_percentile")),
        "spike_risk": spike_risk,
        **active_etf_flow_summary(data_quality),
    }


def active_etf_flow_summary(data_quality: dict[str, Any]) -> dict[str, Any]:
    evidence = (data_quality.get("factors") or {}).get("active_etf_flow", {}).get("evidence") or {}
    change_count = evidence.get("change_count") or 0
    if not change_count:
        return {
            "active_etf_change_count": 0,
            "active_etf_avg_score": None,
            "active_etf_note": None,
        }
    latest_symbol = evidence.get("latest_etf_symbol")
    latest_date = evidence.get("latest_trade_date")
    latest_type = evidence.get("latest_change_type")
    latest_label = active_etf_change_label(latest_type) if latest_type else None
    note = f"近期 {change_count} 筆異動"
    if latest_symbol and latest_date and latest_label:
        note += f"，最新 {latest_symbol} {latest_date} {latest_label}"
    return {
        "active_etf_change_count": change_count,
        "active_etf_avg_score": safe_float(evidence.get("avg_score")),
        "active_etf_note": note,
    }


def get_fundamental_momentum_scan(
    min_revenue_yi: float = 1.0,
    limit: int = 100,
    sort_by: str = "rev_accel",
) -> dict[str, Any]:
    """
    Scan TW universe for fundamental momentum signals:
      - Revenue YoY acceleration (last 3 filed months)
      - Revenue MoM trend
      - EPS QoQ trend (last 4 quarters)
      - Gross margin trend (quarterly, YoY expansion)
    All data from Aegis snapshot — no Yahoo calls.
    """
    min_rev = min_revenue_yi * 1e8
    conn = aegis_connect()
    if conn is None:
        return {"error": "Aegis snapshot unavailable", "rows": []}

    try:
        # ── 1. stock master ──────────────────────────────────────────────
        masters = {
            r["stock_id"]: {"name": r["name"], "market": r["market"], "industry": r["industry_group"]}
            for r in conn.execute("SELECT stock_id, name, market, industry_group FROM stock_master").fetchall()
        }

        # ── 2. revenue: last 14 filed months per stock ───────────────────
        rev_rows = conn.execute(
            "SELECT stock_id, yyyymm, revenue FROM revenue_monthly_raw WHERE revenue > 0 ORDER BY stock_id, yyyymm DESC"
        ).fetchall()
        rev_by_stock: dict[str, list[tuple[str, float]]] = {}
        for r in rev_rows:
            sid = r["stock_id"]
            if sid not in rev_by_stock:
                rev_by_stock[sid] = []
            if len(rev_by_stock[sid]) < 14:
                rev_by_stock[sid].append((r["yyyymm"], float(r["revenue"])))

        # ── 3. EPS: last 8 quarters per stock ────────────────────────────
        eps_rows = conn.execute(
            "SELECT stock_id, year, quarter, eps FROM eps_quarterly_raw ORDER BY stock_id, year DESC, quarter DESC"
        ).fetchall()
        eps_by_stock: dict[str, list[tuple[int, int, float | None]]] = {}
        for r in eps_rows:
            sid = r["stock_id"]
            if sid not in eps_by_stock:
                eps_by_stock[sid] = []
            if len(eps_by_stock[sid]) < 8:
                val = safe_float(r["eps"])
                eps_by_stock[sid].append((int(r["year"]), int(r["quarter"]), val))

        # ── 4. Gross margin from financial statements cache ───────────────
        cache_rows = conn.execute(
            "SELECT params_json, payload_json FROM api_cache WHERE dataset='TaiwanStockFinancialStatements'"
        ).fetchall()
        gm_by_stock: dict[str, list[tuple[str, float]]] = {}
        for crow in cache_rows:
            m = re.search(r'"data_id":\s*"(\d+)"', crow["params_json"] or "")
            if not m:
                continue
            sid = m.group(1)
            try:
                data = json.loads(crow["payload_json"] or "{}").get("data", [])
            except Exception:
                continue
            by_date: dict[str, dict[str, float]] = {}
            for item in data:
                t = item.get("type", "")
                if t not in ("GrossProfit", "Revenue"):
                    continue
                d = item.get("date", "")
                v = safe_float(item.get("value"))
                if d and v is not None:
                    by_date.setdefault(d, {})[t] = v
            gm_series = []
            for dt in sorted(by_date.keys()):
                gp = by_date[dt].get("GrossProfit")
                rev = by_date[dt].get("Revenue")
                if gp is not None and rev and rev > 0:
                    gm_series.append((dt, round(gp / rev, 4)))
            if gm_series:
                gm_by_stock[sid] = gm_series[-8:]

    finally:
        conn.close()

    # ── Helper: yyyymm arithmetic ─────────────────────────────────────────
    def yyyymm_minus_12(ym: str) -> str:
        y, mo = int(ym[:4]), int(ym[4:])
        y -= 1
        return f"{y}{mo:02d}"

    def yyyymm_prev(ym: str) -> str:
        y, mo = int(ym[:4]), int(ym[4:])
        mo -= 1
        if mo == 0:
            mo = 12
            y -= 1
        return f"{y}{mo:02d}"

    # ── Universe from live DB (TW stocks only) ────────────────────────────
    try:
        lconn = db_connect()
        tw_symbols = {
            r[0] for r in lconn.execute("SELECT symbol FROM universe WHERE symbol LIKE '%.TW'").fetchall()
        }
        lconn.close()
    except Exception:
        tw_symbols = set()

    def to_sid(sym: str) -> str | None:
        m = re.match(r"^(\d+)", sym)
        return m.group(1) if m else None

    universe_sids = {to_sid(s) for s in tw_symbols if to_sid(s)}

    # ── Per-stock computation ─────────────────────────────────────────────
    rows_out = []
    for sid in universe_sids:
        rev_series = rev_by_stock.get(sid, [])
        if not rev_series:
            continue
        latest_rev = rev_series[0][1] if rev_series else 0
        if latest_rev < min_rev:
            continue

        rev_map = {ym: v for ym, v in rev_series}

        # Revenue YoY for last 3 available months
        yoy_vals: list[float | None] = []
        yoy_labels: list[str] = []
        mom_vals: list[float | None] = []
        for i in range(min(3, len(rev_series))):
            ym, rv = rev_series[i]
            yoy_ym = yyyymm_minus_12(ym)
            rv_yoy = rev_map.get(yoy_ym)
            yoy = (rv - rv_yoy) / abs(rv_yoy) if rv_yoy and rv_yoy != 0 else None
            yoy_vals.append(yoy)
            yoy_labels.append(ym)
            prev_ym = yyyymm_prev(ym)
            rv_prev = rev_map.get(prev_ym)
            mom = (rv - rv_prev) / abs(rv_prev) if rv_prev and rv_prev != 0 else None
            mom_vals.append(mom)

        latest_yoy = yoy_vals[0] if yoy_vals else None
        # Acceleration = latest YoY minus oldest available YoY (positive = accelerating)
        valid_yoys = [(v, i) for i, v in enumerate(yoy_vals) if v is not None]
        rev_accel: float | None = None
        if len(valid_yoys) >= 2:
            rev_accel = valid_yoys[0][0] - valid_yoys[-1][0]
        latest_mom = mom_vals[0] if mom_vals else None

        # EPS trend
        eps_series = eps_by_stock.get(sid, [])
        eps_qoq_latest: float | None = None
        eps_vals: list[float | None] = []
        if len(eps_series) >= 2:
            e0 = eps_series[0][2]
            e1 = eps_series[1][2]
            if e0 is not None and e1 is not None and e1 != 0:
                eps_qoq_latest = (e0 - e1) / abs(e1)
        for _, _, v in eps_series[:5]:
            eps_vals.append(v)
        # EPS trend direction: positive count vs negative count of QoQ
        eps_trend_dir = None
        if len(eps_series) >= 3:
            diffs = []
            for i in range(min(4, len(eps_series) - 1)):
                a = eps_series[i][2]
                b = eps_series[i + 1][2]
                if a is not None and b is not None:
                    diffs.append(a - b)
            pos = sum(1 for d in diffs if d > 0)
            neg = sum(1 for d in diffs if d < 0)
            if pos > neg:
                eps_trend_dir = "up"
            elif neg > pos:
                eps_trend_dir = "down"
            else:
                eps_trend_dir = "flat"

        # Gross margin trend
        gm_series = gm_by_stock.get(sid, [])
        gm_latest: float | None = None
        gm_yoy_chg: float | None = None
        gm_vals: list[float | None] = []
        if gm_series:
            gm_latest = gm_series[-1][1]
            gm_vals = [v for _, v in gm_series[-5:]]
            if len(gm_series) >= 5:
                gm_yoy_chg = gm_series[-1][1] - gm_series[-5][1]
            elif len(gm_series) >= 2:
                gm_yoy_chg = gm_series[-1][1] - gm_series[0][1]

        # ── Composite momentum tier ───────────────────────────────────────
        tier_score = 0
        if rev_accel is not None and rev_accel > 0.2:
            tier_score += 3
        elif rev_accel is not None and rev_accel > 0:
            tier_score += 1
        elif rev_accel is not None and rev_accel < -0.2:
            tier_score -= 2
        if latest_yoy is not None and latest_yoy > 0.3:
            tier_score += 2
        elif latest_yoy is not None and latest_yoy > 0.1:
            tier_score += 1
        elif latest_yoy is not None and latest_yoy < -0.1:
            tier_score -= 1
        if latest_mom is not None and latest_mom > 0.05:
            tier_score += 1
        elif latest_mom is not None and latest_mom < -0.05:
            tier_score -= 1
        if eps_trend_dir == "up":
            tier_score += 2
        elif eps_trend_dir == "down":
            tier_score -= 1
        if gm_yoy_chg is not None and gm_yoy_chg > 0.02:
            tier_score += 2
        elif gm_yoy_chg is not None and gm_yoy_chg < -0.02:
            tier_score -= 1

        if tier_score >= 6:
            tier = "強加速"
        elif tier_score >= 3:
            tier = "加速"
        elif tier_score >= 1:
            tier = "偏正"
        elif tier_score >= -1:
            tier = "持平"
        else:
            tier = "衰退"

        info = masters.get(sid, {})
        rows_out.append({
            "symbol": sid,
            "name": info.get("name") or sid,
            "market": info.get("market") or "",
            "industry": info.get("industry") or "",
            "tier": tier,
            "tier_score": tier_score,
            "latest_rev_yi": round(latest_rev / 1e8, 1),
            "latest_yyyymm": yoy_labels[0] if yoy_labels else None,
            "rev_yoy_m0": round(latest_yoy * 100, 1) if latest_yoy is not None else None,
            "rev_yoy_m1": round(yoy_vals[1] * 100, 1) if len(yoy_vals) > 1 and yoy_vals[1] is not None else None,
            "rev_yoy_m2": round(yoy_vals[2] * 100, 1) if len(yoy_vals) > 2 and yoy_vals[2] is not None else None,
            "rev_accel": round(rev_accel * 100, 1) if rev_accel is not None else None,
            "rev_mom": round(latest_mom * 100, 1) if latest_mom is not None else None,
            "eps_vals": [round(v, 2) if v is not None else None for v in eps_vals[:5]],
            "eps_qoq": round(eps_qoq_latest * 100, 1) if eps_qoq_latest is not None else None,
            "eps_trend_dir": eps_trend_dir,
            "gm_latest": round(gm_latest * 100, 1) if gm_latest is not None else None,
            "gm_yoy_chg": round(gm_yoy_chg * 100, 2) if gm_yoy_chg is not None else None,
            "gm_vals": [round(v * 100, 1) if v is not None else None for v in gm_vals],
        })

    # ── Sort ──────────────────────────────────────────────────────────────
    SORT_KEYS = {
        "rev_accel": lambda r: (r["tier_score"], r["rev_accel"] or -99, r["rev_yoy_m0"] or -99),
        "rev_yoy":   lambda r: (r["tier_score"], r["rev_yoy_m0"] or -99),
        "gm_chg":    lambda r: (r["gm_yoy_chg"] or -99, r["tier_score"]),
        "eps_trend": lambda r: (1 if r["eps_trend_dir"] == "up" else (-1 if r["eps_trend_dir"] == "down" else 0), r["tier_score"]),
    }
    key_fn = SORT_KEYS.get(sort_by, SORT_KEYS["rev_accel"])
    rows_out.sort(key=key_fn, reverse=True)

    generated_at = datetime.now(tz=TAIPEI_TZ).isoformat()
    return {
        "generated_at": generated_at,
        "total": len(rows_out),
        "min_revenue_yi": min_revenue_yi,
        "sort_by": sort_by,
        "rows": rows_out[:limit],
    }


def get_tw_full_market_research_shortlist(limit: int = 25) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    candidates = []
    counts = {
        "source_snapshot_count": 0,
        "strict_candidate_count": 0,
        "core_watch_count": 0,
        "research_watch_count": 0,
    }
    for item in latest_scope_snapshot_items(TW_FULL_MARKET_SCOPE):
        counts["source_snapshot_count"] += 1
        row = tw_full_market_strict_candidate_row(item)
        if row is None:
            continue
        candidates.append(row)

    # Build persistence lookup: symbols with repeated appearances score higher.
    # persistence_score is 0-100; contributes up to +15 composite points to avoid
    # overriding a genuine high-score newcomer, but consistently appearing symbols
    # beat same-score newcomers.
    try:
        persistence_data = compute_recommendation_persistence(days=30, min_days=2)
        persistence_detail_map = {
            item["symbol"]: item
            for item in persistence_data.get("items", [])
        }
        persistence_map = {
            symbol: safe_float(item.get("persistence_score")) or 0.0
            for symbol, item in persistence_detail_map.items()
        }
    except Exception:
        persistence_data = None
        persistence_detail_map = {}
        persistence_map = {}

    # Market regime: in bear market raise research_watch score threshold for TW stocks.
    try:
        regime_data = get_market_regime("0050.TW")
        tw_regime = regime_data.get("regime", "unknown")
    except Exception:
        tw_regime = "unknown"
    for row in candidates:
        detail = persistence_detail_map.get(row.get("symbol") or "") or {}
        ps = safe_float(detail.get("persistence_score")) or 0.0
        row["persistence_score"] = round(ps, 1) if ps else None
        row["persistence_coverage_percent"] = detail.get("coverage_percent")
        row["persistence_streak"] = detail.get("streak")
        row["persistence_qualified"] = bool(detail.get("qualified"))
        row["persistence_appearances"] = detail.get("appearances")
        row["persistence_logged_days"] = persistence_data.get("logged_days") if persistence_data else None
        row["market_regime"] = tw_regime if row.get("asset_type") == "台股" else None

    # In bear market, keep only core_watch Taiwan stocks (financial_audit=pass/not_applicable).
    if tw_regime == "bear":
        candidates = [
            row for row in candidates
            if row.get("tier") == "core_watch"
            or row.get("asset_type") != "台股"
        ]

    candidates.sort(
        key=lambda row: (
            1 if row.get("tier") == "core_watch" else 0,
            (row.get("score") or 0) + (persistence_map.get(row.get("symbol") or "", 0.0) * 0.15),
            row.get("rs_3m_percentile") or 0,
            row.get("volume") or 0,
        ),
        reverse=True,
    )
    for row in candidates:
        counts["strict_candidate_count"] += 1
        if row.get("tier") == "core_watch":
            counts["core_watch_count"] += 1
        else:
            counts["research_watch_count"] += 1
    segments: dict[str, dict[str, Any]] = {}
    for row in candidates:
        instrument = "etf" if row.get("instrument_type") == "ETF" else "stock"
        tier = row.get("tier") or "unknown"
        segment = segments.setdefault(
            instrument,
            {
                "instrument": instrument,
                "count": 0,
                "core_watch_count": 0,
                "research_watch_count": 0,
                "top": [],
            },
        )
        segment["count"] += 1
        if tier == "core_watch":
            segment["core_watch_count"] += 1
        else:
            segment["research_watch_count"] += 1
        if len(segment["top"]) < 8:
            segment["top"].append(row)
    return {
        "generated_at": utc_now(),
        "scope": TW_FULL_MARKET_SCOPE,
        "definition": TW_FULL_MARKET_POLICY,
        "status": get_tw_full_market_status(),
        "rules": {
            "exclude_chart_source": "TPEx official quote",
            "min_data_confidence": "high",
            "min_data_coverage": 0.75,
            "min_volume": 500_000,
            "min_score": 58,
            "min_rs_3m_percentile": 70,
            "trend_filter": "+DI must be greater than -DI when both are available",
            "core_watch": "financial_audit_status is pass/not_applicable and abs(change_1d) < 8%",
            "research_watch": "requires additional validation before any position sizing",
        },
        "counts": counts,
        "segments": segments,
        "returned_count": min(limit, len(candidates)),
        "shortlist": candidates[:limit],
        "market_regime": tw_regime,
        "note": "This is a research shortlist, not personalized financial advice or an order instruction.",
    }




class InvestmentHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/presets":
            json_response(self, {"presets": ASSET_PRESETS})
            return

        if parsed.path == "/api/db/status":
            json_response(self, get_db_status())
            return

        if parsed.path == "/api/aegis/status":
            json_response(self, get_aegis_snapshot_status())
            return

        if parsed.path == "/api/aegis/refresh":
            params = parse_qs(parsed.query)
            force = params.get("force", ["0"])[0].strip().lower() in {"1", "true", "yes"}
            payload = force_refresh_aegis_snapshot() if force else refresh_aegis_snapshot_if_needed()
            json_response(self, payload)
            return

        if parsed.path == "/api/data/health":
            json_response(self, get_data_health())
            return

        if parsed.path == "/api/data/health/actions":
            json_response(self, {"generated_at": utc_now(), "actions": get_data_health().get("actions", [])})
            return

        if parsed.path == "/api/data/audits":
            params = parse_qs(parsed.query)
            limit_value = safe_float(params.get("limit", ["50"])[0])
            limit = int(limit_value) if limit_value else 50
            dataset = params.get("dataset", [""])[0].strip() or None
            json_response(self, get_data_import_audits(limit, dataset))
            return

        if parsed.path == "/api/data/quality":
            params = parse_qs(parsed.query)
            limit_value = safe_float(params.get("limit", ["100"])[0])
            limit = int(limit_value) if limit_value else 100
            symbol = params.get("symbol", [""])[0].strip() or None
            json_response(self, get_data_quality_history(symbol, limit))
            return

        if parsed.path == "/api/data/financial-audit":
            params = parse_qs(parsed.query)
            limit_value = safe_float(params.get("limit", ["100"])[0])
            limit = int(limit_value) if limit_value else 100
            symbol = params.get("symbol", [""])[0].strip() or None
            json_response(self, get_financial_data_audit(limit, symbol))
            return

        if parsed.path == "/api/data/aegis/status":
            json_response(self, get_aegis_snapshot_status())
            return

        if parsed.path == "/api/industry-comparison":
            params = parse_qs(parsed.query)
            symbol = params.get("symbol", [""])[0].strip()
            if not symbol:
                json_response(self, {"error": "symbol required"}, 400)
                return
            result = get_industry_comparison(symbol)
            if result is None:
                json_response(self, {"error": "no industry comparison data for this symbol", "symbol": symbol}, 404)
                return
            json_response(self, result)
            return

        if parsed.path == "/api/industry-comparison/insight":
            params = parse_qs(parsed.query)
            symbol = params.get("symbol", [""])[0].strip()
            if not symbol:
                json_response(self, {"error": "symbol required"}, 400)
                return
            l1 = get_industry_comparison(symbol)
            if l1 is None:
                json_response(self, {"error": "no industry comparison data", "symbol": symbol}, 404)
                return
            from llm_client import generate_industry_insight, llm_enabled
            if not llm_enabled():
                json_response(self, {"error": "LLM disabled — 請先在 settings.json 設定 llm.enabled=true 並填入 api_key"})
                return
            news = get_taiwan_stock_news(symbol, l1.get("name") or "", limit=8)
            insight = generate_industry_insight(l1, news)
            json_response(self, {**insight, "symbol": symbol, "industry_group": l1.get("industry_group")})
            return

        if parsed.path == "/api/dashboard/latest":
            json_response(self, get_latest_dashboard())
            return

        if parsed.path == "/api/scheduler/status":
            json_response(self, scheduler_state())
            return

        if parsed.path == "/api/scheduler/start":
            params = parse_qs(parsed.query)
            interval_value = safe_float(params.get("interval_minutes", ["30"])[0])
            batch_value = safe_float(params.get("batch_size", ["25"])[0])
            refresh_value = safe_float(params.get("refresh_minutes", ["60"])[0])
            min_priority_value = safe_float(params.get("min_priority", ["25"])[0])
            offset_param = params.get("cursor_offset", params.get("offset"))
            offset_value = safe_float(offset_param[0]) if offset_param else None
            asset_type = params.get("asset_type", [""])[0].strip() or None
            scope = params.get("scope", [""])[0].strip() or None
            try:
                json_response(
                    self,
                    start_scheduler(
                        int(interval_value) if interval_value else 30,
                        int(batch_value) if batch_value else 25,
                        int(refresh_value) if refresh_value is not None else 60,
                        int(min_priority_value) if min_priority_value is not None else 25,
                        asset_type,
                        scope,
                        int(offset_value) if offset_value is not None else None,
                    ),
                )
            except Exception as exc:
                import traceback
                json_response(self, {"error": str(exc), "traceback": traceback.format_exc(), "status": "error"}, 500)
            return

        if parsed.path == "/api/scheduler/stop":
            json_response(self, stop_scheduler())
            return

        if parsed.path == "/api/scheduler/runs":
            params = parse_qs(parsed.query)
            limit_value = safe_float(params.get("limit", ["50"])[0])
            limit = max(1, min(int(limit_value) if limit_value else 50, 500))
            status_filter = params.get("status", [""])[0].strip() or None
            with db_connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, started_at, finished_at, status, interval_minutes, batch_size,
                           refresh_minutes, min_priority, asset_type, scope, scan_run_id, error
                    FROM scheduler_runs
                    WHERE (? IS NULL OR status = ?)
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (status_filter, status_filter, limit),
                ).fetchall()
            json_response(self, {"runs": [dict(row) for row in rows], "count": len(rows)})
            return

        if parsed.path == "/api/recommendations/daily-log":
            params = parse_qs(parsed.query)
            force = params.get("force", ["0"])[0].strip().lower() in {"1", "true", "yes"}
            limit_value = safe_float(params.get("limit", ["50"])[0])
            json_response(self, write_daily_recommendation_log(force=force, limit=int(limit_value) if limit_value else 50))
            return

        if parsed.path == "/api/recommendations/daily-log/history":
            params = parse_qs(parsed.query)
            limit_value = safe_float(params.get("limit", ["20"])[0])
            json_response(self, read_daily_recommendation_log_history(int(limit_value) if limit_value else 20))
            return

        if parsed.path == "/api/recommendations/daily-log/performance":
            params = parse_qs(parsed.query)
            limit_value = safe_float(params.get("limit", ["100"])[0])
            json_response(self, get_daily_recommendation_performance(int(limit_value) if limit_value else 100))
            return

        if parsed.path == "/api/recommendations/persistence":
            params = parse_qs(parsed.query)
            days_value = safe_float(params.get("days", ["30"])[0])
            min_days_value = safe_float(params.get("min_days", ["2"])[0])
            json_response(self, compute_recommendation_persistence(
                days=int(days_value) if days_value else 30,
                min_days=int(min_days_value) if min_days_value else 2,
            ))
            return

        if parsed.path == "/api/recommendations/persistence/backfill":
            params = parse_qs(parsed.query)
            days_value = safe_float(params.get("days", ["30"])[0])
            min_days_value = safe_float(params.get("min_days", ["2"])[0])
            json_response(self, backfill_daily_recommendation_persistence(
                days=int(days_value) if days_value else 30,
                min_days=int(min_days_value) if min_days_value else 2,
            ))
            return

        if parsed.path == "/api/fundamental-momentum":
            params = parse_qs(parsed.query)
            min_rev = safe_float(params.get("min_rev", ["1"])[0]) or 1.0
            limit_val = int(safe_float(params.get("limit", ["150"])[0]) or 150)
            sort_by = params.get("sort", ["rev_accel"])[0].strip() or "rev_accel"
            json_response(self, get_fundamental_momentum_scan(
                min_revenue_yi=min_rev,
                limit=min(limit_val, 300),
                sort_by=sort_by,
            ))
            return

        if parsed.path == "/api/watchlist/status":
            with WATCHLIST_LOCK:
                state = dict(WATCHLIST_STATE)
            thread = WATCHLIST_THREAD
            state["thread_alive"] = bool(thread and thread.is_alive())
            state["generated_at"] = utc_now()
            json_response(self, state)
            return

        if parsed.path == "/api/watchlist/alerts":
            params = parse_qs(parsed.query)
            limit_val = int(safe_float(params.get("limit", ["30"])[0]) or 30)
            days_val = int(safe_float(params.get("days", ["1"])[0]) or 1)
            alerts = get_watchlist_alerts(limit=limit_val, days=days_val)
            json_response(self, {"generated_at": utc_now(), "count": len(alerts), "alerts": alerts})
            return

        if parsed.path == "/api/universe":
            params = parse_qs(parsed.query)
            limit_value = safe_float(params.get("limit", ["100"])[0])
            offset_value = safe_float(params.get("offset", ["0"])[0])
            limit = int(limit_value) if limit_value else 100
            offset = int(offset_value) if offset_value is not None else 0
            asset_type = params.get("asset_type", [""])[0].strip() or None
            json_response(
                self,
                {
                    "universe": get_universe(asset_type, limit, offset),
                    "available_universe_count": get_universe_count(asset_type),
                    "offset": offset,
                },
            )
            return

        if parsed.path == "/api/universe/unsnapped":
            params = parse_qs(parsed.query)
            limit_value = safe_float(params.get("limit", ["500"])[0])
            offset_value = safe_float(params.get("offset", ["0"])[0])
            limit = int(limit_value) if limit_value else 500
            offset = int(offset_value) if offset_value is not None else 0
            asset_type = params.get("asset_type", [""])[0].strip() or None
            export = params.get("export", ["0"])[0].strip().lower() in {"1", "true", "yes"}
            json_response(self, get_unsnapped_universe_report(limit, offset, asset_type, export))
            return

        if parsed.path == "/api/universe/scopes":
            json_response(self, get_universe_scope_definitions())
            return

        if parsed.path == "/api/universe/tw-full-market/status":
            json_response(self, get_tw_full_market_status())
            return

        if parsed.path == "/api/universe/tw-full-market/gaps":
            json_response(self, get_tw_full_market_quote_gaps())
            return

        if parsed.path == "/api/universe/tw-full-market/gaps/remediation":
            json_response(self, get_no_quote_remediation())
            return

        if parsed.path == "/api/universe/tw-full-market/sync":
            try:
                json_response(self, sync_tw_full_market_universe_from_aegis())
            except Exception as exc:
                json_response(self, {"error": str(exc)}, 502)
            return

        if parsed.path == "/api/opportunities/tw-full-market":
            params = parse_qs(parsed.query)
            limit_value = safe_float(params.get("limit", ["50"])[0])
            min_priority_value = safe_float(params.get("min_priority", ["25"])[0])
            limit = int(limit_value) if limit_value else 50
            min_priority = int(min_priority_value) if min_priority_value is not None else 25
            payload = get_tw_full_market_opportunity_candidates(limit, min_priority)
            export = params.get("export", ["0"])[0].strip().lower() in {"1", "true", "yes"}
            if export:
                payload["exports"] = export_tw_full_market_opportunity_report(payload)
            json_response(self, payload)
            return

        if parsed.path == "/api/opportunities/tw-full-market/shortlist":
            params = parse_qs(parsed.query)
            limit_value = safe_float(params.get("limit", ["25"])[0])
            limit = int(limit_value) if limit_value else 25
            json_response(self, get_tw_full_market_research_shortlist(limit))
            return

        if parsed.path == "/api/universe/expand":
            params = parse_qs(parsed.query)
            profile = params.get("profile", ["starter"])[0].strip() or "starter"
            try:
                json_response(self, expand_universe(profile))
            except ValueError as exc:
                json_response(self, {"error": str(exc)}, 400)
            return

        if parsed.path == "/api/universe/import":
            params = parse_qs(parsed.query)
            filename = params.get("file", ["universe_import.csv"])[0].strip() or "universe_import.csv"
            try:
                json_response(self, import_universe_csv(filename))
            except ValueError as exc:
                json_response(self, {"error": str(exc)}, 400)
            return

        if parsed.path == "/api/universe/maximize":
            params = parse_qs(parsed.query)
            count_value = safe_float(params.get("count", ["250"])[0])
            count = int(count_value) if count_value else 250
            json_response(self, sync_yahoo_max_universe(count))
            return

        if parsed.path == "/api/universe/import-active-etf":
            json_response(self, import_universe_from_active_etf_holdings())
            return

        if parsed.path == "/api/universe/import-twse":
            try:
                json_response(self, import_twse_universe_from_openapi())
            except Exception as exc:
                json_response(self, {"error": str(exc)}, 502)
            return

        if parsed.path == "/api/active-etf/import":
            params = parse_qs(parsed.query)
            filename = params.get("file", ["active_etf_changes.csv"])[0].strip() or "active_etf_changes.csv"
            try:
                json_response(self, import_active_etf_changes_csv(filename))
            except ValueError as exc:
                json_response(self, {"error": str(exc)}, 400)
            return

        if parsed.path == "/api/active-etf/import-zdsetf":
            params = parse_qs(parsed.query)
            max_value = safe_float(params.get("max_etfs", [""])[0])
            max_etfs = int(max_value) if max_value else None
            try:
                json_response(self, import_active_etf_zdsetf(max_etfs))
            except Exception as exc:
                json_response(self, {"error": str(exc)}, 502)
            return

        if parsed.path == "/api/active-etf/sync":
            params = parse_qs(parsed.query)
            force_manual = params.get("force_manual_csv", ["0"])[0].strip() in {"1", "true", "yes"}
            payload = sync_active_etf_sources(force_manual)
            status_code = 200 if payload.get("status") in {"ok", "cached", "stale"} else 502
            json_response(self, payload, status_code)
            return

        if parsed.path == "/api/active-etf/source-status":
            json_response(self, get_active_etf_source_status())
            return

        if parsed.path == "/api/active-etf/official-source-candidates":
            json_response(self, get_active_etf_official_source_candidates())
            return

        if parsed.path == "/api/active-etf/holdings/import":
            params = parse_qs(parsed.query)
            filename = params.get("file", ["active_etf_holdings.csv"])[0].strip() or "active_etf_holdings.csv"
            try:
                json_response(self, import_active_etf_holdings_csv(filename))
            except ValueError as exc:
                json_response(self, {"error": str(exc)}, 400)
            except Exception as exc:
                json_response(self, {"error": type(exc).__name__, "message": str(exc)}, 500)
            return

        if parsed.path == "/api/active-etf/holdings":
            params = parse_qs(parsed.query)
            limit_value = safe_float(params.get("limit", ["500"])[0])
            limit = int(limit_value) if limit_value else 500
            trade_date = params.get("trade_date", [""])[0].strip() or None
            etf_symbol = params.get("etf", [""])[0].strip() or None
            json_response(self, get_active_etf_holdings(limit, trade_date, etf_symbol))
            return

        if parsed.path == "/api/active-etf/schedule/run-due":
            params = parse_qs(parsed.query)
            force = params.get("force", ["0"])[0].strip() in {"1", "true", "yes"}
            json_response(self, run_due_active_etf_import(force))
            return

        if parsed.path == "/api/active-etf/audit":
            json_response(self, get_active_etf_audit())
            return

        if parsed.path == "/api/active-etf/changes":
            params = parse_qs(parsed.query)
            limit_value = safe_float(params.get("limit", ["100"])[0])
            limit = int(limit_value) if limit_value else 100
            trade_date = params.get("trade_date", [""])[0].strip() or None
            stock_symbol = params.get("stock", [""])[0].strip() or None
            json_response(self, get_active_etf_changes(limit, trade_date, stock_symbol))
            return

        if parsed.path == "/api/scan":
            params = parse_qs(parsed.query)
            limit_value = safe_float(params.get("limit", ["25"])[0])
            min_priority_value = safe_float(params.get("min_priority", ["25"])[0])
            refresh_value = safe_float(params.get("refresh_minutes", ["60"])[0])
            threshold_override = safe_float(params.get("threshold", [""])[0])
            asset_type = params.get("asset_type", [""])[0].strip() or None
            scope = params.get("scope", [""])[0].strip() or None
            limit = int(limit_value) if limit_value else 25
            refresh_minutes = int(refresh_value) if refresh_value is not None else 60
            offset_value = safe_float(params.get("offset", ["0"])[0])
            min_priority = int(min_priority_value) if min_priority_value is not None else 25
            offset = int(offset_value) if offset_value is not None else 0
            payload = scan_market(limit, threshold_override, min_priority, asset_type, refresh_minutes, offset, scope)
            json_response(self, payload, 200 if payload["scanned_count"] else 502)
            return

        if parsed.path == "/api/history":
            params = parse_qs(parsed.query)
            symbol = params.get("symbol", [""])[0].strip().upper()
            if not symbol:
                json_response(self, {"error": "請提供 symbol。"}, 400)
                return
            limit_value = safe_float(params.get("limit", ["30"])[0])
            limit = int(limit_value) if limit_value else 30
            json_response(self, get_history(symbol, limit))
            return

        if parsed.path == "/api/recommendation/outcomes":
            params = parse_qs(parsed.query)
            limit_value = safe_float(params.get("limit", ["100"])[0])
            limit = int(limit_value) if limit_value else 100
            json_response(self, get_recommendation_outcomes(limit))
            return

        if parsed.path == "/api/recommendation/outcomes/missing-prices":
            params = parse_qs(parsed.query)
            limit_value = safe_float(params.get("limit", ["100"])[0])
            limit = int(limit_value) if limit_value else 100
            json_response(self, get_missing_recommendation_outcome_prices(limit))
            return

        if parsed.path == "/api/recommendation/pre-trade-checklist":
            params = parse_qs(parsed.query)
            symbol = params.get("symbol", [""])[0].strip() or None
            limit_value = safe_float(params.get("limit", ["12"])[0])
            limit = int(limit_value) if limit_value else 12
            json_response(self, get_pre_trade_checklist(symbol, limit))
            return

        if parsed.path == "/api/recommendation/outcomes/attribution":
            params = parse_qs(parsed.query)
            horizon_value = safe_float(params.get("horizon_days", ["5"])[0])
            min_count_value = safe_float(params.get("min_count", ["5"])[0])
            horizon_days = int(horizon_value) if horizon_value else 5
            min_count = int(min_count_value) if min_count_value else 5
            json_response(self, get_recommendation_outcome_attribution(horizon_days, min_count))
            return

        if parsed.path == "/api/recommendation/outcomes/quant-attribution":
            params = parse_qs(parsed.query)
            horizon_value = safe_float(params.get("horizon_days", ["5"])[0])
            min_count_value = safe_float(params.get("min_count", ["3"])[0])
            horizon_days = int(horizon_value) if horizon_value else 5
            min_count = int(min_count_value) if min_count_value else 3
            json_response(self, get_quant_outcome_attribution(horizon_days, min_count))
            return

        if parsed.path == "/api/recommendation/outcomes/backfill":
            params = parse_qs(parsed.query)
            limit_value = safe_float(params.get("limit", ["1000"])[0])
            limit = int(limit_value) if limit_value else 1000
            json_response(self, backfill_recommendation_outcomes(limit))
            return

        if parsed.path == "/api/backtest":
            params = parse_qs(parsed.query)
            symbols = [
                symbol.strip().upper()
                for raw in params.get("symbols", [""])[0].split(",")
                for symbol in [raw]
                if symbol.strip()
            ][:8]
            if not symbols:
                json_response(self, {"error": "請提供至少一個投資代號。"}, 400)
                return
            days_value = safe_float(params.get("days", ["180"])[0])
            cost_value = safe_float(params.get("cost_bps", ["5"])[0])
            days = int(days_value) if days_value else 180
            cost_bps = cost_value if cost_value is not None else 5.0
            payload = run_backtests(symbols, days, cost_bps)
            json_response(self, payload, 200 if payload["results"] else 502)
            return

        if parsed.path == "/api/research":
            params = parse_qs(parsed.query)
            symbols = [
                symbol.strip().upper()
                for raw in params.get("symbols", [""])[0].split(",")
                for symbol in [raw]
                if symbol.strip()
            ][:8]
            if not symbols:
                json_response(self, {"error": "請提供至少一個投資代號。"}, 400)
                return
            threshold_override = safe_float(params.get("threshold", [""])[0])
            payload = run_research_team(symbols, threshold_override)
            json_response(self, payload, 200 if payload["results"] else 502)
            return

        if parsed.path == "/api/analyze":
            params = parse_qs(parsed.query)
            symbols = [
                symbol.strip().upper()
                for raw in params.get("symbols", [""])[0].split(",")
                for symbol in [raw]
                if symbol.strip()
            ][:8]
            if not symbols:
                json_response(self, {"error": "請提供至少一個投資代號。"}, 400)
                return
            threshold_override = safe_float(params.get("threshold", [""])[0])
            started = time.time()
            results = []
            errors = []
            for symbol in symbols:
                try:
                    results.append(analyze_symbol(symbol, threshold_override))
                except Exception as exc:
                    errors.append({"symbol": symbol, "message": str(exc)})
            saved_snapshots = save_analysis_results(results)
            json_response(
                self,
                {
                    "results": results,
                    "errors": errors,
                    "saved_snapshots": saved_snapshots,
                    "elapsed_seconds": round(time.time() - started, 2),
                    "disclaimer": "本工具僅供研究與教育用途，不構成投資顧問、招攬或保證獲利。",
                },
                200 if results else 502,
            )
            return

        self.serve_static(parsed.path)

    def serve_static(self, path: str) -> None:
        requested = "index.html" if path in {"", "/"} else path.lstrip("/")
        file_path = (STATIC_DIR / requested).resolve()
        if not str(file_path).startswith(str(STATIC_DIR.resolve())) or not file_path.exists():
            self.send_error(404)
            return
        content_type = "text/plain; charset=utf-8"
        if file_path.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif file_path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif file_path.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        text_response(self, file_path.read_bytes(), content_type)

    def log_message(self, format: str, *args: Any) -> None:
        return


# ===== WATCHLIST MONITOR =====

def get_watchlist_symbols() -> list[dict[str, Any]]:
    """Return symbols from latest daily log; fall back to current shortlist."""
    history = read_daily_recommendation_log_history(limit=1)
    records = history.get("records") or []
    if records:
        recs = records[0].get("recommendations") or []
        if recs:
            return [{"symbol": r["symbol"], "tier": r.get("tier"), "score": r.get("score")} for r in recs]
    try:
        data = get_tw_full_market_research_shortlist(limit=40)
        return [{"symbol": r["symbol"], "tier": r.get("tier"), "score": r.get("score")} for r in data.get("shortlist", [])]
    except Exception:
        return []


def save_watchlist_alert(
    symbol: str,
    triggered_at: str,
    kind: str,
    change_pct: float,
    price: float | None,
    baseline_price: float | None,
    tier: str | None,
    threshold_pct: float,
    title: str,
    detail: str,
    snapshot_id: int | None = None,
) -> int:
    with db_connect() as conn:
        cur = conn.execute(
            """INSERT INTO watchlist_alerts
               (symbol, triggered_at, kind, change_pct, price, baseline_price, tier, threshold_pct, title, detail, snapshot_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (symbol, triggered_at, kind, change_pct, price, baseline_price, tier, threshold_pct, title, detail, snapshot_id),
        )
        return int(cur.lastrowid or 0)


def run_watchlist_cycle(threshold_pct: float = 2.0) -> dict[str, Any]:
    """One price-check cycle for all watchlist symbols."""
    symbols_meta = get_watchlist_symbols()
    if not symbols_meta:
        return {"checked": 0, "triggered": 0, "errors": 0, "symbols_total": 0}

    now = utc_now()
    checked = 0
    triggered = 0
    errors = 0
    MAX_FULL_ANALYSES = 3  # cap API calls per cycle

    for meta in symbols_meta:
        symbol = meta["symbol"]
        tier = meta.get("tier")
        try:
            quote = get_aegis_quote(symbol)
            if quote is None:
                quotes_map = get_quote([symbol])
                quote = quotes_map.get(symbol)
            if quote is None or quote.price is None:
                continue
            price = quote.price
            change_pct = quote.change_percent or 0.0
            checked += 1

            with WATCHLIST_LOCK:
                baseline = WATCHLIST_BASELINES.get(symbol)
                if baseline is None:
                    # First seen: establish baseline, no alert
                    WATCHLIST_BASELINES[symbol] = {
                        "baseline_price": price,
                        "last_alerted_pct": change_pct,
                    }
                    continue
                last_alerted = baseline.get("last_alerted_pct", change_pct)

            delta = abs(change_pct - last_alerted)
            if delta < threshold_pct:
                continue

            kind = "price_move_up" if change_pct > last_alerted else "price_move_down"
            direction = "急漲" if kind == "price_move_up" else "急跌"
            title = f"{symbol} {direction} {change_pct:+.1f}%"
            detail = (
                f"今日變動 {change_pct:+.2f}%（上次警示 {last_alerted:+.1f}%，門檻 ±{threshold_pct}%），tier={tier or '-'}"
            )

            snapshot_id = None
            if triggered < MAX_FULL_ANALYSES:
                try:
                    item = analyze_symbol(symbol)
                    snapshot_id = save_analysis_result(item)
                except Exception:
                    pass

            save_watchlist_alert(
                symbol=symbol, triggered_at=now, kind=kind, change_pct=change_pct,
                price=price, baseline_price=baseline.get("baseline_price"),
                tier=tier, threshold_pct=threshold_pct, title=title, detail=detail,
                snapshot_id=snapshot_id,
            )
            with WATCHLIST_LOCK:
                WATCHLIST_BASELINES[symbol]["last_alerted_pct"] = change_pct
            triggered += 1

        except Exception as exc:
            errors += 1

    return {"checked": checked, "triggered": triggered, "errors": errors, "symbols_total": len(symbols_meta)}


def watchlist_monitor_loop(interval_minutes: int = 5, threshold_pct: float = 2.0) -> None:
    global WATCHLIST_STATE
    with WATCHLIST_LOCK:
        WATCHLIST_STATE.update({
            "enabled": True, "running": False,
            "started_at": utc_now(), "last_error": None,
        })
    while not WATCHLIST_STOP.is_set():
        with WATCHLIST_LOCK:
            WATCHLIST_STATE["running"] = True
        try:
            result = run_watchlist_cycle(threshold_pct)
            symbols_meta = get_watchlist_symbols()
            with WATCHLIST_LOCK:
                WATCHLIST_STATE.update({
                    "running": False,
                    "last_run_at": utc_now(),
                    "last_result": result,
                    "last_error": None,
                    "symbols_monitored": [m["symbol"] for m in symbols_meta],
                })
        except Exception as exc:
            with WATCHLIST_LOCK:
                WATCHLIST_STATE.update({"running": False, "last_error": str(exc)})

        wake_at = time.time() + interval_minutes * 60
        while time.time() < wake_at and not WATCHLIST_STOP.is_set():
            remaining = int(wake_at - time.time())
            next_dt = datetime.now(timezone.utc) + timedelta(seconds=remaining)
            with WATCHLIST_LOCK:
                WATCHLIST_STATE["next_run_at"] = next_dt.isoformat()
            WATCHLIST_STOP.wait(timeout=10)

    with WATCHLIST_LOCK:
        WATCHLIST_STATE["enabled"] = False


def get_watchlist_alerts(limit: int = 30, days: int = 1) -> list[dict[str, Any]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with db_connect() as conn:
        rows = conn.execute(
            """SELECT id, symbol, triggered_at, kind, change_pct, price, baseline_price,
                      tier, threshold_pct, title, detail, snapshot_id
               FROM watchlist_alerts
               WHERE triggered_at >= ?
               ORDER BY triggered_at DESC
               LIMIT ?""",
            (cutoff, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def main() -> None:
    global WATCHLIST_THREAD
    init_db()
    WATCHLIST_THREAD = threading.Thread(
        target=watchlist_monitor_loop,
        kwargs={"interval_minutes": 5, "threshold_pct": 2.0},
        daemon=True,
    )
    WATCHLIST_THREAD.start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), InvestmentHandler)
    print(f"Investment helper running at http://127.0.0.1:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
