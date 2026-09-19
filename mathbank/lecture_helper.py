"""讲义生成辅助模块：资源加载 + 讲义数据组装。

讲义数据（封面设置/名言池/封面背景）来自 ``mathbank/resources/lecture/``，
知识块修订稿与讲义草稿存数据库（lecture_settings / lecture_notes /
lecture_drafts）。本模块只负责读取与组装，不负责渲染——渲染由前端
（讲义工作台）按 A4 打印视图完成。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from mathbank.curriculums import get_current_curriculum
from mathbank.database import LectureNote, LectureSetting
from mathbank.paths import PROJECT_ROOT

_RESOURCE_DIR = PROJECT_ROOT / "mathbank" / "resources" / "lecture"

_quotes_cache: Optional[list] = None
_cover_bg_cache: Optional[dict] = None

DEFAULT_QUOTES = [
    "数缺形时少直观，形少数时难入微。——华罗庚",
    "宇宙之大，粒子之微，火箭之速，化工之巧，地球之变，生物之谜，日用之繁，无处不用数学。——华罗庚",
    "聪明在于勤奋，天才在于积累。——华罗庚",
    "下棋找高手，弄斧到班门。——华罗庚",
    "我们欣赏数学，我们需要数学。——陈省身",
]


def load_quotes() -> list:
    """加载页眉名言池（资源 JSON，失败时回退内置默认）。"""
    global _quotes_cache
    if _quotes_cache is not None:
        return _quotes_cache
    try:
        raw = (_RESOURCE_DIR / "quote_pool.json").read_text(encoding="utf-8")
        quotes = json.loads(raw)
        if isinstance(quotes, list) and quotes:
            _quotes_cache = [str(q) for q in quotes]
            return _quotes_cache
    except (OSError, ValueError):
        pass
    _quotes_cache = list(DEFAULT_QUOTES)
    return _quotes_cache


def load_cover_bg() -> dict:
    """加载封面背景图案库（章节名 → SVG 模板）。"""
    global _cover_bg_cache
    if _cover_bg_cache is not None:
        return _cover_bg_cache
    try:
        raw = (_RESOURCE_DIR / "cover_bg.json").read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            _cover_bg_cache = {str(k): str(v) for k, v in data.items()}
            return _cover_bg_cache
    except (OSError, ValueError):
        pass
    _cover_bg_cache = {}
    return _cover_bg_cache


def note_key(note_type: str, compulsory: str, chapter: str, knowledge: str, topic_id: str = "") -> str:
    """知识块修订稿唯一键。知识梳理挂小节，易错点/解题方法挂考向。"""
    parts = [note_type, compulsory, chapter, knowledge]
    if topic_id:
        parts.append(topic_id)
    return ":".join(parts)


def get_or_create_settings(db: Session) -> LectureSetting:
    """取讲义设置（单行），不存在则按默认创建。"""
    setting = db.query(LectureSetting).filter(LectureSetting.id == 1).first()
    if setting is None:
        setting = LectureSetting(
            id=1,
            cover_title="",
            cover_subtitle="同步讲义",
            cover_brand="MT-Blaze 的数学空间",
            cover_author="",
            quote_pool=json.dumps(load_quotes(), ensure_ascii=False),
            bound_mode="loose",
        )
        db.add(setting)
        db.commit()
        db.refresh(setting)
    return setting


def find_section(compulsory: str, chapter: str, knowledge: str) -> Optional[dict]:
    """在大纲中查找小节（含考向树）。"""
    curriculum = get_current_curriculum() or {}
    chapters = curriculum.get(compulsory) or {}
    sections = chapters.get(chapter) or []
    for section in sections:
        if isinstance(section, str):
            if section == knowledge:
                return {"name": section, "topics": []}
            continue
        if str(section.get("name") or "") == knowledge:
            return section
    return None


def collect_notes(db: Session, compulsory: str, chapter: str, knowledge: str, topic_ids: list) -> dict:
    """批量取知识块修订稿：知识梳理 + 各考向的易错点/解题方法。"""
    keys = [note_key("knowledge", compulsory, chapter, knowledge)]
    for tid in topic_ids:
        keys.append(note_key("pit", compulsory, chapter, knowledge, tid))
        keys.append(note_key("method", compulsory, chapter, knowledge, tid))
    rows = db.query(LectureNote).filter(LectureNote.note_key.in_(keys)).all() if keys else []
    return {row.note_key: row.content for row in rows}


def build_preview(
    db: Session,
    compulsory: str,
    chapter: str,
    knowledge: str,
    topics: Optional[list] = None,
    extra_ids: Optional[list] = None,
) -> dict:
    """组装讲义预览数据：设置 + 小节考向树 + 知识块修订 + 题目详情。

    topics: 编排结果 [{topic_id, name, items:[{question_id, role}]}]
    extra_ids: 实战演练/跨考向题目 id 列表
    """
    section = find_section(compulsory, chapter, knowledge) or {"name": knowledge, "topics": []}
    all_topic_ids = [t.get("id") for t in (section.get("topics") or []) if isinstance(t, dict) and t.get("id")]
    notes = collect_notes(db, compulsory, chapter, knowledge, all_topic_ids)

    # 题目详情
    qids = []
    role_map = {}  # question_id -> [roles]
    if topics:
        for t in topics:
            for item in (t.get("items") or []):
                qid = item.get("question_id")
                if qid:
                    qids.append(int(qid))
                    role_map.setdefault(int(qid), []).append(item.get("role") or "example")
    for qid in (extra_ids or []):
        qids.append(int(qid))
        role_map.setdefault(int(qid), []).append("homework")

    from mathbank.database import Question
    from mathbank.sources_helper import load_question_sources

    questions = []
    missing_ids: list[int] = []
    if qids:
        sources_map = load_question_sources(db, qids)
        seen = set()
        rows = db.query(Question).filter(Question.id.in_(qids)).all()
        by_id = {q.id: q for q in rows}
        for qid in qids:
            if qid in seen:
                continue
            seen.add(qid)
            q = by_id.get(qid)
            if q is None:
                # 编排引用的题目已被删除：如实登记，不能静默吞掉
                missing_ids.append(qid)
                continue
            questions.append({
                "id": q.id,
                "content": q.content or "",
                "question_type": q.question_type or "",
                "difficulty": q.difficulty or "",
                "topic_id": q.topic_id or "",
                "source": q.source or "",
                "paper_category": q.paper_category or "",
                "answer": q.answer_markdown or "",
                "roles": role_map.get(q.id, []),
                # 一题多来源：按 高考>模考>联考>校考 + 年份新在前 排序后的列表
                "sources": sources_map.get(q.id, []),
            })

    return {
        "settings": get_or_create_settings(db).to_dict(),
        "section": {
            "compulsory": compulsory,
            "chapter": chapter,
            "knowledge": knowledge,
            "name": section.get("name") or knowledge,
            "topics": [
                {"id": t.get("id"), "name": t.get("name"), "no": t.get("no"), "level": t.get("level")}
                for t in (section.get("topics") or [])
                if isinstance(t, dict)
            ],
        },
        "notes": notes,
        "topics": topics or [],
        "extra_ids": extra_ids or [],
        "questions": questions,
        # 编排/实战演练中引用但库中已不存在的题目 ID（前端据此提示）
        "missing_ids": missing_ids,
        "quote_pool": load_quotes(),
        "cover_bg": load_cover_bg(),
    }
