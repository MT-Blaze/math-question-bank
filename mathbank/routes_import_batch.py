"""第四录入方案：批量录入流水线的 HTTP 接口（教材/练习册 + 整卷）。

接口对照望悠：
    session -> plan -> recognize -> risk -> commit -> rollback
另有 detail / list 用于查看批次。

识别环节复用现有的视觉 provider（``resolve_ocr_provider``）与
``post_chat_completion``，不新增供应商判断规则。
"""

from __future__ import annotations

import base64
import os
import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

router = APIRouter()

import main as _main
from mathbank import import_batch, import_prompts
from mathbank.ai_json import parse_ai_json
from mathbank.ai_providers import resolve_ocr_provider
from mathbank.database import get_db
from mathbank.task_manager import TaskQueueFull

SESSION_DIR = os.path.join(_main.UPLOAD_DIR, "tmp", "import_batches")


def _session_dir(session_id: str) -> str:
    return os.path.join(SESSION_DIR, session_id)


def _count_pages(path: str) -> int:
    import pymupdf as fitz

    with fitz.open(path) as doc:
        return int(doc.page_count)


def _render_page(path: str, page_index: int, dpi: int = 150) -> bytes:
    import pymupdf as fitz

    with fitz.open(path) as doc:
        page = doc.load_page(page_index)
        return page.get_pixmap(dpi=dpi).tobytes("png")


def _call_vision(messages: list, page_png: bytes) -> dict:
    """调视觉大模型识别一页，返回解析后的 dict（与具体提示词无关）。"""

    provider = resolve_ocr_provider(
        os.getenv("OCR_PREFER_ENGINE") or "ali_bailian"
    )
    encoded = base64.b64encode(page_png).decode("ascii")
    # 把图片挂到最后一条 user 消息上（OpenAI 兼容的多模态 content）
    messages[-1]["content"] = [
        {"type": "text", "text": messages[-1]["content"]},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
    ]

    payload = {
        "model": provider.model_name,
        "messages": messages,
        "temperature": 0.2,
    }
    response = _main.post_chat_completion(
        provider,
        payload,
        timeout=180,
        provider_name=provider.provider_label,
        # 单页整体截止，防止某一页慢响应把整批识别挂死（墙钟上限，非单次 socket 读超时）。
        deadline_seconds=300,
    )
    raw = response.json()["choices"][0]["message"]["content"].strip()
    return parse_ai_json(raw)


def _vision_recognize(mode: str, page_png: bytes, page_no: int, total_pages: int) -> dict:
    """正文页视觉识别。"""

    messages = import_prompts.build_recognize_messages(
        mode, page_no=page_no, total_pages=total_pages
    )
    return _call_vision(messages, page_png)


def _vision_recognize_toc(page_png: bytes, page_no: int, total_pages: int) -> dict:
    """目录页（TOC）视觉识别，返回 {"chapters": [...]}。"""

    messages = import_prompts.build_toc_messages(
        page_no=page_no, total_pages=total_pages
    )
    return _call_vision(messages, page_png)


def _find_toc_pages(path: str, pages: int) -> list:
    """用 PDF 文本层启发式找目录页（1-based 页码列表）。

    判据：含「目录」/「Contents」，或 ≥3 行形如「标题……页码」的编号行。
    """

    import re

    import pymupdf as fitz

    def looks_like_toc(text: str) -> bool:
        if not text:
            return False
        low = text.lower()
        if "目录" in text or "contents" in low:
            return True
        numbered = 0
        for line in (l.strip() for l in text.splitlines() if l.strip()):
            if re.match(r"^[^0-9]*[\u4e00-\u9fffA-Za-z].*\s+\d{1,4}\s*$", line):
                numbered += 1
        return numbered >= 3

    try:
        with fitz.open(path) as doc:
            hits = []
            for p in range(1, max(1, pages) + 1):
                try:
                    text = doc.load_page(p - 1).get_text("text")
                except Exception:
                    continue
                if looks_like_toc(text):
                    hits.append(p)
            return hits
    except Exception:
        return []


def _normalize_items(payload: dict, mode: str) -> dict:
    """把识别结果翻译成我们的契约：题型映射、选项渲染成 choices 环境。"""

    out = dict(payload or {})
    items = []
    for q in out.get("items") or []:
        options = q.get("options") or []
        qtype = import_prompts.map_kind_to_question_type(
            str(q.get("question_type") or ""),
            mode,
            has_options=bool(options),
        )
        stem = str(q.get("stem") or "").strip()
        choices_block = import_prompts.render_choices(options)
        if choices_block:
            stem = stem + "\n\n" + choices_block
        new_q = dict(q)
        new_q["question_type"] = qtype
        new_q["stem"] = stem
        items.append(new_q)
    out["items"] = items
    return out


def _error(message: str, status: int = 400):
    return JSONResponse(content={"status": "error", "message": message}, status_code=status)


# ---------------------------------------------------------------------------
# 1. session
# ---------------------------------------------------------------------------


