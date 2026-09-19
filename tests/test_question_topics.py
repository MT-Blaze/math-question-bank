"""Integration tests for 一题多考点 (question_topics).

Covers: create/update with extra_topics, detail/list topics payloads,
AND/OR combined-kaoxiang filtering, and legacy compatibility when the
extra_topics field is absent.
"""

import json

from main import LOCAL_TOKEN
from mathbank.database import QuestionTopic


def _headers():
    return {"X-Local-Token": LOCAL_TOKEN}


def _payload(content, **overrides):
    payload = {
        "content": content,
        "question_type": "single_choice",
        "category_compulsory": "专题A",
        "category_chapter": "组1",
        "category_knowledge": "考向1",
        "difficulty": "medium",
        "source": "多考点测试",
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


def test_create_persists_primary_and_extra_kaoxiang(client, db_session):
    extra = json.dumps([
        {"compulsory": "专题B", "chapter": "组2", "knowledge": "考向2", "topic": ""},
        {"compulsory": "专题C", "chapter": "组3", "knowledge": "考向3", "topic": "细分考点"},
    ], ensure_ascii=False)
    created = _create(
        client,
        "设 $x=1$，求一题多考点的基础题。",
        extra_topics=extra,
        topic="主考点",
    )
    assert created.status_code == 200
    question = created.json()["question"]
    assert question["topic"] == "主考点"
    assert [t["sort_order"] for t in question["topics"]] == [0, 1, 2]
    assert question["topics"][0]["knowledge"] == "考向1"
    assert question["topics"][1]["knowledge"] == "考向2"
    assert question["topics"][2]["topic"] == "细分考点"

    stored = (
        db_session.query(QuestionTopic)
        .filter_by(question_id=question["id"])
        .order_by(QuestionTopic.sort_order.asc())
        .all()
    )
    assert len(stored) == 3
    assert stored[0].compulsory == "专题A"
    # 考点名已不在库中：题目只存稳定的 topic_id（无名称快照）
    assert stored[2].topic_id
    assert stored[2].topic_id == question["topics"][2]["topic_id"]
    # 响应仍以当前名呈现（展示翻译层）
    assert question["topics"][2]["topic"] == "细分考点"


def test_detail_returns_topics_and_update_replaces_them(client, db_session):
    created = _create(
        client,
        "设 $a+b$，更新附加考向的题目。",
        extra_topics=json.dumps([
            {"compulsory": "专题B", "chapter": "组2", "knowledge": "考向2", "topic": ""},
        ], ensure_ascii=False),
    )
    qid = created.json()["question"]["id"]

    detail = client.get(f"/api/questions/{qid}")
    assert detail.status_code == 200
    assert [t["sort_order"] for t in detail.json()["topics"]] == [0, 1]

    updated = client.put(
        f"/api/questions/{qid}",
        data=_payload(
            "设 $a+b$，更新附加考向的题目。",
            category_compulsory="专题A",
            category_chapter="组1",
            category_knowledge="考向1",
            extra_topics=json.dumps([
                {"compulsory": "专题D", "chapter": "组4", "knowledge": "考向4", "topic": ""},
                {"compulsory": "专题E", "chapter": "组5", "knowledge": "考向5", "topic": ""},
            ], ensure_ascii=False),
        ),
        headers=_headers(),
    )
    assert updated.status_code == 200
    topics = updated.json()["question"]["topics"]
    assert [t["knowledge"] for t in topics] == ["考向1", "考向4", "考向5"]

    # Clearing extra_topics leaves only the primary slot
    cleared = client.put(
        f"/api/questions/{qid}",
        data=_payload(
            "设 $a+b$，更新附加考向的题目。",
            category_compulsory="专题A",
            category_chapter="组1",
            category_knowledge="考向1",
            extra_topics="[]",
        ),
        headers=_headers(),
    )
    assert cleared.status_code == 200
    assert [t["knowledge"] for t in cleared.json()["question"]["topics"]] == ["考向1"]


def test_legacy_create_without_extra_topics_still_writes_primary(client, db_session):
    created = _create(client, "设 $m$ 为整数，旧客户端兼容题。")
    assert created.status_code == 200
    question = created.json()["question"]
    assert [t["sort_order"] for t in question["topics"]] == [0]
    assert question["topics"][0]["knowledge"] == "考向1"


def test_list_filter_and_and_or(client, db_session):
    # Question A: primary 专题A/组1/考向1 + extra 专题B/组2/考向2
    _create(
        client,
        "同时命中考向1与考向2的题目 A。",
        category_compulsory="专题A",
        category_chapter="组1",
        category_knowledge="考向1",
        extra_topics=json.dumps([
            {"compulsory": "专题B", "chapter": "组2", "knowledge": "考向2", "topic": ""},
        ], ensure_ascii=False),
    )
    # Question B: only 专题B/组2/考向2
    _create(
        client,
        "只命中考向2的题目 B。",
        category_compulsory="专题B",
        category_chapter="组2",
        category_knowledge="考向2",
        extra_topics="[]",
    )
    # Question C: only 专题C/组3/考向3
    _create(
        client,
        "只命中考向3的题目 C。",
        category_compulsory="专题C",
        category_chapter="组3",
        category_knowledge="考向3",
        extra_topics="[]",
    )

    filters = json.dumps([
        {"compulsory": "专题A", "chapter": "组1", "knowledge": "考向1", "topic": ""},
        {"compulsory": "专题B", "chapter": "组2", "knowledge": "考向2", "topic": ""},
    ], ensure_ascii=False)

    # AND: only A matches both
    and_resp = client.get(
        "/api/questions",
        params={"extra_topics": filters, "topic_mode": "and", "page": 1, "page_size": 20},
    )
    assert and_resp.status_code == 200
    and_ids = {item["id"] for item in and_resp.json()["items"]}
    contents_a = {
        item["content"] for item in and_resp.json()["items"]
    }
    assert len(and_ids) == 1
    assert "题目 A" in next(iter(contents_a))
    # The AND item carries its topics payload
    and_item = and_resp.json()["items"][0]
    assert len(and_item["topics"]) == 2

    # OR: A and B match (C does not)
    or_resp = client.get(
        "/api/questions",
        params={"extra_topics": filters, "topic_mode": "or", "page": 1, "page_size": 20},
    )
    assert or_resp.status_code == 200
    or_contents = {item["content"] for item in or_resp.json()["items"]}
    assert any("题目 A" in c for c in or_contents)
    assert any("题目 B" in c for c in or_contents)
    assert not any("题目 C" in c for c in or_contents)


def test_list_legacy_no_extra_topics_param_unchanged(client, db_session):
    _create(client, "无多考点筛选时的普通题。")
    resp = client.get(
        "/api/questions",
        params={"page": 1, "page_size": 20},
    )
    assert resp.status_code == 200
    assert "items" in resp.json()
    for item in resp.json()["items"]:
        assert isinstance(item.get("topics"), list)
