import os
import hashlib


import atexit


import io
import zipfile


import sys


import uuid


import json


import time


import re


import signal


import datetime


import threading


import requests


from contextlib import asynccontextmanager


from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


import secrets


from typing import List, Optional


from PIL import Image


from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, Request, Response, Header


from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, StreamingResponse


from fastapi.staticfiles import StaticFiles


from fastapi.middleware.cors import CORSMiddleware


from sqlalchemy.orm import Session


from dotenv import load_dotenv


from mathbank.database import (
    Question,
    QuestionCurriculum,
    Paper,
    PaperQuestion,
    engine,
    get_db,
    init_db,
)


from mathbank.question_duplicate_service import (
    rebuild_all_missing_fingerprints,
)


from mathbank.paper_helper import build_latex_document, build_answer_sheet_latex, compile_tex_to_pdf, create_tex_zip_package, create_full_bundle_zip_package, collect_referenced_images, build_restricted_tex_environment


from mathbank.word_export_helper import build_word_document, create_word_bundle_zip


from mathbank.backup import acquire_runtime_lock, create_full_backup_if_due


from mathbank.health import readiness_report


from mathbank.auth import LOCAL_TOKEN


from mathbank.routes_lecture import router as lecture_router
from mathbank.routes_lesson_plans import router as lesson_plans_router


from mathbank.routes_library import router as library_router


from mathbank.routes_runtime import router as runtime_router


from mathbank.metadata import (
    _decorate_topic_field,
    get_metadata_cache,
    load_or_init_metadata,
    schedule_database_export,
)


from mathbank.task_manager import (
    TaskCancelled,
    TaskManager,
    TaskQueueFull,
)


from mathbank.docx_helper import extract_docx_markdown


from mathbank.content_locks import (
    ContentLockIntegrityError,
    lock_visible_math,
    restore_visible_math,
)


from mathbank.tex_helper import (
    MAX_TEX_BYTES,
    decode_and_prepare_tex,
    prepare_tex_source,
    tex_asset_basename,
    tex_asset_references_match,
)


from mathbank.latex_diagnostics import (
    build_local_latex_diagnostic,
    merge_ai_latex_diagnostic,
)

from mathbank import math_markdown as _math_markdown_mod


from mathbank.ai_json import parse_ai_json


from mathbank.ai_http import (
    post_chat_completion,
)


from mathbank.ai_providers import (
    MultimodalProviderConfig,
    apply_bailian_thinking_policy,
    apply_deepseek_thinking_policy,
    inject_reasoning_effort,
    is_deepseek_thinking_capable,
    resolve_draw_provider,
    resolve_ocr_fallbacks,
    resolve_ocr_provider,
    resolve_text_provider,
)


from mathbank.curriculums import (
    get_current_curriculum,
    load_curriculum,
)


from mathbank.prompts import (
    COMMON_OCR_PROMPT,
    ILLUSTRATION_BOX_PROMPT,
    PDF_PAGE_ILLUSTRATION_BOX_PROMPT,
    build_ai_solve_prompts,
    build_classification_system_prompt,
    build_import_parse_system_prompt,
    build_latex_error_explanation_prompts,
    build_paper_selection_prompts,
    build_pdf_parse_system_prompt,
    build_tikz_correction_prompt,
    build_tikz_draw_prompt,
)


from mathbank.question_types import (
    detect_structured_question_form,
    normalize_ai_question_form,
)


import shutil


from mathbank.pdf_inspector_helper import (
    is_pdf_inspector_available,
    inspect_and_extract_pdf,
    merge_pdf_page_texts,
)


from mathbank.paths import (
    DATABASE_PATH,
    ENV_FILE,
    IS_TESTING,
    PROJECT_ROOT,
    STATIC_CSS_DIR,
    STATIC_DIR,
    STATIC_JS_DIR,
    TEST_UPLOADS_DIR,
    UPLOADS_DIR,
    SERVER_INSTANCE_ID,
)


from mathbank.routes_settings import router as settings_router


