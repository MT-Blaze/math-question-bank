"""讲义工作台路由：设置 / 知识块修订稿 / 讲义草稿 / 预览组装。

讲义预览不做服务端渲染，返回结构化数据（题目 markdown、知识块修订、
封面库、名言池）由前端 A4 打印视图渲染；导出复用组卷引擎。
"""

from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import quote

import main as _main  # noqa: E402  测试接缝：延迟绑定，编译与上传目录一律运行时查找

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from mathbank.auth import require_local_token
from mathbank.database import LectureDraft, LectureNote, get_db
from mathbank.latex_diagnostics import build_local_latex_diagnostic
from mathbank.lecture_export import build_lecture_latex, collect_lecture_images
from mathbank.lecture_helper import (
    build_merged_preview,
    build_preview,
    find_orphan_notes,
    get_or_create_settings,
    load_cover_bg,
    load_quotes,
    note_key,
    split_note_key,
)


def _preview_payload(payload: dict, db: Session) -> dict:
    """preview/export 共用载荷分派：sections 为多节合并，旧单节字段照常。"""
    sections = payload.get("sections")
    if isinstance(sections, list) and sections:
        merged = build_merged_preview(db, sections)
        if not merged["sections"]:
            raise HTTPException(status_code=400, detail="sections 里没有有效的册次 / 章节 / 小节")
        return merged
    compulsory = str(payload.get("compulsory") or "").strip()
    chapter = str(payload.get("chapter") or "").strip()
    knowledge = str(payload.get("knowledge") or "").strip()
    if not (compulsory and chapter and knowledge):
        raise HTTPException(status_code=400, detail="请先选择册次 / 章节 / 小节")
    return build_preview(
        db,
        compulsory,
        chapter,
        knowledge,
        topics=payload.get("topics"),
        extra_ids=payload.get("extra_ids"),
    )

router = APIRouter(tags=["lecture"])


@router.get("/api/lecture/settings")
def get_settings(db: Session = Depends(get_db)):
    return get_or_create_settings(db).to_dict()


@router.put("/api/lecture/settings")
def update_settings(
    payload: dict,
    db: Session = Depends(get_db),
    _token: str = Depends(require_local_token),
):
    setting = get_or_create_settings(db)
    if "cover_title" in payload:
        setting.cover_title = str(payload.get("cover_title") or "")[:200]
    if "cover_subtitle" in payload:
        setting.cover_subtitle = str(payload.get("cover_subtitle") or "同步讲义")[:200]
    if "cover_brand" in payload:
        setting.cover_brand = str(payload.get("cover_brand") or "")[:200]
    if "cover_author" in payload:
        setting.cover_author = str(payload.get("cover_author") or "")[:200]
    if "bound_mode" in payload:
        mode = str(payload.get("bound_mode") or "loose")
        setting.bound_mode = mode if mode in ("loose", "bound") else "loose"
    if "quote_pool" in payload:
        quotes = payload.get("quote_pool")
        if isinstance(quotes, list):
            setting.quote_pool = json.dumps([str(q) for q in quotes if str(q).strip()][:1000], ensure_ascii=False)
    db.commit()
    db.refresh(setting)
    return setting.to_dict()


@router.get("/api/lecture/cover-bg")
def get_cover_bg():
    return {"cover_bg": load_cover_bg()}


@router.get("/api/lecture/quotes")
def get_quotes():
    return {"quote_pool": load_quotes()}


