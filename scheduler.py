from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from typing import Any

from common import (
    ACTIVE_ETF_IMPORT_STATE,
    LOG_DIR,
    SCHEDULER_LOCK,
    SCHEDULER_STATE,
    SCHEDULER_STOP,
    db_connect,
    parse_utc_timestamp,
    safe_float,
    utc_now,
)

SCHEDULER_THREAD: threading.Thread | None = None

# Consecutive run failures at/above this count, or an overdue run past the
# staleness threshold, means the last-known scan data may no longer be fresh
# and the user should be alerted rather than silently reading stale numbers.
ALERT_CONSECUTIVE_ERROR_THRESHOLD = 3


def _build_scheduler_logger() -> logging.Logger:
    logger = logging.getLogger("scheduler")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_DIR / "scheduler.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


SCHEDULER_LOGGER = _build_scheduler_logger()


def write_scheduler_alert(message: str) -> None:
    """Append a one-line entry to logs/scheduler_alerts.log — a separate,
    intentionally short file so a real anomaly isn't buried in routine
    per-run log noise from scheduler.log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{utc_now()}] {message}\n"
    try:
        with open(LOG_DIR / "scheduler_alerts.log", "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        SCHEDULER_LOGGER.exception("Failed to write scheduler_alerts.log entry: %s", message)


def scheduler_health_fields(state: dict[str, Any], thread: threading.Thread | None) -> dict[str, Any]:
    thread_alive = bool(thread and thread.is_alive())
    next_run = parse_utc_timestamp(state.get("next_run_at")) if state.get("next_run_at") else None
    overdue_seconds = 0
    if state.get("enabled") and next_run and not state.get("running"):
        overdue_seconds = max(0, int((datetime.now(timezone.utc) - next_run).total_seconds()))
    interval_seconds = max(60, int(state.get("interval_minutes") or 3) * 60)
    # "Stale" is a looser, UI-facing threshold than the 90s used below to decide
    # whether to auto-restart a dead thread — it's meant to answer "has this been
    # stuck long enough that the last scan data on screen might not be current."
    stale_threshold_seconds = max(600, interval_seconds * 3)
    consecutive_errors = int(state.get("consecutive_errors") or 0)
    stale = bool(state.get("enabled")) and overdue_seconds >= stale_threshold_seconds
    return {
        "thread_alive": thread_alive,
        "overdue_seconds": overdue_seconds,
        "healthy": (not state.get("enabled")) or thread_alive and overdue_seconds < 90,
        "stale": stale,
        "consecutive_errors": consecutive_errors,
        "should_alert": stale or consecutive_errors >= ALERT_CONSECUTIVE_ERROR_THRESHOLD,
    }


def scheduler_discovery_limit(batch_size: int) -> int:
    core_limit = min(10, max(0, batch_size // 2))
    return max(1, batch_size - core_limit)


def cursor_after_scan_payload(payload: dict[str, Any], batch_size: int) -> int | None:
    offset = safe_float(payload.get("offset"))
    available = safe_float(payload.get("available_universe_count"))
    if offset is None or available is None or available <= 0:
        return None
    return (int(offset) + scheduler_discovery_limit(batch_size)) % int(available)


def scheduler_profile_key(asset_type: str | None = None, scope: str | None = None) -> str:
    if scope:
        key = scope.strip()
        return f"scope:{key}" if key else "__all__"
    return asset_type.strip() if asset_type else "__all__"


def read_scheduler_state_store(asset_type: str | None = None, scope: str | None = None) -> dict[str, Any] | None:
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT profile_key, asset_type, scope, cursor_offset, interval_minutes, batch_size,
                   refresh_minutes, min_priority, source, updated_at, last_scan_run_id,
                   last_started_at, last_finished_at
            FROM scheduler_state_store
            WHERE profile_key = ?
            """,
            (scheduler_profile_key(asset_type, scope),),
        ).fetchone()
    return dict(row) if row else None


