"""
Integration tests for v4.0 Microsoft Recall-inspired features.

These tests are pytest functions, not a standalone demo script: every check
uses assertions, and SQLite files are created under pytest's temporary
directory so the production database is never touched.
"""
from __future__ import annotations

import sqlite3
import sys

from PIL import Image


def test_dedup():
    """Perceptual-hash deduplication."""
    from ocr.dedup import FrameDeduplicator, compute_dhash, hamming_distance

    img1 = Image.new("RGB", (100, 100), color=(255, 0, 0))
    img2 = Image.new("RGB", (100, 100), color=(255, 0, 0))
    img3 = Image.new("RGB", (100, 100), color=(0, 0, 255))

    hash1 = compute_dhash(img1)
    hash2 = compute_dhash(img2)
    hash3 = compute_dhash(img3)

    assert hamming_distance(hash1, hash2) == 0
    assert hamming_distance(hash1, hash3) > 0

    dedup = FrameDeduplicator(max_history=5)
    assert not dedup.is_duplicate(img1)
    dedup.add_frame(img1)
    assert dedup.is_duplicate(img2, threshold=5)
    assert not dedup.is_duplicate(img3, threshold=2)

    dedup.reset()
    assert not dedup.is_duplicate(img1)


def test_privacy_rules():
    """Sensitive application/title exclusions and text redaction."""
    from config.privacy_rules import PrivacyRules, scrub_sensitive_text

    rules = PrivacyRules()
    assert not rules.should_capture("KeePass.exe", "Master Password")
    assert not rules.should_capture("chrome.exe", "My Bank - Login")
    assert not rules.should_capture("edge.exe", "InPrivate Browsing")
    assert rules.should_capture("code.exe", "main.py - VS Code")
    assert rules.should_capture("chrome.exe", "GitHub - Dashboard")

    scrubbed = scrub_sensitive_text(
        "My card is 4111-1111-1111-1111 and SSN is 123-45-6789"
    )
    assert "[REDACTED]" in scrubbed
    assert "4111" not in scrubbed
    assert "123-45-6789" not in scrubbed

    scrubbed = scrub_sensitive_text("Contact me at user@example.com please")
    assert "user@example.com" not in scrubbed


def test_embeddings(monkeypatch):
    """Embedding engine fallback used when optional ML packages are absent."""
    # The packaged executable intentionally excludes torch/transformers.  Force
    # the documented fallback path so this test is offline and deterministic.
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    from ocr.embeddings import EmbeddingEngine

    engine = EmbeddingEngine()
    vec = engine.encode("Hello world, this is a test of semantic search")
    assert vec is not None
    assert len(vec) == 384
    assert engine._using_fallback is True

    vec2 = engine.encode("Testing another sentence for comparison")
    assert vec2 is not None

    self_sim = engine.cosine_similarity(vec, vec)
    cross_sim = engine.cosine_similarity(vec, vec2)
    assert 0.99 <= self_sim <= 1.01
    assert 0.0 <= cross_sim <= 1.0


def test_database_models():
    """ScreenFrame contains the columns used by OCR/search."""
    from database.models import ScreenFrame

    columns = {column.name for column in ScreenFrame.__table__.columns}
    required = {
        "id",
        "timestamp",
        "session_id",
        "screenshot_path",
        "phash",
        "ocr_text",
        "embedding_json",
        "privacy_scrubbed",
        "detected_category",
    }
    assert required <= columns


def test_fts5(tmp_path):
    """SQLite in this build supports FTS5."""
    db_path = tmp_path / "fts5.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE VIRTUAL TABLE test_fts USING fts5(text, source)"
        )
        conn.execute(
            "INSERT INTO test_fts VALUES ('hello world python programming', 'test')"
        )
        conn.execute(
            "INSERT INTO test_fts VALUES "
            "('machine learning artificial intelligence', 'test')"
        )
        assert len(
            conn.execute(
                "SELECT * FROM test_fts WHERE test_fts MATCH 'python'"
            ).fetchall()
        ) == 1
        assert len(
            conn.execute(
                "SELECT * FROM test_fts WHERE test_fts MATCH 'learning'"
            ).fetchall()
        ) == 1


def test_search_engine(tmp_path):
    """Standalone hybrid search over a caller-supplied SQLite database."""
    from ocr.search_engine import HybridSearchEngine

    db_path = tmp_path / "search.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE screen_frames (
                id INTEGER PRIMARY KEY,
                timestamp TEXT,
                ocr_text TEXT,
                window_title TEXT,
                analysis_summary TEXT,
                process_name TEXT,
                screenshot_path TEXT,
                detected_category TEXT,
                embedding_json TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO screen_frames VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    1,
                    "2024-01-01T10:00:00",
                    "Python code for machine learning model",
                    "main.py - VS Code",
                    "Coding in Python",
                    "code.exe",
                    "/screenshots/1.webp",
                    "coding",
                    None,
                ),
                (
                    2,
                    "2024-01-01T10:30:00",
                    "Browsing GitHub repository for open source project",
                    "GitHub - Dashboard",
                    "Browsing GitHub",
                    "chrome.exe",
                    "/screenshots/2.webp",
                    "browsing",
                    None,
                ),
            ],
        )

    engine = HybridSearchEngine(db_path=str(db_path))
    engine.setup_fts5()
    engine.index_frame(
        1,
        "Python code for machine learning model",
        "main.py - VS Code",
        "Coding in Python",
    )
    engine.index_frame(
        2,
        "Browsing GitHub repository for open source project",
        "GitHub - Dashboard",
        "Browsing GitHub",
    )

    assert engine.search_keyword("Python", limit=10)
    assert engine.search_keyword("GitHub", limit=10)
    assert isinstance(engine.search_hybrid("machine learning", limit=10), list)