from mathbank.asset_security import (
    AssetSecurityError,
    InvalidImageError,
    MAX_OCR_IMAGE_BYTES,
    MAX_PDF_BYTES,
    MAX_SINGLE_IMAGE_BYTES,
    UploadTooLargeError,
    harden_private_path,
    normalize_raster_image,
    normalize_upload_asset_reference,
    read_stream_limited,
    resolve_upload_asset,
)


load_dotenv(ENV_FILE)


harden_private_path(ENV_FILE)


_SHUTDOWN_SCHEDULED = threading.Event()


_SHUTDOWN_SCHEDULE_LOCK = threading.Lock()


_RUNTIME_LOCK = None if IS_TESTING else acquire_runtime_lock()


if _RUNTIME_LOCK is not None:
    atexit.register(_RUNTIME_LOCK.close)


init_db()


def print_startup_diagnostics():
    """打印不扫描 PATH、不阻塞就绪的基础启动诊断。"""
    is_venv = sys.prefix != sys.base_prefix
    env_type = f"虚拟环境 ({os.path.basename(sys.prefix)})" if is_venv else "全局/系统环境"
    pdf_insp_ok = is_pdf_inspector_available()

    print("=" * 64, flush=True)
    print("      本地数学题库教研系统 (MathBank) 启动自检与诊断面板", flush=True)
    print("=" * 64, flush=True)
    print(f"  - Python 运行环境   : {sys.version.split()[0]} [{env_type}]", flush=True)
    print(f"  - Python 可执行路径 : {sys.executable}", flush=True)
    if is_venv:
        print(f"  - 虚拟环境根目录   : {sys.prefix}", flush=True)
    print(f"  - PDF Inspector 引擎: {'[OK] 已就绪 (原生矢量试卷毫秒级直提)' if pdf_insp_ok else '[!] 未安装 (已自动平滑降级至 VLM 多模态 OCR)'}", flush=True)
    print("  - 可选排版工具   : 服务就绪后后台检测", flush=True)
    print(f"  - SQLite 本地数据库 : {DATABASE_PATH}", flush=True)
    print(f"  - 项目静态与根路径 : {PROJECT_ROOT}", flush=True)
    print("=" * 64, flush=True)


print_startup_diagnostics()


def print_optional_tool_diagnostics():
    """服务就绪后再扫描可选工具，避免慢 PATH 阻断启动。"""

    latex_engine = shutil.which("xelatex") or shutil.which("pdflatex")
    # Pandoc 必须用与导出/Word 公式能力**相同的解析器**：受托管的 pandoc 装在
    # .system_generated/runtime/pandoc 下、并不在 PATH 上。只看 PATH/env 会误报
    # "missing"，让人以为依赖缺失（其实可用）而白装一遍。
    try:
        from mathbank.runtime_components import pandoc_status

        pandoc_info = pandoc_status()
    except Exception:  # pragma: no cover - 诊断本身不能拖垮启动
        pandoc_info = {}
    if pandoc_info.get("available"):
        pandoc_label = f"{pandoc_info.get('path')} [{pandoc_info.get('source')}]"
    else:
        pandoc_label = "missing"
    try:
        import pymupdf  # noqa: F401

        pymupdf_status = "ready"
    except ImportError:
        pymupdf_status = "missing"
    print(
        "[Optional Tools] "
        f"latex={latex_engine or 'missing'}, "
        f"pandoc={pandoc_label}, "
        f"pymupdf={pymupdf_status}",
        flush=True,
    )