@router.post("/api/import/batch/session")
async def create_batch_session(
    mode: str = Form("exam"),
    title: str = Form(""),
    files: list[UploadFile] = File(...),
):
    if mode not in import_prompts.VALID_MODES:
        return _error(f"未知录入模式: {mode}")
    if not files:
        return _error("没有上传任何文件")

    # 先把每个上传读进内存并做容量校验，再建一次会话——避免重复建会话
    # 在 data/import_batches/ 下留下已废弃的孤儿状态文件。
    uploads = []
    content_by_name = {}
    for upload in files:
        name = upload.filename or "upload.pdf"
        content = await upload.read()
        if len(content) > import_batch.MAX_FILE_BYTES:
            return _error(f"源文件 {name} 超过 300MB 上限")
        uploads.append((name, content))
        content_by_name[name] = content

    metas = [
        {"name": name, "size": len(content), "pages": 1}
        for name, content in uploads
    ]
    try:
        state = import_batch.create_session(mode, metas)
    except ValueError as exc:
        return _error(str(exc))

    session_dir = _session_dir(state["session_id"])
    os.makedirs(session_dir, exist_ok=True)

    # 落盘 PDF，并回填真实页数（建会话时还没读盘，先给 1）。
    for f in state["files"]:
        content = content_by_name.get(f["name"])
        if content is None:
            continue
        target = os.path.join(session_dir, f["name"])
        with open(target, "wb") as fh:
            fh.write(content)
        try:
            f["pages"] = _count_pages(target)
        except Exception:
            f["pages"] = 1

    state["title"] = title
    import_batch._save(state)
    return {"status": "success", "session": state}


# ---------------------------------------------------------------------------
# 2. plan
# ---------------------------------------------------------------------------


@router.post("/api/import/batch/plan")
def build_batch_plan(payload: dict):
    session_id = str((payload or {}).get("session_id") or "").strip()
    if not session_id:
        return _error("缺少 session_id")
    try:
        state = import_batch._load(session_id)
    except KeyError as exc:
        return _error(str(exc), 404)

    # 教材模式：用 PDF 文本层启发式找出目录页，识别阶段改用目录提示词。
    toc_pages = None
    if state.get("mode") == "textbook":
        session_dir = _session_dir(session_id)
        toc_pages = {}
        for f in state["files"]:
            path = os.path.join(session_dir, f["name"])
            if not os.path.exists(path):
                continue
            hits = _find_toc_pages(path, max(1, int(f.get("pages") or 1)))
            if hits:
                toc_pages[f["id"]] = hits

    try:
        state = import_batch.build_plan(session_id, toc_pages=toc_pages)
    except KeyError as exc:
        return _error(str(exc), 404)
    return {"status": "success", "session": state}


# ---------------------------------------------------------------------------
# 3. recognize
# ---------------------------------------------------------------------------


@router.post("/api/import/batch/recognize")
def recognize_batch_items(payload: dict):
    body = payload or {}
    session_id = str(body.get("session_id") or "").strip()
    only_item = str(body.get("item_id") or "").strip()
    if not session_id:
        return _error("缺少 session_id")
    try:
        state = import_batch._load(session_id)
    except KeyError as exc:
        return _error(str(exc), 404)

    task_id = str(uuid.uuid4())
    _main.DOCUMENT_TASKS.create(
        task_id,
        status="pending",
        log="已排队，准备逐页视觉识别…",
        document_type="import_batch",
        session_id=session_id,
        temp_assets=[],
    )
    try:
        _main.DOCUMENT_TASKS.submit(
            task_id, _run_recognize, session_id, task_id, only_item
        )
    except TaskQueueFull as exc:
        _main.DOCUMENT_TASKS.remove(task_id)
        return _error(str(exc), 429)

    return {"status": "success", "task_id": task_id}


