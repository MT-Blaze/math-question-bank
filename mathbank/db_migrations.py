"""Small, versioned SQLite migrations for MathBank.

The project intentionally avoids a heavyweight migration framework.  This
module keeps schema upgrades explicit, backup-first, and fail-closed.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from mathbank.paths import SCHEMA_SNAPSHOT_DIR
from mathbank.runtime_components import _sha256


LATEST_SCHEMA_VERSION = 27
LEGACY_REQUIRED_TABLES = {
    "questions",
    "question_curriculums",
    "papers",
    "paper_questions",
}
REQUIRED_TABLES = LEGACY_REQUIRED_TABLES | {
    "question_fingerprints",
    "question_topics",
    "lesson_plans",
    "tags",
    "question_tags",
    "knowledge_points",
    "question_knowledge",
    "import_batches",
    "tikz_versions",
    "lecture_settings",
    "lecture_notes",
    "lecture_drafts",
    "question_sources",
}

V6_QUESTION_FINGERPRINT_COLUMNS = {
    "question_id",
    "fingerprint_version",
    "content_revision_hash",
    "exact_hash",
    "critical_math_hash",
    "answer_hash",
    "simhash_hex",
    "token_count",
    "choice_count",
    "figure_count",
    "visible_image_hashes",
    "tikz_hashes",
    "band0",
    "band1",
    "band2",
    "band3",
    "band4",
    "band5",
    "band6",
    "band7",
    "status",
    "updated_at",
}
V7_QUESTION_FINGERPRINT_COLUMNS = set(V6_QUESTION_FINGERPRINT_COLUMNS)
QUESTION_FINGERPRINT_COLUMNS = V7_QUESTION_FINGERPRINT_COLUMNS | {
    "text_band0",
    "text_band1",
    "text_band2",
    "text_band3",
    "text_band4",
    "text_band5",
    "text_band6",
    "text_band7",
}

V6_QUESTION_FINGERPRINT_INDEXES = {
    "idx_question_fingerprints_exact": ("fingerprint_version", "exact_hash"),
    "idx_question_fingerprints_band0": ("fingerprint_version", "band0"),
    "idx_question_fingerprints_band1": ("fingerprint_version", "band1"),
    "idx_question_fingerprints_band2": ("fingerprint_version", "band2"),
    "idx_question_fingerprints_band3": ("fingerprint_version", "band3"),
    "idx_question_fingerprints_band4": ("fingerprint_version", "band4"),
    "idx_question_fingerprints_band5": ("fingerprint_version", "band5"),
    "idx_question_fingerprints_band6": ("fingerprint_version", "band6"),
    "idx_question_fingerprints_band7": ("fingerprint_version", "band7"),
}

V7_QUESTION_FINGERPRINT_INDEXES = {
    "idx_question_fingerprints_exact": ("fingerprint_version", "exact_hash"),
    "idx_question_fingerprints_band0": (
        "fingerprint_version",
        "band0",
        "token_count",
    ),
    "idx_question_fingerprints_band1": (
        "fingerprint_version",
        "band1",
        "token_count",
    ),
    "idx_question_fingerprints_band2": (
        "fingerprint_version",
        "band2",
        "token_count",
    ),
    "idx_question_fingerprints_band3": (
        "fingerprint_version",
        "band3",
        "token_count",
    ),
    "idx_question_fingerprints_band4": (
        "fingerprint_version",
        "band4",
        "token_count",
    ),
    "idx_question_fingerprints_band5": (
        "fingerprint_version",
        "band5",
        "token_count",
    ),
    "idx_question_fingerprints_band6": (
        "fingerprint_version",
        "band6",
        "token_count",
    ),
    "idx_question_fingerprints_band7": (
        "fingerprint_version",
        "band7",
        "token_count",
    ),
}

QUESTION_FINGERPRINT_INDEXES = {
    **V7_QUESTION_FINGERPRINT_INDEXES,
    "idx_question_fingerprints_text_band0": (
        "fingerprint_version",
        "text_band0",
        "token_count",
    ),
    "idx_question_fingerprints_text_band1": (
        "fingerprint_version",
        "text_band1",
        "token_count",
    ),
    "idx_question_fingerprints_text_band2": (
        "fingerprint_version",
        "text_band2",
        "token_count",
    ),
    "idx_question_fingerprints_text_band3": (
        "fingerprint_version",
        "text_band3",
        "token_count",
    ),
    "idx_question_fingerprints_text_band4": (
        "fingerprint_version",
        "text_band4",
        "token_count",
    ),
    "idx_question_fingerprints_text_band5": (
        "fingerprint_version",
        "text_band5",
        "token_count",
    ),
    "idx_question_fingerprints_text_band6": (
        "fingerprint_version",
        "text_band6",
        "token_count",
    ),
    "idx_question_fingerprints_text_band7": (
        "fingerprint_version",
        "text_band7",
        "token_count",
    ),
}


def _database_path(engine: Engine) -> Path | None:
    database = engine.url.database
    if not database or database == ":memory:":
        return None
    return Path(database).resolve()


def schema_version(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(connection.exec_driver_sql("PRAGMA user_version").scalar_one())


def create_pre_migration_backup(
    engine: Engine,
    *,
    from_version: int,
    to_version: int,
) -> Path | None:
    """Create and verify a consistent SQLite snapshot before a migration."""

    database_path = _database_path(engine)
    if database_path is None or not database_path.exists():
        return None

    backup_dir = SCHEMA_SNAPSHOT_DIR
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_dir / (
        f"{database_path.stem}.schema-v{from_version}-to-v{to_version}.{timestamp}.db"
    )

    with closing(sqlite3.connect(database_path)) as source, closing(
        sqlite3.connect(backup_path)
    ) as target:
        source.backup(target)
        journal_mode = str(target.execute("PRAGMA journal_mode=DELETE").fetchone()[0])
        if journal_mode.lower() != "delete":
            raise RuntimeError(
                f"迁移前快照无法转换为独立日志模式: {journal_mode}"
            )
        integrity = target.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"迁移前数据库快照校验失败: {integrity}")

    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{backup_path}{suffix}")
        if suffix == "-wal" and sidecar.exists() and sidecar.stat().st_size:
            raise RuntimeError(f"迁移前快照仍依赖未归档 WAL: {sidecar}")
        sidecar.unlink(missing_ok=True)

    backup_path.chmod(0o600)
    checksum_path = backup_path.with_suffix(backup_path.suffix + ".sha256")
    checksum_path.write_text(f"{_sha256(backup_path)}  {backup_path.name}\n", encoding="utf-8")
    checksum_path.chmod(0o600)
    return backup_path


def _ensure_tikz_asset_columns(connection) -> dict[str, int]:
    """Add editable TikZ asset columns when an older table lacks them."""

    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            'PRAGMA table_info("questions")'
        ).fetchall()
    }
    additions = {
        "answer_tikz_assets": "TEXT DEFAULT '[]'",
        "tikz_reference_image_path": "TEXT DEFAULT ''",
        "content_tikz_assets": "TEXT DEFAULT '[]'",
    }
    stats: dict[str, int] = {}
    for column, definition in additions.items():
        added = column not in columns
        if added:
            connection.exec_driver_sql(
                f"ALTER TABLE questions ADD COLUMN {column} {definition}"
            )
        stats[f"added_{column}"] = int(added)
    return stats


def _ensure_v9_question_columns(connection) -> dict[str, int]:
    """Add the v9 classification columns (applicable_grade, topic) to questions.

    Idempotent: safe to run on databases that already carry the columns
    (e.g. a v8 database being upgraded, or an older version reaching v9
    through the generic derived-schema path).
    """

    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            'PRAGMA table_info("questions")'
        ).fetchall()
    }
    additions = {
        "applicable_grade": "VARCHAR(100) NOT NULL DEFAULT ''",
        "topic": "VARCHAR(100) NOT NULL DEFAULT ''",
    }
    stats: dict[str, int] = {}
    for column, definition in additions.items():
        added = column not in columns
        if added:
            connection.exec_driver_sql(
                f"ALTER TABLE questions ADD COLUMN {column} {definition}"
            )
        stats[f"added_{column}"] = int(added)
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_questions_applicable_grade "
        "ON questions (applicable_grade)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_questions_topic ON questions (topic)"
    )
    return stats


def _validate_question_fingerprint_schema(
    connection,
    *,
    expected_columns: set[str] | None = None,
    expected_indexes: dict[str, tuple[str, ...]] | None = None,
) -> None:
    """Fail closed when a versioned fingerprint table is structurally damaged."""

    expected_columns = (
        QUESTION_FINGERPRINT_COLUMNS
        if expected_columns is None
        else expected_columns
    )
    expected_indexes = (
        QUESTION_FINGERPRINT_INDEXES
        if expected_indexes is None
        else expected_indexes
    )

    table_names = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "question_fingerprints" not in table_names:
        raise RuntimeError(
            "数据库结构不完整，缺少必要数据表: question_fingerprints"
        )

    table_info = connection.exec_driver_sql(
        'PRAGMA table_info("question_fingerprints")'
    ).fetchall()
    columns = {row[1] for row in table_info}
    missing_columns = expected_columns - columns
    if missing_columns:
        raise RuntimeError(
            "数据库表 question_fingerprints 缺少核心字段: "
            + ", ".join(sorted(missing_columns))
        )
    unexpected_columns = columns - expected_columns
    if unexpected_columns:
        raise RuntimeError(
            "数据库表 question_fingerprints 包含与版本不符的字段: "
            + ", ".join(sorted(unexpected_columns))
        )

    primary_key = [
        row[1]
        for row in sorted(table_info, key=lambda item: item[5])
        if row[5]
    ]
    if primary_key != ["question_id", "fingerprint_version"]:
        raise RuntimeError("question_fingerprints 复合主键结构异常")

    foreign_keys = connection.exec_driver_sql(
        'PRAGMA foreign_key_list("question_fingerprints")'
    ).fetchall()
    has_question_cascade = any(
        row[2] == "questions"
        and row[3] == "question_id"
        and row[4] == "id"
        and str(row[6]).upper() == "CASCADE"
        for row in foreign_keys
    )
    if not has_question_cascade:
        raise RuntimeError("question_fingerprints 缺少 questions 级联删除外键")

    index_rows = {
        row[1]: row
        for row in connection.exec_driver_sql(
            'PRAGMA index_list("question_fingerprints")'
        ).fetchall()
    }
    missing_indexes = set(expected_indexes) - set(index_rows)
    if missing_indexes:
        raise RuntimeError(
            "question_fingerprints 缺少查重索引: "
            + ", ".join(sorted(missing_indexes))
        )
    managed_indexes = {
        name
        for name in index_rows
        if name.startswith("idx_question_fingerprints_")
    }
    unexpected_indexes = managed_indexes - set(expected_indexes)
    if unexpected_indexes:
        raise RuntimeError(
            "question_fingerprints 包含与版本不符的查重索引: "
            + ", ".join(sorted(unexpected_indexes))
        )
    for index_name, expected_columns in expected_indexes.items():
        actual_columns = tuple(
            row[2]
            for row in connection.exec_driver_sql(
                f'PRAGMA index_info("{index_name}")'
            ).fetchall()
        )
        if actual_columns != expected_columns:
            raise RuntimeError(f"question_fingerprints 索引结构异常: {index_name}")
        index_row = index_rows[index_name]
        if int(index_row[2]) != 0:
            raise RuntimeError(
                f"question_fingerprints 查重索引不得设为唯一: {index_name}"
            )
        if len(index_row) < 5 or int(index_row[4]) != 0:
            raise RuntimeError(
                f"question_fingerprints 查重索引不得设为部分索引: {index_name}"
            )


def _ensure_question_fingerprint_table(connection) -> int:
    """Create the rebuildable fingerprint table with current lookup indexes."""

    table_exists = bool(
        connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='question_fingerprints'"
        ).first()
    )
    if not table_exists:
        connection.exec_driver_sql(
            """
            CREATE TABLE question_fingerprints (
                question_id INTEGER NOT NULL,
                fingerprint_version INTEGER NOT NULL,
                content_revision_hash VARCHAR(64) NOT NULL DEFAULT '',
                exact_hash VARCHAR(64) NOT NULL DEFAULT '',
                critical_math_hash VARCHAR(64) NOT NULL DEFAULT '',
                answer_hash VARCHAR(64) NOT NULL DEFAULT '',
                simhash_hex VARCHAR(32) NOT NULL DEFAULT '',
                token_count INTEGER NOT NULL DEFAULT 0,
                choice_count INTEGER NOT NULL DEFAULT 0,
                figure_count INTEGER NOT NULL DEFAULT 0,
                visible_image_hashes TEXT NOT NULL DEFAULT '[]',
                tikz_hashes TEXT NOT NULL DEFAULT '[]',
                text_band0 VARCHAR(16) NOT NULL DEFAULT '',
                text_band1 VARCHAR(16) NOT NULL DEFAULT '',
                text_band2 VARCHAR(16) NOT NULL DEFAULT '',
                text_band3 VARCHAR(16) NOT NULL DEFAULT '',
                text_band4 VARCHAR(16) NOT NULL DEFAULT '',
                text_band5 VARCHAR(16) NOT NULL DEFAULT '',
                text_band6 VARCHAR(16) NOT NULL DEFAULT '',
                text_band7 VARCHAR(16) NOT NULL DEFAULT '',
                band0 INTEGER NOT NULL DEFAULT 0,
                band1 INTEGER NOT NULL DEFAULT 0,
                band2 INTEGER NOT NULL DEFAULT 0,
                band3 INTEGER NOT NULL DEFAULT 0,
                band4 INTEGER NOT NULL DEFAULT 0,
                band5 INTEGER NOT NULL DEFAULT 0,
                band6 INTEGER NOT NULL DEFAULT 0,
                band7 INTEGER NOT NULL DEFAULT 0,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (question_id, fingerprint_version),
                CONSTRAINT fk_question_fingerprints_question
                    FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
            )
            """
        )
        for index_name, columns in QUESTION_FINGERPRINT_INDEXES.items():
            connection.exec_driver_sql(
                f"CREATE INDEX {index_name} ON question_fingerprints "
                f"({', '.join(columns)})"
            )

    _validate_question_fingerprint_schema(connection)
    return int(not table_exists)


def _rebuild_relationship_tables(engine: Engine) -> dict[str, int]:
    """Rebuild relation tables with real FKs while repairing legacy drift."""

    stats: dict[str, int] = {}
    # AUTOCOMMIT lets us issue an explicit BEGIN IMMEDIATE.  Relying on the
    # sqlite3 driver's implicit transaction behavior is unsafe for DDL because
    # older driver modes may otherwise auto-commit CREATE/DROP statements.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True
            before_curriculums = int(
                connection.exec_driver_sql("SELECT COUNT(*) FROM question_curriculums").scalar_one()
            )
            before_paper_questions = int(
                connection.exec_driver_sql("SELECT COUNT(*) FROM paper_questions").scalar_one()
            )

            connection.exec_driver_sql("DROP TABLE IF EXISTS question_curriculums__new")
            connection.exec_driver_sql(
                """
                CREATE TABLE question_curriculums__new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    question_id INTEGER NOT NULL,
                    version_code VARCHAR(50) NOT NULL,
                    compulsory VARCHAR(100) DEFAULT '',
                    chapter VARCHAR(100) DEFAULT '',
                    knowledge VARCHAR(100) DEFAULT '',
                    CONSTRAINT uq_question_curriculum_version
                        UNIQUE (question_id, version_code),
                    CONSTRAINT fk_question_curriculums_question
                        FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO question_curriculums__new
                    (id, question_id, version_code, compulsory, chapter, knowledge)
                SELECT qc.id, qc.question_id, qc.version_code,
                       qc.compulsory, qc.chapter, qc.knowledge
                FROM question_curriculums AS qc
                JOIN questions AS q ON q.id = qc.question_id
                JOIN (
                    SELECT question_id, version_code, MAX(id) AS keep_id
                    FROM question_curriculums
                    GROUP BY question_id, version_code
                ) AS newest ON newest.keep_id = qc.id
                """
            )
            connection.exec_driver_sql("DROP TABLE question_curriculums")
            connection.exec_driver_sql(
                "ALTER TABLE question_curriculums__new RENAME TO question_curriculums"
            )
            connection.exec_driver_sql(
                "CREATE INDEX idx_question_curriculums_lookup "
                "ON question_curriculums (version_code, compulsory, chapter, knowledge)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX idx_question_curriculums_qid "
                "ON question_curriculums (question_id)"
            )

            connection.exec_driver_sql("DROP TABLE IF EXISTS paper_questions__new")
            connection.exec_driver_sql(
                """
                CREATE TABLE paper_questions__new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    paper_id INTEGER NOT NULL,
                    question_id INTEGER NOT NULL,
                    order_index INTEGER NOT NULL DEFAULT 0,
                    score INTEGER NOT NULL DEFAULT 5 CHECK (score >= 0),
                    CONSTRAINT uq_paper_question_order UNIQUE (paper_id, order_index),
                    CONSTRAINT fk_paper_questions_paper
                        FOREIGN KEY(paper_id) REFERENCES papers(id) ON DELETE CASCADE,
                    CONSTRAINT fk_paper_questions_question
                        FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO paper_questions__new
                    (id, paper_id, question_id, order_index, score)
                SELECT pq.id, pq.paper_id, pq.question_id,
                       COALESCE(pq.order_index, 0),
                       CASE WHEN pq.score IS NULL OR pq.score < 0 THEN 0 ELSE pq.score END
                FROM paper_questions AS pq
                JOIN papers AS p ON p.id = pq.paper_id
                JOIN questions AS q ON q.id = pq.question_id
                JOIN (
                    SELECT paper_id, COALESCE(order_index, 0), MAX(id) AS keep_id
                    FROM paper_questions
                    GROUP BY paper_id, COALESCE(order_index, 0)
                ) AS newest ON newest.keep_id = pq.id
                """
            )
            connection.exec_driver_sql("DROP TABLE paper_questions")
            connection.exec_driver_sql(
                "ALTER TABLE paper_questions__new RENAME TO paper_questions"
            )
            connection.exec_driver_sql(
                "CREATE INDEX idx_paper_questions_paper_id ON paper_questions (paper_id)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX idx_paper_questions_question_id ON paper_questions (question_id)"
            )
            connection.exec_driver_sql(
                """
                UPDATE papers
                SET total_score = COALESCE(
                    (
                        SELECT SUM(pq.score)
                        FROM paper_questions AS pq
                        WHERE pq.paper_id = papers.id
                    ),
                    0
                )
                """
            )

            remaining_curriculums = int(
                connection.exec_driver_sql("SELECT COUNT(*) FROM question_curriculums").scalar_one()
            )
            remaining_paper_questions = int(
                connection.exec_driver_sql("SELECT COUNT(*) FROM paper_questions").scalar_one()
            )
            tikz_column_stats = _ensure_tikz_asset_columns(connection)
            added_question_fingerprints = _ensure_question_fingerprint_table(connection)
            v9_column_stats = _ensure_v9_question_columns(connection)
            topics_stats = _ensure_question_topics_table(connection)
            v11_column_stats = _ensure_v11_topic_id_columns(connection)

            violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"迁移后仍存在外键异常: {violations[:5]}")

            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            stats = {
                "removed_question_curriculums": before_curriculums - remaining_curriculums,
                "removed_paper_questions": before_paper_questions - remaining_paper_questions,
                "added_question_fingerprints": added_question_fingerprints,
                **tikz_column_stats,
                **v9_column_stats,
                **topics_stats,
                **v11_column_stats,
            }
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            enabled = int(connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one())
            if enabled != 1:
                raise RuntimeError("迁移连接未能恢复 SQLite 外键检查")
    return stats


def _upgrade_derived_schema(engine: Engine) -> dict[str, int]:
    """Upgrade v2-v5 databases with TikZ fields and the current derived table."""

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True
            stats = _ensure_tikz_asset_columns(connection)
            stats["added_question_fingerprints"] = (
                _ensure_question_fingerprint_table(connection)
            )
            stats.update(_ensure_v9_question_columns(connection))
            stats.update(_ensure_question_topics_table(connection))
            stats.update(_ensure_v11_topic_id_columns(connection))
            stats.update(_ensure_lesson_plans_table(connection))
            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            return stats
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise


def _upgrade_v6_or_v7_fingerprint_schema(
    engine: Engine,
    *,
    from_version: int,
) -> dict[str, int]:
    """Upgrade v6/v7 indexes and add v8 text bands in one transaction."""

    if from_version not in {6, 7}:
        raise ValueError(f"unsupported fingerprint schema upgrade: {from_version}")

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True
            _validate_question_fingerprint_schema(
                connection,
                expected_columns=(
                    V6_QUESTION_FINGERPRINT_COLUMNS
                    if from_version == 6
                    else V7_QUESTION_FINGERPRINT_COLUMNS
                ),
                expected_indexes=(
                    V6_QUESTION_FINGERPRINT_INDEXES
                    if from_version == 6
                    else V7_QUESTION_FINGERPRINT_INDEXES
                ),
            )
            rebuilt_indexes = 0
            if from_version == 6:
                for index_name, columns in V7_QUESTION_FINGERPRINT_INDEXES.items():
                    if index_name == "idx_question_fingerprints_exact":
                        continue
                    connection.exec_driver_sql(f'DROP INDEX "{index_name}"')
                    connection.exec_driver_sql(
                        f'CREATE INDEX "{index_name}" ON question_fingerprints '
                        f"({', '.join(columns)})"
                    )
                    rebuilt_indexes += 1

            for band_no in range(8):
                connection.exec_driver_sql(
                    "ALTER TABLE question_fingerprints "
                    f"ADD COLUMN text_band{band_no} VARCHAR(16) "
                    "NOT NULL DEFAULT ''"
                )

            added_text_indexes = 0
            for band_no in range(8):
                index_name = f"idx_question_fingerprints_text_band{band_no}"
                columns = QUESTION_FINGERPRINT_INDEXES[index_name]
                connection.exec_driver_sql(
                    f'CREATE INDEX "{index_name}" ON question_fingerprints '
                    f"({', '.join(columns)})"
                )
                added_text_indexes += 1
            _validate_question_fingerprint_schema(connection)
            topics_stats = _ensure_question_topics_table(connection)
            v11_stats = _ensure_v11_topic_id_columns(connection)
            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            return {
                "rebuilt_question_fingerprint_indexes": rebuilt_indexes,
                "added_question_fingerprint_text_columns": 8,
                "added_question_fingerprint_text_indexes": added_text_indexes,
                **topics_stats,
                **v11_stats,
            }
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise


def _upgrade_v8_question_columns(engine: Engine) -> dict[str, int]:
    """Upgrade v8 databases: add the v9 classification columns in one transaction."""

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True
            stats = _ensure_v9_question_columns(connection)
            stats.update(_ensure_question_topics_table(connection))
            stats.update(_ensure_v11_topic_id_columns(connection))
            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            return stats
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise


def _upgrade_v9_question_topics(engine: Engine) -> dict[str, int]:
    """Upgrade v9 databases: add the multi-kaoxiang table in one transaction."""

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True
            stats = _ensure_question_topics_table(connection)
            stats.update(_ensure_v11_topic_id_columns(connection))
            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            return stats
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise


def _rebase_question_topics_to_topic_ids(connection) -> int:
    """Rebuild an old topic-name based question_topics table to topic_id.

    Old (v9/v10) tables carry a ``topic`` column and a UNIQUE constraint that
    includes it; the v11 structure stores only ``topic_id``. Returns the number
    of rows migrated, or 0 when the table is already v11-shaped.
    """
    exists = bool(
        connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='question_topics'"
        ).first()
    )
    if not exists:
        return 0
    columns = {
        row[1]
        for row in connection.exec_driver_sql('PRAGMA table_info("question_topics")').fetchall()
    }
    if "topic_id" in columns and "topic" not in columns:
        return 0

    from mathbank.topics_helper import topic_id_for

    rows = connection.exec_driver_sql(
        """
        SELECT id, question_id, version_code, compulsory, chapter, knowledge, topic, sort_order
        FROM question_topics
        """
    ).fetchall()
    connection.exec_driver_sql(
        'ALTER TABLE question_topics RENAME TO question_topics_legacy_v11'
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE question_topics (
            id INTEGER NOT NULL PRIMARY KEY,
            question_id INTEGER NOT NULL,
            version_code VARCHAR(50) NOT NULL DEFAULT '',
            compulsory VARCHAR(100) NOT NULL DEFAULT '',
            chapter VARCHAR(100) NOT NULL DEFAULT '',
            knowledge VARCHAR(100) NOT NULL DEFAULT '',
            topic_id VARCHAR(100) NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            CONSTRAINT uq_question_topic_slot
                UNIQUE (question_id, version_code, compulsory, chapter, knowledge, topic_id),
            CONSTRAINT fk_question_topics_question
                FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
        )
        """
    )
    migrated = 0
    for row_id, q_id, version_code, comp, chap, know, topic, sort_order in rows:
        tid = topic_id_for(comp or "", chap or "", know or "", topic or "")
        connection.exec_driver_sql(
            """
            INSERT OR IGNORE INTO question_topics
                (id, question_id, version_code, compulsory, chapter, knowledge, topic_id, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (row_id, q_id, version_code or "", comp or "", chap or "", know or "", tid, sort_order or 0),
        )
        migrated += 1
    connection.exec_driver_sql("DROP TABLE question_topics_legacy_v11")
    connection.exec_driver_sql(
        "CREATE INDEX idx_question_topics_question "
        "ON question_topics(question_id, version_code)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX idx_question_topics_lookup "
        "ON question_topics(version_code, compulsory, chapter, knowledge, topic_id)"
    )
    return migrated


def _ensure_v11_topic_id_columns(connection) -> dict[str, int]:
    """v11: replace questions.topic with questions.topic_id (idempotent).

    Deterministic IDs are derived from the slot context, so backfill matches
    whatever the curriculum normalizer assigns for the same names.
    """
    from mathbank.topics_helper import topic_id_for

    columns = {
        row[1]
        for row in connection.exec_driver_sql('PRAGMA table_info("questions")').fetchall()
    }
    stats: dict[str, int] = {}
    if "topic_id" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE questions ADD COLUMN topic_id VARCHAR(100) NOT NULL DEFAULT ''"
        )
        stats["added_questions_topic_id"] = 1
        if {"category_compulsory", "category_chapter", "category_knowledge", "topic"}.issubset(columns):
            rows = connection.exec_driver_sql(
                """
                SELECT id, category_compulsory, category_chapter, category_knowledge, topic
                FROM questions WHERE topic <> ''
                """
            ).fetchall()
            for q_id, comp, chap, know, topic in rows:
                tid = topic_id_for(comp or "", chap or "", know or "", topic or "")
                connection.exec_driver_sql(
                    "UPDATE questions SET topic_id = ? WHERE id = ?", (tid, q_id)
                )
            stats["backfilled_questions_topic_ids"] = len(rows)
        else:
            # Very old layouts (e.g. the v1 fixture) lack the category_*
            # columns; the topic_id column itself is still added so later
            # upgrade steps can rely on it.  No rows to backfill anyway.
            stats["backfilled_questions_topic_ids"] = 0
    else:
        stats["added_questions_topic_id"] = 0
        stats["backfilled_questions_topic_ids"] = 0
    if "topic" in columns:
        for index_row in connection.exec_driver_sql(
            'PRAGMA index_list("questions")'
        ).fetchall():
            index_name = index_row[1]
            index_columns = {
                row[2]
                for row in connection.exec_driver_sql(
                    f'PRAGMA index_info("{index_name}")'
                ).fetchall()
            }
            if "topic" in index_columns:
                connection.exec_driver_sql(f'DROP INDEX "{index_name}"')
        connection.exec_driver_sql("ALTER TABLE questions DROP COLUMN topic")
        stats["dropped_questions_topic"] = 1
    else:
        stats["dropped_questions_topic"] = 0
    return stats


def _upgrade_v10_topic_ids(engine: Engine) -> dict[str, int]:
    """Upgrade v10 databases to v11: topic_id everywhere, topic names removed."""

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True
            stats = _ensure_v11_topic_id_columns(connection)
            stats["rebased_question_topic_slots"] = (
                _rebase_question_topics_to_topic_ids(connection)
            )
            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            return stats
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise


def _upgrade_v11_paper_category(engine: Engine) -> dict[str, int]:
    """Upgrade v11 databases to v12: add paper_category column."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True
            q_columns = {
                row[1]
                for row in connection.exec_driver_sql('PRAGMA table_info("questions")').fetchall()
            }
            added = 0
            if "paper_category" not in q_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE questions ADD COLUMN paper_category VARCHAR(50) NOT NULL DEFAULT ''"
                )
                connection.exec_driver_sql(
                    "CREATE INDEX idx_questions_paper_category ON questions(paper_category)"
                )
                added = 1
            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            return {"added_paper_category_column": added}
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise


