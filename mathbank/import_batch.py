"""共享批量录入流水线（第四录入方案）。

阶段：session -> plan -> recognize -> risk -> commit -> rollback

设计取舍
--------
望悠把批次状态存在数据库里；我们这里**不新建表**——``db_migrations.py``
是上游红线文件，为它加迁移会与上游发版冲突。批次状态改为 JSON 落盘
（``data/import_batches/<session_id>.json``），提交成功后才在既有的
``import_batches`` 表里记一行用于整批回滚，既拿到"整批可回滚"的能力，
又不碰数据库结构。

防陈旧提交沿用了望悠的三道闸门：
- ``previewHash``：提交前校验计划与识别结果未被改动
- ``expectedRevision``：乐观并发，防止两个页面同时改同一批
- 风险项必须 ``riskId`` + ``reason`` 同时非空才准入
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone

from mathbank.paths import PROJECT_ROOT

BATCH_DIR = os.path.join(PROJECT_ROOT, "data", "import_batches")

# 望悠的上限：单文件 300MB。我们沿用。
MAX_FILE_BYTES = 300 * 1024 * 1024

TERMINAL_ITEM_STATUS = ("recognized", "failed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _session_path(session_id: str) -> str:
    return os.path.join(BATCH_DIR, f"{session_id}.json")


def _load(session_id: str) -> dict:
    path = _session_path(session_id)
    if not os.path.exists(path):
        raise KeyError(f"找不到录入会话: {session_id}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save(state: dict) -> None:
    os.makedirs(BATCH_DIR, exist_ok=True)
    path = _session_path(state["session_id"])
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)


def _hash(payload) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 1. session
# ---------------------------------------------------------------------------


def create_session(mode: str, files_meta: list) -> dict:
    """建立录入会话。``files_meta`` 每项 {name, size, pages}。"""

    if mode not in ("textbook", "exam"):
        raise ValueError(f"未知录入模式: {mode}")
    for meta in files_meta:
        if int(meta.get("size") or 0) > MAX_FILE_BYTES:
            raise ValueError(
                f"源文件 {meta.get('name')} 超过 300MB 上限"
            )

    session_id = uuid.uuid4().hex
    state = {
        "session_id": session_id,
        "mode": mode,
        "revision": 1,
        "created_at": _now(),
        "updated_at": _now(),
        "files": [
            {
                "id": uuid.uuid4().hex[:12],
                "name": meta.get("name") or "",
                "size": int(meta.get("size") or 0),
                "pages": int(meta.get("pages") or 0),
            }
            for meta in files_meta
        ],
        "items": [],
        "risks": [],
        "preview_hash": "",
        "committed": False,
        "batch_id": None,
    }
    _save(state)
    return state


# ---------------------------------------------------------------------------
# 2. plan
# ---------------------------------------------------------------------------


def build_plan(session_id: str, toc_pages: dict | None = None) -> dict:
    """按"文件 x 页"切出待识别项，并生成 previewHash。

    ``toc_pages`` 为可选字典 ``{file_id: [页码...]}``，命中页码的项标记为
    ``is_toc=True``，识别阶段改用目录页提示词、且不参与正文风险扫描。
    """

    state = _load(session_id)
    toc_map = {
        str(fid): set(int(p) for p in pages)
        for fid, pages in (toc_pages or {}).items()
    }
    items = []
    for f in state["files"]:
        pages = max(1, int(f.get("pages") or 1))
        toc_set = toc_map.get(f["id"], set())
        for page in range(1, pages + 1):
            items.append(
                {
                    "id": uuid.uuid4().hex[:12],
                    "file_id": f["id"],
                    "page": page,
                    "is_toc": page in toc_set,
                    "status": "pending",
                    "payload": None,
                }
            )
    state["items"] = items
    state["toc_pages"] = sorted(
        {it["page"] for it in items if it.get("is_toc")}
    )
    state["preview_hash"] = _hash(
        [[i["file_id"], i["page"]] for i in items]
    )
    state["updated_at"] = _now()
    _save(state)
    return state


# ---------------------------------------------------------------------------
# 3b. 教材模式：目录树（TOC）解析与跨页缝合
# ---------------------------------------------------------------------------


def set_toc_result(session_id: str, item_id: str, toc: list) -> dict:
    """写入某一目录页的识别结果（chapters 列表）。"""

    state = _load(session_id)
    for item in state["items"]:
        if item["id"] == item_id:
            item["payload"] = {"toc": toc or []}
            item["status"] = "recognized"
            break
    else:
        raise KeyError(f"找不到待识别项: {item_id}")
    state["revision"] = int(state.get("revision") or 1) + 1
    state["updated_at"] = _now()
    _save(state)
    return state


_NUMBER_RE = __import__("re").compile(r"\d+(?:\.\d+)*")


def _derive_chapter_section(text: str) -> tuple[str, str]:
    """从题号/标签里抽章、节编号。

    例："例 1.1.1" / "习题2" / "1.1" -> ("1", "1.1")；"第1章" -> ("1", "")。
    抽不到返回 ("", "")。
    """

    if not text:
        return "", ""
    m = _NUMBER_RE.search(str(text))
    if not m:
        return "", ""
    parts = m.group(0).split(".")
    chapter = parts[0]
    section = ".".join(parts[:2]) if len(parts) >= 2 else ""
    return chapter, section


def _chapter_default(chapter_num: str) -> str:
    return f"第{chapter_num}章" if chapter_num else ""


def _merge_toc(state: dict) -> dict:
    """合并所有目录页识别结果，返回 {编号: 标题}（章、节编号都收）。"""

    index: dict[str, str] = {}
    for item in state.get("items", []):
        if not item.get("is_toc") or item.get("status") != "recognized":
            continue
        for ch in (item.get("payload") or {}).get("toc") or []:
            cnum = str(ch.get("number") or "").strip()
            if cnum:
                index[cnum] = str(ch.get("title") or "").strip()
            for sec in (ch.get("sections") or []):
                snum = str(sec.get("number") or "").strip()
                if snum:
                    index[snum] = str(sec.get("title") or "").strip()
    return index


def _flatten_textbook_questions(state: dict) -> list:
    """把教材正文页的识别结果摊平成按页排序的题列表（含来源信息）。"""

    out = []
    for item in state.get("items", []):
        if item.get("is_toc") or item.get("status") != "recognized":
            continue
        for q in (item.get("payload") or {}).get("items") or []:
            out.append(
                {
                    "src_item_id": item["id"],
                    "src_page": item["page"],
                    "label": str(q.get("label") or ""),
                    "question_type": str(q.get("question_type") or "").strip(),
                    "stem": str(q.get("stem") or ""),
                    "options": q.get("options") or [],
                    "analysis": str(q.get("analysis") or ""),
                    "answer": str(q.get("answer") or ""),
                    "paper_question_no": str(q.get("paper_question_no") or "").strip(),
                    "score": q.get("score") or 0,
                    "figures": q.get("figures") or [],
                    "continues_next": bool(q.get("continues_next") or False),
                }
            )
    return out


def _stitch_textbook(questions: list) -> list:
    """合并跨页长题：上一题 ``continues_next`` 为真且下一题 label 相同则拼接。"""

    out: list = []
    for q in questions:
        if (
            out
            and out[-1].get("_continues")
            and out[-1].get("label") == q.get("label")
        ):
            prev = out[-1]
            prev["stem"] = (prev["stem"] + "\n" + q["stem"]).strip()
            prev["analysis"] = (prev["analysis"] + "\n" + q["analysis"]).strip()
            prev["answer"] = (prev["answer"] + "\n" + q["answer"]).strip()
            for fig in q["figures"]:
                if fig not in prev["figures"]:
                    prev["figures"].append(fig)
            prev["_continues"] = q.get("continues_next", False)
            prev["src_pages"] = prev.get("src_pages", [prev["src_page"]]) + [
                q["src_page"]
            ]
            continue
        merged = dict(q)
        merged["_continues"] = q.get("continues_next", False)
        merged["src_pages"] = [q["src_page"]]
        out.append(merged)
    return out


def _to_commit_question(q: dict, state: dict, chapter: str, section: str) -> dict:
    """拼出提交阶段用的题字典（raw，提交时再 normalize）。"""

    return {
        "content": q.get("stem") or "",
        "answer_markdown": q.get("analysis") or "",
        "question_type": q.get("question_type") or "detailed_answer",
        "source": str(state.get("title") or ""),
        "paper_question_no": q.get("paper_question_no") or "",
        "image_paths": list(q.get("figures") or []),
        "category_chapter": chapter,
        "category_knowledge": section,
        "label": q.get("label") or "",
    }


def build_catalog_tree(session_id: str) -> dict:
    """教材模式：合并目录、推导章/节、跨页缝合、生成目录树与提交列表。

    非教材模式直接返回，不改动状态。``preview_hash`` 会包含生成的题列表，
    使提交时的防陈旧校验与最新结果一致。
    """

    state = _load(session_id)
    if state.get("mode") != "textbook":
        return state

    toc_index = _merge_toc(state)
    flat = _flatten_textbook_questions(state)
    stitched = _stitch_textbook(flat)

    for q in stitched:
        chapter_num, section_num = _derive_chapter_section(
            q.get("label") or q.get("paper_question_no") or ""
        )
        chapter = toc_index.get(chapter_num) or _chapter_default(chapter_num)
        section = (
            toc_index.get(section_num)
            if section_num and section_num in toc_index
            else (section_num if section_num else chapter)
        )
        q["category_chapter"] = chapter
        q["category_knowledge"] = section

    # 前端预览用的目录树：章 -> 节 -> 题数
    tree: list = []
    chapter_order: list[str] = []
    chapter_map: dict[str, dict] = {}
    for q in stitched:
        ch = q["category_chapter"] or "未分类"
        sec = q["category_knowledge"] or ch
        if ch not in chapter_map:
            chapter_map[ch] = {"title": ch, "sections": {}}
            chapter_order.append(ch)
        secs = chapter_map[ch]["sections"]
        secs[sec] = secs.get(sec, 0) + 1
    for ch in chapter_order:
        node = chapter_map[ch]
        tree.append(
            {
                "title": node["title"],
                "sections": [
                    {"title": s, "count": c} for s, c in node["sections"].items()
                ],
            }
        )

    questions = [
        _to_commit_question(
            q, state, q["category_chapter"], q["category_knowledge"]
        )
        for q in stitched
    ]
    state["catalog_tree"] = tree
    state["questions"] = questions
    state["preview_hash"] = compute_preview_hash(state)
    state["updated_at"] = _now()
    _save(state)
    return state


def final_questions(state: dict) -> list:
    """提交阶段用的题列表：教材模式用缝合后的 ``questions``，其余模式摊平正文页。"""

    if state.get("questions"):
        return state["questions"]
    out = []
    for item in state.get("items", []):
        if item.get("is_toc") or item.get("status") != "recognized":
            continue
        payload = item.get("payload") or {}
        src = str(payload.get("source") or state.get("title") or "")
        for q in payload.get("items") or []:
            out.append(
                {
                    "content": str(q.get("stem") or ""),
                    "answer_markdown": str(q.get("analysis") or ""),
                    "question_type": str(q.get("question_type") or "").strip()
                    or "detailed_answer",
                    "source": src,
                    "paper_question_no": str(q.get("paper_question_no") or "").strip(),
                    "image_paths": list(q.get("figures") or []),
                    "category_chapter": "",
                    "category_knowledge": "",
                    "label": str(q.get("label") or ""),
                }
            )
    return out


def compute_preview_hash(state: dict) -> str:
    """当前"计划 + 已识别结果 + 题列表"的指纹，用于提交前防陈旧校验。"""

    payload = [
        [i["file_id"], i["page"], i["status"], i["payload"]]
        for i in state.get("items", [])
    ]
    if state.get("questions"):
        payload = payload + state["questions"]
    return _hash(payload)


# ---------------------------------------------------------------------------
# 3. recognize
# ---------------------------------------------------------------------------


def record_recognized(session_id: str, item_id: str, payload: dict) -> dict:
    """写入某一页的识别结果。"""

    state = _load(session_id)
    for item in state["items"]:
        if item["id"] == item_id:
            item["payload"] = payload
            item["status"] = "recognized"
            break
    else:
        raise KeyError(f"找不到待识别项: {item_id}")
    state["revision"] = int(state.get("revision") or 1) + 1
    state["updated_at"] = _now()
    _save(state)
    return state


# ---------------------------------------------------------------------------
# 4. risk
# ---------------------------------------------------------------------------


def evaluate_risks(session_id: str) -> list:
    """扫识别结果，产出需要人工确认的风险项。

    判定保持保守（宁可多问一句，也不放脏题入库）：题干为空、题型落不到
    四选一、识别条数为 0，都算风险。
    """

    state = _load(session_id)
    risks = []
    for item in state["items"]:
        if item.get("is_toc"):
            continue
        if item["status"] != "recognized":
            continue
        payload = item["payload"] or {}
        items_out = payload.get("items") or []
        if not items_out:
            risks.append(
                {
                    "id": uuid.uuid4().hex[:12],
                    "item_id": item["id"],
                    "code": "empty_result",
                    "message": f"第 {item['page']} 页没有识别出任何条目",
                }
            )
            continue
        for idx, q in enumerate(items_out):
            stem = str(q.get("stem") or "").strip()
            qtype = str(q.get("question_type") or "").strip()
            if not stem:
                risks.append(
                    {
                        "id": uuid.uuid4().hex[:12],
                        "item_id": item["id"],
                        "code": "empty_stem",
                        "message": f"第 {item['page']} 页第 {idx + 1} 条题干为空",
                    }
                )
            elif qtype not in (
                "single_choice",
                "multi_choice",
                "fill_in_blank",
                "detailed_answer",
            ):
                risks.append(
                    {
                        "id": uuid.uuid4().hex[:12],
                        "item_id": item["id"],
                        "code": "unknown_type",
                        "message": f"第 {item['page']} 页第 {idx + 1} 条题型无法识别（{qtype or '空'}）",
                    }
                )
    for r in risks:
        r["accepted"] = False
        r["reason"] = ""
    state["risks"] = risks
    state["updated_at"] = _now()
    _save(state)
    return risks


def accept_risks(session_id: str, acceptances: list, expected_revision: int | None = None) -> dict:
    """人工确认风险项。

    望悠的硬规则：**每个接受项都必须有 riskId 和非空 reason**。
    ``expectedRevision`` 不匹配则拒绝（乐观并发）。
    """

    if not isinstance(acceptances, list):
        raise ValueError("acceptances 必须是数组")
    for entry in acceptances:
        if not isinstance(entry, dict):
            raise ValueError("acceptances 项必须是对象")
        if not str(entry.get("riskId") or "").strip():
            raise ValueError("每个接受项都必须有 riskId")
        if not str(entry.get("reason") or "").strip():
            raise ValueError("每个接受项都必须有非空 reason（接受理由不得留空）")

    state = _load(session_id)
    current = int(state.get("revision") or 1)
    if expected_revision is not None and int(expected_revision) != current:
        raise ValueError(
            f"批次已被修改（当前 revision={current}，传入 {expected_revision}），请刷新后重试"
        )

    by_id = {str(a["riskId"]).strip(): a for a in acceptances}
    accepted = 0
    for risk in state["risks"]:
        entry = by_id.get(risk["id"])
        if not entry:
            continue
        risk["accepted"] = True
        risk["reason"] = str(entry["reason"]).strip()
        accepted += 1

    state["revision"] = current + 1
    state["updated_at"] = _now()
    _save(state)
    state["accepted_count"] = accepted
    return state


def blocking_risks(state: dict) -> list:
    """尚未被接受的风险项——存在则不允许提交。"""

    return [r for r in state.get("risks", []) if not r.get("accepted")]


# ---------------------------------------------------------------------------
# 5. commit / 6. rollback
# ---------------------------------------------------------------------------


def commit_session(session_id: str, preview_hash: str, db) -> dict:
    """整批入库。previewHash 不符或仍有未确认风险则拒绝。"""

    state = _load(session_id)
    if state.get("committed"):
        raise ValueError("该批次已提交，不能重复提交")
    if str(preview_hash or "").strip() != compute_preview_hash(state):
        raise ValueError("预览已变化（previewHash 不匹配），请重新预览后再提交")
    pending = blocking_risks(state)
    if pending:
        raise ValueError(
            f"还有 {len(pending)} 个风险项未确认，确认后才能提交"
        )

    from mathbank.database import Question
    from mathbank.latex_normalize import (
        normalize_for_storage,
        normalize_stem_for_storage,
    )
    from mathbank.question_metadata import record_import_batch_question

    created_ids = []
    for q in final_questions(state):
        stem = str(q.get("content") or q.get("stem") or "").strip()
        if not stem:
            continue
        qtype = str(q.get("question_type") or "").strip() or "detailed_answer"
        # 题干走 normalize_stem_for_storage：它跑完整条链后再剥离行首原卷题号，
        # 返回 (题号, 入库题干)。题号只在识别结果没给时才回填，保证幂等。
        lead_no, content = normalize_stem_for_storage(stem, qtype)
        answer = normalize_for_storage(
            str(q.get("answer_markdown") or q.get("analysis") or ""),
            qtype,
            is_answer=True,
        )
        paper_no = str(q.get("paper_question_no") or "").strip() or lead_no
        record = Question(
            content=content,
            question_type=qtype,
            answer_markdown=answer,
            source=str(q.get("source") or ""),
            paper_question_no=paper_no,
            image_paths=json.dumps(
                q.get("image_paths") or [], ensure_ascii=False
            ),
            category_chapter=str(q.get("category_chapter") or ""),
            category_knowledge=str(q.get("category_knowledge") or ""),
        )
        db.add(record)
        db.flush()
        created_ids.append(record.id)
        record_import_batch_question(
            db,
            f"batch-{session_id}",
            record.id,
            name=str(q.get("source") or ""),
        )

    db.commit()
    state["committed"] = True
    state["committed_ids"] = created_ids
    state["updated_at"] = _now()
    _save(state)
    return {"session_id": session_id, "question_ids": created_ids, "count": len(created_ids)}


def rollback_session(session_id: str, rollback_hash: str, db) -> dict:
    """整批回滚：删除本批创建的题目。"""

    state = _load(session_id)
    if not state.get("committed"):
        raise ValueError("该批次尚未提交，无需回滚")
    if str(rollback_hash or "").strip() != compute_preview_hash(state):
        raise ValueError("rollbackHash 不匹配，拒绝回滚")

    from mathbank.database import Question

    ids = state.get("committed_ids") or []
    removed = 0
    for qid in ids:
        row = db.query(Question).filter(Question.id == qid).first()
        if row is not None:
            db.delete(row)
            removed += 1
    db.commit()
    state["rolled_back"] = True
    state["updated_at"] = _now()
    _save(state)
    return {"session_id": session_id, "removed": removed}
