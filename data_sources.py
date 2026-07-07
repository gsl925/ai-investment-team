from __future__ import annotations

import re
import sqlite3
import statistics
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import requests

from common import (
    HTTP, HTTP_TIMEOUT, NEWS_KEYWORDS, TAIPEI_TZ, YAHOO_HEADERS,
    aegis_connect, classify_asset, db_connect, market_number,
    safe_float, taiwan_stock_id, yahoo_get, yahoo_get_query2,
)
from models import AegisFundamentals, Quote
from scoring import requires_equity_fundamentals
from tw_universe import TW_FULL_MARKET_SCOPE

_tpex_mainboard_cache: list[dict[str, Any]] | None = None
_tpex_esb_cache: list[dict[str, Any]] | None = None


def roc_date_to_datetime(value: str | None) -> datetime:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{7}", text):
        year = int(text[:3]) + 1911
        month = int(text[3:5])
        day = int(text[5:7])
        return datetime(year, month, day, tzinfo=TAIPEI_TZ)
    return datetime.now(TAIPEI_TZ)


def get_universe_symbol_metadata(symbol: str) -> dict[str, Any]:
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT u.symbol, u.name, u.asset_type, u.currency, u.sector, u.industry,
                   um.market, um.instrument_type
            FROM universe u
            LEFT JOIN universe_membership um
              ON um.symbol = u.symbol AND um.scope = ?
            WHERE u.symbol = ?
            LIMIT 1
            """,
            (TW_FULL_MARKET_SCOPE, symbol.upper()),
        ).fetchone()
    return dict(row) if row else {}


def _get_aegis_margins(stock_id: str) -> tuple[float | None, float | None, str | None]:
    """Return (gross_margin, operating_margin, latest_date) from api_cache TaiwanStockFinancialStatements."""
    conn = aegis_connect()
    if conn is None:
        return None, None, None
    try:
        row = conn.execute(
            "SELECT payload_json FROM api_cache WHERE dataset='TaiwanStockFinancialStatements' AND params_json LIKE ? LIMIT 1",
            (f'%"data_id": "{stock_id}"%',),
        ).fetchone()
    except Exception:
        return None, None, None
    finally:
        conn.close()
    if not row:
        return None, None, None
    try:
        import json as _json
        data = _json.loads(row["payload_json"]).get("data", [])
    except Exception:
        return None, None, None
    if not data:
        return None, None, None
    # Build {date: {type: value}} for the types we need
    wanted = {"GrossProfit", "OperatingIncome", "Revenue"}
    by_date: dict[str, dict[str, float]] = {}
    for item in data:
        t = item.get("type", "")
        if t not in wanted:
            continue
        d = item.get("date", "")
        v = safe_float(item.get("value"))
        if d and v is not None:
            by_date.setdefault(d, {})[t] = v
    if not by_date:
        return None, None, None
    latest_date = max(by_date.keys())
    row_data = by_date[latest_date]
    rev = row_data.get("Revenue")
    gp = row_data.get("GrossProfit")
    oi = row_data.get("OperatingIncome")
    gross_margin = round(gp / rev, 4) if rev and gp is not None and rev != 0 else None
    operating_margin = round(oi / rev, 4) if rev and oi is not None and rev != 0 else None
    return gross_margin, operating_margin, latest_date


def get_aegis_fundamentals(symbol: str) -> AegisFundamentals | None:
    stock_id = taiwan_stock_id(symbol)
    if not stock_id:
        return None
    conn = aegis_connect()
    if conn is None:
        return None
    try:
        master = conn.execute(
            "SELECT stock_id, name, market, industry_group FROM stock_master WHERE stock_id = ? ORDER BY updated_at DESC LIMIT 1",
            (stock_id,),
        ).fetchone()
        # 5 quarters: latest for eps/year/quarter, top-4 for TTM, top-2 for QoQ
        eps_rows = conn.execute(
            "SELECT year, quarter, eps FROM eps_quarterly_raw WHERE stock_id = ? ORDER BY year DESC, quarter DESC LIMIT 5",
            (stock_id,),
        ).fetchall()
        # 14 months: top-2 for MoM, top-13 covers same-month-last-year for YoY
        rev_rows = conn.execute(
            "SELECT yyyymm, revenue FROM revenue_monthly_raw WHERE stock_id = ? ORDER BY yyyymm DESC LIMIT 14",
            (stock_id,),
        ).fetchall()
    finally:
        conn.close()

    # EPS
    latest_eps_row = eps_rows[0] if eps_rows else None
    eps_vals = [safe_float(r["eps"]) for r in eps_rows[:4]]
    eps_vals_clean = [v for v in eps_vals if v is not None]
    eps_ttm = round(sum(eps_vals_clean), 4) if len(eps_vals_clean) >= 4 else None

    eps_qoq = None
    if len(eps_rows) >= 2:
        q0 = safe_float(eps_rows[0]["eps"])
        q1 = safe_float(eps_rows[1]["eps"])
        if q0 is not None and q1 is not None and q1 != 0:
            eps_qoq = (q0 - q1) / abs(q1)

    # Revenue
    latest_rev_row = rev_rows[0] if rev_rows else None
    latest_revenue = safe_float(latest_rev_row["revenue"]) if latest_rev_row else None
    revenue_yyyymm = latest_rev_row["yyyymm"] if latest_rev_row else None

    revenue_mom = None
    if len(rev_rows) >= 2:
        r0 = safe_float(rev_rows[0]["revenue"])
        r1 = safe_float(rev_rows[1]["revenue"])
        if r0 is not None and r1 is not None and r1 != 0:
            revenue_mom = (r0 - r1) / abs(r1)

    revenue_yoy = None
    if revenue_yyyymm:
        y, m = int(revenue_yyyymm) // 100, int(revenue_yyyymm) % 100
        year_ago = f"{y - 1}{m:02d}"
        rev_by_month = {r["yyyymm"]: safe_float(r["revenue"]) for r in rev_rows}
        r_yoy = rev_by_month.get(year_ago)
        if latest_revenue is not None and r_yoy is not None and r_yoy != 0:
            revenue_yoy = (latest_revenue - r_yoy) / abs(r_yoy)

    gross_margin, operating_margin, financial_stmt_date = _get_aegis_margins(stock_id)

    return AegisFundamentals(
        stock_id=stock_id,
        name=master["name"] if master else None,
        market=master["market"] if master else None,
        industry_group=master["industry_group"] if master else None,
        latest_eps=safe_float(latest_eps_row["eps"]) if latest_eps_row else None,
        eps_year=int(latest_eps_row["year"]) if latest_eps_row else None,
        eps_quarter=int(latest_eps_row["quarter"]) if latest_eps_row else None,
        eps_qoq=eps_qoq,
        eps_ttm=eps_ttm,
        latest_revenue=latest_revenue,
        revenue_yyyymm=revenue_yyyymm,
        revenue_yoy=revenue_yoy,
        revenue_mom=revenue_mom,
        gross_margin=gross_margin,
        operating_margin=operating_margin,
        financial_stmt_date=financial_stmt_date,
    )


def get_aegis_chart(symbol: str, limit: int = 260) -> list[dict[str, float]]:
    stock_id = taiwan_stock_id(symbol)
    if not stock_id:
        return []
    conn = aegis_connect()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """
            SELECT date, open, high, low, close, volume, turnover, trade_count, change
            FROM price_daily
            WHERE stock_id = ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (stock_id, max(1, limit)),
        ).fetchall()
    finally:
        conn.close()
    chart = []
    for row in reversed(rows):
        close = safe_float(row["close"])
        if close is None:
            continue
        dt = datetime.fromisoformat(row["date"]).replace(tzinfo=TAIPEI_TZ)
        point: dict[str, float] = {"time": dt.timestamp(), "close": close}
        for key in ["open", "high", "low", "volume", "turnover", "trade_count", "change"]:
            value = safe_float(row[key])
            if value is not None:
                point[key] = value
        chart.append(point)
    return chart