def heal_database_curriculum_names():
    from mathbank.database import SessionLocal
    db = SessionLocal()
    try:
        mappings = {
            "选择性必修一": "选修一",
            "选择性必修二": "选修二",
            "选择性必修三": "选修三",
            "必修第一册": "必修一",
            "必修第二册": "必修二",
            "必修第三册": "必修三",
            "必修第四册": "必修四",
        }
        updated_questions = 0
        for old, new in mappings.items():
            res = db.query(Question).filter(Question.category_compulsory == old).update(
                {Question.category_compulsory: new}, synchronize_session=False
            )
            updated_questions += res
            
        updated_mappings = 0
        for old, new in mappings.items():
            res = db.query(QuestionCurriculum).filter(QuestionCurriculum.compulsory == old).update(
                {QuestionCurriculum.compulsory: new}, synchronize_session=False
            )
            updated_mappings += res

        # 清理在主表 questions 及镜像表 question_curriculums 中残留的不属于各自大纲小节列表的旧章名/错位知识点
        curr = get_current_curriculum()
        healed_know_count = 0
        all_qs = db.query(Question).all()
        for q in all_qs:
            comp = q.category_compulsory
            chap = q.category_chapter
            know = q.category_knowledge
            if know:
                valid_knows = curr.get(comp, {}).get(chap, [])
                valid_know_names = {s.get("name") if isinstance(s, dict) else s for s in valid_knows}
                if know not in valid_know_names:
                    q.category_knowledge = ""
                    healed_know_count += 1

        all_qcs = db.query(QuestionCurriculum).all()
        for qc in all_qcs:
            if qc.knowledge:
                try:
                    c_tree = load_curriculum(qc.version_code)
                except ValueError:
                    c_tree = curr
                valid_knows = c_tree.get(qc.compulsory, {}).get(qc.chapter, [])
                valid_know_names = {s.get("name") if isinstance(s, dict) else s for s in valid_knows}
                if qc.knowledge not in valid_know_names:
                    qc.knowledge = ""
                    healed_know_count += 1
            
        if updated_questions > 0 or updated_mappings > 0 or healed_know_count > 0:
            db.commit()
            print(f"[Self-Healing DB] Migrated {updated_questions} questions, {updated_mappings} mappings, and cleaned {healed_know_count} mismatched knowledge values.")
    except Exception as e:
        db.rollback()
        print(f"[Self-Healing DB Error] Failed to run database book names migration: {e}")
    finally:
        db.close()


UPLOAD_DIR_REL = "static/test_uploads" if IS_TESTING else "static/uploads"


UPLOAD_DIR = str(TEST_UPLOADS_DIR if IS_TESTING else UPLOADS_DIR)


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    """在模块完整导入后再启动低优先级维护任务。"""

    if IS_TESTING:
        yield
        return

    DOCUMENT_TASKS.start_maintenance(interval_seconds=60.0)
    threading.Thread(
        target=start_startup_cleanup,
        name="mathbank-post-startup-maintenance",
        daemon=True,
    ).start()
    try:
        yield
    finally:
        DOCUMENT_TASKS.shutdown(wait=False)


app = FastAPI(title="本地化数学题库管理系统 API", lifespan=app_lifespan)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:27182",
        "http://localhost:27182",
        "http://127.0.0.1",
        "http://localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


LAST_ACTIVE_TIME = time.time()


@app.middleware("http")
async def security_and_heartbeat_middleware(request: Request, call_next):
    global LAST_ACTIVE_TIME
    LAST_ACTIVE_TIME = time.time()
    
    # Verify local security token for modifying operations
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.url.path != "/api/heartbeat":
            token = request.headers.get("X-Local-Token")
            if not token or not secrets.compare_digest(token, LOCAL_TOKEN):
                print(f"[Security Alert] Blocked {request.method} {request.url.path} - invalid local token")
                return JSONResponse(
                    status_code=403,
                    content={"status": "error", "message": "Forbidden: Invalid or missing local token."}
                )
                
    response = await call_next(request)
    return response


@app.post("/api/heartbeat")
def api_heartbeat():
    global LAST_ACTIVE_TIME
    LAST_ACTIVE_TIME = time.time()
    return {"status": "success", "timestamp": LAST_ACTIVE_TIME}


