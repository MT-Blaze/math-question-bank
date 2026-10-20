"""题库搜索框的 ID 精确查找契约。

用户可见行为：
  * ``#61`` 只看内部 ID 61，绝不退回全文模糊，也不被侧栏筛选条件挡掉；
  * 纯数字 ``61`` 先置顶内部 ID 命中，未命中才用历史题号映射兜底，最后才全文模糊；
  * 普通文字搜索保持原样。
"""

from mathbank.database import Question


def _add_question(db_session, content, **kwargs):
    question = Question(content=content, question_type="detailed_answer", **kwargs)
    db_session.add(question)
    db_session.commit()
    db_session.refresh(question)
    return question


def test_hash_prefixed_id_returns_only_that_question(client, db_session):
    target = _add_question(db_session, "目标题 461", category_compulsory="必修一")
    _add_question(db_session, "干扰题 61 出现在题干里")
    _add_question(db_session, "干扰题 161 也出现了")

    response = client.get("/api/questions", params={"q": f"#{target.id}", "page": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [item["id"] for item in payload["items"]] == [target.id]
    assert payload["search"]["mode"] == "id_exact"
    assert payload["search"]["exact_match"]["id"] == target.id
    assert payload["search"]["exact_match"]["kind"] == "id"


def test_hash_prefixed_lookup_ignores_active_filters(client, db_session):
    target = _add_question(db_session, "精确查找应忽略筛选", category_compulsory="必修一")

    response = client.get(
        "/api/questions",
        params={
            "q": f"#{target.id}",
            "page": 1,
            "compulsory": "不存在的册次",
            "qtype": "single_choice",
            "exclude_quality": "bad",
        },
    )

    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == target.id


def test_hashed_id_missing_returns_empty_instead_of_fuzzy_matches(client, db_session):
    _add_question(db_session, "正文里写着 99999 但不是 ID")

    response = client.get("/api/questions", params={"q": "#99999", "page": 1})

    payload = response.json()
    assert payload["total"] == 0
    assert payload["items"] == []
    assert payload["search"]["mode"] == "id_exact"
    assert payload["search"]["exact_match"] is None


def test_plain_number_pins_internal_id_first_then_keeps_fuzzy_results(client, db_session):
    target = _add_question(db_session, "这是一道普通题")
    fuzzy = _add_question(db_session, f"正文里提到了 {target.id} 这个数字")

    response = client.get("/api/questions", params={"q": str(target.id), "page": 1})

    payload = response.json()
    assert payload["items"][0]["id"] == target.id
    assert payload["search"]["mode"] == "id_first"
    assert payload["search"]["exact_match"]["id"] == target.id
    assert {item["id"] for item in payload["items"]} == {target.id, fuzzy.id}


def test_plain_number_falls_back_to_display_sequence_when_internal_id_is_gone(client, db_session):
    first = _add_question(db_session, "甲")
    removed = _add_question(db_session, "乙")
    third = _add_question(db_session, "丙")
    db_session.delete(removed)
    db_session.commit()

    response = client.get("/api/questions", params={"q": str(removed.id), "page": 1})

    payload = response.json()
    assert payload["items"][0]["id"] == third.id
    assert payload["search"]["mode"] == "number_first"
    assert payload["search"]["exact_match"]["kind"] == "seq_num"
    assert payload["search"]["exact_match"]["id"] == third.id


def test_plain_number_without_id_or_sequence_match_uses_fulltext(client, db_session):
    _add_question(db_session, "普通文字")
    fuzzy = _add_question(db_session, "这里出现了 999 三个数字")

    response = client.get("/api/questions", params={"q": "999", "page": 1})

    payload = response.json()
    assert [item["id"] for item in payload["items"]] == [fuzzy.id]
    assert payload["search"]["mode"] == "fulltext"
    assert payload["search"]["exact_match"] is None


def test_text_search_stays_fulltext(client, db_session):
    target = _add_question(db_session, "函数的单调性")

    response = client.get("/api/questions", params={"q": "单调性"})

    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert [item["id"] for item in response.json()] == [target.id]


def test_hashed_id_without_page_keeps_legacy_array_shape(client, db_session):
    target = _add_question(db_session, "无分页参数也兼容")

    response = client.get("/api/questions", params={"q": f"#{target.id}"})

    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert [item["id"] for item in response.json()] == [target.id]