def _upgrade_v12_lesson_plans(engine: Engine) -> dict[str, int]:
    """Upgrade v12 to v13: create lesson_plans table."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True

            table_exists = connection.exec_driver_sql(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lesson_plans'"
            ).first()

            added = 0
            if not table_exists:
                connection.exec_driver_sql("""
                    CREATE TABLE lesson_plans (
                        id INTEGER NOT NULL PRIMARY KEY,
                        title VARCHAR(200) NOT NULL DEFAULT '未命名教案',
                        grade VARCHAR(50) NOT NULL DEFAULT '',
                        compulsory VARCHAR(100) NOT NULL DEFAULT '',
                        chapter VARCHAR(100) NOT NULL DEFAULT '',
                        knowledge VARCHAR(100) NOT NULL DEFAULT '',
                        topic_ids TEXT NOT NULL DEFAULT '[]',
                        objectives TEXT NOT NULL DEFAULT '',
                        key_points TEXT NOT NULL DEFAULT '',
                        difficult_points TEXT NOT NULL DEFAULT '',
                        teaching_process TEXT NOT NULL DEFAULT '{}',
                        board_design TEXT NOT NULL DEFAULT '',
                        reflection TEXT NOT NULL DEFAULT '',
                        question_refs TEXT NOT NULL DEFAULT '{}',
                        knowledge_content TEXT NOT NULL DEFAULT '{}',
                        mindmap TEXT NOT NULL DEFAULT '{}',
                        status VARCHAR(20) NOT NULL DEFAULT 'draft',
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                connection.exec_driver_sql(
                    "CREATE INDEX idx_lesson_plans_grade ON lesson_plans(grade)"
                )
                connection.exec_driver_sql(
                    "CREATE INDEX idx_lesson_plans_compulsory ON lesson_plans(compulsory)"
                )
                connection.exec_driver_sql(
                    "CREATE INDEX idx_lesson_plans_status ON lesson_plans(status)"
                )
                added = 1

            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            return {"added_lesson_plans_table": added}
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise


def _upgrade_v13_lesson_plan_content(engine: Engine) -> dict[str, int]:
    """Upgrade v13 to v14: add lesson-plan knowledge content and mind-map columns."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True
            stats: dict[str, int] = {}
            stats.update(_ensure_lesson_plans_table(connection))
            stats.update(_ensure_lesson_plan_columns(connection))
            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            return stats
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise


def _ensure_tags_tables(connection) -> dict[str, int]:
    """Create the normalized tags + question_tags tables if missing."""

    stats: dict[str, int] = {}
    if not connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tags'"
    ).first():
        connection.exec_driver_sql(
            "CREATE TABLE tags ("
            "id INTEGER NOT NULL PRIMARY KEY, "
            "name VARCHAR(100) NOT NULL, "
            "CONSTRAINT uq_tags_name UNIQUE (name))"
        )
        connection.exec_driver_sql("CREATE INDEX idx_tags_name ON tags (name)")
        stats["added_tags_table"] = 1
    else:
        stats["added_tags_table"] = 0
    if not connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_tags'"
    ).first():
        connection.exec_driver_sql(
            """
            CREATE TABLE question_tags (
                question_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                CONSTRAINT pk_question_tags PRIMARY KEY (question_id, tag_id),
                CONSTRAINT fk_question_tags_question
                    FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE,
                CONSTRAINT fk_question_tags_tag
                    FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE
            )
            """
        )
        connection.exec_driver_sql(
            "CREATE INDEX idx_question_tags_tag ON question_tags (tag_id)"
        )
        stats["added_question_tags_table"] = 1
    else:
        stats["added_question_tags_table"] = 0
    return stats


def _ensure_knowledge_tables(connection) -> dict[str, int]:
    """Create the normalized knowledge_points + question_knowledge tables and
    the per-question exam-metadata columns if missing (self-heal path)."""

    stats: dict[str, int] = {}
    if not connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_points'"
    ).first():
        connection.exec_driver_sql(
            "CREATE TABLE knowledge_points ("
            "id INTEGER NOT NULL PRIMARY KEY, "
            "name VARCHAR(100) NOT NULL, "
            "CONSTRAINT uq_knowledge_points_name UNIQUE (name))"
        )
        connection.exec_driver_sql(
            "CREATE INDEX idx_knowledge_points_name ON knowledge_points (name)"
        )
        stats["added_knowledge_points_table"] = 1
    else:
        stats["added_knowledge_points_table"] = 0
    if not connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_knowledge'"
    ).first():
        connection.exec_driver_sql(
            """
            CREATE TABLE question_knowledge (
                question_id INTEGER NOT NULL,
                kp_id INTEGER NOT NULL,
                CONSTRAINT pk_question_knowledge PRIMARY KEY (question_id, kp_id),
                CONSTRAINT fk_question_knowledge_question
                    FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE,
                CONSTRAINT fk_question_knowledge_kp
                    FOREIGN KEY(kp_id) REFERENCES knowledge_points(id) ON DELETE CASCADE
            )
            """
        )
        connection.exec_driver_sql(
            "CREATE INDEX idx_question_knowledge_kp ON question_knowledge (kp_id)"
        )
        stats["added_question_knowledge_table"] = 1
    else:
        stats["added_question_knowledge_table"] = 0

    q_columns = {
        row[1]
        for row in connection.exec_driver_sql(
            'PRAGMA table_info("questions")'
        ).fetchall()
    }
    added_cols = 0
    for column, ddl in (
        ("knowledge_points", "TEXT NOT NULL DEFAULT ''"),
        ("exam_year", "INTEGER"),
        ("exam_region", "VARCHAR(100) NOT NULL DEFAULT ''"),
        ("exam_stage", "VARCHAR(20) NOT NULL DEFAULT ''"),
        ("video_url", "TEXT NOT NULL DEFAULT ''"),
        ("option_columns", "INTEGER NOT NULL DEFAULT 0"),
        ("analysis_methods", "TEXT NOT NULL DEFAULT '[]'"),
    ):
        if column not in q_columns:
            connection.exec_driver_sql(
                f'ALTER TABLE questions ADD COLUMN "{column}" {ddl}'
            )
            added_cols += 1
    stats["added_question_exam_columns"] = added_cols
    # 自愈：exam_stage 索引（v17 引入，旧库跳级到最新版本时补齐）
    index_exists = bool(
        connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='index' AND name='idx_questions_exam_stage'"
        ).first()
    )
    if not index_exists:
        current_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                'PRAGMA table_info("questions")'
            ).fetchall()
        }
        if "exam_stage" in current_columns:
            connection.exec_driver_sql(
                "CREATE INDEX idx_questions_exam_stage ON questions(exam_stage)"
            )
            stats["added_exam_stage_index"] = 1
    return stats


def _upgrade_v15_knowledge_and_exam(engine: Engine) -> dict[str, int]:
    """v15 -> v16: normalize knowledge points and add exam-metadata columns.

    Creates ``knowledge_points`` / ``question_knowledge`` and backfills them
    from the denormalized ``questions.category_knowledge`` column (the 小节 is
    itself a legitimate knowledge point), then adds the per-question exam
    metadata columns (``exam_year``, ``exam_region``, ``video_url``,
    ``option_columns``, ``analysis_methods``) ported from the reference
    library's ``questions`` table.
    """

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True

            stats = _ensure_knowledge_tables(connection)

            backfilled = 0
            rows = connection.exec_driver_sql(
                "SELECT id, category_knowledge FROM questions "
                "WHERE category_knowledge IS NOT NULL AND category_knowledge <> ''"
            ).fetchall()
            for q_id, kp_str in rows:
                names = [
                    t.strip()
                    for t in str(kp_str).replace("，", ",").split(",")
                    if t.strip()
                ]
                seen: set[str] = set()
                clean = []
                for name in names:
                    if name and name not in seen and len(name) <= 64:
                        seen.add(name)
                        clean.append(name)
                kp_ids = []
                for name in clean:
                    existing = connection.exec_driver_sql(
                        "SELECT id FROM knowledge_points WHERE name = ?", (name,)
                    ).first()
                    if existing:
                        kp_ids.append(existing[0])
                    else:
                        connection.exec_driver_sql(
                            "INSERT INTO knowledge_points (name) VALUES (?)", (name,)
                        )
                        kp_ids.append(
                            connection.exec_driver_sql(
                                "SELECT last_insert_rowid()"
                            ).scalar_one()
                        )
                for kp_id in kp_ids:
                    connection.exec_driver_sql(
                        "INSERT OR IGNORE INTO question_knowledge "
                        "(question_id, kp_id) VALUES (?, ?)",
                        (q_id, kp_id),
                    )
                connection.exec_driver_sql(
                    "UPDATE questions SET knowledge_points = ? WHERE id = ?",
                    (",".join(clean), q_id),
                )
                backfilled += 1

            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            stats["backfilled_question_knowledge"] = backfilled
            return stats
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise


def _upgrade_v16_exam_stage(engine: Engine) -> dict[str, int]:
    """v16 -> v17: add per-question exam stage column.

    Adds ``exam_stage`` (期中/期末/月考/其他) to the ``questions`` table so
    school-exam questions (``paper_category == xiaokao``) can be further split
    by examination stage for teaching-plan filtering.
    """

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True

            q_columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    'PRAGMA table_info("questions")'
                ).fetchall()
            }
            added = 0
            if "exam_stage" not in q_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE questions ADD COLUMN exam_stage "
                    "VARCHAR(20) NOT NULL DEFAULT ''"
                )
                added += 1
            if not any(
                row[1] == "idx_questions_exam_stage"
                for row in connection.exec_driver_sql(
                    'PRAGMA index_list("questions")'
                ).fetchall()
            ):
                connection.exec_driver_sql(
                    "CREATE INDEX idx_questions_exam_stage ON questions(exam_stage)"
                )
                added += 1

            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            return {"added_exam_stage_column_or_index": added}
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise


def _ensure_measurement_columns(connection) -> dict[str, int]:
    """Add the v18 educational-measurement columns to ``questions`` if missing."""

    q_columns = {
        row[1]
        for row in connection.exec_driver_sql(
            'PRAGMA table_info("questions")'
        ).fetchall()
    }
    added = 0
    for column, ddl in (
        ("difficulty_index", "FLOAT"),
        ("cognitive_level", "VARCHAR(20) NOT NULL DEFAULT ''"),
        ("core_competency", "TEXT NOT NULL DEFAULT ''"),
        ("error_prone_points", "TEXT NOT NULL DEFAULT '[]'"),
    ):
        if column not in q_columns:
            connection.exec_driver_sql(
                f'ALTER TABLE questions ADD COLUMN "{column}" {ddl}'
            )
            added += 1
    return {"added_question_measurement_columns": added}


def _ensure_batch_and_tikz_tables(connection) -> dict[str, int]:
    """v19: create import_batches / tikz_versions and add questions.options."""

    stats: dict[str, int] = {}
    if not connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='import_batches'"
    ).first():
        connection.exec_driver_sql(
            """
            CREATE TABLE import_batches (
                id INTEGER NOT NULL PRIMARY KEY,
                token VARCHAR(64) NOT NULL DEFAULT '',
                name VARCHAR(200) NOT NULL DEFAULT '',
                source_name VARCHAR(300) NOT NULL DEFAULT '',
                question_ids TEXT NOT NULL DEFAULT '[]',
                question_count INTEGER NOT NULL DEFAULT 0,
                rolled_back INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME
            )
            """
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX idx_import_batches_token ON import_batches(token)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX idx_import_batches_created ON import_batches(created_at)"
        )
        stats["added_import_batches_table"] = 1
    else:
        stats["added_import_batches_table"] = 0
    if not connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tikz_versions'"
    ).first():
        connection.exec_driver_sql(
            """
            CREATE TABLE tikz_versions (
                id INTEGER NOT NULL PRIMARY KEY,
                question_id INTEGER,
                slot VARCHAR(50) NOT NULL DEFAULT '',
                version INTEGER NOT NULL DEFAULT 1,
                parent_version_id INTEGER,
                code TEXT NOT NULL DEFAULT '',
                source_hash VARCHAR(64) NOT NULL DEFAULT '',
                renderer_version VARCHAR(50) NOT NULL DEFAULT '',
                model VARCHAR(100) NOT NULL DEFAULT '',
                created_at DATETIME,
                CONSTRAINT fk_tikz_versions_question
                    FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE SET NULL
            )
            """
        )
        connection.exec_driver_sql(
            "CREATE INDEX idx_tikz_versions_question ON tikz_versions(question_id, slot)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX idx_tikz_versions_hash ON tikz_versions(source_hash)"
        )
        stats["added_tikz_versions_table"] = 1
    else:
        stats["added_tikz_versions_table"] = 0

    q_columns = {
        row[1]
        for row in connection.exec_driver_sql(
            'PRAGMA table_info("questions")'
        ).fetchall()
    }
    if "options" not in q_columns:
        connection.exec_driver_sql(
            "ALTER TABLE questions ADD COLUMN options TEXT NOT NULL DEFAULT '[]'"
        )
        stats["added_question_options_column"] = 1
    else:
        stats["added_question_options_column"] = 0
    return stats



def _ensure_lecture_tables(connection) -> dict[str, int]:
    """Create lecture tables (v20) if they do not exist."""
    stats = {}
    definitions = {
        "lecture_settings": """
            CREATE TABLE lecture_settings (
                id INTEGER NOT NULL PRIMARY KEY,
                cover_title VARCHAR(200) NOT NULL DEFAULT '',
                cover_subtitle VARCHAR(200) NOT NULL DEFAULT '同步讲义',
                cover_brand VARCHAR(200) NOT NULL DEFAULT 'MT-Blaze 的数学空间',
                cover_author VARCHAR(200) NOT NULL DEFAULT '',
                quote_pool TEXT NOT NULL DEFAULT '[]',
                bound_mode VARCHAR(20) NOT NULL DEFAULT 'loose',
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
        "lecture_notes": """
            CREATE TABLE lecture_notes (
                id INTEGER NOT NULL PRIMARY KEY,
                note_key VARCHAR(300) NOT NULL,
                note_type VARCHAR(20) NOT NULL DEFAULT 'knowledge',
                content TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
        "lecture_drafts": """
            CREATE TABLE lecture_drafts (
                id INTEGER NOT NULL PRIMARY KEY,
                title VARCHAR(200) NOT NULL DEFAULT '未命名讲义',
                grade VARCHAR(50) NOT NULL DEFAULT '',
                compulsory VARCHAR(100) NOT NULL DEFAULT '',
                chapter VARCHAR(100) NOT NULL DEFAULT '',
                knowledge VARCHAR(100) NOT NULL DEFAULT '',
                config TEXT NOT NULL DEFAULT '{}',
                knowledge_src TEXT NOT NULL DEFAULT '',
                status VARCHAR(20) NOT NULL DEFAULT 'draft',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
    }
    for table, ddl in definitions.items():
        exists = bool(connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='%s'" % table
        ).first())
        if exists:
            continue
        connection.exec_driver_sql(ddl)
        stats["added_%s_table" % table] = 1
    # 唯一索引
    idx_exists = bool(connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_lecture_notes_key'"
    ).first())
    if not idx_exists:
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX idx_lecture_notes_key ON lecture_notes(note_key)"
        )
        stats["added_lecture_notes_key_index"] = 1
    for col, table in (("grade", "lecture_drafts"), ("compulsory", "lecture_drafts")):
        idx_name = "idx_lecture_drafts_%s" % col
        exists = bool(connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='%s'" % idx_name
        ).first())
        if not exists:
            connection.exec_driver_sql("CREATE INDEX %s ON %s(%s)" % (idx_name, table, col))
            stats["added_%s" % idx_name] = 1
    return stats



