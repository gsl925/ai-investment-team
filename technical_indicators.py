from __future__ import annotations

import math
import sqlite3
import statistics
from typing import Any

from common import db_connect, safe_float


def percent_change(chart: list[dict[str, float]], lookback: int) -> float | None:
    if len(chart) <= lookback:
        return None
    current = chart[-1]["close"]
    previous = chart[-lookback - 1]["close"]
    if previous == 0:
        return None
    return (current / previous - 1) * 100


def moving_average(chart: list[dict[str, float]], window: int) -> float | None:
    if len(chart) < window:
        return None
    return statistics.fmean(point["close"] for point in chart[-window:])


def ema_series(values: list[float], window: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (window + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(value * alpha + result[-1] * (1 - alpha))
    return result


def ema_value(chart: list[dict[str, float]], window: int) -> float | None:
    if len(chart) < window:
        return None
    closes = [point["close"] for point in chart]
    return ema_series(closes, window)[-1]


def macd_metrics(chart: list[dict[str, float]]) -> dict[str, float | None]:
    if len(chart) < 35:
        return {"macd": None, "signal": None, "histogram": None, "histogram_slope": None}
    closes = [point["close"] for point in chart]
    ema_12 = ema_series(closes, 12)
    ema_26 = ema_series(closes, 26)
    macd_line = [fast - slow for fast, slow in zip(ema_12, ema_26, strict=False)]
    signal_line = ema_series(macd_line, 9)
    histogram = [macd - signal for macd, signal in zip(macd_line, signal_line, strict=False)]
    slope = histogram[-1] - histogram[-2] if len(histogram) >= 2 else None
    return {
        "macd": macd_line[-1],
        "signal": signal_line[-1],
        "histogram": histogram[-1],
        "histogram_slope": slope,
    }


def rsi(chart: list[dict[str, float]], window: int = 14) -> float | None:
    if len(chart) <= window:
        return None
    gains = []
    losses = []
    for prev, current in zip(chart[-window - 1:-1], chart[-window:], strict=False):
        change = current["close"] - prev["close"]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))
    avg_gain = statistics.fmean(gains)
    avg_loss = statistics.fmean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def stochastic_kd(chart: list[dict[str, float]], window: int = 14, smooth: int = 3) -> dict[str, float | None]:
    if len(chart) < window + smooth:
        return {"k": None, "d": None, "spread": None}
    k_values = []
    for idx in range(window - 1, len(chart)):
        segment = chart[idx - window + 1:idx + 1]
        highest = max(point.get("high", point["close"]) for point in segment)
        lowest = min(point.get("low", point["close"]) for point in segment)
        close = chart[idx]["close"]
        if highest == lowest:
            k_values.append(50.0)
        else:
            k_values.append((close - lowest) / (highest - lowest) * 100)
    if len(k_values) < smooth:
        return {"k": None, "d": None, "spread": None}
    k = statistics.fmean(k_values[-smooth:])
    d = statistics.fmean(k_values[-smooth * 2:-smooth]) if len(k_values) >= smooth * 2 else k
    return {"k": k, "d": d, "spread": k - d}


def atr(chart: list[dict[str, float]], window: int = 14) -> float | None:
    if len(chart) <= window:
        return None
    true_ranges = []
    for prev, current in zip(chart[-window - 1:-1], chart[-window:], strict=False):
        high = current.get("high", current["close"])
        low = current.get("low", current["close"])
        prev_close = prev["close"]
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return statistics.fmean(true_ranges) if true_ranges else None


def bollinger_metrics(chart: list[dict[str, float]], window: int = 20) -> dict[str, float | None]:
    if len(chart) < window:
        return {"middle": None, "upper": None, "lower": None, "width": None, "zscore": None}
    closes = [point["close"] for point in chart[-window:]]
    middle = statistics.fmean(closes)
    sd = statistics.stdev(closes) if len(closes) >= 2 else 0
    upper = middle + 2 * sd
    lower = middle - 2 * sd
    width = (upper - lower) / middle * 100 if middle else None
    zscore = (chart[-1]["close"] - middle) / sd if sd else 0
    return {"middle": middle, "upper": upper, "lower": lower, "width": width, "zscore": zscore}