@router.get("/api/lecture/notes")
def get_notes(
    note_type: str = None,
    compulsory: str = None,
    chapter: str = None,
    knowledge: str = None,
    topic_id: str = None,
    db: Session = Depends(get_db),
):
    """查知识块修订稿。按 note_key 各段精确匹配（LIKE 会让「集合」误命中
    「集合的基本运算」，且把 topic_id 里的下划线当通配符）。"""
    query = db.query(LectureNote)
    if note_type:
        query = query.filter(LectureNote.note_type == note_type)
    rows = query.order_by(LectureNote.updated_at.desc()).all()
    if not (compulsory or chapter or knowledge or topic_id):
        return [row.to_dict() for row in rows]

    def _segment_matches(row: LectureNote) -> bool:
        # note_key 格式：type:compulsory:chapter:knowledge[:topic_id]，段内分隔符已转义
        parts = split_note_key(row.note_key)
        if compulsory and (len(parts) < 2 or parts[1] != compulsory):
            return False
        if chapter and (len(parts) < 3 or parts[2] != chapter):
            return False
        if knowledge and (len(parts) < 4 or parts[3] != knowledge):
            return False
        if topic_id and (len(parts) < 5 or parts[4] != topic_id):
            return False
        return True

    return [row.to_dict() for row in rows if _segment_matches(row)]


@router.put("/api/lecture/notes")
def save_note(
    payload: dict,
    db: Session = Depends(get_db),
    _token: str = Depends(require_local_token),
):
    """保存知识块修订稿。payload: {note_type, compulsory, chapter, knowledge, topic_id?, content}"""
    note_type = str(payload.get("note_type") or "knowledge")
    if note_type not in ("knowledge", "pit", "method"):
        raise HTTPException(status_code=400, detail="note_type 仅支持 knowledge / pit / method")
    compulsory = str(payload.get("compulsory") or "").strip()
    chapter = str(payload.get("chapter") or "").strip()
    knowledge = str(payload.get("knowledge") or "").strip()
    if not (compulsory and chapter and knowledge):
        raise HTTPException(status_code=400, detail="请先选择册次 / 章节 / 小节")
    topic_id = str(payload.get("topic_id") or "").strip()
    if note_type != "knowledge" and not topic_id:
        # 没有考向 ID 时键会退化成 4 段，多个考向将共写同一行并互相覆盖
        raise HTTPException(status_code=400, detail="易错点/解题方法必须挂在具体考向下，请先重新加载小节")
    key = note_key(note_type, compulsory, chapter, knowledge, topic_id)
    content = str(payload.get("content") or "")
    row = db.query(LectureNote).filter(LectureNote.note_key == key).first()
    if row is None:
        row = LectureNote(note_key=key, note_type=note_type, content=content)
        db.add(row)
    else:
        row.content = content
        row.note_type = note_type
    db.commit()
    db.refresh(row)
    return row.to_dict()


@router.delete("/api/lecture/notes")
def delete_note(
    note_type: str,
    compulsory: str,
    chapter: str,
    knowledge: str,
    topic_id: str = None,
    db: Session = Depends(get_db),
    _token: str = Depends(require_local_token),
):
    """删除知识块修订稿（恢复默认）。"""
    key = note_key(note_type, compulsory, chapter, knowledge, topic_id or "")
    row = db.query(LectureNote).filter(LectureNote.note_key == key).first()
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}


@router.get("/api/lecture/orphan-notes")
def get_orphan_notes(db: Session = Depends(get_db)):
    """列出定位不到的修订稿。

    考向树重建会换掉 topic_id、小节改名会换掉坐标，旧 note_key 从此永远查不
    回来——界面上既看不见也删不掉。本接口只报告，删除仍由用户逐条走
    DELETE /api/lecture/notes。
    """
    return {"items": find_orphan_notes(db)}


@router.post("/api/lecture/preview")
def lecture_preview(
    payload: dict,
    db: Session = Depends(get_db),
):
    """组装讲义预览数据：设置 + 考向树 + 知识块修订 + 题目详情。

    载荷两种形态：``sections=[{compulsory,chapter,knowledge,topics,extra_ids},...]``
    为多节合并讲义（按勾选顺序串联）；旧的单节三坐标字段照常兼容。
    """
    return _preview_payload(payload, db)