def _upgrade_v20_question_sources(engine: Engine) -> dict[str, int]:
    """v20 -> v21: question_sources (one question, many sources)."""

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True
            stats = _ensure_question_sources_table(connection)
            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            return stats
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise


def _upgrade_v21_source_geo(engine: Engine) -> dict[str, int]:
    """v21 -> v22: question_sources 加 province/city/grade/semester 四列（地区三级+学期）。"""

    stats: dict[str, int] = {}
    with engine.begin() as connection:
        existing = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(question_sources)").all()
        } if connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_sources'"
        ).first() else set()
        for col, ddl in (
            ("province", "VARCHAR(50) NOT NULL DEFAULT ''"),
            ("city", "VARCHAR(50) NOT NULL DEFAULT ''"),
            ("grade", "VARCHAR(10) NOT NULL DEFAULT ''"),
            ("semester", "VARCHAR(10) NOT NULL DEFAULT ''"),
        ):
            if col not in existing:
                connection.exec_driver_sql(f"ALTER TABLE question_sources ADD COLUMN {col} {ddl}")
                stats[f"added_source_{col}"] = 1
        connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
    return stats


def _upgrade_v22_paper_question_no(engine: Engine) -> dict[str, int]:
    """v22 -> v23: questions 表加 paper_question_no 列（试卷原题号）。"""
    stats: dict[str, int] = {}
    with engine.begin() as connection:
        existing = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(questions)").all()
        }
        if "paper_question_no" not in existing:
            connection.exec_driver_sql(
                "ALTER TABLE questions ADD COLUMN paper_question_no VARCHAR(50) NOT NULL DEFAULT ''"
            )
            stats["added_questions_paper_question_no"] = 1
        connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
    return stats


