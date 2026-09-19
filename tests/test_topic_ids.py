# -*- coding: utf-8 -*-
"""考点 ID 化 (schema v11) 回归测试。

Covers: 确定性 ID 生成、normalize 对象化、题目只存 topic_id、
手输新考点自动写回大纲、重命名考点后筛题仍命中、附加考向精确到考点过滤。
"""

import json
import os

from main import LOCAL_TOKEN
import mathbank.metadata as metadata_mod
from mathbank.topics_helper import (
    normalize_topic_list,
    topic_id_for,
    topic_id_for_slot,
    topic_id_to_name,
    topic_ids_for_name,
)


def _headers():
    return {"X-Local-Token": LOCAL_TOKEN}


def _payload(content, **overrides):
    payload = {
        "content": content,
        "question_type": "single_choice",
        "category_compulsory": "必修一",
        "category_chapter": "1. 集合与常用逻辑用语",
        "category_knowledge": "1.1 集合的概念",
        "difficulty": "medium",
        "source": "考点ID测试",
        "answer_markdown": "答案",
        "review": "",
        "image_paths": "[]",
        "extra_topics": "[]",
    }
    payload.update(overrides)
    return payload


def _create(client, content, **overrides):
    return client.post(
        "/api/questions",
        data=_payload(content, **overrides),
        headers=_headers(),
    )


# ---------------- 单元层：ID 生成与 normalize ----------------

def test_topic_id_deterministic():
    a = topic_id_for("必修一", "第一章", "1.1", "含参讨论")
    b = topic_id_for("必修一", "第一章", "1.1", "含参讨论")
    c = topic_id_for("必修一", "第一章", "1.2", "含参讨论")
    assert a == b
    assert a != c
    assert a.startswith("k_")
    assert len(a) == 12  # k_ + 10 hex


def test_normalize_topic_list_objects_and_ids():
    raw = ["考点A", {"name": "考点B"}, {"id": "k_keep", "name": "考点C"}]
    out = normalize_topic_list(raw, "必修一", "第一章", "1.1")
    assert len(out) == 3
    assert all(isinstance(t, dict) and t["id"] and t["name"] for t in out)
    assert out[2]["id"] == "k_keep"  # 已有 id 必须保留（重命名安全）
    # 幂等：对象数组再 normalize 结果一致
    out2 = normalize_topic_list(out, "必修一", "第一章", "1.1")
    assert out2 == out


def test_rename_keeps_id_after_normalize():
    renamed = [{"id": "k_keep", "name": "重命名后的考点"}]
    out = normalize_topic_list(renamed, "必修一", "第一章", "1.1")
    assert out[0]["id"] == "k_keep"
    assert out[0]["name"] == "重命名后的考点"


# ---------------- 集成层：录题存 ID、展示翻译、重命名后筛题 ----------------

def test_create_stores_known_topic_id(client):
    """已有考点走 slot 精确匹配，返回确定性 ID 且不触发自动创建。"""

    created = _create(client, "设 $x^2$，已有考点题。", topic="集合的表示方法")
    assert created.status_code == 200
    question = created.json()["question"]
    assert question["topic"] == "集合的表示方法"
    assert question["topic_id"].startswith("k_")

    # 重复创建同考点：ID 相同（确定性）
    again = _create(client, "设 $y^2$，同考点第二题。", topic="集合的表示方法")
    assert again.status_code == 200
    assert again.json()["question"]["topic_id"] == question["topic_id"]


def test_unknown_topic_never_pollutes_curriculum(client):
    """跨章误判的考点不得写回大纲。

    回归防护：同步/AI 归类曾把「条件概率性质」等概率考点写进
    必修二 / 7. 复数 / 7.1 复数的概念，导致单题录入下拉框混入非复数考点。
    """

    before = json.dumps(
        metadata_mod.METADATA_CACHE["curriculum"].get("必修二", {}),
        ensure_ascii=False,
        sort_keys=True,
    )
    created = _create(
        client,
        "设 $z$，跨章误判考点题。",
        category_compulsory="必修二",
        category_chapter="7. 复数",
        category_knowledge="7.1 复数的概念",
        topic="条件概率性质",
    )
    assert created.status_code == 200
    assert created.json()["question"]["topic_id"] == ""

    after = json.dumps(
        metadata_mod.METADATA_CACHE["curriculum"].get("必修二", {}),
        ensure_ascii=False,
        sort_keys=True,
    )
    assert before == after


