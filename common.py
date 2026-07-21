from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DB_PATH = ROOT / "runtime_db" / "investment_live.db"
AEGIS_SNAPSHOT_DB = ROOT / "external_data" / "aegis_trader_snapshot.db"
AEGIS_SNAPSHOT_METADATA = ROOT / "external_data" / "aegis_trader_snapshot.metadata.json"
AEGIS_SOURCE_DB = Path(os.getenv("AEGIS_TRADER_DB", r"D:\_Personal\_Coding\_Python\AegisTrader\data\aegis_trader.db"))
AEGIS_REFRESH_SCRIPT = ROOT / "refresh_aegis_snapshot.py"
EXPORT_DIR = ROOT / "research_exports"
PORT = 8765
HTTP_TIMEOUT = 12
SCHEDULER_LOCK = threading.Lock()
SCHEDULER_STOP = threading.Event()
SCHEDULER_STATE: dict[str, Any] = {
    "enabled": False,
    "running": False,
    "interval_minutes": 30,
    "batch_size": 25,
    "refresh_minutes": 60,
    "min_priority": 25,
    "asset_type": None,
    "scope": None,
    "started_at": None,
    "last_run_at": None,
    "next_run_at": None,
    "last_result": None,
    "last_error": None,
    "run_count": 0,
    "cursor_offset": 0,
    "active_etf": None,
}
WATCHLIST_LOCK = threading.Lock()
WATCHLIST_THREAD: threading.Thread | None = None
WATCHLIST_STOP = threading.Event()
WATCHLIST_STATE: dict[str, Any] = {
    "enabled": False,
    "running": False,
    "interval_minutes": 5,
    "threshold_pct": 2.0,
    "started_at": None,
    "last_run_at": None,
    "next_run_at": None,
    "last_result": None,
    "last_error": None,
    "symbols_monitored": [],
}
WATCHLIST_BASELINES: dict[str, dict[str, Any]] = {}
TAIPEI_TZ = timezone(timedelta(hours=8))
ACTIVE_ETF_IMPORT_SLOTS = ["16:40", "20:40"]
DAILY_RECOMMENDATION_LOG_TIME = "09:00"
ACTIVE_ETF_IMPORT_STATE: dict[str, Any] = {
    "enabled": True,
    "slots": ACTIVE_ETF_IMPORT_SLOTS,
    "last_run_at": None,
    "last_slot": None,
    "last_result": None,
    "last_error": None,
    "completed_slots": [],
}
YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) InvestmentHelper/1.0"
}
HTTP = requests.Session()
HTTP.trust_env = False
TPEX_MAINBOARD_QUOTES_CACHE: list[dict[str, Any]] | None = None
TPEX_ESB_QUOTES_CACHE: list[dict[str, Any]] | None = None

FACTOR_WEIGHTS = {
    "trend": 0.20,
    "momentum": 0.18,
    "value": 0.12,
    "quality": 0.12,
    "risk": 0.14,
    "sentiment": 0.08,
    "liquidity": 0.08,
    "active_etf_flow": 0.08,
}
FACTOR_LABELS = {
    "trend": "趨勢",
    "momentum": "動能",
    "value": "估值",
    "quality": "品質",
    "risk": "風險",
    "sentiment": "情緒",
    "liquidity": "流動性",
    "active_etf_flow": "ETF流向",
}
FACTOR_REQUIRED_FIELDS = {
    "trend": [
        "price",
        "sma_20",
        "sma_60",
        "ema_12",
        "ema_26",
        "macd_histogram",
        "macd_histogram_slope",
        "adx_14",
        "plus_di",
        "minus_di",
    ],
    "momentum": ["change_1m", "rsi_14", "stoch_k", "stoch_d", "stoch_spread", "rs_1m_percentile", "rs_3m_percentile"],
    "value": ["pe", "dividend_yield", "eps_ttm"],
    "quality": ["sector", "industry", "eps", "eps_qoq", "revenue_yoy", "revenue_mom"],
    "risk": ["volatility", "change_3m", "atr_14", "atr_percent", "bollinger_width", "hurst_60", "volatility_regime_percentile"],
    "sentiment": ["news_count", "positive_news", "negative_news"],
    "liquidity": ["volume", "avg_volume", "volume_ratio", "obv_slope", "vwap_20"],
    "active_etf_flow": ["active_etf_change_count", "active_etf_avg_score"],
}
FINANCIAL_AUDIT_RULES = {
    "pass": [
        "Traditional company financials are required and sourced.",
        "Value/quality data is not blocked by missing source.",
        "Monthly revenue is not older than 4 months when present.",
        "Quarterly EPS is not older than 3 quarters when present.",
        "Critical missing fields do not leave value or quality confidence at none/low.",
    ],
    "warn": [
        "Financials are sourced but materially partial.",
        "Critical fields such as pe, eps, eps_ttm, revenue_yoy, or revenue_mom are missing and value/quality confidence is none/low.",
        "Monthly revenue is older than 4 months, or quarterly EPS is older than 3 quarters.",
        "Warn caps financial-confidence-sensitive recommendations below A and should prevent Buy Candidate promotion unless other validated data is strong.",
    ],
    "block": [
        "Traditional company financials are required but no usable financial source is available.",
        "Both value and quality confidence are none for an equity symbol that requires financials.",
        "Block must prevent Buy Candidate and should cap to Watch/Risk Watch until data is fixed.",
    ],
    "not_applicable": [
        "Traditional single-company financials are not required for ETFs, funds, commodities, or crypto.",
        "These assets must be evaluated with asset-specific data instead of EPS/PE requirements.",
    ],
}