def _ensure_option_columns_semantics(connection) -> dict[str, int]:
    """把 ``questions.option_columns`` 的旧语义值归一化到新语义（幂等）。

    旧语义：固定列数，取值范围 1/2/3/4，且**列默认值就是 1**——
    于是存量每一行都是 1，既可能是用户手选的「1 列」，也可能只是默认值。

    新语义：``0`` = 自适应（交给 exam-zh 自动算列数），``1/2/4`` = 强制列数。
    两者在 1 上直接冲突：不迁移的话，所有存量选择题会被压成强制单列竖排。

    因为无法区分「手选 1」与「默认值 1」，取保守解：**一律刷成 0（自适应）**，
    即维持改动前「文字/图片都交给 exam-zh 自己排」的实际观感。
    ``2``/``4`` 在新旧语义下含义一致，原样保留；``3`` 也保留（exam-zh 支持）。
    """
    exists = connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='questions'"
    ).first()
    if not exists:
        return {}
    columns = {
        row[1]
        for row in connection.exec_driver_sql('PRAGMA table_info("questions")').fetchall()
    }
    if "option_columns" not in columns:
        return {}
    result = connection.exec_driver_sql(
        "UPDATE questions SET option_columns = 0 WHERE option_columns = 1"
    )
    return {"normalized_option_columns": int(result.rowcount or 0)}


