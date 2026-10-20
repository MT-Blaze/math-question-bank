"""Question domain API routes (extracted from main.py).

All question CRUD / search / duplicate-scan / associate endpoints live here.
Shared helpers are imported directly from their owning mathbank.* module; only
symbols genuinely defined on the app entrypoint (main) — a few path constants
and two helper functions — are still imported from `main`.
"""
import json
import re
import shutil
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException
from fastapi.responses import JSONResponse
from pathlib import Path
from sqlalchemy import and_, or_, cast, func
from sqlalchemy import Integer as _SQLInteger
from sqlalchemy.orm import Session
from typing import Optional

from mathbank.asset_security import (
    AssetSecurityError,
    SAFE_IMAGE_EXTENSIONS,
    normalize_answer_tikz_assets,
    normalize_content_tikz_assets,
    normalize_optional_upload_asset_reference,
    normalize_upload_asset_reference,
    normalize_upload_asset_references,
    resolve_upload_asset,
    trash_upload_asset,
)
from mathbank.database import (
    FIGURE_ALIGN_VALUES,
    Paper,
    PaperQuestion,
    Question,
    QuestionCurriculum,
    QuestionTopic,
    QuestionFingerprint as StoredQuestionFingerprint,
    get_db,
    normalize_figure_size,
    sync_question_tags,
    sync_question_knowledge,
)
from mathbank.metadata import (
    _decorate_topic_field,
    _decorate_topic_slots,
    _flush_auto_topics,
    _resolve_extra_topic_ids,
    _resolve_topic_id,
    _topic_display,
    get_active_version_code,
    get_metadata_cache,
    schedule_database_export,
)
from mathbank.school_library import resolve_school_label
from mathbank.question_duplicate_service import (
    batch_local_matches,
    exact_duplicate_ids,
    find_indexed_candidates,
    fingerprint_for_question,
    index_status as duplicate_index_status,
    select_answer_images,
    select_visible_question_images,
    tikz_signatures as build_tikz_signatures,
    upsert_question_fingerprint,
    visible_image_signatures as build_visible_image_signatures,
)
from mathbank.question_duplicates import (
    QuestionDuplicateInput,
    build_question_fingerprint,
)
from mathbank.topics_helper import (
    load_question_topics,
    load_questions_topics_map,
    parse_extra_topics,
    sync_question_topics,
    topic_id_for_slot,
    topic_ids_for_name,
)
from main import (
    TMP_UPLOAD_DIR,
    UPLOAD_DIR,
    UPLOAD_DIR_REL,
    get_seq_mapping,
    normalize_fillin_macro,
)
from mathbank.question_metadata import (
    extract_structured_options,
    record_import_batch_question,
    record_tikz_versions,
)
from mathbank.fraction_style import normalize_fraction_style
from mathbank.latex_normalize import (
    _split_merged_choice_items,
    normalize_choice_paren_macro,
    normalize_for_storage,
    normalize_stem_for_storage,
)
from mathbank.question_types import enforce_multi_choice_from_answer

router = APIRouter()


def _normalize_pair_for_db(content: str, answer_markdown: str, question_type: str,
                           paper_question_no: str) -> tuple[str, str, str, str]:
    """写库边界统一归一化：题干/解析走同一条链，题干里残留的原卷题号回填到 paper_question_no。

    A4 预览与 exam-zh 导出都会自行编号，题干自带题号会排成「1. 1. …」，所以在这里剥离；
    已经填了原题号的（拆卷卡片、手填）保留用户自己的值不覆盖。

    题型兜底（答案 ≥2 个独立字母 → multi_choice）必须**先于**归一化：结构层的选项
    包装器带「非选择题不包 choices」的防误伤闸门，题型晚改一步，纯文本选项目前仍是
    选项区形态，包装就会被跳过——等下一趟保存才改对，同一题两次入库得到两种源码。
    """
    question_type = enforce_multi_choice_from_answer(
        question_type, content, answer_markdown
    )
    lead_no, new_content = normalize_stem_for_storage(content, question_type)
    new_answer = normalize_for_storage(answer_markdown, is_answer=True)
    if lead_no and not (paper_question_no or "").strip():
        paper_question_no = lead_no
    return new_content, new_answer, question_type, paper_question_no


