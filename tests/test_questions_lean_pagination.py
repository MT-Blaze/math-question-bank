"""#35 组卷页性能重构：题库列表的 lean 摘要 + SQL 级分页契约。

十万题规模下，「一次性把全量 JSON 拉回前端」是压垮组卷页的第一根稻草。这里钉住三件事：

1. ``mode=lean`` 只回摘要与 140 字预览，绝不带 ``content`` / ``options`` /
   ``answer_markdown`` / ``tikz_code`` —— 完整题面走按需水合的批量端点。
2. 过滤与分页必须留在 SQL 里（WHERE + LIMIT/OFFSET），不许把整表捞进 Python 再筛。
3. 老调用方的契约不破：不带 ``page`` 仍是整库数组；``lean`` 是唯一例外，它永远分页。
"""

import datetime
from contextlib import closing
import sqlite3

from sqlalchemy import create_engine, event

from mathbank import database as database_module
from mathbank import db_migrations
from mathbank.database import Question

_HEAVY_FIELDS = ("content", "options", "answer_markdown", "tikz_code", "review", "topics")


def _question(index, **overrides):
    fields = {
        "content": (
            "第" + str(index) + "题 已知 $x>0$，求 $\\frac{" + str(index) + "}{2}$ 的最小值"
        ),
        "question_type": "single_choice",
        "category_compulsory": "必修一",
        # 奇偶交替，方便按章节验证「过滤发生在分页之前」
        "category_chapter": "第一章" if index % 2 else "第二章",
        "category_knowledge": "集合",
        "difficulty": "L2",
        "source": "单元测试",
        "answer_markdown": "答案解析 " + "详" * 400,
        "options": '[{"label":"A","content":"1"}]',
        # 显式递增的录入时间：默认值在同一秒里会挤在一起，排序断言就不稳了
        "created_at": datetime.datetime(2026, 1, 1, 8, 0, 0) + datetime.timedelta(minutes=index),
    }
    fields.update(overrides)
    return Question(**fields)


def _seed(db_session, count):
    questions = [_question(index) for index in range(1, count + 1)]
    db_session.add_all(questions)
    db_session.commit()
    return [question.id for question in questions]


