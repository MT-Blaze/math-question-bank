# -*- coding: utf-8 -*-
"""教案（以考向树为骨架）回归测试。

Covers: 考向骨架预览、由考向树生成教案、知识内容/思维导图/题目关联的存取、
旧版 question_refs 结构兼容、字段缺省与错误分支。
"""

from main import LOCAL_TOKEN

import pytest

COMPULSORY = "必修一"
CHAPTER = "1. 集合与常用逻辑用语"
KNOWLEDGE = "1.1 集合的概念"

TEST_CURRICULUM = {
    COMPULSORY: {
        CHAPTER: [
            {
                "name": KNOWLEDGE,
                "topics": [
                    {"id": "k_test_1", "name": "集合的表示方法", "level": "L1", "no": 1, "example": "列举法与描述法"},
                    {"id": "k_test_2", "name": "元素与集合的关系", "level": "L3", "no": 2, "example": "判断 1 ∈ A"},
                ],
            },
            {"name": "1.2 集合间的基本关系", "topics": []},
        ]
    }
}


@pytest.fixture(autouse=True)
def _fixed_curriculum(monkeypatch):
    """固定大纲，避免其他测试切换活跃版本导致断言随执行顺序漂移。"""

    from mathbank import routes_lesson_plans as lesson_routes

    monkeypatch.setattr(lesson_routes, "get_current_curriculum", lambda: TEST_CURRICULUM)
    yield


def _headers():
    return {"X-Local-Token": LOCAL_TOKEN}


def _section_topics(client, knowledge=KNOWLEDGE):
    return client.get(
        "/api/lesson-plans/section-topics",
        params={"compulsory": COMPULSORY, "chapter": CHAPTER, "knowledge": knowledge},
    )


def _create_from_section(client, **overrides):
    payload = {
        "title": "",
        "grade": "g1",
        "compulsory": COMPULSORY,
        "chapter": CHAPTER,
        "knowledge": KNOWLEDGE,
    }
    payload.update(overrides)
    return client.post("/api/lesson-plans/from-section", headers=_headers(), json=payload)


# ---------------------------------------------------------------- 考向骨架


def test_section_topics_returns_kaoxiang_tree(client):
    resp = _section_topics(client)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["blocks"]) == 2
    for block in data["blocks"]:
        assert block["title"]
        assert "content" in block and block["content"] == ""
        assert block["topic_id"].startswith("k_test_")
    levels = [b["level"] for b in data["blocks"]]
    assert levels == ["L1", "L3"]
    assert data["blocks"][0]["example"] == "列举法与描述法"
    assert data["mindmap"]["root"]["text"] == KNOWLEDGE
    group_texts = [c["text"] for c in data["mindmap"]["root"]["children"]]
    # 分组标题跟随配置的难度阶梯标签
    from mathbank.curriculums import DEFAULT_DIFFICULTIES

    labels = dict((d["value"], d["label"]) for d in DEFAULT_DIFFICULTIES)
    assert labels["L1"] in group_texts
    assert labels["L3"] in group_texts
    assert sum(data["counts"].values()) == len(data["blocks"])
    assert data["counts"] == {"L1": 1, "L3": 1}


def test_section_topics_rejects_unknown_section(client):
    resp = _section_topics(client, knowledge="9.9 不存在的小节")
    assert resp.status_code == 404


def test_from_section_requires_section_selection(client):
    resp = client.post(
        "/api/lesson-plans/from-section",
        headers=_headers(),
        json={"compulsory": COMPULSORY, "chapter": "", "knowledge": ""},
    )
    assert resp.status_code == 400


def test_from_section_scaffolds_blocks_and_mindmap(client):
    resp = _create_from_section(client)
    assert resp.status_code == 200
    plan = resp.json()
    assert plan["id"]
    assert plan["title"] == KNOWLEDGE + " 教案"
    assert plan["status"] == "draft"
    assert plan["compulsory"] == COMPULSORY
    assert plan["knowledge"] == KNOWLEDGE

    blocks = plan["knowledge_content"]["blocks"]
    assert blocks
    assert blocks[0]["id"].startswith("kx_")
    assert plan["mindmap"]["root"]["text"] == KNOWLEDGE
    assert plan["question_refs"] == {"items": []}
    assert plan["kaoxiang_counts"]


def test_from_section_uses_custom_title(client):
    resp = _create_from_section(client, title="集合入门课")
    assert resp.status_code == 200
    assert resp.json()["title"] == "集合入门课"