def _parse_int_or_none(value):
    """Parse a form string into an int or None (empty/invalid -> None)."""

    value = str(value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_int_or_default(value, default):
    """Parse a form string into an int, falling back to ``default``."""

    parsed = _parse_int_or_none(value)
    return parsed if parsed is not None else default


def _parse_float_or_none(value, *, minimum=0.0, maximum=1.0):
    """Parse a form string into a clamped float, or None when empty/invalid.

    Used for the 0~1 difficulty index; out-of-range values are clamped rather
    than rejected so a stray "1.2" degrades to 1.0 instead of raising.
    """

    value = str(value or "").strip()
    if not value:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return max(minimum, min(maximum, parsed))


def _normalize_text_list(value):
    """Normalize a comma/、 separated payload into a JSON-array string.

    Shared by the multi-valued metadata fields (analysis methods, error-prone
    points) so their storage format stays identical.
    """

    value = str(value or "").strip()
    if not value:
        return "[]"
    parts = [p.strip() for p in value.replace("，", ",").split(",") if p.strip()]
    return json.dumps(parts, ensure_ascii=False)


def _normalize_analysis_methods(value):
    """Normalize a comma/、 separated analysis-methods payload into a JSON array."""

    return _normalize_text_list(value)


def schedule_question_fingerprint_retry(
    background_tasks: BackgroundTasks,
    *,
    question_id: int,
    operation: str,
) -> None:
    def retry_safely() -> None:
        from mathbank.database import SessionLocal

        retry_db = SessionLocal()
        try:
            question = retry_db.query(Question).filter(Question.id == question_id).first()
            if question is None:
                return
            fingerprint = fingerprint_for_question(
                question,
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
            )
            upsert_question_fingerprint(retry_db, question, fingerprint)
            retry_db.commit()
        except Exception as exc:
            retry_db.rollback()
            print(
                f"[Duplicate Index] Post-commit retry failed for {operation} "
                f"(question_id={question_id}, type={type(exc).__name__}); "
                "startup backfill will retry."
            )
        finally:
            retry_db.close()

    try:
        background_tasks.add_task(retry_safely)
    except Exception as exc:
        print(
            f"[Duplicate Index] Retry scheduling failed for {operation} "
            f"(question_id={question_id}, type={type(exc).__name__}); "
            "startup backfill will retry."
        )


@router.get("/api/questions")
def list_questions(
    q: str = None,
    search: str = None,
    compulsory: str = None,
    category_compulsory: str = None,
    chapter: str = None,
    category_chapter: str = None,
    knowledge: str = None,
    category_knowledge: str = None,
    qtype: str = None,
    question_type: str = None,
    difficulty: str = None,
    applicable_grade: str = None,
    topic: str = None,
    source: str = None,
    paper_category: str = None,
    exam_stage: str = None,
    extra_topics: str = None,
    topic_mode: str = "and",
    page: Optional[int] = None,
    page_size: int = 20,
    sort: str = "desc",
    mode: str = "full",
    tikz_pending: bool = False,
    exclude_quality: str = None,
    db: Session = Depends(get_db)
):
    search_q = q or search
    comp_val = compulsory or category_compulsory
    chap_val = chapter or category_chapter
    know_val = knowledge or category_knowledge
    type_val = qtype or question_type

    lean_mode = str(mode).lower() == "lean"
    if lean_mode and page is None:
        # lean 是为「海量列表」准备的序列化，绝不允许不带分页整库下发：客户端漏传
        # page 时补上默认页码，而不是退回下面那条全表 legacy 分支。
        page = 1

    query = db.query(Question)
    
    # Check if searching for a specific display sequence number
    target_id_by_seq = None
    if search_q:
        clean_q = search_q.strip()
        if clean_q.startswith("#"):
            clean_q = clean_q[1:]
        if clean_q.isdigit():
            seq_val = int(clean_q)
            if seq_val >= 1:
                row = (
                    db.query(Question.id)
                    .order_by(Question.id.asc())
                    .offset(seq_val - 1)
                    .limit(1)
                    .first()
                )
                if row:
                    target_id_by_seq = row[0]

    if search_q:
        if target_id_by_seq is not None:
            query = query.filter(
                (Question.id == target_id_by_seq) |
                (Question.content.like(f"%{search_q}%")) | 
                (Question.source.like(f"%{search_q}%")) |
                (Question.answer_markdown.like(f"%{search_q}%")) |
                (Question.review.like(f"%{search_q}%")) |
                (Question.tags.like(f"%{search_q}%"))
            )
        else:
            query = query.filter(
                (Question.content.like(f"%{search_q}%")) | 
                (Question.source.like(f"%{search_q}%")) |
                (Question.answer_markdown.like(f"%{search_q}%")) |
                (Question.review.like(f"%{search_q}%")) |
                (Question.tags.like(f"%{search_q}%"))
            )
    if comp_val:
        query = query.filter(Question.category_compulsory == comp_val)
    if chap_val:
        query = query.filter(Question.category_chapter == chap_val)
    if know_val:
        query = query.filter(Question.category_knowledge == know_val)
    if type_val:
        query = query.filter(Question.question_type == type_val)
    if difficulty:
        query = query.filter(Question.difficulty == difficulty)
    if paper_category:
        query = query.filter(Question.paper_category == paper_category)
    if exam_stage:
        query = query.filter(Question.exam_stage == exam_stage)
    if applicable_grade:
        if "," in applicable_grade:
            _grades = [g.strip() for g in applicable_grade.split(",") if g.strip()]
            if _grades:
                query = query.filter(Question.applicable_grade.in_(_grades))
        else:
            query = query.filter(Question.applicable_grade == applicable_grade)
    if topic:
        topic_ids = topic_ids_for_name(get_metadata_cache().get("curriculum", {}), topic)
        query = query.filter(Question.topic_id.in_(topic_ids))
    if extra_topics:
        topic_filters = parse_extra_topics(extra_topics)
        if topic_filters:
            # 局部变量必须换个名字：`mode` 现在是列表序列化形态参数（full/lean），
            # 在这里重新赋值会遮蔽形参，lean 判定虽然提前算完了，但后人读到就是坑。
            topic_join = "or" if str(topic_mode).lower() == "or" else "and"
            curriculum = get_metadata_cache().get("curriculum", {})
            if topic_join == "or":
                or_clauses = []
                for f in topic_filters:
                    slot_clauses = [
                        QuestionTopic.question_id == Question.id,
                        QuestionTopic.version_code == get_active_version_code(),
                        QuestionTopic.compulsory == f["compulsory"],
                        QuestionTopic.chapter == f["chapter"],
                        QuestionTopic.knowledge == f["knowledge"],
                    ]
                    f_tid = topic_id_for_slot(
                        curriculum, f["compulsory"], f["chapter"], f["knowledge"], f["topic"]
                    )
                    if f["topic"] and f_tid:
                        slot_clauses.append(QuestionTopic.topic_id == f_tid)
                    or_clauses.append(
                        db.query(QuestionTopic.question_id).filter(*slot_clauses).exists()
                    )
                query = query.filter(or_(*or_clauses))
            else:
                for f in topic_filters:
                    slot_clauses = [
                        QuestionTopic.question_id == Question.id,
                        QuestionTopic.version_code == get_active_version_code(),
                        QuestionTopic.compulsory == f["compulsory"],
                        QuestionTopic.chapter == f["chapter"],
                        QuestionTopic.knowledge == f["knowledge"],
                    ]
                    f_tid = topic_id_for_slot(
                        curriculum, f["compulsory"], f["chapter"], f["knowledge"], f["topic"]
                    )
                    if f["topic"] and f_tid:
                        slot_clauses.append(QuestionTopic.topic_id == f_tid)
                    query = query.filter(
                        db.query(QuestionTopic.question_id).filter(*slot_clauses).exists()
                    )
    if source:
        query = query.filter(Question.source.like(f"%{source}%"))

    if tikz_pending:
        query = query.filter(
            and_(
                Question._image_paths != "[]",
                Question._image_paths != "",
                Question._image_paths != None,
                (Question._content_tikz_assets == None)
                | (Question._content_tikz_assets == "[]")
                | (Question._content_tikz_assets == ""),
                (Question._answer_tikz_assets == None)
                | (Question._answer_tikz_assets == "[]")
                | (Question._answer_tikz_assets == ""),
                (Question.tikz_code == None) | (Question.tikz_code == ""),
            )
        )

    if exclude_quality == "bad":
        # 默认视图排除烂题：质量档位 -1（tags 含「烂题」）。tags 为 NULL 时也要保留。
        query = query.filter(or_(Question.tags.is_(None), ~Question.tags.like("%烂题%")))

    if str(sort).lower() == "paper_no":
        # 整卷浏览：按原题号自然序（提取前导数字），题号缺失的落后面。
        order_columns = (
            cast(func.substr(Question.paper_question_no, 1, 6), _SQLInteger),
            Question.paper_question_no,
            Question.id,
        )
    else:
        order_columns = (
            (Question.created_at.asc(), Question.id.asc())
            if str(sort).lower() == "asc"
            else (Question.created_at.desc(), Question.id.desc())
        )
    if page is not None:
        safe_page_size = max(1, min(int(page_size), 200))
        total = query.count()
        total_pages = max(1, (total + safe_page_size - 1) // safe_page_size)
        safe_page = max(1, min(int(page), total_pages))
        questions = (
            query.order_by(*order_columns)
            .offset((safe_page - 1) * safe_page_size)
            .limit(safe_page_size)
            .all()
        )
        seq_map = get_seq_mapping(db, [item.id for item in questions])
        if lean_mode:
            # lean：列表只回摘要与 140 字预览，完整题面/选项/答案一律按需另取。
            # 也不附带 topics 槽位数组——组卷流不读它，十万题时它是纯浪费的字节。
            items = [
                {
                    **_decorate_topic_field(item.to_lean_dict()),
                    "seq_num": seq_map.get(item.id),
                    "school_source_label": resolve_school_label(item.source_school_id) or None,
                    "quality": _quality_level_of(item),
                }
                for item in questions
            ]
        else:
            topics_map = load_questions_topics_map(
                db, [item.id for item in questions], get_active_version_code()
            )
            items = [
                {
                    **_decorate_topic_field(item.to_summary_dict()),
                    "seq_num": seq_map.get(item.id),
                    "topics": _decorate_topic_slots(topics_map.get(item.id, [])),
                    "school_source_label": resolve_school_label(item.source_school_id) or None,
                    "quality": _quality_level_of(item),
                }
                for item in questions
            ]
        return {
            "items": items,
            "total": total,
            "page": safe_page,
            "page_size": safe_page_size,
            "total_pages": total_pages,
        }

    questions = query.order_by(*order_columns).all()
    seq_map = get_seq_mapping(db, [item.id for item in questions])
    topics_map = load_questions_topics_map(
        db, [item.id for item in questions], get_active_version_code()
    )
    return [
        {
            **_decorate_topic_field(item.to_summary_dict()),
            "seq_num": seq_map.get(item.id),
            "topics": _decorate_topic_slots(topics_map.get(item.id, [])),
            "school_source_label": resolve_school_label(item.source_school_id) or None,
            "quality": _quality_level_of(item),
        }
        for item in questions
    ]


# ---------------------------------------------------------------------------
# 按卷聚合 + 质量标签（"查看整套试卷"功能后端）
# ---------------------------------------------------------------------------

QUALITY_TOKENS = {"好题": 1, "经典": 2, "烂题": -1}
QUALITY_BY_LEVEL = {0: "", 1: "好题", 2: "经典", -1: "烂题"}
LEVEL_BY_TOKEN = {"": 0, "好题": 1, "经典": 2, "烂题": -1}


def _quality_level_of(question) -> int:
    """从 tags 文本推导质量档位（-1/0/1/2）。"""

    for t in (question.tags or "").split(","):
        t = t.strip()
        if t in LEVEL_BY_TOKEN and LEVEL_BY_TOKEN[t] != 0:
            return LEVEL_BY_TOKEN[t]
    return 0


@router.get("/api/questions/papers")
def list_papers(
    year: str = "",
    exam_type: str = "",
    grade: str = "",
    region: str = "",
    db: Session = Depends(get_db),
):
    """按卷名（Question.source）聚合，返回每套卷的题数/年份/学校/考试类型。

    支持按年份 / 考试类型 / 年级 / 学校地区筛选（逗号分隔多选）。用于
    "查看整套试卷"的分组树。
    """

    from mathbank.database import QuestionSource

    base = (
        db.query(Question.source, func.count(Question.id).label("cnt"))
        .filter(Question.source.isnot(None), Question.source != "")
    )

    years = [f.strip() for f in (year or "").split(",") if f.strip()]
    etypes = [f.strip() for f in (exam_type or "").split(",") if f.strip()]
    grades = [f.strip() for f in (grade or "").split(",") if f.strip()]
    regions = [f.strip() for f in (region or "").split(",") if f.strip()]

    if years or etypes or grades or regions:
        sub = db.query(QuestionSource.question_id)
        if years:
            sub = sub.filter(QuestionSource.year.in_(years))
        if etypes:
            sub = sub.filter(QuestionSource.exam_type.in_(etypes))
        if grades:
            sub = sub.filter(QuestionSource.grade.in_(grades))
        if regions:
            sub = sub.filter(or_(*[QuestionSource.region.like(f"%{r}%") for r in regions]))
        matched = [r[0] for r in sub.distinct().all()]
        if matched:
            base = base.filter(Question.id.in_(matched))
        else:
            base = base.filter(Question.id == -1)

    groups = base.group_by(Question.source).all()

    result = []
    for source, cnt in groups:
        stats = (
            db.query(
                QuestionSource.year,
                QuestionSource.region,
                QuestionSource.exam_type,
                func.count(QuestionSource.id),
            )
            .join(Question, Question.id == QuestionSource.question_id)
            .filter(Question.source == source)
            .group_by(
                QuestionSource.year,
                QuestionSource.region,
                QuestionSource.exam_type,
            )
            .order_by(func.count(QuestionSource.id).desc())
            .first()
        )
        result.append(
            {
                "source": source,
                "count": cnt,
                "year": (stats[0] if stats else "") or "",
                "school": (stats[1] if stats else "") or "",
                "exam_type": (stats[2] if stats else "") or "",
            }
        )

    result.sort(
        key=lambda g: (
            -int(g["year"]) if str(g["year"]).isdigit() else 0,
            -g["count"],
        )
    )
    return {"groups": result}


@router.post("/api/questions/{question_id}/quality")
def set_question_quality(
    question_id: int, payload: dict, db: Session = Depends(get_db)
):
    """设置单题质量档位：level ∈ {normal, good, classic, bad} 或数值 -1/0/1/2。

    复用现有 tags 字段存质量 token（好题/经典/烂题），普通=无 token；
    其余自定义标签原样保留。单击循环由前端计算下一档。
    """

    q = db.query(Question).filter(Question.id == question_id).first()
    if q is None:
        return JSONResponse(
            content={"status": "error", "message": "题目不存在"}, status_code=404
        )

    raw = payload.get("level") if isinstance(payload, dict) else None
    level_map = {"normal": 0, "good": 1, "classic": 2, "bad": -1}
    if isinstance(raw, str) and raw in level_map:
        level = level_map[raw]
    else:
        try:
            level = int(raw)
        except (TypeError, ValueError):
            return JSONResponse(
                content={"status": "error", "message": "level 无效"}, status_code=400
            )
    if level not in QUALITY_BY_LEVEL:
        return JSONResponse(
            content={"status": "error", "message": "level 超出范围"}, status_code=400
        )

    tags = [t.strip() for t in (q.tags or "").split(",") if t.strip()]
    tags = [t for t in tags if t not in QUALITY_TOKENS]
    token = QUALITY_BY_LEVEL.get(level, "")
    if token:
        tags.append(token)
    sync_question_tags(db, q, ",".join(tags))
    db.commit()
    return {"status": "success", "question_id": question_id, "level": level}


def _duplicate_payload_list(value, *, field_name: str, max_items: int = 50):
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} 格式无效") from exc
    if not isinstance(value, list):
        raise ValueError(f"{field_name} 必须是数组")
    if len(value) > max_items:
        raise ValueError(f"{field_name} 数量超过 {max_items} 项")
    return value


def _duplicate_input_from_payload(item: dict) -> QuestionDuplicateInput:
    content = normalize_fillin_macro(str(item.get("content") or ""))
    if not content.strip():
        raise ValueError("题干内容不能为空")
    if len(content) > 200_000:
        raise ValueError("单题题干过长")
    answer_markdown = str(item.get("answer_markdown") or "")
    if len(answer_markdown) > 500_000:
        raise ValueError("单题解析过长")
    image_paths = [
        str(path or "").strip()
        for path in _duplicate_payload_list(
            item.get("image_paths"), field_name="image_paths"
        )
        if str(path or "").strip()
    ]
    content_assets = _duplicate_payload_list(
        item.get("content_tikz_assets"),
        field_name="content_tikz_assets",
    )
    answer_assets = _duplicate_payload_list(
        item.get("answer_tikz_assets"),
        field_name="answer_tikz_assets",
    )
    legacy_reference = str(item.get("tikz_reference_image_path") or "").strip()
    hidden_references = {legacy_reference}
    for asset in [*content_assets, *answer_assets]:
        if isinstance(asset, dict):
            hidden_references.add(str(asset.get("reference_image_path") or "").strip())
    hidden_references.discard("")
    evidence_image_paths = [
        path for path in image_paths if path not in hidden_references
    ]
    visible_paths = select_visible_question_images(
        content,
        answer_markdown,
        evidence_image_paths,
        content_assets,
    )
    return QuestionDuplicateInput(
        content=content,
        answer_markdown=answer_markdown,
        question_type=str(item.get("question_type") or ""),
        visible_image_signatures=build_visible_image_signatures(
            visible_paths,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
        ),
        tikz_signatures=build_tikz_signatures(
            content_assets,
            str(item.get("tikz_code") or ""),
        ),
        answer_asset_signatures=(
            build_visible_image_signatures(
                select_answer_images(
                    answer_markdown,
                    evidence_image_paths,
                    answer_assets,
                ),
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
            )
            + build_tikz_signatures(answer_assets)
        ),
    )


def _prompt_visible_image_paths(question: Question) -> list[str]:
    return select_visible_question_images(
        question.content or "",
        question.answer_markdown or "",
        question.display_image_paths,
        question.content_tikz_assets,
    )


@router.post("/api/questions/check-duplicates")
def check_question_duplicates(payload: dict, db: Session = Depends(get_db)):
    """Check only when a teacher initiates a save/import; never during parsing."""

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="查重请求格式无效")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="items 必须是非空题目数组")
    if len(items) > 500:
        raise HTTPException(status_code=400, detail="单次最多查重 500 道题")
    max_candidates = max(1, min(int(payload.get("max_candidates") or 5), 10))

    try:
        prepared = []
        total_content_size = 0
        for index, raw_item in enumerate(items):
            if not isinstance(raw_item, dict):
                raise ValueError(f"第 {index + 1} 道题格式无效")
            duplicate_input = _duplicate_input_from_payload(raw_item)
            total_content_size += len(duplicate_input.content) + len(
                duplicate_input.answer_markdown
            )
            if total_content_size > 5_000_000:
                raise ValueError("本次查重内容总量过大")
            exclude_id = raw_item.get("exclude_id")
            if exclude_id not in (None, ""):
                exclude_id = int(exclude_id)
                if exclude_id <= 0:
                    raise ValueError("exclude_id 必须是正整数")
            else:
                exclude_id = None
            client_key = str(raw_item.get("client_key") or index)
            if len(client_key) > 128:
                raise ValueError("client_key 过长")
            prepared.append(
                {
                    "client_key": client_key,
                    "exclude_id": exclude_id,
                    "input": duplicate_input,
                    "fingerprint": build_question_fingerprint(duplicate_input),
                }
            )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        fingerprints = [item["fingerprint"] for item in prepared]
        batch_matches, batch_diagnostics = batch_local_matches(fingerprints)
        candidate_cache = {}
        raw_results = []
        all_candidate_ids = set()
        truncated_recall_band_count = 0
        for index, item in enumerate(prepared):
            recall_diagnostics = {}
            candidates = find_indexed_candidates(
                db,
                item["fingerprint"],
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
                exclude_id=item["exclude_id"],
                limit=max_candidates,
                fingerprint_cache=candidate_cache,
                diagnostics=recall_diagnostics,
            )
            truncated_recall_band_count += int(
                recall_diagnostics.get("truncated_band_count") or 0
            )
            all_candidate_ids.update(candidate.question.id for candidate in candidates)
            raw_results.append((index, item, candidates))
        seq_map = get_seq_mapping(db, all_candidate_ids)
        rank = {"exact": 3, "probable": 2, "possible_variant": 1, "none": 0}
        response_items = []
        for index, item, candidates in raw_results:
            candidate_payloads = []
            levels = []
            needs_visual_review = False
            for candidate in candidates:
                comparison = candidate.comparison.to_dict()
                levels.append(comparison["level"])
                needs_visual_review = (
                    needs_visual_review or comparison["needs_visual_review"]
                )
                question = candidate.question
                content_preview = str(question.content or "")[:500]
                candidate_images = _prompt_visible_image_paths(question)
                candidate_payloads.append(
                    {
                        "id": question.id,
                        "seq_num": seq_map.get(question.id),
                        "content": content_preview,
                        "content_truncated": len(str(question.content or "")) > 500,
                        "source": question.source or "",
                        "question_type": question.question_type or "",
                        "has_answer": bool((question.answer_markdown or "").strip()),
                        "image_paths": candidate_images[:4],
                        "image_count": len(candidate_images),
                        "snapshot_hash": candidate.fingerprint.content_revision_hash,
                        **comparison,
                    }
                )
            local_payloads = []
            for local_match in batch_matches.get(index, []):
                other_index = int(local_match["other_index"])
                local_payload = {
                    **local_match,
                    "other_client_key": prepared[other_index]["client_key"],
                }
                local_payloads.append(local_payload)
                levels.append(str(local_match["level"]))
                needs_visual_review = (
                    needs_visual_review
                    or bool(local_match.get("needs_visual_review"))
                )
            level = max(levels, key=lambda value: rank.get(value, 0)) if levels else "none"
            response_items.append(
                {
                    "client_key": item["client_key"],
                    "snapshot_hash": item["fingerprint"].content_revision_hash,
                    "level": level,
                    "candidates": candidate_payloads,
                    "batch_matches": local_payloads,
                    "needs_visual_review": needs_visual_review,
                }
            )
        status = duplicate_index_status(db)
        if truncated_recall_band_count:
            status = {
                **status,
                "ready": False,
                "warning": (
                    "近似候选分桶过宽，已为保持速度停止扩展；"
                    "本次结果可能不完整。"
                ),
            }
        if not batch_diagnostics.get("index_complete", True):
            status = {
                **status,
                "ready": False,
                "warning": "批内近似候选过多，结果可能不完整",
            }
        return {
            "status": "success",
            "index": status,
            "batch_diagnostics": batch_diagnostics,
            "items": response_items,
        }
    except Exception as exc:
        db.rollback()
        print(
            "[Duplicate Check] Failed "
            f"(type={type(exc).__name__}); saving remains available."
        )
        raise HTTPException(status_code=503, detail="查重暂不可用，可选择继续保存") from exc