def _record_statements(engine):
    """抓下真实执行的 SQL，用来钉住「分页没有退化成 Python 切片」。"""

    seen = []

    def listener(_conn, _cursor, statement, _params, _context, _executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", listener)
    return seen, lambda: event.remove(engine, "before_cursor_execute", listener)


def test_lean_page_returns_previews_without_the_full_body(client, db_session):
    ids = _seed(db_session, 5)

    payload = client.get(
        "/api/questions", params={"mode": "lean", "page": 2, "page_size": 2}
    ).json()

    assert payload["total"] == 5
    assert payload["page"] == 2
    assert payload["page_size"] == 2
    assert payload["total_pages"] == 3
    items = payload["items"]
    assert len(items) == 2
    # created_at 倒序翻页：第 2 页应该是 3、2 号题，而不是整表尾切
    assert [item["id"] for item in items] == [ids[2], ids[1]]

    for item in items:
        assert item["lean"] is True
        for heavy in _HEAVY_FIELDS:
            assert heavy not in item, f"列表接口不该下发 {heavy}：十万题时它就是几百 MB 的白屏"
        preview = item["preview"]
        assert preview, "预览为空时列表卡片会一片空白"
        assert len(preview) <= 141, "预览上限 140 字 + 一个省略号"
        assert "$" not in preview, "数学定界符属于包装，该剥掉"
        assert "\\frac" in preview, "公式本体必须留着：教师认 LaTeX，剥成乱码反而没法读"
        assert item["has_answer"] is True
        assert item["image_count"] == 0
        assert item["has_tikz_assets"] is False
        assert item["seq_num"] == ids.index(item["id"]) + 1


def test_lean_preview_collapses_figures_and_flags_tikz(client, db_session):
    db_session.add(
        _question(
            1,
            content="如图，![](https://cdn.example.com/a.png) 在 $\\triangle ABC$ 中求面积",
        )
    )
    db_session.add(_question(2, content="长题干" * 200, tikz_code="\\begin{tikzpicture}\\end{tikzpicture}", answer_markdown=""))
    db_session.commit()

    items = client.get("/api/questions", params={"mode": "lean", "page": 1, "page_size": 20}).json()["items"]
    by_content = {item["preview"][:2]: item for item in items}

    figure = by_content["如图"]
    assert "〔图〕" in figure["preview"], "插图折成占位符，列表要能看出这道题有配图"
    assert "![" not in figure["preview"] and "http" not in figure["preview"]

    long_one = by_content["长题"]
    assert long_one["preview"].endswith("…"), "超长题干必须截断，否则预览又把整题干拖回来了"
    assert len(long_one["preview"]) == 141
    assert long_one["has_answer"] is False, "只带 has_answer 布尔，答案正文走按需水合"
    assert long_one["has_tikz_assets"] is True


def test_filters_and_pagination_stay_in_sql(client, db_session):
    ids = _seed(db_session, 30)
    seen, detach = _record_statements(database_module.engine)
    try:
        payload = client.get(
            "/api/questions",
            params={"mode": "lean", "page": 2, "page_size": 5, "chapter": "第一章"},
        ).json()
    finally:
        detach()

    first_chapter = [question_id for index, question_id in enumerate(ids, 1) if index % 2]
    assert payload["total"] == len(first_chapter) == 15
    expected = list(reversed(first_chapter))[5:10]
    assert [item["id"] for item in payload["items"]] == expected, (
        "章节过滤必须发生在 LIMIT 之前；若先切片再过滤，第 2 页就会是空的或错位的"
    )

    paged = [statement for statement in seen if "LIMIT" in statement.upper() and "OFFSET" in statement.upper()]
    assert paged, "分页必须走 SQL LIMIT/OFFSET，不能把整表捞进 Python 再切"
    assert any("category_chapter" in statement for statement in paged), (
        "章节条件要和分页在同一条 SELECT 里，否则 Python 侧过滤会先把整表读进内存"
    )


def test_page_size_is_clamped_before_it_reaches_the_database(client, db_session):
    _seed(db_session, 3)

    payload = client.get("/api/questions", params={"mode": "lean", "page": 1, "page_size": 5000}).json()

    assert payload["page_size"] == 200, "上限 200：给组卷流一页拉满用，但不许一次拉穿十万题"
    assert payload["total_pages"] == 1
    assert len(payload["items"]) == 3


def test_lean_mode_never_falls_back_to_the_whole_bank(client, db_session):
    _seed(db_session, 3)

    lean = client.get("/api/questions", params={"mode": "lean"}).json()
    assert isinstance(lean, dict), "lean 漏传 page 也必须分页，绝不能退回整库数组"
    assert lean["page"] == 1 and lean["page_size"] == 20

    legacy = client.get("/api/questions").json()
    assert isinstance(legacy, list) and len(legacy) == 3, (
        "不带 page 的老调用方（录入页、导入页）仍按整库数组返回，本次不动它们的契约"
    )
    assert all("content" in item for item in legacy)


def test_paper_batch_endpoint_hydrates_full_body_with_seq_num(client, db_session):
    """购物车/展开详情的按需水合端点：完整题面 + 用户口径题号，顺序按请求。"""

    ids = _seed(db_session, 3)

    data = client.get(
        "/api/paper/questions", params={"ids": f"{ids[2]},{ids[0]},{ids[1]}"}
    ).json()["data"]

    assert [item["id"] for item in data] == [ids[2], ids[0], ids[1]], "顺序即卷面顺序，不能按物理 ID 重排"
    assert all(item["content"] and item["answer_markdown"] for item in data), (
        "这是水合端点，必须带完整正文，否则加入试卷后卡片是空的"
    )
    assert {item["id"]: item["seq_num"] for item in data} == {
        ids[0]: 1,
        ids[1]: 2,
        ids[2]: 3,
    }, "seq_num 缺位时，刷新后已选题目的题号会整片空白"


def _create_database(path, ddl):
    """建一个裸 SQLite 文件库，绕开 ORM 模型，专门验证迁移侧的自愈分支。"""

    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.executescript(ddl)


def _index_names(connection):
    return {row[1] for row in connection.exec_driver_sql("PRAGMA index_list(questions)")}


def test_perf_index_is_self_healed_and_idempotent(tmp_path):
    """已定版的老库只有 ensure_late_tables 这条路会执行，索引必须在这儿自愈。"""

    database_path = tmp_path / "perf.db"
    _create_database(
        database_path,
        "CREATE TABLE questions (id INTEGER PRIMARY KEY, content TEXT NOT NULL, created_at DATETIME);",
    )
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with engine.begin() as connection:
            assert db_migrations._ensure_perf_indexes(connection) == {
                "added_questions_created_at_id_index": 1
            }
            assert db_migrations._ensure_perf_indexes(connection) == {}, (
                "第二次必须零产出：自愈要幂等，不能每次启动都重建索引"
            )
            names = _index_names(connection)
    finally:
        engine.dispose()

    assert "ix_questions_created_at_id" in names


def test_perf_index_skips_legacy_databases_without_created_at(tmp_path):
    """v0/v1 形态的老库里 questions 表可能根本没有 created_at，此时建索引会打断整条迁移。"""

    database_path = tmp_path / "legacy.db"
    _create_database(
        database_path, "CREATE TABLE questions (id INTEGER PRIMARY KEY, content TEXT NOT NULL);"
    )
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with engine.begin() as connection:
            assert db_migrations._ensure_perf_indexes(connection) == {}
            assert "ix_questions_created_at_id" not in _index_names(connection)
    finally:
        engine.dispose()


def test_perf_index_skips_databases_without_the_questions_table(tmp_path):
    """全新空文件（连 questions 都没有）也不能炸：ensure_late_tables 在 create_all 之前就会被调到。"""

    database_path = tmp_path / "empty.db"
    _create_database(database_path, "CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT);")
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with engine.begin() as connection:
            assert db_migrations._ensure_perf_indexes(connection) == {}
    finally:
        engine.dispose()


def test_default_sort_walks_the_index_instead_of_a_temp_btree(tmp_path):
    """执行计划才是这次改造真正买到的东西：TEMP B-TREE 意味着每翻一页都要重排整表。"""

    database_path = tmp_path / "plan.db"
    _create_database(
        database_path,
        "CREATE TABLE questions (id INTEGER PRIMARY KEY, content TEXT NOT NULL, created_at DATETIME);",
    )
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with engine.begin() as connection:
            db_migrations._ensure_perf_indexes(connection)
            plan = " ".join(
                row[3]
                for row in connection.exec_driver_sql(
                    "EXPLAIN QUERY PLAN SELECT id FROM questions "
                    "ORDER BY created_at DESC, id DESC LIMIT 50 OFFSET 500"
                )
            )
    finally:
        engine.dispose()

    assert "ix_questions_created_at_id" in plan, plan
    assert "TEMP B-TREE" not in plan, plan
