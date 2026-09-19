# -*- coding: utf-8 -*-
"""Regression tests for Windows-only fsync bug in backup.py.

The bug: `_write_zip_atomic` opened the freshly written zip with "rb" and then
called os.fsync() on that read-only handle.  On Windows this raises
OSError: [Errno 9] Bad file descriptor, so every automatic full backup failed
(and startup maintenance chain was skipped).  Fixed by opening with "r+b".
"""

import os
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mathbank.backup import create_full_backup, verify_full_backup  # noqa: E402


def _create_minimal_database(path: Path) -> None:
    """Build a minimal valid MathBank schema snapshot accepted by backup."""
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE questions (
                id INTEGER PRIMARY KEY,
                content TEXT NOT NULL,
                question_type VARCHAR(50),
                category_compulsory VARCHAR(100) DEFAULT '',
                category_chapter VARCHAR(100) DEFAULT '',
                category_knowledge VARCHAR(100) DEFAULT '',
                difficulty VARCHAR(50) DEFAULT 'medium',
                source VARCHAR(200) DEFAULT '',
                answer_markdown TEXT DEFAULT '',
                image_paths TEXT DEFAULT '[]',
                created_at DATETIME
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE question_curriculums (
                id INTEGER PRIMARY KEY,
                question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                version_code VARCHAR(50) NOT NULL,
                compulsory VARCHAR(100) DEFAULT '',
                chapter VARCHAR(100) DEFAULT '',
                knowledge VARCHAR(100) DEFAULT ''
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE papers (
                id INTEGER PRIMARY KEY,
                title VARCHAR(200) NOT NULL,
                subtitle VARCHAR(200) DEFAULT '',
                paper_type VARCHAR(50) DEFAULT 'exam',
                total_score INTEGER DEFAULT 150,
                metadata_json TEXT DEFAULT '{}',
                created_at DATETIME
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE paper_questions (
                id INTEGER PRIMARY KEY,
                paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                order_index INTEGER DEFAULT 0,
                score INTEGER DEFAULT 5
            )
            """
        )
        connection.execute("PRAGMA user_version = 0")
        connection.commit()
    finally:
        connection.close()


def test_write_zip_atomic_survives_windows_fsync(tmp_path: Path) -> None:
    """create_full_backup must not raise EBADF on Windows read-only fsync."""
    database = tmp_path / "math_question_bank.db"
    _create_minimal_database(database)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / ".gitkeep").write_text("", encoding="utf-8")

    backup_path = create_full_backup(
        output_dir=tmp_path / "snapshots",
        database_path=database,
        uploads_dir=uploads,
        metadata_path=tmp_path / "no-metadata.json",
        retention=None,
    )
    assert backup_path.is_file()
    with zipfile.ZipFile(backup_path) as archive:
        assert "manifest.json" in archive.namelist()
    # Verify the produced archive is fully valid.
    manifest = verify_full_backup(backup_path)
    assert manifest["format"] == "mathbank-full-backup"
    assert manifest["database"]["schema_version"] == 0
    assert manifest["upload_file_count"] == 0


def test_fsync_on_readwrite_handle_does_not_raise(tmp_path: Path) -> None:
    """Guard: os.fsync on an r+b handle must not raise on any platform."""
    from mathbank.backup import _write_zip_atomic

    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "hello.txt").write_text("hello", encoding="utf-8")
    output = tmp_path / "out.zip"
    _write_zip_atomic(payload, "manifest.json", {"a": 1}, output)
    assert output.is_file()
    assert output.stat().st_size > 0
