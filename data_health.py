from __future__ import annotations

import csv
import json
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import (
    AEGIS_REFRESH_SCRIPT,
    AEGIS_SNAPSHOT_DB,
    AEGIS_SNAPSHOT_METADATA,
    AEGIS_SOURCE_DB,
    DAILY_RECOMMENDATION_LOG_TIME,
    DB_PATH,
    EXPORT_DIR,
    FINANCIAL_AUDIT_RULES,
    ROOT,
    TAIPEI_TZ,
    aegis_connect,
    db_connect,
    ensure_export_dirs,
    is_fund_like_symbol,
    parse_utc_timestamp,
    safe_float,
    taiwan_stock_id,
    timestamp_slug,
    utc_now,
    write_text_file,
)
from outcomes import update_recommendation_outcomes
from scheduler import scheduler_state
from active_etf import get_active_etf_source_status
from tw_universe import (
    TW_FULL_MARKET_SCOPE,
    get_tw_full_market_quote_gaps,
    get_tw_full_market_status,
    normalize_tw_stock_symbol,
)


def yyyymm_age_months(value: str | None, now: datetime | None = None) -> int | None:
    if not value or not re.fullmatch(r"\d{6}", str(value)):
        return None
    now = now or datetime.now(timezone.utc)
    year = int(str(value)[:4])
    month = int(str(value)[4:6])
    return (now.year - year) * 12 + now.month - month


def quarter_age_quarters(year: Any, quarter: Any, now: datetime | None = None) -> int | None:
    try:
        year_int = int(year)
        quarter_int = int(quarter)
    except (TypeError, ValueError):
        return None
    if quarter_int < 1 or quarter_int > 4:
        return None
    now = now or datetime.now(timezone.utc)
    current_quarter = (now.month - 1) // 3 + 1
    return (now.year - year_int) * 4 + current_quarter - quarter_int