def get_aegis_quote(symbol: str) -> Quote | None:
    stock_id = taiwan_stock_id(symbol)
    if not stock_id:
        return None
    chart = get_aegis_chart(symbol, limit=60)
    if not chart:
        return None
    fundamentals = get_aegis_fundamentals(symbol)
    latest = chart[-1]
    previous = chart[-2] if len(chart) >= 2 else None
    price = safe_float(latest.get("close"))
    previous_close = safe_float(previous.get("close")) if previous else None
    change_percent = (price / previous_close - 1) * 100 if price is not None and previous_close and previous_close > 0 else None
    volumes = [safe_float(point.get("volume")) for point in chart[-20:]]
    valid_volumes = [value for value in volumes if value is not None]
    avg_volume = statistics.fmean(valid_volumes) if valid_volumes else None
    is_fund = stock_id.startswith("00")
    return Quote(
        symbol=symbol.upper(),
        name=fundamentals.name if fundamentals and fundamentals.name else symbol.upper(),
        price=price,
        change_percent=change_percent,
        market_cap=None,
        pe=None,
        eps=fundamentals.latest_eps if fundamentals else None,
        dividend_yield=None,
        volume=safe_float(latest.get("volume")),
        avg_volume=avg_volume,
        currency="TWD",
        asset_type="台股/ETF" if is_fund else "台股",
        sector=fundamentals.market if fundamentals else None,
        industry=fundamentals.industry_group if fundamentals else None,
        quote_type="ETF" if is_fund else "EQUITY",
    )


def tpex_get_json(path: str) -> list[dict[str, Any]]:
    url = f"https://www.tpex.org.tw/openapi/v1/{path}"
    response = HTTP.get(url, headers=YAHOO_HEADERS, timeout=HTTP_TIMEOUT, verify=False)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, list) else []


