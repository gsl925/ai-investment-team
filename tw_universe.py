from __future__ import annotations
import csv, json, re
import requests
from datetime import datetime
from typing import Any
from common import (
    ROOT, AEGIS_SNAPSHOT_DB, EXPANDED_UNIVERSE, YAHOO_SCREENER_IDS, TAIPEI_TZ,
    HTTP, YAHOO_HEADERS, HTTP_TIMEOUT,
    db_connect, utc_now, classify_asset, aegis_connect, yahoo_get,
    record_data_import_audit,
)

def expand_universe(profile: str = "starter") -> dict[str, Any]:
    if profile == "starter":
        groups = EXPANDED_UNIVERSE
    elif profile in EXPANDED_UNIVERSE:
        groups = {profile: EXPANDED_UNIVERSE[profile]}
    else:
        raise ValueError(f"未知 universe profile：{profile}")

    now = utc_now()
    inserted = 0
    existing = 0
    by_group = {}
    with db_connect() as conn:
        for group, symbols in groups.items():
            group_inserted = 0
            group_existing = 0
            for symbol in dict.fromkeys(symbols):
                cursor = conn.execute(
                    """
                    INSERT INTO universe (symbol, asset_type, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(symbol) DO NOTHING
                    """,
                    (symbol, classify_asset(symbol), now, now),
                )
                if cursor.rowcount:
                    inserted += 1
                    group_inserted += 1
                else:
                    existing += 1
                    group_existing += 1
            by_group[group] = {"inserted": group_inserted, "existing": group_existing, "total": len(set(symbols))}
        total = conn.execute("SELECT COUNT(*) FROM universe WHERE enabled = 1").fetchone()[0]
    return {
        "profile": profile,
        "inserted": inserted,
        "existing": existing,
        "enabled_universe_count": total,
        "groups": by_group,
        "note": "這是 starter universe，不是完整全市場；後續可分批加入 S&P 500、Nasdaq 100、台股上市櫃與 crypto top N。",
    }