def read_aegis_snapshot_metadata() -> dict[str, Any]:
    if not AEGIS_SNAPSHOT_METADATA.exists():
        return {}
    try:
        return json.loads(AEGIS_SNAPSHOT_METADATA.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def should_refresh_aegis_snapshot(max_age_minutes: int = 60) -> tuple[bool, str]:
    if not AEGIS_SOURCE_DB.exists():
        return False, "source_missing"
    if not AEGIS_SNAPSHOT_DB.exists() or not AEGIS_SNAPSHOT_METADATA.exists():
        return True, "snapshot_missing"

    metadata = read_aegis_snapshot_metadata()
    source_mtime = AEGIS_SOURCE_DB.stat().st_mtime
    snapshot_source_mtime = safe_float(metadata.get("source_mtime"))
    if snapshot_source_mtime is None:
        return True, "metadata_source_mtime_missing"
    if source_mtime > snapshot_source_mtime + 0.001:
        return True, "source_newer"

    copied_at = parse_utc_timestamp(metadata.get("copied_at") or "")
    if copied_at is None:
        return True, "metadata_copied_at_missing"
    age_minutes = (datetime.now(timezone.utc) - copied_at).total_seconds() / 60
    if age_minutes > max_age_minutes and metadata.get("source_size") != AEGIS_SOURCE_DB.stat().st_size:
        return True, "source_size_changed"
    return False, "up_to_date"


def get_aegis_snapshot_status() -> dict[str, Any]:
    status: dict[str, Any] = {
        "available": False,
        "path": str(AEGIS_SNAPSHOT_DB),
        "metadata_path": str(AEGIS_SNAPSHOT_METADATA),
        "copied_at": None,
        "copied_age_hours": None,
        "source_mtime": None,
        "source_mtime_at": None,
        "snapshot_size": None,
        "source_size": None,
        "table_counts": {},
        "price_daily": {"latest_date": None, "row_count": 0, "stock_count": 0},
        "revenue_monthly": {"latest_yyyymm": None, "row_count": 0, "stock_count": 0},
        "eps_quarterly": {"latest_period": None, "row_count": 0, "stock_count": 0},
        "stock_master_count": 0,
        "stale": True,
        "stale_reasons": [],
        "error": None,
    }
    if not AEGIS_SNAPSHOT_DB.exists():
        status["stale_reasons"].append("snapshot_missing")
        return status

    status["available"] = True
    status["snapshot_size"] = AEGIS_SNAPSHOT_DB.stat().st_size
    if AEGIS_SNAPSHOT_METADATA.exists():
        try:
            metadata = json.loads(AEGIS_SNAPSHOT_METADATA.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            status["stale_reasons"].append("metadata_unreadable")
            status["error"] = str(exc)
        else:
            status["copied_at"] = metadata.get("copied_at")
            status["source_mtime"] = metadata.get("source_mtime")
            status["source_size"] = metadata.get("source_size")
            status["snapshot_size"] = metadata.get("snapshot_size") or status["snapshot_size"]
            status["table_counts"] = metadata.get("table_counts") or {}
            source_mtime = safe_float(metadata.get("source_mtime"))
            if source_mtime is not None:
                status["source_mtime_at"] = datetime.fromtimestamp(source_mtime, timezone.utc).isoformat()
            copied_at = parse_utc_timestamp(metadata.get("copied_at") or "")
            if copied_at is not None:
                age_hours = (datetime.now(timezone.utc) - copied_at).total_seconds() / 3600
                status["copied_age_hours"] = round(age_hours, 2)
                if age_hours > 24:
                    status["stale_reasons"].append("snapshot_older_than_24h")
            else:
                status["stale_reasons"].append("copied_at_missing")
    else:
        status["stale_reasons"].append("metadata_missing")

    conn = aegis_connect()
    if conn is None:
        status["stale_reasons"].append("snapshot_unreadable")
        status["available"] = False
        return status
    try:
        price = conn.execute(
            """
            SELECT MAX(date) AS latest_date, COUNT(*) AS row_count, COUNT(DISTINCT stock_id) AS stock_count
            FROM price_daily
            """
        ).fetchone()
        revenue = conn.execute(
            """
            SELECT MAX(yyyymm) AS latest_yyyymm, COUNT(*) AS row_count, COUNT(DISTINCT stock_id) AS stock_count
            FROM revenue_monthly
            """
        ).fetchone()
        eps = conn.execute(
            """
            SELECT MAX(printf('%04dQ%d', year, quarter)) AS latest_period,
                   COUNT(*) AS row_count,
                   COUNT(DISTINCT stock_id) AS stock_count
            FROM eps_quarterly
            """
        ).fetchone()
        stock_master_count = conn.execute("SELECT COUNT(*) FROM stock_master").fetchone()[0]
    except sqlite3.Error as exc:
        status["error"] = str(exc)
        status["stale_reasons"].append("snapshot_query_failed")
    finally:
        conn.close()

    if "price" in locals() and price:
        status["price_daily"] = {
            "latest_date": price["latest_date"],
            "row_count": price["row_count"],
            "stock_count": price["stock_count"],
        }
    if "revenue" in locals() and revenue:
        status["revenue_monthly"] = {
            "latest_yyyymm": revenue["latest_yyyymm"],
            "row_count": revenue["row_count"],
            "stock_count": revenue["stock_count"],
        }
    if "eps" in locals() and eps:
        status["eps_quarterly"] = {
            "latest_period": eps["latest_period"],
            "row_count": eps["row_count"],
            "stock_count": eps["stock_count"],
        }
    if "stock_master_count" in locals():
        status["stock_master_count"] = stock_master_count

    if not status["price_daily"]["latest_date"]:
        status["stale_reasons"].append("price_daily_empty")
    if not status["revenue_monthly"]["latest_yyyymm"]:
        status["stale_reasons"].append("revenue_monthly_empty")
    if not status["eps_quarterly"]["latest_period"]:
        status["stale_reasons"].append("eps_quarterly_empty")
    status["stale"] = bool(status["stale_reasons"])
    return status


def _run_aegis_main(timeout_seconds: float = 45.0) -> tuple[int, str, str]:
    """Run refresh_aegis_snapshot.main() in a daemon thread so sqlite3.backup()
    cannot block indefinitely when AegisTrader holds a write lock on the source DB.
    Returns (returncode, stdout, stderr)."""
    import refresh_aegis_snapshot as _ars

    result: list = [1, "", ""]

    def _worker() -> None:
        try:
            rc = _ars.main() or 0
            result[0] = rc
            result[1] = f"snapshot refreshed: {_ars.SNAPSHOT_DB}"
        except BaseException as exc:  # noqa: BLE001  catch SystemExit too
            result[0] = 1
            result[2] = str(exc)

    t = threading.Thread(target=_worker, daemon=True, name="aegis-backup")
    t.start()
    t.join(timeout=timeout_seconds)
    if t.is_alive():
        return -1, "", (
            f"backup did not complete within {timeout_seconds:.0f}s — "
            "AegisTrader may be holding a write lock on the source DB"
        )
    return result[0], result[1], result[2]


def refresh_aegis_snapshot_if_needed(max_age_minutes: int = 60) -> dict[str, Any]:
    started = time.time()
    should_refresh, reason = should_refresh_aegis_snapshot(max_age_minutes)
    result: dict[str, Any] = {
        "ran": False,
        "reason": reason,
        "source": str(AEGIS_SOURCE_DB),
        "snapshot": str(AEGIS_SNAPSHOT_DB),
    }
    if not should_refresh:
        result["elapsed_seconds"] = round(time.time() - started, 2)
        return result
    if not AEGIS_REFRESH_SCRIPT.exists():
        result.update({"error": "refresh_script_missing", "elapsed_seconds": round(time.time() - started, 2)})
        return result

    returncode, stdout, stderr = _run_aegis_main()
    result.update(
        {
            "ran": True,
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "elapsed_seconds": round(time.time() - started, 2),
        }
    )
    if returncode != 0:
        result["error"] = "timeout" if returncode == -1 else "refresh_failed"
    else:
        result["status"] = get_aegis_snapshot_status()
    return result


def force_refresh_aegis_snapshot() -> dict[str, Any]:
    if not AEGIS_REFRESH_SCRIPT.exists():
        return {"ran": False, "error": "refresh_script_missing", "script": str(AEGIS_REFRESH_SCRIPT)}
    started = time.time()
    returncode, stdout, stderr = _run_aegis_main()
    result: dict[str, Any] = {
        "ran": True,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "elapsed_seconds": round(time.time() - started, 2),
    }
    if returncode != 0:
        result["error"] = "timeout" if returncode == -1 else "refresh_failed"
    else:
        result["status"] = get_aegis_snapshot_status()
    return result


def get_db_status() -> dict[str, Any]:
    with db_connect() as conn:
        tables = {}
        for table in [
            "universe",
            "price_snapshots",
            "technical_signals",
            "news_items",
            "event_alerts",
            "agent_reports",
            "recommendations",
            "data_quality_snapshots",
            "user_decisions",
            "backtest_runs",
            "scan_runs",
            "opportunities",
            "recommendation_outcomes",
            "scheduler_runs",
            "active_etf_funds",
            "active_etf_changes",
            "active_etf_holdings",
            "active_etf_import_runs",
            "data_import_audits",
        ]:
            tables[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        latest = conn.execute(
            """
            SELECT symbol, captured_at, price, change_1d, score, action
            FROM price_snapshots
            ORDER BY captured_at DESC
            LIMIT 10
            """
        ).fetchall()
    return {
        "database": str(DB_PATH),
        "tables": tables,
        "latest_snapshots": [dict(row) for row in latest],
    }


def latest_scan_errors_by_symbol(max_runs: int = 200) -> dict[str, dict[str, Any]]:
    errors: dict[str, dict[str, Any]] = {}
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, results_json
            FROM scan_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, max_runs),),
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["results_json"] or "{}")
        except json.JSONDecodeError:
            continue
        for error in payload.get("errors") or []:
            symbol = str(error.get("symbol") or "").strip().upper()
            if not symbol or symbol in errors:
                continue
            errors[symbol] = {
                "scan_run_id": row["id"],
                "scan_created_at": row["created_at"],
                "message": error.get("message"),
            }
    return errors