def clean_orphaned_images():
    """扫描 static/uploads 目录及其 tmp 子目录，安全彻底擦除未被数据库引用的孤儿图片与旧残留临时图片"""
    try:
        from mathbank.database import SessionLocal, Question
        db = SessionLocal()
        try:
            # 1. 搜集数据库中所有题目引用的图片路径
            questions = db.query(Question._image_paths).all()
            referenced_images = set()
            for (img_paths_str,) in questions:
                if img_paths_str:
                    try:
                        paths = json.loads(img_paths_str)
                        for path in paths:
                            referenced_images.add(path.lstrip("/").lower())
                    except Exception:
                        pass

            # 1b. 仍被任务记录持有的临时资产同样算"被引用"。
            # 契约是「完成结果保留 1 小时供前端导入」，到期由任务记录自己删除
            # （task_manager._cleanup_temp_assets_locked）。启动清理若不认它们，
            # 就会把用户刚拆完、还没保存入库的插图提前抹掉（"我的图怎么又没了"）。
            try:
                for path in DOCUMENT_TASKS.live_temp_assets():
                    referenced_images.add(path.lstrip("/").lower())
            except Exception:
                pass
                        
            # 2. 遍历本地图片目录及 tmp 子目录
            upload_dir = UPLOAD_DIR
            if not os.path.exists(upload_dir):
                return
                
            cleaned_count = 0
            now = time.time()
            one_hour_seconds = 3600
            
            # 清理 static/uploads/ 根目录下未引用的孤儿图片
            for filename in os.listdir(upload_dir):
                full_path = os.path.join(upload_dir, filename)
                if os.path.isfile(full_path) and not filename.startswith("."):
                    local_rel_path = f"{UPLOAD_DIR_REL}/{filename}".lower()
                    if local_rel_path not in referenced_images:
                        try:
                            mtime = os.path.getmtime(full_path)
                            if now - mtime > one_hour_seconds:
                                os.remove(full_path)
                                cleaned_count += 1
                        except Exception:
                            pass

            # 清理 static/uploads/tmp/ 子目录下残留的所有旧拆卷/OCR临时切片图
            tmp_dir = os.path.join(upload_dir, "tmp")
            if os.path.exists(tmp_dir):
                for filename in os.listdir(tmp_dir):
                    full_path = os.path.join(tmp_dir, filename)
                    if os.path.isfile(full_path) and not filename.startswith("."):
                        local_rel_path = f"{UPLOAD_DIR_REL}/tmp/{filename}".lower()
                        if local_rel_path not in referenced_images:
                            try:
                                mtime = os.path.getmtime(full_path)
                                if now - mtime > 600:  # 超过 10 分钟未被使用的 tmp 切片立即清理
                                    os.remove(full_path)
                                    cleaned_count += 1
                            except Exception:
                                pass
                        
            if cleaned_count > 0:
                print(f"[Storage Cleanup] 成功检测并清除 {cleaned_count} 个残留的旧临时图片与孤儿文件，磁盘无痕瘦身成功！")
        finally:
            db.close()
    except Exception as e:
        print(f"[Storage Cleanup Error] 执行静默图片净化时发生异常: {str(e)}")


def recalibrate_usage_counts():
    """自动校准全库题目的引用频次 usage_count，修正由于历史删除试卷遗留的计数差异"""
    try:
        from mathbank.database import SessionLocal, Question, PaperQuestion
        from sqlalchemy import func
        db = SessionLocal()
        try:
            counts = db.query(PaperQuestion.question_id, func.count(PaperQuestion.id)).group_by(PaperQuestion.question_id).all()
            ref_map = dict(counts)
            questions = db.query(Question).all()
            changed = False
            for q in questions:
                actual_ref = ref_map.get(q.id, 0)
                if (q.usage_count or 0) != actual_ref:
                    q.usage_count = actual_ref
                    changed = True
            if changed:
                db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[Usage Calibration Error] {e}")


def start_startup_cleanup():
    # Lifespan 已确保模块完整导入；再让出短暂时间给首屏请求。
    time.sleep(2.5)
    try:
        backup_path = create_full_backup_if_due()
        if backup_path:
            print(f"[Backup] 已创建并验证每日完整备份: {backup_path.name}")
    except Exception as exc:
        print(f"[Backup Error] 每日完整备份失败: {type(exc).__name__}: {exc}")
        print_optional_tool_diagnostics()
        return
    heal_database_curriculum_names()
    clean_orphaned_images()
    recalibrate_usage_counts()
    try:
        from mathbank.database import SessionLocal

        fingerprint_result = rebuild_all_missing_fingerprints(
            SessionLocal,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
            batch_size=250,
        )
        if fingerprint_result.get("backfilled"):
            print(
                "[Duplicate Index] Backfill complete: "
                f"{fingerprint_result['indexed']}/{fingerprint_result['total']}"
            )
    except Exception as exc:
        print(
            "[Duplicate Index] Background backfill failed "
            f"(type={type(exc).__name__}); duplicate checks will report partial coverage."
        )
    print_optional_tool_diagnostics()


