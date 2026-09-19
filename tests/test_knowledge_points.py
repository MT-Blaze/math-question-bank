"""Tests for knowledge-point normalization and question exam metadata (v16)."""

import pytest
from sqlalchemy import create_engine

from mathbank import database as database_module
from mathbank import db_migrations
from mathbank.database import (
    KnowledgePoint,
    Question,
    QuestionKnowledge,
    sync_question_knowledge,
)


def _auth_headers():
    from main import LOCAL_TOKEN

    return {"X-Local-Token": LOCAL_TOKEN}


def test_sync_question_knowledge_creates_dictionary_and_links(db_session):
    question = Question(content="题干", question_type="single_choice", difficulty="L2")
    db_session.add(question)
    db_session.flush()

    names = sync_question_knowledge(db_session, question, "二次函数最值, 均值不等式，二次函数最值")
    db_session.flush()

    assert names == ["二次函数最值", "均值不等式"]
    assert question.knowledge_points == "二次函数最值,均值不等式"

    stored = {kp.name for kp in db_session.query(KnowledgePoint).all()}
    assert {"二次函数最值", "均值不等式"}.issubset(stored)

    links = (
        db_session.query(QuestionKnowledge)
        .filter(QuestionKnowledge.question_id == question.id)
        .count()
    )
    assert links == 2


def test_sync_question_knowledge_replaces_previous_links(db_session):
    question = Question(content="题干", question_type="single_choice", difficulty="L2")
    db_session.add(question)
    db_session.flush()

    sync_question_knowledge(db_session, question, "A,B")
    db_session.flush()
    sync_question_knowledge(db_session, question, "C")
    db_session.flush()

    names = {
        db_session.get(KnowledgePoint, link.kp_id).name
        for link in db_session.query(QuestionKnowledge)
        .filter(QuestionKnowledge.question_id == question.id)
        .all()
    }
    assert names == {"C"}
    assert question.knowledge_points == "C"


def test_to_dict_exposes_exam_metadata(db_session):
    question = Question(
        content="题干",
        question_type="single_choice",
        difficulty="L2",
        exam_year=2025,
        exam_region="全国甲卷",
        video_url="https://example.com/v/1",
        option_columns=2,
        analysis_methods='["几何法", "代数法"]',
        knowledge_points="二次函数最值",
    )
    db_session.add(question)
    db_session.flush()

    data = question.to_dict()
    assert data["exam_year"] == 2025
    assert data["exam_region"] == "全国甲卷"
    assert data["video_url"] == "https://example.com/v/1"
    assert data["option_columns"] == 2
    assert data["analysis_methods"] == ["几何法", "代数法"]
    assert data["knowledge_points"] == "二次函数最值"

    # Missing/invalid JSON degrades to an empty list, never raises.
    question.analysis_methods = ""
    assert question.to_dict()["analysis_methods"] == []


def test_api_create_persists_knowledge_points_and_exam_metadata(client, db_session):
    response = client.post(
        "/api/questions",
        data={
            "content": "已知 $x^2-1=0$ ...",
            "question_type": "single_choice",
            "difficulty": "L2",
            "category_compulsory": "必修一",
            "category_chapter": "第一章",
            "category_knowledge": "集合",
            "knowledge_points": "二次函数最值,均值不等式",
            "exam_year": "2025",
            "exam_region": "全国甲卷",
            "video_url": "https://example.com/v/1",
            "option_columns": "2",
            "analysis_methods": "几何法,代数法",
        },
        headers=_auth_headers(),
    )
    assert response.status_code == 200, response.text

    question = db_session.query(Question).order_by(Question.id.desc()).first()
    assert question is not None
    assert question.knowledge_points == "二次函数最值,均值不等式"
    assert question.exam_year == 2025
    assert question.exam_region == "全国甲卷"
    assert question.video_url == "https://example.com/v/1"
    assert question.option_columns == 2
    assert question.to_dict()["analysis_methods"] == ["几何法", "代数法"]

    kp_names = {kp.name for kp in db_session.query(KnowledgePoint).all()}
    assert {"二次函数最值", "均值不等式"}.issubset(kp_names)
    assert (
        db_session.query(QuestionKnowledge)
        .filter(QuestionKnowledge.question_id == question.id)
        .count()
        == 2
    )