def tpex_mainboard_quotes() -> list[dict[str, Any]]:
    global _tpex_mainboard_cache
    if _tpex_mainboard_cache is None:
        _tpex_mainboard_cache = tpex_get_json("tpex_mainboard_quotes")
    return _tpex_mainboard_cache


def tpex_esb_quotes() -> list[dict[str, Any]]:
    global _tpex_esb_cache
    if _tpex_esb_cache is None:
        _tpex_esb_cache = tpex_get_json("tpex_esb_latest_statistics")
    return _tpex_esb_cache


def get_tpex_quote_row(symbol: str) -> tuple[dict[str, Any] | None, str | None]:
    stock_id = taiwan_stock_id(symbol)
    if not stock_id:
        return None, None
    metadata = get_universe_symbol_metadata(symbol)
    market = metadata.get("market")
    datasets: list[tuple[str, list[dict[str, Any]]]] = []
    if market == "emerging":
        try:
            datasets.append(("TPEx emerging latest statistics", tpex_esb_quotes()))
        except requests.RequestException:
            return None, None
    else:
        try:
            datasets.append(("TPEx mainboard quotes", tpex_mainboard_quotes()))
            datasets.append(("TPEx emerging latest statistics", tpex_esb_quotes()))
        except requests.RequestException:
            return None, None
    for source, rows in datasets:
        for row in rows:
            if str(row.get("SecuritiesCompanyCode") or "").strip().upper() == stock_id:
                return row, source
    return None, None


def tpex_chart_point(row: dict[str, Any], source: str) -> dict[str, float] | None:
    if source == "TPEx emerging latest statistics":
        close = market_number(row.get("LatestPrice") or row.get("Average"))
        high = market_number(row.get("Highest"))
        low = market_number(row.get("Lowest"))
        volume = market_number(row.get("TransactionVolume"))
    else:
        close = market_number(row.get("Close"))
        high = market_number(row.get("High"))
        low = market_number(row.get("Low"))
        volume = market_number(row.get("TradingShares"))
    if close is None:
        return None
    dt = roc_date_to_datetime(row.get("Date"))
    point: dict[str, float] = {"time": dt.timestamp(), "close": close}
    open_value = market_number(row.get("Open"))
    if open_value is not None:
        point["open"] = open_value
    if high is not None:
        point["high"] = high
    if low is not None:
        point["low"] = low
    if volume is not None:
        point["volume"] = volume
    return point


def get_tpex_chart(symbol: str) -> list[dict[str, float]]:
    row, source = get_tpex_quote_row(symbol)
    if not row or not source:
        return []
    point = tpex_chart_point(row, source)
    return [point] if point else []


def get_tpex_quote(symbol: str) -> Quote | None:
    stock_id = taiwan_stock_id(symbol)
    if not stock_id:
        return None
    row, source = get_tpex_quote_row(symbol)
    if not row or not source:
        return None
    metadata = get_universe_symbol_metadata(symbol)
    if source == "TPEx emerging latest statistics":
        price = market_number(row.get("LatestPrice") or row.get("Average"))
        previous = market_number(row.get("PreviousAveragePrice"))
        volume = market_number(row.get("TransactionVolume"))
    else:
        price = market_number(row.get("Close"))
        change = market_number(row.get("Change"))
        previous = price - change if price is not None and change is not None else None
        volume = market_number(row.get("TradingShares"))
    if price is None:
        return None
    change_percent = (price / previous - 1) * 100 if previous and previous > 0 else None
    asset_type = metadata.get("asset_type") or ("台股/ETF" if stock_id.startswith(("00", "02")) else "台股")
    quote_type = "ETF" if asset_type == "台股/ETF" else "EQUITY"
    return Quote(
        symbol=symbol.upper(),
        name=row.get("CompanyName") or metadata.get("name") or symbol.upper(),
        price=price,
        change_percent=change_percent,
        market_cap=None,
        pe=None,
        eps=None,
        dividend_yield=None,
        volume=volume,
        avg_volume=None,
        currency="TWD",
        asset_type=asset_type,
        sector=metadata.get("sector") or source,
        industry=metadata.get("industry") or metadata.get("instrument_type"),
        quote_type=quote_type,
    )


