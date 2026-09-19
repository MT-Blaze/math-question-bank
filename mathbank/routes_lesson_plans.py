"""Lesson plan (教案) CRUD endpoints plus 考向-tree scaffolding.

A lesson plan is authored against the 考向 tree of one 小节: its 考向
(L1–L5 items, names and examples) become the mind map and the knowledge
blocks, and questions can be attached to each 考向.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from mathbank.auth import require_local_token
from mathbank.curriculums import DEFAULT_DIFFICULTIES, get_current_curriculum
from mathbank.database import LessonPlan, get_db

router = APIRouter(tags=["lesson-plans"])

_UPDATABLE_FIELDS = (
    "title",
    "grade",
    "compulsory",
    "chapter",
    "knowledge",
    "objectives",
    "key_points",
    "difficult_points",
    "board_design",
    "reflection",
    "status",
)

_JSON_FIELDS = (
    "teaching_process",
    "question_refs",
    "knowledge_content",
    "mindmap",
)

_DEFAULT_PROCESS = {
    "导入": "",
    "新知讲解": "",
    "例题精讲": "",
    "课堂练习": "",
    "课堂小结": "",
    "作业布置": "",
}


def _level_labels() -> dict:
    labels = {}
    for item in DEFAULT_DIFFICULTIES:
        if isinstance(item, dict):
            value = str(item.get("value") or "").strip()
            label = str(item.get("label") or "").strip()
            if value:
                labels[value] = label or value
    return labels


def _normalize_question_refs(raw) -> dict:
    """Accept both the legacy {stage: [ids]} shape and the current items list."""

    if not isinstance(raw, dict):
        return {"items": []}
    if isinstance(raw.get("items"), list):
        return {"items": [item for item in raw["items"] if isinstance(item, dict)]}
    items = []
    for stage, value in raw.items():
        if not isinstance(value, list):
            continue
        for entry in value:
            if isinstance(entry, dict):
                item = dict(entry)
            else:
                item = {"question_id": entry}
            item.setdefault("stage", stage)
            items.append(item)
    return {"items": items}


def _as_json_text(raw, fallback: str) -> str:
    if isinstance(raw, str):
        return raw
    try:
        payload = raw if raw is not None else json.loads(fallback)
        return json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        return fallback


def _find_section(compulsory: str, chapter: str, knowledge: str):
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


def _build_from_section(compulsory: str, chapter: str, knowledge: str) -> dict:
    """Turn a 小节's 考向 tree into mind map + knowledge blocks."""

    section = _find_section(compulsory, chapter, knowledge)
    if section is None:
        raise HTTPException(status_code=404, detail="大纲中未找到该小节")

    topics = [t for t in (section.get("topics") or []) if isinstance(t, dict)]
    labels = _level_labels()

    blocks = []
    grouped = {}
    for index, topic in enumerate(topics, 1):
        level = str(topic.get("level") or "").strip()
        number = topic.get("no") or index
        block = {
            "id": "kx_%d" % index,
            "no": number,
            "level": level,
            "title": str(topic.get("name") or "").strip(),
            "topic_id": topic.get("id") or "",
            "example": str(topic.get("example") or "").strip(),
            "content": "",
        }
        blocks.append(block)
        grouped.setdefault(level or "未分级", []).append(block)

    ordered_levels = [lv for lv in labels if lv in grouped]
    ordered_levels += [lv for lv in grouped if lv not in labels]

    root_children = []
    for level in ordered_levels:
        children = []
        for block in grouped[level]:
            text = block["title"]
            if block.get("no"):
                text = "考向%s：%s" % (block["no"], text)
            children.append({"text": text, "topic_id": block.get("topic_id", "")})
        root_children.append({
            "text": labels.get(level, level),
            "level": level,
            "children": children,
        })

    return {
        "blocks": blocks,
        "mindmap": {"root": {"text": knowledge or chapter or "教案", "children": root_children}},
        "topic_ids": [b["topic_id"] for b in blocks if b.get("topic_id")],
        "counts": dict((level, len(grouped[level])) for level in ordered_levels),
    }


@router.get("/api/lesson-plans")
def list_lesson_plans(
    grade: str = None,
    compulsory: str = None,
    status: str = None,
    db: Session = Depends(get_db),
):
    query = db.query(LessonPlan)
    if grade:
        query = query.filter(LessonPlan.grade == grade)
    if compulsory:
        query = query.filter(LessonPlan.compulsory == compulsory)
    if status:
        query = query.filter(LessonPlan.status == status)
    plans = query.order_by(LessonPlan.updated_at.desc()).all()
    return [p.to_dict() for p in plans]