def directional_movement_metrics(chart: list[dict[str, float]], window: int = 14) -> dict[str, float | None]:
    if len(chart) < (window * 2) + 1:
        return {"adx": None, "plus_di": None, "minus_di": None}

    true_ranges: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for prev, current in zip(chart[:-1], chart[1:], strict=False):
        current_high = current.get("high", current["close"])
        current_low = current.get("low", current["close"])
        prev_high = prev.get("high", prev["close"])
        prev_low = prev.get("low", prev["close"])
        prev_close = prev["close"]

        up_move = current_high - prev_high
        down_move = prev_low - current_low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        true_ranges.append(max(current_high - current_low, abs(current_high - prev_close), abs(current_low - prev_close)))

    dx_values: list[float] = []
    plus_di_values: list[float] = []
    minus_di_values: list[float] = []
    for end in range(window, len(true_ranges) + 1):
        tr_sum = sum(true_ranges[end - window:end])
        if tr_sum <= 0:
            continue
        plus_di = 100 * sum(plus_dm[end - window:end]) / tr_sum
        minus_di = 100 * sum(minus_dm[end - window:end]) / tr_sum
        denominator = plus_di + minus_di
        if denominator <= 0:
            continue
        plus_di_values.append(plus_di)
        minus_di_values.append(minus_di)
        dx_values.append(100 * abs(plus_di - minus_di) / denominator)

    if not dx_values or not plus_di_values or not minus_di_values:
        return {"adx": None, "plus_di": None, "minus_di": None}
    adx_window = dx_values[-window:] if len(dx_values) >= window else dx_values
    return {
        "adx": statistics.fmean(adx_window),
        "plus_di": plus_di_values[-1],
        "minus_di": minus_di_values[-1],
    }


def obv_slope(chart: list[dict[str, float]], window: int = 20) -> float | None:
    if len(chart) <= window or any("volume" not in point for point in chart[-window - 1:]):
        return None
    obv = [0.0]
    for prev, current in zip(chart[-window - 1:-1], chart[-window:], strict=False):
        volume = current.get("volume")
        if volume is None:
            return None
        if current["close"] > prev["close"]:
            obv.append(obv[-1] + volume)
        elif current["close"] < prev["close"]:
            obv.append(obv[-1] - volume)
        else:
            obv.append(obv[-1])
    return (obv[-1] - obv[0]) / max(1, window)


def rolling_vwap(chart: list[dict[str, float]], window: int = 20) -> float | None:
    if len(chart) < window:
        return None
    total_value = 0.0
    total_volume = 0.0
    for point in chart[-window:]:
        volume = point.get("volume")
        if not volume:
            return None
        typical = (point.get("high", point["close"]) + point.get("low", point["close"]) + point["close"]) / 3
        total_value += typical * volume
        total_volume += volume
    return total_value / total_volume if total_volume else None


def average_volume(chart: list[dict[str, float]], window: int = 60) -> float | None:
    if len(chart) < window:
        return None
    volumes = [point.get("volume") for point in chart[-window:]]
    if any(volume is None for volume in volumes):
        return None
    valid = [float(volume) for volume in volumes if volume is not None]
    return statistics.fmean(valid) if valid else None


def hurst_exponent(chart: list[dict[str, float]], window: int = 60) -> float | None:
    if len(chart) < window:
        return None
    prices = [point["close"] for point in chart[-window:]]
    lags = [2, 4, 8, 16]
    xs = []
    ys = []
    for lag in lags:
        diffs = [prices[i] - prices[i - lag] for i in range(lag, len(prices))]
        if len(diffs) < 2:
            continue
        tau = statistics.stdev(diffs)
        if tau > 0:
            xs.append(math.log(lag))
            ys.append(math.log(tau))
    if len(xs) < 2:
        return None
    x_mean = statistics.fmean(xs)
    y_mean = statistics.fmean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=False))
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator == 0:
        return None
    return numerator / denominator


