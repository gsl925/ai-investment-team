from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE_DB = Path(os.getenv("AEGIS_TRADER_DB", r"D:\_Personal\_Coding\_Python\AegisTrader\data\aegis_trader.db"))
SNAPSHOT_DB = ROOT / "external_data" / "aegis_trader_snapshot.db"
METADATA_PATH = SNAPSHOT_DB.with_suffix(".metadata.json")


def sidecar_paths(path: Path) -> list[Path]:
    return [Path(f"{path}-wal"), Path(f"{path}-shm")]


def remove_sidecars(path: Path) -> None:
    for sidecar in sidecar_paths(path):
        if sidecar.exists():
            sidecar.unlink()


def remove_if_unlocked(path: Path) -> bool:
    removed = True
    for candidate in [path, *sidecar_paths(path), Path(f"{path}-journal")]:
        if not candidate.exists():
            continue
        try:
            candidate.unlink()
        except OSError as exc:
            removed = False
            print(f"[Investment] Warning: could not remove stale file {candidate}: {exc}", file=sys.stderr)
    return removed


def cleanup_stale_temp_files() -> None:
    for candidate in SNAPSHOT_DB.parent.glob(f"{SNAPSHOT_DB.stem}.tmp.*.db*"):
        remove_if_unlocked(candidate)
    remove_if_unlocked(SNAPSHOT_DB.with_suffix(".tmp.db"))


def new_temp_db_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return SNAPSHOT_DB.parent / f"{SNAPSHOT_DB.stem}.tmp.{stamp}.{os.getpid()}.{uuid.uuid4().hex}.db"


def replace_with_retry(source: Path, target: Path, attempts: int = 6) -> None:
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            source.replace(target)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(min(0.25 * (attempt + 1), 1.5))
    assert last_error is not None
    raise last_error


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    counts: dict[str, int] = {}
    for table in tables:
        counts[table] = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    return counts


def main() -> int:
    if not SOURCE_DB.exists():
        print(f"[Investment] AegisTrader source DB not found. Skipping snapshot: {SOURCE_DB}")
        return 0

    SNAPSHOT_DB.parent.mkdir(parents=True, exist_ok=True)
    cleanup_stale_temp_files()
    temp_db = new_temp_db_path()

    source = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro&immutable=1", uri=True)
    target = sqlite3.connect(temp_db)
    try:
        source.backup(target)
        target.execute("PRAGMA journal_mode=DELETE")
    finally:
        target.close()
        source.close()

    replace_with_retry(temp_db, SNAPSHOT_DB)
    remove_sidecars(SNAPSHOT_DB)
    remove_if_unlocked(temp_db)

    snapshot_conn = sqlite3.connect(f"file:{SNAPSHOT_DB}?mode=ro&immutable=1", uri=True)
    try:
        counts = table_counts(snapshot_conn)
    finally:
        snapshot_conn.close()

    metadata = {
        "source": str(SOURCE_DB),
        "snapshot": str(SNAPSHOT_DB),
        "copied_at": datetime.now(timezone.utc).isoformat(),
        "source_mtime": SOURCE_DB.stat().st_mtime,
        "source_size": SOURCE_DB.stat().st_size,
        "snapshot_size": SNAPSHOT_DB.stat().st_size,
        "table_counts": counts,
    }
    METADATA_PATH.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[Investment] AegisTrader snapshot refreshed: {SNAPSHOT_DB}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
