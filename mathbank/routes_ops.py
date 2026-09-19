"""Operational endpoints layered on the core question store.

* import batches (list / whole-batch rollback),
* TikZ version history,
* two-way specification-table (双向细目表) paper picking.
"""

from __future__ import annotations

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from mathbank.database import ImportBatch, Question, TikzVersion, get_db
from mathbank.question_metadata import _load_ids

router = APIRouter()


def _dump(model) -> dict:
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


@router.get("/api/import-batches")
def list_import_batches(limit: int = 20, db: Session = Depends(get_db)):
    limit = max(1, min(int(limit or 20), 100))
    rows = db.query(ImportBatch).order_by(ImportBatch.id.desc()).limit(limit).all()
    return {"status": "success", "batches": [b.to_dict() for b in rows]}


@router.post("/api/import-batches/{batch_id}/rollback")
def rollback_import_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="未找到该导入批次")
    ids = _load_ids(batch.question_ids)
    deleted = 0
    if ids:
        # DB-level FK actions cascade the junction rows and null out paper links,
        # so the frozen paper snapshots survive the rollback.
        deleted = (
            db.query(Question)
            .filter(Question.id.in_(ids))
            .delete(synchronize_session=False)
        )
    batch.question_ids = "[]"
    batch.question_count = 0
    batch.rolled_back = 1
    db.commit()
    return {"status": "success", "batch_id": batch_id, "deleted": int(deleted)}


@router.get("/api/questions/{question_id}/tikz-versions")
def list_tikz_versions(question_id: int, db: Session = Depends(get_db)):
    rows = (
        db.query(TikzVersion)
        .filter(TikzVersion.question_id == question_id)
        .order_by(TikzVersion.slot.asc(), TikzVersion.version.asc())
        .all()
    )
    return {"status": "success", "versions": [row.to_dict() for row in rows]}


class BlueprintRow(BaseModel):
    question_type: Optional[str] = None
    knowledge: Optional[str] = None
    difficulty: Optional[str] = None
    difficulty_min: Optional[float] = None
    difficulty_max: Optional[float] = None
    count: int = 5


class BlueprintRequest(BaseModel):
    rows: List[BlueprintRow] = []
    exclude_ids: List[int] = []


@router.post("/api/questions/blueprint-match")
def blueprint_match(payload: BlueprintRequest, db: Session = Depends(get_db)):
    """双向细目表挑题：按 题型 × 知识点 × 难度 逐行匹配候选题目。"""

    used = {int(i) for i in (payload.exclude_ids or [])}
    result = []
    for row in payload.rows:
        query = db.query(Question)
        if row.question_type:
            query = query.filter(Question.question_type == row.question_type)
        if row.knowledge:
            like = f"%{row.knowledge}%"
            query = query.filter(
                or_(
                    Question.category_knowledge.like(like),
                    Question.knowledge_points.like(like),
                )
            )
        if row.difficulty:
            query = query.filter(Question.difficulty == row.difficulty)
        if row.difficulty_min is not None:
            query = query.filter(Question.difficulty_index >= float(row.difficulty_min))
        if row.difficulty_max is not None:
            query = query.filter(Question.difficulty_index <= float(row.difficulty_max))
        if used:
            query = query.filter(~Question.id.in_(used))
        count = max(1, min(int(row.count or 5), 50))
        items = query.order_by(Question.id.desc()).limit(count).all()
        for item in items:
            used.add(item.id)
        result.append(
            {"row": _dump(row), "questions": [i.to_summary_dict() for i in items]}
        )
    return {"status": "success", "rows": result}