def write_scheduler_state_store(
    *,
    cursor_offset: int,
    settings: dict[str, Any],
    source: str,
    last_scan_run_id: int | None = None,
    last_started_at: str | None = None,
    last_finished_at: str | None = None,
) -> None:
    profile_key = scheduler_profile_key(settings.get("asset_type"), settings.get("scope"))
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO scheduler_state_store (
                profile_key, asset_type, scope, cursor_offset, interval_minutes, batch_size,
                refresh_minutes, min_priority, source, updated_at, last_scan_run_id,
                last_started_at, last_finished_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_key) DO UPDATE SET
                asset_type = excluded.asset_type,
                scope = excluded.scope,
                cursor_offset = excluded.cursor_offset,
                interval_minutes = excluded.interval_minutes,
                batch_size = excluded.batch_size,
                refresh_minutes = excluded.refresh_minutes,
                min_priority = excluded.min_priority,
                source = excluded.source,
                updated_at = excluded.updated_at,
                last_scan_run_id = COALESCE(excluded.last_scan_run_id, scheduler_state_store.last_scan_run_id),
                last_started_at = COALESCE(excluded.last_started_at, scheduler_state_store.last_started_at),
                last_finished_at = COALESCE(excluded.last_finished_at, scheduler_state_store.last_finished_at)
            """,
            (
                profile_key,
                settings.get("asset_type"),
                settings.get("scope"),
                max(0, int(cursor_offset)),
                settings.get("interval_minutes"),
                settings.get("batch_size"),
                settings.get("refresh_minutes"),
                settings.get("min_priority"),
                source,
                utc_now(),
                last_scan_run_id,
                last_started_at,
                last_finished_at,
            ),
        )


def infer_scheduler_cursor_offset(
    batch_size: int,
    asset_type: str | None = None,
    scope: str | None = None,
) -> int:
    stored = read_scheduler_state_store(asset_type, scope)
    if stored is not None:
        return max(0, int(stored.get("cursor_offset") or 0))

    with db_connect() as conn:
        scheduler_rows = conn.execute(
            """
            SELECT result_json
            FROM scheduler_runs
            WHERE status = 'ok'
              AND result_json IS NOT NULL
              AND (? IS NULL OR asset_type = ?)
              AND (? IS NULL OR scope = ?)
            ORDER BY id DESC
            LIMIT 5
            """,
            (asset_type, asset_type, scope, scope),
        ).fetchall()
        for row in scheduler_rows:
            try:
                payload = json.loads(row["result_json"] or "{}")
            except json.JSONDecodeError:
                continue
            cursor = cursor_after_scan_payload(payload, batch_size)
            if cursor is not None:
                return cursor

        scan_rows = conn.execute(
            """
            SELECT results_json
            FROM scan_runs
            ORDER BY id DESC
            LIMIT 5
            """
        ).fetchall()
        for row in scan_rows:
            try:
                payload = json.loads(row["results_json"] or "{}")
            except json.JSONDecodeError:
                continue
            cursor = cursor_after_scan_payload(payload, batch_size)
            if cursor is not None:
                return cursor
    return 0


def scheduler_state(repair: bool = True) -> dict[str, Any]:
    global SCHEDULER_THREAD
    with SCHEDULER_LOCK:
        state = dict(SCHEDULER_STATE)
        thread = SCHEDULER_THREAD
    health = scheduler_health_fields(state, thread)
    if repair and state.get("enabled") and not state.get("running"):
        should_restart = (not health["thread_alive"]) or health["overdue_seconds"] >= 90
        if should_restart:
            settings = {
                "interval_minutes": int(state.get("interval_minutes") or 30),
                "batch_size": int(state.get("batch_size") or 25),
                "refresh_minutes": int(state.get("refresh_minutes") or 60),
                "min_priority": int(state.get("min_priority") or 25),
                "asset_type": state.get("asset_type"),
                "scope": state.get("scope"),
                "cursor_offset": int(state.get("cursor_offset") or 0),
            }
            SCHEDULER_STOP.set()
            if thread and thread.is_alive():
                thread.join(timeout=2)
            with SCHEDULER_LOCK:
                SCHEDULER_STOP.clear()
                SCHEDULER_THREAD = threading.Thread(target=scheduler_loop, args=(settings,), daemon=True)
                SCHEDULER_THREAD.start()
                SCHEDULER_STATE.update(settings)
                SCHEDULER_STATE.update(
                    {
                        "enabled": True,
                        "running": False,
                        "next_run_at": None,
                        "last_error": f"Scheduler auto-restarted after stale state ({health['overdue_seconds']}s overdue).",
                        "started_at": utc_now(),
                    }
                )
                state = dict(SCHEDULER_STATE)
                thread = SCHEDULER_THREAD
            health = scheduler_health_fields(state, thread)
            health["restarted"] = True
            SCHEDULER_LOGGER.error(
                "Scheduler thread restarted after stale state (%ss overdue)",
                health["overdue_seconds"],
            )
            write_scheduler_alert(
                f"Scheduler thread was dead/stuck ({health['overdue_seconds']}s overdue) and has been auto-restarted."
            )
    state.update(health)
    try:
        state["persisted"] = read_scheduler_state_store(state.get("asset_type"), state.get("scope"))
    except sqlite3.Error:
        state["persisted"] = None
    return state


def update_scheduler_state(**updates: Any) -> None:
    with SCHEDULER_LOCK:
        SCHEDULER_STATE.update(updates)


def record_scheduler_run(
    started_at: str,
    status: str,
    settings: dict[str, Any],
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    finished_at = utc_now()
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO scheduler_runs (
                started_at, finished_at, status, interval_minutes, batch_size, refresh_minutes,
                min_priority, asset_type, scope, scan_run_id, result_json, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                started_at,
                finished_at,
                status,
                settings.get("interval_minutes"),
                settings.get("batch_size"),
                settings.get("refresh_minutes"),
                settings.get("min_priority"),
                settings.get("asset_type"),
                settings.get("scope"),
                result.get("scan_run_id") if result else None,
                json.dumps(result, ensure_ascii=False, separators=(",", ":")) if result else None,
                error,
            ),
        )


def run_scheduler_task(name: str, func: Any, critical: bool = False) -> dict[str, Any]:
    started = time.time()
    try:
        result = func()
    except Exception as exc:
        if critical:
            raise
        return {
            "name": name,
            "status": "error",
            "elapsed_seconds": round(time.time() - started, 2),
            "error": str(exc),
        }
    status = "ok"
    if isinstance(result, dict) and result.get("error"):
        status = "warn"
    return {
        "name": name,
        "status": status,
        "elapsed_seconds": round(time.time() - started, 2),
        "result": result,
    }


def scheduler_loop(settings: dict[str, Any]) -> None:
    # Late import to break circular dependency: scheduler.py <- app.py
    from app import (
        get_priority_scoped_universe,
        get_priority_universe,
        get_scoped_universe,
        get_scoped_universe_count,
        get_universe,
        get_universe_count,
        refresh_aegis_snapshot_if_needed,
        run_due_active_etf_import,
        get_fundamental_momentum_scan,
    )
    from scan import run_due_daily_recommendation_log, scan_market, refresh_pending_outcome_symbols  # circular dep break
    update_scheduler_state(enabled=True, running=False, started_at=utc_now(), last_error=None, run_count=0, **settings)
    while not SCHEDULER_STOP.is_set():
        started_at = utc_now()
        offset = int(settings.get("cursor_offset") or 0)
        update_scheduler_state(running=True, last_run_at=started_at, last_error=None, cursor_offset=offset)
        try:
            scheduler_tasks: dict[str, Any] = {}
            aegis_task = run_scheduler_task("aegis_refresh", refresh_aegis_snapshot_if_needed)
            scheduler_tasks["aegis_refresh"] = aegis_task
            aegis_refresh_result = aegis_task.get("result") or {"error": aegis_task.get("error")}
            active_etf_task = run_scheduler_task("active_etf_import", run_due_active_etf_import)
            scheduler_tasks["active_etf_import"] = active_etf_task
            active_etf_result = active_etf_task.get("result") or {"error": active_etf_task.get("error")}
            batch_size = int(settings["batch_size"])
            discovery_limit = scheduler_discovery_limit(batch_size)
            core_limit = batch_size - discovery_limit
            scope = settings.get("scope")
            if scope:
                core_rows = get_priority_scoped_universe(scope, settings.get("asset_type"), core_limit)
                available = get_scoped_universe_count(scope, settings.get("asset_type"))
            else:
                core_rows = get_priority_universe(settings.get("asset_type"), core_limit)
                available = get_universe_count(settings.get("asset_type"))
            core_symbols = {row["symbol"] for row in core_rows}

            # ── Fundamental momentum override ────────────────────────────
            # After Aegis refresh, identify stocks with accelerating fundamentals
            # that haven't been recently scanned. These skip the discovery cursor.
            fm_override_rows: list[dict[str, Any]] = []
            fm_override_symbols: set[str] = set()
            fm_slots = max(1, batch_size // 5)  # up to 20% of batch reserved for fm
            try:
                fm_result = get_fundamental_momentum_scan(min_revenue_yi=1.0, limit=80, sort_by="rev_accel")
                fm_scope_symbols: set[str] = set()
                if scope:
                    with db_connect() as _c:
                        fm_scope_symbols = {
                            r[0] for r in _c.execute(
                                "SELECT symbol FROM universe_membership WHERE scope = ?", (scope,)
                            ).fetchall()
                        }
                # Stocks recently scanned (within last 4 hours) — skip them
                recently_scanned: set[str] = set()
                with db_connect() as _c:
                    cutoff = (
                        datetime.now(tz=timezone.utc) - timedelta(hours=4)
                    ).isoformat()
                    recently_scanned = {
                        r[0] for r in _c.execute(
                            "SELECT DISTINCT symbol FROM price_snapshots WHERE captured_at >= ?",
                            (cutoff,),
                        ).fetchall()
                    }
                for row in fm_result.get("rows", []):
                    if len(fm_override_rows) >= fm_slots:
                        break
                    if row["tier"] not in ("強加速", "加速"):
                        break
                    if row.get("rev_accel") is None or row["rev_accel"] < 15:
                        continue
                    sym = row["symbol"] + ".TW"
                    if sym in core_symbols or sym in fm_override_symbols:
                        continue
                    if scope and sym not in fm_scope_symbols:
                        continue
                    if sym in recently_scanned:
                        continue
                    fm_override_rows.append({
                        "symbol": sym,
                        "name": row["name"],
                        "asset_type": "stock",
                        "currency": "TWD",
                        "sector": row.get("industry", ""),
                        "industry": row.get("industry", ""),
                        "enabled": 1,
                        "_fm_tier": row["tier"],
                        "_fm_accel": row.get("rev_accel"),
                    })
                    fm_override_symbols.add(sym)
            except Exception as _fm_exc:
                fm_override_rows = []
                fm_override_symbols = set()

            already_in_batch = core_symbols | fm_override_symbols
            discovery_limit = max(1, batch_size - len(core_rows) - len(fm_override_rows))
            discovery_rows = []
            discovery_offset = offset
            attempts = 0
            while len(discovery_rows) < discovery_limit and attempts < 3 and available:
                if scope:
                    rows = get_scoped_universe(
                        scope,
                        settings.get("asset_type"),
                        discovery_limit + len(core_rows) + len(fm_override_rows),
                        discovery_offset,
                    )
                else:
                    rows = get_universe(settings.get("asset_type"), discovery_limit + len(core_rows) + len(fm_override_rows), discovery_offset)
                discovery_rows.extend(row for row in rows if row["symbol"] not in already_in_batch)
                discovery_offset = (discovery_offset + max(1, len(rows))) % available
                attempts += 1
            universe_rows = core_rows + fm_override_rows + discovery_rows[:discovery_limit]
            market_scan_started = time.time()
            result = scan_market(
                limit=len(universe_rows),
                threshold_override=None,
                min_priority=settings["min_priority"],
                asset_type=settings.get("asset_type"),
                refresh_minutes=settings["refresh_minutes"],
                offset=offset,
                scope=scope,
                universe_override=universe_rows,
                available_universe_count_override=available,
            )
            scheduler_tasks["market_scan"] = {
                "name": "market_scan",
                "status": "ok",
                "elapsed_seconds": round(time.time() - market_scan_started, 2),
                "result": {
                    "scan_run_id": result.get("scan_run_id"),
                    "scanned_count": result.get("scanned_count"),
                    "opportunity_count": len(result.get("opportunities", [])),
                    "cache_hits": result.get("cache_hits"),
                    "refreshed_count": result.get("refreshed_count"),
                },
            }
            daily_log_task = run_scheduler_task("daily_recommendation_log", run_due_daily_recommendation_log)
            scheduler_tasks["daily_recommendation_log"] = daily_log_task
            daily_recommendation_log = daily_log_task.get("result") or {"error": daily_log_task.get("error")}
            pending_snapshot_task = run_scheduler_task(
                "pending_outcome_snapshots",
                lambda: refresh_pending_outcome_symbols(limit=30),
            )
            scheduler_tasks["pending_outcome_snapshots"] = pending_snapshot_task
            next_offset = (offset + discovery_limit) % available if available else 0
            settings["cursor_offset"] = next_offset
            record_scheduler_run(started_at, "ok", settings, result=result)
            for task_name, task in scheduler_tasks.items():
                if task.get("status") in ("warn", "error"):
                    SCHEDULER_LOGGER.warning(
                        "Task %s status=%s detail=%s",
                        task_name,
                        task.get("status"),
                        task.get("error") or (task.get("result") or {}).get("error"),
                    )
            SCHEDULER_LOGGER.info(
                "Run ok scan_run_id=%s scanned=%s opportunities=%s elapsed=%.1fs offset=%s->%s",
                result.get("scan_run_id"),
                result.get("scanned_count"),
                len(result.get("opportunities", [])),
                result.get("elapsed_seconds") or 0.0,
                offset,
                next_offset,
            )
            update_scheduler_state(consecutive_errors=0)
            write_scheduler_state_store(
                cursor_offset=next_offset,
                settings=settings,
                source="auto",
                last_scan_run_id=result.get("scan_run_id"),
                last_started_at=started_at,
                last_finished_at=utc_now(),
            )
            with SCHEDULER_LOCK:
                SCHEDULER_STATE["last_result"] = {
                    "scan_run_id": result.get("scan_run_id"),
                    "scanned_count": result.get("scanned_count"),
                    "available_universe_count": result.get("available_universe_count"),
                    "offset": result.get("offset"),
                    "next_offset": next_offset,
                    "core_count": len(core_rows),
                    "fm_override_count": len(fm_override_rows),
                    "fm_override_symbols": [r["symbol"] for r in fm_override_rows],
                    "discovery_count": len(discovery_rows[:discovery_limit]),
                    "cache_hits": result.get("cache_hits"),
                    "refreshed_count": result.get("refreshed_count"),
                    "opportunity_count": len(result.get("opportunities", [])),
                    "elapsed_seconds": result.get("elapsed_seconds"),
                    "aegis_snapshot_refresh": aegis_refresh_result,
                    "active_etf_import": active_etf_result,
                    "daily_recommendation_log": daily_recommendation_log,
                    "pending_outcome_snapshots": pending_snapshot_task.get("result"),
                    "tasks": scheduler_tasks,
                    "scope": scope,
                }
                SCHEDULER_STATE["run_count"] = int(SCHEDULER_STATE.get("run_count") or 0) + 1
                SCHEDULER_STATE["cursor_offset"] = next_offset
                SCHEDULER_STATE["active_etf"] = dict(ACTIVE_ETF_IMPORT_STATE)
        except Exception as exc:
            message = str(exc)
            record_scheduler_run(started_at, "error", settings, error=message)
            with SCHEDULER_LOCK:
                consecutive_errors = int(SCHEDULER_STATE.get("consecutive_errors") or 0) + 1
            update_scheduler_state(last_error=message, consecutive_errors=consecutive_errors)
            SCHEDULER_LOGGER.exception(
                "Scheduler run failed (consecutive_errors=%s)", consecutive_errors
            )
            if consecutive_errors >= ALERT_CONSECUTIVE_ERROR_THRESHOLD:
                write_scheduler_alert(
                    f"Scheduler run failed {consecutive_errors} times in a row. Latest error: {message}"
                )
        interval_seconds = max(60, int(settings["interval_minutes"]) * 60)
        next_run = datetime.now(timezone.utc) + timedelta(seconds=interval_seconds)
        update_scheduler_state(running=False, next_run_at=next_run.isoformat())
        if SCHEDULER_STOP.wait(interval_seconds):
            break
    update_scheduler_state(enabled=False, running=False, next_run_at=None)


def start_scheduler(
    interval_minutes: int = 30,
    batch_size: int = 25,
    refresh_minutes: int = 60,
    min_priority: int = 25,
    asset_type: str | None = None,
    scope: str | None = None,
    cursor_offset: int | None = None,
) -> dict[str, Any]:
    global SCHEDULER_THREAD
    interval_minutes = max(1, min(interval_minutes, 1440))
    batch_size = max(1, min(batch_size, 200))
    refresh_minutes = max(0, min(refresh_minutes, 1440))
    min_priority = max(0, min(min_priority, 100))
    scope = scope.strip() if scope else None
    explicit_cursor = cursor_offset is not None
    cursor_offset = (
        infer_scheduler_cursor_offset(batch_size, asset_type, scope)
        if cursor_offset is None
        else max(0, cursor_offset)
    )
    settings = {
        "interval_minutes": interval_minutes,
        "batch_size": batch_size,
        "refresh_minutes": refresh_minutes,
        "min_priority": min_priority,
        "asset_type": asset_type,
        "scope": scope,
        "cursor_offset": cursor_offset,
    }
    thread_to_stop: threading.Thread | None = None
    with SCHEDULER_LOCK:
        if SCHEDULER_THREAD and SCHEDULER_THREAD.is_alive():
            same_settings = all(SCHEDULER_STATE.get(key) == value for key, value in settings.items())
            if same_settings:
                return dict(SCHEDULER_STATE)
            SCHEDULER_STOP.set()
            thread_to_stop = SCHEDULER_THREAD
    if thread_to_stop and thread_to_stop.is_alive():
        thread_to_stop.join(timeout=5)
    with SCHEDULER_LOCK:
        SCHEDULER_THREAD = None
        write_scheduler_state_store(
            cursor_offset=cursor_offset,
            settings=settings,
            source="manual_reset" if explicit_cursor and cursor_offset == 0 else "manual" if explicit_cursor else "auto_start",
        )
        SCHEDULER_STOP.clear()
        SCHEDULER_THREAD = threading.Thread(target=scheduler_loop, args=(settings,), daemon=True)
        SCHEDULER_THREAD.start()
        SCHEDULER_STATE.update(settings)
        SCHEDULER_STATE.update({"enabled": True, "last_error": None, "started_at": utc_now()})
        return dict(SCHEDULER_STATE)


def stop_scheduler() -> dict[str, Any]:
    global SCHEDULER_THREAD
    SCHEDULER_STOP.set()
    thread = SCHEDULER_THREAD
    if thread and thread.is_alive():
        thread.join(timeout=2)
    with SCHEDULER_LOCK:
        SCHEDULER_THREAD = None
        SCHEDULER_STATE.update({"enabled": False, "running": False, "next_run_at": None})
    return scheduler_state(repair=False)
