"""Repeatable WorkSense watcher/DB soak test.

    .\\venv\\Scripts\\python.exe scripts\\soak_test.py --duration 60 --interval 2

The run is fully isolated: a temporary APPDATA, database and project root are
created under the system temp directory.  It exercises real watchdog events,
sampled memory/CPU/thread/process counts, database growth, duplicate detection,
watcher health and a stop/restart recovery point.  JSON results are written to
``test-artifacts``.

This is a local component soak, not a 24-hour production soak.  Sleep/resume
and kernel event-queue overflow require manual/OS-level testing.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="worksense-soak-"))
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

os.environ["APPDATA"] = str(_TEST_ROOT / "AppData")
os.environ["WS_DB_PATH"] = str(_TEST_ROOT / "soak.db")
os.environ["WS_BACKUP_DIR"] = str(_TEST_ROOT / "backups")
os.environ["WS_TESTING"] = "1"

import psutil
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from database.models import Base, FileChangeEvent
from tracker.fs_watcher import FileSystemWatcher

IGNORED = {".git", "__pycache__", "node_modules", ".venv", "build", "dist"}


def db_bytes(path: Path) -> int:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            total += candidate.stat().st_size
    return total


def exact_duplicates(session_factory) -> int:
    session = session_factory()
    try:
        rows = (
            session.query(
                FileChangeEvent.event_type,
                FileChangeEvent.file_path,
                FileChangeEvent.timestamp,
                func.count(FileChangeEvent.id),
            )
            .group_by(FileChangeEvent.event_type, FileChangeEvent.file_path,
                      FileChangeEvent.timestamp)
            .having(func.count(FileChangeEvent.id) > 1)
            .all()
        )
        return len(rows)
    finally:
        session.close()


def row_count(session_factory) -> int:
    session = session_factory()
    try:
        return session.query(FileChangeEvent).count()
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--files-per-sample", type=int, default=4)
    args = parser.parse_args()

    root = _TEST_ROOT / "soak-project"
    root.mkdir(parents=True)
    db_path = Path(os.environ["WS_DB_PATH"])
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    watcher = FileSystemWatcher(
        roots=[{"path": str(root), "workspace": "development"}],
        debounce_seconds=0.3,
        startup_grace=0.0,
        ignored_dirs=IGNORED,
        session_factory=Session,
        active_window_provider=None,
    )
    process = psutil.Process()
    process.cpu_percent(interval=None)  # prime the counter

    started = time.time()
    baseline_rss = process.memory_info().rss
    baseline_threads = process.num_threads()
    baseline_db = db_bytes(db_path)
    samples = []
    restart_recorded = None
    offline_recorded = None
    errors = []

    if not watcher.start():
        errors.append(f"watcher did not start: {watcher.get_status().get('last_error')}")
        return 2

    restart_done = False
    batch = 0
    next_sample = started + args.interval
    next_write = started + 0.5
    try:
        while time.time() - started < args.duration:
            now = time.time()
            if now >= next_write:
                batch += 1
                for index in range(args.files_per_sample):
                    path = root / f"soak_{batch:04d}_{index:02d}.py"
                    path.write_text(f"VALUE = {batch}-{index}\n", encoding="utf-8")
                next_write = now + args.interval

            # Stop/restart halfway through the run.
            elapsed = time.time() - started
            if not restart_done and elapsed >= args.duration / 2:
                watcher.stop()
                offline = root / "created_while_stopped.py"
                offline.write_text("offline\n", encoding="utf-8")
                watcher.start()
                after = root / "created_after_restart.py"
                after.write_text("after restart\n", encoding="utf-8")
                deadline = time.time() + 10
                while time.time() < deadline:
                    session = Session()
                    try:
                        restart_recorded = session.query(FileChangeEvent).filter(
                            FileChangeEvent.file_path == str(after.resolve())
                        ).count() > 0
                        offline_recorded = session.query(FileChangeEvent).filter(
                            FileChangeEvent.file_path == str(offline.resolve())
                        ).count() > 0
                    finally:
                        session.close()
                    if restart_recorded:
                        break
                    time.sleep(0.1)
                restart_done = True

            now = time.time()
            if now >= next_sample:
                status = watcher.get_status()
                samples.append({
                    "elapsed_sec": round(now - started, 2),
                    "rss_mb": round(process.memory_info().rss / (1024 * 1024), 2),
                    "cpu_percent": round(process.cpu_percent(interval=None), 2),
                    "threads": process.num_threads(),
                    "children": len(process.children(recursive=True)),
                    "db_bytes": db_bytes(db_path),
                    "rows": row_count(Session),
                    "events_seen": status["events_seen"],
                    "events_recorded": status["events_recorded"],
                    "events_ignored": status["events_ignored"],
                    "write_errors": status["write_errors"],
                    "dedupe_cache_size": len(watcher._recent_emits),
                })
                next_sample = now + args.interval

            time.sleep(min(0.1, max(0.02, args.interval / 5)))
    finally:
        watcher.stop()

    final_status = watcher.get_status()
    final_rows = row_count(Session)
    duplicates = exact_duplicates(Session)
    final_rss = process.memory_info().rss
    final_threads = process.num_threads()
    final_db = db_bytes(db_path)

    max_rss = max((s["rss_mb"] for s in samples), default=final_rss / (1024 * 1024))
    max_threads = max((s["threads"] for s in samples), default=final_threads)
    avg_cpu = (
        sum(s["cpu_percent"] for s in samples) / len(samples) if samples else 0.0
    )
    checks = {
        "watcher_started": final_status["events_recorded"] > 0,
        "restart_recovered": bool(restart_recorded),
        "offline_change_not_claimed": offline_recorded is False,
        "database_grew": final_db > baseline_db,
        "no_exact_duplicate_rows": duplicates == 0,
        "no_watcher_write_errors": final_status["write_errors"] == 0,
        "no_thread_leak": final_threads <= baseline_threads + 8,
        "memory_growth_bounded": (final_rss - baseline_rss) < 150 * 1024 * 1024,
        "dedupe_cache_bounded": len(watcher._recent_emits) <= 1000,
        "watchdog_health": final_status["available"] and final_status["running"] is False,
    }
    result = {
        "started_at": datetime.fromtimestamp(started).isoformat(),
        "finished_at": datetime.now().isoformat(),
        "duration_sec": round(time.time() - started, 2),
        "config": {
            "interval_sec": args.interval,
            "files_per_sample": args.files_per_sample,
            "root": str(root),
            "db": str(db_path),
        },
        "baseline": {
            "rss_mb": round(baseline_rss / (1024 * 1024), 2),
            "threads": baseline_threads,
            "db_bytes": baseline_db,
        },
        "final": {
            "rss_mb": round(final_rss / (1024 * 1024), 2),
            "max_rss_mb": max_rss,
            "threads": final_threads,
            "max_threads": max_threads,
            "avg_cpu_percent": round(avg_cpu, 2),
            "db_bytes": final_db,
            "db_growth_bytes": final_db - baseline_db,
            "rows": final_rows,
            "exact_duplicate_groups": duplicates,
            "watcher": final_status,
        },
        "samples": samples,
        "checks": checks,
        "success": all(checks.values()) and not errors,
        "errors": errors,
        "not_covered": {
            "sleep_resume": "manual OS suspend/resume required",
            "event_queue_overflow": "requires OS-level/debugger stress",
            "24h_production_soak": "this is a controlled short local soak",
        },
    }

    out_dir = _PROJECT_ROOT / "test-artifacts"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"soak-{datetime.now():%Y%m%d-%H%M%S}.json"
    out_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "duration_sec": result["duration_sec"],
        "rows": final_rows,
        "max_rss_mb": max_rss,
        "avg_cpu_percent": round(avg_cpu, 2),
        "max_threads": max_threads,
        "db_growth_bytes": final_db - baseline_db,
        "exact_duplicate_groups": duplicates,
        "checks": checks,
        "success": result["success"],
    }, indent=2))
    print(f"Results: {out_file}")

    engine.dispose()
    if result["success"]:
        shutil.rmtree(_TEST_ROOT, ignore_errors=True)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