def latest_yahoo_timeseries_rows(payload: dict[str, Any], field: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in payload.get("timeseries", {}).get("result", []) or []:
        if field in item:
            rows = item.get(field) or []
            break
    return sorted(rows, key=lambda row: row.get("asOfDate") or "")


def yahoo_reported_raw(row: dict[str, Any] | None) -> float | None:
    if not row:
        return None
    reported = row.get("reportedValue") or {}
    return safe_float(reported.get("raw"))


def quarter_from_date(value: str | None) -> tuple[int | None, int | None]:
    if not value:
        return None, None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None, None
    return dt.year, (dt.month - 1) // 3 + 1


def get_yahoo_equity_fundamentals(quote: Quote) -> AegisFundamentals | None:
    if not requires_equity_fundamentals(quote) or quote.asset_type != "美股/ETF":
        return None
    period2 = int(time.time()) + 86400
    period1 = period2 - 8 * 365 * 24 * 60 * 60
    types = ",".join(
        [
            "quarterlyDilutedEPS",
            "annualDilutedEPS",
            "quarterlyTotalRevenue",
            "annualTotalRevenue",
            "trailingAnnualDividendYield",
        ]
    )
    try:
        payload = yahoo_get_query2(
            f"/ws/fundamentals-timeseries/v1/finance/timeseries/{quote_plus(quote.symbol)}",
            {"type": types, "period1": str(period1), "period2": str(period2)},
        )
    except requests.RequestException:
        return None

    quarterly_eps = latest_yahoo_timeseries_rows(payload, "quarterlyDilutedEPS")
    annual_eps = latest_yahoo_timeseries_rows(payload, "annualDilutedEPS")
    quarterly_revenue = latest_yahoo_timeseries_rows(payload, "quarterlyTotalRevenue")
    dividend_rows = latest_yahoo_timeseries_rows(payload, "trailingAnnualDividendYield")

    latest_eps_row = quarterly_eps[-1] if quarterly_eps else None
    latest_eps = yahoo_reported_raw(latest_eps_row)
    previous_eps = yahoo_reported_raw(quarterly_eps[-2]) if len(quarterly_eps) >= 2 else None
    eps_qoq = None
    if latest_eps is not None and previous_eps not in {None, 0}:
        eps_qoq = latest_eps / previous_eps - 1
    eps_ttm = yahoo_reported_raw(annual_eps[-1]) if annual_eps else None
    if eps_ttm is None and len(quarterly_eps) >= 4:
        eps_values = [yahoo_reported_raw(row) for row in quarterly_eps[-4:]]
        if all(value is not None for value in eps_values):
            eps_ttm = sum(value for value in eps_values if value is not None)

    latest_revenue_row = quarterly_revenue[-1] if quarterly_revenue else None
    latest_revenue = yahoo_reported_raw(latest_revenue_row)
    previous_revenue = yahoo_reported_raw(quarterly_revenue[-2]) if len(quarterly_revenue) >= 2 else None
    revenue_mom = None
    if latest_revenue is not None and previous_revenue not in {None, 0}:
        revenue_mom = latest_revenue / previous_revenue - 1
    revenue_yoy = None
    if latest_revenue is not None and len(quarterly_revenue) >= 5:
        prior_year_revenue = yahoo_reported_raw(quarterly_revenue[-5])
        if prior_year_revenue not in {None, 0}:
            revenue_yoy = latest_revenue / prior_year_revenue - 1

    eps_year, eps_quarter = quarter_from_date(latest_eps_row.get("asOfDate") if latest_eps_row else None)
    dividend_yield = yahoo_reported_raw(dividend_rows[-1]) if dividend_rows else None
    if quote.dividend_yield is None and dividend_yield is not None:
        quote.dividend_yield = dividend_yield * 100 if dividend_yield < 1 else dividend_yield

    if not any(value is not None for value in [latest_eps, eps_ttm, latest_revenue, revenue_yoy, revenue_mom]):
        return None
    return AegisFundamentals(
        stock_id=quote.symbol,
        name=quote.name,
        market=quote.currency,
        industry_group=quote.industry,
        latest_eps=latest_eps,
        eps_year=eps_year,
        eps_quarter=eps_quarter,
        eps_qoq=eps_qoq,
        eps_ttm=eps_ttm,
        latest_revenue=latest_revenue,
        revenue_yyyymm=(latest_revenue_row.get("asOfDate") if latest_revenue_row else None),
        revenue_yoy=revenue_yoy,
        revenue_mom=revenue_mom,
        source="Yahoo Finance fundamentals-timeseries",
    )


def get_search_profile(symbol: str) -> dict[str, str | None]:
    try:
        payload = yahoo_get("/v1/finance/search", {"q": symbol, "quotesCount": "5", "newsCount": "0"})
    except requests.RequestException:
        return {"sector": None, "industry": None, "quote_type": None}
    for quote in payload.get("quotes", []):
        if str(quote.get("symbol", "")).upper() == symbol.upper():
            return {
                "sector": quote.get("sectorDisp") or quote.get("sector"),
                "industry": quote.get("industryDisp") or quote.get("industry"),
                "quote_type": quote.get("quoteType") or quote.get("typeDisp"),
            }
    return {"sector": None, "industry": None, "quote_type": None}


def get_quote(symbols: list[str]) -> dict[str, Quote]:
    quotes: dict[str, Quote] = {}
    for requested_symbol in symbols:
        payload = yahoo_get(
            f"/v8/finance/chart/{quote_plus(requested_symbol)}",
            {"range": "5d", "interval": "1d", "includePrePost": "false"},
        )
        results = payload.get("chart", {}).get("result") or []
        if not results:
            continue
        meta = results[0].get("meta", {})
        symbol = str(meta.get("symbol") or requested_symbol).upper()
        price = safe_float(meta.get("regularMarketPrice"))
        previous_close = safe_float(meta.get("chartPreviousClose"))
        change_percent = None
        if price is not None and previous_close and previous_close > 0:
            change_percent = (price / previous_close - 1) * 100
        profile = get_search_profile(symbol)
        quotes[symbol] = Quote(
            symbol=symbol,
            name=meta.get("shortName") or meta.get("longName") or symbol,
            price=price,
            change_percent=change_percent,
            market_cap=None,
            pe=None,
            eps=None,
            dividend_yield=None,
            volume=safe_float(meta.get("regularMarketVolume")),
            avg_volume=None,
            currency=meta.get("currency"),
            asset_type=classify_asset(symbol),
            sector=profile["sector"],
            industry=profile["industry"],
            quote_type=profile.get("quote_type"),
        )
    return quotes


def get_chart(symbol: str, range_value: str = "6mo") -> list[dict[str, float]]:
    payload = yahoo_get(
        f"/v8/finance/chart/{quote_plus(symbol)}",
        {"range": range_value, "interval": "1d", "includePrePost": "false"},
    )
    result = payload.get("chart", {}).get("result") or []
    if not result:
        return []
    timestamps = result[0].get("timestamp") or []
    quote_data = (result[0].get("indicators", {}).get("quote") or [{}])[0]
    opens = quote_data.get("open") or []
    highs = quote_data.get("high") or []
    lows = quote_data.get("low") or []
    closes = quote_data.get("close") or []
    volumes = quote_data.get("volume") or []
    chart: list[dict[str, float]] = []
    for idx, (ts, close) in enumerate(zip(timestamps, closes, strict=False)):
        price = safe_float(close)
        if price is not None:
            point: dict[str, float] = {"time": float(ts), "close": price}
            open_price = safe_float(opens[idx]) if idx < len(opens) else None
            high = safe_float(highs[idx]) if idx < len(highs) else None
            low = safe_float(lows[idx]) if idx < len(lows) else None
            volume = safe_float(volumes[idx]) if idx < len(volumes) else None
            if open_price is not None:
                point["open"] = open_price
            if high is not None:
                point["high"] = high
            if low is not None:
                point["low"] = low
            if volume is not None:
                point["volume"] = volume
            chart.append(point)
    return chart


def chart_range_for_backtest(days: int) -> str:
    if days <= 180:
        return "1y"
    if days <= 700:
        return "2y"
    if days <= 1700:
        return "5y"
    return "10y"


def get_news(symbol: str, name: str) -> list[dict[str, str]]:
    return get_news_for_query(f"{symbol} {name}", limit=8)


def get_news_for_query(query: str, limit: int = 8) -> list[dict[str, str]]:
    try:
        payload = yahoo_get(
            "/v1/finance/search",
            {"q": query, "quotesCount": "0", "newsCount": str(limit)},
        )
    except requests.RequestException:
        return []
    items = []
    for item in payload.get("news", [])[:limit]:
        title = str(item.get("title") or "").strip()
        link = str(item.get("link") or "").strip()
        published_at = safe_float(item.get("providerPublishTime"))
        published = ""
        if published_at:
            published = datetime.fromtimestamp(published_at, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        source = str(item.get("publisher") or "Yahoo Finance")
        items.append(
            {
                "title": re.sub(r"\s+", " ", title),
                "link": link,
                "published": published,
                "source": source,
                "sentiment": news_sentiment(title),
            }
        )
    return items


def get_mops_news(stock_id: str, limit: int = 5) -> list[dict[str, str]]:
    """Fetch major disclosures from MOPS for a Taiwan stock (4-digit code, e.g. '2330')."""
    items: list[dict[str, str]] = []
    try:
        resp = HTTP.post(
            "https://mops.twse.com.tw/mops/web/ajax_t05sr021",
            data={"stock_id": stock_id, "mtype": "F"},
            headers={
                **YAHOO_HEADERS,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": "https://mops.twse.com.tw/mops/web/t05sr021",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=8,
        )
        resp.raise_for_status()
        html = resp.text
        # Parse <tr> rows: extract all <td> text values per row
        for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE):
            cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.DOTALL)]
            cells = [re.sub(r"\s+", " ", c) for c in cells if c]
            # Expect at least: 序號, 公司代號, 公司名稱, 主旨, 發言日期[, ...]
            if len(cells) < 5:
                continue
            # Date column: find first cell matching YYYY/MM/DD or RRRR/MM/DD pattern
            date_str = ""
            subject = ""
            for j, cell in enumerate(cells):
                if re.fullmatch(r"\d{3,4}/\d{2}/\d{2}", cell):
                    date_str = cell
                    # Subject is typically 2 cells before the date (主旨 column)
                    subject = cells[j - 1] if j >= 1 else ""
                    break
            if not subject or not date_str:
                continue
            # Convert ROC or AD year to AD
            year_raw, mm, dd = date_str.split("/")
            year = int(year_raw) + (1911 if int(year_raw) < 1000 else 0)
            try:
                published_dt = datetime(year, int(mm), int(dd), tzinfo=timezone.utc)
                published = published_dt.strftime("%Y-%m-%d UTC")
            except ValueError:
                published = date_str
            link = f"https://mops.twse.com.tw/mops/web/t05sr021"
            items.append(
                {
                    "title": subject,
                    "link": link,
                    "published": published,
                    "source": "MOPS",
                    "sentiment": news_sentiment(subject),
                }
            )
            if len(items) >= limit:
                break
    except Exception:
        pass
    return items