@router.get("/api/lesson-plans/section-topics")
def get_section_topics(compulsory: str, chapter: str, knowledge: str):
    """Preview the 考向 tree a lesson plan would be scaffolded from."""

    return _build_from_section(compulsory, chapter, knowledge)


@router.get("/api/lesson-plans/{plan_id}")
def get_lesson_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.query(LessonPlan).filter(LessonPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="教案不存在")
    return plan.to_dict()


@router.post("/api/lesson-plans")
def create_lesson_plan(
    payload: dict,
    db: Session = Depends(get_db),
    _token: str = Depends(require_local_token),
):
    plan = LessonPlan(
        title=payload.get("title") or "未命名教案",
        grade=payload.get("grade", ""),
        compulsory=payload.get("compulsory", ""),
        chapter=payload.get("chapter", ""),
        knowledge=payload.get("knowledge", ""),
        topic_ids=_as_json_text(payload.get("topic_ids", []), "[]"),
        objectives=payload.get("objectives", ""),
        key_points=payload.get("key_points", ""),
        difficult_points=payload.get("difficult_points", ""),
        teaching_process=_as_json_text(payload.get("teaching_process") or _DEFAULT_PROCESS, "{}"),
        board_design=payload.get("board_design", ""),
        reflection=payload.get("reflection", ""),
        question_refs=_as_json_text(_normalize_question_refs(payload.get("question_refs")), "{}"),
        knowledge_content=_as_json_text(payload.get("knowledge_content", {}), "{}"),
        mindmap=_as_json_text(payload.get("mindmap", {}), "{}"),
        status=payload.get("status", "draft"),
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan.to_dict()


@router.post("/api/lesson-plans/from-section")
def create_lesson_plan_from_section(
    payload: dict,
    db: Session = Depends(get_db),
    _token: str = Depends(require_local_token),
):
    """Create a lesson plan scaffolded from one 小节's 考向 tree."""

    compulsory = str(payload.get("compulsory") or "").strip()
    chapter = str(payload.get("chapter") or "").strip()
    knowledge = str(payload.get("knowledge") or "").strip()
    if not (compulsory and chapter and knowledge):
        raise HTTPException(status_code=400, detail="请先选择册次 / 章节 / 小节")

    built = _build_from_section(compulsory, chapter, knowledge)
    title = str(payload.get("title") or "").strip() or "%s 教案" % knowledge

    plan = LessonPlan(
        title=title,
        grade=payload.get("grade", ""),
        compulsory=compulsory,
        chapter=chapter,
        knowledge=knowledge,
        topic_ids=json.dumps(built["topic_ids"], ensure_ascii=False),
        objectives=payload.get("objectives", ""),
        key_points=payload.get("key_points", ""),
        difficult_points=payload.get("difficult_points", ""),
        teaching_process=json.dumps(_DEFAULT_PROCESS, ensure_ascii=False),
        board_design="",
        reflection="",
        question_refs=json.dumps({"items": []}, ensure_ascii=False),
        knowledge_content=json.dumps({"blocks": built["blocks"]}, ensure_ascii=False),
        mindmap=json.dumps(built["mindmap"], ensure_ascii=False),
        status="draft",
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    result = plan.to_dict()
    result["kaoxiang_counts"] = built["counts"]
    return result


@router.put("/api/lesson-plans/{plan_id}")
def update_lesson_plan(
    plan_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    _token: str = Depends(require_local_token),
):
    plan = db.query(LessonPlan).filter(LessonPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="教案不存在")
    for field in _UPDATABLE_FIELDS:
        if field in payload:
            setattr(plan, field, payload[field])
    for field in _JSON_FIELDS:
        if field in payload:
            value = payload[field]
            if field == "question_refs":
                value = _normalize_question_refs(value)
            setattr(plan, field, _as_json_text(value, "{}"))
    if "topic_ids" in payload:
        plan.topic_ids = _as_json_text(payload["topic_ids"], "[]")
    db.commit()
    db.refresh(plan)
    return plan.to_dict()


@router.delete("/api/lesson-plans/{plan_id}")
def delete_lesson_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    _token: str = Depends(require_local_token),
):
    plan = db.query(LessonPlan).filter(LessonPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="教案不存在")
    db.delete(plan)
    db.commit()
    return {"ok": True}
