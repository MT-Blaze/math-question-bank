"""PLAN「查看整套试卷」后端覆盖：按卷聚合 / 质量标签 / 按原题号排序。"""

from main import LOCAL_TOKEN
from mathbank.database import Question


def _post_question(client, headers, **kw):
    data = {
        "content": kw.get("content", "题目内容 $x$。"),
        "question_type": kw.get("question_type", "single_choice"),
        "difficulty": kw.get("difficulty", "medium"),
        "source": kw.get("source", ""),
        "answer_markdown": kw.get("answer_markdown", "解析 $1$。"),
        "image_paths": "[]",
        "paper_question_no": kw.get("paper_question_no", ""),
        "tags": kw.get("tags", ""),
    }
    res = client.post("/api/questions", data=data, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["question"]["id"]


def test_papers_aggregation_counts(client):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    _post_question(client, headers, source="2025哈三中一模", paper_question_no="2")
    _post_question(client, headers, source="2025哈三中一模", paper_question_no="1")
    _post_question(client, headers, source="2024省实验期末", paper_question_no="3")

    res = client.get("/api/questions/papers")
    assert res.status_code == 200
    groups = {g["source"]: g["count"] for g in res.json()["groups"]}
    assert groups.get("2025哈三中一模") == 2
    assert groups.get("2024省实验期末") == 1
    # 无 source 的题不应出现在分组里
    _post_question(client, headers, source="")
    res2 = client.get("/api/questions/papers")
    assert res2.json()["groups"]


def test_quality_tag_cycle_and_default_excludes(client):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    qid = _post_question(client, headers, source="卷X", paper_question_no="1")

    # 默认普通（0），无质量 token
    detail = client.get(f"/api/questions/{qid}", headers=headers).json()
    assert detail["quality"] == 0

    # 设好题 -> 经典 -> 烂题
    r = client.post(f"/api/questions/{qid}/quality", json={"level": "good"}, headers=headers)
    assert r.json()["level"] == 1
    row = client.get(f"/api/questions/{qid}", headers=headers).json()
    assert row["quality"] == 1
    assert "好题" in (row["tags"] or "")

    r = client.post(f"/api/questions/{qid}/quality", json={"level": 2}, headers=headers)
    assert r.json()["level"] == 2
    row = client.get(f"/api/questions/{qid}", headers=headers).json()
    assert "好题" not in (row["tags"] or "") and "经典" in (row["tags"] or "")

    # 回到普通：token 被清空
    r = client.post(f"/api/questions/{qid}/quality", json={"level": "normal"}, headers=headers)
    assert r.json()["level"] == 0
    row = client.get(f"/api/questions/{qid}", headers=headers).json()
    assert row["quality"] == 0


def test_exclude_quality_bad(client):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    good = _post_question(client, headers, source="卷Z", paper_question_no="1")
    bad = _post_question(client, headers, source="卷Z", paper_question_no="2")
    client.post(f"/api/questions/{bad}/quality", json={"level": "bad"}, headers=headers)

    base = {"source": "卷Z", "page": 1, "page_size": 50}
    # 不带 exclude_quality：烂题仍在
    all_items = client.get("/api/questions", params=base, headers=headers).json()["items"]
    ids_all = {it["id"] for it in all_items}
    assert good in ids_all and bad in ids_all

    # exclude_quality=bad：隐藏烂题
    filtered = client.get(
        "/api/questions", params={**base, "exclude_quality": "bad"}, headers=headers
    ).json()["items"]
    ids_f = {it["id"] for it in filtered}
    assert good in ids_f and bad not in ids_f



def test_sort_by_paper_no(client):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    _post_question(client, headers, source="卷Y", paper_question_no="12", content="第十二题")
    _post_question(client, headers, source="卷Y", paper_question_no="2", content="第二题")
    _post_question(client, headers, source="卷Y", paper_question_no="1", content="第一题")

    res = client.get("/api/questions", params={"source": "卷Y", "sort": "paper_no"})
    assert res.status_code == 200
    nos = [it["paper_question_no"] for it in res.json()]
    assert nos == ["1", "2", "12"], nos