def _upgrade_v23_option_columns(engine: Engine) -> dict[str, int]:
    """v23 -> v24: ``option_columns`` 语义改为「0=自适应」，存量 1 刷成 0。"""
    stats: dict[str, int] = {}
    with engine.begin() as connection:
        stats.update(_ensure_option_columns_semantics(connection))
        connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
    return stats


def _ensure_school_id_columns(connection) -> dict[str, int]:
    """v24 -> v25：question_sources.school_id 与 questions.source_school_id（可空）。

    学校 ID 化：schools.json 每条有确定性 id（sch_+sha1[:10]），来源记录改挂
    school_id 后，教师改简称只需更新词库一处，显示层按 id 翻译自动跟随。
    列一律可空——旧记录与手输地区不强制建档，回填由 scripts/backfill_school_ids 完成。
    """
    stats: dict[str, int] = {}
    if connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_sources'"
    ).first():
        qs_cols = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(question_sources)").all()}
        if "school_id" not in qs_cols:
            connection.exec_driver_sql("ALTER TABLE question_sources ADD COLUMN school_id VARCHAR(20)")
            stats["added_question_sources_school_id"] = 1
        if "idx_question_sources_school" not in {
            row[1] for row in connection.exec_driver_sql("PRAGMA index_list(question_sources)").all()
        }:
            connection.exec_driver_sql(
                "CREATE INDEX idx_question_sources_school ON question_sources(school_id)"
            )
            stats["added_question_sources_school_index"] = 1
    q_cols = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(questions)").all()}
    if "source_school_id" not in q_cols:
        connection.exec_driver_sql("ALTER TABLE questions ADD COLUMN source_school_id VARCHAR(20)")
        stats["added_questions_source_school_id"] = 1
    return stats