@router.get("/api/questions/{question_id}")

def get_question(question_id: int, db: Session = Depends(get_db)):
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
    seq_map = get_seq_mapping(db, [q.id])
    q_dict = _decorate_topic_field(q.to_dict())
    q_dict["seq_num"] = seq_map.get(q.id)
    q_dict["topics"] = _decorate_topic_slots(load_question_topics(db, q.id, get_active_version_code()))
    q_dict["sources"] = _load_sources_sorted(db, q.id)
    # 主来源学校：按 source_school_id 现查当前简称（词库删校/未建档回落 None，前端用 source 文本）
    q_dict["school_source_label"] = resolve_school_label(q.source_school_id) or None
    q_dict["quality"] = _quality_level_of(q)
    return q_dict


@router.post("/api/questions/scan-duplicates")
def scan_question_duplicates(payload: dict, db: Session = Depends(get_db)):
    """Scan the whole bank and list questions that have duplicate / near-duplicate
    candidates already indexed. Read-only scan; never deletes or merges."""
    if not isinstance(payload, dict):
        payload = {}
    min_level = str(payload.get("min_level") or "probable")
    if min_level not in {"exact", "probable", "possible_variant"}:
        raise HTTPException(
            status_code=400, detail="min_level 必须是 exact / probable / possible_variant"
        )
    try:
        max_per_question = max(1, min(int(payload.get("max_per_question") or 5), 10))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="max_per_question 必须是正整数")
    level_rank = {"exact": 3, "probable": 2, "possible_variant": 1}
    min_rank = level_rank[min_level]

    questions = db.query(Question).order_by(Question.id).all()
    all_ids = [q.id for q in questions]
    seq_map = get_seq_mapping(db, all_ids) if all_ids else {}
    items = []
    scanned = 0
    fingerprint_missing = 0
    candidate_cache = {}
    for q in questions:
        scanned += 1
        try:
            fp = fingerprint_for_question(
                q, uploads_dir=UPLOAD_DIR, url_prefix=UPLOAD_DIR_REL
            )
        except Exception:
            fp = None
        if fp is None:
            fingerprint_missing += 1
            continue
        candidates = find_indexed_candidates(
            db,
            fp,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
            exclude_id=q.id,
            limit=max_per_question,
            fingerprint_cache=candidate_cache,
        )
        matched = []
        for cand in candidates:
            comparison = cand.comparison
            if level_rank.get(comparison.level, 0) < min_rank:
                continue
            qq = cand.question
            matched.append({
                "id": qq.id,
                "seq_num": seq_map.get(qq.id),
                "level": comparison.level,
                "score": round(comparison.score, 4),
                "needs_visual_review": comparison.needs_visual_review,
                "reasons": list(comparison.reasons)[:3],
                "content": str(qq.content or "")[:300],
                "content_truncated": len(str(qq.content or "")) > 300,
                "source": qq.source or "",
                "question_type": qq.question_type or "",
                "applicable_grade": qq.applicable_grade or "",
                "topic": _topic_display(qq.topic_id or ""),
                "tags": qq.tags or "",
            })
        if matched:
            best = max(matched, key=lambda m: level_rank[m["level"]])
            items.append({
                "question_id": q.id,
                "seq_num": seq_map.get(q.id),
                "level": best["level"],
                "content": str(q.content or "")[:300],
                "content_truncated": len(str(q.content or "")) > 300,
                "source": q.source or "",
                "question_type": q.question_type or "",
                "applicable_grade": q.applicable_grade or "",
                "topic": _topic_display(q.topic_id or ""),
                "tags": q.tags or "",
                "candidates": matched,
            })
    return {
        "status": "success",
        "scanned": scanned,
        "fingerprint_missing": fingerprint_missing,
        "matched_groups": len(items),
        "items": items,
        "index_status": duplicate_index_status(db),
    }


