from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from common import db_connect, parse_utc_timestamp, safe_float, utc_now

OUTCOME_HORIZONS = [5, 20, 60]


def add_recommendation_outcomes(
    conn: sqlite3.Connection,
    opportunity_id: int,
    scan_run_id: int,
    opportunity: dict[str, Any],
    created_at: str,
) -> None:
    start_at = opportunity.get("generated_at") or created_at
    start_price = safe_float(opportunity.get("price"))
    raw_json = json.dumps(opportunity, ensure_ascii=False, separators=(",", ":"))
    try:
        start_dt = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
    except ValueError:
        start_dt = datetime.now(timezone.utc)
        start_at = start_dt.isoformat()
    for horizon in OUTCOME_HORIZONS:
        due_at = (start_dt + timedelta(days=horizon)).isoformat()
        conn.execute(
            """
            INSERT OR IGNORE INTO recommendation_outcomes (
                opportunity_id, scan_run_id, snapshot_id, symbol, horizon_days, start_price,
                start_at, due_at, status, grade, action_bucket, score, priority,
                created_at, updated_at, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                opportunity_id,
                scan_run_id,
                opportunity.get("snapshot_id"),
                opportunity["symbol"],
                horizon,
                start_price,
                start_at,
                due_at,
                "pending",
                opportunity.get("grade"),
                opportunity.get("action_bucket"),
                opportunity.get("score"),
                opportunity.get("priority"),
                created_at,
                created_at,
                raw_json,
            ),
        )


def update_recommendation_outcomes(limit: int = 500) -> dict[str, Any]:
    now = utc_now()
    limit = max(1, min(limit, 5000))
    updated = 0
    still_pending = 0
    missing_price = 0
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM recommendation_outcomes
            WHERE status IN ('pending', 'open_no_price', 'missing_price')
            ORDER BY due_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in rows:
            if row["due_at"] > now:
                still_pending += 1
                continue
            outcome = conn.execute(
                """
                SELECT id, captured_at, price
                FROM price_snapshots
                WHERE symbol = ?
                  AND captured_at >= ?
                  AND id != COALESCE(?, -1)
                  AND price IS NOT NULL
                ORDER BY captured_at ASC
                LIMIT 1
                """,
                (row["symbol"], row["due_at"], row["snapshot_id"]),
            ).fetchone()
            start_price = safe_float(row["start_price"])
            if outcome is None:
                missing_price += 1
                conn.execute(
                    """
                    UPDATE recommendation_outcomes
                    SET status = 'missing_price', updated_at = ?
                    WHERE id = ?
                    """,
                    (now, row["id"]),
                )
                continue
            outcome_price = safe_float(outcome["price"])
            return_percent = None
            status = "open_no_price"
            if start_price is not None and start_price > 0 and outcome_price is not None:
                return_percent = (outcome_price / start_price - 1) * 100
                status = "suspicious" if abs(return_percent) > 500 else "ready"
            else:
                missing_price += 1
            conn.execute(
                """
                UPDATE recommendation_outcomes
                SET outcome_snapshot_id = ?, outcome_price = ?, outcome_at = ?,
                    return_percent = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (outcome["id"], outcome_price, outcome["captured_at"], return_percent, status, now, row["id"]),
            )
            updated += 1
    return {
        "checked": len(rows),
        "updated": updated,
        "pending": still_pending,
        "missing_price": missing_price,
        "generated_at": now,
    }


