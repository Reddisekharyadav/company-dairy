"""Safe SQLite backup and restore helpers.

A backup is taken with SQLite's online backup API (never a plain file copy), so
WAL contents are included and the copy is transactionally consistent.  The
application calls :func:`create_backup` before any schema upgrade.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy.engine import Engine

log = logging.getLogger("backup")

DEFAULT_KEEP = 10


def database_path(engine: Engine) -> Optional[Path]:
    """Return the filesystem path for a file-backed SQLite engine."""
    try:
        if engine.url.get_backend_name() != "sqlite":
            return None
        database = engine.url.database
        if not database or database == ":memory:":
            return None
        return Path(database).expanduser().resolve()
    except Exception:
        return None


def backup_directory() -> Path:
    override = os.environ.get("WS_BACKUP_DIR")
    if override:
        folder = Path(override).expanduser()
    else:
        base = Path(os.environ.get("APPDATA") or Path.home())
        folder = base / "WorkSense" / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity_ok(path: Path) -> bool:
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return bool(row and str(row[0]).lower() == "ok")
    finally:
        conn.close()


def _prune(folder: Path, keep: int) -> None:
    keep = max(1, int(keep))
    backups = sorted(folder.glob("events-backup-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[keep:]:
        try:
            old.unlink()
            manifest = old.with_suffix(old.suffix + ".sha256")
            if manifest.exists():
                manifest.unlink()
        except OSError as exc:
            log.warning("Could not prune old backup %s: %s", old, exc)


def create_backup(
    engine_or_path,
    *,
    reason: str = "manual",
    backup_dir: Optional[Path | str] = None,
    keep: int = DEFAULT_KEEP,
) -> Optional[Path]:
    """Create a consistent backup and verify its integrity.

    Returns ``None`` when the source database does not exist or is empty.  A
    failed integrity check raises so a caller never proceeds with an unsafe
    schema upgrade.
    """
    if isinstance(engine_or_path, Engine):
        source = database_path(engine_or_path)
    else:
        source = Path(str(engine_or_path)).expanduser().resolve()

    if source is None or not source.exists() or source.stat().st_size == 0:
        return None

    folder = Path(backup_dir).expanduser() if backup_dir else backup_directory()
    folder.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_reason = "".join(ch for ch in reason if ch.isalnum() or ch in "-_") or "backup"
    destination = folder / f"events-backup-{stamp}-{safe_reason}-{uuid.uuid4().hex[:6]}.db"

    source_conn = sqlite3.connect(str(source), timeout=30)
    dest_conn = sqlite3.connect(str(destination), timeout=30)
    try:
        source_conn.backup(dest_conn)
        dest_conn.commit()
    finally:
        dest_conn.close()
        source_conn.close()

    if not _integrity_ok(destination):
        try:
            destination.unlink()
        except OSError:
            pass
        raise RuntimeError("Database backup failed its integrity check")

    digest = _sha256(destination)
    destination.with_suffix(destination.suffix + ".sha256").write_text(
        f"{digest}  {destination.name}\n", encoding="ascii"
    )
    try:
        os.chmod(destination, 0o600)
    except OSError:
        pass

    _prune(folder, keep)
    log.info("Database backup created: %s", destination)
    return destination


def restore_backup(
    backup_path: Path | str,
    target_path: Path | str,
    *,
    safety_backup: bool = True,
    backup_dir: Optional[Path | str] = None,
) -> Path:
    """Restore a verified backup into ``target_path``.

    The caller must dispose/close any SQLAlchemy engine connected to the target
    before restoring.  A safety backup of the current target is taken first.
    """
    source = Path(backup_path).expanduser().resolve()
    target = Path(target_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    if not _integrity_ok(source):
        raise RuntimeError("Backup failed SQLite integrity check")

    if target.exists() and target.stat().st_size and safety_backup:
        create_backup(target, reason="pre-restore", backup_dir=backup_dir)

    # Stale WAL/SHM files from a disposed connection must not replay old pages
    # over the restored database.
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(target) + suffix)
        try:
            if sidecar.exists():
                sidecar.unlink()
        except OSError:
            pass

    target.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(str(source), timeout=30)
    target_conn = sqlite3.connect(str(target), timeout=30)
    try:
        source_conn.backup(target_conn)
        target_conn.commit()
    finally:
        target_conn.close()
        source_conn.close()

    if not _integrity_ok(target):
        raise RuntimeError("Restored database failed SQLite integrity check")
    log.info("Database restored from %s to %s", source, target)
    return target


def list_backups(backup_dir: Optional[Path | str] = None) -> Iterable[Path]:
    folder = Path(backup_dir).expanduser() if backup_dir else backup_directory()
    if not folder.exists():
        return []
    return sorted(folder.glob("events-backup-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
