"""只读重复题扫描脚本的分档与报告契约。"""

from __future__ import annotations

import json
import sqlite3

import pytest

from mathbank.question_duplicates import QuestionDuplicateInput, build_question_fingerprint
from scripts import find_exact_duplicates as scanner


BASE_SCHEMA = """
CREATE TABLE questions (
    id INTEGER PRIMARY KEY,
    content TEXT NOT NULL DEFAULT '',
    answer_markdown TEXT NOT NULL DEFAULT '',
    question_type TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    category_compulsory TEXT NOT NULL DEFAULT '',
    category_chapter TEXT NOT NULL DEFAULT '',
    category_knowledge TEXT NOT NULL DEFAULT ''
);
CREATE TABLE question_fingerprints (
    fingerprint_version INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    exact_hash TEXT NOT NULL,
    answer_hash TEXT NOT NULL,
    content_revision_hash TEXT NOT NULL,
    critical_math_hash TEXT NOT NULL,
    visible_image_hashes TEXT NOT NULL DEFAULT '[]',
    tikz_hashes TEXT NOT NULL DEFAULT '[]',
    figure_count INTEGER NOT NULL DEFAULT 0,
    choice_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ready'
);
CREATE TABLE paper_questions (
    paper_id INTEGER NOT NULL,
    question_id INTEGER
);
CREATE TABLE lesson_plans (
    id INTEGER PRIMARY KEY,
    question_refs TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE lecture_drafts (
    id INTEGER PRIMARY KEY,
    config TEXT NOT NULL DEFAULT '{}'
);
"""

STRICT_CONTENT = (
    "已知 $x>0$，求 $x+\\dfrac{1}{x}$ 的最小值。\\paren\n"
    "\\begin{choices}\n\\item 1\n\\item 2\n\\item 3\n\\item 4\n\\end{choices}"
)
STRICT_ANSWER = "【答案】B\n【解析】由均值不等式可得。"

ANSWER_MISMATCH_CONTENT = (
    "已知 $a>b>0$，下列不等式一定成立的是 \\paren\n"
    "\\begin{choices}\n\\item $a^2>b^2$\n\\item $a-b<0$\n"
    "\\item $\\dfrac{1}{a}>\\dfrac{1}{b}$\n\\item $a+b<0$\n\\end{choices}"
)
ANSWER_ONLY = "【答案】A\n【解析】由 $a>b>0$ 得 $a^2>b^2$。"

LAYOUT_CONTENT_A = (
    "已知 $1 \\le a+b \\le 4$，$-1 \\le a-b \\le 2$，"
    "则 $3a+2b$ 的取值范围是\\fillin."
)
LAYOUT_CONTENT_B = (
    "已知 $1 \\leq a + b \\leq 4$，$-1 \\leq a - b \\leq 2$，"
    "则 $3a + 2b$ 的取值范围是 \\fillin."
)
LAYOUT_ANSWER = "【答案】$[1,10]$\n【解析】线性组合可得。"

UNIQUE_CONTENT = "求 $1+1$ 的值。\\fillin."


def _fingerprint(content: str, answer: str) -> dict:
    fingerprint = build_question_fingerprint(
        QuestionDuplicateInput(
            content=content,
            answer_markdown=answer,
            question_type="single_choice",
        )
    )
    return {
        "fingerprint_version": fingerprint.fingerprint_version,
        "exact_hash": fingerprint.exact_hash,
        "answer_hash": fingerprint.answer_hash,
        "content_revision_hash": fingerprint.content_revision_hash,
        "critical_math_hash": fingerprint.critical_math_hash,
        "visible_image_hashes": json.dumps(
            list(fingerprint.visible_image_signatures)
        ),
        "tikz_hashes": json.dumps(list(fingerprint.tikz_signatures)),
        "figure_count": fingerprint.figure_count,
        "choice_count": fingerprint.choice_count,
        "status": "ready",
    }