def get_taiwan_stock_news(symbol: str, name: str, limit: int = 8) -> list[dict[str, str]]:
    """Get news for a Taiwan stock: merge Yahoo Finance + MOPS, deduplicated."""
    stock_id = taiwan_stock_id(symbol)
    yahoo_items = get_news_for_query(f"{symbol} {name}", limit=limit)
    if stock_id:
        mops_items = get_mops_news(stock_id, limit=5)
        combined = mops_items + yahoo_items
        return dedupe_news(combined)[:limit]
    return yahoo_items[:limit]


def dedupe_news(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    unique = []
    for item in items:
        key = item.get("link") or item.get("title") or ""
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def news_sentiment(text: str) -> str:
    lower = text.lower()
    pos = sum(1 for word in NEWS_KEYWORDS["positive"] if word.lower() in lower)
    neg = sum(1 for word in NEWS_KEYWORDS["negative"] if word.lower() in lower)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def get_peer_daily_changes(
    industry_group: str, exclude_stock_id: str | None = None
) -> dict[str, Any] | None:
    """
    Return the most recent trading-day % change distribution for all stocks in industry_group.
    Uses Aegis price_daily (1-day stale). Returns None if not enough data.
    """
    conn = aegis_connect()
    if conn is None:
        return None
    try:
        peer_rows = conn.execute(
            "SELECT stock_id FROM stock_master WHERE industry_group = ? ORDER BY stock_id LIMIT 300",
            (industry_group,),
        ).fetchall()
        if not peer_rows:
            return None
        peer_ids = [r["stock_id"] for r in peer_rows if r["stock_id"] != (exclude_stock_id or "")]
        if not peer_ids:
            return None
        placeholders = ",".join("?" * len(peer_ids))
        # Get last 10 calendar days so we always catch the latest 2 trading days
        latest_row = conn.execute(
            f"SELECT MAX(date) AS d FROM price_daily WHERE stock_id IN ({placeholders})",
            peer_ids,
        ).fetchone()
        if not latest_row or not latest_row["d"]:
            return None
        latest_date = latest_row["d"]
        price_rows = conn.execute(
            f"""SELECT stock_id, date, close FROM price_daily
                WHERE stock_id IN ({placeholders}) AND date >= date(?, '-15 days')
                ORDER BY stock_id, date DESC""",
            peer_ids + [latest_date],
        ).fetchall()
    finally:
        conn.close()

    by_stock: dict[str, list] = {}
    for row in price_rows:
        sid = row["stock_id"]
        if sid not in by_stock:
            by_stock[sid] = []
        if len(by_stock[sid]) < 2:
            by_stock[sid].append(row)

    changes: list[float] = []
    for sid, rows in by_stock.items():
        if len(rows) < 2:
            continue
        c0 = safe_float(rows[0]["close"])
        c1 = safe_float(rows[1]["close"])
        if c0 and c1 and c1 > 0:
            changes.append((c0 - c1) / c1)

    if not changes:
        return None

    THRESHOLD = 0.005
    up_count = sum(1 for c in changes if c > THRESHOLD)
    down_count = sum(1 for c in changes if c < -THRESHOLD)
    return {
        "peer_count": len(changes),
        "up_count": up_count,
        "down_count": down_count,
        "flat_count": len(changes) - up_count - down_count,
        "median_change": round(statistics.median(changes), 4),
        "latest_date": latest_date,
    }


def get_benchmark_daily_change(asset_type: str) -> dict[str, Any] | None:
    """
    Return the most recent daily change for the market benchmark via Aegis price_daily.
    Taiwan stocks → 0050 (大盤ETF proxy). Others → None (no Aegis data for SPY).
    """
    if asset_type not in {"台股", "台股/ETF"}:
        return None
    conn = aegis_connect()
    if conn is None:
        return None
    try:
        rows = conn.execute(
            "SELECT date, close FROM price_daily WHERE stock_id = '0050' ORDER BY date DESC LIMIT 2",
        ).fetchall()
    finally:
        conn.close()
    if len(rows) < 2:
        return None
    c0 = safe_float(rows[0]["close"])
    c1 = safe_float(rows[1]["close"])
    if not c0 or not c1 or c1 == 0:
        return None
    return {
        "benchmark": "0050.TW",
        "benchmark_label": "元大台灣50",
        "change": round((c0 - c1) / c1, 4),
        "date": rows[0]["date"],
    }


def get_industry_comparison(symbol: str) -> dict[str, Any] | None:
    """
    Level 1 peer comparison for a Taiwan stock within the same industry_group.
    Computes percentile ranks for GM, OM, EPS QoQ, Revenue YoY, EPS TTM.
    Returns None if symbol is not a Taiwan stock or has no industry_group.
    """
    import json as _json

    fund = get_aegis_fundamentals(symbol)
    if fund is None or not fund.industry_group:
        return None

    stock_id = fund.stock_id
    industry_group = fund.industry_group

    conn = aegis_connect()
    if conn is None:
        return None

    try:
        peer_rows = conn.execute(
            "SELECT stock_id, name FROM stock_master WHERE industry_group = ? ORDER BY stock_id LIMIT 300",
            (industry_group,),
        ).fetchall()
        if not peer_rows:
            return None

        peer_ids = [r["stock_id"] for r in peer_rows]
        peer_names = {r["stock_id"]: r["name"] for r in peer_rows}
        placeholders = ",".join("?" * len(peer_ids))

        eps_all = conn.execute(
            f"SELECT stock_id, year, quarter, eps FROM eps_quarterly_raw "
            f"WHERE stock_id IN ({placeholders}) ORDER BY stock_id, year DESC, quarter DESC",
            peer_ids,
        ).fetchall()

        rev_all = conn.execute(
            f"SELECT stock_id, yyyymm, revenue FROM revenue_monthly_raw "
            f"WHERE stock_id IN ({placeholders}) ORDER BY stock_id, yyyymm DESC",
            peer_ids,
        ).fetchall()

        # Two-step: first get params_json to find which rowids belong to peer stocks,
        # then fetch payload_json only for those rows.
        cache_params = conn.execute(
            "SELECT rowid, params_json FROM api_cache WHERE dataset='TaiwanStockFinancialStatements'",
        ).fetchall()
        peer_id_set = set(peer_ids)
        target_rowids: list[tuple[int, str]] = []
        for crow in cache_params:
            m = re.search(r'"data_id":\s*"(\d+)"', crow["params_json"] or "")
            if m and m.group(1) in peer_id_set:
                target_rowids.append((crow["rowid"], m.group(1)))

        gm_om_by_stock: dict[str, tuple[float | None, float | None]] = {}
        if target_rowids:
            rid_placeholders = ",".join("?" * len(target_rowids))
            rid_list = [r[0] for r in target_rowids]
            rid_to_sid = {r[0]: r[1] for r in target_rowids}
            wanted = {"GrossProfit", "OperatingIncome", "Revenue"}
            payload_rows = conn.execute(
                f"SELECT rowid, payload_json FROM api_cache WHERE rowid IN ({rid_placeholders})",
                rid_list,
            ).fetchall()
            for prow in payload_rows:
                sid = rid_to_sid.get(prow["rowid"])
                if not sid:
                    continue
                try:
                    data = _json.loads(prow["payload_json"] or "{}").get("data", [])
                except Exception:
                    continue
                by_date: dict[str, dict[str, float]] = {}
                for item in data:
                    t = item.get("type", "")
                    if t not in wanted:
                        continue
                    d = item.get("date", "")
                    v = safe_float(item.get("value"))
                    if d and v is not None:
                        if d not in by_date:
                            by_date[d] = {}
                        by_date[d][t] = v
                for dt in sorted(by_date.keys(), reverse=True):
                    fields = by_date[dt]
                    rev = fields.get("Revenue")
                    gp = fields.get("GrossProfit")
                    oi = fields.get("OperatingIncome")
                    if rev and rev != 0 and (gp is not None or oi is not None):
                        gm = round(gp / rev, 4) if gp is not None else None
                        om = round(oi / rev, 4) if oi is not None else None
                        gm_om_by_stock[sid] = (gm, om)
                        break
    finally:
        conn.close()

    # Group EPS rows by stock (take most recent 5)
    eps_by_stock: dict[str, list] = {}
    for row in eps_all:
        sid = row["stock_id"]
        if sid not in eps_by_stock:
            eps_by_stock[sid] = []
        if len(eps_by_stock[sid]) < 5:
            eps_by_stock[sid].append(row)

    # Group Revenue rows by stock (take most recent 14)
    rev_by_stock: dict[str, list] = {}
    for row in rev_all:
        sid = row["stock_id"]
        if sid not in rev_by_stock:
            rev_by_stock[sid] = []
        if len(rev_by_stock[sid]) < 14:
            rev_by_stock[sid].append(row)

    # Compute metrics for each peer
    peers_data: list[dict[str, Any]] = []
    for sid in peer_ids:
        eps_rows_peer = eps_by_stock.get(sid, [])
        rev_rows_peer = rev_by_stock.get(sid, [])
        gm_val, om_val = gm_om_by_stock.get(sid, (None, None))

        eps_qoq_peer = None
        if len(eps_rows_peer) >= 2:
            q0 = safe_float(eps_rows_peer[0]["eps"])
            q1 = safe_float(eps_rows_peer[1]["eps"])
            if q0 is not None and q1 is not None and q1 != 0:
                eps_qoq_peer = round((q0 - q1) / abs(q1), 4)

        eps_ttm_peer = None
        eps_vals_clean = [v for v in [safe_float(r["eps"]) for r in eps_rows_peer[:4]] if v is not None]
        if len(eps_vals_clean) >= 4:
            eps_ttm_peer = round(sum(eps_vals_clean), 4)

        revenue_yoy_peer = None
        if rev_rows_peer:
            r0_row = rev_rows_peer[0]
            r0_val = safe_float(r0_row["revenue"])
            r0_yyyymm = r0_row["yyyymm"]
            if r0_yyyymm and r0_val is not None:
                try:
                    y_val = int(r0_yyyymm) // 100
                    m_val = int(r0_yyyymm) % 100
                    year_ago = f"{y_val - 1}{m_val:02d}"
                    rev_by_month_peer = {r["yyyymm"]: safe_float(r["revenue"]) for r in rev_rows_peer}
                    r_yoy = rev_by_month_peer.get(year_ago)
                    if r_yoy is not None and r_yoy != 0:
                        revenue_yoy_peer = round((r0_val - r_yoy) / abs(r_yoy), 4)
                except (ValueError, ZeroDivisionError):
                    pass

        if not any(v is not None for v in [gm_val, om_val, eps_qoq_peer, revenue_yoy_peer, eps_ttm_peer]):
            continue

        peers_data.append({
            "stock_id": sid,
            "name": peer_names.get(sid),
            "gross_margin": gm_val,
            "operating_margin": om_val,
            "eps_qoq": eps_qoq_peer,
            "revenue_yoy": revenue_yoy_peer,
            "eps_ttm": eps_ttm_peer,
            "is_target": sid == stock_id,
        })

    if not peers_data:
        return None

    def _pct_rank(target_val: float | None, key: str) -> int | None:
        if target_val is None:
            return None
        vals = [p[key] for p in peers_data if p[key] is not None]
        if not vals:
            return None
        return round(sum(1 for v in vals if v < target_val) / len(vals) * 100)

    metrics = ["gross_margin", "operating_margin", "eps_qoq", "revenue_yoy", "eps_ttm"]
    metric_labels = {
        "gross_margin": "毛利率",
        "operating_margin": "營業利益率",
        "eps_qoq": "EPS QoQ",
        "revenue_yoy": "營收 YoY",
        "eps_ttm": "EPS TTM",
    }

    target = {
        "gross_margin": fund.gross_margin,
        "operating_margin": fund.operating_margin,
        "eps_qoq": fund.eps_qoq,
        "revenue_yoy": fund.revenue_yoy,
        "eps_ttm": fund.eps_ttm,
    }

    percentiles: dict[str, int | None] = {}
    ranks: dict[str, str] = {}
    medians: dict[str, float | None] = {}
    for m in metrics:
        pct = _pct_rank(target.get(m), m)
        percentiles[m] = pct
        if pct is None:
            ranks[m] = "N/A"
        elif pct >= 75:
            ranks[m] = "高"
        elif pct <= 25:
            ranks[m] = "低"
        else:
            ranks[m] = "中"
        vals = [p[m] for p in peers_data if p[m] is not None]
        medians[m] = round(statistics.median(vals), 4) if vals else None

    outlier_flags: list[dict[str, Any]] = []
    for m in metrics:
        pct = percentiles.get(m)
        val = target.get(m)
        med = medians.get(m)
        if pct is None or val is None or med is None:
            continue
        if pct >= 80 or pct <= 20:
            direction = "high" if pct >= 80 else "low"
            m_label = metric_labels[m]
            if m in ("gross_margin", "operating_margin"):
                val_str = f"{val:.1%}"
                med_str = f"{med:.1%}"
            elif m in ("eps_qoq", "revenue_yoy"):
                val_str = f"{val:+.1%}"
                med_str = f"{med:+.1%}"
            else:
                val_str = f"{val:.2f}"
                med_str = f"{med:.2f}"
            if direction == "high":
                insight = f"{m_label} {val_str} 位居同業前 {100 - pct:.0f}%，高於{industry_group}中位數（{med_str}）"
            else:
                insight = f"{m_label} {val_str} 位居同業後 {pct:.0f}%，低於{industry_group}中位數（{med_str}）"
            outlier_flags.append({
                "metric": m,
                "metric_label": m_label,
                "value": val,
                "percentile": pct,
                "median": med,
                "direction": direction,
                "insight": insight,
            })

    top_peers = sorted(
        peers_data,
        key=lambda p: (not p["is_target"], -(p["gross_margin"] if p["gross_margin"] is not None else -999)),
    )[:20]

    return {
        "symbol": symbol,
        "stock_id": stock_id,
        "name": fund.name,
        "industry_group": industry_group,
        "peer_count": len(peer_ids),
        "data_count": len(peers_data),
        "target": target,
        "percentiles": percentiles,
        "ranks": ranks,
        "medians": medians,
        "outlier_flags": outlier_flags,
        "top_peers": top_peers,
    }