def volatility(chart: list[dict[str, float]]) -> float | None:
    if len(chart) < 22:
        return None
    returns = []
    for prev, current in zip(chart[-63:-1], chart[-62:], strict=False):
        if prev["close"] > 0:
            returns.append(current["close"] / prev["close"] - 1)
    if len(returns) < 10:
        return None
    return statistics.stdev(returns) * math.sqrt(252) * 100


def percentile_rank(values: list[float], current: float | None) -> float | None:
    clean = sorted(value for value in values if value is not None and math.isfinite(value))
    if current is None or not math.isfinite(current) or not clean:
        return None
    below = sum(1 for value in clean if value < current)
    equal = sum(1 for value in clean if value == current)
    return (below + 0.5 * equal) / len(clean) * 100


def volatility_regime_percentile(chart: list[dict[str, float]], window: int = 20, lookback: int = 120) -> float | None:
    if len(chart) < window + 2:
        return None
    closes = [point["close"] for point in chart if point.get("close") is not None and point["close"] > 0]
    if len(closes) < window + 2:
        return None
    returns = [current / prev - 1 for prev, current in zip(closes[:-1], closes[1:], strict=False) if prev > 0]
    if len(returns) < window:
        return None
    rolling_vols: list[float] = []
    start_index = max(window, len(returns) - lookback)
    for end in range(start_index, len(returns) + 1):
        segment = returns[end - window:end]
        if len(segment) >= max(10, window // 2):
            rolling_vols.append(statistics.stdev(segment) * math.sqrt(252) * 100)
    if not rolling_vols:
        return None
    return percentile_rank(rolling_vols, rolling_vols[-1])


def relative_strength_percentiles(
    symbol: str,
    asset_type: str,
    change_1m: float | None,
    change_3m: float | None,
) -> dict[str, float | int | None]:
    rows: list[sqlite3.Row] = []
    try:
        with db_connect() as conn:
            rows = conn.execute(
                """
                WITH latest AS (
                    SELECT symbol, MAX(captured_at) AS captured_at
                    FROM price_snapshots
                    GROUP BY symbol
                )
                SELECT ps.symbol, ps.change_1m, ps.change_3m
                FROM price_snapshots ps
                JOIN latest l ON l.symbol = ps.symbol AND l.captured_at = ps.captured_at
                JOIN universe u ON u.symbol = ps.symbol
                WHERE u.enabled = 1 AND u.asset_type = ?
                """,
                (asset_type,),
            ).fetchall()
    except sqlite3.Error:
        rows = []

    change_1m_values: list[float] = []
    change_3m_values: list[float] = []
    seen = set()
    for row in rows:
        row_symbol = row["symbol"]
        seen.add(row_symbol)
        row_1m = safe_float(row["change_1m"])
        row_3m = safe_float(row["change_3m"])
        if row_symbol == symbol:
            row_1m = change_1m if change_1m is not None else row_1m
            row_3m = change_3m if change_3m is not None else row_3m
        if row_1m is not None:
            change_1m_values.append(row_1m)
        if row_3m is not None:
            change_3m_values.append(row_3m)
    if symbol not in seen:
        if change_1m is not None:
            change_1m_values.append(change_1m)
        if change_3m is not None:
            change_3m_values.append(change_3m)

    peer_count = max(len(change_1m_values), len(change_3m_values))
    return {
        "rs_1m_percentile": percentile_rank(change_1m_values, change_1m),
        "rs_3m_percentile": percentile_rank(change_3m_values, change_3m),
        "rs_peer_count": peer_count,
    }


def max_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1)
    return worst * 100