def _run_recognize(session_id: str, task_id: str, only_item: str) -> None:
    """后台逐页视觉识别：进度与取消走 DOCUMENT_TASKS，前端轮询状态后取详情。"""

    try:
        state = import_batch._load(session_id)
    except KeyError:
        _main.DOCUMENT_TASKS.fail(task_id, "录入会话状态丢失")
        return

    session_dir = _session_dir(session_id)
    targets = [
        it for it in (state.get("items") or [])
        if (not only_item) or it["id"] == only_item
    ]
    total = len(targets)
    if total == 0:
        _main.DOCUMENT_TASKS.complete(task_id, message="没有待识别的页")
        return

    done = 0
    failures = []
    _main.DOCUMENT_TASKS.update(
        task_id, status="processing", progress=0, log=f"开始识别 0/{total} 页"
    )
    for item in targets:
        _main.DOCUMENT_TASKS.check_cancelled(task_id)
        if item.get("status") == "recognized" and not only_item:
            done += 1
            continue
        file_meta = next(
            (f for f in state["files"] if f["id"] == item["file_id"]), None
        )
        if not file_meta:
            failures.append({"item_id": item["id"], "error": "找不到源文件"})
            done += 1
            continue
        path = os.path.join(session_dir, file_meta["name"])
        if not os.path.exists(path):
            failures.append({"item_id": item["id"], "error": "源文件已丢失"})
            done += 1
            continue
        try:
            png = _render_page(path, max(0, int(item["page"]) - 1))
            if item.get("is_toc"):
                raw = _vision_recognize_toc(
                    png,
                    page_no=item["page"],
                    total_pages=file_meta.get("pages") or total,
                )
                toc = raw.get("chapters") if isinstance(raw, dict) else None
                if not isinstance(toc, list):
                    toc = []
                import_batch.set_toc_result(session_id, item["id"], toc)
            else:
                raw = _vision_recognize(
                    state.get("mode") or "exam",
                    png,
                    page_no=item["page"],
                    total_pages=file_meta.get("pages") or total,
                )
                if isinstance(raw, dict) and "items" not in raw:
                    raw = {"items": [raw] if raw else []}
                raw = _normalize_items(
                    raw if isinstance(raw, dict) else {"items": []},
                    state.get("mode") or "exam",
                )
                raw["source"] = state.get("title") or ""
                import_batch.record_recognized(session_id, item["id"], raw)
            done += 1
        except Exception as exc:  # noqa: BLE001 - 单页失败不中断整批
            failures.append({"item_id": item["id"], "error": str(exc)[:200]})

        _main.DOCUMENT_TASKS.update(
            task_id,
            progress=int(done / total * 100),
            log=f"已识别 {done}/{total} 页",
        )

    try:
        state = import_batch._load(session_id)
        # 识别完成后自动评估风险，供前端「风险确认」步骤展示
        try:
            import_batch.evaluate_risks(session_id)
        except Exception:
            pass
        # 教材模式：合并目录、推导章/节、跨页缝合、生成目录树与题列表
        try:
            import_batch.build_catalog_tree(session_id)
        except Exception:
            pass
        state = import_batch._load(session_id)
        # 重算 previewHash 为"识别完成态"快照，使提交时防陈旧校验能与最新结果一致
        state["preview_hash"] = import_batch.compute_preview_hash(state)
        state["recognize_failures"] = failures
        import_batch._save(state)
    except KeyError:
        pass
    if failures:
        _main.DOCUMENT_TASKS.update(
            task_id, log=f"识别完成，{len(failures)} 页识别失败"
        )
    _main.DOCUMENT_TASKS.complete(task_id, failures=failures)


# ---------------------------------------------------------------------------
# 4. risk
# ---------------------------------------------------------------------------


@router.get("/api/import/batch/risk")
def list_batch_risks(session_id: str = ""):
    try:
        state = import_batch._load(session_id)
    except KeyError as exc:
        return _error(str(exc), 404)
    return {
        "status": "success",
        "risks": state.get("risks") or [],
        "revision": state.get("revision"),
    }


@router.post("/api/import/batch/risk")
def accept_batch_risks(payload: dict):
    body = payload or {}
    session_id = str(body.get("session_id") or "").strip()
    acceptances = body.get("acceptances")
    if not session_id:
        return _error("缺少 session_id")
    expected = body.get("expected_revision")
    try:
        state = import_batch.accept_risks(
            session_id,
            acceptances if isinstance(acceptances, list) else [],
            expected_revision=int(expected) if expected is not None else None,
        )
    except (ValueError, KeyError) as exc:
        return _error(str(exc))
    return {"status": "success", "session": state}


# ---------------------------------------------------------------------------
# 5. commit / 6. rollback
# ---------------------------------------------------------------------------


@router.post("/api/import/batch/commit")
def commit_batch(payload: dict, db: Session = Depends(get_db)):
    body = payload or {}
    session_id = str(body.get("session_id") or "").strip()
    preview_hash = str(body.get("preview_hash") or "")
    if not session_id:
        return _error("缺少 session_id")
    try:
        result = import_batch.commit_session(session_id, preview_hash, db)
    except (ValueError, KeyError) as exc:
        return _error(str(exc))
    return {"status": "success", **result}


@router.post("/api/import/batch/rollback")
def rollback_batch(payload: dict, db: Session = Depends(get_db)):
    body = payload or {}
    session_id = str(body.get("session_id") or "").strip()
    rollback_hash = str(body.get("rollback_hash") or "")
    if not session_id:
        return _error("缺少 session_id")
    try:
        result = import_batch.rollback_session(session_id, rollback_hash, db)
    except (ValueError, KeyError) as exc:
        return _error(str(exc))
    return {"status": "success", **result}


# ---------------------------------------------------------------------------
# 详情 / 列表
# ---------------------------------------------------------------------------


@router.get("/api/import/batch/detail")
def batch_detail(session_id: str = ""):
    try:
        state = import_batch._load(session_id)
    except KeyError as exc:
        return _error(str(exc), 404)
    return {"status": "success", "session": state}


@router.get("/api/import/batch/list")
def batch_list():
    from mathbank.import_batch import BATCH_DIR

    if not os.path.isdir(BATCH_DIR):
        return {"status": "success", "sessions": []}
    rows = []
    for name in sorted(os.listdir(BATCH_DIR)):
        if not name.endswith(".json"):
            continue
        try:
            rows.append(import_batch._load(name[:-5]))
        except Exception:
            continue
    rows.sort(key=lambda s: s.get("created_at") or "", reverse=True)
    return {"status": "success", "sessions": rows}
