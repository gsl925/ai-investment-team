from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from common import (
    ACTIVE_ETF_IMPORT_SLOTS,
    ACTIVE_ETF_IMPORT_STATE,
    HTTP,
    HTTP_TIMEOUT,
    ROOT,
    TAIPEI_TZ,
    YAHOO_HEADERS,
    db_connect,
    record_data_import_audit,
    safe_float,
    utc_now,
)
from tw_universe import normalize_tw_stock_symbol


def active_etf_signal_score(change_type: str, share_delta: float | None, weight: float | None) -> int:
    kind = change_type.strip()
    score = 50
    if kind in {"新增", "加碼", "買進", "increase", "add", "new"}:
        score += 20
    elif kind in {"減碼", "刪除", "賣出", "decrease", "remove", "sell"}:
        score -= 20
    if share_delta is not None:
        if share_delta > 0:
            score += min(15, int(abs(share_delta) // 100000))
        elif share_delta < 0:
            score -= min(15, int(abs(share_delta) // 100000))
    if weight is not None and weight >= 3:
        score += 8 if score >= 50 else -8
    return max(0, min(100, score))


def active_etf_evidence(source: str | None) -> dict[str, str]:
    value = (source or "").strip()
    lower = value.lower()
    if not value:
        return {"status": "unverified", "label": "無來源", "note": "這筆資料沒有來源欄位，不能作為可靠投研證據。"}
    if lower in {"manual_import", "demo_manual_unverified"}:
        return {"status": "unverified", "label": "手動/示範", "note": "這筆資料是手動或示範匯入，尚未連到官方或第三方來源驗證。"}
    if lower.startswith(("http://", "https://")):
        official = any(domain in lower for domain in ["twse.com.tw", "tpex.org.tw"])
        return {
            "status": "verified" if official else "third_party",
            "label": "官方來源" if official else "第三方來源",
            "note": "來源 URL 已保存，仍需人工覆核原始頁面與揭露日期。",
        }
    return {"status": "third_party", "label": value, "note": "這筆資料有非空來源標籤，但不是 URL；需人工確認來源檔案或供應者。"}


def active_etf_change_type(previous_shares: float | None, current_shares: float | None) -> str | None:
    previous = previous_shares or 0
    current = current_shares or 0
    if previous <= 0 and current > 0:
        return "new"
    if previous > 0 and current <= 0:
        return "remove"
    if current > previous:
        return "increase"
    if current < previous:
        return "decrease"
    return None


def active_etf_change_label(change_type: str) -> str:
    labels = {
        "new": "新增",
        "increase": "加碼",
        "decrease": "減碼",
        "remove": "刪除",
    }
    return labels.get(change_type, change_type)


def active_etf_source_value(source: str | None, source_url: str | None) -> str | None:
    return (source_url or "").strip() or (source or "").strip() or None


def html_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = re.sub(r"\s+", " ", text)
    return (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .strip()
    )


def parse_numeric_text(value: str | None) -> float | None:
    if value is None:
        return None
    text = html_text(str(value)).replace(",", "").replace("—", "").strip()
    if not text:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return safe_float(match.group(0)) if match else None


def parse_market_value_text(value: str | None) -> float | None:
    if value is None:
        return None
    text = html_text(str(value)).replace(",", "")
    number = parse_numeric_text(text)
    if number is None:
        return None
    if "億" in text:
        return number * 100_000_000
    if "萬" in text:
        return number * 10_000
    return number


def parse_etf_option_list(html: str) -> list[dict[str, str]]:
    funds: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(r'<option value="([0-9]{5}A|[0-9]{4}A)">([^<]+)', html):
        symbol = match.group(1).strip().upper()
        if symbol in seen:
            continue
        label = html_text(match.group(2))
        name = label.split("·", 1)[1].strip() if "·" in label else label.strip()
        funds.append({"etf_symbol": symbol, "etf_name": name})
        seen.add(symbol)
    return funds


def parse_html_table_rows(section_html: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row_match in re.finditer(r"<tr[^>]*>(.*?)</tr>", section_html, re.S):
        cells = [html_text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_match.group(1), re.S)]
        if cells:
            rows.append(cells)
    return rows


def parse_zdsetf_detail(html: str, etf_symbol: str, fallback_name: str = "") -> dict[str, Any]:
    title_match = re.search(r"<title>(.*?)</title>", html, re.S)
    title = html_text(title_match.group(1)) if title_match else ""
    name = fallback_name
    if title and etf_symbol in title:
        name = title.split(etf_symbol, 1)[1].split("持股追蹤", 1)[0].strip() or fallback_name
    issuer_match = re.search(r'"provider"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html)
    issuer = issuer_match.group(1).strip() if issuer_match else None
    date_match = re.search(r'<input type="date"[^>]*value="(\d{4}-\d{2}-\d{2})"', html)
    if not date_match:
        date_match = re.search(r'<span class="muted">(\d{4}-\d{2}-\d{2})</span>', html)
    trade_date = date_match.group(1) if date_match else datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")

    holdings: list[dict[str, Any]] = []
    all_match = re.search(
        r'<div class="tab-pane" data-tab="all"[^>]*>(.*?)(?=<div class="tab-pane" data-tab=|</div>\s*</section>)',
        html,
        re.S,
    )
    if all_match:
        for cells in parse_html_table_rows(all_match.group(1)):
            if len(cells) < 5:
                continue
            holdings.append(
                {
                    "trade_date": trade_date,
                    "etf_symbol": etf_symbol,
                    "etf_name": name,
                    "issuer": issuer,
                    "stock_symbol": normalize_tw_stock_symbol(cells[0]),
                    "stock_name": cells[1],
                    "shares": parse_numeric_text(cells[2]),
                    "weight": parse_numeric_text(cells[3]),
                    "market_value": parse_market_value_text(cells[4]),
                }
            )

    changes: list[dict[str, Any]] = []
    tab_map = {"new": "new", "increase": "increase", "decrease": "decrease", "removed": "remove"}
    for tab, change_type in tab_map.items():
        tab_match = re.search(
            r'<div class="tab-pane" data-tab="' + re.escape(tab) + r'"[^>]*>(.*?)(?=<div class="tab-pane" data-tab=|</div>\s*</section>)',
            html,
            re.S,
        )
        if not tab_match:
            continue
        for cells in parse_html_table_rows(tab_match.group(1)):
            if len(cells) < 5:
                continue
            previous_shares = parse_numeric_text(cells[2])
            current_shares = parse_numeric_text(cells[3])
            share_delta = None
            if previous_shares is not None and current_shares is not None:
                share_delta = current_shares - previous_shares
            estimated_price = parse_numeric_text(cells[5]) if len(cells) >= 7 else None
            estimated_value = parse_market_value_text(cells[-1])
            changes.append(
                {
                    "trade_date": trade_date,
                    "etf_symbol": etf_symbol,
                    "etf_name": name,
                    "issuer": issuer,
                    "stock_symbol": normalize_tw_stock_symbol(cells[0]),
                    "stock_name": cells[1],
                    "change_type": change_type,
                    "previous_shares": previous_shares,
                    "current_shares": current_shares,
                    "share_delta": share_delta,
                    "estimated_price": estimated_price,
                    "estimated_value": estimated_value,
                }
            )
    return {"trade_date": trade_date, "etf_symbol": etf_symbol, "etf_name": name, "issuer": issuer, "holdings": holdings, "changes": changes}


def import_active_etf_zdsetf(max_etfs: int | None = None) -> dict[str, Any]:
    base_url = "https://www.zdsetf.com"
    captured_at = utc_now()
    warnings: list[dict[str, Any]] = []
    errors: list[str] = []
    source_label = "ZDS ETF Tracker"
    home = HTTP.get(
        f"{base_url}/",
        headers=YAHOO_HEADERS,
        timeout=HTTP_TIMEOUT,
    )
    home.raise_for_status()
    funds = parse_etf_option_list(home.text)
    if max_etfs:
        funds = funds[: max(1, max_etfs)]
    if not funds:
        raise ValueError("No active ETF symbols found from ZDS ETF Tracker.")

    inserted_holdings = updated_holdings = 0
    inserted_changes = updated_changes = 0
    skipped = 0
    dates: set[str] = set()
    stock_symbols: set[str] = set()
    by_change: dict[str, int] = {}
    imported_funds: list[dict[str, Any]] = []

    with db_connect() as conn:
        for fund in funds:
            etf_symbol = fund["etf_symbol"]
            detail_url = f"{base_url}/etf/{etf_symbol}"
            try:
                response = HTTP.get(detail_url, headers=YAHOO_HEADERS, timeout=HTTP_TIMEOUT)
                response.raise_for_status()
                parsed = parse_zdsetf_detail(response.text, etf_symbol, fund.get("etf_name", ""))
            except Exception as exc:
                errors.append(f"{etf_symbol}: {exc}")
                continue

            etf_name = parsed["etf_name"] or fund.get("etf_name")
            issuer = parsed.get("issuer")
            dates.add(parsed["trade_date"])
            imported_funds.append(
                {
                    "etf_symbol": etf_symbol,
                    "etf_name": etf_name,
                    "issuer": issuer,
                    "trade_date": parsed["trade_date"],
                    "holdings": len(parsed["holdings"]),
                    "changes": len(parsed["changes"]),
                    "source_url": detail_url,
                }
            )
            conn.execute(
                """
                INSERT INTO active_etf_funds (
                    etf_symbol, etf_name, issuer, market, enabled, source_url, created_at, updated_at
                )
                VALUES (?, ?, ?, 'TW', 1, ?, ?, ?)
                ON CONFLICT(etf_symbol) DO UPDATE SET
                    etf_name = excluded.etf_name,
                    issuer = excluded.issuer,
                    source_url = excluded.source_url,
                    updated_at = excluded.updated_at
                """,
                (etf_symbol, etf_name, issuer, detail_url, captured_at, captured_at),
            )

            for row in parsed["holdings"]:
                stock_symbols.add(row["stock_symbol"])
                existed = conn.execute(
                    """
                    SELECT 1 FROM active_etf_holdings
                    WHERE trade_date = ? AND etf_symbol = ? AND stock_symbol = ?
                    """,
                    (row["trade_date"], etf_symbol, row["stock_symbol"]),
                ).fetchone()
                raw = dict(row)
                raw["source_url"] = detail_url
                raw["source_label"] = source_label
                conn.execute(
                    """
                    INSERT INTO active_etf_holdings (
                        trade_date, etf_symbol, etf_name, issuer, stock_symbol, stock_name,
                        shares, weight, market_value, source, source_url, captured_at, raw_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(trade_date, etf_symbol, stock_symbol) DO UPDATE SET
                        etf_name = excluded.etf_name,
                        issuer = excluded.issuer,
                        stock_name = excluded.stock_name,
                        shares = excluded.shares,
                        weight = excluded.weight,
                        market_value = excluded.market_value,
                        source = excluded.source,
                        source_url = excluded.source_url,
                        captured_at = excluded.captured_at,
                        raw_json = excluded.raw_json
                    """,
                    (
                        row["trade_date"],
                        etf_symbol,
                        etf_name,
                        issuer,
                        row["stock_symbol"],
                        row["stock_name"],
                        row["shares"],
                        row["weight"],
                        row["market_value"],
                        source_label,
                        detail_url,
                        captured_at,
                        json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
                if existed:
                    updated_holdings += 1
                else:
                    inserted_holdings += 1
                conn.execute(
                    """
                    INSERT INTO universe (symbol, name, asset_type, currency, sector, industry, enabled, created_at, updated_at)
                    VALUES (?, ?, '台股', 'TWD', '主動式ETF持股', ?, 1, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        name = COALESCE(NULLIF(excluded.name, ''), universe.name),
                        updated_at = excluded.updated_at
                    """,
                    (row["stock_symbol"], row["stock_name"], etf_symbol, captured_at, captured_at),
                )

            for row in parsed["changes"]:
                stock_symbols.add(row["stock_symbol"])
                by_change[row["change_type"]] = by_change.get(row["change_type"], 0) + 1
                score = active_etf_signal_score(row["change_type"], row["share_delta"], None)
                label = active_etf_change_label(row["change_type"])
                thesis = (
                    f"{etf_symbol} {row['trade_date']} {row['stock_symbol']} {label}; "
                    f"shares {row['previous_shares']} -> {row['current_shares']}. Source: {detail_url}"
                )
                raw = dict(row)
                raw["source_url"] = detail_url
                raw["source_label"] = source_label
                existed = conn.execute(
                    """
                    SELECT 1 FROM active_etf_changes
                    WHERE trade_date = ? AND etf_symbol = ? AND stock_symbol = ? AND change_type = ?
                    """,
                    (row["trade_date"], etf_symbol, row["stock_symbol"], row["change_type"]),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO active_etf_changes (
                        trade_date, etf_symbol, etf_name, stock_symbol, stock_name, change_type,
                        previous_shares, current_shares, share_delta, weight, estimated_price,
                        estimated_value, source, captured_at, signal_score, thesis, raw_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(trade_date, etf_symbol, stock_symbol, change_type) DO UPDATE SET
                        etf_name = excluded.etf_name,
                        stock_name = excluded.stock_name,
                        previous_shares = excluded.previous_shares,
                        current_shares = excluded.current_shares,
                        share_delta = excluded.share_delta,
                        estimated_price = excluded.estimated_price,
                        estimated_value = excluded.estimated_value,
                        source = excluded.source,
                        captured_at = excluded.captured_at,
                        signal_score = excluded.signal_score,
                        thesis = excluded.thesis,
                        raw_json = excluded.raw_json
                    """,
                    (
                        row["trade_date"],
                        etf_symbol,
                        etf_name,
                        row["stock_symbol"],
                        row["stock_name"],
                        row["change_type"],
                        row["previous_shares"],
                        row["current_shares"],
                        row["share_delta"],
                        row["estimated_price"],
                        row["estimated_value"],
                        detail_url,
                        captured_at,
                        score,
                        thesis,
                        json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
                if existed:
                    updated_changes += 1
                else:
                    inserted_changes += 1

        total_holdings = conn.execute("SELECT COUNT(*) FROM active_etf_holdings").fetchone()[0]
        total_changes = conn.execute("SELECT COUNT(*) FROM active_etf_changes").fetchone()[0]

    result = {
        "source": base_url,
        "source_label": source_label,
        "source_evidence": active_etf_evidence(base_url),
        "fund_count": len(imported_funds),
        "inserted_holdings": inserted_holdings,
        "updated_holdings": updated_holdings,
        "inserted_changes": inserted_changes,
        "updated_changes": updated_changes,
        "skipped": skipped,
        "dates": sorted(dates),
        "by_change": by_change,
        "total_holdings": total_holdings,
        "total_changes": total_changes,
        "funds": imported_funds,
        "note": "ZDS ETF Tracker is a third-party public source; records are stored with source URLs for verification.",
    }
    audit = record_data_import_audit(
        {
            "dataset": "active_etf_zdsetf",
            "source_file": None,
            "source_url": base_url,
            "source_label": source_label,
            "imported_at": captured_at,
            "status": "warning" if errors else "ok",
            "row_count": inserted_holdings + updated_holdings + inserted_changes + updated_changes,
            "inserted": inserted_holdings + inserted_changes,
            "updated": updated_holdings + updated_changes,
            "skipped": skipped,
            "fields": [
                "trade_date",
                "etf_symbol",
                "stock_symbol",
                "stock_name",
                "shares",
                "weight",
                "market_value",
                "change_type",
                "previous_shares",
                "current_shares",
                "share_delta",
                "estimated_price",
                "estimated_value",
                "source_url",
            ],
            "warnings": warnings,
            "errors": errors,
            "stats": {
                "dates": sorted(dates),
                "fund_count": len(imported_funds),
                "stock_count": len(stock_symbols),
                "by_change": by_change,
                "funds": imported_funds,
            },
            "result": result,
        }
    )
    result["audit"] = {
        "id": audit["id"],
        "status": audit["status"],
        "warning_count": len(warnings),
        "error_count": len(errors),
        "markdown_path": audit["markdown_path"],
    }
    return result


def validate_active_etf_holding_row(row: dict[str, Any], row_number: int) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    trade_date = (row.get("trade_date") or "").strip()
    etf_symbol = (row.get("etf_symbol") or "").strip()
    stock_symbol = (row.get("stock_symbol") or "").strip()
    shares = safe_float(row.get("shares") or row.get("current_shares"))
    weight = safe_float(row.get("weight"))
    market_value = safe_float(row.get("market_value") or row.get("estimated_value"))
    source_value = active_etf_source_value(row.get("source"), row.get("source_url"))

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", trade_date):
        warnings.append({"row": row_number, "field": "trade_date", "value": trade_date, "message": "trade_date should be YYYY-MM-DD."})
    if not etf_symbol:
        warnings.append({"row": row_number, "field": "etf_symbol", "value": etf_symbol, "message": "ETF symbol is required."})
    if not stock_symbol:
        warnings.append({"row": row_number, "field": "stock_symbol", "value": stock_symbol, "message": "Stock symbol is required."})
    if shares is None:
        warnings.append({"row": row_number, "field": "shares", "value": row.get("shares"), "message": "shares is empty or not numeric."})
    elif shares < 0:
        warnings.append({"row": row_number, "field": "shares", "value": shares, "message": "shares should not be negative."})
    if weight is not None and (weight < 0 or weight > 100):
        warnings.append({"row": row_number, "field": "weight", "value": weight, "message": "weight should be between 0 and 100."})
    if market_value is not None and market_value < 0:
        warnings.append({"row": row_number, "field": "market_value", "value": market_value, "message": "market_value should not be negative."})
    if active_etf_evidence(source_value)["status"] == "unverified":
        warnings.append({"row": row_number, "field": "source", "value": source_value, "message": "source is unverified; keep for record but do not treat as proven."})
    return warnings


def rebuild_active_etf_changes_from_holdings(trade_date: str) -> dict[str, Any]:
    captured_at = utc_now()
    inserted = 0
    updated = 0
    skipped = 0
    by_change: dict[str, int] = {}
    with db_connect() as conn:
        etfs = conn.execute(
            "SELECT DISTINCT etf_symbol FROM active_etf_holdings WHERE trade_date = ? ORDER BY etf_symbol",
            (trade_date,),
        ).fetchall()
        for etf_row in etfs:
            etf_symbol = etf_row["etf_symbol"]
            previous_date_row = conn.execute(
                """
                SELECT MAX(trade_date) AS previous_date
                FROM active_etf_holdings
                WHERE etf_symbol = ? AND trade_date < ?
                """,
                (etf_symbol, trade_date),
            ).fetchone()
            previous_date = previous_date_row["previous_date"] if previous_date_row else None
            if not previous_date:
                skipped += 1
                continue

            current_rows = conn.execute(
                "SELECT * FROM active_etf_holdings WHERE etf_symbol = ? AND trade_date = ?",
                (etf_symbol, trade_date),
            ).fetchall()
            previous_rows = conn.execute(
                "SELECT * FROM active_etf_holdings WHERE etf_symbol = ? AND trade_date = ?",
                (etf_symbol, previous_date),
            ).fetchall()
            current_by_symbol = {row["stock_symbol"]: row for row in current_rows}
            previous_by_symbol = {row["stock_symbol"]: row for row in previous_rows}

            for stock_symbol in sorted(set(current_by_symbol) | set(previous_by_symbol)):
                current = current_by_symbol.get(stock_symbol)
                previous = previous_by_symbol.get(stock_symbol)
                current_shares = current["shares"] if current else 0
                previous_shares = previous["shares"] if previous else 0
                change_type = active_etf_change_type(previous_shares, current_shares)
                if not change_type:
                    continue
                stock_name = (current or previous)["stock_name"]
                etf_name = (current or previous)["etf_name"]
                share_delta = (current_shares or 0) - (previous_shares or 0)
                weight = current["weight"] if current else 0
                estimated_value = current["market_value"] if current else 0
                source_value = active_etf_source_value(
                    (current or previous)["source"],
                    (current or previous)["source_url"],
                )
                score = active_etf_signal_score(change_type, share_delta, weight)
                label = active_etf_change_label(change_type)
                thesis = (
                    f"{etf_symbol} {trade_date} vs {previous_date}: "
                    f"{stock_symbol} {label}, shares {previous_shares or 0:g} -> {current_shares or 0:g}."
                )
                raw = {
                    "derived_from_holdings": True,
                    "previous_date": previous_date,
                    "trade_date": trade_date,
                    "etf_symbol": etf_symbol,
                    "stock_symbol": stock_symbol,
                    "change_type": change_type,
                    "previous": dict(previous) if previous else None,
                    "current": dict(current) if current else None,
                }
                existed = conn.execute(
                    """
                    SELECT 1 FROM active_etf_changes
                    WHERE trade_date = ? AND etf_symbol = ? AND stock_symbol = ? AND change_type = ?
                    """,
                    (trade_date, etf_symbol, stock_symbol, change_type),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO active_etf_changes (
                        trade_date, etf_symbol, etf_name, stock_symbol, stock_name, change_type,
                        previous_shares, current_shares, share_delta, weight, estimated_price,
                        estimated_value, source, captured_at, signal_score, thesis, raw_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(trade_date, etf_symbol, stock_symbol, change_type) DO UPDATE SET
                        etf_name = excluded.etf_name,
                        stock_name = excluded.stock_name,
                        previous_shares = excluded.previous_shares,
                        current_shares = excluded.current_shares,
                        share_delta = excluded.share_delta,
                        weight = excluded.weight,
                        estimated_value = excluded.estimated_value,
                        source = excluded.source,
                        captured_at = excluded.captured_at,
                        signal_score = excluded.signal_score,
                        thesis = excluded.thesis,
                        raw_json = excluded.raw_json
                    """,
                    (
                        trade_date,
                        etf_symbol,
                        etf_name,
                        stock_symbol,
                        stock_name,
                        change_type,
                        previous_shares,
                        current_shares,
                        share_delta,
                        weight,
                        estimated_value,
                        source_value,
                        captured_at,
                        score,
                        thesis,
                        json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
                if existed:
                    updated += 1
                else:
                    inserted += 1
                by_change[change_type] = by_change.get(change_type, 0) + 1
    return {
        "trade_date": trade_date,
        "inserted": inserted,
        "updated": updated,
        "skipped_etfs_without_previous_snapshot": skipped,
        "by_change": by_change,
        "note": "Changes are derived by comparing the full holding snapshot with the previous available snapshot for each ETF.",
    }


def import_active_etf_holdings_csv(filename: str = "active_etf_holdings.csv") -> dict[str, Any]:
    source = (ROOT / filename).resolve()
    if ROOT not in source.parents and source != ROOT:
        raise ValueError("CSV path must be inside project directory.")
    if not source.exists():
        raise ValueError(f"CSV file not found: {source.name}")

    required = {"trade_date", "etf_symbol", "stock_symbol"}
    inserted = 0
    updated = 0
    skipped = 0
    row_count = 0
    warnings: list[dict[str, Any]] = []
    errors: list[str] = []
    dates: set[str] = set()
    etfs: set[str] = set()
    stocks: set[str] = set()
    source_values: set[str] = set()
    captured_at = utc_now()
    with source.open("r", encoding="utf-8-sig", newline="") as handle, db_connect() as conn:
        reader = csv.DictReader(handle)
        field_list = reader.fieldnames or []
        fields = set(field_list)
        missing = required - fields
        if missing:
            raise ValueError(f"CSV missing required fields: {', '.join(sorted(missing))}")
        for row_number, row in enumerate(reader, start=2):
            row_count += 1
            warnings.extend(validate_active_etf_holding_row(row, row_number))
            trade_date = (row.get("trade_date") or "").strip()
            etf_symbol = (row.get("etf_symbol") or "").strip().upper()
            stock_symbol = normalize_tw_stock_symbol(row.get("stock_symbol") or "")
            if not trade_date or not etf_symbol or not stock_symbol:
                skipped += 1
                continue
            dates.add(trade_date)
            etfs.add(etf_symbol)
            stocks.add(stock_symbol)
            etf_name = (row.get("etf_name") or "").strip() or None
            issuer = (row.get("issuer") or "").strip() or None
            stock_name = (row.get("stock_name") or "").strip() or None
            shares = safe_float(row.get("shares") or row.get("current_shares"))
            weight = safe_float(row.get("weight"))
            market_value = safe_float(row.get("market_value") or row.get("estimated_value"))
            source_label = (row.get("source") or "").strip() or None
            source_url = (row.get("source_url") or "").strip() or None
            source_value = active_etf_source_value(source_label, source_url)
            if source_value:
                source_values.add(source_value)
            conn.execute(
                """
                INSERT INTO active_etf_funds (
                    etf_symbol, etf_name, issuer, market, enabled, source_url, created_at, updated_at
                )
                VALUES (?, ?, ?, 'TW', 1, ?, ?, ?)
                ON CONFLICT(etf_symbol) DO UPDATE SET
                    etf_name = COALESCE(NULLIF(excluded.etf_name, ''), active_etf_funds.etf_name),
                    issuer = COALESCE(NULLIF(excluded.issuer, ''), active_etf_funds.issuer),
                    source_url = COALESCE(NULLIF(excluded.source_url, ''), active_etf_funds.source_url),
                    updated_at = excluded.updated_at
                """,
                (etf_symbol, etf_name, issuer, source_url, captured_at, captured_at),
            )
            existed = conn.execute(
                """
                SELECT 1 FROM active_etf_holdings
                WHERE trade_date = ? AND etf_symbol = ? AND stock_symbol = ?
                """,
                (trade_date, etf_symbol, stock_symbol),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO active_etf_holdings (
                    trade_date, etf_symbol, etf_name, issuer, stock_symbol, stock_name,
                    shares, weight, market_value, source, source_url, captured_at, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date, etf_symbol, stock_symbol) DO UPDATE SET
                    etf_name = excluded.etf_name,
                    issuer = excluded.issuer,
                    stock_name = excluded.stock_name,
                    shares = excluded.shares,
                    weight = excluded.weight,
                    market_value = excluded.market_value,
                    source = excluded.source,
                    source_url = excluded.source_url,
                    captured_at = excluded.captured_at,
                    raw_json = excluded.raw_json
                """,
                (
                    trade_date,
                    etf_symbol,
                    etf_name,
                    issuer,
                    stock_symbol,
                    stock_name,
                    shares,
                    weight,
                    market_value,
                    source_label,
                    source_url,
                    captured_at,
                    json.dumps(dict(row), ensure_ascii=False, separators=(",", ":")),
                ),
            )
            conn.execute(
                """
                INSERT INTO universe (symbol, name, asset_type, currency, sector, industry, enabled, created_at, updated_at)
                VALUES (?, ?, '台股', 'TWD', '主動式ETF持股', ?, 1, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name = COALESCE(NULLIF(excluded.name, ''), universe.name),
                    updated_at = excluded.updated_at
                """,
                (stock_symbol, stock_name, etf_symbol, captured_at, captured_at),
            )
            if existed:
                updated += 1
            else:
                inserted += 1
    rebuilds = [rebuild_active_etf_changes_from_holdings(trade_date) for trade_date in sorted(dates)]
    with db_connect() as conn:
        total_holdings = conn.execute("SELECT COUNT(*) FROM active_etf_holdings").fetchone()[0]
        total_changes = conn.execute("SELECT COUNT(*) FROM active_etf_changes").fetchone()[0]
    result = {
        "source": str(source),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "row_count": row_count,
        "dates": sorted(dates),
        "rebuilds": rebuilds,
        "total_holdings": total_holdings,
        "total_changes": total_changes,
        "note": "Raw daily holdings are preserved; changes are derived from snapshot comparison.",
    }
    audit = record_data_import_audit(
        {
            "dataset": "active_etf_holdings",
            "source_file": str(source),
            "source_url": next((value for value in source_values if value.startswith(("http://", "https://"))), None),
            "source_label": ", ".join(sorted(source_values))[:500] if source_values else None,
            "imported_at": captured_at,
            "status": "warning" if warnings else "ok",
            "row_count": row_count,
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "fields": field_list,
            "warnings": warnings,
            "errors": errors,
            "stats": {
                "dates": sorted(dates),
                "etf_count": len(etfs),
                "stock_count": len(stocks),
                "source_count": len(source_values),
                "rebuilds": rebuilds,
            },
            "result": result,
        }
    )
    result["audit"] = {
        "id": audit["id"],
        "status": audit["status"],
        "warning_count": len(warnings),
        "error_count": len(errors),
        "markdown_path": audit["markdown_path"],
    }
    return result


def get_active_etf_holdings(limit: int = 500, trade_date: str | None = None, etf_symbol: str | None = None) -> dict[str, Any]:
    limit = max(1, min(limit, 2000))
    where = []
    params: list[Any] = []
    if trade_date:
        where.append("trade_date = ?")
        params.append(trade_date)
    if etf_symbol:
        where.append("etf_symbol = ?")
        params.append(etf_symbol.strip().upper())
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)
    with db_connect() as conn:
        rows = conn.execute(
            f"""
            SELECT trade_date, etf_symbol, etf_name, issuer, stock_symbol, stock_name,
                   shares, weight, market_value, source, source_url, captured_at
            FROM active_etf_holdings
            {clause}
            ORDER BY trade_date DESC, etf_symbol, weight DESC, stock_symbol
            LIMIT ?
            """,
            params,
        ).fetchall()
        summary_rows = conn.execute(
            f"""
            SELECT trade_date, etf_symbol, etf_name,
                   COUNT(*) AS holding_count,
                   SUM(COALESCE(market_value, 0)) AS total_market_value,
                   SUM(COALESCE(weight, 0)) AS total_weight,
                   MAX(captured_at) AS last_captured_at
            FROM active_etf_holdings
            GROUP BY trade_date, etf_symbol, etf_name
            ORDER BY trade_date DESC, etf_symbol
            LIMIT 100
            """
        ).fetchall()
    holdings = []
    for row in rows:
        item = dict(row)
        item["evidence"] = active_etf_evidence(active_etf_source_value(item.get("source"), item.get("source_url")))
        holdings.append(item)
    return {
        "holdings": holdings,
        "summary": [dict(row) for row in summary_rows],
        "note": "This endpoint returns preserved raw holdings snapshots. Use /api/active-etf/changes for derived differences.",
    }


def import_active_etf_changes_csv(filename: str = "active_etf_changes.csv") -> dict[str, Any]:
    source = (ROOT / filename).resolve()
    if ROOT not in source.parents and source != ROOT:
        raise ValueError("匯入檔案必須放在專案目錄內。")
    if not source.exists():
        raise ValueError(f"找不到匯入檔案：{source.name}")

    required = {"trade_date", "etf_symbol", "stock_symbol", "change_type"}
    inserted = 0
    updated = 0
    skipped = 0
    by_change: dict[str, int] = {}
    captured_at = utc_now()
    with source.open("r", encoding="utf-8-sig", newline="") as handle, db_connect() as conn:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        missing = required - fields
        if missing:
            raise ValueError(f"CSV 缺少欄位：{', '.join(sorted(missing))}")
        for row in reader:
            trade_date = (row.get("trade_date") or "").strip()
            etf_symbol = (row.get("etf_symbol") or "").strip().upper()
            stock_symbol = normalize_tw_stock_symbol(row.get("stock_symbol") or "")
            change_type = (row.get("change_type") or "").strip()
            if not trade_date or not etf_symbol or not stock_symbol or not change_type:
                skipped += 1
                continue
            previous_shares = safe_float(row.get("previous_shares"))
            current_shares = safe_float(row.get("current_shares"))
            share_delta = safe_float(row.get("share_delta"))
            if share_delta is None and previous_shares is not None and current_shares is not None:
                share_delta = current_shares - previous_shares
            weight = safe_float(row.get("weight"))
            estimated_price = safe_float(row.get("estimated_price"))
            estimated_value = safe_float(row.get("estimated_value"))
            score = active_etf_signal_score(change_type, share_delta, weight)
            direction = "正向追蹤" if score >= 58 else "風險觀察" if score <= 42 else "中性紀錄"
            thesis = (
                f"{etf_symbol} 於 {trade_date} 對 {stock_symbol} {change_type}；"
                f"股數變化 {share_delta if share_delta is not None else '未知'}，權重 {weight if weight is not None else '未知'}。"
                f"系統列為{direction}。"
            )
            conn.execute(
                """
                INSERT INTO active_etf_funds (
                    etf_symbol, etf_name, issuer, market, enabled, source_url, created_at, updated_at
                )
                VALUES (?, ?, ?, 'TW', 1, ?, ?, ?)
                ON CONFLICT(etf_symbol) DO UPDATE SET
                    etf_name = COALESCE(NULLIF(excluded.etf_name, ''), active_etf_funds.etf_name),
                    issuer = COALESCE(NULLIF(excluded.issuer, ''), active_etf_funds.issuer),
                    source_url = COALESCE(NULLIF(excluded.source_url, ''), active_etf_funds.source_url),
                    updated_at = excluded.updated_at
                """,
                (
                    etf_symbol,
                    (row.get("etf_name") or "").strip() or None,
                    (row.get("issuer") or "").strip() or None,
                    (row.get("source") or "").strip() or None,
                    captured_at,
                    captured_at,
                ),
            )
            existed = conn.execute(
                """
                SELECT 1 FROM active_etf_changes
                WHERE trade_date = ? AND etf_symbol = ? AND stock_symbol = ? AND change_type = ?
                """,
                (trade_date, etf_symbol, stock_symbol, change_type),
            ).fetchone()
            raw = dict(row)
            conn.execute(
                """
                INSERT INTO active_etf_changes (
                    trade_date, etf_symbol, etf_name, stock_symbol, stock_name, change_type,
                    previous_shares, current_shares, share_delta, weight, estimated_price,
                    estimated_value, source, captured_at, signal_score, thesis, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date, etf_symbol, stock_symbol, change_type) DO UPDATE SET
                    etf_name = excluded.etf_name,
                    stock_name = excluded.stock_name,
                    previous_shares = excluded.previous_shares,
                    current_shares = excluded.current_shares,
                    share_delta = excluded.share_delta,
                    weight = excluded.weight,
                    estimated_price = excluded.estimated_price,
                    estimated_value = excluded.estimated_value,
                    source = excluded.source,
                    captured_at = excluded.captured_at,
                    signal_score = excluded.signal_score,
                    thesis = excluded.thesis,
                    raw_json = excluded.raw_json
                """,
                (
                    trade_date,
                    etf_symbol,
                    (row.get("etf_name") or "").strip() or None,
                    stock_symbol,
                    (row.get("stock_name") or "").strip() or None,
                    change_type,
                    previous_shares,
                    current_shares,
                    share_delta,
                    weight,
                    estimated_price,
                    estimated_value,
                    (row.get("source") or "").strip() or None,
                    captured_at,
                    score,
                    thesis,
                    json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            if existed:
                updated += 1
            else:
                inserted += 1
            by_change[change_type] = by_change.get(change_type, 0) + 1
            conn.execute(
                """
                INSERT INTO universe (symbol, name, asset_type, currency, sector, industry, enabled, created_at, updated_at)
                VALUES (?, ?, '台股', 'TWD', '主動式ETF異動', ?, 1, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name = COALESCE(NULLIF(excluded.name, ''), universe.name),
                    sector = COALESCE(NULLIF(universe.sector, ''), excluded.sector),
                    updated_at = excluded.updated_at
                """,
                (
                    stock_symbol,
                    (row.get("stock_name") or "").strip() or None,
                    change_type,
                    captured_at,
                    captured_at,
                ),
            )
        total = conn.execute("SELECT COUNT(*) FROM active_etf_changes").fetchone()[0]
    return {
        "source": str(source),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "by_change": by_change,
        "total_changes": total,
        "note": "主動式 ETF 異動會保存為研究線索；新增/加碼偏正向，減碼/刪除偏風險觀察，仍需 PM 覆核。",
    }


def active_etf_completed_slots(scheduled_date: str) -> list[str]:
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT scheduled_slot
            FROM active_etf_import_runs
            WHERE scheduled_date = ? AND status = 'ok'
            ORDER BY scheduled_slot
            """,
            (scheduled_date,),
        ).fetchall()
    return [row["scheduled_slot"] for row in rows if row["scheduled_slot"] in ACTIVE_ETF_IMPORT_SLOTS]


def active_etf_import_due_slot(now: datetime | None = None) -> tuple[str, str] | None:
    local_now = (now or datetime.now(TAIPEI_TZ)).astimezone(TAIPEI_TZ)
    scheduled_date = local_now.strftime("%Y-%m-%d")
    completed = set(active_etf_completed_slots(scheduled_date))
    for slot in ACTIVE_ETF_IMPORT_SLOTS:
        hour, minute = [int(part) for part in slot.split(":", 1)]
        scheduled_at = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if local_now >= scheduled_at and slot not in completed:
            return scheduled_date, slot
    return None


def record_active_etf_import_run(
    scheduled_date: str,
    scheduled_slot: str,
    started_at: str,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    finished_at = utc_now()
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO active_etf_import_runs (
                scheduled_date, scheduled_slot, started_at, finished_at, status, source_file,
                inserted, updated, skipped, result_json, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scheduled_date, scheduled_slot) DO UPDATE SET
                started_at = excluded.started_at,
                finished_at = excluded.finished_at,
                status = excluded.status,
                source_file = excluded.source_file,
                inserted = excluded.inserted,
                updated = excluded.updated,
                skipped = excluded.skipped,
                result_json = excluded.result_json,
                error = excluded.error
            """,
            (
                scheduled_date,
                scheduled_slot,
                started_at,
                finished_at,
                status,
                result.get("source") if result else "active_etf_changes.csv",
                int(result.get("inserted") or 0) if result else 0,
                int(result.get("updated") or 0) if result else 0,
                int(result.get("skipped") or 0) if result else 0,
                json.dumps(result, ensure_ascii=False, separators=(",", ":")) if result else None,
                error,
            ),
        )


def active_etf_csv_has_rows(filename: str = "active_etf_holdings.csv") -> bool:
    source = ROOT / filename
    if not source.exists():
        return False
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return any(bool(row) and any((value or "").strip() for value in row.values()) for row in reader)


def get_active_etf_source_status(stale_days: int = 3) -> dict[str, Any]:
    with db_connect() as conn:
        latest_audit = conn.execute(
            """
            SELECT dataset, source_url, source_label, imported_at, status, row_count,
                   warning_count, error_count, markdown_path
            FROM data_import_audits
            WHERE dataset IN ('active_etf_zdsetf', 'active_etf_holdings')
            ORDER BY imported_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        latest_holding = conn.execute(
            """
            SELECT MAX(trade_date) AS latest_trade_date, MAX(captured_at) AS latest_captured_at,
                   COUNT(*) AS holding_count
            FROM active_etf_holdings
            """
        ).fetchone()
        latest_change = conn.execute(
            """
            SELECT MAX(trade_date) AS latest_trade_date, COUNT(*) AS change_count
            FROM active_etf_changes
            """
        ).fetchone()
    audit = dict(latest_audit) if latest_audit else None
    holding = dict(latest_holding) if latest_holding else {}
    change = dict(latest_change) if latest_change else {}
    status = "unavailable"
    stale = True
    age_days = None
    latest_captured_at = holding.get("latest_captured_at")
    if latest_captured_at:
        captured = datetime.fromisoformat(latest_captured_at.replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds() / 86400
        stale = age_days > stale_days
        status = "stale" if stale else "available"
    return {
        "status": status,
        "stale": stale,
        "stale_days": stale_days,
        "age_days": round(age_days, 2) if age_days is not None else None,
        "latest_audit": audit,
        "latest_holding": holding,
        "latest_change": change,
        "official_source_status": {
            "status": "pending_official_adapters",
            "endpoint": "/api/active-etf/official-source-candidates",
        },
        "strategy": ["official_sources_pending", "zdsetf_third_party", "manual_csv"],
        "note": "verified official source is not implemented yet; ZDS ETF Tracker is used as third_party fallback, then manual CSV.",
    }


def get_active_etf_official_source_candidates() -> dict[str, Any]:
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT f.etf_symbol, f.etf_name, f.issuer, f.source_url,
                   MAX(h.trade_date) AS latest_trade_date,
                   COUNT(DISTINCT h.stock_symbol) AS holding_count
            FROM active_etf_funds f
            LEFT JOIN active_etf_holdings h ON h.etf_symbol = f.etf_symbol
            WHERE f.enabled = 1
            GROUP BY f.etf_symbol, f.etf_name, f.issuer, f.source_url
            ORDER BY f.etf_symbol
            """
        ).fetchall()

    issuers: dict[str, int] = {}
    funds = []
    for row in rows:
        issuer = row["issuer"] or "unknown"
        issuers[issuer] = issuers.get(issuer, 0) + 1
        funds.append(
            {
                "etf_symbol": row["etf_symbol"],
                "etf_name": row["etf_name"],
                "issuer": issuer,
                "current_source": {
                    "evidence": "third_party",
                    "source_url": row["source_url"],
                    "latest_trade_date": row["latest_trade_date"],
                    "holding_count": row["holding_count"],
                },
                "official_adapter_status": "pending",
                "required_evidence": [
                    "official issuer or exchange URL",
                    "machine-readable daily holdings or downloadable file",
                    "stable date, symbol, shares, weight, and market-value fields",
                    "terms that permit local research ingestion",
                ],
                "next_step": "Attach an issuer/exchange adapter only after the official source URL and schema are verified.",
            }
        )

    return {
        "generated_at": utc_now(),
        "status": "pending_official_adapters",
        "fund_count": len(funds),
        "issuer_count": len(issuers),
        "issuer_summary": [{"issuer": key, "fund_count": value} for key, value in sorted(issuers.items())],
        "funds": funds,
        "note": "This inventory is intentionally conservative: it tracks official-source gaps without scraping unverified pages.",
    }


def sync_active_etf_sources(force_manual_csv: bool = False) -> dict[str, Any]:
    started_at = utc_now()
    attempts: list[dict[str, Any]] = []

    attempts.append(
        {
            "source": "official",
            "status": "skipped",
            "evidence": "verified",
            "reason": "official TWSE/TPEx/issuer adapters are not implemented yet",
        }
    )

    try:
        result = import_active_etf_zdsetf()
        attempts.append(
            {
                "source": "zdsetf",
                "status": "ok",
                "evidence": "third_party",
                "fund_count": result.get("fund_count"),
                "inserted_holdings": result.get("inserted_holdings"),
                "updated_holdings": result.get("updated_holdings"),
                "inserted_changes": result.get("inserted_changes"),
                "updated_changes": result.get("updated_changes"),
                "audit": result.get("audit"),
            }
        )
        return {
            "source": "active_etf_source_sync",
            "selected_source": "zdsetf",
            "status": "ok",
            "started_at": started_at,
            "attempts": attempts,
            "result": result,
            "source_status": get_active_etf_source_status(),
        }
    except Exception as exc:
        attempts.append({"source": "zdsetf", "status": "error", "evidence": "third_party", "error": str(exc)})

    if force_manual_csv or active_etf_csv_has_rows("active_etf_holdings.csv"):
        try:
            result = import_active_etf_holdings_csv("active_etf_holdings.csv")
            attempts.append(
                {
                    "source": "manual_csv",
                    "status": "ok",
                    "evidence": "manual",
                    "inserted": result.get("inserted"),
                    "updated": result.get("updated"),
                    "audit": result.get("audit"),
                }
            )
            return {
                "source": "active_etf_source_sync",
                "selected_source": "manual_csv",
                "status": "ok",
                "started_at": started_at,
                "attempts": attempts,
                "result": result,
                "source_status": get_active_etf_source_status(),
            }
        except Exception as exc:
            attempts.append({"source": "manual_csv", "status": "error", "evidence": "manual", "error": str(exc)})
    else:
        attempts.append({"source": "manual_csv", "status": "skipped", "evidence": "manual", "reason": "active_etf_holdings.csv has no data rows"})

    source_status = get_active_etf_source_status()
    if source_status["status"] in {"available", "stale"}:
        return {
            "source": "active_etf_source_sync",
            "selected_source": "cached",
            "status": "stale" if source_status["stale"] else "cached",
            "started_at": started_at,
            "attempts": attempts,
            "source_status": source_status,
            "note": "All live sources failed; keeping existing cached data.",
        }
    return {
        "source": "active_etf_source_sync",
        "selected_source": None,
        "status": "unavailable",
        "started_at": started_at,
        "attempts": attempts,
        "source_status": source_status,
        "error": "No active ETF source is available and no cached data exists.",
    }


def run_due_active_etf_import(force: bool = False) -> dict[str, Any]:
    due = active_etf_import_due_slot()
    if force and not due:
        local_now = datetime.now(TAIPEI_TZ)
        due = (local_now.strftime("%Y-%m-%d"), "manual")
    today = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    completed = active_etf_completed_slots(today)
    if not due:
        ACTIVE_ETF_IMPORT_STATE.update({"completed_slots": completed, "last_error": None})
        return {"ran": False, "completed_slots": completed, "slots": ACTIVE_ETF_IMPORT_SLOTS}
    scheduled_date, scheduled_slot = due
    started_at = utc_now()
    try:
        result = sync_active_etf_sources()
        run_status = "ok" if result.get("status") in {"ok", "cached", "stale"} else "error"
        record_active_etf_import_run(scheduled_date, scheduled_slot, started_at, run_status, result=result)
        if run_status != "ok":
            raise RuntimeError(result.get("error") or "active ETF source sync failed")
        completed = active_etf_completed_slots(scheduled_date)
        ACTIVE_ETF_IMPORT_STATE.update(
            {
                "last_run_at": started_at,
                "last_slot": scheduled_slot,
                "last_result": result,
                "last_error": None,
                "completed_slots": completed,
            }
        )
        return {"ran": True, "scheduled_date": scheduled_date, "scheduled_slot": scheduled_slot, "result": result}
    except Exception as exc:
        message = str(exc)
        record_active_etf_import_run(scheduled_date, scheduled_slot, started_at, "error", error=message)
        ACTIVE_ETF_IMPORT_STATE.update(
            {
                "last_run_at": started_at,
                "last_slot": scheduled_slot,
                "last_error": message,
                "completed_slots": completed,
            }
        )
        return {"ran": True, "scheduled_date": scheduled_date, "scheduled_slot": scheduled_slot, "error": message}


def get_active_etf_changes(limit: int = 100, trade_date: str | None = None, stock_symbol: str | None = None) -> dict[str, Any]:
    limit = max(1, min(limit, 500))
    where = []
    params: list[Any] = []
    if trade_date:
        where.append("trade_date = ?")
        params.append(trade_date)
    if stock_symbol:
        where.append("stock_symbol = ?")
        params.append(normalize_tw_stock_symbol(stock_symbol))
    with db_connect() as conn:
        has_derived = conn.execute(
            "SELECT 1 FROM active_etf_changes WHERE raw_json LIKE ? LIMIT 1",
            ('%"derived_from_holdings":true%',),
        ).fetchone()
        summary_where = list(where)
        summary_params = list(params)
        if has_derived:
            where.append("raw_json LIKE ?")
            params.append('%"derived_from_holdings":true%')
            summary_where.append("raw_json LIKE ?")
            summary_params.append('%"derived_from_holdings":true%')
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        summary_clause = f"WHERE {' AND '.join(summary_where)}" if summary_where else ""
        params.append(limit)
        rows = conn.execute(
            f"""
            SELECT trade_date, etf_symbol, etf_name, stock_symbol, stock_name, change_type,
                   previous_shares, current_shares, share_delta, weight, estimated_price,
                   estimated_value, source, captured_at, signal_score, thesis
            FROM active_etf_changes
            {clause}
            ORDER BY trade_date DESC, signal_score DESC, ABS(COALESCE(share_delta, 0)) DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        summary_rows = conn.execute(
            f"""
            SELECT stock_symbol, stock_name,
                   SUM(CASE WHEN change_type IN ('新增','加碼','買進') OR share_delta > 0 THEN 1 ELSE 0 END) AS positive_count,
                   SUM(CASE WHEN change_type IN ('減碼','刪除','賣出') OR share_delta < 0 THEN 1 ELSE 0 END) AS negative_count,
                   COUNT(DISTINCT etf_symbol) AS etf_count,
                   MAX(trade_date) AS latest_date,
                   AVG(signal_score) AS avg_score
            FROM active_etf_changes
            {summary_clause}
            GROUP BY stock_symbol, stock_name
            ORDER BY latest_date DESC, etf_count DESC, avg_score DESC
            LIMIT 30
            """,
            summary_params,
        ).fetchall()
    changes = []
    for row in rows:
        item = dict(row)
        item["evidence"] = active_etf_evidence(item.get("source"))
        changes.append(item)
    return {
        "changes": changes,
        "stock_summary": [dict(row) for row in summary_rows],
        "disclaimer": "主動式 ETF 異動是投資線索，不是買賣指令；需確認揭露來源、成交量、價格位置與自身風險。",
    }


def get_active_etf_audit() -> dict[str, Any]:
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT source, COUNT(*) AS count
            FROM active_etf_changes
            GROUP BY source
            ORDER BY count DESC
            """
        ).fetchall()
        latest = conn.execute(
            """
            SELECT trade_date, etf_symbol, stock_symbol, change_type, source, captured_at
            FROM active_etf_changes
            ORDER BY captured_at DESC, id DESC
            LIMIT 20
            """
        ).fetchall()
    source_summary = []
    totals = {"verified": 0, "third_party": 0, "unverified": 0}
    for row in rows:
        evidence = active_etf_evidence(row["source"])
        totals[evidence["status"]] = totals.get(evidence["status"], 0) + int(row["count"])
        source_summary.append({"source": row["source"], "count": row["count"], "evidence": evidence})
    latest_rows = []
    for row in latest:
        item = dict(row)
        item["evidence"] = active_etf_evidence(item.get("source"))
        latest_rows.append(item)
    return {
        "totals": totals,
        "source_summary": source_summary,
        "latest": latest_rows,
        "verdict": "只有 evidence.status 為 verified 或 third_party 的資料才可視為有外部來源；unverified 只能當測試/示範資料。",
    }


def active_etf_flow_score(symbol: str) -> tuple[float | None, list[dict[str, Any]], dict[str, Any]]:
    try:
        with db_connect() as conn:
            rows = conn.execute(
                """
                SELECT etf_symbol, change_type, share_delta, signal_score, trade_date, source
                FROM active_etf_changes
                WHERE stock_symbol = ?
                ORDER BY trade_date DESC, captured_at DESC, signal_score DESC
                LIMIT 12
                """,
                (symbol,),
            ).fetchall()
    except sqlite3.Error:
        return None, [], {"change_count": 0, "source_status": "source_failed"}
    if not rows:
        return None, [], {"change_count": 0, "source_status": "source_missing"}
    scores = [safe_float(row["signal_score"]) for row in rows]
    scores = [score for score in scores if score is not None]
    if not scores:
        return None, [], {"change_count": len(rows), "source_status": "source_missing"}
    latest = rows[0]
    positive_count = sum(1 for score in scores if score >= 58)
    negative_count = sum(1 for score in scores if score <= 42)
    average_score = sum(scores) / len(scores)
    detail = (
        f"近期主動式 ETF 異動 {len(rows)} 筆，正向 {positive_count} 筆、"
        f"負向 {negative_count} 筆；最新為 {latest['etf_symbol']} "
        f"{latest['trade_date']} {latest['change_type']}。"
    )
    signal = {
        "category": "主動ETF",
        "label": "主動式 ETF 異動線索",
        "impact": int(round(average_score - 50)),
        "detail": detail,
        "source": latest["source"],
    }
    evidence = {
        "change_count": len(rows),
        "avg_score": average_score,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "latest_etf_symbol": latest["etf_symbol"],
        "latest_trade_date": latest["trade_date"],
        "latest_change_type": latest["change_type"],
        "source": latest["source"],
        "source_status": "available",
    }
    return average_score, [signal], evidence
