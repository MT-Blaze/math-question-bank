"""Multi-kaoxiang (一题多考点) helpers for question_topics rows.

The primary slot (sort_order == 0) always mirrors the question's main
classification fields; extra slots (sort_order 1..N) hold additional
kaoxiang on the same question. Version-aware: every row carries the active
curriculum version code so switching textbooks never mixes trees.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from sqlalchemy.orm import Session

from mathbank.database import QuestionTopic

MAX_EXTRA_TOPICS = 3


def topic_id_for(
    compulsory: str,
    chapter: str,
    knowledge: str,
    name: str,
) -> str:
    """Deterministic stable topic ID.

    The ID is derived from the slot context (册次/章节/小节/考点名) the first
    time a topic is seen; once created it is preserved across renames, so
    questions store only this ID and never a name snapshot. The same name in
    the same section always maps to the same ID (cross-database friendly);
    renaming keeps the existing ID because callers pass the old ``id`` back.
    """
    raw = f"{compulsory}||{chapter}||{knowledge}||{name}"
    return "k_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def normalize_topic_list(
    topics: Any,
    compulsory: str,
    chapter: str,
    knowledge: str,
) -> list[dict[str, str]]:
    """Upgrade topic entries to [{id, name}] objects.

    - plain strings gain a deterministic id (auto-created topics);
    - {name} entries gain a deterministic id;
    - {id, name} entries keep their id, which is what makes renames safe.
    """
    result: list[dict[str, str]] = []
    for item in topics or []:
        if isinstance(item, str):
            name = str(item).strip()
            if not name:
                continue
            result.append(
                {
                    "id": topic_id_for(compulsory, chapter, knowledge, name),
                    "name": name,
                }
            )
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            tid = str(item.get("id") or "").strip()
            if not tid:
                tid = topic_id_for(compulsory, chapter, knowledge, name)
            result.append({"id": tid, "name": name})
    return result


def topic_id_to_name(curriculum: Any, topic_id: str) -> str:
    """Translate a topic ID to its current display name (no snapshots)."""
    tid = str(topic_id or "").strip()
    if not tid:
        return ""
    for chapters in (curriculum or {}).values():
        if not isinstance(chapters, dict):
            continue
        for sections in chapters.values():
            if not isinstance(sections, list):
                continue
            for section in sections:
                if not isinstance(section, dict):
                    continue
                for topic in section.get("topics") or []:
                    if isinstance(topic, dict) and str(topic.get("id") or "") == tid:
                        return str(topic.get("name") or "")
    return ""


def topic_ids_for_name(curriculum: Any, name: str) -> list[str]:
    """All topic IDs across the whole tree whose current name equals `name`."""
    target = str(name or "").strip()
    if not target:
        return []
    ids: list[str] = []
    for compulsory, chapters in (curriculum or {}).items():
        if not isinstance(chapters, dict):
            continue
        for chapter, sections in chapters.items():
            if not isinstance(sections, list):
                continue
            for section in sections:
                if not isinstance(section, dict):
                    continue
                knowledge = str(section.get("name") or "")
                for topic in section.get("topics") or []:
                    if isinstance(topic, dict) and str(topic.get("name") or "").strip() == target:
                        ids.append(str(topic.get("id") or ""))
                    elif isinstance(topic, str) and str(topic).strip() == target:
                        ids.append(topic_id_for(compulsory, chapter, knowledge, topic))
    return ids


def topic_id_for_slot(
    curriculum: Any,
    compulsory: str,
    chapter: str,
    knowledge: str,
    name: str,
) -> str:
    """Exact slot-level match (册次/章节/小节/考点). Returns "" when missing."""
    target = str(name or "").strip()
    for sections in ((curriculum or {}).get(compulsory, {}).get(chapter, []) or []):
        if not isinstance(sections, dict) or sections.get("name") != knowledge:
            continue
        for topic in sections.get("topics") or []:
            if isinstance(topic, dict) and str(topic.get("name") or "").strip() == target:
                return str(topic.get("id") or "")
            if isinstance(topic, str) and str(topic).strip() == target:
                return topic_id_for(compulsory, chapter, knowledge, topic)
    return ""


def parse_extra_topics(raw: str | None) -> list[dict[str, str]]:
    """Safely parse the extra_topics form field into a clean list of slots.

    Each slot is a dict with only the four classification keys; empty and
    malformed entries are dropped; the list is capped at MAX_EXTRA_TOPICS.
    """
    if not raw or not str(raw).strip():
        return []
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    result: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        slot = {
            "compulsory": str(item.get("compulsory") or "").strip(),
            "chapter": str(item.get("chapter") or "").strip(),
            "knowledge": str(item.get("knowledge") or "").strip(),
            "topic": str(item.get("topic") or "").strip(),
            "topic_id": str(item.get("topic_id") or "").strip(),
        }
        if not (slot["compulsory"] or slot["chapter"] or slot["knowledge"] or slot["topic"]):
            continue
        result.append(slot)
    return result[:MAX_EXTRA_TOPICS]


def sync_question_topics(
    db: Session,
    question_id: int,
    version_code: str,
    primary: dict[str, str],
    extra: Iterable[dict[str, str]],
) -> list[dict[str, Any]]:
    """Rewrite all question_topics rows for one question in one delete+insert.

    The primary slot is always first (sort_order 0); extra slots follow.
    Returns the new full list (as dicts) for immediate API responses.
    """
    version = str(version_code or "").strip() or "A"
    rows: list[dict[str, str]] = []
    primary_slot = {
        "compulsory": str(primary.get("compulsory") or "").strip(),
        "chapter": str(primary.get("chapter") or "").strip(),
        "knowledge": str(primary.get("knowledge") or "").strip(),
        "topic": str(primary.get("topic") or "").strip(),
        "topic_id": str(primary.get("topic_id") or "").strip(),
    }
    if primary_slot["compulsory"] or primary_slot["chapter"] or primary_slot["knowledge"] or primary_slot["topic"]:
        rows.append(primary_slot)
    seen: set[tuple[str, str, str, str]] = {
        (r["compulsory"], r["chapter"], r["knowledge"], r["topic"]) for r in rows
    }
    for item in parse_extra_topics(json.dumps(list(extra), ensure_ascii=False)):
        key = (item["compulsory"], item["chapter"], item["knowledge"], item["topic"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(item)
        if len(rows) - 1 >= MAX_EXTRA_TOPICS:
            break

    db.query(QuestionTopic).filter(QuestionTopic.question_id == question_id).delete(
        synchronize_session=False
    )
    for order, slot in enumerate(rows):
        db.add(
            QuestionTopic(
                question_id=question_id,
                version_code=version,
                compulsory=slot["compulsory"],
                chapter=slot["chapter"],
                knowledge=slot["knowledge"],
                topic_id=slot.get("topic_id") or "",
                sort_order=order,
            )
        )
    db.flush()
    return load_question_topics(db, question_id, version)


def load_question_topics(
    db: Session,
    question_id: int,
    version_code: str,
) -> list[dict[str, Any]]:
    """Return all topic slots for one question under the given version, ordered."""
    version = str(version_code or "").strip() or "A"
    rows = (
        db.query(QuestionTopic)
        .filter(
            QuestionTopic.question_id == question_id,
            QuestionTopic.version_code == version,
        )
        .order_by(QuestionTopic.sort_order.asc(), QuestionTopic.id.asc())
        .all()
    )
    return [row.to_dict() for row in rows]


def load_questions_topics_map(
    db: Session,
    question_ids: Iterable[int],
    version_code: str,
) -> dict[int, list[dict[str, Any]]]:
    """Batch-load topic slots for many questions (one IN query)."""
    ids = [int(i) for i in question_ids if i]
    result: dict[int, list[dict[str, Any]]] = {qid: [] for qid in ids}
    if not ids:
        return result
    version = str(version_code or "").strip() or "A"
    rows = (
        db.query(QuestionTopic)
        .filter(
            QuestionTopic.question_id.in_(ids),
            QuestionTopic.version_code == version,
        )
        .order_by(QuestionTopic.sort_order.asc(), QuestionTopic.id.asc())
        .all()
    )
    for row in rows:
        result.setdefault(row.question_id, []).append(row.to_dict())
    return result
