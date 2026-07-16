from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from common import (
    ROOT, EXPORT_DIR, TAIPEI_TZ, DAILY_RECOMMENDATION_LOG_TIME,
    FACTOR_WEIGHTS, FACTOR_LABELS,
    utc_now, timestamp_slug, ensure_export_dirs, db_connect,
    safe_float, write_text_file, parse_utc_timestamp,
)
from outcomes import OUTCOME_HORIZONS, add_recommendation_outcomes
from tw_universe import TW_FULL_MARKET_SCOPE, TW_FULL_MARKET_POLICY
from scoring import opportunity_grade

def daily_recommendation_log_paths(day: str) -> dict[str, Path]:
    directory = EXPORT_DIR / "daily_recommendations"
    return {
        "directory": directory,
        "markdown": directory / f"recommendations_{day.replace('-', '')}.md",
        "jsonl": directory / "recommendations.jsonl",
    }


def recommendation_log_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": item.get("symbol"),
        "name": item.get("name"),
        "tier": item.get("tier"),
        "market": item.get("market"),
        "instrument_type": item.get("instrument_type"),
        "score": item.get("score"),
        "priority": item.get("priority"),
        "price": item.get("price"),
        "change_1d": item.get("change_1d"),
        "change_5d": item.get("change_5d"),
        "volume": item.get("volume"),
        "avg_volume": item.get("avg_volume"),
        "rs_1m_percentile": item.get("rs_1m_percentile"),
        "rs_3m_percentile": item.get("rs_3m_percentile"),
        "adx_14": item.get("adx_14"),
        "plus_di": item.get("plus_di"),
        "minus_di": item.get("minus_di"),
        "volatility_regime_percentile": item.get("volatility_regime_percentile"),
        "financial_audit_status": item.get("financial_audit_status"),
        "data_confidence": item.get("data_confidence"),
        "data_coverage": item.get("data_coverage"),
        "captured_at": item.get("captured_at"),
        "thesis": item.get("thesis"),
        "active_etf_change_count": item.get("active_etf_change_count"),
        "active_etf_avg_score": item.get("active_etf_avg_score"),
        "active_etf_note": item.get("active_etf_note"),
    }


