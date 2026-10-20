# -*- coding: utf-8 -*-
"""讲义模块（lecture）API 回归测试：设置/知识块/草稿/预览组装。"""

from sqlalchemy import text

from main import LOCAL_TOKEN
from mathbank.database import Question


def _headers():
    return {"X-Local-Token": LOCAL_TOKEN}


def _seed(db_session):
    db_session.add_all([
        Question(content="已知集合 $A=\\{x\\mid x^2<4\\}$，求 $A$。", question_type="detailed_answer",
                 category_compulsory="必修一", category_chapter="第一章 集合与常用逻辑用语",
                 category_knowledge="1.1 集合的概念", applicable_grade="g1",
                 difficulty="basic", answer_markdown="由 $x^2<4$ 得 $-2<x<2$，故 $A=(-2,2)$。"),
        Question(content="设集合 $A=\\{1,2,3\\}$，$B=\\{2,3,4\\}$，则 $A\\cap B=$（   ）",
                 question_type="single_choice",
                 category_compulsory="必修一", category_chapter="第一章 集合与常用逻辑用语",
                 category_knowledge="1.1 集合的概念", applicable_grade="g1",
                 difficulty="basic", answer_markdown="$\\{2,3\\}$"),
    ])
    db_session.commit()
    return db_session.query(Question.id).order_by(Question.id.asc()).all()


def test_lecture_tables_exist(db_session):
    """v20 迁移必须创建三张讲义表。"""
    rows = db_session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'lecture%'")
    ).fetchall()
    names = {r[0] for r in rows}
    assert {"lecture_settings", "lecture_notes", "lecture_drafts"} <= names