@router.post("/api/questions/{question_id}/add-tag")
def add_question_tag(question_id: int, payload: dict, db: Session = Depends(get_db)):
    """Append a single tag to a question (idempotent, safe to call repeatedly)."""
    tag = str((payload or {}).get("tag") or "").strip()
    if not tag:
        raise HTTPException(status_code=400, detail="tag 不能为空")
    if len(tag) > 64:
        raise HTTPException(status_code=400, detail="tag 过长（最多 64 字符）")
    if any(ch in tag for ch in ",，"):
        raise HTTPException(status_code=400, detail="tag 不能包含逗号")
    db_question = db.query(Question).filter(Question.id == question_id).first()
    if not db_question:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
    existing = [t.strip() for t in str(db_question.tags or "").split(",") if t.strip()]
    if tag not in existing:
        sync_question_tags(db, db_question, ",".join(existing + [tag]))
        db.commit()
    return {"status": "success", "tags": db_question.tags}


def committed_question_response(
    db: Session,
    db_question: Question,
    question_id: int,
    *,
    operation: str,
) -> dict:
    """Serialize a committed write without ever misreporting it as failed."""

    try:
        db.refresh(db_question)
        seq_map = get_seq_mapping(db, [question_id])
        question = _decorate_topic_field(db_question.to_dict())
        question["seq_num"] = seq_map.get(question_id)
        try:
            question["topics"] = _decorate_topic_slots(load_question_topics(
                db, question_id, get_active_version_code()
            ))
        except Exception as topics_exc:
            print(
                "[Question Topics] Post-commit response topics degraded "
                f"(type={type(topics_exc).__name__})."
            )
            question["topics"] = []
        return {"status": "success", "question": question}
    except Exception as exc:
        # The durable transaction is already complete.  End any failed read
        # transaction and return enough identity for the client to continue;
        # a later list/detail refresh can obtain the full representation.
        try:
            db.rollback()
        except Exception:
            pass
        print(
            f"[Question Write] Post-commit {operation} response degraded "
            f"(type={type(exc).__name__})."
        )
        return {"status": "success", "question": {"id": question_id}}


