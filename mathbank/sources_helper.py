# -*- coding: utf-8 -*-
"""题目来源辅助模块：一题多来源的排序与批量加载。

排序规则（讲义展示用，主来源 = 排序后第一条）：
  1. 考试类型优先级：高考 > 模考 > 联考 > 校考/月考（其余归末档）
  2. 同优先级内：年份新的在前
  3. 再同：记录 id 新的在前
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session


def source_rank(exam_type: str) -> int:
    """考试类型优先级：高考 0 / 模考 1 / 联考 2 / 其余（校考·月考·期中·期末等）3。"""
    t = (exam_type or "").strip()
    if "高考" in t:
        return 0
    if "模" in t:
        return 1
    if "联考" in t:
        return 2
    return 3


def sort_sources(rows) -> list:
    """按 rank asc → year desc → id desc 排序。rows 为 QuestionSource 对象列表。"""
    def key(row):
        year = str(row.year or "").strip()
        y = 0
        if year.isdigit():
            y = int(year)
        return (source_rank(row.exam_type), -y, -(row.id or 0))
    return sorted(rows, key=key)


def load_question_sources(db: Session, qids: list) -> dict:
    """批量加载来源并按排序规则分组：{question_id: [source.to_dict(), ...]}。"""
    from mathbank.database import QuestionSource

    qids = [int(q) for q in (qids or []) if q]
    if not qids:
        return {}
    rows = db.query(QuestionSource).filter(QuestionSource.question_id.in_(qids)).all()
    grouped: dict[int, list] = {}
    for row in rows:
        grouped.setdefault(row.question_id, []).append(row)
    result = {}
    for qid, lst in grouped.items():
        result[qid] = [r.to_dict() for r in sort_sources(lst)]
    return result


def parse_sources_payload(raw: str) -> Optional[list]:
    """解析前端 Form 字段 sources（JSON 数组字符串）。非 JSON / 非数组返回 None（表示未传）。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return []
    try:
        import json
        data = json.loads(s)
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    return data


def _validated_source_item(item) -> Optional[dict]:
    """校验单条来源：年份/地区/考试类型至少一项、字符串长度上限、枚举合法。非法返回 None。"""
    if not isinstance(item, dict):
        return None
    year = str(item.get("year") or "").strip()
    region = str(item.get("region") or "").strip()
    province = str(item.get("province") or "").strip()
    city = str(item.get("city") or "").strip()
    grade = str(item.get("grade") or "").strip()
    semester = str(item.get("semester") or "").strip()
    exam_type = str(item.get("exam_type") or "").strip()
    paper_title = str(item.get("paper_title") or "").strip()
    question_no = str(item.get("question_no") or "").strip()
    if not (year or region or exam_type or province or city):
        return None
    if year and (len(year) > 20 or not year.isdigit()):
        return None
    if len(region) > 100:
        return None
    if len(province) > 50:
        return None
    if len(city) > 50:
        return None
    if grade not in ("", "g1", "g2", "g3"):
        return None
    if semester not in ("", "上", "下"):
        return None
    if len(exam_type) > 50:
        return None
    if len(paper_title) > 200:
        return None
    if len(question_no) > 50:
        return None
    return {
        "year": year,
        "region": region,
        "province": province,
        "city": city,
        "grade": grade,
        "semester": semester,
        "exam_type": exam_type,
        "paper_title": paper_title,
        "question_no": question_no,
    }


def resequence_sources(db: Session, question_id: int) -> None:
    """按排序规则重排某题来源的 sort_order（存储序 = 展示序）。"""
    from mathbank.database import QuestionSource

    rows = db.query(QuestionSource).filter(
        QuestionSource.question_id == question_id
    ).all()
    for idx, row in enumerate(sort_sources(rows), start=1):
        if row.sort_order != idx:
            row.sort_order = idx


def sync_question_sources(db: Session, question_id: int, raw_sources) -> Optional[str]:
    """以前端传入的 sources 为准重写某题的来源列表（删旧插新），并返回主来源文本。

    - raw_sources 为 None 或非法 JSON → 不动来源，返回 None（兼容旧客户端）。
    - 合法数组（可为空）→ 删旧插新；空数组表示清空。
    - 回填 questions.source 为主来源文本（列表非空时）；主来源带 question_no 时
      同步镜像到 questions.paper_question_no。
    """
    from mathbank.database import QuestionSource, Question, format_source_text

    items = parse_sources_payload(raw_sources) if not isinstance(raw_sources, list) else raw_sources
    if items is None:
        return None
    cleaned = []
    for item in items:
        v = _validated_source_item(item)
        if v:
            cleaned.append(v)

    # 重写来源列表
    db.query(QuestionSource).filter(QuestionSource.question_id == question_id).delete()
    for idx, item in enumerate(cleaned, start=1):
        db.add(QuestionSource(
            question_id=question_id,
            year=item["year"],
            region=item["region"],
            province=item["province"],
            city=item["city"],
            grade=item["grade"],
            semester=item["semester"],
            exam_type=item["exam_type"],
            paper_title=item["paper_title"],
            question_no=item["question_no"],
            sort_order=idx,
        ))
    db.flush()
    resequence_sources(db, question_id)

    # 主来源 = 排序后第一条（高考>模考>联考>校考 + 年份新在前）
    main_text = ""
    rows = db.query(QuestionSource).filter(QuestionSource.question_id == question_id).all()
    sorted_rows = sort_sources(rows)
    if sorted_rows:
        main_row = sorted_rows[0]
        main_text = format_source_text(
            main_row.year, main_row.region, main_row.exam_type,
            province=main_row.province, city=main_row.city,
            grade=main_row.grade, semester=main_row.semester,
        )
        db_question = db.query(Question).filter(Question.id == question_id).first()
        if db_question is not None:
            db_question.source = main_text
            # 主表字段只是主来源的镜像。原卷题号属于「题 × 卷」关系：换卷后
            # 各来源编号互不相同，全局身份永远是 questions.id。
            if str(main_row.question_no or "").strip():
                db_question.paper_question_no = str(main_row.question_no).strip()
    return main_text