os.makedirs(UPLOAD_DIR, exist_ok=True)


TMP_UPLOAD_DIR = os.path.join(UPLOAD_DIR, "tmp")


os.makedirs(TMP_UPLOAD_DIR, exist_ok=True)


DOCUMENT_TASKS = TaskManager(
    max_workers=2,
    max_queue=4,
    terminal_ttl_seconds=3600,
    temp_asset_cleanup=lambda paths: _delete_task_temp_assets(paths),
)


PDF_OCR_SEMAPHORE = threading.BoundedSemaphore(4)


MAX_PDF_TASK_PAGES = 80


def get_seq_mapping(db: Session, question_ids=None):
    """Map physical ID order to the user-facing contiguous sequence number."""

    if question_ids is None:
        all_q = db.query(Question.id).order_by(Question.id.asc()).all()
        return {q_id: idx + 1 for idx, (q_id,) in enumerate(all_q)}

    normalized_ids = {int(question_id) for question_id in question_ids}
    if not normalized_ids:
        return {}
    from sqlalchemy import func

    ranked = db.query(
        Question.id.label("question_id"),
        func.row_number().over(order_by=Question.id.asc()).label("seq_num"),
    ).subquery()
    rows = db.query(ranked.c.question_id, ranked.c.seq_num).filter(
        ranked.c.question_id.in_(normalized_ids)
    ).all()
    return {question_id: int(seq_num) for question_id, seq_num in rows}


@app.get("/healthz", include_in_schema=False)
def healthz():
    report = readiness_report(engine)
    report["server_instance_id"] = SERVER_INSTANCE_ID
    return JSONResponse(report, status_code=200 if report["ready"] else 503)