def test_rename_topic_keeps_questions_reachable(client):
    import main as main_mod

    created = _create(client, "设 $z$，重命名场景题。", topic="集合的表示方法")
    qid = created.json()["question"]["id"]
    old_tid = created.json()["question"]["topic_id"]
    assert old_tid

    # 重命名大纲考点（保持 id 不变）
    section = None
    for s in metadata_mod.METADATA_CACHE["curriculum"]["必修一"]["1. 集合与常用逻辑用语"]:
        if s["name"] == "1.1 集合的概念":
            section = s
            break
    assert section is not None
    topic = next(t for t in section["topics"] if t["name"] == "集合的表示方法")
    topic["name"] = "改名考点v2"

    # 按新名筛题必须命中（筛选走 ID，不受重命名影响）
    listed = client.get("/api/questions", params={"topic": "改名考点v2"})
    assert listed.status_code == 200
    assert any(q["id"] == qid for q in listed.json())

    # 详情展示当前名（无旧名快照）
    detail = client.get(f"/api/questions/{qid}")
    assert detail.status_code == 200
    assert detail.json()["topic"] == "改名考点v2"
    assert detail.json()["topic_id"] == old_tid

    # 还原大纲
    topic["name"] = "集合的表示方法"


def test_extra_topic_filter_matches_exact_kaoxiang(client):
    extra = json.dumps([
        {"compulsory": "必修一", "chapter": "1. 集合与常用逻辑用语",
         "knowledge": "1.1 集合的概念", "topic": "元素与集合的关系"},
    ], ensure_ascii=False)
    _create(client, "附加考向精确匹配题。", topic="主考点", extra_topics=extra)
    _create(client, "无附加考向题。", topic="主考点", extra_topics="[]")

    import main as main_mod

    # 确保大纲里存在「元素与集合的关系」考点（作为附加考向候选）
    section = None
    for s in metadata_mod.METADATA_CACHE["curriculum"]["必修一"]["1. 集合与常用逻辑用语"]:
        if s["name"] == "1.1 集合的概念":
            section = s
            break
    if section is None:
        metadata_mod.METADATA_CACHE["curriculum"]["必修一"]["1. 集合与常用逻辑用语"] = [
            {"name": "1.1 集合的概念", "topics": []}
        ]
        section = metadata_mod.METADATA_CACHE["curriculum"]["必修一"]["1. 集合与常用逻辑用语"][0]
    if not any(t["name"] == "元素与集合的关系" for t in section["topics"]):
        section["topics"].append({"name": "元素与集合的关系"})

    params = {
        "extra_topics": json.dumps([
            {"compulsory": "必修一", "chapter": "1. 集合与常用逻辑用语",
             "knowledge": "1.1 集合的概念", "topic": "元素与集合的关系"},
        ], ensure_ascii=False),
        "topic_mode": "and",
    }
    listed = client.get("/api/questions", params=params)
    assert listed.status_code == 200
    qs = listed.json()
    assert len(qs) == 1
    assert "附加考向精确匹配题" in qs[0]["content"]


def test_slot_and_name_helpers():
    curriculum = {
        "必修一": {
            "第一章": [
                {
                    "name": "1.1 集合的概念",
                    "topics": [
                        {"id": "k_abc", "name": "含参讨论"},
                        {"id": "k_def", "name": "综合运用"},
                    ],
                },
                {"name": "1.2 集合间的基本关系", "topics": []},
            ]
        }
    }
    assert topic_id_for_slot(curriculum, "必修一", "第一章", "1.1 集合的概念", "含参讨论") == "k_abc"
    assert topic_id_for_slot(curriculum, "必修一", "第一章", "1.1 集合的概念", "不存在") == ""
    assert topic_id_to_name(curriculum, "k_def") == "综合运用"
    assert topic_id_to_name(curriculum, "k_missing") == ""
    assert topic_ids_for_name(curriculum, "综合运用") == ["k_def"]
    assert topic_ids_for_name(curriculum, "没有的考点") == []


# ---------------- v10 -> v11 真实迁移路径 ----------------

