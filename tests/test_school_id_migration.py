# -*- coding: utf-8 -*-
"""v25 学校 ID 化迁移回归：两列+索引产出、幂等自愈、旧库升级路径。

独立文件放新用例，避免与并行会话在 test_database_migrations.py 上撞车。
"""
import sqlite3
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine

from mathbank import db_migrations


@contextmanager
def _sqlite_connection(path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def _dispose_created_engines(monkeypatch):
    created = []
    original = create_engine

    def tracked(*args, **kwargs):
        engine = original(*args, **kwargs)
        created.append(engine)
        return engine

    monkeypatch.setattr("test_school_id_migration.create_engine", tracked)
    yield
    for engine in created:
        engine.dispose()


def _create_v24_database(path):
    """按 v24 形态建最小库：questions（无 source_school_id）+ question_sources（无 school_id）。"""
    with _sqlite_connection(path) as connection:
        connection.executescript(
            """
            CREATE TABLE questions (id INTEGER PRIMARY KEY, content TEXT NOT NULL, source VARCHAR(200) DEFAULT '');
            CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
            CREATE TABLE question_curriculums (id INTEGER PRIMARY KEY, question_id INTEGER, version_code VARCHAR(10), compulsory VARCHAR(50) DEFAULT '', chapter VARCHAR(100) DEFAULT '', knowledge VARCHAR(100) DEFAULT '');
            CREATE TABLE paper_questions (id INTEGER PRIMARY KEY, paper_id INTEGER, question_id INTEGER);
            CREATE TABLE question_sources (
                id INTEGER NOT NULL PRIMARY KEY,
                question_id INTEGER NOT NULL,
                year VARCHAR(20) NOT NULL DEFAULT '',
                region VARCHAR(100) NOT NULL DEFAULT '',
                exam_type VARCHAR(50) NOT NULL DEFAULT '',
                paper_title VARCHAR(200) NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                province VARCHAR(50) NOT NULL DEFAULT '',
                city VARCHAR(50) NOT NULL DEFAULT '',
                grade VARCHAR(10) NOT NULL DEFAULT '',
                semester VARCHAR(10) NOT NULL DEFAULT ''
            );
            INSERT INTO questions (id, content, source) VALUES (1, 't', '2023年哈师大附中10月月考');
            INSERT INTO question_sources (question_id, year, region) VALUES (1, '2023', '哈师大附中');
            PRAGMA user_version=24;
            """
        )


def test_v24_to_v25_adds_school_id_columns_and_index(tmp_path, monkeypatch):
    database_path = tmp_path / "v24.db"
    _create_v24_database(database_path)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snapshots")
    engine = create_engine(f"sqlite:///{database_path}")

    result = db_migrations.migrate_database(engine)

    assert result["from_version"] == 24
    assert result["to_version"] == db_migrations.LATEST_SCHEMA_VERSION == 26
    assert result["added_question_sources_school_id"] == 1
    assert result["added_questions_source_school_id"] == 1
    assert result["added_question_sources_school_index"] == 1
    with _sqlite_connection(database_path) as connection:
        qs_cols = {row[1] for row in connection.execute("PRAGMA table_info(question_sources)")}
        q_cols = {row[1] for row in connection.execute("PRAGMA table_info(questions)")}
        assert "school_id" in qs_cols and "source_school_id" in q_cols
        # 可空：旧行不被强制回填成假值
        assert connection.execute("SELECT school_id FROM question_sources").fetchone()[0] is None
        assert connection.execute("SELECT source_school_id FROM questions").fetchone()[0] is None
        index_names = {row[1] for row in connection.execute("PRAGMA index_list(question_sources)")}
        assert "idx_question_sources_school" in index_names
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_school_id_ensure_is_idempotent_on_latest(tmp_path):
    """已是 v25 的库再跑 ensure_late_tables 不得重复加列/报错（自愈路径）。"""
    database_path = tmp_path / "v25.db"
    _create_v24_database(database_path)
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        db_migrations._ensure_school_id_columns(connection)
        rows = connection.exec_driver_sql("SELECT COUNT(*) FROM question_sources").fetchone()[0]
        # 第二次必须零产出（列已存在）
        second = db_migrations._ensure_school_id_columns(connection)
        assert second == {}, second
        assert rows == 1
    engine.dispose()


def test_future_version_still_refused():
    """高于 LATEST 的库必须在任何建表/加列前拒绝启动（规范硬约束）。"""
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with _sqlite_connection(path) as connection:
            connection.executescript(
                """
                CREATE TABLE questions (id INTEGER PRIMARY KEY, content TEXT NOT NULL);
                CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
                CREATE TABLE question_curriculums (id INTEGER PRIMARY KEY, question_id INTEGER, version_code VARCHAR(10));
                CREATE TABLE paper_questions (id INTEGER PRIMARY KEY, paper_id INTEGER, question_id INTEGER);
                PRAGMA user_version=99;
                """
            )
        engine = create_engine(f"sqlite:///{path}")
        with pytest.raises(RuntimeError):
            db_migrations.migrate_database(engine)
        engine.dispose()
    finally:
        os.unlink(path)