def _insert_question(
    conn: sqlite3.Connection,
    question_id: int,
    *,
    content: str,
    answer: str = "",
    tags: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO questions
            (id, content, answer_markdown, question_type, source, tags,
             created_at, category_compulsory, category_chapter, category_knowledge)
        VALUES (?, ?, ?, 'single_choice', '测试来源', ?, ?, '必修一', '第一章', '集合')
        """,
        (
            question_id,
            content,
            answer,
            tags,
            f"2026-01-0{question_id} 00:00:00",
        ),
    )
    values = _fingerprint(content, answer)
    conn.execute(
        """
        INSERT INTO question_fingerprints
            (fingerprint_version, question_id, exact_hash, answer_hash,
             content_revision_hash, critical_math_hash, visible_image_hashes,
             tikz_hashes, figure_count, choice_count, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (values["fingerprint_version"], question_id, *(
            values[key]
            for key in (
                "exact_hash",
                "answer_hash",
                "content_revision_hash",
                "critical_math_hash",
                "visible_image_hashes",
                "tikz_hashes",
                "figure_count",
                "choice_count",
                "status",
            )
        )),
    )


@pytest.fixture()
def duplicate_db(tmp_path):
    db_path = tmp_path / "duplicates.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(BASE_SCHEMA)
    # 1/2 完全重复；3/4 仅解析缺失；5/6 仅排版差异；7 唯一题。
    _insert_question(conn, 1, content=STRICT_CONTENT, answer=STRICT_ANSWER)
    _insert_question(conn, 2, content=STRICT_CONTENT, answer=STRICT_ANSWER)
    _insert_question(conn, 3, content=ANSWER_MISMATCH_CONTENT, answer=ANSWER_ONLY)
    _insert_question(conn, 4, content=ANSWER_MISMATCH_CONTENT, tags="疑似重复#3")
    _insert_question(conn, 5, content=LAYOUT_CONTENT_A, answer=LAYOUT_ANSWER)
    _insert_question(conn, 6, content=LAYOUT_CONTENT_B, tags="疑似重复#5")
    _insert_question(conn, 7, content=UNIQUE_CONTENT)
    conn.execute("INSERT INTO paper_questions (paper_id, question_id) VALUES (1, 3)")
    conn.execute("INSERT INTO paper_questions (paper_id, question_id) VALUES (2, 4)")
    conn.execute(
        "INSERT INTO lesson_plans (id, question_refs) VALUES (1, ?)",
        (json.dumps({"question_id": 6}),),
    )
    conn.execute(
        "INSERT INTO lecture_drafts (id, config) VALUES (1, ?)",
        (
            json.dumps(
                {
                    "sections": [{"items": [{"question_id": 6}]}],
                    "extra_ids": [5],
                }
            ),
        ),
    )
    conn.commit()
    conn.close()
    return db_path


def _group_ids(report: dict, tier: str) -> list[list[int]]:
    return [
        [item["id"] for item in group["instances"]]
        for group in report["groups"]
        if group["tier"] == tier
    ]


def test_collect_report_classifies_every_tier(duplicate_db):
    report = scanner.collect_report(duplicate_db)

    assert report["fingerprint_version"] == 3
    assert report["total_questions"] == 7
    assert report["missing_fingerprints"] == 0
    assert _group_ids(report, "strict") == [[1, 2]]
    assert _group_ids(report, "answer_mismatch") == [[3, 4]]
    assert _group_ids(report, "layout_variant") == [[5, 6]]
    assert report["stats"]["strict_groups"] == 1
    assert report["stats"]["answer_mismatch_groups"] == 1
    assert report["stats"]["layout_variant_groups"] == 1
    assert report["stats"]["total_groups"] == 3
    assert report["stats"]["total_affected"] == 6
    assert report["stats"]["total_redundant"] == 3


def test_recommended_keeper_prefers_answered_and_scheme_b_warns(duplicate_db):
    report = scanner.collect_report(duplicate_db)
    by_tier = {group["tier"]: group for group in report["groups"]}

    assert by_tier["strict"]["recommended_keeper"] == 1
    assert by_tier["answer_mismatch"]["recommended_keeper"] == 3
    assert by_tier["layout_variant"]["recommended_keeper"] == 5

    plan_a = by_tier["answer_mismatch"]["plans"]["A"]
    assert plan_a["keeper"]["id"] == 3
    assert [item["id"] for item in plan_a["remove"]] == [4]
    assert plan_a["warnings"] == []

    plan_b = by_tier["answer_mismatch"]["plans"]["B"]
    assert plan_b["keeper"]["id"] == 4
    assert plan_b["warnings"], "方案 B 保留无解析副本时必须给出告警"


def test_references_are_collected_for_removed_duplicates(duplicate_db):
    report = scanner.collect_report(duplicate_db)
    by_tier = {group["tier"]: group for group in report["groups"]}

    layout_remove = by_tier["layout_variant"]["plans"]["A"]["remove"][0]
    assert layout_remove["id"] == 6
    assert "教案#1" in layout_remove["references"]
    assert "讲义草稿#1" in layout_remove["references"]

    layout_instances = {
        item["id"]: item for item in by_tier["layout_variant"]["instances"]
    }
    assert "讲义草稿#1" in layout_instances[5]["references"]

    answer_remove = by_tier["answer_mismatch"]["plans"]["A"]["remove"][0]
    assert answer_remove["references"] == ["试卷#2"]


def test_scan_connection_is_really_read_only(duplicate_db):
    before = duplicate_db.read_bytes()
    conn = scanner._connect_read_only(duplicate_db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM questions")
    finally:
        conn.close()
    scanner.collect_report(duplicate_db)
    assert duplicate_db.read_bytes() == before


def test_reference_rewriters_replace_and_dedupe_ids():
    mapping = {2: 1, 4: 3}

    entries, changed = scanner._replace_id_list([1, 2, 3, 4, 2], mapping)
    assert changed is True
    assert entries == [1, 3]

    lesson_raw = json.dumps(
        {"items": [{"question_id": 2, "stage": "导入"}, {"question_id": 1, "stage": "讲解"}]}
    )
    lesson_rewritten, lesson_changed = scanner._rewrite_lesson_refs(lesson_raw, mapping)
    assert lesson_changed is True
    assert json.loads(lesson_rewritten)["items"] == [
        {"question_id": 1, "stage": "导入"}
    ]

    lecture_raw = json.dumps(
        {
            "topics": [{"items": [{"question_id": 2, "role": "example"}]}],
            "sections": [{"items": [{"question_id": 4, "role": "variant"}]}],
            "extra_ids": [1, 2, 4],
        }
    )
    lecture_rewritten, lecture_changed = scanner._rewrite_lecture_config(
        lecture_raw, mapping
    )
    assert lecture_changed is True
    payload = json.loads(lecture_rewritten)
    assert payload["topics"][0]["items"][0]["question_id"] == 1
    assert payload["sections"][0]["items"][0]["question_id"] == 3
    assert payload["extra_ids"] == [1, 3]


def test_apply_cleanup_repoints_refs_and_deletes_duplicates(tmp_path):
    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from mathbank.database import (
        Base,
        ImportBatch,
        LectureDraft,
        LessonPlan,
        Paper,
        PaperQuestion,
        Question,
    )

    db_path = tmp_path / "apply.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all(
        [
            Question(
                id=1,
                content="题干",
                answer_markdown="【答案】A",
                created_at=datetime(2026, 1, 1),
            ),
            Question(id=2, content="题干（副本）", created_at=datetime(2026, 1, 2)),
            Paper(id=1, title="测试卷", total_score=10),
        ]
    )
    session.commit()
    session.add_all(
        [
            PaperQuestion(paper_id=1, question_id=2, order_index=1, score=10),
            LessonPlan(
                id=1,
                title="教案",
                question_refs=json.dumps(
                    {"items": [{"question_id": 2, "stage": "导入"}]}
                ),
            ),
            LectureDraft(
                id=1,
                title="讲义",
                config=json.dumps(
                    {
                        "topics": [
                            {"topic_id": "k_1", "name": "考点",
                             "items": [{"question_id": 2, "role": "example"}]}
                        ],
                        "extra_ids": [1, 2],
                    }
                ),
            ),
            ImportBatch(
                id=1,
                token="batch-1",
                question_ids="[1, 2]",
                question_count=2,
            ),
        ]
    )
    session.commit()
    session.close()

    result = scanner._apply_cleanup(
        db_path, [{"keeper": 1, "remove": 2, "tier": "strict"}]
    )

    assert result["affected_paper_ids"] == [1]
    session = sessionmaker(bind=engine)()
    try:
        assert session.query(Question).count() == 1
        paper_question = session.query(PaperQuestion).one()
        assert paper_question.question_id == 1
        assert json.loads(paper_question.snapshot_json)["id"] == 1
        assert session.get(Paper, 1).total_score == 10
        assert session.get(Question, 1).usage_count == 1

        refs = json.loads(session.get(LessonPlan, 1).question_refs)
        assert refs["items"][0]["question_id"] == 1

        config = json.loads(session.get(LectureDraft, 1).config)
        assert config["topics"][0]["items"][0]["question_id"] == 1
        assert config["extra_ids"] == [1]

        batch = session.get(ImportBatch, 1)
        assert json.loads(batch.question_ids) == [1]
        assert batch.question_count == 1
    finally:
        session.close()


def test_main_writes_markdown_and_json_reports(duplicate_db, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(scanner, "OUTPUT_DIR", tmp_path)

    exit_code = scanner.main(["--db", str(duplicate_db)])

    assert exit_code == 0
    markdown_files = list(tmp_path.glob("exact_duplicates_*.md"))
    json_files = list(tmp_path.glob("exact_duplicates_*.json"))
    assert len(markdown_files) == 1
    assert len(json_files) == 1
    markdown = markdown_files[0].read_text(encoding="utf-8")
    assert "仅排版 / 标点 / 等价数学写法不同" in markdown
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert payload["stats"]["total_groups"] == 3
    assert "报告已写入" in capsys.readouterr().out