@router.post("/api/lecture/export/pdf")
def export_lecture_pdf(payload: dict, db: Session = Depends(get_db)):
    """把当前讲义编排交给组卷引擎（exam-zh + XeLaTeX）编译 PDF。

    载荷与 preview 同口径：``sections`` 多节合并，或旧的单节三坐标字段。
    知识块存的是 HTML / @mindmap JSON，按保守规则转换；转换不了的部分经
    ``X-MathBank-Export-Notes`` 响应头回传（URL 编码、分号分隔），前端必须
    显示而不是静默吞掉。编译失败返回本地 LaTeX 诊断，不触碰任何付费模型。
    """
    view = str(payload.get("view") or "jiexi")
    if view not in ("yuan", "jiexi", "daan"):
        raise HTTPException(status_code=400, detail="view 仅支持 yuan / jiexi / daan")

    preview = payload.get("preview") or _preview_payload(payload, db)
    tex_content, degradations = build_lecture_latex(preview, view=view)
    questions_pool = list((preview.get("questions") or []))
    for entry in (preview.get("sections") or []):
        questions_pool.extend(entry.get("questions") or [])
    image_paths = collect_lecture_images(
        questions_pool, _main.UPLOAD_DIR, _main.UPLOAD_DIR_REL
    )
    pdf_bytes, log_or_err = _main.compile_tex_to_pdf(tex_content, image_paths)
    if not pdf_bytes:
        diagnostic = build_local_latex_diagnostic(str(log_or_err or ""), tex_content)
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": diagnostic.get("summary") or "讲义 PDF 编译失败",
                "diagnostic": diagnostic,
                "degradations": degradations,
            },
        )
    headers = {
        "Content-Disposition": 'inline; filename="lecture_%s_%s.pdf"'
        % (view, datetime.now().strftime("%Y%m%d_%H%M%S")),
    }
    if degradations:
        headers["X-MathBank-Export-Notes"] = quote("；".join(degradations))
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)


@router.get("/api/lecture/drafts")
def list_drafts(
    grade: str = None,
    compulsory: str = None,
    status: str = None,
    db: Session = Depends(get_db),
):
    query = db.query(LectureDraft)
    if grade:
        query = query.filter(LectureDraft.grade == grade)
    if compulsory:
        query = query.filter(LectureDraft.compulsory == compulsory)
    if status:
        query = query.filter(LectureDraft.status == status)
    drafts = query.order_by(LectureDraft.updated_at.desc()).all()
    return [d.to_dict() for d in drafts]


@router.get("/api/lecture/drafts/{draft_id}")
def get_draft(draft_id: int, db: Session = Depends(get_db)):
    draft = db.query(LectureDraft).filter(LectureDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="讲义草稿不存在")
    return draft.to_dict()


@router.post("/api/lecture/drafts")
def create_draft(
    payload: dict,
    db: Session = Depends(get_db),
    _token: str = Depends(require_local_token),
):
    draft = LectureDraft(
        title=str(payload.get("title") or "未命名讲义")[:200],
        grade=str(payload.get("grade") or "")[:50],
        compulsory=str(payload.get("compulsory") or "")[:100],
        chapter=str(payload.get("chapter") or "")[:100],
        knowledge=str(payload.get("knowledge") or "")[:100],
        config=json.dumps(payload.get("config") or {}, ensure_ascii=False),
        knowledge_src=str(payload.get("knowledge_src") or ""),
        status=str(payload.get("status") or "draft")[:20],
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft.to_dict()


@router.put("/api/lecture/drafts/{draft_id}")
def update_draft(
    draft_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    _token: str = Depends(require_local_token),
):
    draft = db.query(LectureDraft).filter(LectureDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="讲义草稿不存在")
    for field in ("title", "grade", "compulsory", "chapter", "knowledge", "knowledge_src", "status"):
        if field in payload:
            setattr(draft, field, str(payload[field] or ""))
    if "config" in payload and isinstance(payload["config"], dict):
        draft.config = json.dumps(payload["config"], ensure_ascii=False)
    db.commit()
    db.refresh(draft)
    return draft.to_dict()


@router.delete("/api/lecture/drafts/{draft_id}")
def delete_draft(
    draft_id: int,
    db: Session = Depends(get_db),
    _token: str = Depends(require_local_token),
):
    draft = db.query(LectureDraft).filter(LectureDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="讲义草稿不存在")
    db.delete(draft)
    db.commit()
    return {"ok": True}
