# -*- coding: utf-8 -*-
"""题末插图逐图排版（移植自上游 V2.7）回归：判定口径、脏数据、迁移、端点、导出。

移植的动机不是「多一个尺寸下拉框」。老判定只看「第一张图之后还剩没剩正文」，落在
``choices`` / ``tabular`` 里的图（典型就是选项拼图）会被当成题末插图搬走：卷面上同
一张图出现两份、选项变成空壳。新判定带环境栈，网页预览与 LaTeX 导出共用同一套口径。
"""
import json
import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import create_engine

from main import LOCAL_TOKEN

from mathbank import db_migrations
from mathbank.database import Question, normalize_figure_size
from mathbank.image_layout import (
    SIZE_CM,
    image_key,
    normalize_image_layouts,
    split_image_anchors,
)
from mathbank.paper_helper import (
    _detachable_figure_cluster,
    _should_preserve_inline_image_positions,
    build_latex_document,
    clean_content_for_latex,
)


def _columns(path, table):
    with closing(sqlite3.connect(path)) as connection:
        return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def test_split_image_anchors_only_detaches_the_trailing_cluster():
    """只有「环境之外、且一直到文末只剩图」的那一串才允许被摘到题末。"""

    anchored, detached = split_image_anchors("题干 ![](uploads/a.png) ![](/static/uploads/b.png)")
    assert anchored == "题干"
    assert detached == "![](uploads/a.png) ![](/static/uploads/b.png)"

    # 图后面还有正文：图片与正文绑死，一张都不许搬
    anchored, detached = split_image_anchors("题干 ![](a.png) 请据此求第二问")
    assert detached == ""
    assert "请据此求第二问" in anchored


def test_split_image_anchors_keeps_option_collage_inside_choices():
    """选项拼图不能当题末插图：这正是「卷面里同一张图出现两份」的根因。"""

    content = (
        "如图，下列说法正确的是\n"
        "\\begin{choices}\n\\item ![](uploads/collage_a.png)\n"
        "\\item ![](uploads/b.png)\n\\end{choices}\n"
    )
    anchored, detached = split_image_anchors(content)
    assert detached == "", "choices 环境里的图必须留在原锚点"
    assert anchored.rstrip() == content.rstrip()

    # 表格单元格里的图同理
    anchored, detached = split_image_anchors(
        "题干 \\begin{tabular}{|c|}\\hline ![](uploads/t.png) \\\\ \\hline \\end{tabular}"
    )
    assert detached == ""

    # 环境已经闭合之后的图，才是可以搬走的题末图簇
    anchored, detached = split_image_anchors(
        "题干 \\begin{choices}\n\\item A\n\\end{choices} ![](uploads/fig.png)"
    )
    assert detached == "![](uploads/fig.png)"


def test_normalize_image_layouts_only_keeps_visible_images_and_enum_values():
    """布局字典是用户可控输入，最终要拼进 adjustbox：值必须是枚举、键必须真存在。"""

    content = "题干 ![](/static/uploads/a.png)"
    assert normalize_image_layouts({"a.png": {"align": "left", "size": "large"}}, content) == {
        "a.png": {"align": "left", "size": "large"}
    }
    # 表单传来的是 JSON 字符串
    assert normalize_image_layouts('{"uploads/a.png": {"align": "center"}}', content) == {
        "a.png": {"align": "center", "size": "auto"}
    }
    assert image_key("/static/uploads/a.png") == image_key("uploads/a.png") == "a.png"
    # 正文里没有的图直接丢弃，不污染导出
    assert normalize_image_layouts({"ghost.png": {"size": "large"}}, content) == {}
    with pytest.raises(ValueError):
        normalize_image_layouts({"a.png": {"size": "huge"}}, content)
    with pytest.raises(ValueError):
        normalize_image_layouts({"a.png": {"align": "center;\\includegraphics{x}"}}, content)
    with pytest.raises(ValueError):
        normalize_image_layouts({"a.png": "large"}, content)
    with pytest.raises(ValueError):
        normalize_image_layouts("not-json", content)
    with pytest.raises(ValueError):
        normalize_image_layouts([{"a.png": {}}], content)
    with pytest.raises(ValueError):
        normalize_image_layouts({f"i{i}.png": {} for i in range(201)}, content)


def test_question_image_layouts_round_trip_and_survives_dirty_rows():
    """属性读写都过校验；脏数据只退回默认，绝不能把列表或导出打成 500。"""

    question = Question(content="题干 ![](uploads/a.png) ![](uploads/b.png)")
    question.image_layouts = {"a.png": {"align": "right", "size": "small"}}
    assert question.image_layouts == {"a.png": {"align": "right", "size": "small"}}
    assert json.loads(question._image_layouts) == {"a.png": {"align": "right", "size": "small"}}

    question.image_layouts = '{"b.png": {"size": "medium"}}'
    assert question.image_layouts == {"b.png": {"align": "center", "size": "medium"}}

    question._image_layouts = "{坏数据"
    assert question.image_layouts == {}
    with pytest.raises(ValueError):
        question.image_layouts = {"a.png": {"size": "ginormous"}}

    # 枚举是严格域：混合大小写按「没设过」处理（前端 getQuestionFigSize 自己先转小写）
    assert normalize_figure_size("large") == "large"
    assert normalize_figure_size("LARGE") == "auto"
    assert normalize_figure_size(None) == "auto"
    assert normalize_figure_size("野值") == "auto"