ASSET_PRESETS = {
    "us": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "SPY", "QQQ"],
    "tw": ["2330.TW", "2317.TW", "2454.TW", "2308.TW", "0050.TW", "006208.TW"],
    "commodity": ["GC=F", "SI=F", "CL=F", "BZ=F", "HG=F", "ZW=F"],
    "crypto": ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD"],
}

EXPANDED_UNIVERSE = {
    "us_core": [
        "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "AVGO", "BRK-B",
        "LLY", "JPM", "V", "MA", "XOM", "UNH", "COST", "WMT", "HD", "PG",
        "JNJ", "ABBV", "BAC", "KO", "NFLX", "CRM", "ORCL", "AMD", "ADBE", "CSCO",
        "PEP", "TMO", "ACN", "MCD", "LIN", "ABT", "WFC", "DIS", "INTU", "IBM",
        "GE", "NOW", "QCOM", "TXN", "AMAT", "CAT", "VZ", "PFE", "UBER", "MU",
        "SPY", "QQQ", "DIA", "IWM", "VTI", "VOO", "IVV", "XLK", "XLF", "XLE",
        "XLV", "XLY", "XLP", "XLI", "XLU", "SMH", "SOXX", "ARKK", "TLT", "HYG",
        "LQD", "GLD", "SLV", "USO", "VNQ",
    ],
    "tw_core": [
        "2330.TW", "2317.TW", "2454.TW", "2308.TW", "2382.TW", "2412.TW", "2881.TW", "2882.TW",
        "2886.TW", "2891.TW", "2884.TW", "2885.TW", "2880.TW", "1301.TW", "1303.TW", "2002.TW",
        "2207.TW", "2303.TW", "2327.TW", "2357.TW", "2379.TW", "2395.TW", "2408.TW", "2603.TW",
        "2609.TW", "2615.TW", "3008.TW", "3034.TW", "3045.TW", "3711.TW", "4904.TW", "5871.TW",
        "5880.TW", "6505.TW", "6669.TW", "8046.TW", "0050.TW", "0056.TW", "006208.TW", "00878.TW",
        "00919.TW", "00929.TW",
    ],
    "crypto_core": [
        "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD", "ADA-USD", "DOGE-USD", "TRX-USD",
        "LINK-USD", "AVAX-USD", "XLM-USD", "DOT-USD", "BCH-USD", "LTC-USD", "UNI-USD", "AAVE-USD",
        "ETC-USD", "FIL-USD", "ATOM-USD", "NEAR-USD",
    ],
    "commodity_core": ["GC=F", "SI=F", "CL=F", "BZ=F", "NG=F", "HG=F", "PL=F", "PA=F", "ZC=F", "ZS=F", "ZW=F"],
}

YAHOO_SCREENER_IDS = [
    "most_actives",
    "day_gainers",
    "day_losers",
    "undervalued_growth_stocks",
    "growth_technology_stocks",
    "aggressive_small_caps",
    "portfolio_anchors",
    "solid_large_growth_funds",
    "top_mutual_funds",
    "high_yield_bond",
]


NEWS_KEYWORDS = {
    "positive": [
        "beat",
        "surge",
        "upgrade",
        "growth",
        "record",
        "profit",
        "approval",
        "合作",
        "成長",
        "上修",
        "創高",
        "獲利",
        "優於",
        "買進",
    ],
    "negative": [
        "miss",
        "downgrade",
        "fall",
        "lawsuit",
        "probe",
        "loss",
        "recall",
        "裁員",
        "下修",
        "衰退",
        "虧損",
        "調查",
        "賣出",
        "不如",
    ],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp_slug(value: str | None = None) -> str:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%d_%H%M%S")


def ensure_export_dirs() -> None:
    for name in ["scans", "reports", "jsonl", "data_audits"]:
        (EXPORT_DIR / name).mkdir(parents=True, exist_ok=True)


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return None
        return result
    except (TypeError, ValueError):
        return None


def market_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "----", "N/A", "null"}:
        return None
    return safe_float(text)


def parse_utc_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Domain helpers (used by multiple modules)
# ---------------------------------------------------------------------------