@app.get("/")
def read_index():
    index_path = str(STATIC_DIR / "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        
        # Inject dynamic cache-busting version parameter based on file mtime
        import re as _re
        js_files = ["api.js", "editor.js", "ocr.js", "import.js", "paper.js", "lesson.js", "lecture.js", "filter-panel.js"]
        for js in js_files:
            js_path = str(STATIC_JS_DIR / js)
            if not os.path.exists(js_path):
                continue
            mtime = int(os.path.getmtime(js_path))
            # Replace ANY existing ?v=xxx (不管写死的是 1.0.1 / 1.0.4 / 别的)
            html_content = _re.sub(
                r'(/static/js/' + _re.escape(js) + r')\?v=[^"\s]*',
                lambda m: m.group(1) + f"?v={mtime}",
                html_content,
            )
            # 没有 ?v= 的纯引用也补上
            html_content = html_content.replace(
                f'src="/static/js/{js}"',
                f'src="/static/js/{js}?v={mtime}"',
            )
            
        # Inject dynamic cache-busting version parameter for app.css and favicon assets
        css_path = str(STATIC_CSS_DIR / "app.css")
        css_mtime = int(os.path.getmtime(css_path)) if os.path.exists(css_path) else 0
        html_content = html_content.replace('/static/css/app.css', f'/static/css/app.css?v={css_mtime}')

        fav_path = str(STATIC_DIR / "favicon.png")
        fav_mtime = int(os.path.getmtime(fav_path)) if os.path.exists(fav_path) else 0
        html_content = html_content.replace('/static/favicon.png', f'/static/favicon.png?v={fav_mtime}')
            
        # Inject the token and server_instance_id directly into index.html to bypass any cookie blocking policies
        token_script = f'<script>window.__localToken = "{LOCAL_TOKEN}"; window.__serverInstanceId = "{SERVER_INSTANCE_ID}";</script>'
        html_content = html_content.replace('<head>', f'<head>\n    {token_script}')
            
        res = HTMLResponse(content=html_content)
        res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        res.headers["Pragma"] = "no-cache"
        res.headers["Expires"] = "0"
        
        res.set_cookie(
            key="local_token",
            value=LOCAL_TOKEN,
            httponly=False,  # JavaScript must be able to read this cookie to send it back via headers
            samesite="lax",
            secure=False
        )
        return res
    return JSONResponse(
        content={"status": "error", "message": "static/index.html not found. Please create it."},
        status_code=404
    )


@app.get("/favicon.ico", include_in_schema=False)
def read_favicon():
    favicon_path = str(STATIC_DIR / "favicon.ico")
    if os.path.exists(favicon_path):
        res = FileResponse(favicon_path, media_type="image/x-icon")
        res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        res.headers["Pragma"] = "no-cache"
        res.headers["Expires"] = "0"
        return res
    favicon_png_path = str(STATIC_DIR / "favicon.png")
    if os.path.exists(favicon_png_path):
        res = FileResponse(favicon_png_path, media_type="image/png")
        res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        res.headers["Pragma"] = "no-cache"
        res.headers["Expires"] = "0"
        return res
    return JSONResponse(
        content={"status": "error", "message": "favicon not found."},
        status_code=404
    )


@app.get("/favicon.svg", include_in_schema=False)
def read_favicon_svg():
    svg_path = str(STATIC_DIR / "favicon.svg")
    if os.path.exists(svg_path):
        res = FileResponse(svg_path, media_type="image/svg+xml")
        res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        res.headers["Pragma"] = "no-cache"
        res.headers["Expires"] = "0"
        return res
    return JSONResponse(
        content={"status": "error", "message": "favicon.svg not found."},
        status_code=404
    )


@app.get("/apple-touch-icon.png", include_in_schema=False)
@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
def read_apple_touch_icon():
    for name in ["apple-touch-icon.png", "favicon.png"]:
        icon_path = str(STATIC_DIR / name)
        if os.path.exists(icon_path):
            res = FileResponse(icon_path, media_type="image/png")
            res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            res.headers["Pragma"] = "no-cache"
            res.headers["Expires"] = "0"
            return res
    return JSONResponse(
        content={"status": "error", "message": "apple touch icon not found."},
        status_code=404
    )


@app.post("/api/upload")
def upload_image(file: UploadFile = File(...)):
    try:
        raw = read_stream_limited(file.file, MAX_SINGLE_IMAGE_BYTES)
        normalized = normalize_raster_image(raw)

        # Never trust the client suffix.  The server-generated extension and
        # re-encoded bytes prevent HTML/SVG/polyglot files being served same-origin.
        # Content-addressing: the filename is the sha256 of the re-encoded bytes,
        # so byte-identical uploads share one file (dedup).  Physical cleanup only
        # unlinks a file when no remaining question references it, so sharing is safe.
        sha256 = hashlib.sha256(normalized.data).hexdigest()
        filename = f"{sha256[:32]}{normalized.extension}"
        filepath = os.path.join(UPLOAD_DIR, filename)

        if not os.path.exists(filepath):
            with open(filepath, "wb") as f:
                f.write(normalized.data)

        relative_path = f"/{UPLOAD_DIR_REL}/{filename}"
        return {
            "status": "success",
            "file_path": relative_path,
            "filename": file.filename,
            "sha256": sha256
        }
    except UploadTooLargeError:
        return JSONResponse(
            content={"status": "error", "message": "图片过大，请上传 10MB 以内的文件。"},
            status_code=413,
        )
    except InvalidImageError as e:
        return JSONResponse(
            content={"status": "error", "message": f"图片上传失败: {str(e)}"},
            status_code=400,
        )
    except Exception as e:
        print(f"[Upload Error] {type(e).__name__}: {e}")
        return JSONResponse(
            content={"status": "error", "message": "文件上传失败，请检查文件后重试。"},
            status_code=500
        )


def normalize_fillin_macro(text: str) -> str:
    """薄包装：实现已归位到 `mathbank.math_markdown`（入库归一化链的唯一来源）。

    保留本名是为了 `scripts/migrate_fillin.py` 与既有 `from main import ...`
    接缝继续生效；新代码请直接使用 `mathbank.latex_normalize.normalize_for_storage`。
    """
    return _math_markdown_mod.normalize_fillin_macro(text)



load_or_init_metadata()

# 考向树 TXT 是底层文件：启动时先以 TXT 为准对齐一次活跃大纲。
try:
    from mathbank.kaoxiang_tree import auto_pull_if_needed

    _kaoxiang_report = auto_pull_if_needed(force=True)
    if _kaoxiang_report.get("changed"):
        print(
            "[KaoxiangTree] 已从考向树 TXT 同步 "
            f"{len(_kaoxiang_report.get('files', []))} 个章节"
        )
except Exception as exc:  # 同步失败不能阻断启动
    print(f"[KaoxiangTree] 启动同步失败 (type={type(exc).__name__}): {exc}")


app.include_router(lesson_plans_router)
app.include_router(lecture_router)


app.include_router(settings_router)


app.include_router(library_router)


def parse_paper_text_internal(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.parsing_pipeline``."""
    return parsing_pipeline.parse_paper_text_internal(*args, **kwargs)


@app.post("/api/shutdown")
def shutdown_server(
    x_mathbank_launch_id: str | None = Header(
        None, alias="X-MathBank-Launch-ID"
    ),
):
    if not x_mathbank_launch_id or not secrets.compare_digest(
        x_mathbank_launch_id, SERVER_INSTANCE_ID
    ):
        raise HTTPException(status_code=409, detail="Server instance changed")

    def stop_server():
        time.sleep(0.5)
        # Raising SIGINT inside this process lets uvicorn's installed handler
        # run its normal lifespan shutdown.  On Windows, os.kill(SIGINT) would
        # call TerminateProcess instead of delivering a cooperative console
        # control event.
        try:
            signal.raise_signal(signal.SIGINT)
        except Exception as exc:
            _SHUTDOWN_SCHEDULED.clear()
            print(f"[shutdown] Failed to raise cooperative SIGINT: {exc}")

    with _SHUTDOWN_SCHEDULE_LOCK:
        if _SHUTDOWN_SCHEDULED.is_set():
            return {
                "status": "already_stopping",
                "message": "题库系统已在关闭中...",
            }
        _SHUTDOWN_SCHEDULED.set()
        worker = threading.Thread(target=stop_server, daemon=True)
        try:
            worker.start()
        except Exception:
            _SHUTDOWN_SCHEDULED.clear()
            raise
    
    return {"status": "success", "message": "题库系统正在关闭中..."}


def post_process_pdf_parsed_questions(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.parsing_pipeline``."""
    return parsing_pipeline.post_process_pdf_parsed_questions(*args, **kwargs)


app.include_router(runtime_router)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---- Question domain routes extracted to mathbank.routes_questions ----
from mathbank.routes_questions import router as questions_router
app.include_router(questions_router)


# ---- AI 能力域路由下沉（2026-09-17，AST 源区间重建）----
from mathbank.routes_ai import router as ai_router  # noqa: E402
app.include_router(ai_router)

# ---- 试卷解析 / 组卷导出域路由下沉（2026-09-17）----
from mathbank.routes_paper import router as paper_router  # noqa: E402
app.include_router(paper_router)

# ---- 薄包装：实现已下沉，保留 main 同名接缝供既有 patch("main.xxx") / 直调使用 ----
from mathbank import routes_ai as routes_ai  # noqa: E402
from mathbank import routes_paper as routes_paper  # noqa: E402


def auto_crop_image(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_ai``."""
    return routes_ai.auto_crop_image(*args, **kwargs)


def ocr_via_provider(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_ai``."""
    return routes_ai.ocr_via_provider(*args, **kwargs)


def extract_tikz_source(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_ai``."""
    return routes_ai.extract_tikz_source(*args, **kwargs)


def request_tikz_completion(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_ai``."""
    return routes_ai.request_tikz_completion(*args, **kwargs)


def draw_tikz_via_high_model(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_ai``."""
    return routes_ai.draw_tikz_via_high_model(*args, **kwargs)


def ocr_formula(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_ai``."""
    return routes_ai.ocr_formula(*args, **kwargs)


def ai_solve(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_ai``."""
    return routes_ai.ai_solve(*args, **kwargs)


def compile_tikz_to_png(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_ai``."""
    return routes_ai.compile_tikz_to_png(*args, **kwargs)


def render_tikz_endpoint(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_ai``."""
    return routes_ai.render_tikz_endpoint(*args, **kwargs)


def correct_tikz_endpoint(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_ai``."""
    return routes_ai.correct_tikz_endpoint(*args, **kwargs)


def draw_tikz_workbench_endpoint(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_ai``."""
    return routes_ai.draw_tikz_workbench_endpoint(*args, **kwargs)


def draw_tikz_from_image_endpoint(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_ai``."""
    return routes_ai.draw_tikz_from_image_endpoint(*args, **kwargs)


def ai_classify(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_ai``."""
    return routes_ai.ai_classify(*args, **kwargs)


def ocr_pdf_page_image(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_ai``."""
    return routes_ai.ocr_pdf_page_image(*args, **kwargs)


def process_ocr_illustrations(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_ai``."""
    return routes_ai.process_ocr_illustrations(*args, **kwargs)


def resolve_ocr_illustration_boxes(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_ai``."""
    return routes_ai.resolve_ocr_illustration_boxes(*args, **kwargs)


def find_source_page_by_overlap(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_ai``."""
    return routes_ai.find_source_page_by_overlap(*args, **kwargs)


def upload_tex_source(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.upload_tex_source(*args, **kwargs)


def upload_batch_images(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.upload_batch_images(*args, **kwargs)


def ai_parse_paper(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.ai_parse_paper(*args, **kwargs)


def manual_crop_pdf(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.manual_crop_pdf(*args, **kwargs)


def extract_title_from_latex(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.extract_title_from_latex(*args, **kwargs)


def ai_select_paper(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.ai_select_paper(*args, **kwargs)


def _resolve_task_paper_title(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper._resolve_task_paper_title(*args, **kwargs)


def run_mineru_pdf_parsing_task(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.run_mineru_pdf_parsing_task(*args, **kwargs)


def run_pdf_parsing_task(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.run_pdf_parsing_task(*args, **kwargs)


def parse_page_range(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.parse_page_range(*args, **kwargs)


def upload_pdf_task(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.upload_pdf_task(*args, **kwargs)


def _delete_task_temp_assets(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper._delete_task_temp_assets(*args, **kwargs)


def run_docx_parsing_task(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.run_docx_parsing_task(*args, **kwargs)


def upload_docx_task(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.upload_docx_task(*args, **kwargs)


def get_latest_document_task(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.get_latest_document_task(*args, **kwargs)


def get_pdf_task_status(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.get_pdf_task_status(*args, **kwargs)


def cancel_pdf_task(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.cancel_pdf_task(*args, **kwargs)


def clear_temp_crops(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.clear_temp_crops(*args, **kwargs)


def get_paper_questions(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.get_paper_questions(*args, **kwargs)


def save_paper(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.save_paper(*args, **kwargs)


def list_papers(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.list_papers(*args, **kwargs)


def get_paper_detail(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.get_paper_detail(*args, **kwargs)


def delete_paper(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.delete_paper(*args, **kwargs)


def _build_paper_questions_data(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper._build_paper_questions_data(*args, **kwargs)


def export_paper_tex(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.export_paper_tex(*args, **kwargs)


def export_paper_bundle(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.export_paper_bundle(*args, **kwargs)


def explain_latex_compile_error(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.explain_latex_compile_error(*args, **kwargs)


def export_paper_pdf(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.export_paper_pdf(*args, **kwargs)


def export_paper_word(*args, **kwargs):
    """Thin wrapper — implementation lives in ``mathbank.routes_paper``."""
    return routes_paper.export_paper_word(*args, **kwargs)

# ---- 解析流水线实现下沉（main 保留薄包装，供既有 patch("main.xxx") 接缝使用）----
from mathbank import parsing_pipeline  # noqa: E402

# ---- OCR 插图坐标裁图实现下沉（同上，main 保留同名薄包装）----
from mathbank import illustration_crop  # noqa: E402

# ---- 考向树 TXT 底层文件同步 ----
from mathbank.routes_kaoxiang import router as kaoxiang_router
app.include_router(kaoxiang_router)

# ---- 导入批次 / TikZ 版本 / 双向细目表 ----
from mathbank.routes_ops import router as ops_router
app.include_router(ops_router)