def test_to_dict_exposes_figure_layout_without_touching_lean_payload():
    """完整序列化带三列；lean 摘要不带（否则十万题列表又要背 JSON）。"""

    question = Question(content="题干 ![](uploads/a.png)", question_type="single_choice")
    question.figure_size = "small"
    question.figure_align = "center"
    question.image_layouts = {"a.png": {"align": "left", "size": "large"}}
    payload = question.to_dict()
    assert payload["figure_size"] == "small"
    assert payload["figure_align"] == "center"
    assert payload["figure_align_custom"] is False
    assert payload["image_layouts"] == {"a.png": {"align": "left", "size": "large"}}

    lean = question.to_lean_dict()
    assert "image_layouts" not in lean

def _create_v25_database(path):
    """按 v25 形态建最小库：questions 有 figure_align，但还没有 v26 的三列。"""

    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            CREATE TABLE questions (
                id INTEGER PRIMARY KEY,
                content TEXT NOT NULL,
                question_type VARCHAR(50) DEFAULT 'single_choice',
                category_compulsory VARCHAR(50) DEFAULT '',
                category_chapter VARCHAR(100) DEFAULT '',
                category_knowledge VARCHAR(100) DEFAULT '',
                difficulty VARCHAR(20) DEFAULT '',
                source VARCHAR(200) DEFAULT '',
                answer_markdown TEXT DEFAULT '',
                image_paths TEXT DEFAULT '[]',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                figure_align VARCHAR(50) DEFAULT 'right'
            );
            CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
            CREATE TABLE question_curriculums (
                id INTEGER PRIMARY KEY,
                question_id INTEGER,
                version_code VARCHAR(10),
                compulsory VARCHAR(50) DEFAULT '',
                chapter VARCHAR(100) DEFAULT '',
                knowledge VARCHAR(100) DEFAULT ''
            );
            CREATE TABLE paper_questions (id INTEGER PRIMARY KEY, paper_id INTEGER, question_id INTEGER);
            INSERT INTO questions (id, content) VALUES (1, 'v25 存量题目');
            PRAGMA user_version=25;
            """
        )


def test_v25_to_v26_migration_adds_figure_layout_columns(tmp_path, monkeypatch):
    database_path = tmp_path / "v25.db"
    _create_v25_database(database_path)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snapshots")
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        result = db_migrations.migrate_database(engine)
    finally:
        engine.dispose()

    assert result["from_version"] == 25
    assert result["to_version"] == db_migrations.LATEST_SCHEMA_VERSION == 26
    assert result["added_questions_image_layouts"] == 1
    assert result["added_questions_figure_size"] == 1
    assert result["added_questions_figure_align_custom"] == 1

    columns = _columns(database_path, "questions")
    assert {"image_layouts", "figure_size", "figure_align_custom"} <= columns
    with closing(sqlite3.connect(database_path)) as connection:
        row = connection.execute(
            "SELECT figure_size, figure_align_custom, image_layouts FROM questions WHERE id=1"
        ).fetchone()
    assert row == ("auto", 0, "{}")

    # 幂等：启动路径每一次都会跑自愈入口，第二次必须完全是空操作
    # （这里不重跑整条 migrate_database：这份合成 v25 库刻意只有最小表集）
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with engine.begin() as connection:
            assert db_migrations._ensure_figure_layout_columns(connection) == {}
    finally:
        engine.dispose()


def test_figure_layout_self_heal_washes_invalid_enums(tmp_path):
    """自愈顺手把非法枚举洗回默认：导出侧要把它拼进 adjustbox，不能收野值。"""

    database_path = tmp_path / "wash.db"
    with closing(sqlite3.connect(database_path)) as connection:
        connection.executescript(
            """
            CREATE TABLE questions (
                id INTEGER PRIMARY KEY,
                content TEXT NOT NULL,
                figure_align VARCHAR(50) DEFAULT 'right',
                figure_size VARCHAR(20) NOT NULL DEFAULT 'auto',
                figure_align_custom INTEGER NOT NULL DEFAULT 0,
                image_layouts TEXT NOT NULL DEFAULT '{}'
            );
            INSERT INTO questions (id, content, figure_size, figure_align_custom)
            VALUES (1, 'a', 'ginormous', 7);
            INSERT INTO questions (id, content, figure_size) VALUES (2, 'b', 'medium');
            """
        )
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with engine.begin() as connection:
            stats = db_migrations._ensure_figure_layout_columns(connection)
    finally:
        engine.dispose()

    assert stats["normalized_questions_figure_size"] == 1
    assert stats["normalized_questions_figure_align_custom"] == 1
    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute(
            "SELECT id, figure_size, figure_align_custom FROM questions ORDER BY id"
        ).fetchall()
    assert rows == [(1, "auto", 0), (2, "medium", 0)]

    # 已经全在枚举域内时，二次自愈是纯空操作（启动路径每次都会跑它）
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with engine.begin() as connection:
            assert db_migrations._ensure_figure_layout_columns(connection) == {}
    finally:
        engine.dispose()


def test_figure_align_endpoint_persists_size_without_resetting_other_fields(client, db_session):
    """端点三列「没传就不动」：只调位置绝不能把尺寸与逐图布局静默清回默认。"""

    question = Question(
        content="题干 ![](uploads/a.png)",
        question_type="single_choice",
        category_compulsory="必修一",
    )
    db_session.add(question)
    db_session.commit()
    qid = question.id

    headers = {"X-Local-Token": LOCAL_TOKEN}
    response = client.post(
        f"/api/questions/{qid}/figure_align",
        headers=headers,
        data={
            "figure_align": "center",
            "figure_size": "medium",
            "image_layouts": json.dumps({"a.png": {"align": "left", "size": "large"}}),
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["figure_align"] == "center"
    assert payload["figure_size"] == "medium"
    assert payload["figure_align_custom"] is True
    assert payload["image_layouts"] == {"a.png": {"align": "left", "size": "large"}}

    db_session.expire_all()
    stored = db_session.query(Question).filter(Question.id == qid).first()
    assert stored.figure_size == "medium"
    assert bool(stored.figure_align_custom) is True

    # 只调位置：尺寸与逐图布局原封不动（表单缺省是 None，而不是会覆盖的 "auto"）
    again = client.post(
        f"/api/questions/{qid}/figure_align",
        headers=headers,
        data={"figure_align": "bottom_right"},
    )
    assert again.json()["figure_size"] == "medium"
    assert again.json()["image_layouts"] == {"a.png": {"align": "left", "size": "large"}}

    # 非法布局必须是 400，而不是悄悄吞掉
    bad = client.post(
        f"/api/questions/{qid}/figure_align",
        headers=headers,
        data={"figure_align": "right", "image_layouts": '{"a.png": {"size": "huge"}}'},
    )
    assert bad.status_code == 400
    db_session.expire_all()
    assert db_session.query(Question).filter(Question.id == qid).first().figure_size == "medium"


def test_export_preserves_option_collage_and_applies_sizes():
    """导出侧：选项拼图留在原位；锚定图按逐图尺寸、题末图簇按整题尺寸。"""

    collage = (
        "下列说法正确的是\n"
        "\\begin{choices}\n\\item ![](uploads/a.png)\n\\item ![](uploads/b.png)\n\\end{choices}\n"
    )
    assert _should_preserve_inline_image_positions(collage) is True
    anchored, detached = _detachable_figure_cluster(collage)
    assert detached == "" and "choices" in anchored

    tail = "已知图形如图\n![](/static/uploads/a.png)"
    assert _should_preserve_inline_image_positions(tail) is False

    tex = clean_content_for_latex(
        "题干 ![](uploads/a.png) 结尾",
        q_type="detailed_answer",
        preserve_image_positions=True,
        image_layouts={"a.png": {"align": "center", "size": "large"}},
    )
    assert "max width=10.5cm,max height=7.5cm" in tex

    # 没设过尺寸的题目保持既有默认宽度，一个字都不多改
    untouched = clean_content_for_latex(
        "题干 ![](uploads/a.png) 结尾",
        q_type="detailed_answer",
        preserve_image_positions=True,
        image_layouts={},
    )
    assert "max width=9.0cm,max height=6.0cm" in untouched

    table_tex = clean_content_for_latex(
        "\\begin{tabular}{|c|}\\hline ![](uploads/a.png) \\\\ \\hline \\end{tabular}",
        image_layouts={"a.png": {"align": "center", "size": "large"}},
    )
    assert "max height=4.0cm" in table_tex, "单元格图再设多大都不许把行撑破"


def test_build_latex_document_uses_question_level_figure_size():
    """整题 figure_size 管题末可分离图簇；逐图设置优先级更高。"""

    def item(**extra):
        question = {
            "id": 1,
            "content": "已知函数 $f(x)$ 的图象如图所示\n![](/static/uploads/fig.png)",
            "question_type": "detailed_answer",
            "figure_align": "center",
            "answer_markdown": "",
            "image_paths": ["/static/uploads/fig.png"],
        }
        question.update(extra)
        return {"question": question, "score": 10}

    small = build_latex_document("测试卷", "", "exam", [item(figure_size="small")])
    assert "width=5.0cm" in small
    override = build_latex_document(
        "测试卷",
        "",
        "exam",
        [item(figure_size="small", image_layouts={"fig.png": {"size": "large"}})],
    )
    assert "width=10.5cm" in override
    legacy = build_latex_document("测试卷", "", "exam", [item()])
    assert "width=5.0cm" in legacy, "没设过尺寸时沿用既有单图默认宽度"
    assert SIZE_CM["large"][0] == 10.5