def test_lecture_settings_get_and_put(client, db_session):
    r = client.get("/api/lecture/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["cover_brand"] == "MT-Blaze 的数学空间"
    assert body["bound_mode"] in ("loose", "bound")

    r = client.put("/api/lecture/settings", headers=_headers(),
                   json={"cover_title": "第一章 集合", "bound_mode": "bound"})
    assert r.status_code == 200
    assert r.json()["cover_title"] == "第一章 集合"
    assert r.json()["bound_mode"] == "bound"


def test_lecture_quotes_and_cover_bg(client):
    r = client.get("/api/lecture/quotes")
    assert r.status_code == 200
    pool = r.json()["quote_pool"]
    assert isinstance(pool, list) and len(pool) >= 5

    r = client.get("/api/lecture/cover-bg")
    assert r.status_code == 200
    assert isinstance(r.json()["cover_bg"], dict)


def test_lecture_notes_put_get_delete(client, db_session):
    payload = {
        "note_type": "knowledge",
        "compulsory": "必修一",
        "chapter": "第一章 集合与常用逻辑用语",
        "knowledge": "1.1 集合的概念",
        "content": "<p>集合是<strong>确定性</strong>对象的整体。</p>",
    }
    r = client.put("/api/lecture/notes", headers=_headers(), json=payload)
    assert r.status_code == 200
    assert "集合" in r.json()["content"]

    r = client.get("/api/lecture/notes?note_type=knowledge&compulsory=" + "必修一")
    assert r.status_code == 200
    rows = r.json()
    assert any("集合" in row["content"] for row in rows)

    r = client.put("/api/lecture/notes", headers=_headers(),
                   json={**payload, "note_type": "pit", "topic_id": "k_abc123"})
    assert r.status_code == 200

    r = client.delete("/api/lecture/notes", headers=_headers(), params={
        "note_type": "knowledge", "compulsory": "必修一",
        "chapter": "第一章 集合与常用逻辑用语", "knowledge": "1.1 集合的概念",
    })
    assert r.status_code == 200


def test_lecture_notes_rejects_bad_type(client):
    r = client.put("/api/lecture/notes", headers=_headers(), json={
        "note_type": "bad", "compulsory": "必修一", "chapter": "x", "knowledge": "y",
    })
    assert r.status_code == 400


def test_lecture_preview(client, db_session):
    ids = [q.id for q in _seed(db_session)]
    topics = [{"topic_id": "", "name": "集合的概念", "items": [
        {"question_id": ids[0], "role": "example"},
        {"question_id": ids[1], "role": "variant"},
    ]}]
    r = client.post("/api/lecture/preview", headers=_headers(), json={
        "compulsory": "必修一", "chapter": "第一章 集合与常用逻辑用语",
        "knowledge": "1.1 集合的概念", "topics": topics, "extra_ids": [],
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["questions"]) == 2
    assert body["questions"][0]["answer"]
    assert body["extra_ids"] == []
    assert isinstance(body["quote_pool"], list)
    assert isinstance(body["cover_bg"], dict)


def test_lecture_drafts_crud(client, db_session):
    payload = {
        "title": "集合1.1讲义", "grade": "g1",
        "compulsory": "必修一", "chapter": "第一章 集合与常用逻辑用语",
        "knowledge": "1.1 集合的概念",
        "config": {"topics": [], "extra_ids": [1]},
    }
    r = client.post("/api/lecture/drafts", headers=_headers(), json=payload)
    assert r.status_code == 200
    did = r.json()["id"]

    r = client.get("/api/lecture/drafts")
    assert r.status_code == 200
    assert any(d["id"] == did for d in r.json())

    r = client.get(f"/api/lecture/drafts/{did}")
    assert r.status_code == 200
    assert r.json()["title"] == "集合1.1讲义"

    r = client.put(f"/api/lecture/drafts/{did}", headers=_headers(),
                   json={**payload, "title": "集合1.1讲义v2"})
    assert r.status_code == 200
    assert r.json()["title"] == "集合1.1讲义v2"

    r = client.delete(f"/api/lecture/drafts/{did}", headers=_headers())
    assert r.status_code == 200

    r = client.get(f"/api/lecture/drafts/{did}")
    assert r.status_code == 404


# ============ 一题多来源 (v21) ============

def test_question_sources_tables_exist(db_session):
    """v21 迁移必须创建 question_sources 表。"""
    rows = db_session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='question_sources'")
    ).fetchall()
    assert len(rows) == 1


def test_question_sources_crud(client, db_session):
    ids = [q.id for q in _seed(db_session)]
    qid = ids[0]

    r = client.get(f"/api/questions/{qid}/sources")
    assert r.status_code == 200
    assert r.json()["sources"] == []

    r = client.post(f"/api/questions/{qid}/sources", headers=_headers(), json={
        "year": "2026", "region": "哈三中", "exam_type": "期末", "paper_title": "2026届哈三中高二期末"
    })
    assert r.status_code == 200
    body = r.json()
    assert body["source"]["text"] == "2026年哈三中期末"
    assert len(body["sources"]) == 1

    r = client.get(f"/api/questions/{qid}/sources")
    assert r.status_code == 200
    srcs = r.json()["sources"]
    assert len(srcs) == 1
    assert srcs[0]["text"] == "2026年哈三中期末"

    r = client.delete(f"/api/questions/{qid}/sources/{srcs[0]['id']}", headers=_headers())
    assert r.status_code == 200
    assert r.json()["sources"] == []


def test_question_sources_question_no_is_stored_and_mirrored(client, db_session):
    """原卷题号属于来源关系：写入来源后，主表字段只镜像主来源。"""
    ids = [q.id for q in _seed(db_session)]
    qid = ids[0]

    r = client.post(f"/api/questions/{qid}/sources", headers=_headers(), json={
        "year": "2026", "region": "哈三中", "exam_type": "三模",
        "paper_title": "2026届哈三中高三三模", "question_no": "18",
    })
    assert r.status_code == 200
    assert r.json()["source"]["question_no"] == "18"

    db_session.expire_all()
    assert db_session.get(Question, qid).paper_question_no == "18"
    detail = client.get(f"/api/questions/{qid}").json()
    assert detail["paper_question_no"] == "18"
    assert detail["sources"][0]["question_no"] == "18"


def test_question_source_rejects_overlong_question_no(client, db_session):
    ids = [q.id for q in _seed(db_session)]
    r = client.post(f"/api/questions/{ids[0]}/sources", headers=_headers(), json={
        "year": "2026", "region": "哈三中", "exam_type": "三模", "question_no": "x" * 51,
    })
    assert r.status_code == 400


def test_sync_question_sources_round_trips_question_no_and_mirrors_primary(db_session):
    from mathbank.sources_helper import sync_question_sources

    question = Question(content="来源题号往返", question_type="detailed_answer")
    db_session.add(question)
    db_session.commit()

    sync_question_sources(db_session, question.id, [
        {"year": "2025", "region": "省实验", "exam_type": "期末", "question_no": "7"},
        {"year": "2026", "region": "哈三中", "exam_type": "三模", "question_no": "18"},
    ])
    db_session.commit()
    db_session.expire_all()

    assert db_session.get(Question, question.id).paper_question_no == "18", (
        "主表镜像必须取排序后的主来源（三模优先于期末）"
    )
    from mathbank.database import QuestionSource
    rows = db_session.query(QuestionSource).filter_by(question_id=question.id).all()
    assert sorted((row.region, row.question_no) for row in rows) == [
        ("哈三中", "18"), ("省实验", "7"),
    ]


def test_question_sources_sorting(client, db_session):
    """排序：高考 > 模考 > 联考 > 校考；同优先级年份新在前。"""
    ids = [q.id for q in _seed(db_session)]
    qid = ids[1]
    cases = [
        {"year": "2024", "region": "哈尔滨", "exam_type": "期末"},
        {"year": "2026", "region": "哈三中", "exam_type": "三模"},
        {"year": "2025", "region": "东北三省三校", "exam_type": "联考"},
        {"year": "2026", "region": "全国甲卷", "exam_type": "高考"},
    ]
    for c in cases:
        r = client.post(f"/api/questions/{qid}/sources", headers=_headers(), json=c)
        assert r.status_code == 200

    r = client.get(f"/api/questions/{qid}/sources")
    texts = [s["text"] for s in r.json()["sources"]]
    assert texts[0] == "2026年全国甲卷高考"          # 高考优先
    assert texts[1] == "2026年哈三中三模"            # 模考，同年新在前
    assert texts[2] == "2025年东北三省三校联考"       # 联考
    assert texts[3] == "2024年哈尔滨期末"            # 校考


def test_question_sources_validation(client, db_session):
    ids = [q.id for q in _seed(db_session)]
    qid = ids[0]
    r = client.post(f"/api/questions/{qid}/sources", headers=_headers(), json={
        "year": "", "region": "", "exam_type": ""
    })
    assert r.status_code == 400
    r = client.post(f"/api/questions/{qid}/sources", headers=_headers(), json={
        "year": "abc", "region": "哈三中", "exam_type": "期末"
    })
    assert r.status_code == 400


def test_lecture_preview_includes_sources(client, db_session):
    ids = [q.id for q in _seed(db_session)]
    qid = ids[0]
    client.post(f"/api/questions/{qid}/sources", headers=_headers(), json={
        "year": "2026", "region": "哈三中", "exam_type": "三模", "paper_title": "2026届哈三中高三三模"
    })
    topics = [{"topic_id": "", "name": "集合的概念", "items": [
        {"question_id": qid, "role": "example"},
    ]}]
    r = client.post("/api/lecture/preview", headers=_headers(), json={
        "compulsory": "必修一", "chapter": "第一章 集合与常用逻辑用语",
        "knowledge": "1.1 集合的概念", "topics": topics, "extra_ids": [],
    })
    assert r.status_code == 200
    body = r.json()
    q = body["questions"][0]
    assert q["sources"] == [{
        "id": q["sources"][0]["id"],
        "question_id": qid,
        "year": "2026",
        "region": "哈三中",
        "province": "",
        "city": "",
        "grade": "",
        "semester": "",
        "exam_type": "三模",
        "paper_title": "2026届哈三中高三三模",
        "question_no": "",
        "text": "2026年哈三中三模",
        "school_id": "",
        "sort_order": 1,
    }]


# ============ 讲义模块缺陷回归 ============


def test_lecture_notes_knowledge_filter_is_exact(client, db_session):
    """knowledge 过滤必须整段精确匹配，不得把「集合」误命中到「集合的基本运算」。"""
    for know in ("集合", "集合的基本运算"):
        r = client.put("/api/lecture/notes", headers=_headers(), json={
            "note_type": "knowledge", "compulsory": "必修一",
            "chapter": "第一章 集合与常用逻辑用语", "knowledge": know,
            "content": f"<p>{know}</p>",
        })
        assert r.status_code == 200

    r = client.get("/api/lecture/notes", params={
        "note_type": "knowledge", "compulsory": "必修一",
        "chapter": "第一章 集合与常用逻辑用语", "knowledge": "集合",
    })
    assert r.status_code == 200
    keys = [row["note_key"] for row in r.json()]
    assert keys == ["knowledge:必修一:第一章 集合与常用逻辑用语:集合"]


def test_lecture_notes_topic_filter_ignores_like_wildcards(client, db_session):
    """topic_id 过滤不得把下划线当 SQL LIKE 通配符（k_abc 不得命中 kzabc）。"""
    r = client.put("/api/lecture/notes", headers=_headers(), json={
        "note_type": "pit", "compulsory": "必修一",
        "chapter": "第一章 集合与常用逻辑用语", "knowledge": "1.1 集合的概念",
        "topic_id": "kzabc123", "content": "<p>x</p>",
    })
    assert r.status_code == 200

    r = client.get("/api/lecture/notes", params={
        "note_type": "pit", "compulsory": "必修一",
        "chapter": "第一章 集合与常用逻辑用语", "knowledge": "1.1 集合的概念",
        "topic_id": "k_abc123",
    })
    assert r.status_code == 200
    assert r.json() == []


def test_lecture_preview_reports_missing_ids(client, db_session):
    """编排里引用了已删除题目时，preview 必须如实返回 missing_ids 供前端提示。"""
    ids = [q.id for q in _seed(db_session)]
    missing_one, missing_two = 987654, 987655
    topics = [{"topic_id": "", "name": "集合的概念", "items": [
        {"question_id": ids[0], "role": "example"},
        {"question_id": missing_one, "role": "example"},
    ]}]
    r = client.post("/api/lecture/preview", headers=_headers(), json={
        "compulsory": "必修一", "chapter": "第一章 集合与常用逻辑用语",
        "knowledge": "1.1 集合的概念", "topics": topics, "extra_ids": [missing_two],
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["questions"]) == 1
    assert body["missing_ids"] == [missing_one, missing_two]


def test_lecture_draft_archive_and_restore(client, db_session):
    """复制为新版依赖的契约：单独 PUT status 即可归档/恢复，其余字段不动。"""
    payload = {
        "title": "必修一 1.1 讲义（2026届）", "grade": "g1",
        "compulsory": "必修一", "chapter": "第一章", "knowledge": "1.1",
        "config": {"topics": [], "extra_ids": [], "series": "sync", "drawer": "必修一", "year": "2026"},
    }
    did = client.post("/api/lecture/drafts", headers=_headers(), json=payload).json()["id"]

    r = client.put(f"/api/lecture/drafts/{did}", headers=_headers(), json={"status": "archived"})
    assert r.status_code == 200
    assert r.json()["status"] == "archived"
    assert r.json()["title"] == payload["title"]          # patch 语义：未传字段不动
    assert r.json()["config"]["drawer"] == "必修一"

    r = client.get("/api/lecture/drafts", params={"status": "archived"})
    assert [d["id"] for d in r.json()] == [did]

    r = client.put(f"/api/lecture/drafts/{did}", headers=_headers(), json={"status": "draft"})
    assert r.json()["status"] == "draft"


def test_clone_produces_new_year_and_archives_old(client, db_session):
    """模拟前端 cloneDraft 完整链路：新行带 cloned_from，旧行归档。"""
    payload = {
        "title": "一轮 函数 讲义", "grade": "g3",
        "compulsory": "必修一", "chapter": "第三章", "knowledge": "函数的应用",
        "config": {"topics": [], "extra_ids": [], "series": "review", "drawer": "一轮复习", "year": "2026"},
    }
    old = client.post("/api/lecture/drafts", headers=_headers(), json=payload).json()

    new_payload = dict(payload)
    new_cfg = dict(old["config"])
    new_cfg.update({"year": "2027", "cloned_from": old["id"]})
    new_payload["config"] = new_cfg
    new = client.post("/api/lecture/drafts", headers=_headers(), json=new_payload).json()
    assert new["id"] != old["id"]
    assert new["config"]["cloned_from"] == old["id"]
    assert new["config"]["year"] == "2027"

    client.put(f"/api/lecture/drafts/{old['id']}", headers=_headers(), json={"status": "archived"})
    listed = client.get("/api/lecture/drafts").json()
    assert any(d["id"] == new["id"] for d in listed)


def _make_question_form(extra=None):
    base = {
        "content": "设集合 $A=\\{1,2\\}$，求 $A$。",
        "question_type": "detailed_answer",
        "category_compulsory": "必修一",
        "category_chapter": "第一章 集合与常用逻辑用语",
        "category_knowledge": "1.1 集合的概念",
        "applicable_grade": "g1",
        "difficulty": "basic",
        "answer_markdown": "$A=\\{1,2\\}$",
    }
    if extra:
        base.update(extra)
    return base


def test_create_question_with_sources(client, db_session):
    """新建题目时传 sources：写入 question_sources、详情返回 sources、source 回填主来源。"""
    r = client.post("/api/questions", headers=_headers(),
                    data=_make_question_form({"source": "2026年哈三中期末",
                                              "sources": '[{"year":"2024","region":"哈尔滨","exam_type":"期末"},{"year":"2026","region":"哈三中","exam_type":"三模"}]'}))
    assert r.status_code == 200, r.text
    qid = r.json()["question"]["id"]

    # 主来源应为排序后第一条（模考 > 校考）
    r = client.get(f"/api/questions/{qid}")
    body = r.json()
    assert body["source"] == "2026年哈三中三模"
    assert len(body["sources"]) == 2
    assert body["sources"][0]["text"] == "2026年哈三中三模"
    assert body["sources"][1]["text"] == "2024年哈尔滨期末"


def test_update_question_rewrites_sources(client, db_session):
    """更新题目时传 sources：重写来源列表（删旧插新）。"""
    r = client.post("/api/questions", headers=_headers(),
                    data=_make_question_form({"sources": '[{"year":"2025","region":"省实验","exam_type":"联考"}]'}))
    assert r.status_code == 200
    qid = r.json()["question"]["id"]

    r = client.put(f"/api/questions/{qid}", headers=_headers(),
                   data=_make_question_form({"source": "",
                                             "sources": '[{"year":"2026","region":"全国甲卷","exam_type":"高考"}]'}))
    assert r.status_code == 200, r.text

    r = client.get(f"/api/questions/{qid}")
    body = r.json()
    assert len(body["sources"]) == 1
    assert body["sources"][0]["text"] == "2026年全国甲卷高考"
    assert body["source"] == "2026年全国甲卷高考"


def test_update_question_clears_sources(client, db_session):
    """更新题目传空数组 sources：清空全部来源。"""
    r = client.post("/api/questions", headers=_headers(),
                    data=_make_question_form({"sources": '[{"year":"2025","region":"省实验","exam_type":"联考"}]'}))
    assert r.status_code == 200
    qid = r.json()["question"]["id"]

    r = client.put(f"/api/questions/{qid}", headers=_headers(),
                   data=_make_question_form({"sources": "[]"}))
    assert r.status_code == 200, r.text

    r = client.get(f"/api/questions/{qid}")
    assert r.json()["sources"] == []


def test_create_question_invalid_sources_ignored(client, db_session):
    """sources 为非法 JSON / 非法条目：不报错，非法项被丢弃。"""
    r = client.post("/api/questions", headers=_headers(),
                    data=_make_question_form({"sources": "not-json"}))
    assert r.status_code == 200, r.text
    qid = r.json()["question"]["id"]
    assert client.get(f"/api/questions/{qid}").json()["sources"] == []

    r = client.post("/api/questions", headers=_headers(),
                    data=_make_question_form({"sources": '[{"year":"abc","region":"哈三中","exam_type":"期末"}]'}))
    assert r.status_code == 200, r.text
    qid = r.json()["question"]["id"]
    assert client.get(f"/api/questions/{qid}").json()["sources"] == []



def test_source_suggestions(client, db_session):
    """来源智能联想：从 question_sources 去重返回年份/学校/考试类型/卷名。"""
    r = client.get("/api/source-suggestions")
    assert r.status_code == 200
    body = r.json()
    # 空库时返回空数组
    assert body["years"] == []
    assert body["regions"] == []
    assert body["exam_types"] == []
    assert body["paper_titles"] == []

    # 录 3 条来源：哈三中三模(2026)、哈三中期末(2025)、全国甲卷高考(2025)
    r = client.post("/api/questions", headers=_headers(),
                    data=_make_question_form({"sources":
                        '[{"year":"2026","region":"哈三中","exam_type":"三模","paper_title":"2026届哈三中高三三模"},'
                        '{"year":"2025","region":"哈三中","exam_type":"期末"},'
                        '{"year":"2025","region":"全国甲卷","exam_type":"高考"}]'}))
    assert r.status_code == 200, r.text

    r = client.get("/api/source-suggestions")
    assert r.status_code == 200
    body = r.json()
    # 年份降序：2026 在 2025 前
    assert body["years"] == ["2026", "2025"]
    # 学校去重：哈三中 2 次 > 全国甲卷 1 次，按频次排序
    assert body["regions"][0] == "哈三中"
    assert "全国甲卷" in body["regions"]
    # 考试类型去重
    assert set(body["exam_types"]) == {"三模", "期末", "高考"}
    # 卷名去重
    assert body["paper_titles"] == ["2026届哈三中高三三模"]


# ============ 知识块修订稿拼接回归 ============

_CH = "第一章 集合与常用逻辑用语"
_KN = "1.1 集合的概念"


def _pin_curriculum(monkeypatch):
    """把活跃大纲钉成一个小节，考点 ID 可控，避免依赖真实元数据内容。"""
    tree = {"必修一": {_CH: [{"name": _KN, "topics": [
        {"id": "k_tree000001", "name": "集合判断", "no": 1},
        {"id": "k_tree000002", "name": "元素与集合的关系", "no": 3},
    ]}]}}
    monkeypatch.setattr("mathbank.lecture_helper.get_current_curriculum", lambda: tree)


def test_preview_collects_notes_for_outline_and_config_topic_ids(client, db_session, monkeypatch):
    """草稿 config 里的 topic_id 是编排当时的快照，可能已不在当前大纲中；
    修订稿收集必须覆盖 大纲 ∪ 编排，否则库里已有的内容会渲染成「待补充」。"""
    _pin_curriculum(monkeypatch)
    base = {"compulsory": "必修一", "chapter": _CH, "knowledge": _KN}
    for tid in ("k_tree000001", "k_ghost00999"):
        r = client.put("/api/lecture/notes", headers=_headers(),
                       json={**base, "note_type": "pit", "topic_id": tid, "content": "<p>%s</p>" % tid})
        assert r.status_code == 200

    r = client.post("/api/lecture/preview", headers=_headers(), json={
        **base, "topics": [{"topic_id": "k_ghost00999", "name": "已改名考点", "items": []}],
        "extra_ids": [],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["section_found"] is True
    notes = body["notes"]
    assert notes["pit:必修一:%s:%s:k_ghost00999" % (_CH, _KN)] == "<p>k_ghost00999</p>"
    assert notes["pit:必修一:%s:%s:k_tree000001" % (_CH, _KN)] == "<p>k_tree000001</p>"


def test_preview_marks_section_not_found(client, db_session, monkeypatch):
    """小节在当前教材树里解析不到时如实返回 section_found=False，
    让前端警示，而不是静默出一份易错点全空的讲义。"""
    _pin_curriculum(monkeypatch)
    r = client.post("/api/lecture/preview", headers=_headers(), json={
        "compulsory": "选修二", "chapter": "没这门课", "knowledge": "没这节",
        "topics": [], "extra_ids": [],
    })
    assert r.status_code == 200
    assert r.json()["section_found"] is False


def test_pit_and_method_notes_require_topic_id(client, db_session):
    """易错点/解题方法缺考点 ID 时会退化成 4 段键，多个考向共写一行互相覆盖，必须拒写。"""
    base = {"compulsory": "必修一", "chapter": _CH, "knowledge": _KN, "content": "<p>x</p>"}
    for note_type in ("pit", "method"):
        r = client.put("/api/lecture/notes", headers=_headers(), json={**base, "note_type": note_type})
        assert r.status_code == 400, note_type
        assert "考向" in r.json()["detail"]
    # 知识梳理挂小节，本就不需要考点 ID
    assert client.put("/api/lecture/notes", headers=_headers(),
                      json={**base, "note_type": "knowledge"}).status_code == 200


def test_note_key_escapes_colon_in_segments(client, db_session):
    """段内出现分隔符时不得跨键误命中，也不得因此取不回自己的修订稿。"""
    base = {"compulsory": "必修一", "chapter": "比与比例", "note_type": "knowledge"}
    for knowledge in ("比", "a:b"):
        r = client.put("/api/lecture/notes", headers=_headers(),
                       json={**base, "knowledge": knowledge, "content": "<p>%s</p>" % knowledge})
        assert r.status_code == 200, knowledge

    r = client.get("/api/lecture/notes", params={**base, "chapter": "比与比例", "knowledge": "a"})
    assert r.json() == []  # 半截前缀不得命中 "a:b"

    r = client.get("/api/lecture/notes", params={**base, "chapter": "比与比例", "knowledge": "a:b"})
    assert r.status_code == 200
    assert [row["note_key"] for row in r.json()] == [r"knowledge:必修一:比与比例:a\:b"]

    r = client.post("/api/lecture/preview", headers=_headers(), json={
        "compulsory": "必修一", "chapter": "比与比例", "knowledge": "a:b",
        "topics": [], "extra_ids": [],
    })
    assert r.json()["notes"][r"knowledge:必修一:比与比例:a\:b"] == "<p>a:b</p>"


def test_preview_quote_pool_prefers_saved_settings(client, db_session):
    """设置里保存的名言池必须真正生效，资源文件池只作首次播种与兜底。"""
    r = client.put("/api/lecture/settings", headers=_headers(),
                   json={"quote_pool": ["自定义名言 A", "自定义名言 B"]})
    assert r.status_code == 200
    r = client.post("/api/lecture/preview", headers=_headers(), json={
        "compulsory": "必修一", "chapter": _CH, "knowledge": _KN,
        "topics": [], "extra_ids": [],
    })
    assert r.status_code == 200
    assert r.json()["quote_pool"] == ["自定义名言 A", "自定义名言 B"]


def test_preview_survives_non_numeric_question_ids(client, db_session):
    """编排 config 被外部脚本或手改污染时 preview 不能 500：跳过并如实分开回报。"""
    r = client.post("/api/lecture/preview", headers=_headers(), json={
        "compulsory": "必修一", "chapter": _CH, "knowledge": _KN,
        "topics": [{"topic_id": "k_x", "name": "n", "items": [
            {"question_id": "abc", "role": "example"},
            {"question_id": None, "role": "example"},
            {"question_id": 999999, "role": "example"},
        ]}],
        "extra_ids": ["not-a-number"],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["questions"] == []
    assert sorted(body["invalid_ids"]) == sorted(["abc", "not-a-number"])
    assert body["missing_ids"] == [999999]


def test_preview_question_payload_carries_no_dead_roles(client, db_session):
    """roles 是零消费方的派生字段：曾由 role_map 算好塞进响应却无人读取，已删除。"""
    ids = [q.id for q in _seed(db_session)]
    r = client.post("/api/lecture/preview", headers=_headers(), json={
        "compulsory": "必修一", "chapter": _CH, "knowledge": _KN,
        "topics": [{"topic_id": "k_x", "name": "n", "items": [
            {"question_id": ids[0], "role": "example"},
            {"question_id": ids[0], "role": "practice"},
        ]}],
        "extra_ids": [],
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["questions"]) == 1
    assert "roles" not in body["questions"][0]
    # 派生它的那段计算也已移除，不得再长回来
    from pathlib import Path
    helper = Path(__file__).resolve().parents[1] / "mathbank" / "lecture_helper.py"
    assert "role_map" not in helper.read_text(encoding="utf-8")


def test_preview_exposes_outline_example_for_unrevised_topics(client, db_session, monkeypatch):
    """无修订稿的考向要靠大纲 example 兜底，preview 必须把默认内容带出来。"""
    monkeypatch.setattr("mathbank.lecture_helper.get_current_curriculum", lambda: {
        "必修一": {_CH: [{"name": _KN, "topics": [
            {"id": "k_e1", "name": "集合判断", "no": 1, "example": "例：判断小于5的自然数能否构成集合。"},
            {"id": "k_e2", "name": "无示例考向", "no": 2},
        ]}]}
    })
    r = client.post("/api/lecture/preview", headers=_headers(), json={
        "compulsory": "必修一", "chapter": _CH, "knowledge": _KN, "topics": [], "extra_ids": [],
    })
    assert r.status_code == 200
    topics = r.json()["section"]["topics"]
    assert topics[0]["example"].startswith("例：")
    assert topics[1]["example"] == ""


def test_merged_preview_orders_sections_and_unions_notes(client, db_session, monkeypatch):
    """多节合并：按勾选顺序逐节出数据，修订稿各归各节，题目一次批量加载。"""
    ids = [q.id for q in _seed(db_session)]
    kn2 = "1.2 集合间的基本关系"
    monkeypatch.setattr("mathbank.lecture_helper.get_current_curriculum", lambda: {
        "必修一": {_CH: [
            {"name": _KN, "topics": [{"id": "k_t1", "name": "集合判断", "no": 1}]},
            {"name": kn2, "topics": [{"id": "k_t2", "name": "子集判断", "no": 2}]},
        ]}
    })
    for knowledge, tid in ((_KN, "k_t1"), (kn2, "k_t2")):
        r = client.put("/api/lecture/notes", headers=_headers(), json={
            "compulsory": "必修一", "chapter": _CH, "knowledge": knowledge,
            "note_type": "pit", "topic_id": tid, "content": "<p>坑-%s</p>" % knowledge})
        assert r.status_code == 200

    r = client.post("/api/lecture/preview", headers=_headers(), json={"sections": [
        {"compulsory": "必修一", "chapter": _CH, "knowledge": kn2,
         "topics": [{"topic_id": "k_t2", "name": "子集判断",
                     "items": [{"question_id": ids[1], "role": "example"}]}],
         "extra_ids": []},
        {"compulsory": "必修一", "chapter": _CH, "knowledge": _KN,
         "topics": [{"topic_id": "k_t1", "name": "集合判断",
                     "items": [{"question_id": ids[0], "role": "example"}]}],
         "extra_ids": [ids[0]]},
    ]})
    assert r.status_code == 200
    body = r.json()
    # 顺序 = 载荷顺序（先 K2 后 K1），不是字母序或激活序
    assert [e["section"]["knowledge"] for e in body["sections"]] == [kn2, _KN]
    assert "坑-%s" % kn2 in body["sections"][0]["notes"]["pit:必修一:%s:%s:k_t2" % (_CH, kn2)]
    assert "坑-%s" % _KN in body["sections"][1]["notes"]["pit:必修一:%s:%s:k_t1" % (_CH, _KN)]
    # 每节只带自己编排引用的题；实战演练的题也在第二节里
    assert [q["id"] for q in body["sections"][0]["questions"]] == [ids[1]]
    assert sorted(q["id"] for q in body["sections"][1]["questions"]) == sorted({ids[0]})
    assert body["sections"][1]["extra_ids"] == [ids[0]]


def test_merged_preview_rejects_empty_or_garbage_sections(client, db_session):
    """sections 全空/坐标缺失时 400，而不是出一份没有内容的讲义。"""
    r = client.post("/api/lecture/preview", headers=_headers(), json={"sections": []})
    assert r.status_code == 400
    r = client.post("/api/lecture/preview", headers=_headers(), json={"sections": [
        {"compulsory": "", "chapter": "", "knowledge": ""}]})
    assert r.status_code == 400


def test_merged_preview_reports_dead_sections_per_entry(client, db_session, monkeypatch):
    """某一节在小节改名后 section_found=False、题目被删后 missing_ids 如实带出，
    其余节照常出稿——合并讲义不得让一节的问题拖垮整本。"""
    ids = [q.id for q in _seed(db_session)]
    _pin_curriculum(monkeypatch)  # 活跃树只有 _KN 一个小节
    r = client.post("/api/lecture/preview", headers=_headers(), json={"sections": [
        {"compulsory": "必修一", "chapter": _CH, "knowledge": "已被改名的旧小节",
         "topics": [], "extra_ids": []},
        {"compulsory": "必修一", "chapter": _CH, "knowledge": _KN,
         "topics": [{"topic_id": "k_x", "name": "n",
                     "items": [{"question_id": 999999, "role": "example"}]}],
         "extra_ids": []},
    ]})
    assert r.status_code == 200
    entries = r.json()["sections"]
    assert entries[0]["section_found"] is False
    assert entries[1]["section_found"] is True
    assert entries[1]["missing_ids"] == [999999]


def test_orphan_notes_lists_only_unreachable_revisions(client, db_session, monkeypatch):
    """考向树重建 / 小节改名后留下的修订稿必须列得出来，且能按列出的坐标删掉。

    这类行此前在界面上完全隐身：note_key 永远查不回来，用户既看不到也删不掉。
    """
    _pin_curriculum(monkeypatch)
    base = {"compulsory": "必修一", "chapter": _CH, "knowledge": _KN}
    alive_knowledge = "knowledge:必修一:%s:%s" % (_CH, _KN)
    seed = [
        {**base, "note_type": "knowledge", "content": "<p>活：知识梳理</p>"},
        {**base, "note_type": "pit", "topic_id": "k_tree000001", "content": "<p>活：在册考向</p>"},
        {**base, "note_type": "method", "topic_id": "k_gone00001", "content": "<p>考向已重建</p>"},
        {**base, "note_type": "pit", "topic_id": "undefined", "content": "<p>旧脏键</p>"},
        {"compulsory": "必修一", "chapter": "第九章 已被删掉的章", "knowledge": _KN,
         "note_type": "knowledge", "content": "<p>小节没了</p>"},
    ]
    for row in seed:
        assert client.put("/api/lecture/notes", headers=_headers(), json=row).status_code == 200

    items = client.get("/api/lecture/orphan-notes").json()["items"]
    by_key = {item["note_key"]: item for item in items}
    assert alive_knowledge not in by_key
    assert "pit:必修一:%s:%s:k_tree000001" % (_CH, _KN) not in by_key
    dead_method = "method:必修一:%s:%s:k_gone00001" % (_CH, _KN)
    assert by_key[dead_method]["reason"] == "topic_missing"
    assert by_key[dead_method]["topic_id"] == "k_gone00001"
    dirty_pit = "pit:必修一:%s:%s:undefined" % (_CH, _KN)
    assert by_key[dirty_pit]["reason"] == "topic_missing"
    gone_chapter = "knowledge:必修一:第九章 已被删掉的章:%s" % _KN
    assert by_key[gone_chapter]["reason"] == "section_missing"
    assert by_key[gone_chapter]["content_chars"] == len("<p>小节没了</p>")

    # 列出来的东西必须删得掉：删除路由自己按同样的口径重拼 note_key
    client.delete("/api/lecture/notes", headers=_headers(), params={
        "note_type": "method", "compulsory": "必修一", "chapter": _CH,
        "knowledge": _KN, "topic_id": "k_gone00001",
    })
    remaining = {item["note_key"] for item in client.get("/api/lecture/orphan-notes").json()["items"]}
    assert dead_method not in remaining
    assert dirty_pit in remaining


def test_orphan_notes_accepts_revisions_from_inactive_textbook_versions(client, db_session, monkeypatch):
    """lecture_notes 没有版本列：只按当前活跃树判存活，会把另一套教材写好的修订稿误报成孤儿。

    考点 ID 从预设树里实际取，不硬编码——同步考向树重建会换掉 ID，钉死字面量
    会让这条回归在每次改大纲后都假红。
    """
    _pin_curriculum(monkeypatch)   # 活跃树里只有 _CH/_KN 这一个小节
    from mathbank.curriculums import load_curriculum

    preset_section = load_curriculum("A")["必修一"]["1. 集合与常用逻辑用语"][0]
    tid = preset_section["topics"][0]["id"]
    preset = {"compulsory": "必修一", "chapter": "1. 集合与常用逻辑用语", "knowledge": preset_section["name"]}
    assert client.put("/api/lecture/notes", headers=_headers(), json={
        **preset, "note_type": "pit", "topic_id": tid,
        "content": "<p>人教A 版预设里的真考向</p>",
    }).status_code == 200

    keys = [item["note_key"] for item in client.get("/api/lecture/orphan-notes").json()["items"]]
    assert keys == []