def test_v10_to_v11_migration_rebases_topic_ids(tmp_path):
    import sqlite3

    from sqlalchemy import create_engine

    from mathbank import db_migrations

    db_path = tmp_path / "v10.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE questions (
                id INTEGER PRIMARY KEY,
                content TEXT NOT NULL,
                category_compulsory VARCHAR(100) DEFAULT '',
                category_chapter VARCHAR(100) DEFAULT '',
                category_knowledge VARCHAR(100) DEFAULT '',
                applicable_grade VARCHAR(100) DEFAULT '',
                topic VARCHAR(100) DEFAULT ''
            );
            CREATE TABLE papers (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                subtitle TEXT DEFAULT '',
                paper_type TEXT DEFAULT 'exam',
                total_score INTEGER DEFAULT 150,
                metadata_json TEXT DEFAULT '{}',
                created_at DATETIME
            );
            CREATE TABLE question_curriculums (
                id INTEGER PRIMARY KEY,
                question_id INTEGER NOT NULL,
                version_code VARCHAR(50) NOT NULL,
                compulsory VARCHAR(100) DEFAULT '',
                chapter VARCHAR(100) DEFAULT '',
                knowledge VARCHAR(100) DEFAULT ''
            );
            CREATE TABLE paper_questions (
                id INTEGER PRIMARY KEY,
                paper_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                order_index INTEGER DEFAULT 0,
                score REAL DEFAULT 0
            );
            CREATE TABLE question_topics (
                id INTEGER PRIMARY KEY,
                question_id INTEGER NOT NULL,
                version_code VARCHAR(50) NOT NULL,
                compulsory VARCHAR(100) DEFAULT '',
                chapter VARCHAR(100) DEFAULT '',
                knowledge VARCHAR(100) DEFAULT '',
                topic VARCHAR(100) DEFAULT '',
                sort_order INTEGER DEFAULT 0
            );
            INSERT INTO questions
                (id, content, category_compulsory, category_chapter, category_knowledge, applicable_grade, topic)
            VALUES (1, '设 $x$，迁移题。', '必修一', '第一章', '1.1 集合的概念', 'g1', '含参讨论');
            INSERT INTO question_topics
                (question_id, version_code, compulsory, chapter, knowledge, topic, sort_order)
            VALUES
                (1, 'A', '必修一', '第一章', '1.1 集合的概念', '含参讨论', 0),
                (1, 'A', '必修一', '第一章', '1.1 集合的概念', '综合运用', 1);
            PRAGMA user_version = 10;
            """
        )

    latest_schema = db_migrations.LATEST_SCHEMA_VERSION
    engine = create_engine(f"sqlite:///{db_path}")
    result = db_migrations.migrate_database(engine)
    assert result["to_version"] == latest_schema
    assert db_migrations.schema_version(engine) == latest_schema

    with sqlite3.connect(db_path) as conn:
        q_cols = {r[1] for r in conn.execute('PRAGMA table_info("questions")').fetchall()}
        assert "topic" not in q_cols
        assert "topic_id" in q_cols
        qt_cols = {r[1] for r in conn.execute('PRAGMA table_info("question_topics")').fetchall()}
        assert "topic" not in qt_cols
        assert "topic_id" in qt_cols

        row = conn.execute(
            "SELECT topic_id FROM questions WHERE id = 1"
        ).fetchone()
        assert row[0] == topic_id_for("必修一", "第一章", "1.1 集合的概念", "含参讨论")

        slots = conn.execute(
            "SELECT topic_id, sort_order FROM question_topics WHERE question_id = 1 ORDER BY sort_order"
        ).fetchall()
        assert slots == [
            (topic_id_for("必修一", "第一章", "1.1 集合的概念", "含参讨论"), 0),
            (topic_id_for("必修一", "第一章", "1.1 集合的概念", "综合运用"), 1),
        ]
    engine.dispose()


# ---------------- 大纲热重载：外部修好的文件无需重启 ----------------

def _metadata_payload(curriculum):
    return {
        "applicable_grades": [],
        "question_types": [],
        "difficulties": [],
        "curriculum": curriculum,
    }


def _bump_mtime(path):
    """Push mtime forward: Windows file-system clock granularity is ~15ms."""

    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))


def _protect_global_state(monkeypatch):
    """Snapshot the process-wide cache so a reload test cannot leak state."""

    from mathbank import curriculums as curriculums_mod

    monkeypatch.setattr(metadata_mod, "METADATA_CACHE", metadata_mod.METADATA_CACHE)
    monkeypatch.setattr(
        metadata_mod, "_METADATA_FILE_MTIME", metadata_mod._METADATA_FILE_MTIME
    )
    monkeypatch.setattr(
        curriculums_mod, "_CURRENT_CURRICULUM", curriculums_mod._CURRENT_CURRICULUM
    )


def test_reload_metadata_if_changed_refreshes_cache(monkeypatch, tmp_path):
    """Disk repair of the knowledge tree is picked up on the next read."""

    _protect_global_state(monkeypatch)
    target = tmp_path / "custom_metadata.json"
    payload = _metadata_payload(
        {"必修二": {"7. 复数": [{"name": "7.1 复数的概念", "topics": []}]}}
    )
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(metadata_mod, "METADATA_FILE", str(target))
    metadata_mod._record_metadata_mtime()

    # 文件未变化时不应重载
    assert metadata_mod.reload_metadata_if_changed() is False

    # 模拟外部修复：给 7.1 补回被污染的考点
    payload["curriculum"]["必修二"]["7. 复数"][0]["topics"] = [
        {"id": "k_test00001", "name": "复数相等"}
    ]
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _bump_mtime(target)

    assert metadata_mod.reload_metadata_if_changed() is True
    section = metadata_mod.get_metadata_cache()["curriculum"]["必修二"]["7. 复数"][0]
    assert [t["name"] for t in section["topics"]] == ["复数相等"]

    # 再次调用：mtime 已同步，不重复重载
    assert metadata_mod.reload_metadata_if_changed() is False


def test_admin_reload_metadata_requires_token(client):
    """缺少/错误的本机 token 一律拒绝（中间件 403 或路由 401）。"""

    resp = client.post("/api/admin/reload-metadata")
    assert resp.status_code in (401, 403)


def test_admin_reload_metadata_with_token(client, monkeypatch, tmp_path):
    _protect_global_state(monkeypatch)
    target = tmp_path / "custom_metadata.json"
    target.write_text(
        json.dumps(_metadata_payload({"必修一": {}}), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(metadata_mod, "METADATA_FILE", str(target))

    resp = client.post("/api/admin/reload-metadata", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["reloaded"] is True