def _ensure_figure_layout_columns(connection) -> dict[str, int]:
    """自愈 v26 的题末插图三列（幂等，不动任何已有排版设置）。

    三列都是「可选装饰」：老库补上默认值即可，已有的 figure_align 一律保留。
    只顺手把非法枚举洗回默认——导出侧会把这些值拼进 adjustbox，绝不能放野值进去。
    """

    stats: dict[str, int] = {}
    if not connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='questions'"
    ).first():
        return stats
    columns = {
        row[1] for row in connection.exec_driver_sql("PRAGMA table_info(questions)").all()
    }
    if "figure_align_custom" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE questions ADD COLUMN figure_align_custom INTEGER NOT NULL DEFAULT 0"
        )
        stats["added_questions_figure_align_custom"] = 1
    if "figure_size" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE questions ADD COLUMN figure_size VARCHAR(20) NOT NULL DEFAULT 'auto'"
        )
        stats["added_questions_figure_size"] = 1
    if "image_layouts" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE questions ADD COLUMN image_layouts TEXT NOT NULL DEFAULT '{}'"
        )
        stats["added_questions_image_layouts"] = 1
    washed_sizes = connection.exec_driver_sql(
        "UPDATE questions SET figure_size = 'auto' "
        "WHERE figure_size IS NULL OR figure_size NOT IN ('auto', 'small', 'medium', 'large')"
    ).rowcount
    if washed_sizes:
        stats["normalized_questions_figure_size"] = int(washed_sizes)
    washed_custom = connection.exec_driver_sql(
        "UPDATE questions SET figure_align_custom = 0 "
        "WHERE figure_align_custom IS NULL OR figure_align_custom NOT IN (0, 1)"
    ).rowcount
    if washed_custom:
        stats["normalized_questions_figure_align_custom"] = int(washed_custom)
    if connection.exec_driver_sql(
        "SELECT COUNT(*) FROM questions WHERE figure_size NOT IN "
        "('auto', 'small', 'medium', 'large')"
    ).scalar():
        raise RuntimeError("questions.figure_size 包含无效值")
    if connection.exec_driver_sql(
        "SELECT COUNT(*) FROM questions WHERE figure_align_custom NOT IN (0, 1)"
    ).scalar():
        raise RuntimeError("questions.figure_align_custom 包含无效值")
    return stats


def _upgrade_v25_figure_layouts(engine: Engine) -> dict[str, int]:
    """v25 -> v26: 题末插图逐图尺寸/位置（见 _ensure_figure_layout_columns）。"""

    stats: dict[str, int] = {}
    with engine.begin() as connection:
        stats.update(_ensure_figure_layout_columns(connection))
        connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
    return stats


def _ensure_source_question_no_column(connection) -> dict[str, int]:
    """question_sources.question_no（原卷题号）列自愈，幂等。"""
    stats: dict[str, int] = {}
    exists = connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_sources'"
    ).first()
    if not exists:
        return stats
    columns = {
        row[1]
        for row in connection.exec_driver_sql('PRAGMA table_info("question_sources")').fetchall()
    }
    if "question_no" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE question_sources ADD COLUMN question_no VARCHAR(50) NOT NULL DEFAULT ''"
        )
        stats["added_source_question_no"] = 1
    return stats


def _backfill_source_question_no(connection) -> dict[str, int]:
    """把主表 paper_question_no 回填到每道题的主来源（sort_order 最小那条）。

    原卷题号属于「题 × 卷」关系，多来源各题的卷面编号本就不该相同；主表字段
    只镜像主来源，因此只有主来源能安全继承它。已有值不覆盖，重复执行是空操作。
    极老的库可能还没 paper_question_no 列（v22 之前），此时没有可回填的数据。
    """
    question_columns = {
        row[1]
        for row in connection.exec_driver_sql('PRAGMA table_info("questions")').fetchall()
    }
    source_columns = {
        row[1]
        for row in connection.exec_driver_sql('PRAGMA table_info("question_sources")').fetchall()
    }
    if "paper_question_no" not in question_columns or "question_no" not in source_columns:
        return {}
    result = connection.exec_driver_sql(
        """
        UPDATE question_sources
           SET question_no = (
                SELECT q.paper_question_no FROM questions q
                 WHERE q.id = question_sources.question_id
           )
         WHERE (question_no IS NULL OR question_no = '')
           AND sort_order = (
                SELECT MIN(s.sort_order) FROM question_sources s
                 WHERE s.question_id = question_sources.question_id
           )
           AND EXISTS (
                SELECT 1 FROM questions q
                 WHERE q.id = question_sources.question_id
                   AND q.paper_question_no IS NOT NULL
                   AND TRIM(q.paper_question_no) <> ''
           )
        """
    )
    return {"backfilled_source_question_no": int(result.rowcount or 0)}


def _upgrade_v26_source_question_no(engine: Engine) -> dict[str, int]:
    """v26 -> v27: question_sources 加 question_no（原卷题号），并回填主来源。"""

    stats: dict[str, int] = {}
    with engine.begin() as connection:
        stats.update(_ensure_source_question_no_column(connection))
        stats.update(_backfill_source_question_no(connection))
        connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
    return stats


def _upgrade_v24_school_ids(engine: Engine) -> dict[str, int]:
    """v24 -> v25: 学校 ID 化两列（见 _ensure_school_id_columns）。"""
    stats: dict[str, int] = {}
    with engine.begin() as connection:
        stats.update(_ensure_school_id_columns(connection))
        connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
    return stats


def _ensure_question_sources_table(connection) -> dict[str, int]:
    """Create question_sources (v21) if it does not exist."""
    stats = {}
    exists = bool(connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_sources'"
    ).first())
    if not exists:
        connection.exec_driver_sql(
            """
            CREATE TABLE question_sources (
                id INTEGER NOT NULL PRIMARY KEY,
                question_id INTEGER NOT NULL,
                year VARCHAR(20) NOT NULL DEFAULT '',
                region VARCHAR(100) NOT NULL DEFAULT '',
                exam_type VARCHAR(50) NOT NULL DEFAULT '',
                paper_title VARCHAR(200) NOT NULL DEFAULT '',
                question_no VARCHAR(50) NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                school_id VARCHAR(20)
            )"""
        )
        stats["added_question_sources_table"] = 1
    idx_exists = bool(connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_question_sources_question'"
    ).first())
    if not idx_exists:
        connection.exec_driver_sql(
            "CREATE INDEX idx_question_sources_question ON question_sources(question_id)"
        )
        stats["added_question_sources_question_index"] = 1
    # v25：school_id 列与索引统一交给 _ensure_school_id_columns（含老表自愈）
    stats.update(_ensure_school_id_columns(connection))
    # v27：原卷题号列（老表自愈；回填只在迁移里跑，避免每次启动覆盖用户清空）
    stats.update(_ensure_source_question_no_column(connection))
    return stats


def _upgrade_v19_lecture(engine: Engine) -> dict[str, int]:
    """v19 -> v20: lecture settings / notes / drafts tables."""

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True
            stats = _ensure_lecture_tables(connection)
            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            return stats
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise


