"""Real Windows filesystem watcher validation.

Run with the project venv:

    .\\venv\\Scripts\\python.exe scripts\\validate_watcher.py

The script uses a temporary APPDATA, database and project roots.  It never
touches the production WorkSense database.  It performs editor-like writes,
PowerShell/terminal writes, a background process, rename/delete, multiple
roots, restart, a deleted root, a burst and duplicate suppression.  Results are
written as JSON under ``test-artifacts`` and printed to the console.

Sleep/resume and kernel event-queue overflow cannot be triggered reliably from
a user-mode script; they are reported as skipped/manual rather than guessed.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

# Isolate before importing application modules.
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="worksense-watcher-validation-"))
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
os.environ["APPDATA"] = str(_TEST_ROOT / "AppData")
os.environ["WS_DB_PATH"] = str(_TEST_ROOT / "watcher.db")
os.environ["WS_BACKUP_DIR"] = str(_TEST_ROOT / "backups")
os.environ["WS_TESTING"] = "1"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, FileChangeEvent
from tracker.fs_watcher import FileSystemWatcher
import tracker.fs_watcher as fs_watcher

ROOT = _TEST_ROOT / "projects"
ROOT.mkdir(parents=True)
DB_PATH = Path(os.environ["WS_DB_PATH"])
ENGINE = create_engine(
    f"sqlite:///{DB_PATH.as_posix()}", connect_args={"check_same_thread": False}
)
Base.metadata.create_all(ENGINE)
Session = sessionmaker(bind=ENGINE)

# Real temp paths contain "Temp"; keep the watcher's own ignore list narrow so
# the test exercises the files it creates.
IGNORED = {".git", "__pycache__", "node_modules", ".venv", "build", "dist"}

results = {"started_at": datetime.now().isoformat(), "checks": {}, "skipped": {}}


def check(name: str, condition: bool, detail=None):
    results["checks"][name] = {"ok": bool(condition), "detail": detail}
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" - {detail}" if detail and not condition else ""))
    return condition


def rows():
    session = Session()
    try:
        return [
            {
                "event_type": r.event_type,
                "file_path": os.path.normcase(os.path.abspath(r.file_path)),
                "old_path": os.path.normcase(os.path.abspath(r.old_path)) if r.old_path else None,
                "workspace": r.workspace,
            }
            for r in session.query(FileChangeEvent).order_by(FileChangeEvent.id).all()
        ]
    finally:
        session.close()


def count():
    session = Session()
    try:
        return session.query(FileChangeEvent).count()
    finally:
        session.close()


def wait_for(predicate, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def make_watcher(roots, debounce=0.35):
    # Prevent this validation run from mutating classifier rules outside the
    # temporary APPDATA (defensive; APPDATA is already isolated).
    return FileSystemWatcher(
        roots=roots,
        debounce_seconds=debounce,
        startup_grace=0.0,
        ignored_dirs=IGNORED,
        session_factory=Session,
    )


def path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def run():
    root_a = ROOT / "editor-project"
    root_b = ROOT / "terminal-project"
    root_a.mkdir()
    root_b.mkdir()

    watcher = make_watcher([
        {"path": str(root_a), "workspace": "development"},
        {"path": str(root_b), "workspace": "company"},
    ])
    check("watcher_start", watcher.start() is True, watcher.get_status().get("last_error"))
    check("watchdog_available", fs_watcher.WATCHDOG_AVAILABLE)

    # 1. Editor-like create + modify (UTF-8, realistic source file).
    editor_file = root_a / "app.py"
    editor_file.write_text("# café\r\nprint('hello')\r\n", encoding="utf-8")
    check("editor_create_recorded", wait_for(lambda: any(
        r["file_path"] == path_key(editor_file) for r in rows()
    )), rows())
    time.sleep(0.5)
    editor_file.write_text("# café\r\nprint('changed')\r\n", encoding="utf-8")
    check("editor_modify_recorded", wait_for(lambda: any(
        r["file_path"] == path_key(editor_file) and r["event_type"] == "modified"
        for r in rows()
    )), rows())

    # 2. Terminal / PowerShell write.
    terminal_file = root_b / "terminal.txt"
    subprocess.run([
        "powershell", "-NoProfile", "-Command",
        f"Set-Content -LiteralPath '{terminal_file}' -Value 'written by terminal' -Encoding UTF8",
    ], check=True, capture_output=True, timeout=30)
    check("terminal_write_recorded", wait_for(lambda: any(
        r["file_path"] == path_key(terminal_file) for r in rows()
    )), rows())

    # 3. Background process create then delete.
    background_file = root_b / "background.js"
    subprocess.Popen([
        "powershell", "-NoProfile", "-Command",
        "Start-Sleep -Milliseconds 400; "
        f"Set-Content -LiteralPath '{background_file}' -Value 'background' -Encoding UTF8; "
        "Start-Sleep -Milliseconds 600; "
        f"Remove-Item -LiteralPath '{background_file}' -Force",
    ])
    check("background_process_recorded", wait_for(lambda: any(
        r["file_path"] == path_key(background_file) for r in rows()
    ), timeout=12), rows())

    # 4. Rename and delete.
    source = root_a / "old_name.py"
    destination = root_a / "new_name.py"
    source.write_text("x = 1\n", encoding="utf-8")
    time.sleep(0.6)
    os.replace(source, destination)
    check("rename_recorded", wait_for(lambda: any(
        r["event_type"] == "moved" and r["file_path"] == path_key(destination)
        and r["old_path"] == path_key(source) for r in rows()
    )), rows())

    time.sleep(0.5)
    destination.unlink()
    check("delete_recorded", wait_for(lambda: any(
        r["event_type"] == "deleted" and r["file_path"] == path_key(destination)
        for r in rows()
    )), rows())

    # 5. Duplicate suppression: two writes inside one debounce window produce
    #    one coalesced row, not one row per write.
    dedupe_file = root_a / "dedupe.py"
    dedupe_file.write_text("v1\n", encoding="utf-8")
    wait_for(lambda: any(r["file_path"] == path_key(dedupe_file) for r in rows()))
    time.sleep(0.6)
    dedupe_file.write_text("v2\n", encoding="utf-8")
    dedupe_file.write_text("v3\n", encoding="utf-8")
    time.sleep(1.0)
    dedupe_rows = [r for r in rows()
                   if r["file_path"] == path_key(dedupe_file)
                   and r["event_type"] == "modified"]
    # watchdog/Windows may coalesce the two writes into zero or one modified
    # notification; more than one would mean our debounce/dedupe failed.
    check("duplicate_suppression", len(dedupe_rows) <= 1, dedupe_rows)

    # 6. Burst: create 60 files in one directory.
    burst = root_a / "burst"
    burst.mkdir()
    before = count()
    for index in range(60):
        (burst / f"file_{index:03d}.py").write_text(f"VALUE = {index}\n", encoding="utf-8")
    check("burst_recorded", wait_for(lambda: count() - before >= 30, timeout=15),
          {"before": before, "after": count(), "expected_min": 30})

    # 7. Restart: stop, make an offline edit (should not be observed), restart,
    #    then make a new edit (must be observed).
    watcher.stop()
    offline = root_a / "offline.py"
    offline.write_text("offline\n", encoding="utf-8")
    check("restart_started", watcher.start() is True, watcher.get_status().get("last_error"))
    online = root_a / "online.py"
    online.write_text("online\n", encoding="utf-8")
    check("restart_records_new_events", wait_for(lambda: any(
        r["file_path"] == path_key(online) for r in rows()
    )), rows())
    check("offline_change_not_claimed", not any(
        r["file_path"] == path_key(offline) for r in rows()
    ), rows())

    # Repeated stop/start must be safe; this is also the regression test for a
    # CPython 3.14 watchdog shutdown crash observed after a root disappeared.
    restart_results = [watcher.restart() for _ in range(10)]
    check("repeated_restart_safe", all(restart_results), restart_results)

    # 8. Deleted root: remove one root while running.  The watcher may keep its
    #    observer alive, but status must report it inaccessible and a restart
    #    of a watcher whose only root is gone must fail safely.
    deleted_root = ROOT / "deleted-root"
    deleted_root.mkdir()
    deleted_watcher = make_watcher([{"path": str(deleted_root), "workspace": "development"}])
    check("deleted_root_watcher_started", deleted_watcher.start() is True)
    shutil.rmtree(deleted_root)
    roots_status = deleted_watcher.get_roots()
    check("deleted_root_reported_missing", roots_status and roots_status[0]["exists"] is False,
          roots_status)
    check("deleted_root_restart_fails_safely", deleted_watcher.restart() is False,
          deleted_watcher.get_status().get("last_error"))
    deleted_watcher.stop()

    # 9. Read-only file: watcher must not crash when the filesystem rejects a
    #    write made by the validation script (the event may or may not appear).
    readonly_file = root_a / "readonly.py"
    readonly_file.write_text("readonly\n", encoding="utf-8")
    os.chmod(readonly_file, stat.S_IREAD)
    try:
        try:
            readonly_file.write_text("blocked\n", encoding="utf-8")
            readonly_behavior = "write unexpectedly succeeded"
        except PermissionError:
            readonly_behavior = "write denied as expected"
        check("watcher_survives_readonly", watcher.get_status()["running"] is True,
              readonly_behavior)
    finally:
        os.chmod(readonly_file, stat.S_IREAD | stat.S_IWRITE)

    watcher.stop()
    status = watcher.get_status()
    check("watcher_stopped", status["running"] is False)
    check("no_write_errors", status["write_errors"] == 0, status["recent_errors"])

    results["status"] = status
    results["row_count"] = count()
    results["duplicate_rows"] = _exact_duplicate_count()
    check("no_exact_duplicate_rows", results["duplicate_rows"] == 0, results["duplicate_rows"])

    results["skipped"]["sleep_resume"] = (
        "Cannot reliably suspend/resume this Windows session from user mode; "
        "requires a manual power-cycle test."
    )
    results["skipped"]["kernel_queue_overflow"] = (
        "Cannot force watchdog's OS event queue to overflow deterministically; "
        "would require an external stress tool or manual debugger."
    )


def _exact_duplicate_count():
    session = Session()
    try:
        from sqlalchemy import func
        duplicates = (
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
        return len(duplicates)
    finally:
        session.close()


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        results["fatal_error"] = f"{type(exc).__name__}: {exc}"
        check("no_fatal_error", False, results["fatal_error"])
    finally:
        try:
            ENGINE.dispose()
        except Exception:
            pass

    results["finished_at"] = datetime.now().isoformat()
    results["success"] = (
        "fatal_error" not in results
        and all(item["ok"] for item in results["checks"].values())
    )
    out_dir = Path("test-artifacts")
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"watcher-validation-{datetime.now():%Y%m%d-%H%M%S}.json"
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults: {out_file}")
    print(json.dumps({k: v["ok"] for k, v in results["checks"].items()}, indent=2))
    print(f"SUCCESS={results['success']}")
    # Keep the temp tree only on failure so failures can be inspected.
    if results["success"]:
        shutil.rmtree(_TEST_ROOT, ignore_errors=True)
    sys.exit(0 if results["success"] else 1)