def aegis_symbol_coverage(stock_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not stock_ids:
        return {}
    conn = aegis_connect()
    if conn is None:
        return {}
    coverage: dict[str, dict[str, Any]] = {
        stock_id: {
            "stock_master": False,
            "price_daily_rows": 0,
            "price_latest_date": None,
        }
        for stock_id in stock_ids
    }
    try:
        for stock_id in stock_ids:
            master = conn.execute(
                "SELECT 1 FROM stock_master WHERE stock_id = ? LIMIT 1",
                (stock_id,),
            ).fetchone()
            price = conn.execute(
                """
                SELECT COUNT(*) AS row_count, MAX(date) AS latest_date
                FROM price_daily
                WHERE stock_id = ?
                """,
                (stock_id,),
            ).fetchone()
            coverage[stock_id] = {
                "stock_master": bool(master),
                "price_daily_rows": int(price["row_count"] or 0) if price else 0,
                "price_latest_date": price["latest_date"] if price else None,
            }
    finally:
        conn.close()
    return coverage


def classify_unsnapped_gap(row: dict[str, Any], error: dict[str, Any] | None, aegis: dict[str, Any] | None) -> dict[str, str]:
    symbol = str(row.get("symbol") or "").upper()
    asset_type = str(row.get("asset_type") or "")
    message = str((error or {}).get("message") or "")
    has_aegis_price = bool(aegis and int(aegis.get("price_daily_rows") or 0) > 0)
    is_taiwan_asset = "台股" in asset_type or bool(taiwan_stock_id(symbol))
    is_plain_taiwan_code = bool(re.fullmatch(r"\d{4,6}", symbol))

    if has_aegis_price:
        reason = "aegis_price_available_but_not_used"
        action = "retry_with_aegis_chart_fallback"
    elif is_plain_taiwan_code and is_taiwan_asset:
        reason = "plain_taiwan_symbol_without_exchange_suffix"
        action = "normalize_to_tw_or_two_or_add_source_fallback"
    elif "404 Client Error" in message and "finance/chart" in message:
        reason = "yahoo_chart_404"
        action = "verify_symbol_mapping_or_delisting"
    elif "timed out" in message.lower() or "temporarily" in message.lower():
        reason = "transient_fetch_error"
        action = "retry_fixed_subset"
    elif message:
        reason = "scan_error"
        action = "inspect_error_message"
    else:
        reason = "not_scanned_or_no_recorded_error"
        action = "scan_fixed_subset_once"

    return {"reason": reason, "action": action}


def export_unsnapped_universe_report(payload: dict[str, Any]) -> dict[str, str]:
    ensure_export_dirs()
    created_at = utc_now()
    slug = f"unsnapped_universe_{timestamp_slug(created_at)}"
    md_path = EXPORT_DIR / "data_audits" / f"{slug}.md"
    latest_path = EXPORT_DIR / "data_audits" / "latest_unsnapped_universe.md"
    csv_path = EXPORT_DIR / "data_audits" / f"{slug}.csv"

    lines = [
        "# Unsnapped Universe Report",
        "",
        f"- generated_at: `{created_at}`",
        f"- total_unsnapped: `{payload.get('total_unsnapped')}`",
        f"- returned_count: `{len(payload.get('items') or [])}`",
        "",
        "## Summary By Asset Type",
        "",
    ]
    for item in payload.get("summary", {}).get("by_asset_type", []):
        lines.append(f"- {item['asset_type']}: `{item['count']}`")
    lines.extend(["", "## Summary By Reason", ""])
    for item in payload.get("summary", {}).get("by_reason", []):
        lines.append(f"- {item['reason']}: `{item['count']}`")
    lines.extend(["", "## Items", ""])
    for item in payload.get("items", []):
        error = item.get("last_error") or {}
        lines.append(
            f"- `{item.get('symbol')}` {item.get('name') or ''} | "
            f"asset_type=`{item.get('asset_type')}` | reason=`{item.get('reason')}` | "
            f"action=`{item.get('action')}` | scan_run=`{error.get('scan_run_id')}`"
        )

    content = "\n".join(lines).rstrip() + "\n"
    write_text_file(md_path, content)
    write_text_file(latest_path, content)

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "symbol",
                "name",
                "asset_type",
                "currency",
                "reason",
                "action",
                "aegis_price_daily_rows",
                "aegis_price_latest_date",
                "last_error_scan_run_id",
                "last_error_message",
            ],
        )
        writer.writeheader()
        for item in payload.get("items", []):
            aegis = item.get("aegis") or {}
            error = item.get("last_error") or {}
            writer.writerow(
                {
                    "symbol": item.get("symbol"),
                    "name": item.get("name"),
                    "asset_type": item.get("asset_type"),
                    "currency": item.get("currency"),
                    "reason": item.get("reason"),
                    "action": item.get("action"),
                    "aegis_price_daily_rows": aegis.get("price_daily_rows"),
                    "aegis_price_latest_date": aegis.get("price_latest_date"),
                    "last_error_scan_run_id": error.get("scan_run_id"),
                    "last_error_message": error.get("message"),
                }
            )
    return {"markdown": str(md_path), "latest_markdown": str(latest_path), "csv": str(csv_path)}