def get_recommendation_outcomes(limit: int = 100) -> dict[str, Any]:
    update_result = update_recommendation_outcomes()
    limit = max(1, min(limit, 500))
    with db_connect() as conn:
        summary_rows = conn.execute(
            """
            SELECT horizon_days, grade, action_bucket, status,
                   COUNT(*) AS count,
                   AVG(return_percent) AS avg_return_percent,
                   SUM(CASE WHEN return_percent > 0 THEN 1 ELSE 0 END) AS win_count
            FROM recommendation_outcomes
            GROUP BY horizon_days, grade, action_bucket, status
            ORDER BY horizon_days, grade, action_bucket, status
            """
        ).fetchall()
        latest_rows = conn.execute(
            """
            SELECT id, opportunity_id, scan_run_id, symbol, horizon_days, start_price, start_at,
                   due_at, outcome_price, outcome_at, return_percent, status, grade,
                   action_bucket, score, priority, updated_at
            FROM recommendation_outcomes
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    summary = []
    for row in summary_rows:
        count = int(row["count"] or 0)
        win_count = int(row["win_count"] or 0)
        is_ready = row["status"] == "ready"
        summary.append(
            {
                "horizon_days": row["horizon_days"],
                "grade": row["grade"],
                "action_bucket": row["action_bucket"],
                "status": row["status"],
                "count": count,
                "ready_count": count if is_ready else 0,
                "win_count": win_count if is_ready else 0,
                "win_rate": (win_count / count) if count and is_ready else None,
                "avg_return_percent": row["avg_return_percent"],
            }
        )
    return {
        "update": update_result,
        "summary": summary,
        "outcomes": [dict(row) for row in latest_rows],
        "count": len(latest_rows),
    }


def get_missing_recommendation_outcome_prices(limit: int = 100) -> dict[str, Any]:
    update_result = update_recommendation_outcomes(limit=5000)
    limit = max(1, min(limit, 500))
    now = datetime.now(timezone.utc)
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT id, opportunity_id, scan_run_id, symbol, horizon_days, start_price,
                   start_at, due_at, status, grade, action_bucket, score, priority,
                   updated_at, raw_json
            FROM recommendation_outcomes
            WHERE status = 'missing_price'
            ORDER BY due_at ASC, id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        symbols = sorted({row["symbol"] for row in rows})
        latest_by_symbol: dict[str, dict[str, Any]] = {}
        for symbol in symbols:
            latest = conn.execute(
                """
                SELECT id, captured_at, price, currency, raw_json
                FROM price_snapshots
                WHERE symbol = ?
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                (symbol,),
            ).fetchone()
            if latest:
                latest_by_symbol[symbol] = dict(latest)

    items = []
    by_symbol: dict[str, int] = {}
    by_asset_type: dict[str, int] = {}
    for row in rows:
        payload = {}
        try:
            payload = json.loads(row["raw_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        due_dt = parse_utc_timestamp(row["due_at"] or "")
        days_overdue = None
        if due_dt:
            days_overdue = round((now - due_dt).total_seconds() / 86400, 2)
        symbol = row["symbol"]
        latest = latest_by_symbol.get(symbol)
        asset_type = payload.get("asset_type") or payload.get("category") or "unknown"
        if latest is None:
            reason = "no_snapshot"
            suggested_action = "No snapshot exists for this symbol; refresh the symbol or mark the instrument unsupported."
        elif latest.get("captured_at") and latest["captured_at"] < row["due_at"]:
            reason = "snapshot_before_due"
            suggested_action = "Latest snapshot is before the due timestamp; refresh this symbol to close the outcome."
        else:
            reason = "unlinked_snapshot"
            suggested_action = "Snapshot exists after due date but was not linked; rerun outcome update and inspect symbol formatting."
        item = {
            "id": row["id"],
            "symbol": symbol,
            "name": payload.get("name"),
            "asset_type": asset_type,
            "horizon_days": row["horizon_days"],
            "start_price": row["start_price"],
            "start_at": row["start_at"],
            "due_at": row["due_at"],
            "days_overdue": days_overdue,
            "grade": row["grade"],
            "action_bucket": row["action_bucket"],
            "score": row["score"],
            "priority": row["priority"],
            "latest_snapshot": latest,
            "reason": reason,
            "suggested_action": suggested_action,
        }
        items.append(item)
        by_symbol[symbol] = by_symbol.get(symbol, 0) + 1
        by_asset_type[str(asset_type)] = by_asset_type.get(str(asset_type), 0) + 1

    return {
        "generated_at": utc_now(),
        "update": update_result,
        "count": len(items),
        "summary": {
            "by_symbol": [{"symbol": key, "count": value} for key, value in sorted(by_symbol.items())],
            "by_asset_type": [{"asset_type": key, "count": value} for key, value in sorted(by_asset_type.items())],
        },
        "items": items,
        "note": "Missing outcome prices are excluded from attribution until a due-or-later snapshot is available.",
    }


def outcome_group_labels(row: sqlite3.Row, payload: dict[str, Any]) -> list[tuple[str, str]]:
    labels: list[tuple[str, str]] = []
    asset_type = payload.get("asset_type")
    category = payload.get("category")
    grade = row["grade"] or payload.get("grade")
    action_bucket = row["action_bucket"] or payload.get("action_bucket")
    if asset_type:
        labels.append(("asset_type", str(asset_type)))
    if category:
        labels.append(("category", str(category)))
    if grade:
        labels.append(("grade", str(grade)))
    if action_bucket:
        labels.append(("action_bucket", str(action_bucket)))

    score = safe_float(row["score"] if row["score"] is not None else payload.get("score"))
    if score is not None:
        if score >= 70:
            labels.append(("score_band", "score >= 70"))
        elif score >= 58:
            labels.append(("score_band", "58 <= score < 70"))
        elif score <= 42:
            labels.append(("score_band", "score <= 42"))
        else:
            labels.append(("score_band", "43 <= score < 58"))

    priority = safe_float(row["priority"] if row["priority"] is not None else payload.get("priority"))
    if priority is not None:
        if priority >= 80:
            labels.append(("priority_band", "priority >= 80"))
        elif priority >= 50:
            labels.append(("priority_band", "50 <= priority < 80"))
        else:
            labels.append(("priority_band", "priority < 50"))

    factors = payload.get("factors") or {}
    if isinstance(factors, dict):
        for factor, detail in factors.items():
            factor_score = safe_float(detail.get("score") if isinstance(detail, dict) else detail)
            if factor_score is None:
                continue
            if factor_score >= 65:
                labels.append(("factor_high", f"{factor} high"))
            elif factor_score <= 40:
                labels.append(("factor_low", f"{factor} low"))
    return labels


def summarize_outcome_group(group_type: str, label: str, returns: list[float]) -> dict[str, Any]:
    ordered = sorted(returns)
    count = len(ordered)
    wins = sum(1 for value in ordered if value > 0)
    median = ordered[count // 2] if count % 2 else (ordered[count // 2 - 1] + ordered[count // 2]) / 2
    avg_return = sum(ordered) / count
    return {
        "group_type": group_type,
        "label": label,
        "count": count,
        "win_count": wins,
        "win_rate": wins / count if count else None,
        "avg_return_percent": avg_return,
        "median_return_percent": median,
        "worst_return_percent": ordered[0],
        "best_return_percent": ordered[-1],
    }


def backfill_recommendation_outcomes(limit: int = 1000) -> dict[str, Any]:
    limit = max(1, min(limit, 10000))
    created = 0
    checked = 0
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT o.*
            FROM opportunities o
            WHERE NOT EXISTS (
                SELECT 1 FROM recommendation_outcomes ro WHERE ro.opportunity_id = o.id
            )
            ORDER BY o.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in rows:
            checked += 1
            try:
                opportunity = json.loads(row["raw_json"] or "{}")
            except json.JSONDecodeError:
                opportunity = {}
            opportunity.update(
                {
                    "symbol": row["symbol"],
                    "snapshot_id": row["snapshot_id"],
                    "price": row["price"],
                    "grade": opportunity.get("grade"),
                    "action_bucket": opportunity.get("action_bucket"),
                    "score": row["score"],
                    "priority": row["priority"],
                    "generated_at": row["created_at"],
                }
            )
            before = conn.execute("SELECT COUNT(*) FROM recommendation_outcomes").fetchone()[0]
            add_recommendation_outcomes(conn, row["id"], row["scan_run_id"], opportunity, row["created_at"])
            after = conn.execute("SELECT COUNT(*) FROM recommendation_outcomes").fetchone()[0]
            created += int(after - before)
    update_result = update_recommendation_outcomes(limit=5000)
    return {"checked": checked, "created": created, "update": update_result}