def write_daily_recommendation_log(force: bool = False, limit: int = 50) -> dict[str, Any]:
    from app import get_tw_full_market_research_shortlist  # circular dep break
    local_now = datetime.now(TAIPEI_TZ)
    day = local_now.strftime("%Y-%m-%d")
    paths = daily_recommendation_log_paths(day)
    if paths["markdown"].exists() and not force:
        return {
            "ran": False,
            "reason": "already_exists",
            "scheduled_time": DAILY_RECOMMENDATION_LOG_TIME,
            "date": day,
            "markdown_path": str(paths["markdown"].relative_to(ROOT)),
            "jsonl_path": str(paths["jsonl"].relative_to(ROOT)),
        }

    payload = get_tw_full_market_research_shortlist(limit=limit)
    rows = [recommendation_log_row(item) for item in payload.get("shortlist", [])]
    generated_at = local_now.isoformat()
    record = {
        "date": day,
        "generated_at": generated_at,
        "scheduled_time": DAILY_RECOMMENDATION_LOG_TIME,
        "scope": TW_FULL_MARKET_SCOPE,
        "definition": TW_FULL_MARKET_POLICY,
        "status": payload.get("status"),
        "rules": payload.get("rules"),
        "counts": payload.get("counts"),
        "recommendations": rows,
        "note": payload.get("note"),
    }

    paths["directory"].mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Daily recommendations {day}",
        "",
        f"- Generated at: {generated_at}",
        f"- Scope: {TW_FULL_MARKET_SCOPE}",
        f"- Full-market definition: {', '.join(TW_FULL_MARKET_POLICY['included'])}",
        f"- Scheduled log time: {DAILY_RECOMMENDATION_LOG_TIME} Asia/Taipei",
        f"- Snapshot coverage: {payload.get('status', {}).get('snapshot_coverage_percent')}%",
        f"- Tradable quote coverage: {payload.get('status', {}).get('tradable_quote_coverage_percent')}%",
        f"- Strict candidates: {payload.get('counts', {}).get('strict_candidate_count', 0)}",
        "",
        "This is a research log for future review/backtesting, not personalized financial advice or an order instruction.",
        "",
        "| Rank | Symbol | Name | Tier | Score | Price | 1D | RS 3M | ADX | Volume | Financial | Active ETF flow | Thesis |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for index, row in enumerate(rows, 1):
        lines.append(
            "| {rank} | {symbol} | {name} | {tier} | {score} | {price} | {change_1d} | {rs_3m} | {adx} | {volume} | {financial} | {etf_flow} | {thesis} |".format(
                rank=index,
                symbol=row.get("symbol") or "",
                name=(row.get("name") or "").replace("|", "\\|"),
                tier=row.get("tier") or "",
                score=row.get("score") if row.get("score") is not None else "",
                price=row.get("price") if row.get("price") is not None else "",
                change_1d=row.get("change_1d") if row.get("change_1d") is not None else "",
                rs_3m=row.get("rs_3m_percentile") if row.get("rs_3m_percentile") is not None else "",
                adx=row.get("adx_14") if row.get("adx_14") is not None else "",
                volume=row.get("volume") if row.get("volume") is not None else "",
                financial=row.get("financial_audit_status") or "",
                etf_flow=(row.get("active_etf_note") or "").replace("|", "\\|"),
                thesis=(row.get("thesis") or "").replace("|", "\\|"),
            )
        )
    paths["markdown"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Keep one record per date: drop any existing line for the same day, then append the latest.
    kept_lines: list[str] = []
    if paths["jsonl"].exists():
        for existing in paths["jsonl"].read_text(encoding="utf-8").splitlines():
            if not existing.strip():
                continue
            try:
                if json.loads(existing).get("date") == day:
                    continue
            except json.JSONDecodeError:
                continue
            kept_lines.append(existing)
    kept_lines.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    paths["jsonl"].write_text("\n".join(kept_lines) + "\n", encoding="utf-8")
    return {
        "ran": True,
        "date": day,
        "generated_at": generated_at,
        "scheduled_time": DAILY_RECOMMENDATION_LOG_TIME,
        "markdown_path": str(paths["markdown"].relative_to(ROOT)),
        "jsonl_path": str(paths["jsonl"].relative_to(ROOT)),
        "recommendation_count": len(rows),
        "counts": payload.get("counts"),
    }


def read_daily_recommendation_log_history(limit: int = 20) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    paths = daily_recommendation_log_paths(datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d"))
    jsonl_path = paths["jsonl"]
    if not jsonl_path.exists():
        return {
            "path": str(jsonl_path.relative_to(ROOT)),
            "log_count": 0,
            "records": [],
            "summary": {
                "recommendation_count": 0,
                "tier_counts": {},
                "top_symbols": [],
            },
        }

    records: list[dict[str, Any]] = []
    parse_errors = 0
    for line in reversed(jsonl_path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        records.append(record)
        if len(records) >= limit:
            break

    tier_counts: dict[str, int] = {}
    symbol_counts: dict[str, dict[str, Any]] = {}
    recommendation_count = 0
    for record in records:
        recommendations = record.get("recommendations") or []
        recommendation_count += len(recommendations)
        for item in recommendations:
            tier = str(item.get("tier") or "unknown")
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            symbol = str(item.get("symbol") or "").strip()
            if not symbol:
                continue
            entry = symbol_counts.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "name": item.get("name"),
                    "tier": item.get("tier"),
                    "count": 0,
                    "latest_date": record.get("date"),
                    "latest_score": item.get("score"),
                    "latest_price": item.get("price"),
                },
            )
            entry["count"] += 1
            if record.get("date") and str(record.get("date")) >= str(entry.get("latest_date") or ""):
                entry.update(
                    {
                        "name": item.get("name"),
                        "tier": item.get("tier"),
                        "latest_date": record.get("date"),
                        "latest_score": item.get("score"),
                        "latest_price": item.get("price"),
                    }
                )

    top_symbols = sorted(
        symbol_counts.values(),
        key=lambda item: (-int(item.get("count") or 0), str(item.get("symbol") or "")),
    )[:20]
    return {
        "path": str(jsonl_path.relative_to(ROOT)),
        "log_count": len(records),
        "parse_errors": parse_errors,
        "summary": {
            "recommendation_count": recommendation_count,
            "tier_counts": tier_counts,
            "top_symbols": top_symbols,
        },
        "records": records,
    }


RECOMMENDATION_PERSISTENCE_RULES = {
    "purpose": "Quantify how consistently a symbol stays in the daily strict shortlist over time, as a conviction/ranking signal. Standalone indicator; it does NOT change research_score, grades, or financial-audit gates.",
    "source": "research_exports/daily_recommendations/recommendations.jsonl (one record per date)",
    "min_days_qualified": 2,
    "score_range": "0-100, higher = more persistently and constructively recommended",
    "components": {
        "coverage": {"weight": 0.35, "desc": "appearances / logged days in the window"},
        "recency_streak": {"weight": 0.25, "desc": "consecutive logged-day streak ending at the latest log (measured over logged days, not calendar days, so gaps do not break it); requires presence in the latest log for full credit"},
        "score_trend": {"weight": 0.15, "desc": "direction of research score across appearances; rising is bullish"},
        "score_level": {"weight": 0.15, "desc": "average research score across appearances"},
        "tier_quality": {"weight": 0.10, "desc": "share of core_watch appearances"},
    },
    "notes": [
        "A symbol on only 1 logged day is marked insufficient_history and is not a qualified persistent candidate (one-day spikes are treated as noise).",
        "The indicator becomes more meaningful as more daily logs accumulate.",
        "Weights are tunable; persist them here so the rule stays transparent and traceable.",
    ],
}


def compute_recommendation_persistence(days: int = 30, min_days: int = 2) -> dict[str, Any]:
    days = max(1, min(days, 100))
    min_days = max(1, min(min_days, days))
    history = read_daily_recommendation_log_history(limit=days)

    # One record per date (latest generation wins), then order ascending by date.
    by_date: dict[str, dict[str, Any]] = {}
    for record in history.get("records", []):
        day = record.get("date")
        if not day:
            continue
        prev = by_date.get(day)
        if not prev or str(record.get("generated_at") or "") > str(prev.get("generated_at") or ""):
            by_date[day] = record
    ordered_dates = sorted(by_date.keys())
    total_days = len(ordered_dates)
    date_index = {day: index for index, day in enumerate(ordered_dates)}

    symbols: dict[str, dict[str, Any]] = {}
    for day in ordered_dates:
        for item in by_date[day].get("recommendations", []) or []:
            symbol = item.get("symbol")
            if not symbol:
                continue
            entry = symbols.setdefault(symbol, {"symbol": symbol, "name": item.get("name"), "appearances": []})
            entry["appearances"].append({"date": day, "score": item.get("score"), "tier": item.get("tier"), "price": item.get("price")})
            entry["name"] = item.get("name") or entry["name"]

    weights = {key: spec["weight"] for key, spec in RECOMMENDATION_PERSISTENCE_RULES["components"].items()}
    results: list[dict[str, Any]] = []
    for entry in symbols.values():
        apps = entry["appearances"]
        count = len(apps)
        present = {date_index[a["date"]] for a in apps}
        scores = [safe_float(a["score"]) for a in apps]
        scores = [s for s in scores if s is not None]

        coverage = (count / total_days * 100) if total_days else 0.0
        streak = 0
        cursor = total_days - 1
        while cursor in present:
            streak += 1
            cursor -= 1
        recency_streak = (streak / total_days * 100) if total_days else 0.0
        score_level = (sum(scores) / len(scores)) if scores else 50.0
        if len(scores) >= 2:
            score_trend = max(0.0, min(100.0, 50.0 + (scores[-1] - scores[0]) * 2.5))
        else:
            score_trend = 50.0
        core_count = sum(1 for a in apps if a["tier"] == "core_watch")
        tier_quality = (core_count / count * 100) if count else 0.0

        persistence_score = round(
            coverage * weights["coverage"]
            + recency_streak * weights["recency_streak"]
            + score_trend * weights["score_trend"]
            + score_level * weights["score_level"]
            + tier_quality * weights["tier_quality"],
            1,
        )
        latest = apps[-1]
        results.append({
            "symbol": entry["symbol"],
            "name": entry["name"],
            "appearances": count,
            "logged_days": total_days,
            "coverage_percent": round(coverage, 1),
            "streak": streak,
            "in_latest_log": (total_days - 1) in present,
            "avg_score": round(score_level, 1),
            "score_trend": round(score_trend, 1),
            "score_first": scores[0] if scores else None,
            "score_last": scores[-1] if scores else None,
            "core_watch_count": core_count,
            "tier_quality_percent": round(tier_quality, 1),
            "persistence_score": persistence_score,
            "qualified": count >= min_days,
            "insufficient_history": count < 2,
            "scores": [a["score"] for a in apps],
            "dates": [a["date"] for a in apps],
            "latest_tier": latest["tier"],
            "latest_date": latest["date"],
            "latest_price": latest.get("price"),
        })

    results.sort(key=lambda r: (0 if r["qualified"] else 1, -r["persistence_score"], -r["appearances"], r["symbol"]))
    return {
        "generated_at": utc_now(),
        "logged_days": total_days,
        "window_dates": ordered_dates,
        "symbol_count": len(results),
        "qualified_count": sum(1 for r in results if r["qualified"]),
        "one_day_only_count": sum(1 for r in results if r["appearances"] == 1),
        "min_days": min_days,
        "rules": RECOMMENDATION_PERSISTENCE_RULES,
        "items": results,
    }


def _attribution_bucket(accum: dict[str, Any]) -> dict[str, Any]:
    ready = int(accum["ready"])
    return {
        "n": accum["n"],
        "ready": ready,
        "pending": accum["pending"],
        "wins": accum["wins"],
        "win_rate": (accum["wins"] / ready) if ready else None,
        "avg_return_percent": (accum["return_sum"] / ready) if ready else None,
    }


def _attr_acc() -> dict[str, Any]:
    return {"n": 0, "ready": 0, "pending": 0, "wins": 0, "return_sum": 0.0}


def _score_bucket(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 72:
        return "≥72"
    if score >= 58:
        return "58-71"
    if score >= 45:
        return "45-57"
    return "<45"


def _rs3m_bucket(pct: float | None) -> str:
    if pct is None:
        return "unknown"
    if pct >= 75:
        return "≥75"
    if pct >= 50:
        return "50-74"
    if pct >= 25:
        return "25-49"
    return "<25"


def _adx_bucket(adx: float | None) -> str:
    if adx is None:
        return "unknown"
    if adx >= 30:
        return "≥30"
    if adx >= 20:
        return "20-29"
    return "<20"


def _update_attr(buckets: dict[str, dict[str, Any]], key: str, result: dict[str, Any]) -> None:
    if key not in buckets:
        buckets[key] = _attr_acc()
    b = buckets[key]
    b["n"] += 1
    if result["status"] == "pending":
        b["pending"] += 1
    elif result["status"] == "ready":
        b["ready"] += 1
        ret = result.get("return_percent")
        if ret is not None:
            b["return_sum"] += ret
            if ret > 0:
                b["wins"] += 1


def get_daily_recommendation_performance(limit: int = 100) -> dict[str, Any]:
    history = read_daily_recommendation_log_history(limit=limit)
    now = datetime.now(timezone.utc)
    horizons = OUTCOME_HORIZONS
    rows: list[dict[str, Any]] = []
    summary: dict[int, dict[str, Any]] = {
        horizon: {"horizon_days": horizon, "ready": 0, "pending": 0, "missing_price": 0, "suspicious": 0, "wins": 0, "return_sum": 0.0}
        for horizon in horizons
    }
    # attribution[horizon][factor][bucket] → accum
    attribution: dict[int, dict[str, dict[str, dict[str, Any]]]] = {
        h: {"score": {}, "rs_3m": {}, "adx": {}, "financial_audit": {}, "tier": {}}
        for h in horizons
    }
    records = list(reversed(history.get("records") or []))
    with db_connect() as conn:
        for record in records:
            start_at = parse_utc_timestamp(record.get("generated_at") or "")
            if start_at is None:
                continue
            for item in record.get("recommendations") or []:
                symbol = item.get("symbol")
                start_price = safe_float(item.get("price"))
                if not symbol or start_price is None or start_price <= 0:
                    continue
                for horizon in horizons:
                    due_at = start_at + timedelta(days=horizon)
                    bucket = summary[horizon]
                    result = {
                        "date": record.get("date"),
                        "symbol": symbol,
                        "name": item.get("name"),
                        "tier": item.get("tier"),
                        "score": item.get("score"),
                        "rs_3m_percentile": item.get("rs_3m_percentile"),
                        "adx_14": item.get("adx_14"),
                        "financial_audit_status": item.get("financial_audit_status"),
                        "horizon_days": horizon,
                        "start_at": start_at.isoformat(),
                        "start_price": start_price,
                        "due_at": due_at.isoformat(),
                        "status": "pending",
                        "outcome_price": None,
                        "outcome_at": None,
                        "return_percent": None,
                    }
                    if due_at > now:
                        bucket["pending"] += 1
                        rows.append(result)
                        _update_attr(attribution[horizon]["score"], _score_bucket(safe_float(item.get("score"))), result)
                        _update_attr(attribution[horizon]["rs_3m"], _rs3m_bucket(safe_float(item.get("rs_3m_percentile"))), result)
                        _update_attr(attribution[horizon]["adx"], _adx_bucket(safe_float(item.get("adx_14"))), result)
                        _update_attr(attribution[horizon]["financial_audit"], str(item.get("financial_audit_status") or "unknown"), result)
                        _update_attr(attribution[horizon]["tier"], str(item.get("tier") or "unknown"), result)
                        continue
                    outcome = conn.execute(
                        """
                        SELECT price, captured_at
                        FROM price_snapshots
                        WHERE symbol = ?
                          AND captured_at >= ?
                          AND price IS NOT NULL
                        ORDER BY captured_at ASC
                        LIMIT 1
                        """,
                        (symbol, due_at.isoformat()),
                    ).fetchone()
                    if outcome is None:
                        result["status"] = "missing_price"
                        bucket["missing_price"] += 1
                        rows.append(result)
                        continue
                    outcome_price = safe_float(outcome["price"])
                    return_percent = (outcome_price / start_price - 1) * 100 if outcome_price is not None else None
                    is_suspicious = return_percent is not None and abs(return_percent) > 500
                    result.update(
                        {
                            "status": "suspicious" if is_suspicious else "ready",
                            "outcome_price": outcome_price,
                            "outcome_at": outcome["captured_at"],
                            "return_percent": return_percent,
                        }
                    )
                    if is_suspicious:
                        bucket["suspicious"] += 1
                    else:
                        bucket["ready"] += 1
                        if return_percent is not None:
                            bucket["return_sum"] += return_percent
                            if return_percent > 0:
                                bucket["wins"] += 1
                    rows.append(result)
                    _update_attr(attribution[horizon]["score"], _score_bucket(safe_float(item.get("score"))), result)
                    _update_attr(attribution[horizon]["rs_3m"], _rs3m_bucket(safe_float(item.get("rs_3m_percentile"))), result)
                    _update_attr(attribution[horizon]["adx"], _adx_bucket(safe_float(item.get("adx_14"))), result)
                    _update_attr(attribution[horizon]["financial_audit"], str(item.get("financial_audit_status") or "unknown"), result)
                    _update_attr(attribution[horizon]["tier"], str(item.get("tier") or "unknown"), result)
    summary_rows = []
    for item in summary.values():
        ready = int(item["ready"])
        summary_rows.append(
            {
                "horizon_days": item["horizon_days"],
                "ready": ready,
                "pending": item["pending"],
                "missing_price": item["missing_price"],
                "suspicious": item.get("suspicious", 0),
                "wins": item["wins"],
                "win_rate": (item["wins"] / ready) if ready else None,
                "avg_return_percent": (item["return_sum"] / ready) if ready else None,
            }
        )
    attribution_out: dict[int, dict[str, list[dict[str, Any]]]] = {}
    for h, factors in attribution.items():
        attribution_out[h] = {}
        for factor, buckets in factors.items():
            attribution_out[h][factor] = [
                {"bucket": k, **_attribution_bucket(v)}
                for k, v in sorted(buckets.items())
            ]
    return {
        "generated_at": utc_now(),
        "source": history.get("path"),
        "log_count": history.get("log_count"),
        "summary": summary_rows,
        "attribution": attribution_out,
        "rows": rows[-500:],
        "note": "Daily log performance uses logged recommendation price as baseline and the first available snapshot after each calendar-day horizon.",
    }


def run_due_daily_recommendation_log(now: datetime | None = None) -> dict[str, Any]:
    local_now = (now or datetime.now(TAIPEI_TZ)).astimezone(TAIPEI_TZ)
    if local_now.strftime("%H:%M") < DAILY_RECOMMENDATION_LOG_TIME:
        return {
            "ran": False,
            "reason": "not_due",
            "scheduled_time": DAILY_RECOMMENDATION_LOG_TIME,
            "date": local_now.strftime("%Y-%m-%d"),
        }
    return write_daily_recommendation_log(force=False)


def build_opportunity(item: dict[str, Any]) -> dict[str, Any]:
    from app import movement_threshold, get_symbol_trend_context  # circular dep break
    rec = item["recommendation"]
    monitoring = item["monitoring"]
    metrics = monitoring["metrics"]
    alerts = monitoring.get("alerts", [])
    factors = rec.get("factors") or {}
    rec_metrics = rec.get("metrics") or {}
    quant_metrics = {
        key: rec_metrics.get(key)
        for key in [
            "adx_14",
            "plus_di",
            "minus_di",
            "rs_1m_percentile",
            "rs_3m_percentile",
            "rs_peer_count",
            "volatility_regime_percentile",
        ]
        if key in rec_metrics
    }
    factor_summary = {
        key: {
            "label": value.get("label", FACTOR_LABELS.get(key, key)),
            "score": value.get("score"),
            "weight": value.get("weight", FACTOR_WEIGHTS.get(key)),
            "coverage": value.get("coverage"),
            "confidence": value.get("confidence"),
            "missing_fields": value.get("missing_fields", []),
        }
        for key, value in factors.items()
    }
    priority = 0
    reasons = []
    category = "watch"

    for alert in alerts:
        if alert.get("severity") == "high":
            priority += 35
        elif alert.get("severity") == "medium":
            priority += 20
        else:
            priority += 8
        reasons.append(alert.get("title", "事件警示"))

    score = int(rec.get("score") or 0)

    # Multi-signal confirmation: measure alignment of trend/momentum/sentiment
    _key_dims = ["trend", "momentum", "sentiment"]
    _bullish_count = sum(
        1 for k in _key_dims
        if safe_float((factor_summary.get(k) or {}).get("score")) is not None
        and safe_float((factor_summary.get(k) or {}).get("score")) >= 60  # type: ignore[arg-type]
    )
    _bearish_count = sum(
        1 for k in _key_dims
        if safe_float((factor_summary.get(k) or {}).get("score")) is not None
        and safe_float((factor_summary.get(k) or {}).get("score")) < 40  # type: ignore[arg-type]
    )
    bullish_confirmation_count = _bullish_count
    if score >= 58 and _bearish_count >= 2:
        score = max(score - 8, 43)
        reasons.append(f"趨勢/動能/情緒信號衝突（{_bearish_count} 個偏弱），評分下調。")
    elif score >= 58 and _bearish_count >= 1 and _bullish_count == 0:
        score = max(score - 3, 43)
        reasons.append("主要信號一致性不足，評分略降。")

    if score >= 72:
        priority += 30
        category = "technical_strength"
        reasons.append("投研分數達偏多候選區。")
    elif score >= 58:
        priority += 15
        category = "constructive_watch"
        reasons.append("投研分數略偏正向，適合追蹤。")
    elif score <= 42:
        priority += 12
        category = "risk_watch"
        reasons.append("分數偏弱，列入風險觀察。")

    change_1d = safe_float(item.get("change_percent"))
    change_5d = safe_float(metrics.get("change_5d"))
    daily_vol = safe_float(metrics.get("daily_volatility"))
    threshold = safe_float(metrics.get("movement_threshold")) or movement_threshold(item.get("asset_type") or "")
    trend_context = get_symbol_trend_context(item["symbol"], 10)
    if change_1d is not None and abs(change_1d) >= threshold:
        priority += 25
        category = "price_event"
        reasons.append(f"單日變化 {change_1d:+.2f}% 超過門檻 {threshold:.2f}%。")
    if change_5d is not None and abs(change_5d) >= threshold * 2:
        priority += 20
        category = "trend_event"
        reasons.append(f"5 日變化 {change_5d:+.2f}% 明顯。")
    if daily_vol is not None and daily_vol >= threshold:
        priority += 8
        reasons.append(f"近期日波動 {daily_vol:.2f}% 接近或超過門檻。")
    if trend_context["snapshot_count"] >= 3:
        score_change = safe_float(trend_context.get("score_change"))
        price_change = safe_float(trend_context.get("price_change"))
        if score_change is not None and score_change >= 8:
            priority += 12
            category = "improving_trend"
            reasons.append(f"歷史投研分數改善 {score_change:+.0f} 分。")
        elif score_change is not None and score_change <= -8:
            priority += 12
            category = "deteriorating_trend"
            reasons.append(f"歷史投研分數惡化 {score_change:+.0f} 分。")
        if price_change is not None and abs(price_change) >= 5:
            priority += 6
            reasons.append(f"本機歷史快照價格變化 {price_change:+.2f}%。")

    if not reasons:
        reasons.append("未觸發重大異常，保留為一般追蹤。")

    priority = max(0, min(100, priority))
    thesis = " ".join(reasons[:4])
    grade_payload = opportunity_grade(
        score,
        priority,
        category,
        rec.get("data_quality", {}).get("factors", {}),
        item.get("asset_type") or "",
        item.get("symbol") or "",
        item.get("quote_type"),
        rec.get("financial_audit_status"),
    )
    return {
        "symbol": item["symbol"],
        "name": item.get("name"),
        "asset_type": item.get("asset_type"),
        "quote_type": item.get("quote_type"),
        "priority": priority,
        "category": category,
        "grade": grade_payload["grade"],
        "raw_grade": grade_payload["raw_grade"],
        "action_bucket": grade_payload["action_bucket"],
        "data_gate": grade_payload["data_gate"],
        "data_policy": rec.get("data_policy"),
        "thesis": thesis,
        "price": item.get("price"),
        "change_1d": change_1d,
        "change_5d": change_5d,
        "score": score,
        "action": rec.get("action"),
        "factors": factor_summary,
        "quant_metrics": quant_metrics,
        "effective_factor_weights": rec.get("effective_factor_weights"),
        "alerts": alerts,
        "bullish_confirmation_count": bullish_confirmation_count,
        "snapshot_id": item.get("snapshot_id"),
        "history": trend_context,
    }


def save_scan_run(
    universe_count: int,
    scanned_count: int,
    opportunities: list[dict[str, Any]],
    threshold_override: float | None,
    payload: dict[str, Any],
) -> int:
    created_at = utc_now()
    with db_connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO scan_runs (
                created_at, universe_count, scanned_count, opportunity_count, threshold, results_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                universe_count,
                scanned_count,
                len(opportunities),
                threshold_override,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        scan_run_id = int(cursor.lastrowid)
        for opportunity in opportunities:
            opportunity_cursor = conn.execute(
                """
                INSERT INTO opportunities (
                    scan_run_id, snapshot_id, symbol, name, asset_type, priority, category,
                    thesis, price, change_1d, change_5d, score, action, created_at, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_run_id,
                    opportunity.get("snapshot_id"),
                    opportunity["symbol"],
                    opportunity.get("name"),
                    opportunity.get("asset_type"),
                    opportunity["priority"],
                    opportunity["category"],
                    opportunity["thesis"],
                    opportunity.get("price"),
                    opportunity.get("change_1d"),
                    opportunity.get("change_5d"),
                    opportunity.get("score"),
                    opportunity.get("action"),
                    created_at,
                    json.dumps(opportunity, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            add_recommendation_outcomes(conn, int(opportunity_cursor.lastrowid), scan_run_id, opportunity, created_at)
    return scan_run_id


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def export_scan_markdown(payload: dict[str, Any]) -> dict[str, str]:
    ensure_export_dirs()
    created_at = utc_now()
    slug = f"scan_{payload.get('scan_run_id', 'pending')}_{timestamp_slug(created_at)}"
    md_path = EXPORT_DIR / "scans" / f"{slug}.md"
    latest_path = EXPORT_DIR / "scans" / "latest_scan.md"
    jsonl_path = EXPORT_DIR / "jsonl" / "opportunities.jsonl"
    lines = [
        f"# Market Scan #{payload.get('scan_run_id')}",
        "",
        f"- generated_at: `{created_at}`",
        f"- universe_count: `{payload.get('universe_count')}`",
        f"- scanned_count: `{payload.get('scanned_count')}`",
        f"- cache_hits: `{payload.get('cache_hits')}`",
        f"- refreshed_count: `{payload.get('refreshed_count')}`",
        f"- refresh_minutes: `{payload.get('refresh_minutes')}`",
        f"- opportunity_count: `{len(payload.get('opportunities', []))}`",
        f"- elapsed_seconds: `{payload.get('elapsed_seconds')}`",
        "",
        "## Opportunities",
        "",
    ]
    if not payload.get("opportunities"):
        lines.append("No opportunities passed the priority threshold.")
    for index, item in enumerate(payload.get("opportunities", []), 1):
        history = item.get("history") or {}
        lines.extend(
            [
                f"### {index}. {item.get('symbol')} - {item.get('name') or ''}",
                "",
                f"- asset_type: `{item.get('asset_type')}`",
                f"- category: `{item.get('category')}`",
                f"- radar_priority: `{item.get('priority')}`",
                f"- research_score: `{item.get('score')}`",
                f"- action: `{item.get('action')}`",
                f"- price: `{item.get('price')}`",
                f"- change_1d: `{item.get('change_1d')}`",
                f"- change_5d: `{item.get('change_5d')}`",
                f"- snapshot_id: `{item.get('snapshot_id')}`",
                f"- history_snapshot_count: `{history.get('snapshot_count')}`",
                f"- history_score_change: `{history.get('score_change')}`",
                f"- history_price_change: `{history.get('price_change')}`",
                "",
                f"Thesis: {item.get('thesis')}",
                "",
            ]
        )
        if item.get("alerts"):
            lines.append("Alerts:")
            for alert in item["alerts"]:
                lines.append(f"- `{alert.get('severity')}` {alert.get('title')} - {alert.get('detail')}")
            lines.append("")
    if payload.get("errors"):
        lines.extend(["## Errors", ""])
        for error in payload["errors"]:
            lines.append(f"- {error.get('symbol')}: {error.get('message')}")
    content = "\n".join(lines).rstrip() + "\n"
    write_text_file(md_path, content)
    write_text_file(latest_path, content)
    records = []
    for item in payload.get("opportunities", []):
        record = dict(item)
        record["scan_run_id"] = payload.get("scan_run_id")
        record["exported_at"] = created_at
        records.append(record)
    if records:
        append_jsonl(jsonl_path, records)
    return {"markdown": str(md_path), "latest_markdown": str(latest_path), "jsonl": str(jsonl_path)}


def scan_universe_rows(
    universe: list[dict[str, Any]],
    threshold_override: float | None,
    min_priority: int,
    refresh_minutes: int,
) -> dict[str, Any]:
    from app import get_recent_analysis_result, analyze_symbol, save_analysis_result  # circular dep break
    opportunities = []
    errors = []
    scanned = 0
    cache_hits = 0
    refreshed = 0
    for row in universe:
        symbol = row["symbol"]
        try:
            item = get_recent_analysis_result(symbol, refresh_minutes)
            if item is not None:
                cache_hits += 1
            else:
                item = analyze_symbol(symbol, threshold_override)
                snapshot_id = save_analysis_result(item)
                item["snapshot_id"] = snapshot_id
                item["cache"] = {"hit": False, "max_age_minutes": refresh_minutes}
                refreshed += 1
            opportunity = build_opportunity(item)
            if opportunity["priority"] >= min_priority:
                opportunities.append(opportunity)
            scanned += 1
        except Exception as exc:
            errors.append({"symbol": symbol, "message": str(exc)})
    return {
        "opportunities": opportunities,
        "errors": errors,
        "scanned_count": scanned,
        "cache_hits": cache_hits,
        "refreshed_count": refreshed,
    }


def scan_market(
    limit: int = 25,
    threshold_override: float | None = None,
    min_priority: int = 25,
    asset_type: str | None = None,
    refresh_minutes: int = 60,
    offset: int = 0,
    scope: str | None = None,
    universe_override: list[dict[str, Any]] | None = None,
    available_universe_count_override: int | None = None,
) -> dict[str, Any]:
    from app import get_universe_count, get_scoped_universe_count, get_universe, get_scoped_universe  # circular dep break
    started = time.time()
    if universe_override is None:
        if scope:
            available_universe_count = get_scoped_universe_count(scope, asset_type)
        else:
            available_universe_count = get_universe_count(asset_type)
        offset = max(0, offset)
        if available_universe_count:
            offset = min(offset, max(available_universe_count - 1, 0))
        if scope:
            universe = get_scoped_universe(scope, asset_type, limit, offset)
        else:
            universe = get_universe(asset_type, limit, offset)
    else:
        universe = universe_override
        available_universe_count = available_universe_count_override or len(universe)
    scan_result = scan_universe_rows(universe, threshold_override, min_priority, refresh_minutes)
    opportunities = scan_result["opportunities"]
    errors = scan_result["errors"]
    scanned = scan_result["scanned_count"]
    cache_hits = scan_result["cache_hits"]
    refreshed = scan_result["refreshed_count"]
    opportunities.sort(key=lambda row: (row["priority"], abs(row.get("change_1d") or 0)), reverse=True)
    payload = {
        "universe_count": len(universe),
        "available_universe_count": available_universe_count,
        "offset": offset,
        "scanned_count": scanned,
        "cache_hits": cache_hits,
        "refreshed_count": refreshed,
        "refresh_minutes": refresh_minutes,
        "scope": scope,
        "opportunities": opportunities,
        "errors": errors,
        "elapsed_seconds": round(time.time() - started, 2),
        "disclaimer": "市場掃描僅供產生研究候選清單，不構成投資建議或自動交易指令。",
    }
    payload["scan_run_id"] = save_scan_run(len(universe), scanned, opportunities, threshold_override, payload)
    payload["exports"] = export_scan_markdown(payload)
    return payload


def save_backtest_run(symbols: list[str], days: int, cost_bps: float, payload: dict[str, Any]) -> int:
    created_at = utc_now()
    with db_connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO backtest_runs (created_at, symbols, days, strategy, cost_bps, results_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                ",".join(symbols),
                days,
                "technical_score_position_v1",
                cost_bps,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ),
        )
    return int(cursor.lastrowid)


def run_backtests(symbols: list[str], days: int = 180, cost_bps: float = 5.0) -> dict[str, Any]:
    from app import run_symbol_backtest  # circular dep break
    started = time.time()
    results = []
    errors = []
    for symbol in symbols[:8]:
        try:
            results.append(run_symbol_backtest(symbol, days, cost_bps))
        except Exception as exc:
            errors.append({"symbol": symbol, "message": str(exc)})
    summary = {
        "results": results,
        "errors": errors,
        "elapsed_seconds": round(time.time() - started, 2),
        "disclaimer": "回測僅供研究。歷史績效不代表未來績效，且未完整計入滑價、稅費、流動性與新聞事件。",
    }
    if results:
        summary["run_id"] = save_backtest_run(symbols[:8], days, cost_bps, summary)
    return summary


def pct_text(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "資料不足"
    return f"{number:+.2f}%"


def report_section(agent: str, title: str, verdict: str, bullets: list[str], data_gaps: list[str]) -> dict[str, Any]:
    return {
        "agent": agent,
        "title": title,
        "verdict": verdict,
        "bullets": bullets,
        "data_gaps": data_gaps,
    }


def build_data_retrieval_report(item: dict[str, Any], history: dict[str, Any]) -> dict[str, Any]:
    latest = history["snapshots"][0] if history["snapshots"] else None
    gaps = []
    for key, label in [("pe", "本益比"), ("eps", "EPS"), ("avg_volume", "均量")]:
        if item.get(key) is None:
            gaps.append(label)
    return report_section(
        "Data Retrieval Agent",
        "資料擷取與完整性",
        "可建立基礎行情快照，但基本面資料仍不足。" if gaps else "資料完整度足以支撐第一層分析。",
        [
            f"最新價格：{item.get('price')} {item.get('currency') or ''}".strip(),
            f"資產類別：{item.get('asset_type')}，產業：{' / '.join(part for part in [item.get('sector'), item.get('industry')] if part) or '未取得'}。",
            f"已保存歷史快照：{len(history['snapshots'])} 筆；最近 snapshot id：{latest['id'] if latest else '尚無'}。",
            f"新聞筆數：{len(item.get('news', []))}；波動警示：{len(item.get('monitoring', {}).get('alerts', []))} 則。",
        ],
        gaps,
    )


def build_technical_agent_report(item: dict[str, Any]) -> dict[str, Any]:
    rec = item["recommendation"]
    metrics = rec["metrics"]
    monitoring = item["monitoring"]["metrics"]
    technical_signals = [signal for signal in rec["signals"] if signal.get("category") in {"技術面", "風險"}]
    verdict = "技術面偏多" if rec["score"] >= 58 else "技術面中性或偏弱"
    return report_section(
        "Technical Analyst Agent",
        "技術面與波動結構",
        verdict,
        [
            f"投研分數：{rec['score']}，系統動作：{rec['action']}。",
            f"1 個月變化：{pct_text(metrics.get('change_1m'))}；3 個月變化：{pct_text(metrics.get('change_3m'))}；5 日變化：{pct_text(monitoring.get('change_5d'))}。",
            f"20 日均線：{metrics.get('ma_20') or '資料不足'}；60 日均線：{metrics.get('ma_60') or '資料不足'}。",
            f"年化波動：{pct_text(metrics.get('volatility'))}；日波動：{pct_text(monitoring.get('daily_volatility'))}；目前波動門檻：{pct_text(monitoring.get('movement_threshold'))}。",
            *[f"{signal['label']}：{signal['detail']}" for signal in technical_signals[:3]],
        ],
        ["RSI", "MACD", "布林通道", "盤中量價"] if not item.get("chart") else ["RSI", "MACD", "布林通道"],
    )


def build_macro_news_report(item: dict[str, Any]) -> dict[str, Any]:
    news = item.get("news", [])
    positive = sum(1 for row in news if row.get("sentiment") == "positive")
    negative = sum(1 for row in news if row.get("sentiment") == "negative")
    catalyst = item.get("monitoring", {}).get("catalyst_news", [])
    verdict = "新聞面偏負或需警戒" if negative > positive else "新聞面未見明確利空" if news else "新聞資料不足"
    bullets = [
        f"新聞語氣：正向 {positive} 則、負向 {negative} 則、中性 {max(0, len(news) - positive - negative)} 則。",
        f"波動佐證新聞候選：{len(catalyst)} 則。",
    ]
    bullets.extend([f"{row.get('source')}: {row.get('title')}" for row in news[:4]])
    return report_section(
        "Macro & News Analyst Agent",
        "總經、產業與新聞事件",
        verdict,
        bullets,
        ["Reuters/SEC/公開資訊觀測站來源", "新聞發布時間與價格分鐘級比對", "Fed/利率/美元指數資料"],
    )


def build_flow_agent_report(item: dict[str, Any]) -> dict[str, Any]:
    asset_type = item.get("asset_type")
    if asset_type == "台股":
        gaps = ["三大法人買賣超", "融資融券", "外資持股變化"]
        verdict = "籌碼資料尚未接入，不能判定法人方向。"
    elif asset_type == "虛擬貨幣":
        gaps = ["交易所資金費率", "未平倉量", "鏈上大額轉帳", "交易所淨流入"]
        verdict = "鏈上與衍生品資料尚未接入，不能判定槓桿風險。"
    elif asset_type == "原物料":
        gaps = ["期貨持倉 COT", "庫存資料", "美元指數", "期限結構"]
        verdict = "供需與期貨結構資料尚未接入，僅能做價格面觀察。"
    else:
        gaps = ["ETF flow", "選擇權 put/call", "機構持股變化"]
        verdict = "資金流資料尚未接入，不能判定機構部位。"
    return report_section(
        "Flow / On-chain Agent",
        "籌碼、資金流與鏈上資料",
        verdict,
        [
            f"目前資產類別：{asset_type}。",
            f"成交量：{item.get('volume') or '資料不足'}；均量：{item.get('avg_volume') or '資料不足'}。",
            "此 Agent 目前只回報資料缺口，待接入資料源後再產生方向性判斷。",
        ],
        gaps,
    )


def build_risk_report(item: dict[str, Any]) -> dict[str, Any]:
    rec = item["recommendation"]
    monitoring = item["monitoring"]
    severity = [alert.get("severity") for alert in monitoring.get("alerts", [])]
    risks = list(rec.get("risks", []))
    if "high" in severity:
        risks.append("存在高優先波動警示，需先確認事件原因再加碼。")
    verdict = "風險可控但需小部位驗證" if rec["score"] >= 58 and "high" not in severity else "需優先風控，不宜直接重倉"
    return report_section(
        "Risk Officer Agent",
        "風控與限制條件",
        verdict,
        [
            f"目前建議：{rec['action']}。",
            f"風險提示：{' '.join(risks) if risks else '目前未產生重大風險提示。'}",
            f"警示數：{len(monitoring.get('alerts', []))}。",
            "沒有使用者持倉、現金比例與最大回撤設定，因此尚不能給出完整部位上限。",
        ],
        ["使用者持倉", "現金比例", "單一標的上限", "最大可承受回撤", "投資期限"],
    )


def build_chief_strategist_report(item: dict[str, Any], agent_reports: list[dict[str, Any]]) -> dict[str, Any]:
    rec = item["recommendation"]
    alerts = item.get("monitoring", {}).get("alerts", [])
    news_count = len(item.get("news", []))
    if rec["score"] >= 72 and not alerts:
        verdict = "可列入候選清單，等待 PM 核准分批布局。"
        action = "建議候選權重 3% 至 5%，需設定停損與資料覆核點。"
    elif rec["score"] >= 58:
        verdict = "資料略偏正向，但仍需等待事件與風險確認。"
        action = "建議先觀察或小部位 1% 至 3%，等新聞與波動確認後再提高權重。"
    else:
        verdict = "目前不適合主動加碼。"
        action = "建議維持觀望，等待技術面或新聞面重新轉強。"
    return report_section(
        "Chief Strategist Agent",
        "交叉驗證與 PM 提案",
        verdict,
        [
            f"技術/資料分數為 {rec['score']}，系統原始動作為「{rec['action']}」。",
            f"新聞樣本 {news_count} 則，事件警示 {len(alerts)} 則。",
            f"交叉驗證結論：{action}",
            "PM 決策前必查：新聞來源原文、公司公告/財報、產業事件時間線、個人部位與風險上限。",
        ],
        sorted({gap for report in agent_reports for gap in report.get("data_gaps", [])})[:10],
    )


def save_agent_reports(symbol: str, reports: list[dict[str, Any]], snapshot_id: int | None) -> None:
    generated_at = utc_now()
    with db_connect() as conn:
        for report in reports:
            content = "\n".join([report["verdict"], *[f"- {line}" for line in report["bullets"]]])
            conn.execute(
                """
                INSERT INTO agent_reports (
                    symbol, report_type, generated_at, title, content, source_snapshot_ids, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    report["agent"],
                    generated_at,
                    report["title"],
                    content,
                    json.dumps([snapshot_id] if snapshot_id else [], separators=(",", ":")),
                    json.dumps(report, ensure_ascii=False, separators=(",", ":")),
                ),
            )


def export_research_markdown(result: dict[str, Any]) -> dict[str, str]:
    ensure_export_dirs()
    created_at = utc_now()
    symbol = result["symbol"]
    slug = f"research_{symbol}_{timestamp_slug(created_at)}"
    md_path = EXPORT_DIR / "reports" / f"{slug}.md"
    latest_path = EXPORT_DIR / "reports" / f"latest_{symbol}.md"
    jsonl_path = EXPORT_DIR / "jsonl" / "agent_reports.jsonl"
    lines = [
        f"# Agent Research - {symbol}",
        "",
        f"- generated_at: `{created_at}`",
        f"- name: `{result.get('name')}`",
        f"- snapshot_id: `{result.get('snapshot_id')}`",
        f"- price: `{result.get('price')}`",
        f"- research_score: `{result.get('score')}`",
        f"- action: `{result.get('action')}`",
        "",
    ]
    for report in result.get("reports", []):
        lines.extend(
            [
                f"## {report.get('agent')}: {report.get('title')}",
                "",
                f"Verdict: {report.get('verdict')}",
                "",
                "Evidence:",
            ]
        )
        for bullet in report.get("bullets", []):
            lines.append(f"- {bullet}")
        if report.get("data_gaps"):
            lines.extend(["", "Data gaps:"])
            for gap in report["data_gaps"]:
                lines.append(f"- {gap}")
        lines.append("")
    content = "\n".join(lines).rstrip() + "\n"
    write_text_file(md_path, content)
    write_text_file(latest_path, content)
    records = []
    for report in result.get("reports", []):
        record = dict(report)
        record.update(
            {
                "symbol": symbol,
                "snapshot_id": result.get("snapshot_id"),
                "research_score": result.get("score"),
                "action": result.get("action"),
                "exported_at": created_at,
            }
        )
        records.append(record)
    if records:
        append_jsonl(jsonl_path, records)
    return {"markdown": str(md_path), "latest_markdown": str(latest_path), "jsonl": str(jsonl_path)}


def refresh_pending_outcome_symbols(limit: int = 30) -> dict[str, Any]:
    """Refresh price snapshots for symbols with pending outcomes that lack a recent snapshot."""
    from app import analyze_symbol, save_analysis_result  # circular dep break
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat()
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT ro.symbol
            FROM recommendation_outcomes ro
            WHERE ro.status = 'pending'
              AND NOT EXISTS (
                  SELECT 1 FROM price_snapshots ps
                  WHERE ps.symbol = ro.symbol
                    AND ps.captured_at >= ?
              )
            ORDER BY ro.due_at ASC
            LIMIT ?
            """,
            (cutoff, max(1, min(limit, 100))),
        ).fetchall()
    symbols = [row["symbol"] for row in rows]
    refreshed = 0
    errors: list[str] = []
    for symbol in symbols:
        try:
            item = analyze_symbol(symbol)
            save_analysis_result(item)
            refreshed += 1
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
    return {
        "checked": len(symbols),
        "refreshed": refreshed,
        "errors": len(errors),
        "error_details": errors[:5],
        "generated_at": utc_now(),
    }


_REPORT_AGENT_KEY_MAP = {
    "Data Retrieval Agent": "data_retrieval",
    "Technical Analyst Agent": "technical",
    "Macro & News Analyst Agent": "macro_news",
    "Flow / On-chain Agent": "flow",
    "Risk Officer Agent": "risk",
    "Chief Strategist Agent": "chief_strategist",
}


def _inject_llm_insights(reports: list[dict[str, Any]], insights: dict[str, str]) -> None:
    if not insights:
        return
    for report in reports:
        key = _REPORT_AGENT_KEY_MAP.get(report.get("agent", ""), "")
        if key and insights.get(key):
            report["llm_insight"] = insights[key]


def run_research_team(symbols: list[str], threshold_override: float | None = None) -> dict[str, Any]:
    from app import analyze_symbol, save_analysis_result, get_history  # circular dep break
    from llm_client import llm_enabled, generate_research_insights
    started = time.time()
    results = []
    errors = []
    use_llm = llm_enabled()
    for symbol in symbols[:8]:
        try:
            item = analyze_symbol(symbol, threshold_override)
            snapshot_id = save_analysis_result(item)
            item["snapshot_id"] = snapshot_id
            history = get_history(item["symbol"], 10)
            reports = [
                build_data_retrieval_report(item, history),
                build_technical_agent_report(item),
                build_macro_news_report(item),
                build_flow_agent_report(item),
                build_risk_report(item),
            ]
            reports.append(build_chief_strategist_report(item, reports))
            if use_llm:
                try:
                    insights = generate_research_insights(item, reports)
                    _inject_llm_insights(reports, insights)
                except Exception as llm_exc:
                    for report in reports:
                        report["llm_error"] = str(llm_exc)
            save_agent_reports(item["symbol"], reports, snapshot_id)
            result = {
                "symbol": item["symbol"],
                "name": item["name"],
                "snapshot_id": snapshot_id,
                "price": item["price"],
                "score": item["recommendation"]["score"],
                "action": item["recommendation"]["action"],
                "reports": reports,
                "llm_used": use_llm,
            }
            result["exports"] = export_research_markdown(result)
            results.append(result)
        except Exception as exc:
            errors.append({"symbol": symbol, "message": str(exc)})
    return {
        "results": results,
        "errors": errors,
        "elapsed_seconds": round(time.time() - started, 2),
        "llm_used": use_llm,
        "disclaimer": "Agent 報告僅供研究與覆核，不構成投資建議或自動交易指令。",
    }