def _upgrade_v18_batch_tikz_options(engine: Engine) -> dict[str, int]:
    """v18 -> v19: import batches + TikZ version tree + structured options."""

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True
            stats = _ensure_batch_and_tikz_tables(connection)
            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            return stats
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise


def _upgrade_v17_measurement(engine: Engine) -> dict[str, int]:
    """v17 -> v18: add difficulty index / cognitive level / competency / error points.

    Adds the educational-measurement metadata borrowed from mainstream question
    banks (菁优网/学科网) and item-response theory: a continuous 0~1 difficulty
    index (kept alongside the existing L1~L5 band), a Bloom-style cognitive
    level, the 数学核心素养 dimension, and free-form error-prone points.
    """

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True
            stats = _ensure_measurement_columns(connection)
            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            return stats
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise


def _upgrade_v14_storage(engine: Engine) -> dict[str, int]:
    """v14 -> v15: normalize tags and freeze paper snapshots.

    Creates ``tags`` / ``question_tags`` and backfills them from the
    denormalized ``questions.tags`` column, then rebuilds ``paper_questions`` so
    its ``question_id`` FK becomes SET NULL (deleting a question no longer
    cascades the paper row away) and gains a ``snapshot_json`` column holding a
    frozen copy of the question for stable export/view after deletion.
    """

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True

            tag_stats = _ensure_tags_tables(connection)
            backfilled = 0
            rows = connection.exec_driver_sql(
                "SELECT id, tags FROM questions "
                "WHERE tags IS NOT NULL AND tags <> ''"
            ).fetchall()
            for q_id, tag_str in rows:
                names = [
                    t.strip()
                    for t in str(tag_str).replace("，", ",").split(",")
                    if t.strip()
                ]
                seen: set[str] = set()
                clean = []
                for name in names:
                    if name and name not in seen and len(name) <= 64:
                        seen.add(name)
                        clean.append(name)
                tag_ids = []
                for name in clean:
                    existing = connection.exec_driver_sql(
                        "SELECT id FROM tags WHERE name = ?", (name,)
                    ).first()
                    if existing:
                        tag_ids.append(existing[0])
                    else:
                        connection.exec_driver_sql(
                            "INSERT INTO tags (name) VALUES (?)", (name,)
                        )
                        tag_ids.append(
                            connection.exec_driver_sql(
                                "SELECT last_insert_rowid()"
                            ).scalar_one()
                        )
                for tag_id in tag_ids:
                    connection.exec_driver_sql(
                        "INSERT OR IGNORE INTO question_tags "
                        "(question_id, tag_id) VALUES (?, ?)",
                        (q_id, tag_id),
                    )
                backfilled += 1

            before = int(
                connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM paper_questions"
                ).scalar_one()
            )
            connection.exec_driver_sql("DROP TABLE IF EXISTS paper_questions__new")
            connection.exec_driver_sql(
                """
                CREATE TABLE paper_questions__new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    paper_id INTEGER NOT NULL,
                    question_id INTEGER,
                    order_index INTEGER NOT NULL DEFAULT 0,
                    score INTEGER NOT NULL DEFAULT 5 CHECK (score >= 0),
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    CONSTRAINT uq_paper_question_order UNIQUE (paper_id, order_index),
                    CONSTRAINT fk_paper_questions_paper
                        FOREIGN KEY(paper_id) REFERENCES papers(id) ON DELETE CASCADE,
                    CONSTRAINT fk_paper_questions_question
                        FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE SET NULL
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO paper_questions__new
                    (id, paper_id, question_id, order_index, score, snapshot_json)
                SELECT pq.id, pq.paper_id, pq.question_id,
                       COALESCE(pq.order_index, 0),
                       CASE WHEN pq.score IS NULL OR pq.score < 0 THEN 0 ELSE pq.score END,
                       '{}'
                FROM paper_questions AS pq
                JOIN papers AS p ON p.id = pq.paper_id
                """
            )
            connection.exec_driver_sql("DROP TABLE paper_questions")
            connection.exec_driver_sql(
                "ALTER TABLE paper_questions__new RENAME TO paper_questions"
            )
            connection.exec_driver_sql(
                "CREATE INDEX idx_paper_questions_paper_id ON paper_questions (paper_id)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX idx_paper_questions_question_id ON paper_questions (question_id)"
            )
            after = int(
                connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM paper_questions"
                ).scalar_one()
            )

            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            return {
                **tag_stats,
                "backfilled_question_tags": backfilled,
                "rebuilt_paper_questions": before,
                "rebuilt_paper_questions_after": after,
            }
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise


