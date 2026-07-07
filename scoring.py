from __future__ import annotations

from typing import Any

from common import (
    FACTOR_LABELS,
    FACTOR_REQUIRED_FIELDS,
    FACTOR_WEIGHTS,
    is_fund_like_symbol,
)
from data_health import yyyymm_age_months, quarter_age_quarters
from models import AegisFundamentals, Quote


def add_score(
    score: int,
    signals: list[dict[str, Any]],
    category: str,
    label: str,
    impact: int,
    detail: str,
) -> int:
    signals.append({"category": category, "label": label, "impact": impact, "detail": detail})
    return score + impact


def empty_factor_state() -> dict[str, dict[str, Any]]:
    return {
        key: {"key": key, "label": FACTOR_LABELS[key], "score": 50, "signals": []}
        for key in FACTOR_WEIGHTS
    }


def add_factor_score(
    factors: dict[str, dict[str, Any]],
    signals: list[dict[str, Any]],
    factor: str,
    category: str,
    label: str,
    impact: int,
    detail: str,
) -> None:
    entry = factors[factor]
    entry["score"] = max(0, min(100, int(entry["score"]) + impact))
    signal = {
        "factor": factor,
        "factor_label": FACTOR_LABELS[factor],
        "category": category,
        "label": label,
        "impact": impact,
        "detail": detail,
    }
    entry["signals"].append(signal)
    signals.append(signal)


def set_factor_score(
    factors: dict[str, dict[str, Any]],
    signals: list[dict[str, Any]],
    factor: str,
    score: float,
    category: str,
    label: str,
    detail: str,
) -> None:
    normalized_score = max(0, min(100, int(round(score))))
    impact = normalized_score - 50
    entry = factors[factor]
    entry["score"] = normalized_score
    signal = {
        "factor": factor,
        "factor_label": FACTOR_LABELS[factor],
        "category": category,
        "label": label,
        "impact": impact,
        "detail": detail,
    }
    entry["signals"].append(signal)
    signals.append(signal)


def applicable_factor_weights(data_quality: dict[str, dict[str, Any]]) -> dict[str, float]:
    return {
        key: weight
        for key, weight in FACTOR_WEIGHTS.items()
        if data_quality.get(key, {}).get("confidence") != "not_applicable"
    }


def weighted_factor_score(
    factors: dict[str, dict[str, Any]],
    data_quality: dict[str, dict[str, Any]] | None = None,
) -> int:
    weights = applicable_factor_weights(data_quality or {}) if data_quality else FACTOR_WEIGHTS
    weighted_sum = 0.0
    weight_total = 0.0
    for key, weight in weights.items():
        weighted_sum += float(factors[key]["score"]) * weight
        weight_total += weight
    if weight_total <= 0:
        return 50
    return max(0, min(100, int(round(weighted_sum / weight_total))))


def confidence_from_coverage(coverage: float, source_status: str) -> str:
    if source_status == "not_applicable":
        return "not_applicable"
    if source_status in {"source_missing", "source_failed", "stale", "unverified"}:
        return "none"
    if coverage >= 0.75:
        return "high"
    if coverage >= 0.4:
        return "medium"
    if coverage > 0:
        return "low"
    return "none"