def _tikz_asset_field(raw: Optional[str]) -> list:
    """把 tikz assets JSON 字段安全摊平成对象数组（坏数据按空处理，不在此处报错）。"""

    if not raw or not str(raw).strip():
        return []
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _referenced_image_basenames(*texts: str) -> set:
    """正文里被引用到的图片文件名集合。

    三种形态都要认：Markdown `![x](/static/uploads/a.png)`、LaTeX
    `\\includegraphics[...]{a.png}`、以及 TikZ 工作台插入的裸 URL。
    扩展名一律取 asset_security 的同一份白名单，禁止另抄一份。
    """

    pattern = r"[A-Za-z0-9][A-Za-z0-9_.\-]*(?:" + "|".join(
        re.escape(ext) for ext in sorted(SAFE_IMAGE_EXTENSIONS)
    ) + r")"
    found: set = set()
    for text in texts:
        value = str(text or "")
        for match in re.finditer(r"!\[[^\]]*\]\(([^)\s]+)\)", value):
            found.add(match.group(1).rsplit("/", 1)[-1])
        for match in re.finditer(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", value):
            found.add(match.group(1).rsplit("/", 1)[-1])
        for match in re.finditer(r"/static/(?:uploads|test_uploads)/(" + pattern + r")", value):
            found.add(match.group(1))
    return {name for name in found if name}


def reconcile_image_paths_with_text(
    parsed_img_paths: list,
    content: str,
    answer_markdown: str,
    *,
    content_tikz_assets: Optional[str],
    answer_tikz_assets: str,
    tikz_reference_image_path: str,
    tikz_code: str = "",
) -> list:
    """剔除磁盘上**已不存在**的 image_path 条目，再送去 require_file 校验。

    「转 TikZ」每次重试都生成新 uuid 文件名并**追加**登记；旧名字对应的文件会在下次
    启动孤儿清理时被删除，而它仍留在卡片的 image_paths 里——于是保存报「插图文件不存在
    或不是普通文件」，用户看着配图一切正常却存不下去。判据只能是**文件是否还在**：
    拆卷卡片的题干截图按设计就只登记在 image_paths、由配图条统一展示，正文里根本不
    引用它（`import.js` 的 `appendParsedCardImageStrip` / `assignParsedUnassignedImages`，
    以及 `parsing_pipeline` 的 `q["image_paths"] = owned_crops`），后端
    `select_visible_question_images` 与 `_referenced_question_assets` 也都认这种形态。
    若改成「无引用方即剔除」，会把这类合法配图静默丢掉、并把刚晋升的文件回滚成孤儿。

    引用方信息（正文 + 两组 tikz assets 渲染图/参考图 + 旧版单幅兼容字段）**只用于
    日志理由**，绝不参与去留判定；宁留不错删（误删会让图片生命周期失控）。
    """

    referenced = _referenced_image_basenames(content, answer_markdown)
    # 绘图资产自己认领的物理文件（渲染图 + 原参考图）：它们按设计不出现在正文里
    # （正文仍指向原图，渲染图只在展示/导出时替换），所以客户端很容易漏登记。
    owned: list = []
    for raw in (content_tikz_assets, answer_tikz_assets):
        for asset in _tikz_asset_field(raw):
            for key in ("image_path", "reference_image_path"):
                value = str(asset.get(key) or "").strip()
                if not value:
                    continue
                referenced.add(value.rsplit("/", 1)[-1])
                owned.append(value)
    legacy_reference = str(tikz_reference_image_path or "").strip()
    if legacy_reference:
        referenced.add(legacy_reference.rsplit("/", 1)[-1])
        owned.append(legacy_reference)

    kept: list = []
    for reference in parsed_img_paths:
        basename = str(reference or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
        if not basename:
            continue
        if Path(UPLOAD_DIR).joinpath(basename).is_file():
            kept.append(reference)
            continue
        reason = "正文/绘图槽有引用" if basename in referenced else "无任何引用方"
        print(
            f"[asset-reconcile] drop stale image_path: {basename}"
            f"（文件已不在 uploads 根目录；{reason}）"
        )

    # 补登记：绘图资产认领、且文件确实还在的图片，即使客户端漏了也必须进 image_paths。
    # asset_security 的不变式是「渲染图/参考图必须被题目登记」，用来挡住孤儿回收；
    # 漏登记时直接 400 会让用户「图看着好好的却存不下去」（2026-10-19 事故）。文件不在
    # 就不补——留给 normalize_upload_asset_reference 报「插图文件不存在」这种可行动的错。
    kept_basenames = {
        str(path or "").strip().replace("\\", "/").rsplit("/", 1)[-1] for path in kept
    }
    for value in owned:
        basename = value.replace("\\", "/").rsplit("/", 1)[-1]
        if not basename or basename in kept_basenames:
            continue
        if Path(UPLOAD_DIR).joinpath(basename).is_file():
            kept.append(value)
            kept_basenames.add(basename)
            print(f"[asset-reconcile] register tikz-owned asset: {basename}")
    return kept


def prepare_question_assets(
    content: str,
    answer_markdown: str,
    image_paths: str,
    content_tikz_assets: Optional[str],
    tikz_reference_image_path: str,
    answer_tikz_assets: str,
    tikz_code: str = "",
    *,
    promotion_log: list[tuple[Path, Path]],
) -> tuple[
    str,
    str,
    list[str],
    str,
    str,
    list[dict[str, str]],
    list[dict[str, str]],
]:
    """Promote and validate all visible and AI-only assets in one policy path."""

    parsed_img_paths = json.loads(image_paths) if image_paths else []
    content, answer_markdown, parsed_img_paths = promote_question_temp_assets(
        content,
        answer_markdown,
        parsed_img_paths,
        promotion_log=promotion_log,
    )
    # 对账必须放在 require_file 校验之前、晋升之后：晋升会把 tmp 引用换成正式名，
    # 换完才是「正文里最终会出现的文件名」。
    parsed_img_paths = reconcile_image_paths_with_text(
        parsed_img_paths,
        content,
        answer_markdown,
        content_tikz_assets=content_tikz_assets,
        answer_tikz_assets=answer_tikz_assets,
        tikz_reference_image_path=tikz_reference_image_path,
        tikz_code=tikz_code,
    )
    parsed_img_paths = normalize_upload_asset_references(
        parsed_img_paths,
        uploads_dir=UPLOAD_DIR,
        url_prefix=UPLOAD_DIR_REL,
    )
    parsed_answer_tikz_assets = normalize_answer_tikz_assets(
        answer_tikz_assets,
        allowed_image_paths=parsed_img_paths,
        uploads_dir=UPLOAD_DIR,
        url_prefix=UPLOAD_DIR_REL,
    )
    if content_tikz_assets is None:
        # Compatibility for clients and existing drafts created before v5.
        parsed_content_tikz_assets: list[dict[str, str]] = []
        parsed_tikz_code = str(tikz_code or "").strip()
        parsed_tikz_reference_image_path = normalize_optional_upload_asset_reference(
            tikz_reference_image_path,
            allowed_image_paths=parsed_img_paths,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
        )
    else:
        parsed_content_tikz_assets = normalize_content_tikz_assets(
            content_tikz_assets,
            allowed_image_paths=parsed_img_paths,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
        )
        first_content_asset = (
            parsed_content_tikz_assets[0] if parsed_content_tikz_assets else {}
        )
        parsed_tikz_code = str(first_content_asset.get("tikz_code") or "")
        parsed_tikz_reference_image_path = str(
            first_content_asset.get("reference_image_path") or ""
        )
    return (
        content,
        answer_markdown,
        parsed_img_paths,
        parsed_tikz_code,
        parsed_tikz_reference_image_path,
        parsed_content_tikz_assets,
        parsed_answer_tikz_assets,
    )


def build_prepared_question_fingerprint(
    *,
    content: str,
    answer_markdown: str,
    question_type: str,
    image_paths: list[str],
    content_tikz_assets: list[dict[str, str]],
    answer_tikz_assets: list[dict[str, str]],
    tikz_code: str,
    tikz_reference_image_path: str,
):
    # 指纹必须基于「入库后的归一化形态」计算，否则查重（_check，用原文）与保存
    # （create/update，用归一化后 content）算出的 content_revision_hash 不一致，会误报 409。
    # 指纹归一化已下沉到 build_question_fingerprint 最低层（check-duplicates 与
    # create/update 自动同源，不会误报 409）。此处图片可见性判定依赖 answer_markdown
    # 原文里的完整路径做「答案图 vs 题干图」区分，故用原文；哈希由下层归一化。
    hidden_references = {str(tikz_reference_image_path or "").strip()}
    for asset in [*content_tikz_assets, *answer_tikz_assets]:
        if isinstance(asset, dict):
            hidden_references.add(str(asset.get("reference_image_path") or "").strip())
    hidden_references.discard("")
    registered_visible_paths = [
        path for path in image_paths if path not in hidden_references
    ]
    visible_paths = select_visible_question_images(
        content,
        answer_markdown,
        registered_visible_paths,
        content_tikz_assets,
    )
    return build_question_fingerprint(
        QuestionDuplicateInput(
            content=content,
            answer_markdown=answer_markdown,
            question_type=question_type,
            visible_image_signatures=build_visible_image_signatures(
                visible_paths,
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
            ),
            tikz_signatures=build_tikz_signatures(
                content_tikz_assets,
                tikz_code,
            ),
            answer_asset_signatures=(
                build_visible_image_signatures(
                    select_answer_images(
                        answer_markdown,
                        image_paths,
                        answer_tikz_assets,
                    ),
                    uploads_dir=UPLOAD_DIR,
                    url_prefix=UPLOAD_DIR_REL,
                )
                + build_tikz_signatures(answer_tikz_assets)
            ),
        )
    )


def duplicate_review_required_response(
    db: Session,
    *,
    fingerprint,
    question_ids: list[int],
    message: str,
    code: str = "duplicate_review_required",
):
    questions = db.query(Question).filter(Question.id.in_(question_ids)).all()
    seq_map = get_seq_mapping(db, question_ids)
    return JSONResponse(
        status_code=409,
        content={
            "status": "error",
            "code": code,
            "message": message,
            "snapshot_hash": fingerprint.content_revision_hash,
            "candidates": [
                {
                    "id": question.id,
                    "seq_num": seq_map.get(question.id),
                    "content": str(question.content or "")[:500],
                    "content_truncated": len(str(question.content or "")) > 500,
                    "source": question.source or "",
                    "question_type": question.question_type or "",
                    "image_paths": _prompt_visible_image_paths(question)[:4],
                }
                for question in questions
            ],
        },
    )


@router.post("/api/questions")
def create_question(
    background_tasks: BackgroundTasks,
    content: str = Form(...),
    question_type: str = Form(...),
    category_compulsory: str = Form(""),
    category_chapter: str = Form(""),
    category_knowledge: str = Form(""),
    applicable_grade: str = Form(""),
    topic: str = Form(""),
    difficulty: str = Form(...),
    source: str = Form(""),
    sources: str = Form("[]"),
    paper_category: str = Form(""),
    paper_question_no: str = Form(""),
    answer_markdown: str = Form(""),
    review: str = Form(""),
    tikz_code: str = Form(""),
    content_tikz_assets: Optional[str] = Form(None),
    tikz_reference_image_path: str = Form(""),
    answer_tikz_assets: str = Form("[]"),
    figure_align: str = Form("right"),
    tags: str = Form(""),
    related_question_id: str = Form(""),
    image_paths: str = Form("[]"),  # JSON array string
    image_layouts: str = Form(""),  # 逐图排版 JSON（归属修复播种，可缺省）
    duplicate_snapshot_hash: str = Form(""),
    duplicate_override: str = Form(""),
    extra_topics: str = Form("[]"),
    knowledge_points: str = Form(""),
    exam_year: str = Form(""),
    exam_region: str = Form(""),
    exam_stage: str = Form(""),
    video_url: str = Form(""),
    option_columns: str = Form(""),
    analysis_methods: str = Form(""),
    difficulty_index: str = Form(""),
    cognitive_level: str = Form(""),
    core_competency: str = Form(""),
    error_prone_points: str = Form(""),
    import_batch: str = Form(""),
    import_batch_name: str = Form(""),
    db: Session = Depends(get_db)
):
    asset_promotions: list[tuple[Path, Path]] = []
    duplicate_warning = ""
    try:
        duplicate_snapshot_hash = str(duplicate_snapshot_hash or "").strip()
        duplicate_override = str(duplicate_override or "").strip()
        if duplicate_override not in {"", "independent"}:
            raise ValueError("无效的查重处理方式")
        if duplicate_override and not duplicate_snapshot_hash:
            raise ValueError("独立保存确认缺少题目快照")
        if duplicate_snapshot_hash and not re.fullmatch(
            r"[0-9a-f]{64}", duplicate_snapshot_hash
        ):
            raise ValueError("查重题目快照格式无效")
        # 规范化填空题下划线为 \fillin 宏；并把 AI 挤在同一 \item 的多选项拆开
        content = normalize_fillin_macro(content)
        content = _split_merged_choice_items(content)

        (
            content,
            answer_markdown,
            parsed_img_paths,
            parsed_tikz_code,
            parsed_tikz_reference_image_path,
            parsed_content_tikz_assets,
            parsed_answer_tikz_assets,
        ) = prepare_question_assets(
            content,
            answer_markdown,
            image_paths,
            content_tikz_assets,
            tikz_reference_image_path,
            answer_tikz_assets,
            tikz_code,
            promotion_log=asset_promotions,
        )

        # 结构归一化：三条录入路径（编辑器手敲/单题 OCR、试卷拆解卡片、二次编辑保存）
        # 在写库这一步统一走 normalize_for_storage —— 它同时负责结构层（图片 \\includegraphics、
        # 表格 tabular、选项 choices、加粗 textbf、填空 \\fillin）与字符层（补 $ 定界符、
        # * -> \\cdot、$$ 降级、多余空行压缩），且幂等，所以卡片里手改过的文字、草稿箱再保存
        # 都不会让入库源码走形。解析卡在 post_process 里已经跑过同一条链，这里再过一遍是恒等。
        # 先用原文抽选项（options 字段认得「A．」行，不认 \\begin{choices}）。
        # content_for_options / answer_markdown_raw 是归一化前的原文，供查重指纹使用
        # （build_question_fingerprint 内部会再归一化做哈希；图片可见性判定也依赖原文）。
        content_for_options = content
        answer_markdown_raw = answer_markdown
        content, answer_markdown, question_type, paper_question_no = _normalize_pair_for_db(
            content, answer_markdown, question_type, paper_question_no
        )
        # 选择题末尾的作答空括号统一成 \paren。**必须放在 _normalize_pair_for_db
        # 之后**：只有它内部的 normalize_for_storage 跑完，纯文本选项行才被包装成
        # \begin{choices}，此时补 \paren 才会落在题干末尾而不是选项行之后。
        # 该顺序与 parsing_pipeline.apply_storage_normalization 完全一致，是
        # 「三条录入路径入库源码逐字节相同」的必要条件（test_entry_path_latex_parity）。
        content = normalize_choice_paren_macro(content, question_type)

        # 1. Fallback if third level is empty, default to chapter
        if not category_knowledge and category_chapter:
            category_knowledge = category_chapter
            
        db_question = Question(
            content=content,
            question_type=question_type,
            category_compulsory=category_compulsory,
            category_chapter=category_chapter,
            category_knowledge=category_knowledge,
            applicable_grade=applicable_grade,
            topic_id=_resolve_topic_id(category_compulsory, category_chapter, category_knowledge, topic),
            difficulty=difficulty,
            source=source,
            paper_category=paper_category,
            paper_question_no=paper_question_no,
            answer_markdown=answer_markdown,
            review=review,
            tikz_code=parsed_tikz_code,
            tikz_reference_image_path=parsed_tikz_reference_image_path,
            figure_align=figure_align if figure_align in ["right", "center", "bottom_right"] else "right",
            tags=tags,
            knowledge_points="",
            exam_year=_parse_int_or_none(exam_year),
            exam_region=exam_region or "",
            exam_stage=exam_stage or "",
            video_url=video_url or "",
            option_columns=_parse_int_or_default(option_columns, 0),
            analysis_methods=_normalize_analysis_methods(analysis_methods),
            difficulty_index=_parse_float_or_none(difficulty_index),
            cognitive_level=cognitive_level or "",
            core_competency=core_competency or "",
            error_prone_points=_normalize_text_list(error_prone_points),
            options=json.dumps(
                extract_structured_options(content_for_options, question_type), ensure_ascii=False
            ),
        )
        db_question.image_paths = parsed_img_paths
        # 逐图排版：卡片播种的 image_layouts 原样透传；属性 setter 会按 content
        # 校验枚举并过滤野键，坏值只丢弃、绝不阻塞导入。
        if image_layouts and image_layouts.strip() not in ("", "{}"):
            try:
                db_question.image_layouts = image_layouts
            except ValueError:
                pass
        db_question.content_tikz_assets = parsed_content_tikz_assets
        db_question.answer_tikz_assets = parsed_answer_tikz_assets
        
        # Handle related question association (transitive relation)
        related_id_int = int(related_question_id) if related_question_id and related_question_id.strip() else None
        if related_id_int:
            q_related = db.query(Question).filter(Question.id == related_id_int).first()
            if q_related:
                g2 = q_related.association_group_id
                if not g2:
                    new_grp = str(uuid.uuid4())
                    q_related.association_group_id = new_grp
                    db_question.association_group_id = new_grp
                else:
                    db_question.association_group_id = g2
        
        db.add(db_question)
        db.flush()
        from mathbank.sources_helper import sync_question_sources as _sync_question_sources
        _sync_question_sources(db, db_question.id, sources)
        sync_question_tags(db, db_question, tags)
        sync_question_knowledge(db, db_question, knowledge_points)
        record_import_batch_question(
            db, import_batch, db_question.id, name=import_batch_name
        )
        record_tikz_versions(
            db,
            db_question.id,
            {"content": parsed_content_tikz_assets, "answer": parsed_answer_tikz_assets},
        )

        try:
            question_fingerprint = build_prepared_question_fingerprint(
                content=content_for_options,
                answer_markdown=answer_markdown_raw,
                question_type=question_type,
                image_paths=parsed_img_paths,
                content_tikz_assets=parsed_content_tikz_assets,
                answer_tikz_assets=parsed_answer_tikz_assets,
                tikz_code=parsed_tikz_code,
                tikz_reference_image_path=parsed_tikz_reference_image_path,
            )
        except Exception as fingerprint_exc:
            question_fingerprint = None
            duplicate_warning = "题目已保存，但本次查重指纹未生成；后台将尝试补建，失败时下次启动继续。"
            print(
                "[Duplicate Index] Create fingerprint failed open "
                f"(type={type(fingerprint_exc).__name__}); background backfill will retry."
            )
        if duplicate_snapshot_hash and question_fingerprint is not None:
            if duplicate_snapshot_hash != question_fingerprint.content_revision_hash:
                response = duplicate_review_required_response(
                    db,
                    fingerprint=question_fingerprint,
                    question_ids=[],
                    message="题目在查重后已发生变化，请重新查重后保存。",
                    code="duplicate_snapshot_stale",
                )
                db.rollback()
                rollback_question_asset_promotions(asset_promotions)
                return response
            exact_ids = exact_duplicate_ids(
                db,
                question_fingerprint,
                exclude_id=db_question.id,
            )
            if exact_ids and duplicate_override != "independent":
                response = duplicate_review_required_response(
                    db,
                    fingerprint=question_fingerprint,
                    question_ids=exact_ids,
                    message="入库前发现新的疑似已收录题，请核对后再决定。",
                )
                db.rollback()
                rollback_question_asset_promotions(asset_promotions)
                return response
        if question_fingerprint is not None:
            upsert_question_fingerprint(db, db_question, question_fingerprint)

        # Save the question and its active curriculum mirror atomically.
        active_version = get_active_version_code()
        curriculum_map = QuestionCurriculum(
            question_id=db_question.id,
            version_code=active_version,
            compulsory=category_compulsory,
            chapter=category_chapter,
            knowledge=category_knowledge
        )
        db.add(curriculum_map)
        topic_slots = sync_question_topics(
            db,
            db_question.id,
            active_version,
            primary={
                "compulsory": category_compulsory,
                "chapter": category_chapter,
                "knowledge": category_knowledge,
                "topic": topic,
                "topic_id": _resolve_topic_id(category_compulsory, category_chapter, category_knowledge, topic),
            },
            extra=_resolve_extra_topic_ids(extra_topics),
        )
        committed_question_id = db_question.id
        db.commit()
    except Exception as e:
        db.rollback()
        rollback_question_asset_promotions(asset_promotions)
        raise HTTPException(status_code=400, detail=f"保存题目失败: {str(e)}")

    # Everything below is compensating or response work after the durable
    # success boundary; none of it may turn the write into a misleading 400.
    _flush_auto_topics()
    schedule_database_export(background_tasks, operation="create_question")
    if duplicate_warning:
        schedule_question_fingerprint_retry(
            background_tasks,
            question_id=committed_question_id,
            operation="create_question",
        )
    response = committed_question_response(
        db,
        db_question,
        committed_question_id,
        operation="create_question",
    )
    if duplicate_warning:
        response["warning"] = duplicate_warning
    return response


@router.put("/api/questions/{question_id}")
def update_question(
    question_id: int,
    background_tasks: BackgroundTasks,
    content: str = Form(...),
    question_type: str = Form(...),
    category_compulsory: str = Form(""),
    category_chapter: str = Form(""),
    category_knowledge: str = Form(""),
    applicable_grade: str = Form(""),
    topic: str = Form(""),
    difficulty: str = Form(...),
    source: str = Form(""),
    sources: str = Form("[]"),
    paper_category: str = Form(""),
    paper_question_no: str = Form(""),
    answer_markdown: str = Form(""),
    review: str = Form(""),
    tikz_code: str = Form(""),
    content_tikz_assets: Optional[str] = Form(None),
    tikz_reference_image_path: str = Form(""),
    answer_tikz_assets: str = Form("[]"),
    figure_align: str = Form("right"),
    tags: str = Form(""),
    related_question_id: str = Form(""),
    image_paths: str = Form("[]"),
    duplicate_snapshot_hash: str = Form(""),
    duplicate_override: str = Form(""),
    extra_topics: str = Form("[]"),
    knowledge_points: str = Form(""),
    exam_year: str = Form(""),
    exam_region: str = Form(""),
    exam_stage: str = Form(""),
    video_url: str = Form(""),
    option_columns: str = Form(""),
    analysis_methods: str = Form(""),
    difficulty_index: str = Form(""),
    cognitive_level: str = Form(""),
    core_competency: str = Form(""),
    error_prone_points: str = Form(""),
    import_batch: str = Form(""),
    import_batch_name: str = Form(""),
    db: Session = Depends(get_db)
):
    db_question = db.query(Question).filter(Question.id == question_id).first()
    if not db_question:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
        
    asset_promotions: list[tuple[Path, Path]] = []
    duplicate_warning = ""
    old_images = list(db_question.image_paths)
    try:
        duplicate_snapshot_hash = str(duplicate_snapshot_hash or "").strip()
        duplicate_override = str(duplicate_override or "").strip()
        if duplicate_override not in {"", "independent"}:
            raise ValueError("无效的查重处理方式")
        if duplicate_override and not duplicate_snapshot_hash:
            raise ValueError("独立保存确认缺少题目快照")
        if duplicate_snapshot_hash and not re.fullmatch(
            r"[0-9a-f]{64}", duplicate_snapshot_hash
        ):
            raise ValueError("查重题目快照格式无效")
        # 规范化填空题下划线为 \fillin 宏；并把 AI 挤在同一 \item 的多选项拆开
        content = normalize_fillin_macro(content)
        content = _split_merged_choice_items(content)

        (
            content,
            answer_markdown,
            parsed_img_paths,
            parsed_tikz_code,
            parsed_tikz_reference_image_path,
            parsed_content_tikz_assets,
            parsed_answer_tikz_assets,
        ) = prepare_question_assets(
            content,
            answer_markdown,
            image_paths,
            content_tikz_assets,
            tikz_reference_image_path,
            answer_tikz_assets,
            tikz_code,
            promotion_log=asset_promotions,
        )

        # 结构归一化：三条录入路径（编辑器手敲/单题 OCR、试卷拆解卡片、二次编辑保存）
        # 在写库这一步统一走 normalize_for_storage —— 它同时负责结构层（图片 \\includegraphics、
        # 表格 tabular、选项 choices、加粗 textbf、填空 \\fillin）与字符层（补 $ 定界符、
        # * -> \\cdot、$$ 降级、多余空行压缩），且幂等，所以卡片里手改过的文字、草稿箱再保存
        # 都不会让入库源码走形。解析卡在 post_process 里已经跑过同一条链，这里再过一遍是恒等。
        # 先用原文抽选项（options 字段认得「A．」行，不认 \\begin{choices}）。
        # content_for_options / answer_markdown_raw 是归一化前的原文，供查重指纹使用
        # （build_question_fingerprint 内部会再归一化做哈希；图片可见性判定也依赖原文）。
        content_for_options = content
        answer_markdown_raw = answer_markdown
        content, answer_markdown, question_type, paper_question_no = _normalize_pair_for_db(
            content, answer_markdown, question_type, paper_question_no
        )
        # 选择题末尾的作答空括号统一成 \paren。**必须放在 _normalize_pair_for_db
        # 之后**：只有它内部的 normalize_for_storage 跑完，纯文本选项行才被包装成
        # \begin{choices}，此时补 \paren 才会落在题干末尾而不是选项行之后。
        # 该顺序与 parsing_pipeline.apply_storage_normalization 完全一致，是
        # 「三条录入路径入库源码逐字节相同」的必要条件（test_entry_path_latex_parity）。
        content = normalize_choice_paren_macro(content, question_type)

        # 1. Fallback if third level is empty, default to chapter
        if not category_knowledge and category_chapter:
            category_knowledge = category_chapter
            
        db_question.content = content
        db_question.question_type = question_type
        db_question.category_compulsory = category_compulsory
        db_question.category_chapter = category_chapter
        db_question.category_knowledge = category_knowledge
        db_question.applicable_grade = applicable_grade
        db_question.topic_id = _resolve_topic_id(category_compulsory, category_chapter, category_knowledge, topic)
        db_question.difficulty = difficulty
        db_question.source = source
        db_question.paper_category = paper_category
        db_question.paper_question_no = paper_question_no
        db_question.answer_markdown = answer_markdown
        db_question.review = review
        db_question.tikz_code = parsed_tikz_code
        db_question.tikz_reference_image_path = parsed_tikz_reference_image_path
        if figure_align in ["right", "center", "bottom_right"]:
            db_question.figure_align = figure_align
        db_question.tags = tags
        from mathbank.sources_helper import sync_question_sources as _sync_question_sources
        _sync_question_sources(db, db_question.id, sources)
        sync_question_tags(db, db_question, tags)
        sync_question_knowledge(db, db_question, knowledge_points)
        db_question.exam_year = _parse_int_or_none(exam_year)
        db_question.exam_region = exam_region or ""
        db_question.exam_stage = exam_stage or ""
        db_question.video_url = video_url or ""
        db_question.option_columns = _parse_int_or_default(option_columns, 0)
        db_question.analysis_methods = _normalize_analysis_methods(analysis_methods)
        db_question.difficulty_index = _parse_float_or_none(difficulty_index)
        db_question.cognitive_level = cognitive_level or ""
        db_question.core_competency = core_competency or ""
        db_question.error_prone_points = _normalize_text_list(error_prone_points)
        db_question.options = json.dumps(
            extract_structured_options(content_for_options, question_type), ensure_ascii=False
        )
        record_tikz_versions(
            db,
            db_question.id,
            {"content": parsed_content_tikz_assets, "answer": parsed_answer_tikz_assets},
        )
        # Physical cleanup happens only after the database commit succeeds.
        removed_images = set(old_images) - set(parsed_img_paths)

        db_question.image_paths = parsed_img_paths
        db_question.content_tikz_assets = parsed_content_tikz_assets
        db_question.answer_tikz_assets = parsed_answer_tikz_assets
        
        # Handle related question association updates (transitive relation)
        related_id_int = int(related_question_id) if related_question_id and related_question_id.strip() else None
        if related_id_int:
            q_related = db.query(Question).filter(Question.id == related_id_int).first()
            if q_related and q_related.id != db_question.id:
                g1 = db_question.association_group_id
                g2 = q_related.association_group_id
                
                if not g1 and not g2:
                    new_grp = str(uuid.uuid4())
                    db_question.association_group_id = new_grp
                    q_related.association_group_id = new_grp
                elif g1 and not g2:
                    q_related.association_group_id = g1
                elif not g1 and g2:
                    db_question.association_group_id = g2
                else:
                    if g1 != g2:
                        db.query(Question).filter(Question.association_group_id == g1).update(
                            {Question.association_group_id: g2}, synchronize_session=False
                        )
                        db_question.association_group_id = g2
        
        # Update or create active QuestionCurriculum mapping
        active_version = get_active_version_code()
        curriculum_map = db.query(QuestionCurriculum).filter(
            QuestionCurriculum.question_id == db_question.id,
            QuestionCurriculum.version_code == active_version
        ).first()
        if not curriculum_map:
            curriculum_map = QuestionCurriculum(
                question_id=db_question.id,
                version_code=active_version
            )
            db.add(curriculum_map)
        curriculum_map.compulsory = category_compulsory
        curriculum_map.chapter = category_chapter
        curriculum_map.knowledge = category_knowledge

        topic_slots = sync_question_topics(
            db,
            db_question.id,
            active_version,
            primary={
                "compulsory": category_compulsory,
                "chapter": category_chapter,
                "knowledge": category_knowledge,
                "topic": topic,
                "topic_id": _resolve_topic_id(category_compulsory, category_chapter, category_knowledge, topic),
            },
            extra=_resolve_extra_topic_ids(extra_topics),
        )
        db.flush()
        try:
            question_fingerprint = build_prepared_question_fingerprint(
                content=content_for_options,
                answer_markdown=answer_markdown_raw,
                question_type=question_type,
                image_paths=parsed_img_paths,
                content_tikz_assets=parsed_content_tikz_assets,
                answer_tikz_assets=parsed_answer_tikz_assets,
                tikz_code=parsed_tikz_code,
                tikz_reference_image_path=parsed_tikz_reference_image_path,
            )
        except Exception as fingerprint_exc:
            question_fingerprint = None
            duplicate_warning = "题目已更新，但本次查重指纹未生成；后台将尝试补建，失败时下次启动继续。"
            db.query(StoredQuestionFingerprint).filter(
                StoredQuestionFingerprint.question_id == db_question.id
            ).delete(synchronize_session=False)
            print(
                "[Duplicate Index] Update fingerprint failed open "
                f"(type={type(fingerprint_exc).__name__}); background backfill will retry."
            )
        if duplicate_snapshot_hash and question_fingerprint is not None:
            if duplicate_snapshot_hash != question_fingerprint.content_revision_hash:
                response = duplicate_review_required_response(
                    db,
                    fingerprint=question_fingerprint,
                    question_ids=[],
                    message="题目在查重后已发生变化，请重新查重后保存。",
                    code="duplicate_snapshot_stale",
                )
                db.rollback()
                rollback_question_asset_promotions(asset_promotions)
                return response
            exact_ids = exact_duplicate_ids(
                db,
                question_fingerprint,
                exclude_id=db_question.id,
            )
            if exact_ids and duplicate_override != "independent":
                response = duplicate_review_required_response(
                    db,
                    fingerprint=question_fingerprint,
                    question_ids=exact_ids,
                    message="更新后的题目与题库已有题相同，请核对后再决定。",
                )
                db.rollback()
                rollback_question_asset_promotions(asset_promotions)
                return response
        if question_fingerprint is not None:
            upsert_question_fingerprint(db, db_question, question_fingerprint)
        
        db.commit()
    except Exception as e:
        db.rollback()
        rollback_question_asset_promotions(asset_promotions)
        raise HTTPException(status_code=400, detail=f"更新题目失败: {str(e)}")

    # The question is already durably updated at this point.  Best-effort
    # cleanup and response assembly must not turn success into a false failure.
    _flush_auto_topics()
    try:
        delete_unreferenced_question_assets(db, removed_images)
    except Exception as cleanup_exc:
        print(
            "[Storage Cleanup] Post-commit update cleanup failed "
            f"(type={type(cleanup_exc).__name__}); it will be retried by "
            "the startup orphan cleanup."
        )
    schedule_database_export(background_tasks, operation="update_question")
    if duplicate_warning:
        schedule_question_fingerprint_retry(
            background_tasks,
            question_id=question_id,
            operation="update_question",
        )
    response = committed_question_response(
        db,
        db_question,
        question_id,
        operation="update_question",
    )
    if duplicate_warning:
        response["warning"] = duplicate_warning
    return response


@router.post("/api/format/fractions")
def format_fraction_style(text: str = Form("", max_length=200000)):
    r"""「规范分式」预览：正文级分式统一成 ``\dfrac``，上标/分式内部降回 ``\frac``。

    刻意做成无状态纯文本端点：不读题也不写题。前端拿返回值先给老师确认，确认之后才写回
    编辑框，真正落库仍然是点「保存」那一次。这样即使整段公式被判为「需要完整 TeX 解释器」
    而原样返回，对存量数据也没有任何副作用。
    """

    normalized = normalize_fraction_style(text)
    return {"status": "success", "text": normalized, "changed": normalized != text}


@router.post("/api/questions/{question_id}/figure_align")
def update_question_figure_align(
    question_id: int,
    figure_align: str = Form("right"),
    figure_size: str | None = Form(None),
    image_layouts: str | None = Form(None),
    db: Session = Depends(get_db)
):
    """持久化题末插图的位置与尺寸（A4 预览气泡菜单的落库入口）。

    这个端点只在用户真的点过气泡菜单时调用，所以 figure_align_custom 直接置真：
    此后导出的默认排版不能再悄悄盖掉老师手选的「题干右侧」。
    image_layouts 走枚举校验后落库，值最终要拼进 adjustbox，不收野值。
    """

    db_question = db.query(Question).filter(Question.id == question_id).first()
    if not db_question:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
    if figure_align not in FIGURE_ALIGN_VALUES:
        figure_align = "right"
    # 三列都是「没传就不动」：调用方只调位置时，绝不该把老师设过的插图尺寸/逐图布局
    # 一起清回默认（表单字段缺省值是 None，而不是会覆盖旧值的 "auto" / "{}"）。
    if image_layouts is not None:
        try:
            db_question.image_layouts = image_layouts
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if figure_size is not None:
        db_question.figure_size = normalize_figure_size(figure_size)
    db_question.figure_align = figure_align
    db_question.figure_align_custom = True
    db.commit()
    db.refresh(db_question)
    return {
        "status": "success",
        "question_id": question_id,
        "figure_align": db_question.figure_align,
        "figure_align_custom": True,
        "figure_size": db_question.figure_size,
        "image_layouts": db_question.image_layouts,
    }


@router.get("/api/questions/{question_id}/associated")
def get_associated_questions(question_id: int, db: Session = Depends(get_db)):
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="未找到题目")
        
    grp = q.association_group_id
    if not grp or grp.strip() == "":
        return []
        
    associated = db.query(Question).filter(
        Question.association_group_id == grp,
        Question.id != question_id
    ).all()
    
    seq_map = get_seq_mapping(db, [item.id for item in associated])
    return [{**_decorate_topic_field(item.to_dict()), "seq_num": seq_map.get(item.id)} for item in associated]


@router.post("/api/questions/{question_id}/associate")
def associate_questions_endpoint(
    background_tasks: BackgroundTasks,
    question_id: int,
    target_id: int = Form(...),
    db: Session = Depends(get_db)
):
    q1 = db.query(Question).filter(Question.id == question_id).first()
    q2 = db.query(Question).filter(Question.id == target_id).first()
    if not q1 or not q2:
        raise HTTPException(status_code=404, detail="未找到对应题目")
        
    if q1.id == q2.id:
        raise HTTPException(status_code=400, detail="不能自己和自己关联")
        
    g1 = q1.association_group_id
    g2 = q2.association_group_id
    
    try:
        if not g1 and not g2:
            new_grp = str(uuid.uuid4())
            q1.association_group_id = new_grp
            q2.association_group_id = new_grp
        elif g1 and not g2:
            q2.association_group_id = g1
        elif not g1 and g2:
            q1.association_group_id = g2
        else:
            if g1 != g2:
                db.query(Question).filter(Question.association_group_id == g1).update(
                    {Question.association_group_id: g2}, synchronize_session=False
                )
                q1.association_group_id = g2
                
        db.commit()
        
        # Auto export database to files for Git synchronization and AI referencing (Async Background Task)
        schedule_database_export(background_tasks, operation="associate_questions")
        
        return {"status": "success", "message": "关联成功"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"关联失败: {str(e)}")


@router.delete("/api/questions/{question_id}/associated")
def remove_association(
    question_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Remove a question from its association group (bidirectional)."""
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="未找到题目")

    grp = q.association_group_id
    if not grp or grp.strip() == "":
        return {"status": "success", "message": "该题目无关联关系"}

    try:
        # Clear this question's group ID
        q.association_group_id = ""

        # If only one other question remains in the group, clear its group too (no point in a group of one)
        remaining = db.query(Question).filter(
            Question.association_group_id == grp,
            Question.id != question_id
        ).all()

        if len(remaining) == 1:
            remaining[0].association_group_id = ""

        db.commit()
        
        # Auto export database to files for Git synchronization and AI referencing (Async Background Task)
        schedule_database_export(background_tasks, operation="remove_association")
        
        return {"status": "success", "message": "已成功解除所有关联"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"解除关联失败: {str(e)}")


@router.delete("/api/questions/{question_id}")
def delete_question(
    question_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    db_question = db.query(Question).filter(Question.id == question_id).first()
    if not db_question:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
        
    image_paths_to_check = list(db_question.image_paths)
    try:
        from sqlalchemy import func

        affected_paper_ids = [
            paper_id
            for (paper_id,) in db.query(PaperQuestion.paper_id)
            .filter(PaperQuestion.question_id == question_id)
            .distinct()
            .all()
        ]
        if affected_paper_ids:
            remaining_scores = dict(
                db.query(
                    PaperQuestion.paper_id,
                    func.coalesce(func.sum(PaperQuestion.score), 0),
                )
                .filter(
                    PaperQuestion.paper_id.in_(affected_paper_ids),
                    PaperQuestion.question_id != question_id,
                )
                .group_by(PaperQuestion.paper_id)
                .all()
            )
            for paper in db.query(Paper).filter(
                Paper.id.in_(affected_paper_ids)
            ):
                paper.total_score = int(remaining_scores.get(paper.id, 0))
        db.delete(db_question)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"删除题目失败: {str(e)}")

    # The database delete is complete.  Image cleanup is intentionally
    # best-effort so a locked/missing file cannot make the client believe the
    # question still exists and submit a duplicate delete.
    try:
        delete_unreferenced_question_assets(db, image_paths_to_check)
    except Exception as cleanup_exc:
        print(
            "[Storage Cleanup] Post-commit delete cleanup failed "
            f"(type={type(cleanup_exc).__name__}); it will be retried by "
            "the startup orphan cleanup."
        )

    # Auto export database to files for Git synchronization and AI referencing (Async Background Task)
    schedule_database_export(background_tasks, operation="delete_question")

    return {"status": "success", "message": "题目删除成功"}


def rollback_question_asset_promotions(promotions: list[tuple[Path, Path]]) -> None:
    """Best-effort compensation when a DB transaction rejects promoted files."""

    for source, destination in reversed(promotions):
        try:
            if destination.is_file() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(source))
        except OSError as exc:
            print(f"[Storage Rollback] Failed to restore a promoted asset: {type(exc).__name__}")


def _referenced_question_assets(db: Session) -> set[Path]:
    """Resolve every stored question image reference with one database query.

    ⚠️ 判据统一走 ``mathbank.image_references.collect_referenced_assets``：
    早先这里只认带 ``/static/uploads/`` 前缀的引用，而「彻底 LaTeX 化」后
    content 里存的是 ``\\includegraphics{裸文件名}``，导致已入库的插图被
    判成孤儿删掉。共享实现同时覆盖 image_paths / content / answer_markdown
    里的裸名与带前缀两种写法。
    """
    from mathbank.image_references import collect_referenced_assets

    return collect_referenced_assets(
        db, uploads_dir=UPLOAD_DIR, url_prefix=UPLOAD_DIR_REL
    )


def delete_unreferenced_question_assets(db: Session, references) -> int:
    """Delete committed-away images only when no remaining question uses them."""

    candidates: set[Path] = set()
    for reference in set(references or []):
        try:
            candidates.add(
                resolve_upload_asset(
                    reference,
                    uploads_dir=UPLOAD_DIR,
                    url_prefix=UPLOAD_DIR_REL,
                    require_file=False,
                )
            )
        except AssetSecurityError:
            print("[Storage Cleanup] Skipped an invalid legacy image path.")

    if not candidates:
        return 0
    referenced = _referenced_question_assets(db)
    removed = 0
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate not in referenced:
                # 软删除：移入 .trash/ 保留 30 天，误删可恢复
                if trash_upload_asset(candidate, UPLOAD_DIR):
                    removed += 1
        except OSError:
            print("[Storage Cleanup] Skipped an unavailable legacy image path.")
    return removed


def promote_question_temp_assets(
    content: str,
    answer_markdown: str,
    image_paths_list: list,
    *,
    promotion_log: list[tuple[Path, Path]] | None = None,
) -> tuple:
    """物理移动临时图片到永久目录，并更新题干、解析和图片路径列表中的引用"""
    import shutil

    if not isinstance(image_paths_list, list):
        raise AssetSecurityError("image_paths 必须是插图路径数组。")

    embedded_paths = re.findall(
        r'/static/(?:uploads|test_uploads)/tmp/[a-zA-Z0-9_.-]+',
        f"{content}\n{answer_markdown}",
    )
    all_references = [value for value in image_paths_list if value] + embedded_paths

    # Validate the complete set before moving anything.  A bad second path must
    # not leave the first path half-promoted.
    canonical_by_input: dict[str, str] = {}
    resolved_by_canonical: dict[str, Path] = {}
    for reference in all_references:
        canonical = normalize_upload_asset_reference(
            reference,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
        )
        canonical_by_input[reference] = canonical
        resolved_by_canonical.setdefault(
            canonical,
            resolve_upload_asset(
                canonical,
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
            ),
        )

    upload_root = Path(UPLOAD_DIR).resolve()
    temp_root = Path(TMP_UPLOAD_DIR).resolve()
    promoted_by_canonical: dict[str, str] = {}
    for canonical, source in resolved_by_canonical.items():
        if source.parent == temp_root:
            destination_url = f"/{UPLOAD_DIR_REL}/{source.name}"
            destination = resolve_upload_asset(
                destination_url,
                uploads_dir=upload_root,
                url_prefix=UPLOAD_DIR_REL,
                require_file=False,
            )
            if destination.exists():
                raise AssetSecurityError("目标插图文件已存在，已停止覆盖。")
            shutil.move(str(source), str(destination))
            if promotion_log is not None:
                promotion_log.append((source, destination))
            promoted_by_canonical[canonical] = normalize_upload_asset_reference(
                destination_url,
                uploads_dir=upload_root,
                url_prefix=UPLOAD_DIR_REL,
            )
        elif source.is_relative_to(upload_root):
            promoted_by_canonical[canonical] = canonical
        else:  # Defensive; resolve_upload_asset should already make this impossible.
            raise AssetSecurityError("临时插图越出了上传目录。")

    replacements: dict[str, str] = {}
    for original, canonical in canonical_by_input.items():
        promoted = promoted_by_canonical[canonical]
        replacements[original] = promoted
        replacements[canonical] = promoted

    new_content = content
    new_answer = answer_markdown
    for old_path, new_path in replacements.items():
        new_content = new_content.replace(old_path, new_path)
        new_answer = new_answer.replace(old_path, new_path)

    updated_paths: list[str] = []
    for original in image_paths_list:
        if not original:
            continue
        promoted = promoted_by_canonical[canonical_by_input[original]]
        if promoted not in updated_paths:
            updated_paths.append(promoted)

    for embedded in embedded_paths:
        promoted = promoted_by_canonical[canonical_by_input[embedded]]
        if promoted not in updated_paths:
            updated_paths.append(promoted)

    return new_content, new_answer, updated_paths


# ============ 一题多来源 CRUD (v21) ============

def _load_sources_sorted(db: Session, question_id: int) -> list:
    """加载某题全部来源，按 高考>模考>联考>校考 + 年份新在前 排序。"""
    from mathbank.database import QuestionSource
    from mathbank.sources_helper import sort_sources

    rows = db.query(QuestionSource).filter(QuestionSource.question_id == question_id).all()
    return [r.to_dict() for r in sort_sources(rows)]


@router.get("/api/source-suggestions")
def source_suggestions(db: Session = Depends(get_db)):
    """来源智能联想：从 question_sources 去重出常用年份/学校地区/考试类型/卷名，
    供录题与拆卷来源输入框的 datalist 自动补全（按使用频次排序）。"""
    from collections import Counter

    from mathbank.database import QuestionSource

    rows = db.query(
        QuestionSource.year,
        QuestionSource.region,
        QuestionSource.exam_type,
        QuestionSource.paper_title,
    ).all()
    years, regions, exam_types, titles = Counter(), Counter(), Counter(), Counter()
    for y, region, exam_type, title in rows:
        y = (y or "").strip()
        if y:
            years[y] += 1
        region = (region or "").strip()
        if region:
            regions[region] += 1
        exam_type = (exam_type or "").strip()
        if exam_type:
            exam_types[exam_type] += 1
        title = (title or "").strip()
        if title:
            titles[title] += 1

    def _top(counter, n=60):
        return [k for k, _ in counter.most_common(n)]

    return {
        "years": sorted(years.keys(), reverse=True),
        "regions": _top(regions),
        "exam_types": _top(exam_types),
        "paper_titles": _top(titles),
    }


@router.get("/api/questions/{question_id}/sources")
def list_question_sources(question_id: int, db: Session = Depends(get_db)):
    """某题的全部来源（已排序，第一条为主来源）。"""
    db_question = db.query(Question).filter(Question.id == question_id).first()
    if not db_question:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
    return {"status": "success", "sources": _load_sources_sorted(db, question_id)}


@router.post("/api/questions/{question_id}/sources")
def add_question_source(question_id: int, payload: dict, db: Session = Depends(get_db)):
    """给某题追加一条来源（年份 + 学校/地区 + 考试类型 + 整卷出处）。"""
    from mathbank.database import QuestionSource

    db_question = db.query(Question).filter(Question.id == question_id).first()
    if not db_question:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
    year = str((payload or {}).get("year") or "").strip()
    region = str((payload or {}).get("region") or "").strip()
    exam_type = str((payload or {}).get("exam_type") or "").strip()
    paper_title = str((payload or {}).get("paper_title") or "").strip()
    if not (year or region or exam_type):
        raise HTTPException(status_code=400, detail="年份/学校地区/考试类型至少填写一项")
    if year and (len(year) > 20 or not year.isdigit()):
        raise HTTPException(status_code=400, detail="年份需为数字（如 2026）")
    if len(region) > 100:
        raise HTTPException(status_code=400, detail="学校/地区过长（最多 100 字符）")
    if len(exam_type) > 50:
        raise HTTPException(status_code=400, detail="考试类型过长（最多 50 字符）")
    if len(paper_title) > 200:
        raise HTTPException(status_code=400, detail="试卷出处过长（最多 200 字符）")
    last = db.query(QuestionSource).filter(
        QuestionSource.question_id == question_id
    ).order_by(QuestionSource.sort_order.desc()).first()
    max_order = last.sort_order if last else 0
    row = QuestionSource(
        question_id=question_id,
        year=year,
        region=region,
        exam_type=exam_type,
        paper_title=paper_title,
        sort_order=max_order + 1,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    from mathbank.sources_helper import resequence_sources, sort_sources
    resequence_sources(db, question_id)
    # 主来源字段同步为排序后第一条
    rows = db.query(QuestionSource).filter(
        QuestionSource.question_id == question_id
    ).all()
    if rows:
        from mathbank.database import format_source_text
        main_row = sort_sources(rows)[0]
        db_question.source = format_source_text(
            main_row.year, main_row.region, main_row.exam_type
        )
    db.commit()
    # 总文本字段回填：旧字段 questions.source 为空时用主来源文本补上
    if not (db_question.source or "").strip():
        from mathbank.database import format_source_text

        db_question.source = format_source_text(year, region, exam_type)
        db.commit()
    return {
        "status": "success",
        "source": row.to_dict(),
        "sources": _load_sources_sorted(db, question_id),
    }


@router.delete("/api/questions/{question_id}/sources/{source_id}")
def delete_question_source(question_id: int, source_id: int, db: Session = Depends(get_db)):
    """删除某题的一条来源。"""
    from mathbank.database import QuestionSource

    row = db.query(QuestionSource).filter(
        QuestionSource.id == source_id,
        QuestionSource.question_id == question_id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="未找到该来源记录")
    db_question = db.query(Question).filter(Question.id == question_id).first()
    db.delete(row)
    db.commit()
    from mathbank.sources_helper import resequence_sources, sort_sources
    resequence_sources(db, question_id)
    from mathbank.database import format_source_text
    rows = db.query(QuestionSource).filter(
        QuestionSource.question_id == question_id
    ).all()
    if rows:
        main_row = sort_sources(rows)[0]
        if db_question is not None:
            db_question.source = format_source_text(
                main_row.year, main_row.region, main_row.exam_type
            )
    elif db_question is not None:
        db_question.source = ""
    db.commit()
    return {"status": "success", "sources": _load_sources_sorted(db, question_id)}