def import_universe_csv(filename: str = "universe_import.csv") -> dict[str, Any]:
    source = (ROOT / filename).resolve()
    if ROOT not in source.parents and source != ROOT:
        raise ValueError("匯入檔案必須放在專案目錄內。")
    if not source.exists():
        raise ValueError(f"找不到匯入檔案：{source.name}")

    inserted = 0
    updated = 0
    skipped = 0
    by_asset: dict[str, int] = {}
    now = utc_now()
    with source.open("r", encoding="utf-8-sig", newline="") as handle, db_connect() as conn:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "symbol" not in reader.fieldnames:
            raise ValueError("CSV 至少需要 symbol 欄位。")
        for row in reader:
            symbol = (row.get("symbol") or "").strip().upper()
            if not symbol or symbol.startswith("#"):
                skipped += 1
                continue
            asset_type = (row.get("asset_type") or "").strip() or classify_asset(symbol)
            enabled_raw = (row.get("enabled") or "1").strip().lower()
            enabled = 0 if enabled_raw in {"0", "false", "no", "n"} else 1
            existed = conn.execute("SELECT 1 FROM universe WHERE symbol = ?", (symbol,)).fetchone() is not None
            conn.execute(
                """
                INSERT INTO universe (
                    symbol, name, asset_type, currency, sector, industry, enabled, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name = COALESCE(NULLIF(excluded.name, ''), universe.name),
                    asset_type = excluded.asset_type,
                    currency = COALESCE(NULLIF(excluded.currency, ''), universe.currency),
                    sector = COALESCE(NULLIF(excluded.sector, ''), universe.sector),
                    industry = COALESCE(NULLIF(excluded.industry, ''), universe.industry),
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    symbol,
                    (row.get("name") or "").strip() or None,
                    asset_type,
                    (row.get("currency") or "").strip() or None,
                    (row.get("sector") or "").strip() or None,
                    (row.get("industry") or "").strip() or None,
                    enabled,
                    now,
                    now,
                ),
            )
            if existed:
                updated += 1
            else:
                inserted += 1
            by_asset[asset_type] = by_asset.get(asset_type, 0) + 1
        total = conn.execute("SELECT COUNT(*) FROM universe WHERE enabled = 1").fetchone()[0]
    return {
        "source": str(source),
        "inserted_or_updated": inserted + updated,
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "by_asset": by_asset,
        "enabled_universe_count": total,
        "note": "CSV 匯入會 upsert universe；既有 snapshot 不會重抓，背景掃描仍依 refresh_minutes 快取輪巡。",
    }


def asset_type_from_yahoo_quote(quote: dict[str, Any]) -> str:
    quote_type = str(quote.get("quoteType") or "").upper()
    symbol = str(quote.get("symbol") or "").upper()
    if quote_type in {"ETF", "MUTUALFUND"}:
        return "美股/ETF"
    return classify_asset(symbol)


def sync_yahoo_max_universe(count_per_screen: int = 250) -> dict[str, Any]:
    count_per_screen = max(1, min(count_per_screen, 250))
    inserted = 0
    updated = 0
    skipped = 0
    errors = []
    by_source = {}
    by_asset: dict[str, int] = {}
    seen: set[str] = set()
    now = utc_now()

    with db_connect() as conn:
        for screener_id in YAHOO_SCREENER_IDS:
            source_inserted = 0
            source_updated = 0
            source_seen = 0
            try:
                payload = yahoo_get(
                    "/v1/finance/screener/predefined/saved",
                    {
                        "formatted": "false",
                        "scrIds": screener_id,
                        "count": str(count_per_screen),
                    },
                )
                results = payload.get("finance", {}).get("result") or []
                quotes = results[0].get("quotes", []) if results else []
            except requests.RequestException as exc:
                errors.append({"source": screener_id, "message": str(exc)})
                continue

            for quote in quotes:
                symbol = str(quote.get("symbol") or "").strip().upper()
                if not symbol or symbol in seen:
                    skipped += 1
                    continue
                seen.add(symbol)
                source_seen += 1
                asset_type = asset_type_from_yahoo_quote(quote)
                existed = conn.execute("SELECT 1 FROM universe WHERE symbol = ?", (symbol,)).fetchone() is not None
                conn.execute(
                    """
                    INSERT INTO universe (
                        symbol, name, asset_type, currency, sector, industry, enabled, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        name = COALESCE(NULLIF(excluded.name, ''), universe.name),
                        asset_type = excluded.asset_type,
                        currency = COALESCE(NULLIF(excluded.currency, ''), universe.currency),
                        sector = COALESCE(NULLIF(excluded.sector, ''), universe.sector),
                        industry = COALESCE(NULLIF(excluded.industry, ''), universe.industry),
                        enabled = 1,
                        updated_at = excluded.updated_at
                    """,
                    (
                        symbol,
                        quote.get("longName") or quote.get("shortName") or symbol,
                        asset_type,
                        quote.get("currency") or quote.get("financialCurrency"),
                        quote.get("sector") or quote.get("sectorDisp"),
                        quote.get("industry") or quote.get("industryDisp"),
                        now,
                        now,
                    ),
                )
                if existed:
                    updated += 1
                    source_updated += 1
                else:
                    inserted += 1
                    source_inserted += 1
                by_asset[asset_type] = by_asset.get(asset_type, 0) + 1
            by_source[screener_id] = {
                "seen": source_seen,
                "inserted": source_inserted,
                "updated": source_updated,
            }
        total = conn.execute("SELECT COUNT(*) FROM universe WHERE enabled = 1").fetchone()[0]
    return {
        "source": "Yahoo Finance predefined screeners",
        "screeners": YAHOO_SCREENER_IDS,
        "count_per_screen": count_per_screen,
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "by_source": by_source,
        "by_asset": by_asset,
        "enabled_universe_count": total,
        "note": "這是低頻水池擴建任務；不會立即重抓所有行情，背景掃描會依批次與快取慢慢更新。",
    }


def import_universe_from_active_etf_holdings() -> dict[str, Any]:
    now = utc_now()
    inserted = 0
    updated = 0
    skipped = 0
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT stock_symbol, stock_name,
                   COUNT(DISTINCT etf_symbol) AS etf_count,
                   MAX(trade_date) AS latest_trade_date
            FROM active_etf_holdings
            GROUP BY stock_symbol, stock_name
            ORDER BY etf_count DESC, latest_trade_date DESC
            """
        ).fetchall()
        for row in rows:
            symbol = (row["stock_symbol"] or "").strip().upper()
            if not symbol:
                skipped += 1
                continue
            existed = conn.execute("SELECT 1 FROM universe WHERE symbol = ?", (symbol,)).fetchone() is not None
            asset_type = classify_asset(symbol)
            conn.execute(
                """
                INSERT INTO universe (symbol, name, asset_type, currency, sector, industry, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, '主動式ETF持股', ?, 1, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name = COALESCE(NULLIF(excluded.name, ''), universe.name),
                    sector = COALESCE(NULLIF(universe.sector, ''), excluded.sector),
                    industry = COALESCE(NULLIF(universe.industry, ''), excluded.industry),
                    enabled = 1,
                    updated_at = excluded.updated_at
                """,
                (
                    symbol,
                    row["stock_name"],
                    asset_type,
                    "TWD" if symbol.endswith((".TW", ".TWO")) else None,
                    f"active_etf_count={row['etf_count']};latest={row['latest_trade_date']}",
                    now,
                    now,
                ),
            )
            if existed:
                updated += 1
            else:
                inserted += 1
    with db_connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM universe WHERE enabled = 1").fetchone()[0]
    return {
        "source": "active_etf_holdings",
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "enabled_universe_count": total,
        "note": "Imports symbols observed in active ETF holdings into the candidate universe.",
    }


def import_twse_universe_from_openapi() -> dict[str, Any]:
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    captured_at = utc_now()
    response = HTTP.get(url, headers=YAHOO_HEADERS, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    rows = response.json()
    inserted = 0
    updated = 0
    skipped = 0
    with db_connect() as conn:
        for row in rows:
            code = str(row.get("Code") or "").strip().upper()
            name = str(row.get("Name") or "").strip() or None
            if not code:
                skipped += 1
                continue
            symbol = normalize_tw_stock_symbol(code)
            existed = conn.execute("SELECT 1 FROM universe WHERE symbol = ?", (symbol,)).fetchone() is not None
            asset_type = "台股/ETF" if code.startswith("00") else "台股"
            conn.execute(
                """
                INSERT INTO universe (symbol, name, asset_type, currency, sector, industry, enabled, created_at, updated_at)
                VALUES (?, ?, ?, 'TWD', 'TWSE全市場', ?, 1, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name = COALESCE(NULLIF(excluded.name, ''), universe.name),
                    asset_type = excluded.asset_type,
                    currency = 'TWD',
                    sector = COALESCE(NULLIF(universe.sector, ''), excluded.sector),
                    industry = COALESCE(NULLIF(universe.industry, ''), excluded.industry),
                    enabled = 1,
                    updated_at = excluded.updated_at
                """,
                (symbol, name, asset_type, f"source=TWSE_OPENAPI;date={row.get('Date')}", captured_at, captured_at),
            )
            if existed:
                updated += 1
            else:
                inserted += 1
    with db_connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM universe WHERE enabled = 1").fetchone()[0]
    result = {
        "source": url,
        "market": "TWSE",
        "row_count": len(rows),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "enabled_universe_count": total,
        "note": "Imports TWSE listed market symbols from official OpenAPI into candidate universe only; no snapshots are fetched.",
    }
    record_data_import_audit(
        {
            "dataset": "universe_twse_openapi",
            "source_file": None,
            "source_url": url,
            "source_label": "TWSE OpenAPI",
            "imported_at": captured_at,
            "status": "ok",
            "row_count": len(rows),
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "fields": list(rows[0].keys()) if rows else [],
            "warnings": [],
            "errors": [],
            "stats": {"market": "TWSE", "enabled_universe_count": total},
            "result": result,
        }
    )
    return result


TW_FULL_MARKET_SCOPE = "tw_full_market"
TW_FULL_MARKET_POLICY = {
    "scope": TW_FULL_MARKET_SCOPE,
    "name": "Taiwan full market",
    "purpose": "Persistent full-market monitoring universe; not a temporary shortlist.",
    "included": [
        "TWSE listed common stocks",
        "TPEx listed common stocks",
        "Taiwan ETFs",
        "Emerging common stocks",
    ],
    "excluded": [
        "warrants",
        "callable bull/bear products",
        "preferred shares",
        "TDR",
        "foreign holdings from ETF portfolios",
        "bonds and other non-stock/non-ETF instruments",
    ],
    "canonical_symbol": "stock_id.TW",
    "scan_policy": "Always rescan and refresh the full scope over time. Low score, low liquidity, or low confidence today does not remove a symbol from future scans.",
    "shortlist_policy": "Opportunity candidates are only the current research priority list derived from latest full-market snapshots.",
    "no_quote_policy": "No-quote symbols remain in membership but are excluded from opportunity ranking until an official quote becomes available; do not fabricate snapshots.",
}
TW_FULL_MARKET_MARKETS = {"twse", "tpex", "emerging"}
TW_FULL_MARKET_INSTRUMENTS = {"股票", "ETF"}


def tw_market_label(market: str | None) -> str:
    labels = {"twse": "上市", "tpex": "上櫃", "emerging": "興櫃"}
    return labels.get(str(market or "").lower(), str(market or "") or "未知")


def tw_full_market_symbol(stock_id: str) -> str:
    return f"{stock_id.strip().upper()}.TW"


def tw_full_market_asset_type(instrument_type: str | None) -> str:
    return "台股/ETF" if str(instrument_type or "").upper() == "ETF" else "台股"


def get_tw_full_market_rows_from_aegis() -> list[dict[str, Any]]:
    conn = aegis_connect()
    if conn is None:
        raise RuntimeError("AegisTrader snapshot is not available.")
    try:
        rows = conn.execute(
            """
            SELECT stock_id, name, market, market_zh, instrument_type, isin, start_date,
                   industry_group, cfi, updated_at
            FROM stock_master
            WHERE market IN ('twse', 'tpex', 'emerging')
              AND (
                  instrument_type IN ('股票', 'ETF')
                  OR (market = 'emerging' AND COALESCE(instrument_type, '') = '')
              )
            ORDER BY market, instrument_type, stock_id
            """
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def sync_tw_full_market_universe_from_aegis() -> dict[str, Any]:
    captured_at = utc_now()
    source = "AegisTrader snapshot stock_master"
    rows = get_tw_full_market_rows_from_aegis()
    inserted = 0
    updated = 0
    membership_inserted = 0
    membership_updated = 0
    skipped = 0
    by_market: dict[str, int] = {}
    by_instrument: dict[str, int] = {}
    with db_connect() as conn:
        conn.execute("DELETE FROM universe_membership WHERE scope = ?", (TW_FULL_MARKET_SCOPE,))
        for row in rows:
            stock_id = str(row.get("stock_id") or "").strip().upper()
            market = str(row.get("market") or "").strip().lower()
            instrument_type = str(row.get("instrument_type") or "").strip()
            if market == "emerging" and not instrument_type:
                instrument_type = "股票"
            if not stock_id or market not in TW_FULL_MARKET_MARKETS or instrument_type not in TW_FULL_MARKET_INSTRUMENTS:
                skipped += 1
                continue
            symbol = tw_full_market_symbol(stock_id)
            existed = conn.execute("SELECT 1 FROM universe WHERE symbol = ?", (symbol,)).fetchone() is not None
            asset_type = tw_full_market_asset_type(instrument_type)
            sector = f"台灣全市場/{tw_market_label(market)}"
            industry = row.get("industry_group") or instrument_type
            conn.execute(
                """
                INSERT INTO universe (symbol, name, asset_type, currency, sector, industry, enabled, created_at, updated_at)
                VALUES (?, ?, ?, 'TWD', ?, ?, 1, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name = COALESCE(NULLIF(excluded.name, ''), universe.name),
                    asset_type = excluded.asset_type,
                    currency = 'TWD',
                    sector = excluded.sector,
                    industry = COALESCE(NULLIF(excluded.industry, ''), universe.industry),
                    enabled = 1,
                    updated_at = excluded.updated_at
                """,
                (symbol, row.get("name"), asset_type, sector, industry, captured_at, captured_at),
            )
            if existed:
                updated += 1
            else:
                inserted += 1

            membership_existed = conn.execute(
                "SELECT 1 FROM universe_membership WHERE scope = ? AND symbol = ?",
                (TW_FULL_MARKET_SCOPE, symbol),
            ).fetchone() is not None
            metadata = {
                "stock_id": stock_id,
                "name": row.get("name"),
                "market_zh": row.get("market_zh"),
                "isin": row.get("isin"),
                "start_date": row.get("start_date"),
                "industry_group": row.get("industry_group"),
                "cfi": row.get("cfi"),
                "source_updated_at": row.get("updated_at"),
                "definition": "上市普通股 + 上櫃普通股 + ETF + 興櫃普通股; excludes warrants, preferred shares, TDR, and other instruments.",
            }
            conn.execute(
                """
                INSERT INTO universe_membership (
                    scope, symbol, market, instrument_type, source, source_symbol, included_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, symbol) DO UPDATE SET
                    market = excluded.market,
                    instrument_type = excluded.instrument_type,
                    source = excluded.source,
                    source_symbol = excluded.source_symbol,
                    included_at = excluded.included_at,
                    metadata_json = excluded.metadata_json
                """,
                (
                    TW_FULL_MARKET_SCOPE,
                    symbol,
                    market,
                    instrument_type,
                    source,
                    stock_id,
                    captured_at,
                    json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            if membership_existed:
                membership_updated += 1
            else:
                membership_inserted += 1
            by_market[market] = by_market.get(market, 0) + 1
            by_instrument[instrument_type] = by_instrument.get(instrument_type, 0) + 1

    status = get_tw_full_market_status()
    result = {
        "scope": TW_FULL_MARKET_SCOPE,
        "source": source,
        "definition": TW_FULL_MARKET_POLICY,
        "row_count": len(rows),
        "inserted": inserted,
        "updated": updated,
        "membership_inserted": membership_inserted,
        "membership_updated": membership_updated,
        "skipped": skipped,
        "by_market": by_market,
        "by_instrument": by_instrument,
        "status": status,
    }
    record_data_import_audit(
        {
            "dataset": "universe_tw_full_market_aegis",
            "source_file": str(AEGIS_SNAPSHOT_DB),
            "source_url": None,
            "source_label": source,
            "imported_at": captured_at,
            "status": "ok",
            "row_count": len(rows),
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "fields": [
                "stock_id",
                "name",
                "market",
                "market_zh",
                "instrument_type",
                "isin",
                "start_date",
                "industry_group",
                "cfi",
                "updated_at",
            ],
            "warnings": [],
            "errors": [],
            "stats": {"by_market": by_market, "by_instrument": by_instrument, "status": status},
            "result": result,
        }
    )
    return result


def get_tw_full_market_status() -> dict[str, Any]:
    with db_connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM universe_membership WHERE scope = ?",
            (TW_FULL_MARKET_SCOPE,),
        ).fetchone()[0]
        snapshot_symbols = conn.execute(
            """
            SELECT COUNT(DISTINCT um.symbol)
            FROM universe_membership um
            JOIN price_snapshots ps ON ps.symbol = um.symbol
            WHERE um.scope = ?
            """,
            (TW_FULL_MARKET_SCOPE,),
        ).fetchone()[0]
        by_market = conn.execute(
            """
            SELECT um.market, um.instrument_type,
                   COUNT(DISTINCT um.symbol) AS universe_count,
                   COUNT(DISTINCT ps.symbol) AS snapshot_symbol_count
            FROM universe_membership um
            LEFT JOIN price_snapshots ps ON ps.symbol = um.symbol
            WHERE um.scope = ?
            GROUP BY um.market, um.instrument_type
            ORDER BY um.market, um.instrument_type
            """,
            (TW_FULL_MARKET_SCOPE,),
        ).fetchall()
        latest_membership = conn.execute(
            """
            SELECT MAX(included_at)
            FROM universe_membership
            WHERE scope = ?
            """,
            (TW_FULL_MARKET_SCOPE,),
        ).fetchone()[0]
        no_quote_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM universe_membership um
            WHERE um.scope = ?
              AND NOT EXISTS (SELECT 1 FROM price_snapshots ps WHERE ps.symbol = um.symbol)
            """,
            (TW_FULL_MARKET_SCOPE,),
        ).fetchone()[0]
    tradable_count = max(0, int(total or 0) - int(no_quote_count or 0))
    return {
        "scope": TW_FULL_MARKET_SCOPE,
        "definition": TW_FULL_MARKET_POLICY,
        "canonical_symbol": TW_FULL_MARKET_POLICY["canonical_symbol"],
        "universe_count": int(total or 0),
        "snapshot_symbol_count": int(snapshot_symbols or 0),
        "unsnapped_count": max(0, int(total or 0) - int(snapshot_symbols or 0)),
        "snapshot_coverage_percent": round((snapshot_symbols or 0) / total * 100, 2) if total else 0,
        "tradable_quote_count": tradable_count,
        "no_quote_count": int(no_quote_count or 0),
        "tradable_quote_coverage_percent": round(tradable_count / total * 100, 2) if total else 0,
        "analysis_ready_percent": round((snapshot_symbols or 0) / tradable_count * 100, 2) if tradable_count else 0,
        "latest_membership_at": latest_membership,
        "by_market": [dict(row) for row in by_market],
    }


def get_universe_scope_definitions() -> dict[str, Any]:
    return {
        "generated_at": utc_now(),
        "scopes": [
            {
                "policy": TW_FULL_MARKET_POLICY,
                "status": get_tw_full_market_status(),
            }
        ],
        "note": "Shortlists do not replace scan scopes. They are current research priorities computed from the latest full-scope snapshots.",
    }


def get_tw_full_market_quote_gaps() -> dict[str, Any]:
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT um.symbol, um.market, um.instrument_type, u.name, u.asset_type,
                   um.source, um.source_symbol, um.included_at, um.metadata_json
            FROM universe_membership um
            JOIN universe u ON u.symbol = um.symbol
            WHERE um.scope = ?
              AND NOT EXISTS (SELECT 1 FROM price_snapshots ps WHERE ps.symbol = um.symbol)
            ORDER BY
                CASE um.market WHEN 'twse' THEN 1 WHEN 'tpex' THEN 2 WHEN 'emerging' THEN 3 ELSE 9 END,
                CASE um.instrument_type WHEN '股票' THEN 1 WHEN 'ETF' THEN 2 ELSE 9 END,
                um.symbol
            """,
            (TW_FULL_MARKET_SCOPE,),
        ).fetchall()
    items = []
    by_market: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for row in rows:
        item = dict(row)
        try:
            metadata = json.loads(item.pop("metadata_json") or "{}")
        except json.JSONDecodeError:
            metadata = {}
        market = item.get("market") or "unknown"
        symbol = str(item.get("symbol") or "")
        source_symbol = str(item.get("source_symbol") or "")
        start_date_text = str(metadata.get("start_date") or "").strip()
        future_start_date = False
        if start_date_text:
            try:
                future_start_date = datetime.strptime(start_date_text, "%Y-%m-%d").date() > datetime.now(TAIPEI_TZ).date()
            except ValueError:
                future_start_date = False
        if future_start_date:
            reason = "future_listing_no_quote"
            attempted_sources = ["Aegis stock_master start_date", "official quote after listing date"]
        elif market == "emerging":
            reason = "missing_tpex_emerging_quote"
            attempted_sources = ["Aegis price_daily", "TPEx emerging latest statistics", "Yahoo Finance"]
        elif item.get("instrument_type") == "ETF":
            reason = "missing_etf_quote"
            attempted_sources = ["Aegis price_daily", "TWSE/TPEX official quote", "Yahoo Finance .TW/.TWO"]
        elif not re.fullmatch(r"\d{4}\.TW", symbol):
            reason = "symbol_format_needs_review"
            attempted_sources = ["canonical symbol normalization", "Aegis stock_master"]
        elif source_symbol and source_symbol != symbol.split(".", 1)[0]:
            reason = "source_symbol_mapping_needs_review"
            attempted_sources = ["Aegis stock_master", "universe_membership metadata"]
        else:
            reason = "missing_official_quote"
            attempted_sources = ["Aegis price_daily", "TPEx mainboard quote", "Yahoo Finance .TW/.TWO"]
        item.update(
            {
                "reason": reason,
                "attempted_sources": attempted_sources,
                "metadata": metadata,
                "action": "keep_in_universe_but_exclude_from_opportunity_scan_until_quote_available",
                "manual_review_priority": "high" if reason.endswith("needs_review") else "normal",
            }
        )
        by_market[market] = by_market.get(market, 0) + 1
        by_reason[reason] = by_reason.get(reason, 0) + 1
        items.append(item)
    return {
        "generated_at": utc_now(),
        "scope": TW_FULL_MARKET_SCOPE,
        "count": len(items),
        "summary": {
            "by_market": [{"market": key, "count": value} for key, value in sorted(by_market.items())],
            "by_reason": [{"reason": key, "count": value} for key, value in sorted(by_reason.items())],
        },
        "items": items,
        "note": "These symbols remain in the full-market membership but are excluded from opportunity ranking until an official quote exists.",
    }


def normalize_tw_stock_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if re.fullmatch(r"\d{4}", value):
        return f"{value}.TW"
    return value

