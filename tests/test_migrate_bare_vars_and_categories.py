# -*- coding: utf-8 -*-
"""存量回洗脚本的行为钉（裸数学变量 + 教材分类字段）。

只读计划层用最小替身对象跑，落库层用内存 SQLite 真表，钉住四件事：

  1. 文本洗得动、而且**幂等**（第二遍零命中），指纹随之重算；
  2. 分类归一真改了就必须同步 ``question_curriculums`` 与 ``question_topics`` 主槽位，
     **附加槽位永不碰**；
  3. protect-existing：归一算出空值、或考向搬不到新槽位时，整题分类一个字都不改；
  4. 扫描与写库之间被并发编辑过的字段直接跳过，绝不覆盖新内容。
"""

from types import SimpleNamespace

from mathbank.database import (
    Question,
    QuestionCurriculum,
    QuestionFingerprint,
    QuestionTopic,
)
from mathbank.question_duplicate_service import (
    fingerprint_for_question,
    upsert_question_fingerprint,
)
from scripts.migrate_bare_vars_and_categories import (
    UPLOAD_DIR,
    UPLOAD_DIR_REL,
    apply_category,
    plan_category_wash,
    plan_text_wash,
    plan_wash,
    rebuild_fingerprints,
    verify_clean,
)

ACTIVE_VERSION = "A"

# 与 tests/test_kaoxiang_tree.py 同构的小大纲：够窄，脏章名一定洗得出来；
# 「求参数范围」刻意在两个小节下各有一份不同 ID，用来钉考向随槽位迁移的分支。
CURRICULUM = {
    "必修一": {
        "1. 集合与常用逻辑用语": [
            {
                "name": "1.1 集合的概念",
                "topics": [
                    {"id": "k_a", "name": "集合的基本概念", "level": "L1"},
                    {"id": "k_dup", "name": "求参数范围", "level": "L3"},
                ],
            },
            {
                "name": "1.3 集合的基本运算",
                "topics": [{"id": "k_c", "name": "交集运算", "level": "L1"}],
            },
        ],
        "3. 函数的概念与性质": [
            {
                "name": "3.2.1 函数的单调性",
                "topics": [
                    {"id": "k_d", "name": "利用单调性解不等式", "level": "L3"},
                    {"id": "k_scope", "name": "求参数范围", "level": "L3"},
                ],
            },
        ],
    }
}


