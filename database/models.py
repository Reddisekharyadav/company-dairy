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
    # NEW: category and website extracted from window title
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
    # NEW: path to saved screenshot thumbnail
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
