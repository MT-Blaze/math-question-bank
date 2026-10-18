"""第四录入方案：教材模式目录树（TOC）与跨页缝合的逻辑测试。

纯逻辑，不需要视觉模型 / 数据库：直接构造批次状态，跑
``build_plan`` / ``set_toc_result`` / ``build_catalog_tree`` / ``final_questions``。
"""

import os

import pytest

from mathbank import import_batch


def _cleanup(session_id):
    path = import_batch._session_path(session_id)
    if os.path.exists(path):
        os.remove(path)


def test_derive_chapter_section():
    assert import_batch._derive_chapter_section("例 1.1.1") == ("1", "1.1")
    assert import_batch._derive_chapter_section("习题2") == ("2", "")
    assert import_batch._derive_chapter_section("第1章") == ("1", "")
    assert import_batch._derive_chapter_section("1.1") == ("1", "1.1")
    assert import_batch._derive_chapter_section("") == ("", "")
    assert import_batch._derive_chapter_section("没有编号") == ("", "")


def test_build_plan_marks_toc():
    state = import_batch.create_session(
        "textbook", [{"name": "book.pdf", "size": 10, "pages": 3}]
    )
    sid = state["session_id"]
    try:
        fid = state["files"][0]["id"]
        state = import_batch.build_plan(sid, toc_pages={fid: [1]})
        toc_items = [it for it in state["items"] if it.get("is_toc")]
        assert [it["page"] for it in toc_items] == [1]
        assert state["toc_pages"] == [1]
        # 未命中页码的不是目录页
        assert sum(1 for it in state["items"] if not it.get("is_toc")) == 2
    finally:
        _cleanup(sid)


def _textbook_state(sid):
    return {
        "session_id": sid,
        "mode": "textbook",
        "revision": 1,
        "title": "测试教材",
        "files": [{"id": "f1", "name": "book.pdf", "size": 10, "pages": 3}],
        "items": [
            {
                "id": "i0",
                "file_id": "f1",
                "page": 1,
                "is_toc": True,
                "status": "recognized",
                "payload": {
                    "toc": [
                        {
                            "number": "1",
                            "title": "集合与函数",
                            "sections": [
                                {"number": "1.1", "title": "函数概念"},
                                {"number": "1.2", "title": "函数性质"},
                            ],
                        }
                    ]
                },
            },
            {
                "id": "i1",
                "file_id": "f1",
                "page": 2,
                "is_toc": False,
                "status": "recognized",
                "payload": {
                    "items": [
                        {
                            "label": "例 1.1.1",
                            "question_type": "detailed_answer",
                            "stem": "求 f(x)。",
                            "analysis": "解：",
                            "paper_question_no": "1.1.1",
                            "figures": ["a.png"],
                            "continues_next": True,
                        }
                    ]
                },
            },
            {
                "id": "i2",
                "file_id": "f1",
                "page": 3,
                "is_toc": False,
                "status": "recognized",
                "payload": {
                    "items": [
                        {
                            "label": "例 1.1.1",
                            "question_type": "detailed_answer",
                            "stem": "续：得 x=2。",
                            "analysis": "证毕。",
                            "paper_question_no": "1.1.1",
                            "figures": ["b.png"],
                            "continues_next": False,
                        }
                    ]
                },
            },
        ],
        "risks": [],
        "preview_hash": "",
        "committed": False,
        "batch_id": None,
    }


def test_catalog_tree_derives_and_stitches():
    sid = "test-textbook-catalog"
    try:
        import_batch._save(_textbook_state(sid))
        state = import_batch.build_catalog_tree(sid)

        # 目录树：章 1 -> 节 1.1，1 题
        assert state["catalog_tree"] == [
            {
                "title": "集合与函数",
                "sections": [{"title": "函数概念", "count": 1}],
            }
        ]

        # 跨页缝合：两条 label 相同的题合成一条
        assert len(state["questions"]) == 1
        q = state["questions"][0]
        assert q["category_chapter"] == "集合与函数"
        assert q["category_knowledge"] == "函数概念"
        assert q["content"] == "求 f(x)。\n续：得 x=2。"
        assert q["answer_markdown"] == "解：\n证毕。"
        assert q["image_paths"] == ["a.png", "b.png"]
        assert q["paper_question_no"] == "1.1.1"
        assert q["source"] == "测试教材"

        # preview_hash 随题列表变化，用于提交防陈旧
        assert state["preview_hash"]
        assert state["preview_hash"] == import_batch.compute_preview_hash(state)
    finally:
        _cleanup(sid)


def test_final_questions_textbook_uses_questions():
    sid = "test-textbook-final"
    try:
        import_batch._save(_textbook_state(sid))
        import_batch.build_catalog_tree(sid)
        state = import_batch._load(sid)
        qs = import_batch.final_questions(state)
        assert len(qs) == 1
        assert qs[0]["category_chapter"] == "集合与函数"
    finally:
        _cleanup(sid)


def test_final_questions_exam_flattens_without_catalog():
    sid = "test-exam-final"
    try:
        state = {
            "session_id": sid,
            "mode": "exam",
            "revision": 1,
            "title": "某卷",
            "files": [{"id": "f1", "name": "p.pdf", "size": 10, "pages": 1}],
            "items": [
                {
                    "id": "i1",
                    "file_id": "f1",
                    "page": 1,
                    "is_toc": False,
                    "status": "recognized",
                    "payload": {
                        "source": "某卷",
                        "items": [
                            {
                                "label": "",
                                "question_type": "single_choice",
                                "stem": "1+1=?",
                                "analysis": "略",
                                "paper_question_no": "1",
                                "figures": [],
                            }
                        ],
                    },
                }
            ],
            "risks": [],
            "preview_hash": "",
            "committed": False,
        }
        import_batch._save(state)
        # exam 模式：build_catalog_tree 不改动状态
        out = import_batch.build_catalog_tree(sid)
        assert "questions" not in out or not out.get("questions")
        qs = import_batch.final_questions(out)
        assert len(qs) == 1
        assert qs[0]["content"] == "1+1=?"
        assert qs[0]["source"] == "某卷"
        assert qs[0]["category_chapter"] == ""
        assert qs[0]["category_knowledge"] == ""
    finally:
        _cleanup(sid)


def test_evaluate_risks_skips_toc_pages():
    sid = "test-risk-skips-toc"
    try:
        state = _textbook_state(sid)
        # 目录页 payload 只有 toc、没有 items —— 不应被当成"空结果"风险
        state["items"][0]["payload"] = {"toc": []}
        import_batch._save(state)
        risks = import_batch.evaluate_risks(sid)
        assert all(r["item_id"] != "i0" for r in risks)
    finally:
        _cleanup(sid)