def get_unsnapped_universe_report(
    limit: int = 500,
    offset: int = 0,
    asset_type: str | None = None,
    export: bool = False,
) -> dict[str, Any]:
    limit = max(1, min(limit, 2000))
    offset = max(0, offset)
    params: list[Any] = []
    asset_filter = ""
    if asset_type:
        asset_filter = "AND u.asset_type = ?"
        params.append(asset_type)
    with db_connect() as conn:
        total = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM universe u
            WHERE u.enabled = 1
              {asset_filter}
              AND NOT EXISTS (SELECT 1 FROM price_snapshots ps WHERE ps.symbol = u.symbol)
            """,
            params,
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT u.symbol, u.name, u.asset_type, u.currency, u.sector, u.industry
            FROM universe u
            WHERE u.enabled = 1
              {asset_filter}
              AND NOT EXISTS (SELECT 1 FROM price_snapshots ps WHERE ps.symbol = u.symbol)
            ORDER BY u.asset_type, u.symbol
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()

    errors = latest_scan_errors_by_symbol()
    stock_ids = {stock_id for row in rows for stock_id in [taiwan_stock_id(row["symbol"])] if stock_id}
    aegis_coverage = aegis_symbol_coverage(stock_ids)
    items = []
    by_asset_type: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    by_action: dict[str, int] = {}
    for row in rows:
        item = dict(row)
        symbol = str(item["symbol"]).upper()
        stock_id = taiwan_stock_id(symbol)
        error = errors.get(symbol)
        aegis = aegis_coverage.get(stock_id or "")
        classification = classify_unsnapped_gap(item, error, aegis)
        item["last_error"] = error
        item["aegis"] = aegis
        item.update(classification)
        by_asset_type[item.get("asset_type") or "unknown"] = by_asset_type.get(item.get("asset_type") or "unknown", 0) + 1
        by_reason[item["reason"]] = by_reason.get(item["reason"], 0) + 1
        by_action[item["action"]] = by_action.get(item["action"], 0) + 1
        items.append(item)

    payload = {
        "generated_at": utc_now(),
        "total_unsnapped": int(total),
        "limit": limit,
        "offset": offset,
        "asset_type": asset_type,
        "summary": {
            "by_asset_type": [{"asset_type": key, "count": value} for key, value in sorted(by_asset_type.items())],
            "by_reason": [{"reason": key, "count": value} for key, value in sorted(by_reason.items())],
            "by_action": [{"action": key, "count": value} for key, value in sorted(by_action.items())],
        },
        "items": items,
        "note": "Classification uses local scan errors and AegisTrader snapshot coverage only; it does not call external market data sources.",
    }
    if export:
        payload["exports"] = export_unsnapped_universe_report(payload)
    return payload


def audit_financial_payload(item: dict[str, Any], snapshot_id: int | None = None, captured_at: str | None = None) -> dict[str, Any]:
    recommendation = item.get("recommendation") or {}
    factors = (recommendation.get("data_quality") or {}).get("factors") or {}
    value_quality = factors.get("value") or {}
    quality_quality = factors.get("quality") or {}
    fundamentals = item.get("fundamentals") or recommendation.get("fundamentals") or {}
    symbol = item.get("symbol")
    asset_type = item.get("asset_type")
    quote_type = item.get("quote_type")
    needs_financials = asset_type in {"美股/ETF", "台股"} and not is_fund_like_symbol(str(symbol or ""), quote_type)
    now = datetime.now(timezone.utc)

    stale_flags = []
    revenue_age = yyyymm_age_months(fundamentals.get("revenue_yyyymm"), now)
    if revenue_age is not None and revenue_age > 4:
        stale_flags.append(f"revenue_yyyymm older than 4 months ({fundamentals.get('revenue_yyyymm')})")
    eps_age = quarter_age_quarters(fundamentals.get("eps_year"), fundamentals.get("eps_quarter"), now)
    if eps_age is not None and eps_age > 3:
        stale_flags.append(f"eps_quarter older than 3 quarters ({fundamentals.get('eps_year')}Q{fundamentals.get('eps_quarter')})")

    critical_missing = []
    for field in ["pe", "eps_ttm"]:
        if field in value_quality.get("missing_fields", []):
            critical_missing.append(field)
    for field in ["eps", "revenue_yoy", "revenue_mom"]:
        if field in quality_quality.get("missing_fields", []):
            critical_missing.append(field)

    source = fundamentals.get("source") or value_quality.get("evidence", {}).get("source") or quality_quality.get("evidence", {}).get("source")
    if not needs_financials:
        status = "not_applicable"
    elif not source or (value_quality.get("confidence") == "none" and quality_quality.get("confidence") == "none"):
        status = "block"
    elif stale_flags:
        status = "warn"
    elif critical_missing and (value_quality.get("confidence") in {"none", "low"} or quality_quality.get("confidence") in {"none", "low"}):
        status = "warn"
    else:
        status = "pass"

    return {
        "snapshot_id": snapshot_id,
        "captured_at": captured_at or item.get("generated_at"),
        "symbol": symbol,
        "name": item.get("name"),
        "asset_type": asset_type,
        "quote_type": quote_type,
        "needs_financials": needs_financials,
        "status": status,
        "source": source,
        "fundamentals": fundamentals,
        "value": {
            "coverage": value_quality.get("coverage"),
            "confidence": value_quality.get("confidence"),
            "source_status": value_quality.get("source_status"),
            "used_fields": value_quality.get("used_fields", {}),
            "missing_fields": value_quality.get("missing_fields", []),
        },
        "quality": {
            "coverage": quality_quality.get("coverage"),
            "confidence": quality_quality.get("confidence"),
            "source_status": quality_quality.get("source_status"),
            "used_fields": quality_quality.get("used_fields", {}),
            "missing_fields": quality_quality.get("missing_fields", []),
        },
        "critical_missing": sorted(set(critical_missing)),
        "stale_flags": stale_flags,
        "data_gate": recommendation.get("data_gate"),
    }


def get_financial_data_audit(limit: int = 100, symbol: str | None = None) -> dict[str, Any]:
    limit = max(1, min(limit, 500))
    params: list[Any] = []
    where = ""
    if symbol:
        where = "WHERE symbol = ?"
        params.append(symbol.strip().upper())
    params.append(limit)
    with db_connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, symbol, captured_at, raw_json
            FROM price_snapshots
            {where}
            ORDER BY captured_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    records = []
    counts: dict[str, int] = {}
    for row in rows:
        try:
            item = json.loads(row["raw_json"] or "{}")
        except json.JSONDecodeError:
            continue
        audit = audit_financial_payload(item, row["id"], row["captured_at"])
        records.append(audit)
        counts[audit["status"]] = counts.get(audit["status"], 0) + 1
    return {
        "generated_at": utc_now(),
        "records": records,
        "count": len(records),
        "status_counts": counts,
        "rules": FINANCIAL_AUDIT_RULES,
        "rule": "Financial data must be sourced, current enough for its field type, and explicitly marked when partial or not applicable.",
    }


def get_data_quality_history(symbol: str | None = None, limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(limit, 500))
    params: list[Any] = []
    where = ""
    if symbol:
        where = "WHERE symbol = ?"
        params.append(symbol.strip().upper())
    params.append(limit)
    with db_connect() as conn:
        rows = conn.execute(
            f"""
            SELECT snapshot_id, symbol, captured_at, factor, factor_label, coverage, confidence,
                   source_status, required_fields_json, used_fields_json, missing_fields_json, evidence_json
            FROM data_quality_snapshots
            {where}
            ORDER BY captured_at DESC, factor
            LIMIT ?
            """,
            params,
        ).fetchall()
    records = []
    for row in rows:
        records.append(
            {
                "snapshot_id": row["snapshot_id"],
                "symbol": row["symbol"],
                "captured_at": row["captured_at"],
                "factor": row["factor"],
                "factor_label": row["factor_label"],
                "coverage": row["coverage"],
                "confidence": row["confidence"],
                "source_status": row["source_status"],
                "required_fields": json.loads(row["required_fields_json"] or "[]"),
                "used_fields": json.loads(row["used_fields_json"] or "{}"),
                "missing_fields": json.loads(row["missing_fields_json"] or "[]"),
                "evidence": json.loads(row["evidence_json"] or "{}"),
            }
        )
    return {"records": records, "count": len(records)}


def health_status_rank(status: str) -> int:
    return {"ok": 0, "warn": 1, "block": 2}.get(status, 1)


def get_no_quote_remediation() -> dict[str, Any]:
    gaps = get_tw_full_market_quote_gaps()
    items = []
    summary: dict[str, int] = {}
    for item in gaps.get("items") or []:
        reason = item.get("reason") or "unknown"
        if reason in {"missing_tpex_emerging_quote"}:
            disposition = "keep_excluded"
            suggested_action = "Keep in membership; exclude from ranking until TPEx emerging quote support or official quote appears."
        elif reason == "missing_etf_quote":
            disposition = "manual_review"
            suggested_action = "Verify ETF listing/quote source. If active, add or fix quote adapter; if delisted, mark excluded."
        elif reason == "future_listing_no_quote":
            disposition = "keep_excluded"
            suggested_action = "Keep in membership and exclude from ranking until the listing start date and first official quote arrive."
        elif reason.endswith("needs_review"):
            disposition = "fix_mapping"
            suggested_action = "Review symbol and source_symbol mapping against Aegis stock_master."
        else:
            disposition = "manual_review"
            suggested_action = "Check official exchange status, suspension, delisting, or Yahoo/Aegis symbol support."
        row = {
            "symbol": item.get("symbol"),
            "name": item.get("name"),
            "market": item.get("market"),
            "instrument_type": item.get("instrument_type"),
            "reason": reason,
            "manual_review_priority": item.get("manual_review_priority"),
            "metadata": item.get("metadata"),
            "disposition": disposition,
            "suggested_action": suggested_action,
            "keep_in_universe": disposition != "remove",
            "exclude_from_ranking": True,
        }
        items.append(row)
        summary[disposition] = summary.get(disposition, 0) + 1
    summary_rows = [{"disposition": key, "count": value} for key, value in sorted(summary.items())]
    return {
        "generated_at": utc_now(),
        "scope": TW_FULL_MARKET_SCOPE,
        "count": len(items),
        "summary": summary_rows,
        "by_disposition": dict(sorted(summary.items())),
        "items": items,
        "note": "No-quote symbols are tracked as remediation items; they are not fabricated into rankings.",
    }


def data_health_actions(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for check in checks:
        name = check.get("name")
        status = check.get("status")
        detail = check.get("detail") or {}
        if status == "ok":
            continue
        if name == "full_market_coverage":
            no_quote = int(detail.get("no_quote_count") or 0)
            remediation = get_no_quote_remediation()
            by_disposition = remediation.get("by_disposition") or {}
            unresolved = int(by_disposition.get("manual_review") or 0) + int(by_disposition.get("fix_mapping") or 0)
            next_step = (
                "Review /api/universe/tw-full-market/gaps/remediation and resolve manual-review or mapping items."
                if unresolved
                else "No manual no-quote remediation remains; keep excluded symbols out of ranking until official quotes or source support are available."
            )
            actions.append(
                {
                    "area": name,
                    "severity": "warn",
                    "reason": "no_quote_gap",
                    "message": f"{no_quote} symbols are excluded from ranking until quotes are available.",
                    "next_step": next_step,
                    "decision_impact": "shortlist remains usable, but coverage is not complete",
                    "classified_count": remediation.get("count"),
                    "by_disposition": by_disposition,
                }
            )
        elif name == "daily_recommendation_log":
            actions.append(
                {
                    "area": name,
                    "severity": "warn",
                    "reason": "daily_log_due",
                    "message": "Daily recommendation log is due but missing.",
                    "next_step": "Run /api/recommendations/daily-log?force=1&limit=50 after 16:10 Asia/Taipei.",
                    "decision_impact": "historical tracking for the day is incomplete",
                }
            )
        elif name == "recommendation_outcomes":
            actions.append(
                {
                    "area": name,
                    "severity": "warn",
                    "reason": "missing_outcome_prices",
                    "message": check.get("message") or "Some due outcomes are missing prices.",
                    "next_step": "Review /api/recommendation/outcomes/missing-prices and refresh stale symbols or mark unsupported instruments.",
                    "decision_impact": "performance attribution is incomplete",
                }
            )
        elif name == "active_etf":
            actions.append(
                {
                    "area": name,
                    "severity": "warn",
                    "reason": "active_etf_source_stale_or_third_party",
                    "message": check.get("message") or "Active ETF source needs validation.",
                    "next_step": "Treat ZDS as secondary evidence until an official issuer/exchange source is attached.",
                    "decision_impact": "ETF flow should not be a sole buy/sell reason",
                }
            )
        else:
            actions.append(
                {
                    "area": name,
                    "severity": status or "warn",
                    "reason": "health_check_not_ok",
                    "message": check.get("message") or "Health check is not OK.",
                    "next_step": "Review the check detail before using current shortlist output.",
                    "decision_impact": "data readiness may be impaired",
                }
            )
    return actions


def get_data_health() -> dict[str, Any]:
    from scan import read_daily_recommendation_log_history  # circular dep break
    now = datetime.now(timezone.utc)
    local_now = now.astimezone(TAIPEI_TZ)
    checks: list[dict[str, Any]] = []

    def add_check(name: str, status: str, message: str, detail: dict[str, Any] | None = None) -> None:
        checks.append({"name": name, "status": status, "message": message, "detail": detail or {}})

    scheduler = scheduler_state(repair=True)
    if not scheduler.get("enabled") or not scheduler.get("thread_alive") or not scheduler.get("healthy"):
        add_check("scheduler", "block", "Background scheduler is not healthy.", scheduler)
    elif scheduler.get("scope") != TW_FULL_MARKET_SCOPE:
        add_check("scheduler", "block", "Scheduler is not scanning the Taiwan full-market scope.", scheduler)
    else:
        add_check("scheduler", "ok", "Scheduler is healthy and scoped to Taiwan full market.", scheduler)

    aegis = get_aegis_snapshot_status()
    if not aegis.get("available") or aegis.get("stale"):
        add_check("aegis_snapshot", "block", "Aegis snapshot is missing or stale.", aegis)
    else:
        copied_age = safe_float(aegis.get("copied_age_hours"))
        status = "warn" if copied_age is not None and copied_age > 6 else "ok"
        add_check("aegis_snapshot", status, "Aegis snapshot is available.", aegis)

    full_market = get_tw_full_market_status()
    coverage = safe_float(full_market.get("snapshot_coverage_percent")) or 0
    no_quote = int(full_market.get("no_quote_count") or 0)
    if coverage < 95:
        add_check("full_market_coverage", "block", "Full-market snapshot coverage is below 95%.", full_market)
    elif no_quote:
        add_check("full_market_coverage", "warn", f"{no_quote} symbols have no tradable quote.", full_market)
    else:
        add_check("full_market_coverage", "ok", "Full-market quote coverage is complete.", full_market)

    active_etf_status = get_active_etf_source_status()
    etf_stale = bool(active_etf_status.get("stale"))
    add_check(
        "active_etf",
        "warn" if etf_stale else "ok",
        "Active ETF data is stale." if etf_stale else "Active ETF data is available.",
        active_etf_status,
    )

    daily_history = read_daily_recommendation_log_history(limit=3)
    latest_record = (daily_history.get("records") or [{}])[0] if daily_history.get("records") else {}
    daily_detail = {
        "path": daily_history.get("path"),
        "log_count": daily_history.get("log_count"),
        "parse_errors": daily_history.get("parse_errors"),
        "summary": daily_history.get("summary"),
        "latest_date": latest_record.get("date"),
        "latest_generated_at": latest_record.get("generated_at"),
    }
    today = local_now.strftime("%Y-%m-%d")
    daily_due = local_now.strftime("%H:%M") >= DAILY_RECOMMENDATION_LOG_TIME
    if latest_record.get("date") == today:
        add_check("daily_recommendation_log", "ok", "Today's daily recommendation log exists.", daily_detail)
    elif daily_due:
        add_check("daily_recommendation_log", "warn", "Today's daily recommendation log is due but not present yet.", daily_detail)
    else:
        add_check("daily_recommendation_log", "ok", "Today's daily recommendation log is not due yet.", daily_detail)

    outcome_update = update_recommendation_outcomes(limit=500)
    pending = int(outcome_update.get("pending") or 0)
    missing_price = int(outcome_update.get("missing_price") or 0)
    if missing_price:
        add_check("recommendation_outcomes", "warn", f"{missing_price} due outcomes are missing prices.", outcome_update)
    else:
        add_check("recommendation_outcomes", "ok", f"Outcome tracker checked; {pending} outcomes are still pending.", outcome_update)

    overall = max(checks, key=lambda row: health_status_rank(row["status"]))["status"] if checks else "warn"
    blocking_checks = [row["name"] for row in checks if row.get("status") == "block"]
    warning_checks = [row["name"] for row in checks if row.get("status") == "warn"]
    contained_warning_names = {"full_market_coverage"}
    contained_warnings = [name for name in warning_checks if name in contained_warning_names]
    uncontained_warnings = [name for name in warning_checks if name not in contained_warning_names]
    ranking_readiness = not blocking_checks and not uncontained_warnings
    return {
        "generated_at": utc_now(),
        "timezone": "Asia/Taipei",
        "overall_status": overall,
        "decision_readiness": overall == "ok",
        "readiness": {
            "strict_decision_readiness": overall == "ok",
            "ranking_readiness": ranking_readiness,
            "blocking_checks": blocking_checks,
            "contained_warnings": contained_warnings,
            "uncontained_warnings": uncontained_warnings,
            "ranking_note": (
                "Ranking output is usable because remaining warnings are contained by exclusion policy."
                if ranking_readiness and warning_checks
                else "Ranking output has no active data-health warnings."
                if ranking_readiness
                else "Ranking output needs review before use."
            ),
        },
        "checks": checks,
        "actions": data_health_actions(checks),
        "note": "Use warn/block states as data-readiness gates before treating shortlist output as current research input.",
    }


def pre_trade_checklist_for_candidate(item: dict[str, Any], health: dict[str, Any] | None = None) -> dict[str, Any]:
    health = health or get_data_health()
    checks = []

    def add(name: str, status: str, message: str) -> None:
        checks.append({"name": name, "status": status, "message": message})

    add(
        "data_readiness",
        "pass" if health.get("overall_status") in {"ok", "warn"} else "block",
        f"Data health is {health.get('overall_status')}.",
    )
    add("tier", "pass" if item.get("tier") == "core_watch" else "warn", f"Tier is {item.get('tier') or '-'}.")
    add("spike_risk", "warn" if item.get("spike_risk") else "pass", "Recent 1D move is hot." if item.get("spike_risk") else "No 1D spike risk flag.")
    change_5d = safe_float(item.get("change_5d"))
    add(
        "five_day_heat",
        "warn" if change_5d is not None and abs(change_5d) >= 12 else "pass",
        f"5D move is {round(change_5d, 2) if change_5d is not None else '-'}%.",
    )
    financial = item.get("financial_audit_status")
    if financial == "block":
        financial_status = "block"
    elif financial == "warn":
        financial_status = "warn"
    else:
        financial_status = "pass"
    add("financial_audit", financial_status, f"Financial audit status is {financial or '-'}.")
    volume = safe_float(item.get("volume")) or 0
    add("liquidity", "pass" if volume >= 500_000 else "block", f"Volume is {int(volume)}.")
    confidence = item.get("data_confidence")
    coverage = safe_float(item.get("data_coverage")) or 0
    add("data_quality", "pass" if confidence == "high" and coverage >= 0.75 else "block", f"Data confidence {confidence}, coverage {round(coverage, 4)}.")
    if item.get("instrument_type") == "ETF":
        add("etf_context", "warn", "ETF candidate: validate constituents, premium/discount, and issuer disclosure manually.")
    data_policy = item.get("data_policy") if isinstance(item.get("data_policy"), dict) else {}
    if data_policy.get("attempted_sources"):
        third_party = any(
            "zdsetf" in str(source).lower() or "third" in str(source).lower()
            for source in data_policy.get("attempted_sources", [])
        )
        if third_party:
            add("third_party_evidence", "warn", "Contains third-party evidence; manually validate before sizing.")

    rank = {"pass": 0, "warn": 1, "block": 2}
    overall = max(checks, key=lambda row: rank.get(row["status"], 1))["status"] if checks else "warn"
    return {
        "symbol": item.get("symbol"),
        "generated_at": utc_now(),
        "overall_status": overall,
        "trade_ready": overall == "pass",
        "checks": checks,
        "note": "This checklist is a research gate, not an order instruction.",
    }


def get_pre_trade_checklist(symbol: str | None = None, limit: int = 12) -> dict[str, Any]:
    from app import get_tw_full_market_research_shortlist  # circular dep break
    shortlist = get_tw_full_market_research_shortlist(limit=100)
    candidates = shortlist.get("shortlist") or []
    health = get_data_health()
    if symbol:
        normalized = normalize_tw_stock_symbol(symbol)
        candidates = [item for item in candidates if item.get("symbol") == normalized]
    candidates = candidates[: max(1, min(limit, 50))]
    return {
        "generated_at": utc_now(),
        "symbol": normalize_tw_stock_symbol(symbol) if symbol else None,
        "count": len(candidates),
        "data_health": {"overall_status": health.get("overall_status"), "decision_readiness": health.get("decision_readiness")},
        "items": [pre_trade_checklist_for_candidate(item, health) for item in candidates],
    }
