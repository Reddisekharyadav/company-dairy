"""Clean-install smoke test for the packaged WorkSense executable.

    .\\venv\\Scripts\\python.exe scripts\\test_packaged.py

Uses a temporary APPDATA and database, launches ``dist/WorkSense.exe`` in
headless mode, waits for the dashboard, verifies the watcher, generates all
report formats, saves settings, terminates the process, restarts it and checks
that the existing database/config were detected.  Results are written to
``test-artifacts``.

The GUI tray/consent dialog and a true missing-DLL scenario are reported as
manual/not covered rather than inferred.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXE = _PROJECT_ROOT / "dist" / "WorkSense.exe"
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="worksense-packaged-"))
APPDATA = _TEST_ROOT / "AppData"
WORKSPACE = _TEST_ROOT / "project"
DB_PATH = _TEST_ROOT / "events.db"
EXPORT_DIR = _TEST_ROOT / "exports"
APPDATA.mkdir(parents=True)
WORKSPACE.mkdir(parents=True)

results = {"checks": {}, "started_at": datetime.now().isoformat()}


def check(name, condition, detail=None):
    results["checks"][name] = {"ok": bool(condition), "detail": detail}
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    return bool(condition)


def base_env():
    env = os.environ.copy()
    env.update({
        "APPDATA": str(APPDATA),
        "WS_DB_PATH": str(DB_PATH),
        "WS_EXPORT": str(EXPORT_DIR),
        "WS_PROJECT_ROOTS": str(WORKSPACE),
        "WS_HEADLESS": "1",
        "WS_NO_BROWSER": "1",
        "WS_WATCHER": "1",
        "WS_WATCHER_DEBOUNCE": "0.2",
        "WS_WATCHER_GRACE": "0.0",
        "WS_INTERVAL": "1.0",
    })
    # Do not inherit real mail/AI credentials into the packaged smoke test.
    for key in ("WS_EMAIL_TO", "WS_EMAIL_USER", "WS_EMAIL_PASS", "WS_EMAIL_FROM",
                "WS_AI_API_KEY"):
        env.pop(key, None)
    return env


def kill_tree(proc: subprocess.Popen):
    import psutil
    try:
        parent = psutil.Process(proc.pid)
        for child in parent.children(recursive=True):
            try:
                child.terminate()
            except Exception:
                pass
        parent.terminate()
        try:
            parent.wait(timeout=10)
        except Exception:
            parent.kill()
        for child in parent.children(recursive=True):
            try:
                child.kill()
            except Exception:
                pass
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


def log_text():
    log = APPDATA / "WorkSense" / "worksense.log"
    if not log.exists():
        return ""
    return log.read_text(encoding="utf-8", errors="replace")


def wait_port(proc, timeout=75):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return None
        text = log_text()
        matches = re.findall(r"Starting Web Dashboard server on http://127\.0\.0\.1:(\d+)", text)
        if matches:
            port = int(matches[-1])
            try:
                request("/api/consent/status", port=port, timeout=1.5)
                return port
            except Exception:
                pass
        time.sleep(0.25)
    return None


def request(path, *, port, method="GET", payload=None, timeout=30):
    url = f"http://127.0.0.1:{port}{path}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read()
        return response.status, response.headers, body


def wait_until(predicate, timeout=20, interval=0.25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def start(env):
    return subprocess.Popen(
        [str(EXE)], cwd=str(WORKSPACE), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def columns(table):
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


def main():
    if not EXE.is_file():
        print(f"Missing packaged executable: {EXE}")
        return 2

    # Archive content check: no bundled .env or credential file.
    try:
        from PyInstaller.archive.readers import CArchiveReader
        archive = CArchiveReader(str(EXE))
        entries = list(archive.toc.keys())
        check("no_env_bundled", not any(
            name.lower().endswith(".env") or name.lower() in (".env", "env")
            for name in entries
        ), entries[-5:])
        check("archive_has_main", "main" in entries)
    except Exception as exc:
        check("archive_inspection", False, f"{type(exc).__name__}: {exc}")

    check("first_launch_starts_without_database", not DB_PATH.exists())
    env = base_env()
    proc = start(env)
    port = wait_port(proc)
    if not check("packaged_server_started", port is not None,
                 "see %APPDATA%/WorkSense/worksense.log"):
        kill_tree(proc)
        return 1

    try:
        status, _headers, body = request("/api/consent/status", port=port)
        check("api_responds", status == 200 and b"screen_capture_enabled" in body)

        status, _headers, body = request("/api/settings", port=port)
        settings = json.loads(body)
        check("settings_created", status == 200 and settings.get("retention_days") == 30,
              settings.get("retention_days"))

        check("database_created", DB_PATH.exists() and DB_PATH.stat().st_size > 0)
        check("migration_columns_present",
              {"workspace", "project_root", "relative_path"}.issubset(columns("file_change_events"))
              and "branch" in columns("git"))

        # Watcher startup and real packaged-process event recording.
        event_file = WORKSPACE / "packaged_event.py"
        event_file.write_text("VALUE = 1\n", encoding="utf-8")
        check("packaged_watcher_records_event", wait_until(lambda: (
            json.loads(request("/api/file_changes?workspace=development&period=daily",
                               port=port)[2]).get("changes")
        ), timeout=25))

        # Report generation through the packaged backend.
        status, _headers, body = request(
            "/api/generate_report?period=daily&workspace=company&formats=md,pdf,docx",
            port=port, timeout=120,
        )
        report = json.loads(body)
        report_paths = [report.get("md"), report.get("pdf"), report.get("docx")]
        check("reports_generated", status == 200 and all(
            p and Path(p).is_file() and Path(p).stat().st_size > 500 for p in report_paths
        ), report_paths)

        status, headers, body = request("/api/export/excel?workspace=company", port=port, timeout=120)
        xlsx = _TEST_ROOT / "packaged.xlsx"
        xlsx.write_bytes(body)
        check("excel_exported", status == 200 and xlsx.read_bytes()[:2] == b"PK"
              and xlsx.stat().st_size > 1000, xlsx.stat().st_size)

        status, _headers, body = request(
            "/api/settings", port=port, method="POST",
            payload={"retention_days": 45, "project_roots": [str(WORKSPACE)]},
        )
        check("settings_saved", status == 200 and json.loads(body).get("status") == "saved")
        config_file = APPDATA / "WorkSense" / "config.json"
        config = json.loads(config_file.read_text(encoding="utf-8"))
        check("config_persisted", config.get("retention_days") == 45,
              config.get("retention_days"))

        rows_before = json.loads(request(
            "/api/file_changes?workspace=development&period=daily", port=port
        )[2]).get("changes", [])
        results["rows_before_restart"] = len(rows_before)
    finally:
        kill_tree(proc)

    time.sleep(2.0)
    check("shutdown_process_stopped", proc.poll() is not None, proc.poll())

    # Second launch with the existing database and config.
    proc2 = start(env)
    port2 = wait_port(proc2)
    if not check("restart_server_started", port2 is not None):
        kill_tree(proc2)
        return 1
    try:
        settings2 = json.loads(request("/api/settings", port=port2)[2])
        check("existing_config_detected", settings2.get("retention_days") == 45,
              settings2.get("retention_days"))
        rows_after = json.loads(request(
            "/api/file_changes?workspace=development&period=daily", port=port2
        )[2]).get("changes", [])
        check("existing_database_preserved", len(rows_after) >= results.get("rows_before_restart", 0),
              {"before": results.get("rows_before_restart"), "after": len(rows_after)})
        check("second_launch_no_pending_migration",
              not (APPDATA / "WorkSense" / "backups").exists()
              or len(list((APPDATA / "WorkSense" / "backups").glob("events-backup-*.db"))) == 0,
              "fresh databases need no upgrade backup")
    finally:
        kill_tree(proc2)

    results["not_covered"] = {
        "gui_first_launch_consent_dialog": "headless smoke test; use manual GUI launch for dialog/tray",
        "missing_dependency_repair": "cannot remove a DLL from a onefile build without modifying it",
        "installer_signing": "no code-signing certificate configured in this repository",
    }
    results["finished_at"] = datetime.now().isoformat()
    results["success"] = all(item["ok"] for item in results["checks"].values())

    out_dir = _PROJECT_ROOT / "test-artifacts"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"packaged-{datetime.now():%Y%m%d-%H%M%S}.json"
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults: {out_file}")
    print(f"SUCCESS={results['success']}")
    if results["success"]:
        shutil.rmtree(_TEST_ROOT, ignore_errors=True)
    return 0 if results["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