def export_data_audit_markdown(audit: dict[str, Any]) -> str:
    ensure_export_dirs()
    imported_at = audit.get("imported_at") or utc_now()
    slug = timestamp_slug(imported_at)
    dataset = re.sub(r"[^A-Za-z0-9_.-]+", "_", audit.get("dataset") or "dataset")
    path = EXPORT_DIR / "data_audits" / f"{dataset}_{slug}.md"
    warnings = audit.get("warnings") or []
    errors = audit.get("errors") or []
    stats = audit.get("stats") or {}
    lines = [
        f"# Data Audit - {audit.get('dataset')}",
        "",
        f"- Imported at: {imported_at}",
        f"- Status: {audit.get('status')}",
        f"- Source file: {audit.get('source_file') or '-'}",
        f"- Source URL: {audit.get('source_url') or '-'}",
        f"- Source label: {audit.get('source_label') or '-'}",
        f"- Rows: {audit.get('row_count', 0)}",
        f"- Inserted: {audit.get('inserted', 0)}",
        f"- Updated: {audit.get('updated', 0)}",
        f"- Skipped: {audit.get('skipped', 0)}",
        f"- Warnings: {len(warnings)}",
        f"- Errors: {len(errors)}",
        "",
        "## Fields",
        "",
        ", ".join(audit.get("fields") or []) or "-",
        "",
        "## Stats",
        "",
        "```json",
        json.dumps(stats, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Warnings",
        "",
    ]
    if warnings:
        lines.extend(f"- row {item.get('row')}: {item.get('field')} = {item.get('value')} - {item.get('message')}" for item in warnings[:200])
    else:
        lines.append("- none")
    lines.extend(["", "## Errors", ""])
    if errors:
        lines.extend(f"- {item}" for item in errors[:200])
    else:
        lines.append("- none")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def record_data_import_audit(audit: dict[str, Any]) -> dict[str, Any]:
    imported_at = audit.get("imported_at") or utc_now()
    audit["imported_at"] = imported_at
    markdown_path = export_data_audit_markdown(audit)
    audit["markdown_path"] = markdown_path
    warnings = audit.get("warnings") or []
    errors = audit.get("errors") or []
    with db_connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO data_import_audits (
                dataset, source_file, source_url, source_label, imported_at, status,
                row_count, inserted, updated, skipped, warning_count, error_count,
                fields_json, warnings_json, errors_json, stats_json, result_json, markdown_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit.get("dataset"),
                audit.get("source_file"),
                audit.get("source_url"),
                audit.get("source_label"),
                imported_at,
                audit.get("status", "ok"),
                int(audit.get("row_count") or 0),
                int(audit.get("inserted") or 0),
                int(audit.get("updated") or 0),
                int(audit.get("skipped") or 0),
                len(warnings),
                len(errors),
                json.dumps(audit.get("fields") or [], ensure_ascii=False, separators=(",", ":")),
                json.dumps(warnings, ensure_ascii=False, separators=(",", ":")),
                json.dumps(errors, ensure_ascii=False, separators=(",", ":")),
                json.dumps(audit.get("stats") or {}, ensure_ascii=False, separators=(",", ":")),
                json.dumps(audit.get("result") or {}, ensure_ascii=False, separators=(",", ":")),
                markdown_path,
            ),
        )
        audit_id = cursor.lastrowid
    audit["id"] = audit_id
    return audit


def classify_asset(symbol: str) -> str:
    upper = symbol.upper()
    if upper.endswith((".TW", ".TWO")):
        return "台股"
    if upper.endswith("-USD") or upper.endswith("-USDT"):
        return "虛擬貨幣"
    if upper.endswith("=F"):
        return "原物料"
    return "美股/ETF"


def aegis_connect() -> sqlite3.Connection | None:
    if not AEGIS_SNAPSHOT_DB.exists():
        return None
    conn = sqlite3.connect(f"file:{AEGIS_SNAPSHOT_DB}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def yahoo_get(path: str, params: dict[str, str]) -> dict[str, Any]:
    url = f"https://query1.finance.yahoo.com{path}"
    response = HTTP.get(url, params=params, headers=YAHOO_HEADERS, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    return response.json()


def yahoo_get_query2(path: str, params: dict[str, str]) -> dict[str, Any]:
    url = f"https://query2.finance.yahoo.com{path}"
    response = HTTP.get(url, params=params, headers=YAHOO_HEADERS, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    return response.json()


def taiwan_stock_id(symbol: str) -> str | None:
    upper = symbol.upper()
    if upper.endswith(".TW") or upper.endswith(".TWO"):
        return upper.split(".", 1)[0]
    if re.fullmatch(r"\d{4}", upper):
        return upper
    return None


def is_fund_like_symbol(symbol: str, quote_type: str | None = None) -> bool:
    normalized_type = str(quote_type or "").upper()
    if normalized_type in {"ETF", "MUTUALFUND", "MUTUAL FUND", "FUND"}:
        return True
    stock_id = taiwan_stock_id(symbol)
    if stock_id and stock_id.startswith("00"):
        return True
    return False


def write_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

