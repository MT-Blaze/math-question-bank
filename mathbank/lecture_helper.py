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


def _esc_key_seg(seg) -> str:
    """转义键段里的分隔符，保证 ``split_note_key`` 能无损反解。

    段内不含 ``:`` 与 ``\\`` 时原样返回，旧库里的键因此不受影响。
    """
    text = str(seg or "")
    if ":" in text or "\\" in text:
        text = text.replace("\\", "\\\\").replace(":", "\\:")
    return text


def note_key(note_type: str, compulsory: str, chapter: str, knowledge: str, topic_id: str = "") -> str:
    """知识块修订稿唯一键。知识梳理挂小节，易错点/解题方法挂考向。"""
    parts = [note_type, compulsory, chapter, knowledge]
    if topic_id:
        parts.append(topic_id)
    return ":".join(_esc_key_seg(p) for p in parts)


def split_note_key(key) -> list:
    """把 ``note_key`` 反解回各段（``\\:`` → ``:``，``\\\\`` → ``\\``）。"""
    parts: list[str] = []
    buf: list[str] = []
    text = str(key or "")
    index = 0
    while index < len(text):
        ch = text[index]
        if ch == "\\" and index + 1 < len(text):
            buf.append(text[index + 1])
            index += 2
            continue
        if ch == ":":
            parts.append("".join(buf))
            buf = []
            index += 1
            continue
        buf.append(ch)
        index += 1
    parts.append("".join(buf))
    return parts


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
    """批量取知识块修订稿：知识梳理 + 各考向的易错点/解题方法。

    缺失或重复的 topic_id 在这里被剔除，避免拼出「...:None」这类永远
    命中不了的键，也让同一考向只查一次。
    """
    keys = [note_key("knowledge", compulsory, chapter, knowledge)]
    seen = set()
    for tid in (topic_ids or []):
        clean = str(tid or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        keys.append(note_key("pit", compulsory, chapter, knowledge, clean))
        keys.append(note_key("method", compulsory, chapter, knowledge, clean))
    rows = db.query(LectureNote).filter(LectureNote.note_key.in_(keys)).all()
    return {row.note_key: row.content for row in rows}


def _as_question_id(value) -> Optional[int]:
    """编排快照里的 ID 可能来自外部脚本或手改的 config，非数字要容错而不是 500。"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _collect_ids(topics: Optional[list], extra_ids: Optional[list]) -> tuple:
    qids: list[int] = []
    invalid_ids: list[str] = []
    if topics:
        for t in topics:
            if not isinstance(t, dict):
                continue
            for item in (t.get("items") or []):
                if not isinstance(item, dict) or not item.get("question_id"):
                    continue
                parsed = _as_question_id(item.get("question_id"))
                if parsed is None:
                    invalid_ids.append(str(item.get("question_id")))
                    continue
                qids.append(parsed)
    for qid in (extra_ids or []):
        parsed = _as_question_id(qid)
        if parsed is None:
            invalid_ids.append(str(qid))
        else:
            qids.append(parsed)
    return qids, invalid_ids


def _load_questions(db: Session, qids: list) -> tuple:
    """按 ID 批量取题目详情（去重、保持首次出现顺序）。"""
    from mathbank.database import Question
    from mathbank.sources_helper import load_question_sources

    questions: list[dict] = []
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
                # 一题多来源：按 高考>模考>联考>校考 + 年份新在前 排序后的列表
                "sources": sources_map.get(q.id, []),
            })
    return questions, missing_ids


def collect_sections(
    db: Session,
    sections: list,
    questions_by_id: Optional[dict] = None,
) -> list:
    """按用户勾选顺序逐节组装预览数据。

    每节独立解析小节、收集修订稿与编排引用；``questions_by_id`` 由合并预览
    预先批量加载后传入，避免同一道题在多节间重复查库。旧草稿的编排是当时的
    快照，换过教材树后 topic_id 可能与当前大纲不等，因此修订稿收集范围仍是
    大纲考点 ∪ 编排考点（与单节 ``build_preview`` 同一口径）。
    """
    entries: list[dict] = []
    shared_questions = questions_by_id or {}
    for raw in sections or []:
        if not isinstance(raw, dict):
            continue
        compulsory = str(raw.get("compulsory") or "").strip()
        chapter = str(raw.get("chapter") or "").strip()
        knowledge = str(raw.get("knowledge") or "").strip()
        if not (compulsory and chapter and knowledge):
            continue
        found = find_section(compulsory, chapter, knowledge)
        section_found = found is not None
        section = found or {"name": knowledge, "topics": []}
        tree_topic_ids = [
            t.get("id") for t in (section.get("topics") or [])
            if isinstance(t, dict) and t.get("id")
        ]
        topics = raw.get("topics") if isinstance(raw.get("topics"), list) else []
        extra_ids = raw.get("extra_ids") if isinstance(raw.get("extra_ids"), list) else []
        config_topic_ids = [
            t.get("topic_id") for t in topics
            if isinstance(t, dict) and t.get("topic_id")
        ]
        notes = collect_notes(
            db,
            compulsory,
            chapter,
            knowledge,
            list(dict.fromkeys([*tree_topic_ids, *config_topic_ids])),
        )
        qids, invalid_ids = _collect_ids(topics, extra_ids)
        questions = []
        missing_ids: list[int] = []
        for qid in dict.fromkeys(qids):
            q = shared_questions.get(qid)
            if q is None:
                missing_ids.append(qid)
            else:
                questions.append(q)
        entries.append({
            "section": {
                "compulsory": compulsory,
                "chapter": chapter,
                "knowledge": knowledge,
                "name": section.get("name") or knowledge,
                "topics": [
                    {"id": t.get("id"), "name": t.get("name"), "no": t.get("no"), "level": t.get("level"),
                     # 大纲自带的例题说明：无修订稿时作为「解题方法」的默认内容
                     "example": str(t.get("example") or "").strip()}
                    for t in (section.get("topics") or [])
                    if isinstance(t, dict)
                ],
            },
            "section_found": section_found,
            "notes": notes,
            "topics": topics,
            "extra_ids": extra_ids,
            "questions": questions,
            "missing_ids": missing_ids,
            "invalid_ids": invalid_ids,
        })
    return entries


def build_merged_preview(db: Session, sections: list) -> dict:
    """多节合并讲义：封面共用，正文按节串联，题目一次批量加载。"""
    settings = get_or_create_settings(db).to_dict()
    all_qids: list[int] = []
    for raw in sections or []:
        if not isinstance(raw, dict):
            continue
        qids, _ = _collect_ids(
            raw.get("topics") if isinstance(raw.get("topics"), list) else [],
            raw.get("extra_ids") if isinstance(raw.get("extra_ids"), list) else [],
        )
        all_qids.extend(qids)
    questions, _missing = _load_questions(db, list(dict.fromkeys(all_qids)))
    questions_by_id = {q["id"]: q for q in questions}
    entries = collect_sections(db, sections, questions_by_id=questions_by_id)
    return {
        "settings": settings,
        "sections": entries,
        "quote_pool": settings.get("quote_pool") or load_quotes(),
        "cover_bg": load_cover_bg(),
    }


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
    found = find_section(compulsory, chapter, knowledge)
    # 小节在当前教材树里找不到时照常出稿，但必须把事实交给前端如实提示，
    # 否则整份讲义的易错点/解题方法会静默变成「（待补充）」
    section_found = found is not None
    section = found or {"name": knowledge, "topics": []}
    tree_topic_ids = [
        t.get("id") for t in (section.get("topics") or [])
        if isinstance(t, dict) and t.get("id")
    ]
    # 修订稿按 topic_id 定位，而草稿 config 里的 topic_id 是编排当时的快照：
    # 换过教材树、考点被删除重建或移到别的小节之后，它与当前大纲不再相等。
    # 只按大纲查会让库里已有的修订稿被当成没写过，因此取两者并集。
    config_topic_ids = [
        t.get("topic_id") for t in (topics or [])
        if isinstance(t, dict) and t.get("topic_id")
    ]
    notes = collect_notes(
        db,
        compulsory,
        chapter,
        knowledge,
        list(dict.fromkeys([*tree_topic_ids, *config_topic_ids])),
    )

    qids, invalid_ids = _collect_ids(topics, extra_ids)
    questions, missing_ids = _load_questions(db, qids)

    settings = get_or_create_settings(db).to_dict()
    return {
        "settings": settings,
        "section": {
            "compulsory": compulsory,
            "chapter": chapter,
            "knowledge": knowledge,
            "name": section.get("name") or knowledge,
            "topics": [
                {"id": t.get("id"), "name": t.get("name"), "no": t.get("no"), "level": t.get("level"),
                 # 大纲自带的例题说明：无修订稿时作为「解题方法」的默认内容
                 "example": str(t.get("example") or "").strip()}
                for t in (section.get("topics") or [])
                if isinstance(t, dict)
            ],
        },
        # 当前教材树里是否解析到该小节：False 时考向地图与易错点必然不完整
        "section_found": section_found,
        "notes": notes,
        "topics": topics or [],
        "extra_ids": extra_ids or [],
        "questions": questions,
        # 编排/实战演练中引用但库中已不存在的题目 ID（前端据此提示）
        "missing_ids": missing_ids,
        # 无法解析成题目 ID 的编排引用（手改/外部写入的 config），与「已删除」分开报
        "invalid_ids": invalid_ids,
        # 用户在设置里改过的名言池优先，资源文件池只作兜底
        "quote_pool": settings.get("quote_pool") or load_quotes(),
        "cover_bg": load_cover_bg(),
    }


NOTE_TYPES = ("knowledge", "pit", "method")


def _all_curriculum_trees() -> list:
    """四套教材预设树 + 当前活跃树。

    lecture_notes 没有版本列，坐标只活在 note_key 里；只按当前活跃树判存活，
    会把另一套教材下写好的修订稿误报成孤儿。
    """
    from mathbank.curriculums import CURRICULUM_NAMES, load_curriculum

    trees = [get_current_curriculum() or {}]
    for code in CURRICULUM_NAMES:
        try:
            trees.append(load_curriculum(code))
        except Exception:  # 预设资源损坏时不得拖垮整个清单
            continue
    return [tree for tree in trees if isinstance(tree, dict) and tree]


def _topic_ids_of_section(tree: dict, compulsory: str, chapter: str, knowledge: str):
    """该小节在这棵树里的考向 ID 集合；小节解析不到时返回 None。"""
    for section in ((tree.get(compulsory) or {}).get(chapter) or []):
        if isinstance(section, str):
            if section == knowledge:
                return set()
            continue
        if str(section.get("name") or "") == knowledge:
            return {
                str(topic["id"])
                for topic in (section.get("topics") or [])
                if isinstance(topic, dict) and topic.get("id")
            }
    return None


def find_orphan_notes(db: Session) -> list:
    """列出在任何教材版本里都定位不到的修订稿。

    考向树重建（换 ID）或改名小节后，旧 note_key 永远查不回来，用户在界面上
    既看不见也删不掉。本函数只报告，删除仍走显式的 DELETE /api/lecture/notes。
    """
    trees = _all_curriculum_trees()
    orphans = []
    for row in db.query(LectureNote).order_by(LectureNote.note_key).all():
        segments = split_note_key(row.note_key)
        segments += [""] * (4 - len(segments))
        note_type, compulsory, chapter, knowledge = segments[:4]
        topic_id = segments[4] if len(segments) > 4 else ""
        topic_ids: set = set()
        section_found = False
        for tree in trees:
            found = _topic_ids_of_section(tree, compulsory, chapter, knowledge)
            if found is not None:
                section_found = True
                topic_ids |= found
        if not section_found:
            reason = "section_missing"
        elif note_type not in NOTE_TYPES:
            reason = "unknown_type"
        elif note_type == "knowledge":
            continue
        elif topic_id and topic_id in topic_ids:
            continue
        else:
            reason = "topic_missing"
        orphans.append({
            "note_key": row.note_key,
            "note_type": note_type,
            "compulsory": compulsory,
            "chapter": chapter,
            "knowledge": knowledge,
            "topic_id": topic_id,
            "reason": reason,
            "content_chars": len(row.content or ""),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        })
    return orphans