# ---------------------------------------------------------------- 存取往返


def test_lesson_plan_round_trip(client):
    plan = _create_from_section(client).json()
    plan_id = plan["id"]
    blocks = plan["knowledge_content"]["blocks"]
    blocks[0]["content"] = "元素的三个特性。"

    mindmap = plan["mindmap"]
    mindmap["root"]["children"][0]["children"].append({"text": "手写节点"})

    payload = {
        "objectives": "理解集合概念。",
        "knowledge_content": {"blocks": blocks},
        "mindmap": mindmap,
        "question_refs": {
            "items": [
                {
                    "block_id": blocks[0]["id"],
                    "question_id": None,
                    "content": "判断：{1,1,2} 能否构成集合？",
                }
            ]
        },
        "teaching_process": {"导入": "从班级分组引入。", "新知讲解": "", "例题精讲": "",
                             "课堂练习": "", "课堂小结": "", "作业布置": ""},
    }
    put = client.put("/api/lesson-plans/%d" % plan_id, headers=_headers(), json=payload)
    assert put.status_code == 200

    saved = client.get("/api/lesson-plans/%d" % plan_id).json()
    assert saved["objectives"] == "理解集合概念。"
    assert saved["knowledge_content"]["blocks"][0]["content"] == "元素的三个特性。"
    assert saved["teaching_process"]["导入"] == "从班级分组引入。"
    assert len(saved["question_refs"]["items"]) == 1
    assert saved["question_refs"]["items"][0]["block_id"] == blocks[0]["id"]

    texts = []

    def walk(node):
        texts.append(node["text"])
        for child in node.get("children", []):
            walk(child)

    walk(saved["mindmap"]["root"])
    assert "手写节点" in texts


def test_lesson_plan_normalizes_legacy_question_refs(client):
    plan_id = _create_from_section(client).json()["id"]
    resp = client.put(
        "/api/lesson-plans/%d" % plan_id,
        headers=_headers(),
        json={"question_refs": {"例题精讲": [11, 12]}},
    )
    assert resp.status_code == 200
    items = resp.json()["question_refs"]["items"]
    assert len(items) == 2
    assert all(item["stage"] == "例题精讲" for item in items)
    assert sorted(item["question_id"] for item in items) == [11, 12]


def test_update_only_touches_provided_fields(client):
    plan = _create_from_section(client, title="保持标题").json()
    plan_id = plan["id"]
    resp = client.put(
        "/api/lesson-plans/%d" % plan_id, headers=_headers(), json={"status": "published"}
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["status"] == "published"
    assert updated["title"] == "保持标题"
    assert updated["knowledge_content"]["blocks"], "未提交的字段应保持不变"


def test_lesson_plan_allows_manual_blocks_without_section(client):
    created = client.post(
        "/api/lesson-plans",
        headers=_headers(),
        json={
            "title": "手写教案",
            "knowledge_content": {
                "blocks": [
                    {"id": "kx_manual", "no": 1, "level": "L3", "title": "含参讨论", "content": "分类讨论。"}
                ]
            },
            "mindmap": {"root": {"text": "手写教案", "children": [{"text": "L3", "children": [{"text": "含参讨论"}]}]}},
        },
    )
    assert created.status_code == 200
    plan = created.json()
    assert plan["knowledge_content"]["blocks"][0]["title"] == "含参讨论"
    assert plan["mindmap"]["root"]["children"][0]["text"] == "L3"


def test_lesson_plan_defaults_title_when_missing(client):
    created = client.post("/api/lesson-plans", headers=_headers(), json={})
    assert created.status_code == 200
    assert created.json()["title"] == "未命名教案"


def test_lesson_plan_delete_and_missing_lookup(client):
    plan_id = _create_from_section(client).json()["id"]
    assert client.delete("/api/lesson-plans/%d" % plan_id, headers=_headers()).status_code == 200
    assert client.get("/api/lesson-plans/%d" % plan_id).status_code == 404
    assert client.delete("/api/lesson-plans/%d" % plan_id, headers=_headers()).status_code == 404


def test_lesson_plan_status_filter(client):
    created = _create_from_section(client).json()
    client.put("/api/lesson-plans/%d" % created["id"], headers=_headers(), json={"status": "published"})
    published = client.get("/api/lesson-plans", params={"status": "published"}).json()
    drafts = client.get("/api/lesson-plans", params={"status": "draft"}).json()
    assert len(published) == 1
    assert len(drafts) == 0
