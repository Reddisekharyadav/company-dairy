from __future__ import annotations
from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Text,
    Float,
    Boolean,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Application(Base):
    __tablename__ = "applications"
    id = Column(Integer, primary_key=True)
    name = Column(String(256), nullable=False)
    process_name = Column(String(256))
    created_at = Column(DateTime, default=datetime.now)


class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.now, index=True)
    duration = Column(Float, default=0.0)
    application = Column(String(256))
    window_title = Column(String(1024))
    process_name = Column(String(256))
    project = Column(String(512), nullable=True)
    opened_file = Column(String(1024), nullable=True)
    language = Column(String(64), nullable=True)
    cpu = Column(Float, nullable=True)
    idle = Column(Boolean, default=False)
    extra = Column(Text, nullable=True)
    category = Column(String(64), nullable=True)
    website = Column(String(256), nullable=True)


class GitActivity(Base):
    __tablename__ = "git"
    id = Column(Integer, primary_key=True)
    repo = Column(String(1024))
    commit_hash = Column(String(64))
    message = Column(Text)
    author = Column(String(256))
    timestamp = Column(DateTime, default=datetime.now)


class OCRText(Base):
    __tablename__ = "ocr"
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.now)
    source = Column(String(256))
    text = Column(Text)
    screenshot_path = Column(String(2048), nullable=True)


class Report(Base):
    __tablename__ = "reports"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.now)
    period_start = Column(DateTime)
    period_end = Column(DateTime)
    format = Column(String(16))
    path = Column(String(2048))
    summary = Column(Text)


# ── File Edit Journal (Solo Developer + Company Worker) ───────────────────────

class FileEdit(Base):
    """Tracks which files were edited and for how long."""
    __tablename__ = "file_edits"
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.now, index=True)
    file_path = Column(String(2048), nullable=False)
    file_name = Column(String(512))
    project = Column(String(512), nullable=True)
    language = Column(String(64), nullable=True)
    duration_sec = Column(Float, default=0.0)
    event_type = Column(String(32), default='modified')
    session_date = Column(String(20))
    editor = Column(String(128), nullable=True)      # "VS Code", "PyCharm", "Cursor"


# ── Search Query Tracker (Researcher Mode) ────────────────────────────────────

class SearchQuery(Base):
    """Stores search queries extracted from browser window titles."""
    __tablename__ = "search_queries"
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.now, index=True)
    query = Column(Text, nullable=False)
    source = Column(String(128))
    raw_title = Column(String(1024), nullable=True)
    research_session_id = Column(Integer, nullable=True)
    bookmarked = Column(Boolean, default=False)


class ResearchSession(Base):
    """Groups related search queries into a research topic cluster."""
    __tablename__ = "research_sessions"
    id = Column(Integer, primary_key=True)
    start_time = Column(DateTime, default=datetime.now, index=True)
    end_time = Column(DateTime, nullable=True)
    topic_label = Column(String(256), nullable=True)
    query_count = Column(Integer, default=0)
    total_duration_sec = Column(Float, default=0.0)
    sources = Column(Text, nullable=True)
    date = Column(String(20))


# ── Session Snapshot (Cross-Session Memory + AI Handoff) ──────────────────────

class SessionSnapshot(Base):
    """
    Saved at end of each tracking session. Loaded on next startup to power
    the Session Handoff Card and AI context export.
    """
    __tablename__ = "session_snapshots"
    id = Column(Integer, primary_key=True)
    snapshot_date = Column(String(20), index=True)
    created_at = Column(DateTime, default=datetime.now)
    files_json = Column(Text, nullable=True)
    top_project = Column(String(512), nullable=True)
    top_language = Column(String(64), nullable=True)
    research_topics_json = Column(Text, nullable=True)
    search_count = Column(Integer, default=0)
    last_commit_hash = Column(String(64), nullable=True)
    last_commit_message = Column(Text, nullable=True)
    repos_touched_json = Column(Text, nullable=True)
    total_active_sec = Column(Float, default=0.0)
    total_idle_sec = Column(Float, default=0.0)
    categories_json = Column(Text, nullable=True)
    summary_text = Column(Text, nullable=True)
    ai_context_md = Column(Text, nullable=True)


# ── Browser History (Real URLs from Chrome/Edge/Firefox) ──────────────────────

class BrowserHistory(Base):
    """Stores actual browsing history read from Chrome/Edge/Firefox databases."""
    __tablename__ = "browser_history"
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.now, index=True)
    url = Column(String(2048), nullable=True)
    title = Column(String(1024), nullable=True)
    site_name = Column(String(256), nullable=True)
    domain = Column(String(256), nullable=True)
    visit_count = Column(Integer, default=1)
    duration_sec = Column(Float, default=0.0)
    browser = Column(String(64), nullable=True)       # "Chrome", "Edge", "Firefox"
    category = Column(String(64), nullable=True)
    session_date = Column(String(20))