def data_quality_entry(
    factor: str,
    used_fields: dict[str, Any],
    source_status: str = "available",
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    required = FACTOR_REQUIRED_FIELDS[factor]
    if source_status == "not_applicable":
        return {
            "factor": factor,
            "factor_label": FACTOR_LABELS[factor],
            "coverage": 0,
            "confidence": "not_applicable",
            "source_status": source_status,
            "required_fields": required,
            "used_fields": {},
            "missing_fields": [],
            "evidence": evidence or {},
        }
    available_fields = [
        key for key in required
        if used_fields.get(key) is not None and used_fields.get(key) != ""
    ]
    missing_fields = [key for key in required if key not in available_fields]
    coverage = round(len(available_fields) / len(required), 4) if required else 1.0
    if source_status == "available" and missing_fields:
        source_status = "partial"
    return {
        "factor": factor,
        "factor_label": FACTOR_LABELS[factor],
        "coverage": coverage,
        "confidence": confidence_from_coverage(coverage, source_status),
        "source_status": source_status,
        "required_fields": required,
        "used_fields": {key: used_fields.get(key) for key in available_fields},
        "missing_fields": missing_fields,
        "evidence": evidence or {},
    }


def factor_payload(
    factors: dict[str, dict[str, Any]],
    data_quality: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        key: {
            "label": value["label"],
            "score": int(value["score"]),
            "weight": FACTOR_WEIGHTS[key],
            "signals": value["signals"],
            "coverage": data_quality.get(key, {}).get("coverage", 0),
            "confidence": data_quality.get(key, {}).get("confidence", "none"),
            "source_status": data_quality.get(key, {}).get("source_status", "unknown"),
            "used_fields": data_quality.get(key, {}).get("used_fields", {}),
            "missing_fields": data_quality.get(key, {}).get("missing_fields", []),
        }
        for key, value in factors.items()
    }


GRADE_RANK = {"Risk Watch": 0, "Watch": 1, "C": 2, "B": 3, "A": 4}
GRADE_BY_RANK = {value: key for key, value in GRADE_RANK.items()}


def cap_grade(grade: str, cap: str) -> str:
    return GRADE_BY_RANK[min(GRADE_RANK.get(grade, 1), GRADE_RANK.get(cap, 1))]


def preliminary_grade(
    score: int,
    data_quality: dict[str, dict[str, Any]],
    asset_type: str,
    symbol: str = "",
    quote_type: str | None = None,
    financial_status: str | None = None,
) -> dict[str, Any]:
    if score >= 72:
        grade = "A"
    elif score >= 62:
        grade = "B"
    elif score >= 52:
        grade = "C"
    elif score <= 42:
        grade = "Risk Watch"
    else:
        grade = "Watch"

    coverage_values = [
        row.get("coverage", 0)
        for row in data_quality.values()
        if row.get("confidence") != "not_applicable"
    ]
    total_coverage = sum(coverage_values) / len(coverage_values) if coverage_values else 0
    cap = "A"
    reasons = []
    for key in ["trend", "momentum", "risk"]:
        row = data_quality.get(key, {})
        if row.get("coverage", 0) < 0.4:
            cap = cap_grade(cap, "Risk Watch")
            reasons.append(f"{FACTOR_LABELS.get(key, key)}資料不足，無法建立核心行情判斷。")
    if total_coverage < 0.4:
        cap = cap_grade(cap, "C")
        reasons.append("整體資料覆蓋率低於 40%，限制為 C 級以下。")
    if asset_type in {"美股/ETF", "台股"} and not is_fund_like_symbol(symbol, quote_type):
        value_quality = [
            data_quality.get("value", {}).get("coverage", 0),
            data_quality.get("quality", {}).get("coverage", 0),
        ]
        if max(value_quality) < 0.35:
            cap = cap_grade(cap, "C")
            reasons.append("股權類標的估值/品質資料仍不足，限制為 C 級以下。")
        elif sum(value_quality) / len(value_quality) < 0.55:
            cap = cap_grade(cap, "B")
            reasons.append("股權類基本面資料部分缺口，暫不給 A 級。")
    if financial_status == "warn":
        cap = cap_grade(cap, "C")
        reasons.append("財務資料稽核為 warn，基本面支撐不足，限制為 C 級以下。")
    elif financial_status == "block":
        cap = cap_grade(cap, "Watch")
        reasons.append("財務資料稽核為 block，禁止列為可買候選。")
    final_grade = cap_grade(grade, cap)
    return {
        "grade": final_grade,
        "raw_grade": grade,
        "cap": cap,
        "reasons": reasons,
        "total_coverage": round(total_coverage, 4),
    }


def financial_status_from_quality(
    asset_type: str,
    symbol: str,
    quote_type: str | None,
    data_quality: dict[str, dict[str, Any]],
    fundamentals: AegisFundamentals | None = None,
) -> str:
    needs_financials = asset_type in {"美股/ETF", "台股"} and not is_fund_like_symbol(symbol, quote_type)
    if not needs_financials:
        return "not_applicable"
    value_quality = data_quality.get("value", {})
    quality_quality = data_quality.get("quality", {})
    if value_quality.get("confidence") == "none" and quality_quality.get("confidence") == "none":
        return "block"
    critical_missing = set(value_quality.get("missing_fields", [])) & {"pe", "eps_ttm"}
    critical_missing.update(set(quality_quality.get("missing_fields", [])) & {"eps", "revenue_yoy", "revenue_mom"})
    if critical_missing and (
        value_quality.get("confidence") in {"none", "low"} or quality_quality.get("confidence") in {"none", "low"}
    ):
        return "warn"
    if fundamentals:
        revenue_age = yyyymm_age_months(fundamentals.revenue_yyyymm)
        eps_age = quarter_age_quarters(fundamentals.eps_year, fundamentals.eps_quarter)
        if (revenue_age is not None and revenue_age > 4) or (eps_age is not None and eps_age > 3):
            return "warn"
    return "pass"


def opportunity_grade(
    score: int,
    priority: int,
    category: str,
    data_quality: dict[str, dict[str, Any]],
    asset_type: str,
    symbol: str = "",
    quote_type: str | None = None,
    financial_status: str | None = None,
) -> dict[str, Any]:
    if category in {"risk_watch", "deteriorating_trend"} or score <= 42:
        grade = "Risk Watch"
        bucket = "Risk Watch"
    elif score >= 72 and priority >= 55:
        grade = "A"
        bucket = "Buy Candidate"
    elif score >= 62 and priority >= 40:
        grade = "B"
        bucket = "Breakout Watch" if category in {"price_event", "trend_event", "technical_strength"} else "Buy Candidate"
    elif score >= 52 or priority >= 25:
        grade = "C"
        bucket = "Pullback Watch" if score >= 52 else "Event Watch"
    else:
        grade = "Watch"
        bucket = "Watch"

    gate = preliminary_grade(score, data_quality, asset_type, symbol, quote_type, financial_status)
    final_grade = cap_grade(grade, gate["cap"])
    if final_grade != grade and bucket in {"Buy Candidate", "Breakout Watch"}:
        bucket = "Data-Limited Watch"
    return {
        "grade": final_grade,
        "raw_grade": grade,
        "action_bucket": bucket,
        "data_gate": gate,
    }


def requires_equity_fundamentals(quote: Quote) -> bool:
    if quote.asset_type not in {"美股/ETF", "台股"}:
        return False
    return not is_fund_like_symbol(quote.symbol, quote.quote_type)


def build_factor_data_quality(
    quote: Quote,
    chart: list[dict[str, float]],
    news: list[dict[str, str]],
    metrics: dict[str, Any],
    flow_evidence: dict[str, Any],
    fundamentals: AegisFundamentals | None,
    chart_source: str,
) -> dict[str, dict[str, Any]]:
    positive_news = sum(1 for item in news if item["sentiment"] == "positive")
    negative_news = sum(1 for item in news if item["sentiment"] == "negative")
    volume_ratio = None
    if quote.volume and quote.avg_volume and quote.avg_volume > 0:
        volume_ratio = quote.volume / quote.avg_volume
    fundamental_metrics = fundamentals.to_metrics() if fundamentals else {}
    needs_fundamentals = requires_equity_fundamentals(quote)
    value_status = "available" if needs_fundamentals else "not_applicable"
    quality_status = "available" if needs_fundamentals else "not_applicable"
    fundamental_source = f"Yahoo Finance quote + {fundamentals.source}" if fundamentals else "Yahoo Finance quote"
    flow_count = flow_evidence.get("change_count")
    if quote.asset_type != "台股" or is_fund_like_symbol(quote.symbol, quote.quote_type):
        flow_status = "not_applicable"
    else:
        flow_status = "available" if flow_count else "source_missing"
    return {
        "trend": data_quality_entry(
            "trend",
            {
                "price": quote.price,
                "sma_20": metrics.get("ma_20"),
                "sma_60": metrics.get("ma_60"),
                "ema_12": metrics.get("ema_12"),
                "ema_26": metrics.get("ema_26"),
                "macd_histogram": metrics.get("macd_histogram"),
                "macd_histogram_slope": metrics.get("macd_histogram_slope"),
                "adx_14": metrics.get("adx_14"),
                "plus_di": metrics.get("plus_di"),
                "minus_di": metrics.get("minus_di"),
            },
            evidence={"chart_points": len(chart), "source": chart_source},
        ),
        "momentum": data_quality_entry(
            "momentum",
            {
                "change_1m": metrics.get("change_1m"),
                "rsi_14": metrics.get("rsi_14"),
                "stoch_k": metrics.get("stoch_k"),
                "stoch_d": metrics.get("stoch_d"),
                "stoch_spread": metrics.get("stoch_spread"),
                "rs_1m_percentile": metrics.get("rs_1m_percentile"),
                "rs_3m_percentile": metrics.get("rs_3m_percentile"),
            },
            evidence={"chart_points": len(chart), "source": chart_source},
        ),
        "value": data_quality_entry(
            "value",
            {
                "pe": quote.pe,
                "dividend_yield": quote.dividend_yield,
                "eps_ttm": fundamental_metrics.get("eps_ttm"),
            },
            source_status=value_status,
            evidence={"source": fundamental_source, "quote_type": quote.quote_type},
        ),
        "quality": data_quality_entry(
            "quality",
            {
                "sector": quote.sector,
                "industry": quote.industry or fundamental_metrics.get("industry_group"),
                "eps": quote.eps or fundamental_metrics.get("latest_eps"),
                "eps_qoq": fundamental_metrics.get("eps_qoq"),
                "revenue_yoy": fundamental_metrics.get("revenue_yoy"),
                "revenue_mom": fundamental_metrics.get("revenue_mom"),
                "gross_margin": fundamental_metrics.get("gross_margin"),
                "operating_margin": fundamental_metrics.get("operating_margin"),
            },
            source_status=quality_status,
            evidence={"source": fundamental_source, "aegis": fundamental_metrics or None, "quote_type": quote.quote_type},
        ),
        "risk": data_quality_entry(
            "risk",
            {
                "volatility": metrics.get("volatility"),
                "change_3m": metrics.get("change_3m"),
                "atr_14": metrics.get("atr_14"),
                "atr_percent": metrics.get("atr_percent"),
                "bollinger_width": metrics.get("bollinger_width"),
                "hurst_60": metrics.get("hurst_60"),
                "volatility_regime_percentile": metrics.get("volatility_regime_percentile"),
            },
            evidence={"chart_points": len(chart), "source": chart_source},
        ),
        "sentiment": data_quality_entry(
            "sentiment",
            {
                "news_count": len(news) if news else None,
                "positive_news": positive_news if news else None,
                "negative_news": negative_news if news else None,
            },
            source_status="available" if news else "source_missing",
            evidence={"source": "Yahoo Finance search/news", "news_count": len(news)},
        ),
        "liquidity": data_quality_entry(
            "liquidity",
            {
                "volume": quote.volume,
                "avg_volume": quote.avg_volume,
                "volume_ratio": volume_ratio,
                "obv_slope": metrics.get("obv_slope"),
                "vwap_20": metrics.get("vwap_20"),
            },
            evidence={"source": "Yahoo Finance quote"},
        ),
        "active_etf_flow": data_quality_entry(
            "active_etf_flow",
            {
                "active_etf_change_count": flow_count,
                "active_etf_avg_score": flow_evidence.get("avg_score"),
            },
            source_status=flow_status,
            evidence=flow_evidence,
        ),
    }


def build_data_policy(
    quote: Quote,
    chart: list[dict[str, float]],
    news: list[dict[str, str]],
    fundamentals: AegisFundamentals | None,
    chart_source: str,
    data_quality: dict[str, dict[str, Any]],
    flow_evidence: dict[str, Any],
) -> dict[str, Any]:
    needs_fundamentals = requires_equity_fundamentals(quote)
    attempts = [
        {
            "source": "Yahoo Finance quote",
            "status": "available",
            "fields": {
                "price": quote.price,
                "pe": quote.pe,
                "eps": quote.eps,
                "dividend_yield": quote.dividend_yield,
                "volume": quote.volume,
                "avg_volume": quote.avg_volume,
                "quote_type": quote.quote_type,
            },
        },
        {
            "source": chart_source,
            "status": "available" if chart else "source_missing",
            "fields": {"chart_points": len(chart)},
        },
        {
            "source": "Yahoo Finance search/news",
            "status": "available" if news else "source_missing",
            "fields": {"news_count": len(news)},
        },
    ]
    if quote.asset_type == "台股" and needs_fundamentals:
        attempts.append(
            {
                "source": "AegisTrader snapshot fundamentals",
                "status": "available" if fundamentals else "source_missing",
                "fields": fundamentals.to_metrics() if fundamentals else {},
            }
        )
    elif needs_fundamentals:
        if fundamentals:
            attempts.append(
                {
                    "source": fundamentals.source,
                    "status": "available",
                    "fields": fundamentals.to_metrics(),
                }
            )
        else:
            attempts.append(
                {
                    "source": "Yahoo Finance quote fundamentals",
                    "status": "available" if quote.pe is not None or quote.eps is not None else "partial",
                    "fields": {"pe": quote.pe, "eps": quote.eps, "dividend_yield": quote.dividend_yield},
                }
            )
    else:
        attempts.append(
            {
                "source": "Traditional equity fundamentals",
                "status": "not_applicable",
                "fields": {"asset_type": quote.asset_type, "quote_type": quote.quote_type},
            }
        )
    attempts.append(
        {
            "source": "Taiwan active ETF flow",
            "status": flow_evidence.get("source_status") or ("available" if flow_evidence.get("change_count") else "source_missing"),
            "fields": flow_evidence,
        }
    )

    unresolved = []
    for key, row in data_quality.items():
        if row.get("confidence") in {"none", "low"} and row.get("source_status") != "not_applicable":
            unresolved.append(
                {
                    "factor": key,
                    "label": row.get("factor_label", key),
                    "confidence": row.get("confidence"),
                    "source_status": row.get("source_status"),
                    "missing_fields": row.get("missing_fields", []),
                }
            )
    return {
        "rule": "Fetch first, then cap the grade only for unresolved material gaps.",
        "attempted_sources": attempts,
        "unresolved_gaps": unresolved,
    }