def test_migration_v15_backfills_knowledge_points(tmp_path):
    db_path = tmp_path / "v15.db"
    engine = create_engine(f"sqlite:///{db_path}")
    database_module.Base.metadata.create_all(bind=engine)

    # Simulate a pre-v16 database: the two new tables are absent and the
    # version stamp is still 15, with a legacy category_knowledge value.
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE IF EXISTS question_knowledge")
        connection.exec_driver_sql("DROP TABLE IF EXISTS knowledge_points")
        connection.exec_driver_sql(
            "INSERT INTO questions (id, content, category_knowledge) "
            "VALUES (1, 'legacy question', '二次函数最值')"
        )
        connection.exec_driver_sql("PRAGMA user_version=15")

    result = db_migrations.migrate_database(
        engine, pre_migration_backup=tmp_path / "pre_v16.db"
    )

    assert result["to_version"] == db_migrations.LATEST_SCHEMA_VERSION
    assert result["from_version"] == 15

    with engine.begin() as connection:
        tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"knowledge_points", "question_knowledge"}.issubset(tables)

        kp_names = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM knowledge_points"
            ).fetchall()
        }
        assert "二次函数最值" in kp_names

        links = connection.exec_driver_sql(
            "SELECT question_id, kp_id FROM question_knowledge"
        ).fetchall()
        assert len(links) == 1 and links[0][0] == 1

        cache = connection.exec_driver_sql(
            "SELECT knowledge_points FROM questions WHERE id=1"
        ).scalar_one()
        assert cache == "二次函数最值"

        columns = {
            row[1]
            for row in connection.exec_driver_sql(
                'PRAGMA table_info("questions")'
            ).fetchall()
        }
        assert {
            "knowledge_points",
            "exam_year",
            "exam_region",
            "exam_stage",
            "video_url",
            "option_columns",
            "analysis_methods",
            "difficulty_index",
            "cognitive_level",
            "core_competency",
            "error_prone_points",
        }.issubset(columns)

    engine.dispose()


def test_api_create_persists_measurement_metadata(client, db_session):
    response = client.post(
        "/api/questions",
        data={
            "content": "求 $f(x)=\\ln x - ax$ 的最值",
            "question_type": "detailed_answer",
            "difficulty": "L3",
            "category_compulsory": "必修一",
            "category_chapter": "第一章",
            "category_knowledge": "函数",
            "difficulty_index": "0.62",
            "cognitive_level": "应用",
            "core_competency": "数学抽象,逻辑推理",
            "error_prone_points": "忽略定义域,符号错误",
        },
        headers=_auth_headers(),
    )
    assert response.status_code == 200, response.text

    question = db_session.query(Question).order_by(Question.id.desc()).first()
    assert question.difficulty_index == pytest.approx(0.62)
    assert question.cognitive_level == "应用"
    assert question.core_competency == "数学抽象,逻辑推理"

    data = question.to_dict()
    assert data["difficulty_index"] == pytest.approx(0.62)
    assert data["cognitive_level"] == "应用"
    assert data["core_competency"] == "数学抽象,逻辑推理"
    assert data["error_prone_points"] == ["忽略定义域", "符号错误"]


def test_difficulty_index_is_clamped_and_optional(client, db_session):
    # Out-of-range values clamp into [0, 1]; blank stays None.
    response = client.post(
        "/api/questions",
        data={
            "content": "测试难度系数",
            "question_type": "single_choice",
            "difficulty": "L2",
            "difficulty_index": "1.7",
        },
        headers=_auth_headers(),
    )
    assert response.status_code == 200, response.text
    assert db_session.query(Question).order_by(Question.id.desc()).first().difficulty_index == 1.0

    response = client.post(
        "/api/questions",
        data={
            "content": "无难度系数",
            "question_type": "single_choice",
            "difficulty": "L2",
            "difficulty_index": "",
        },
        headers=_auth_headers(),
    )
    assert response.status_code == 200, response.text
    assert db_session.query(Question).order_by(Question.id.desc()).first().difficulty_index is None


def test_migration_v17_adds_measurement_columns(tmp_path):
    db_path = tmp_path / "v17.db"
    engine = create_engine(f"sqlite:///{db_path}")
    database_module.Base.metadata.create_all(bind=engine)

    with engine.begin() as connection:
        # Simulate a v17 database: the measurement columns are absent.
        # Their auto-indexes must go first, otherwise DROP COLUMN is refused.
        for index in ("ix_questions_difficulty_index", "ix_questions_cognitive_level"):
            connection.exec_driver_sql(f"DROP INDEX IF EXISTS {index}")
        for column in (
            "difficulty_index",
            "cognitive_level",
            "core_competency",
            "error_prone_points",
        ):
            connection.exec_driver_sql(f"ALTER TABLE questions DROP COLUMN {column}")
        connection.exec_driver_sql(
            "INSERT INTO questions (id, content) VALUES (1, 'legacy')"
        )
        connection.exec_driver_sql("PRAGMA user_version=17")

    result = db_migrations.migrate_database(
        engine, pre_migration_backup=tmp_path / "pre_v18.db"
    )

    assert result["from_version"] == 17
    assert result["to_version"] == db_migrations.LATEST_SCHEMA_VERSION

    with engine.begin() as connection:
        columns = {
            row[1]
            for row in connection.exec_driver_sql(
                'PRAGMA table_info("questions")'
            ).fetchall()
        }
        assert {
            "difficulty_index",
            "cognitive_level",
            "core_competency",
            "error_prone_points",
        }.issubset(columns)
        # Backfilled defaults are safe for the pre-existing row.
        assert connection.exec_driver_sql(
            "SELECT cognitive_level FROM questions WHERE id=1"
        ).scalar_one() == ""
        assert connection.exec_driver_sql(
            "SELECT error_prone_points FROM questions WHERE id=1"
        ).scalar_one() == "[]"

    engine.dispose()