def _ensure_lesson_plans_table(connection) -> dict[str, int]:
    """Create lesson_plans table if it does not exist."""
    table_exists = bool(
        connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lesson_plans'"
        ).first()
    )
    if table_exists:
        return {"added_lesson_plans_table": 0}
    connection.exec_driver_sql("""
        CREATE TABLE lesson_plans (
            id INTEGER NOT NULL PRIMARY KEY,
            title VARCHAR(200) NOT NULL DEFAULT '未命名教案',
            grade VARCHAR(50) NOT NULL DEFAULT '',
            compulsory VARCHAR(100) NOT NULL DEFAULT '',
            chapter VARCHAR(100) NOT NULL DEFAULT '',
            knowledge VARCHAR(100) NOT NULL DEFAULT '',
            topic_ids TEXT NOT NULL DEFAULT '[]',
            objectives TEXT NOT NULL DEFAULT '',
            key_points TEXT NOT NULL DEFAULT '',
            difficult_points TEXT NOT NULL DEFAULT '',
            teaching_process TEXT NOT NULL DEFAULT '{}',
            board_design TEXT NOT NULL DEFAULT '',
            reflection TEXT NOT NULL DEFAULT '',
            question_refs TEXT NOT NULL DEFAULT '{}',
            status VARCHAR(20) NOT NULL DEFAULT 'draft',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.exec_driver_sql(
        "CREATE INDEX idx_lesson_plans_grade ON lesson_plans(grade)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX idx_lesson_plans_compulsory ON lesson_plans(compulsory)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX idx_lesson_plans_status ON lesson_plans(status)"
    )
    return {"added_lesson_plans_table": 1}


LESSON_PLAN_ADDED_COLUMNS = {
    "knowledge_content": "TEXT NOT NULL DEFAULT '{}'",
    "mindmap": "TEXT NOT NULL DEFAULT '{}'",
}


def _ensure_lesson_plan_columns(connection) -> dict[str, int]:
    """Add lesson-plan columns introduced after the table itself was created."""

    table_exists = bool(
        connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lesson_plans'"
        ).first()
    )
    if not table_exists:
        return {"added_lesson_plan_columns": 0}
    existing = {
        row[1]
        for row in connection.exec_driver_sql('PRAGMA table_info("lesson_plans")').fetchall()
    }
    added = 0
    for column, ddl in LESSON_PLAN_ADDED_COLUMNS.items():
        if column not in existing:
            connection.exec_driver_sql(
                f'ALTER TABLE lesson_plans ADD COLUMN "{column}" {ddl}'
            )
            added += 1
    return {"added_lesson_plan_columns": added}


def ensure_late_tables(connection) -> dict[str, int]:
    """Create tables/columns introduced after a database reached the latest version.

    ``Base.metadata.create_all`` only fills the legacy core layout for existing
    databases, so a database that was already stamped as the latest version can
    still be missing a newer table (``lesson_plans`` in v13) or a newer column
    (``knowledge_content`` / ``mindmap`` in v14).  Heal both here instead of
    refusing to boot; the operation is idempotent and never drops data.
    """

    stats = _ensure_lesson_plans_table(connection)
    stats.update(_ensure_lesson_plan_columns(connection))
    stats.update(_ensure_tags_tables(connection))
    stats.update(_ensure_knowledge_tables(connection))
    stats.update(_ensure_measurement_columns(connection))
    stats.update(_ensure_batch_and_tikz_tables(connection))
    stats.update(_ensure_lecture_tables(connection))
    stats.update(_ensure_question_sources_table(connection))
    stats.update(_ensure_school_id_columns(connection))
    stats.update(_ensure_paper_snapshot_column(connection))
    # 先补 v26 的列，再建索引：列不在的时候索引不该抢先失败
    stats.update(_ensure_figure_layout_columns(connection))
    stats.update(_ensure_perf_indexes(connection))
    return stats


def _ensure_perf_indexes(connection) -> dict[str, int]:
    """自愈分页列表依赖的排序索引（幂等、不动任何数据）。

    列表接口按「created_at + id」翻页。缺这条复合索引时，SQLite 的执行计划是
    `SCAN questions` + `USE TEMP B-TREE FOR ORDER BY`——十万题规模下每翻一页都要
    把整表重排一遍。

    为什么挂在 ensure_late_tables 而不是新加一个 schema 版本：建索引不改数据语义、
    不影响旧版程序读写，而「已定版但仍需自愈」的启动路径恰好只有这里会执行；
    真按版本号走反而会让已经是最新版的老库永远补不上这条索引。
    """

    stats: dict[str, int] = {}
    if not connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='questions'"
    ).first():
        return stats
    # 老库形状守卫：v0/v1 形态的 questions 表可能**根本没有 created_at 列**
    # （迁移测试里就有这种库），此时建索引会直接把整条迁移打断，必须跳过。
    question_columns = {
        row[1]
        for row in connection.exec_driver_sql("PRAGMA table_info(questions)").all()
    }
    if "created_at" not in question_columns or "id" not in question_columns:
        return stats
    existing = {
        row[1] for row in connection.exec_driver_sql("PRAGMA index_list(questions)").all()
    }
    if "ix_questions_created_at_id" not in existing:
        connection.exec_driver_sql(
            "CREATE INDEX ix_questions_created_at_id ON questions(created_at, id)"
        )
        stats["added_questions_created_at_id_index"] = 1
    return stats


def _ensure_paper_snapshot_column(connection) -> dict[str, int]:
    """自愈 ``paper_questions.snapshot_json``（v14 引入），缺失时补回。

    v0/v1 形态的库会先走 `_rebuild_relationship_tables()`，它按**旧 DDL** 重建 paper_questions
    （不含 snapshot_json、FK 还是 CASCADE），而这条路径上没有任何后续步骤补列——只有 current==14
    才跑 `_upgrade_v14_storage`。测试态的内存库正是这个形态：ORM 模型有这列、表里没有，
    于是任何走 HTTP `/api/paper/save` 的用例都在 INSERT 上炸 500，整卷删除的不变式也无法被覆盖。
    加列是纯增量操作，不动既有行（DEFAULT '{}'），对已含该列的真实库零影响。
    """
    columns = {
        row[1]
        for row in connection.exec_driver_sql("PRAGMA table_info(paper_questions)").fetchall()
    }
    if not columns or "snapshot_json" in columns:
        return {}
    connection.exec_driver_sql(
        "ALTER TABLE paper_questions ADD COLUMN snapshot_json TEXT NOT NULL DEFAULT '{}'"
    )
    return {"added_paper_snapshot_column": 1}


def _ensure_question_topics_table(connection) -> dict[str, int]:
    """Create question_topics (topic_id based) and backfill primary slots."""

    table_exists = bool(
        connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='question_topics'"
        ).first()
    )
    rebased = 0
    if table_exists:
        rebased = _rebase_question_topics_to_topic_ids(connection)
        if rebased:
            return {
                "added_question_topics_table": 0,
                "backfilled_question_topic_slots": 0,
                "rebased_question_topic_slots": rebased,
            }
    else:
        connection.exec_driver_sql(
            """
            CREATE TABLE question_topics (
                id INTEGER NOT NULL PRIMARY KEY,
                question_id INTEGER NOT NULL,
                version_code VARCHAR(50) NOT NULL DEFAULT '',
                compulsory VARCHAR(100) NOT NULL DEFAULT '',
                chapter VARCHAR(100) NOT NULL DEFAULT '',
                knowledge VARCHAR(100) NOT NULL DEFAULT '',
                topic_id VARCHAR(100) NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                CONSTRAINT uq_question_topic_slot
                    UNIQUE (question_id, version_code, compulsory, chapter, knowledge, topic_id),
                CONSTRAINT fk_question_topics_question
                    FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
            )
            """
        )
        connection.exec_driver_sql(
            "CREATE INDEX idx_question_topics_question "
            "ON question_topics(question_id, version_code)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX idx_question_topics_lookup "
            "ON question_topics(version_code, compulsory, chapter, knowledge, topic_id)"
        )
    # Backfill the primary slot (sort_order 0) from the questions main columns.
    # Very old (v2-v5) layouts may lack the category_* columns entirely; only
    # backfill when they exist, so the table itself is always created.
    q_columns = {
        row[1]
        for row in connection.exec_driver_sql('PRAGMA table_info("questions")').fetchall()
    }
    if {"category_compulsory", "category_chapter", "category_knowledge", "topic"}.issubset(q_columns):
        from mathbank.topics_helper import topic_id_for

        backfilled = 0
        rows = connection.exec_driver_sql(
            """
            SELECT q.id, qc.version_code,
                   q.category_compulsory, q.category_chapter, q.category_knowledge, q.topic
            FROM questions q
            LEFT JOIN question_curriculums qc
                ON qc.question_id = q.id AND qc.compulsory = q.category_compulsory
                   AND qc.chapter = q.category_chapter AND qc.knowledge = q.category_knowledge
            WHERE q.category_compulsory <> ''
               OR q.category_chapter <> ''
               OR q.category_knowledge <> ''
            """
        ).fetchall()
        for q_id, version_code, comp, chap, know, topic in rows:
            if not topic:
                continue
            tid = topic_id_for(comp or "", chap or "", know or "", topic)
            try:
                connection.exec_driver_sql(
                    """
                    INSERT OR IGNORE INTO question_topics
                        (question_id, version_code, compulsory, chapter, knowledge, topic_id, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?, 0)
                    """,
                    (q_id, version_code or "A", comp or "", chap or "", know or "", tid),
                )
                backfilled += 1
            except sqlite3.IntegrityError:
                # Duplicate slot (same topic twice on one question) is ignored.
                pass
    else:
        backfilled = 0
    return {"added_question_topics_table": int(not table_exists), "backfilled_question_topic_slots": backfilled}


def migrate_database(
    engine: Engine,
    *,
    pre_migration_backup: Path | None = None,
) -> dict[str, object]:
    """Upgrade the connected SQLite database and fail loudly on errors."""

    current = schema_version(engine)
    if current > LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本 {current} 高于程序支持版本 {LATEST_SCHEMA_VERSION}，请升级程序。"
        )
    table_names = set(inspect(engine).get_table_names())
    if current == LATEST_SCHEMA_VERSION:
        if LEGACY_REQUIRED_TABLES.issubset(table_names):
            # Self-heal tables introduced after this version was stamped
            # (``lesson_plans`` in v13).  create_all only fills the legacy core
            # layout for existing databases, so without this a partially
            # migrated database would brick the next startup.
            with engine.begin() as connection:
                ensure_late_tables(connection)
            table_names = set(inspect(engine).get_table_names())
        if not REQUIRED_TABLES.issubset(table_names):
            missing = ", ".join(sorted(REQUIRED_TABLES - table_names))
            raise RuntimeError(f"数据库结构不完整，缺少必要数据表: {missing}")
        with engine.connect() as connection:
            _validate_question_fingerprint_schema(connection)
        return {"from_version": current, "to_version": current, "backup": None}

    if not table_names:
        # A caller may version an empty database immediately before creating the
        # current ORM metadata.
        with engine.begin() as connection:
            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
        return {"from_version": current, "to_version": LATEST_SCHEMA_VERSION, "backup": None}
    if not LEGACY_REQUIRED_TABLES.issubset(table_names):
        missing = ", ".join(sorted(LEGACY_REQUIRED_TABLES - table_names))
        raise RuntimeError(f"数据库结构不完整，缺少必要数据表: {missing}")

    backup = pre_migration_backup or create_pre_migration_backup(
        engine, from_version=current, to_version=LATEST_SCHEMA_VERSION
    )
    if current < 2:
        stats = _rebuild_relationship_tables(engine)
    elif current in {6, 7}:
        stats = _upgrade_v6_or_v7_fingerprint_schema(
            engine,
            from_version=current,
        )
    elif current == 8:
        stats = _upgrade_v8_question_columns(engine)
    elif current == 9:
        stats = _upgrade_v9_question_topics(engine)
    elif current == 10:
        stats = _upgrade_v10_topic_ids(engine)
    elif current == 11:
        stats = _upgrade_v11_paper_category(engine)
    elif current == 12:
        stats = _upgrade_v12_lesson_plans(engine)
    elif current == 13:
        stats = _upgrade_v13_lesson_plan_content(engine)
    elif current == 14:
        stats = _upgrade_v14_storage(engine)
    elif current == 15:
        stats = _upgrade_v15_knowledge_and_exam(engine)
    elif current == 16:
        stats = _upgrade_v16_exam_stage(engine)
    elif current == 17:
        stats = _upgrade_v17_measurement(engine)
    elif current == 18:
        stats = _upgrade_v18_batch_tikz_options(engine)
    elif current == 19:
        stats = _upgrade_v19_lecture(engine)
    elif current == 20:
        stats = _upgrade_v20_question_sources(engine)
    elif current == 21:
        stats = _upgrade_v21_source_geo(engine)
    elif current == 22:
        stats = _upgrade_v22_paper_question_no(engine)
    elif current == 23:
        stats = _upgrade_v23_option_columns(engine)
    elif current == 24:
        stats = _upgrade_v24_school_ids(engine)
    elif current == 25:
        stats = _upgrade_v25_figure_layouts(engine)
    elif current == 26:
        stats = _upgrade_v26_source_question_no(engine)
    else:
        stats = _upgrade_derived_schema(engine)
    # Every upgrade path must end with the tables/columns added in later
    # versions, even when a database jumps straight from an older version to
    # the latest one.  Counters accumulate so the report reflects real work.
    with engine.begin() as connection:
        for key, value in ensure_late_tables(connection).items():
            stats[key] = int(stats.get(key, 0)) + int(value)
        # 从 v<26 一步跳到最新版的库不走 v26 分支，主来源题号回填需在这里补一次；
        # current==26 已由分支完成，不重复统计。
        if current < 26:
            for key, value in _backfill_source_question_no(connection).items():
                stats[key] = int(stats.get(key, 0)) + int(value)
        # 兜底：从 v<23 一步跳到 v24 的库不会进 v23 分支（各 _upgrade_vN 都直接
        # 把 user_version 写成 LATEST），这里补跑一次相同的归一化。放在
        # ensure_late_tables 之后，确保 option_columns 列此时已存在。
        if current < 23:
            for key, value in _ensure_option_columns_semantics(connection).items():
                stats[key] = int(stats.get(key, 0)) + int(value)
    return {
        "from_version": current,
        "to_version": LATEST_SCHEMA_VERSION,
        "backup": str(backup) if backup else None,
        **stats,
    }
