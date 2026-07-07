from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Quote:
    symbol: str
    name: str
    price: float | None
    change_percent: float | None
    market_cap: float | None
    pe: float | None
    eps: float | None
    dividend_yield: float | None
    volume: float | None
    avg_volume: float | None
    currency: str | None
    asset_type: str
    sector: str | None
    industry: str | None
    quote_type: str | None = None


@dataclass
class AegisFundamentals:
    stock_id: str
    name: str | None = None
    market: str | None = None
    industry_group: str | None = None
    latest_eps: float | None = None
    eps_year: int | None = None
    eps_quarter: int | None = None
    eps_qoq: float | None = None
    eps_ttm: float | None = None
    latest_revenue: float | None = None
    revenue_yyyymm: str | None = None
    revenue_yoy: float | None = None
    revenue_mom: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    financial_stmt_date: str | None = None
    source: str = "AegisTrader snapshot"

    def to_metrics(self) -> dict[str, Any]:
        return {
            "stock_id": self.stock_id,
            "name": self.name,
            "market": self.market,
            "industry_group": self.industry_group,
            "latest_eps": self.latest_eps,
            "eps_year": self.eps_year,
            "eps_quarter": self.eps_quarter,
            "eps_qoq": self.eps_qoq,
            "eps_ttm": self.eps_ttm,
            "latest_revenue": self.latest_revenue,
            "revenue_yyyymm": self.revenue_yyyymm,
            "revenue_yoy": self.revenue_yoy,
            "revenue_mom": self.revenue_mom,
            "gross_margin": self.gross_margin,
            "operating_margin": self.operating_margin,
            "financial_stmt_date": self.financial_stmt_date,
            "source": self.source,
        }