def _stub(**kwargs):
    """plan_* 只读属性，用替身对象即可，不必真的连库。"""
    base = {
        "id": 1,
        "content": "",
        "answer_markdown": "",
        "category_compulsory": "",
        "category_chapter": "",
        "category_knowledge": "",
        "topic_id": "",
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _question(db, **kwargs):
    """往内存库塞一道带镜像行的存量题（主槽位 + 一个附加槽位）。"""
    question = Question(
        content=kwargs.pop("content", "若 p 是 q 的充分不必要条件"),
        answer_markdown=kwargs.pop("answer_markdown", "实数 a 的取值范围为____。"),
        question_type="single_choice",
        **kwargs
    )
    db.add(question)
    db.flush()
    db.add(
        QuestionCurriculum(
            question_id=question.id,
            version_code=ACTIVE_VERSION,
            compulsory=question.category_compulsory or "",
            chapter=question.category_chapter or "",
            knowledge=question.category_knowledge or "",
        )
    )
    db.add(
        QuestionTopic(
            question_id=question.id,
            version_code=ACTIVE_VERSION,
            compulsory=question.category_compulsory or "",
            chapter=question.category_chapter or "",
            knowledge=question.category_knowledge or "",
            topic_id=question.topic_id or "",
            sort_order=0,
        )
    )
    db.add(
        QuestionTopic(
            question_id=question.id,
            version_code=ACTIVE_VERSION,
            compulsory="必修一",
            chapter="1. 集合与常用逻辑用语",
            knowledge="1.3 集合的基本运算",
            topic_id="k_c",
            sort_order=1,
        )
    )
    db.flush()
    return question


# ---------------------------------------------------------------------------
# 文本层：#36 裸数学变量
# ---------------------------------------------------------------------------


def test_plan_text_wash_wraps_bare_variables_in_both_fields():
    """题干与解析里的中文语境裸变量都要洗到（存量 278 处就是这个形态）。"""
    question = _stub(
        content="若 p 是 q 的充分不必要条件，则实数 a 的取值范围为____。",
        answer_markdown="已知函数 y 是 x 的函数，第 t 天利润最大。",
    )
    hits = plan_text_wash(question)
    assert [hit["field"] for hit in hits] == ["content", "answer_markdown"]
    assert hits[0]["count"] == 3
    assert "若 $p$ 是 $q$ 的充分不必要条件" in hits[0]["after"]
    assert "实数 $a$ 的取值范围" in hits[0]["after"]
    assert "已知函数 $y$ 是 $x$ 的函数" in hits[1]["after"]
    assert hits[0]["samples"]


def test_plan_text_wash_is_idempotent_and_skips_clean_text():
    """洗完第二遍必须零命中；本来干净的文本也不该产生任何改动。"""
    hits = plan_text_wash(_stub(content="若 p 是 q 的充分不必要条件"))
    assert len(hits) == 1
    assert plan_text_wash(_stub(content=hits[0]["after"])) == []
    assert plan_text_wash(_stub(content="若 $p$ 是 $q$ 的充要条件")) == []
    assert plan_text_wash(_stub(content="")) == []


# ---------------------------------------------------------------------------
# 分类层：归一 + protect-existing
# ---------------------------------------------------------------------------


def test_plan_category_wash_maps_composite_chapter_to_outline_keys():
    r"""脏章名「章 > 小节」整行抄写：归一回大纲章键，并把小节填进 category_knowledge。"""
    plan, reason = plan_category_wash(
        _stub(category_compulsory="必修一", category_chapter="1. 集合与常用逻辑用语 > 1.1 集合的概念"),
        CURRICULUM,
    )
    assert reason == ""
    assert plan["after"]["category_chapter"] == "1. 集合与常用逻辑用语"
    assert plan["after"]["category_knowledge"] == "1.1 集合的概念"
    assert plan["labels"]


def test_plan_category_wash_carries_topic_id_into_normalized_slot():
    """小节归一后同名考向挂在别的槽位：topic_id 必须跟着换成新槽位的 ID。"""
    plan, reason = plan_category_wash(
        _stub(
            category_compulsory="必修一",
            category_chapter="3. 函数的概念与性质 > 3.2.1 函数的单调性",
            topic_id="k_dup",
        ),
        CURRICULUM,
    )
    assert reason == ""
    assert plan["after"]["category_chapter"] == "3. 函数的概念与性质"
    assert plan["after"]["category_knowledge"] == "3.2.1 函数的单调性"
    assert plan["topic_id_before"] == "k_dup"
    assert plan["topic_id_after"] == "k_scope"
    assert any("topic_id" in label for label in plan["labels"])


def test_plan_category_wash_protects_existing_when_alignment_blanks_it():
    """章名压根不在大纲里：归一会把非空存量洗成空，整题分类必须一个字都不改。"""
    plan, reason = plan_category_wash(
        _stub(
            category_compulsory="必修一",
            category_chapter="9. 平面向量",
            category_knowledge="向量加减运算",
        ),
        CURRICULUM,
    )
    assert plan is None
    assert "归一后为空" in reason


def test_plan_category_wash_skips_unknown_topic_id_when_chapter_drifts():
    """存量 topic_id 在当前大纲里翻不出名字，而章节又要变：线索不能瞎搬，整题跳过。"""
    plan, reason = plan_category_wash(
        _stub(
            category_compulsory="必修一",
            category_chapter="1. 集合与常用逻辑用语 > 1.1 集合的概念",
            topic_id="k_legacy_not_in_outline",
        ),
        CURRICULUM,
    )
    assert plan is None
    assert "不在当前大纲里" in reason


def test_plan_category_wash_skips_when_topic_missing_in_normalized_slot():
    """考向名归一后在新小节下找不到对应 ID：宁可保留脏章名，也不把考向挂错地方。"""
    plan, reason = plan_category_wash(
        _stub(
            category_compulsory="必修一",
            category_chapter="1. 集合与常用逻辑用语",
            category_knowledge="1.3 集合的基本运算",
            topic_id="k_c",
        ),
        CURRICULUM,
    )
    assert reason == ""
    assert plan is None

def test_plan_category_wash_skips_when_topic_missing_in_normalized_slot():
    """章要归一、而挂在原槽位的考向在新小节下没有对应 ID：保留脏线索，整题不动。"""
    plan, reason = plan_category_wash(
        _stub(
            category_compulsory="必修一",
            category_chapter="3. 函数的概念与性质 > 3.2.1 函数的单调性",
            topic_id="k_c",
        ),
        CURRICULUM,
    )
    assert plan is None
    assert "找不到对应 ID" in reason


# ---------------------------------------------------------------------------
# 扫描入口 + 落库层
# ---------------------------------------------------------------------------


def test_plan_wash_splits_plans_and_manual_list():
    """一次扫描同时给出改动清单与人工清单，两者按题号可追溯。"""
    rows = [
        _stub(id=11, content="若 p 是 q 的充分不必要条件"),
        _stub(
            id=12,
            category_compulsory="必修一",
            category_chapter="1. 集合与常用逻辑用语 > 1.1 集合的概念",
        ),
        _stub(
            id=13,
            category_compulsory="必修一",
            category_chapter="9. 平面向量",
            category_knowledge="向量加减运算",
        ),
        _stub(id=14, content="干净的题干", category_compulsory="必修一", category_chapter="3. 函数的概念与性质"),
    ]
    plans, manual = plan_wash(rows, CURRICULUM)
    assert {plan["question_id"] for plan in plans} == {11, 12}
    assert [qid for qid, _reason in manual] == [13]
    assert plans[0]["texts"][0]["after"] == "若 $p$ 是 $q$ 的充分不必要条件"
    assert plans[0]["category"] is None
    assert plans[1]["category"]["after"]["category_knowledge"] == "1.1 集合的概念"


def test_apply_category_syncs_both_mirror_tables_and_keeps_extra_slot(db_session):
    """分类一改，questions/镜像/主槽位三处必须同时落，附加考向槽位原样不动。"""
    question = _question(
        db_session,
        content="已知定义域为 R 的函数 f(x)",
        answer_markdown="**【答案】** A",
        category_compulsory="必修一",
        category_chapter="3. 函数的概念与性质 > 3.2.1 函数的单调性",
        category_knowledge="",
        topic_id="k_dup",
    )
    plan, reason = plan_category_wash(question, CURRICULUM)
    assert reason == ""
    assert apply_category(db_session, question, plan, ACTIVE_VERSION) is True
    db_session.flush()

    assert question.category_chapter == "3. 函数的概念与性质"
    assert question.category_knowledge == "3.2.1 函数的单调性"
    assert question.topic_id == "k_scope"

    mirror = (
        db_session.query(QuestionCurriculum)
        .filter_by(question_id=question.id, version_code=ACTIVE_VERSION)
        .one()
    )
    assert (mirror.compulsory, mirror.chapter, mirror.knowledge) == (
        "必修一",
        "3. 函数的概念与性质",
        "3.2.1 函数的单调性",
    )
    slots = {
        row.sort_order or 0: row
        for row in db_session.query(QuestionTopic).filter_by(
            question_id=question.id, version_code=ACTIVE_VERSION
        ).all()
    }
    assert slots[0].chapter == "3. 函数的概念与性质"
    assert slots[0].knowledge == "3.2.1 函数的单调性"
    assert slots[0].topic_id == "k_scope"
    # 附加槽位（sort_order=1）是教师另挂的考点，回洗绝不能顺手改掉
    assert slots[1].knowledge == "1.3 集合的基本运算"
    assert slots[1].topic_id == "k_c"


def test_apply_category_refuses_when_concurrently_edited(db_session):
    """扫描后题目被并发编辑：分类改动整体作废，绝不覆盖线上刚写进去的值。"""
    question = _question(
        db_session,
        category_compulsory="必修一",
        category_chapter="1. 集合与常用逻辑用语 > 1.1 集合的概念",
    )
    plan, _reason = plan_category_wash(question, CURRICULUM)
    question.category_chapter = "3. 函数的概念与性质"
    assert apply_category(db_session, question, plan, ACTIVE_VERSION) is False
    db_session.flush()
    assert question.category_chapter == "3. 函数的概念与性质"
    mirror = (
        db_session.query(QuestionCurriculum)
        .filter_by(question_id=question.id, version_code=ACTIVE_VERSION)
        .one()
    )
    assert mirror.chapter == "1. 集合与常用逻辑用语 > 1.1 集合的概念"


def test_rebuild_fingerprints_after_text_wash_and_verify_clean(db_session):
    """洗完文本必须重算指纹（旧行整批作废），复检归零。"""
    question = _question(
        db_session,
        content="若 p 是 q 的充分不必要条件",
        answer_markdown="则实数 a 的取值范围是开区间",
    )
    stale = fingerprint_for_question(
        question, uploads_dir=UPLOAD_DIR, url_prefix=UPLOAD_DIR_REL
    )
    upsert_question_fingerprint(db_session, question, stale)
    db_session.flush()

    plans, _manual = plan_wash([question], CURRICULUM)
    assert plans and plans[0]["texts"]
    for hit in plans[0]["texts"]:
        setattr(question, hit["field"], hit["after"])
    db_session.flush()

    deleted, rebuilt = rebuild_fingerprints(db_session, [question.id], {question.id: question})
    db_session.flush()
    assert deleted == 1 and rebuilt == 1
    fresh = fingerprint_for_question(
        question, uploads_dir=UPLOAD_DIR, url_prefix=UPLOAD_DIR_REL
    )
    stored = (
        db_session.query(QuestionFingerprint)
        .filter_by(question_id=question.id)
        .one()
    )
    assert stored.exact_hash == fresh.exact_hash
    assert "$p$" in question.content and "$a$" in question.answer_markdown
    assert verify_clean(db_session, CURRICULUM) == (0, 0)