# -*- coding: utf-8 -*-
"""试卷域路由：试卷解析任务（PDF/MinerU/docx/TeX/图片批量）、组卷选题、试卷 CRUD 与导出。

2026-09-17 自 main.py 下沉（AST 精确源区间重建）。main.py 保留同名薄包装，
供既有 patch("main.xxx") / from main import xxx 测试接缝使用。
凡测试会 patch 的依赖一律经 `import main as _main` 运行时查找，避免导入期冻结。
"""

from PIL import Image
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Response, UploadFile
from fastapi.responses import JSONResponse
from mathbank import illustration_crop
from mathbank.ai_json import parse_ai_json
from mathbank.ai_providers import apply_bailian_thinking_policy, apply_deepseek_thinking_policy, inject_reasoning_effort
from mathbank.asset_security import AssetSecurityError, InvalidImageError, UploadTooLargeError, normalize_raster_image, read_stream_limited, resolve_upload_asset
from mathbank.content_locks import ContentLockIntegrityError, lock_visible_math, restore_visible_math
from mathbank.database import Paper, PaperQuestion, Question, get_db
from mathbank.latex_diagnostics import build_local_latex_diagnostic, merge_ai_latex_diagnostic
from mathbank.metadata import _decorate_topic_field, get_metadata_cache, schedule_database_export
from mathbank.paper_helper import build_answer_sheet_latex, build_latex_document, collect_referenced_images, create_full_bundle_zip_package, create_tex_zip_package, restore_storage_images_to_markdown
from mathbank.pdf_inspector_helper import inspect_and_extract_pdf, merge_pdf_page_texts
from mathbank.prompts import build_import_parse_system_prompt, build_latex_error_explanation_prompts, build_paper_selection_prompts
from mathbank.task_manager import TaskCancelled, TaskQueueFull, format_duration
from mathbank.tex_helper import MAX_TEX_BYTES, decode_and_prepare_tex, prepare_tex_source, tex_asset_basename, tex_asset_references_match
from mathbank.word_export_helper import build_word_document, create_word_bundle_zip
from pathlib import Path
from sqlalchemy.orm import Session
from typing import List, Optional
import datetime
import hashlib
import json
import os
import re
import shutil
import time
import uuid

import main as _main  # noqa: E402  测试接缝：延迟绑定

router = APIRouter()


@router.post("/api/upload/tex-source")
def upload_tex_source(file: UploadFile = File(...)):
    """Decode and inspect a single TeX source file without executing it."""
    filename = file.filename or ""
    if not filename.lower().endswith(".tex"):
        return JSONResponse(
            content={"status": "error", "message": "上传文件格式不正确，必须为 .tex 格式！"},
            status_code=400,
        )
    try:
        content = read_stream_limited(file.file, MAX_TEX_BYTES)
        result = decode_and_prepare_tex(content)
        return {
            "status": "success",
            "source": result["source"],
            "title": result["title"],
            "diagnostics": result["diagnostics"],
        }
    except UploadTooLargeError:
        return JSONResponse(
            content={"status": "error", "message": "TeX 文件过大，请上传 5MB 以内的单文件试卷源码！"},
            status_code=413,
        )
    except ValueError as exc:
        return JSONResponse(content={"status": "error", "message": str(exc)}, status_code=400)


@router.post("/api/upload/batch")
def upload_batch_images(files: List[UploadFile] = File(...)):
    try:
        if not files or len(files) > 20:
            return JSONResponse(
                content={"status": "error", "message": "配图数量必须为 1 至 20 张。"},
                status_code=400,
            )
        validated = []
        total_bytes = 0
        seen_names: set[str] = set()
        for file in files:
            original_name = tex_asset_basename(file.filename or "image") or "image"
            normalized_name = original_name.casefold()
            if normalized_name in seen_names:
                raise ValueError(f"存在重名配图 {original_name}，请保留一张或先重命名。")
            seen_names.add(normalized_name)
            try:
                raw = read_stream_limited(file.file, _main.MAX_SINGLE_IMAGE_BYTES)
            except UploadTooLargeError as exc:
                raise ValueError(f"图片 {original_name} 超过 10MB。") from exc
            total_bytes += len(raw)
            if total_bytes > 50 * 1024 * 1024:
                raise ValueError("配图总大小不能超过 50MB。")
            try:
                normalized = normalize_raster_image(raw)
            except InvalidImageError as exc:
                raise ValueError(f"图片 {original_name} 不是安全的栅格图片。") from exc
            validated.append((original_name, normalized))

        mapping = {}
        for original_name, normalized in validated:
            # Content-addressed filename (sha256) so identical images dedup.
            sha256 = hashlib.sha256(normalized.data).hexdigest()
            filename = f"{sha256[:32]}{normalized.extension}"
            filepath = os.path.join(_main.UPLOAD_DIR, filename)
            if not os.path.exists(filepath):
                with open(filepath, "wb") as f:
                    f.write(normalized.data)
            relative_path = f"/{_main.UPLOAD_DIR_REL}/{filename}"
            mapping[original_name] = relative_path
            
        return {
            "status": "success",
            "mapping": mapping
        }
    except (ValueError, OSError, Image.DecompressionBombError) as e:
        return JSONResponse(
            content={"status": "error", "message": f"批量图片上传失败: {str(e)}"},
            status_code=400
        )


@router.post("/api/ai/parse-paper")
def ai_parse_paper(
    latex_content: str = Form(...),
    paper_title: str = Form(""),
    image_mapping_json: str = Form("{}"),
    generate_answers: str = Form("false")
):
    generate_answers_bool = generate_answers.lower() in ("true", "1", "yes")
    parse_model = os.getenv("PREFER_PARSE_MODEL") or os.getenv("DEEPSEEK_PARSE_MODEL", "deepseek-flash")
    provider = _main.resolve_text_provider(parse_model)
    api_key = provider.api_key
    api_base = provider.api_base
    model_name = provider.model_name
    provider_name = provider.provider_label

    if not api_key:
        return JSONResponse(
            content={
                "status": "error", 
                "message": f"未配置对应的 API Key ({provider.credential_label})，无法智能拆解试卷！请在工作台右上角设置面板进行配置。"
            },
            status_code=400
        )
        
    try:
        image_mapping = json.loads(image_mapping_json)
        if not isinstance(image_mapping, dict):
            image_mapping = {}
    except Exception:
        image_mapping = {}

    try:
        tex_result = prepare_tex_source(latex_content)
        tex_diagnostics = tex_result["diagnostics"]
        model_source, math_locks = lock_visible_math(
            tex_result["model_source"],
            "TEX_" + uuid.uuid4().hex[:16],
        )
        tex_diagnostics["math_locks_created"] = len(math_locks)
        if not paper_title.strip() and tex_result["title"]:
            paper_title = tex_result["title"]

        max_output_tokens = 65536

        def _request_parse(extra_user_content: str) -> list:
            system_instructions = build_import_parse_system_prompt(
                _main.get_current_curriculum(),
                difficulties=get_metadata_cache().get("difficulties"),
            )
            user_content = model_source
            if extra_user_content:
                user_content = user_content + "\n\n" + extra_user_content

            data = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_instructions},
                    {"role": "user", "content": user_content},
                ],
                "response_format": {
                    "type": "json_object"
                },
                "temperature": 0.2,
                "max_tokens": max_output_tokens,
            }

            data = inject_reasoning_effort(data, provider.reasoning_effort)
            # 与上面的解析分支保持同一个 DeepSeek 思考策略。
            data = apply_deepseek_thinking_policy(
                data,
                model_name=model_name,
                api_base=api_base,
                provider_code=provider.provider_code,
                task="parse",
                thinking_enabled=False,
            )
            data = apply_bailian_thinking_policy(
                data,
                provider_code=provider.provider_code,
                model_name=model_name,
                task="parse",
            )

            response = _main.post_chat_completion(
                provider,
                data,
                timeout=180,
                provider_name=provider_name,
            )

            res_json = response.json()
            raw_ai_text = res_json["choices"][0]["message"]["content"].strip()

            parsed_data = parse_ai_json(raw_ai_text, raw_markdown=model_source)

            if isinstance(parsed_data, dict):
                if "questions" in parsed_data and isinstance(parsed_data["questions"], list):
                    parsed_questions = parsed_data["questions"]
                elif "data" in parsed_data and isinstance(parsed_data["data"], list):
                    parsed_questions = parsed_data["data"]
                else:
                    parsed_questions = None
                    for key, val in parsed_data.items():
                        if isinstance(val, list):
                            parsed_questions = val
                            break
                    if parsed_questions is None:
                        parsed_questions = [parsed_data]
            elif isinstance(parsed_data, list):
                parsed_questions = parsed_data
            else:
                raise Exception("AI 返回的 JSON 格式不正确，期望是一个数组或包含 questions 列表的对象。")

            if not parsed_questions or not all(isinstance(question, dict) for question in parsed_questions):
                raise ValueError("AI 未返回有效的题目对象列表。")
            for index, question in enumerate(parsed_questions, start=1):
                if not isinstance(question.get("content"), str) or not question["content"].strip():
                    raise ValueError(f"AI 返回的第 {index} 题缺少有效题干，已停止导入以避免静默漏题。")
                if not isinstance(question.get("referenced_images"), list):
                    question["referenced_images"] = []

            lock_report = restore_visible_math(parsed_questions, math_locks)
            tex_diagnostics.update(lock_report)
            return parsed_questions

        try:
            parsed_questions = _request_parse("")
        except ContentLockIntegrityError as exc:
            if not exc.missing:
                raise
            missing_hint = (
                "【上次拆解修正指令】系统校验发现上一版拆解遗漏了以下公式定位标记（缺失 id："
                + "、".join(exc.missing[:40])
                + "）。请基于上述试卷源码重新输出完整的题目 JSON："
                "把每个遗漏的 [[对应完整 id]] 放回它所属题目的 content 或 answer_markdown 的正确语义位置，"
                "所有 `<mathbank-math>` 锁定 id 必须且只能出现一次，严禁遗漏、删除、改名或重复。"
            )
            parsed_questions = _request_parse(missing_hint)
        tex_diagnostics["question_count_actual"] = len(parsed_questions)
        estimated_count = tex_diagnostics.get("question_count_estimate", 0)
        if estimated_count and estimated_count != len(parsed_questions):
            tex_diagnostics.setdefault("warnings", []).append(
                f"源码约识别到 {estimated_count} 道题，但模型返回 {len(parsed_questions)} 道，请重点核对是否漏题或误拆。"
            )

        for graphic_ref in tex_diagnostics.get("referenced_graphics", []):
            graphic_ref = str(graphic_ref)
            candidates = []
            for question in parsed_questions:
                content_graphics = re.findall(
                    r"\\includegraphics(?:\s*\[[^\]]*\])?\s*\{([^}]+)\}",
                    question.get("content", ""),
                )
                question_refs = content_graphics + [
                    str(value) for value in question.get("referenced_images", [])
                ]
                if any(tex_asset_references_match(graphic_ref, value) for value in question_refs):
                    candidates.append(question)
            if len(candidates) == 1 and not any(
                tex_asset_references_match(graphic_ref, existing)
                for existing in candidates[0]["referenced_images"]
            ):
                candidates[0]["referenced_images"].append(graphic_ref)
            elif not candidates:
                tex_diagnostics.setdefault("unassigned_source_images", []).append(graphic_ref)
        
        # Translate referenced_images to server paths
        for q in parsed_questions:
            # 强制进行静默净化：若未勾选自动生成答案，则对于没有带有 [EXTRACTED_ORIGINAL] 的解析和解答，将其强行抹平为空。
            ans = q.get("answer_markdown", "")
            if not ans:
                q["answer_markdown"] = ""
            else:
                if not generate_answers_bool:
                    if "[EXTRACTED_ORIGINAL]" in ans:
                        q["answer_markdown"] = ans.replace("[EXTRACTED_ORIGINAL]", "").strip()
                    else:
                        q["answer_markdown"] = ""
                else:
                    q["answer_markdown"] = ans.replace("[EXTRACTED_ORIGINAL]", "").strip()

            # 归一化**不在这里做**：TeX/批量图片路线与 PDF/Word 路线共用同一个入库出口
            # （见函数末尾的 apply_storage_normalization）。历史上这里单独跑字符层与紧凑化，
            # 于是同一种写法在三条路径上进库变成三种源码。

            # 智能提取出处双重保险：AI 提取优先，若 AI 未提取则尝试正则从 content 中提取
            extracted_source = q.get("source")
            content_str = q.get("content", "")
            
            # 正则匹配题干开头形如 "10. (2019·全国·高考真题)已知..." 的出处
            # group(1): 题号前缀, group(2): 左括号, group(3): 出处内容, group(4): 右括号
            prefix_match = re.match(r'^(\s*(?:\d+[\.、\s]*)?)([\(（])([^\(（\)）\s]{4,})([\)）])', content_str)
            if prefix_match:
                if not extracted_source:
                    extracted_source = prefix_match.group(3).strip()
                # 剔除题干中的出处括号及前面的题号前缀，保持题干纯净
                to_remove = prefix_match.group(1) + prefix_match.group(2) + prefix_match.group(3) + prefix_match.group(4)
                content_str = content_str.replace(to_remove, "", 1).strip()
                # 移除可能残存的开头符号（如句点或顿号）
                content_str = re.sub(r'^[\s、\.．]+', '', content_str)
                q["content"] = content_str
                
            q["source"] = (extracted_source or paper_title).strip()
            
            # Clean up double-escaped literal \n in fields
            for field in ["content", "answer_markdown"]:
                if field in q and isinstance(q[field], str):
                    text = q[field]
                    # Replace literal "\n" safely using negative lookahead (so it doesn't touch commands like \normalsize or \nabla)
                    text = re.sub(r'\\n(?![a-zA-Z])', '\n', text)
                    q[field] = text
            
            # Map images
            mapped_images = []
            ref_imgs = q.get("referenced_images", [])
            for ref_name in ref_imgs:
                ref_name = str(ref_name)
                # Direct match or fuzzy match
                found_path = None
                for orig_name, serv_path in image_mapping.items():
                    if tex_asset_references_match(ref_name, orig_name):
                        found_path = serv_path
                        break
                if found_path:
                    if found_path not in mapped_images:
                        mapped_images.append(found_path)
                    include_pattern = re.compile(
                        r"\\includegraphics(?:\s*\[[^\]]*\])?\s*\{\s*([^}]+?)\s*\}"
                    )
                    q["content"] = include_pattern.sub(
                        lambda match: (
                            f"![插图]({found_path})"
                            if tex_asset_references_match(ref_name, match.group(1))
                            else match.group(0)
                        ),
                        q["content"],
                    )
                else:
                    tex_diagnostics.setdefault("unmapped_images", []).append(str(ref_name))
                    
            q["image_paths"] = mapped_images
            
            # If AI didn't map it in content text but referenced it, append it to content
            for img_path in mapped_images:
                if img_path not in q["content"]:
                    q["content"] += f"\n\n![插图]({img_path})\n\n"

        unmapped_images = sorted(set(tex_diagnostics.get("unmapped_images", [])))
        unassigned_images = sorted(set(tex_diagnostics.get("unassigned_source_images", [])))
        tex_diagnostics["unmapped_images"] = unmapped_images
        tex_diagnostics["unassigned_source_images"] = unassigned_images
        if unmapped_images:
            tex_diagnostics.setdefault("warnings", []).append(
                "以下 TeX 配图未找到同名上传文件：" + "、".join(unmapped_images[:8])
            )
        if unassigned_images:
            tex_diagnostics.setdefault("warnings", []).append(
                "以下配图未能确定所属题目：" + "、".join(unassigned_images[:8])
            )
                    
        # 与 PDF/Word 路线同一个归一化出口：拆卷卡片预览必须等于入库源码（同一条幂等链），
        # 所以只能整体跑，不许只跑其中一层；写库边界还会再跑一遍，结果字节相同。
        parsed_questions = _main.apply_storage_normalization(parsed_questions)

        return {
            "status": "success",
            "questions": parsed_questions,
            "tex_diagnostics": tex_diagnostics,
        }
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"试卷解析失败: {str(e)}"},
            status_code=500
        )


@router.post("/api/ai/manual-crop-pdf")
def manual_crop_pdf(payload: dict):
    """用户在前端手动拖拽框选后，后端根据坐标裁剪 PDF 页面的特定区域"""
    try:
        import math

        if not isinstance(payload, dict):
            raise ValueError("裁剪参数格式不正确。")
        try:
            task_id = str(uuid.UUID(str(payload.get("task_id", ""))))
        except (ValueError, AttributeError) as exc:
            raise ValueError("任务 ID 格式不正确。") from exc
        task = _main.DOCUMENT_TASKS.snapshot(task_id)
        if not task or task.get("document_type") != "pdf":
            return JSONResponse(
                content={"status": "error", "message": "未找到对应的 PDF 任务！"},
                status_code=404,
            )
        if task.get("status") in {"cancelled", "error"}:
            raise ValueError("已取消或失败的 PDF 任务不能再裁剪。")
        page_index = int(payload.get("page_index", 0))
        if page_index < 0 or page_index >= _main.MAX_PDF_TASK_PAGES:
            raise ValueError("页码越界。")
        ymin = float(payload.get("ymin", 0))
        xmin = float(payload.get("xmin", 0))
        ymax = float(payload.get("ymax", 0))
        xmax = float(payload.get("xmax", 0))
        coordinates = (ymin, xmin, ymax, xmax)
        if not all(math.isfinite(value) for value in coordinates):
            raise ValueError("裁剪坐标必须是有限数值。")
        if not (
            0 <= ymin < ymax <= 100
            and 0 <= xmin < xmax <= 100
        ):
            raise ValueError("裁剪坐标必须位于 0–100，且框选区域不能为空。")

        img_filename = f"pdf_page_{task_id}_{page_index}.png"
        img_filepath = Path(_main.TMP_UPLOAD_DIR) / img_filename
        
        if not img_filepath.is_file() or img_filepath.is_symlink():
            return JSONResponse(
                content={"status": "error", "message": "未找到对应的 PDF 页面图片！"},
                status_code=404
            )
            
        with Image.open(img_filepath) as img:
            img.load()
            w, h = img.size

            # Convert percentage to pixels.
            left = max(0, min((xmin / 100.0) * w, w - 1))
            top = max(0, min((ymin / 100.0) * h, h - 1))
            right = max(left + 1, min((xmax / 100.0) * w, w))
            bottom = max(top + 1, min((ymax / 100.0) * h, h))
            cropped = img.crop((left, top, right, bottom))

        crop_filename = f"pdf_crop_{task_id}_{uuid.uuid4().hex[:12]}.png"
        crop_filepath = Path(_main.TMP_UPLOAD_DIR) / crop_filename
        cropped.save(crop_filepath, format="PNG")
        
        img_url = f"/{_main.UPLOAD_DIR_REL}/tmp/{crop_filename}"
        if not _main.DOCUMENT_TASKS.add_temp_asset(task_id, img_url):
            crop_filepath.unlink(missing_ok=True)
            raise ValueError("任务记录已过期，无法登记裁剪图片。")
        return {"status": "success", "image_path": img_url}
    except ValueError as e:
        return JSONResponse(
            content={"status": "error", "message": f"手动裁剪失败: {str(e)}"},
            status_code=400,
        )
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"手动裁剪失败: {str(e)}"},
            status_code=500
        )


def extract_title_from_latex(latex: str) -> str:
    """从 LaTeX 源码中尝试自动提取试卷标题"""
    if not latex:
        return ""
    import re
    
    def clean_latex(txt: str) -> str:
        # 移除字体大小命令等
        txt = re.sub(r'\\(large|Large|LARGE|huge|Huge|small|bf|bfseries|it|itshape|sf|tt|heiti|kaishu|fangsong|songti)', '', txt)
        # 解包 textbf 等
        txt = re.sub(r'\\text(bf|it|sf|tt)?\s*\{([^}]+)\}', r'\2', txt)
        txt = txt.replace('{', '').replace('}', '').replace('\\\\', '\n').strip()
        lines = [line.strip() for line in txt.split('\n') if line.strip()]
        if lines:
            return lines[0][:60]
        return ""

    # 1. 尝试匹配 \title{...}
    match = re.search(r'\\title\s*\{([^}]+)\}', latex)
    if match:
        cleaned = clean_latex(match.group(1))
        if cleaned:
            return cleaned
            
    # 2. 尝试匹配 \chead{...}
    match = re.search(r'\\chead\s*\{([^}]+)\}', latex)
    if match:
        cleaned = clean_latex(match.group(1))
        if cleaned and "页" not in cleaned and "绝密" not in cleaned:
            return cleaned
            
    # 3. 尝试匹配 \begin{center} ... \end{center} 头部区域
    top_part = latex[:1500]
    match = re.search(r'\\begin\s*\{center\}([\s\S]*?)\\end\s*\{center\}', top_part)
    if match:
        cleaned = clean_latex(match.group(1))
        if cleaned:
            return cleaned
            
    return ""


@router.post("/api/paper/ai-select")
def ai_select_paper(payload: dict, db: Session = Depends(get_db)):
    """AI 智能选题：结合用户指定的 PREFER_SOLVE_MODEL 大模型与 math-teaching 教研引擎组卷"""
    try:
        prompt = payload.get("prompt", "").strip()
        question_type = payload.get("question_type", "")
        difficulty = payload.get("difficulty", "")
        compulsory = payload.get("compulsory", "")
        chapter = payload.get("chapter", "")
        knowledge = payload.get("knowledge", "")
        limit = max(1, min(int(payload.get("limit", 5)), 20))

        # 0. 自然语言意图智能分析 (NL Intent Parser)
        extracted_topics = []
        is_review_intent = False
        if prompt:
            is_review_intent = any(k in prompt for k in ['做过', '考过', '已抽过', '已用过', '复习', '旧题', '重做', '错题', '以往', '历史'])
            num_match = re.search(r'([一二三四五六七八九十1-9]+)\s*道', prompt)
            cn_to_num = {'一':1, '两':2, '二':2, '三':3, '四':4, '五':5, '六':6, '七':7, '八':8, '九':9, '十':10}
            if num_match:
                val = num_match.group(1)
                limit = cn_to_num.get(val, int(val) if val.isdigit() else limit)

            if not question_type:
                if '填空' in prompt: question_type = 'fill_in_blank'
                elif '单选' in prompt: question_type = 'single_choice'
                elif '多选' in prompt: question_type = 'multi_choice'
                elif '解答' in prompt: question_type = 'detailed_answer'

            known_topics = ['立体几何', '集合', '函数', '导数', '数列', '三角函数', '平面向量', '概率', '解析几何', '圆锥曲线', '复数', '不等式', '排列组合']
            extracted_topics = [t for t in known_topics if t in prompt]

        # 1. 结构化过滤基础题目池
        query = db.query(Question)
        if question_type:
            query = query.filter(Question.question_type == question_type)
        if difficulty:
            query = query.filter(Question.difficulty == difficulty)
        if compulsory:
            query = query.filter(Question.category_compulsory == compulsory)
        if chapter:
            query = query.filter(Question.category_chapter == chapter)
        if knowledge:
            query = query.filter(Question.category_knowledge == knowledge)
            
        if is_review_intent:
            # 复习/旧题模式：优先提取已使用频次高的题目
            review_query = query.filter(Question.usage_count > 0).order_by(Question.usage_count.desc(), Question.id.desc())
            candidates = review_query.limit(35).all()
            if not candidates:
                candidates = query.order_by(Question.id.desc()).limit(35).all()
        else:
            # 默认鲜活模式：优先提取从未被使用过的冷门题目
            candidates = query.order_by(Question.usage_count.asc(), Question.id.desc()).limit(35).all()
            if not candidates:
                candidates = db.query(Question).order_by(Question.usage_count.asc(), Question.id.desc()).limit(35).all()

        # 2. 解题、拆卷、分类和组卷共用同一供应商解析规则。
        # 不会因为某家 Key 缺失而静默改用另一家。
        target_model = (
            os.getenv("PREFER_SOLVE_MODEL")
            or os.getenv("PREFER_PARSE_MODEL")
            or "deepseek-flash"
        )
        provider = _main.resolve_text_provider(target_model)
        api_key = provider.api_key
        api_base = provider.api_base
        model_name = provider.model_name
        provider_name = provider.provider_label

        api_error_detail = None
        if prompt and candidates:
            if not api_key or not api_base:
                api_error_detail = (
                    f"指定的 AI 解题模型 ({target_model}) 未配置有效的 "
                    f"API Key 或 Base URL（{provider.credential_label}）。"
                )
            else:
                candidate_items = []
                for q in candidates:
                    clean_stem = re.sub(r'[\r\n]+', ' ', q.content[:80])
                    candidate_items.append({
                        "id": q.id,
                        "question_type": q.question_type,
                        "difficulty": q.difficulty,
                        "usage_count": q.usage_count or 0,
                        "knowledge": q.category_knowledge or q.category_chapter or "通用知识点",
                        "tags": q.tags or "",
                        "stem_excerpt": clean_stem
                    })

                system_prompt, user_content = build_paper_selection_prompts(
                    teacher_prompt=prompt,
                    limit=limit,
                    candidates=candidate_items,
                    is_review_intent=is_review_intent,
                )

                try:
                    payload_data = {
                        "model": model_name,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content}
                        ],
                        "temperature": 0.3
                    }
                    payload_data = inject_reasoning_effort(
                        payload_data, provider.reasoning_effort
                    )
                    payload_data = apply_bailian_thinking_policy(
                        payload_data,
                        provider_code=provider.provider_code,
                        model_name=model_name,
                        task="paper_selection",
                    )
                    response = _main.post_chat_completion(
                        provider,
                        payload_data,
                        timeout=20,
                        provider_name=provider_name,
                    )
                    res_json = response.json()
                    raw_content = res_json.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    if raw_content.startswith("```"):
                        raw_content = re.sub(r"^```(?:json)?\s*", "", raw_content)
                        raw_content = re.sub(r"\s*```$", "", raw_content)

                    parsed = json.loads(raw_content)
                    raw_selected_ids = parsed.get("selected_ids", [])
                    ai_analysis = parsed.get("ai_analysis", "")

                    if raw_selected_ids and isinstance(raw_selected_ids, list):
                        # The model may only rank the candidate IDs that were
                        # actually supplied after local filters.  This prevents
                        # prompt output from bypassing chapter/type constraints
                        # or selecting arbitrary records from the database.
                        allowed_ids = {question.id for question in candidates}
                        selected_ids = []
                        seen_ids = set()
                        for raw_id in raw_selected_ids:
                            try:
                                selected_id = int(raw_id)
                            except (TypeError, ValueError):
                                continue
                            if (
                                selected_id in allowed_ids
                                and selected_id not in seen_ids
                            ):
                                selected_ids.append(selected_id)
                                seen_ids.add(selected_id)
                            if len(selected_ids) >= limit:
                                break
                        db_selected = db.query(Question).filter(Question.id.in_(selected_ids)).all()
                        id_map = {q.id: q for q in db_selected}
                        seq_map = _main.get_seq_mapping(db, selected_ids)
                        final_questions = [{**_decorate_topic_field(id_map[qid].to_dict()), "seq_num": seq_map.get(qid)} for qid in selected_ids if qid in id_map]

                        if final_questions:
                            return {
                                "status": "success",
                                "data": final_questions,
                                "count": len(final_questions),
                                "ai_analysis": ai_analysis,
                                "model_used": f"{provider_name} ({model_name})",
                                "fallback": False
                            }
                except Exception as llm_err:
                    api_error_detail = f"{provider_name} API 请求失败: {str(llm_err)}"

        # 3. 降级本地算法（带明确错误反馈）
        fallback_questions = []
        if extracted_topics:
            for topic in extracted_topics:
                sub_query = db.query(Question)
                if question_type:
                    sub_query = sub_query.filter(Question.question_type == question_type)
                sub_query = sub_query.filter(
                    (Question.content.like(f"%{topic}%")) |
                    (Question.category_chapter.like(f"%{topic}%")) |
                    (Question.category_knowledge.like(f"%{topic}%")) |
                    (Question.tags.like(f"%{topic}%"))
                )
                order_clause = Question.usage_count.desc() if is_review_intent else Question.usage_count.asc()
                for q in sub_query.order_by(order_clause, Question.id.desc()).all():
                    if q not in fallback_questions:
                        fallback_questions.append(q)

        # 补足数量
        if len(fallback_questions) < limit:
            for q in candidates:
                if q not in fallback_questions:
                    fallback_questions.append(q)
                if len(fallback_questions) >= limit:
                    break

        selected_fallback = fallback_questions[:limit]
        seq_map = _main.get_seq_mapping(db, [q.id for q in selected_fallback])
        result = [{**_decorate_topic_field(q.to_dict()), "seq_num": seq_map.get(q.id)} for q in selected_fallback]
        
        topic_str = "、".join(extracted_topics) if extracted_topics else "通用知识点"
        err_banner = f"⚠️ 【AI 解题模型调用未成功】: {api_error_detail}\n系统已为您自动启动本地教研算法，根据意图（{topic_str}）在本地题库中筛选并组合了 {len(result)} 道精选题目。" if api_error_detail else f"【本地智能筛选分析】已为您自动识别意图（{topic_str}），从题库中精准挑选并组合了鲜活试题。"
        
        return {
            "status": "success",
            "data": result,
            "count": len(result),
            "ai_analysis": err_banner,
            "model_used": f"⚠️ 模型调用失败 ({target_model}) ➔ 退回本地算法" if api_error_detail else "本地算法",
            "fallback": True
        }
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"AI 智能选题失败: {str(e)}"}, status_code=500)


def _resolve_task_paper_title(paper_title: Optional[str], filename: str) -> str:
    """用户在前端统一填写的试卷标题优先；留空时回退到文件名。"""
    title = (paper_title or "").strip()
    return title if title else os.path.splitext(filename)[0]


def _register_staged_task_assets(task_id: str, urls) -> None:
    """把已落盘的插图登记为任务临时资产（``add_temp_asset`` 自带去重）。

    MinerU 抽出的插图落在 ``static/uploads`` 根目录，而 ``_delete_task_temp_assets``
    只删 tmp 下的文件，所以登记它们是安全的。登记之后：
      ① 启动时的孤儿清理不会把它们当垃圾删掉（``live_temp_assets`` 算作被引用）；
      ② 前端「审图」面板才能列出「未归属图」——已抽出但没有任何题目引用的图。
    登记失败不应影响主流程，因此这里吞掉异常。
    """

    for url in urls or []:
        if not isinstance(url, str) or not url:
            continue
        try:
            _main.DOCUMENT_TASKS.add_temp_asset(task_id, url)
        except Exception:  # pragma: no cover - 防御性
            pass


def _page_texts_log_note(page_texts: list) -> str:
    """把「能否推断题目来源页」写进导入日志。

    结果包里没有 ``*_content_list.json`` 时，来源页推断只能放弃。把原因写进日志，
    用户（以及排查时的我们）就能一眼看到，而不是事后怀疑是功能坏了。
    """

    if page_texts:
        return (
            f"文本与插图就绪（已重建 {len(page_texts)} 页文本，可推断题目来源页）！"
        )
    return "文本与插图就绪（结果包内无 *_content_list.json，本卷无法推断题目来源页）！"


def _render_pdf_page_images(
    source,
    task_id: str,
    temp_assets: list,
    page_indices: Optional[list] = None,
    log: str = "正在渲染高清图...",
) -> tuple[list, list]:
    """把 PDF 指定页渲染成 150dpi 整页 PNG，登记为任务临时资产并写入任务快照。

    整页原图是三项能力的**共同前提**：
      ① ``manual_crop_pdf`` 直接读 ``pdf_page_{task}_{n}.png`` 做手动裁图；
      ② 拆完题后对照原页核对「这道题到底有没有图、图有没有被漏掉」；
      ③ 插图兜底（按页找图）。
    MinerU 分支同样必须渲染——它只是替我们抽了它认得的插图，并不能免除整页原图。

    返回 ``(page_file_paths, page_urls)``，两者都与实际渲染的页序同序。
    """

    import pymupdf as fitz

    if isinstance(source, (bytes, bytearray)):
        document = fitz.open(stream=bytes(source), filetype="pdf")
    else:
        document = fitz.open(str(source))

    page_paths: list = []
    page_urls: list = []
    with document:
        total_pages = len(document)
        if total_pages == 0:
            raise ValueError("此 PDF 没有有效页面，或者格式已损坏！")

        if page_indices is None:
            limit = min(total_pages, _main.MAX_PDF_TASK_PAGES)
            if total_pages > limit:
                print(
                    f"[PDF Render] 共 {total_pages} 页，超过单任务上限 "
                    f"{_main.MAX_PDF_TASK_PAGES} 页，只渲染前 {limit} 页的整页图。",
                    flush=True,
                )
            target_page_indices = list(range(limit))
        else:
            target_page_indices = list(page_indices)

        if target_page_indices:
            _main.DOCUMENT_TASKS.update(
                task_id,
                log=(
                    f"PDF 共 {total_pages} 页；本次处理 {len(target_page_indices)} 页"
                    f"（第 {target_page_indices[0] + 1}–{target_page_indices[-1] + 1} 页），"
                    f"{log}"
                ),
            )

        for page_num in target_page_indices:
            _main.DOCUMENT_TASKS.check_cancelled(task_id)
            page = document.load_page(page_num)
            estimated_pixels = int(
                (page.rect.width / 72 * 150) * (page.rect.height / 72 * 150)
            )
            if estimated_pixels > 30_000_000:
                raise ValueError(f"第 {page_num + 1} 页尺寸异常，已停止高清渲染。")
            pixmap = page.get_pixmap(dpi=150)
            image_filename = f"pdf_page_{task_id}_{page_num}.png"
            image_path = Path(_main.TMP_UPLOAD_DIR) / image_filename
            pixmap.save(image_path)
            image_url = f"/{_main.UPLOAD_DIR_REL}/tmp/{image_filename}"
            page_paths.append(str(image_path))
            page_urls.append(image_url)
            if image_url not in temp_assets:
                temp_assets.append(image_url)
            _main.DOCUMENT_TASKS.update(
                task_id,
                page_images=list(page_urls),
                temp_assets=list(temp_assets),
            )

    return page_paths, page_urls


def run_mineru_pdf_parsing_task(
    task_id: str,
    file_bytes: bytes,
    filename: str,
    generate_answers: bool = False,
    paper_title: Optional[str] = None,
    force_fresh: bool = False,
):
    """MinerU 云端解析分支：把 PDF 交给 MinerU 云端，拿回 Markdown 与插图，
    插图落盘后原位替换引用，再复用既有大模型拆题流程。

    ``force_fresh`` 跳过结果缓存（见 mathbank.mineru_cache），用于用户明确要
    重跑云端的场景；默认命中缓存即不再消耗云额度。
    """

    temp_assets: list[str] = []
    workdir = Path(_main.TMP_UPLOAD_DIR) / f"mineru_{task_id}"
    try:
        from mathbank import mineru_client
    except ImportError as exc:  # pragma: no cover - requests 缺失
        _main.DOCUMENT_TASKS.fail(
            task_id,
            f"MinerU 客户端依赖缺失（requests），请联系开发者安装：{exc}",
            document_type="pdf",
        )
        return

    try:
        _main.DOCUMENT_TASKS.check_cancelled(task_id)
        _main.DOCUMENT_TASKS.update(
            task_id,
            status="mineru_upload",
            progress=10,
            log="已接收文件，正在上传至 MinerU 云端解析...",
            document_type="pdf",
            temp_assets=[],
        )
        if not mineru_client.is_configured():
            raise mineru_client.MineruError(
                "未配置 MinerU API Token，请在设置面板填写并保存后重试。"
            )

        workdir.mkdir(parents=True, exist_ok=True)

        # 整页原图必须自己渲染：MinerU 只替我们抽出它认得的插图，并不能免除整页 PNG。
        # 少了这一步，MinerU 路径下「手动裁图」会直接报错（它读的就是这些文件），
        # 拆完题之后也无从对照原页核对「这道题到底有没有图」。
        _main.DOCUMENT_TASKS.update(
            task_id,
            progress=15,
            log="正在渲染整页原图（供手动裁图与图文核对）...",
            document_type="pdf",
        )
        try:
            _render_pdf_page_images(file_bytes, task_id, temp_assets)
        except Exception as render_exc:  # noqa: BLE001 - 渲染失败不得拖垮主流程
            print(
                f"[MinerU] 整页原图渲染失败，手动裁图与图文核对将不可用: {render_exc}",
                flush=True,
            )

        def on_progress(state: str, item) -> None:
            if state == "cached":
                _main.DOCUMENT_TASKS.update(
                    task_id,
                    progress=60,
                    log="命中本机 MinerU 结果缓存，跳过云端解析（不消耗云额度）。",
                    document_type="pdf",
                )
            elif state == "uploading":
                _main.DOCUMENT_TASKS.update(
                    task_id,
                    progress=25,
                    log="文件已上传，MinerU 云端正在解析排版、公式与插图...",
                    document_type="pdf",
                )
            elif state in ("processing", "running"):
                _main.DOCUMENT_TASKS.update(
                    task_id,
                    progress=45,
                    log="MinerU 云端解析中（含公式与插图识别）...",
                    document_type="pdf",
                )

        result = mineru_client.parse_pdf(
            file_bytes,
            filename,
            workdir,
            on_progress=on_progress,
            force=force_fresh,
        )
        markdown = result.get("markdown") or ""
        image_paths = result.get("image_paths") or []
        cache_hit = bool(result.get("cache_hit"))

        # 先过滤 MinerU 误抽的装饰图（水印/二维码/页眉条/公式碎片），避免它们被
        # 大模型挂到题目上造成「末题吞掉整卷图」的现象。
        markdown, image_paths = mineru_client.filter_mineru_decoration_images(
            markdown, image_paths
        )

        _main.DOCUMENT_TASKS.check_cancelled(task_id)
        _main.DOCUMENT_TASKS.update(
            task_id,
            status="mineru_staging",
            progress=70,
            log="云端解析完成，正在落盘插图并建立链接...",
            document_type="pdf",
        )

        mapping = mineru_client.stage_images(image_paths, _main.UPLOAD_DIR)
        markdown = mineru_client.replace_image_refs(markdown, mapping)
        # 登记落盘插图：前端审图面板据此列出「未归属图」（抽出来但没被任何题目引用的图）。
        _register_staged_task_assets(task_id, mapping.values())
        # 逐页文本：来自结果包的结构化清单（*_content_list.json，每项带 page_idx）。
        # 有了它才能推断「这道题来自第几页」，前端卡片才能给出「源自第 N 页 / 查看原页」。
        # 拿不到清单时返回空列表，行为退化成改动前（不会更差）。
        page_texts = mineru_client.collect_page_texts(result.get("workdir") or workdir)

        if not markdown.strip():
            raise ValueError("MinerU 未能从 PDF 中提取出可解析的文字内容。")

        _main.DOCUMENT_TASKS.check_cancelled(task_id)
        _main.DOCUMENT_TASKS.update(
            task_id,
            status="ai_splitting",
            progress=80,
            log=_page_texts_log_note(page_texts) + "正在调用大模型拆解题目...",
            document_type="pdf",
        )
        _main.DOCUMENT_TASKS.check_cancelled(task_id)

        paper_title = _resolve_task_paper_title(paper_title, filename)
        parsed_questions = _main.parse_paper_text_internal(markdown, generate_answers)
        _main.DOCUMENT_TASKS.check_cancelled(task_id)
        # 传入逐页文本（见上）：用来推断每道题的来源页，供卡片上的「源自第 N 页」入口。
        # 它**不会**让插图兜底误挂图 —— 兜底只认 tmp 下的 pdf_crop_*，而 MinerU 的插图
        # 落在静态根目录，所以这里实际只会产出 _source_page。
        final_questions = _main.post_process_pdf_parsed_questions(
            parsed_questions, paper_title, task_id, page_texts or None
        )
        _main.DOCUMENT_TASKS.check_cancelled(task_id)
        # 云路线同样要对账：MinerU 抽了几幅图、模型把几幅挂到了题上。整卷口径
        # （见 summarize_figure_coverage），因为 mineru_*.jpg 不带页码。
        figure_report = illustration_crop.summarize_figure_coverage(
            file_bytes, None, final_questions
        )
        finish_log = "完成！MinerU 云端解析 + 大模型拆解全部题目卡片。"
        if cache_hit:
            finish_log = (
                "完成！本次复用本机 MinerU 结果缓存（未消耗云额度）+ 大模型拆解全部题目卡片。"
            )
        if figure_report.get("warnings"):
            finish_log += " 插图待核对：" + "；".join(figure_report["warnings"])
        _main.DOCUMENT_TASKS.complete(
            task_id,
            log=finish_log,
            data=final_questions,
            document_type="pdf",
            temp_assets=list(temp_assets),
            diagnostics={"figures": figure_report},
        )
    except TaskCancelled:
        _delete_task_temp_assets(temp_assets)
    except mineru_client.MineruError as exc:
        _delete_task_temp_assets(temp_assets)
        _main.DOCUMENT_TASKS.fail(task_id, f"MinerU 解析失败: {str(exc)}", document_type="pdf")
    except Exception as ex:
        _delete_task_temp_assets(temp_assets)
        _main.DOCUMENT_TASKS.fail(
            task_id,
            f"PDF 智能拆解解析失败: {str(ex)}",
            document_type="pdf",
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def run_pdf_parsing_task(
    task_id: str,
    file_bytes: bytes,
    filename: str,
    generate_answers: bool = False,
    page_range: str = None,
    pdf_strategy: str = "native_preferred",
    paper_title: Optional[str] = None,
    force_fresh: bool = False,
):
    """PDF parsing with bounded OCR concurrency and cooperative cancellation."""

    # MinerU 云端解析走独立分支：本地无需安装 MinerU，仅把 PDF 交给云端，
    # 拿回 Markdown + 插图后灌入既有大模型拆题流程。
    if pdf_strategy == "mineru_cloud":
        run_mineru_pdf_parsing_task(
            task_id,
            file_bytes,
            filename,
            generate_answers,
            paper_title,
            force_fresh,
        )
        return

    import concurrent.futures

    # 分步计时：每个阶段结束时把耗时写进该阶段的日志，最后再报总计用时。
    # 日志面板据此逐行显示"这一步用了多久"，无需前端按轮询间隔推算。
    run_started = time.monotonic()
    stage_started = run_started

    def _lap() -> str:
        """返回上一阶段耗时文本，并重开阶段计时。"""

        nonlocal stage_started
        now = time.monotonic()
        text = format_duration(now - stage_started)
        stage_started = now
        return text

    temp_assets: list[str] = []
    tmp_pdf_path = Path(_main.TMP_UPLOAD_DIR) / f"{task_id}.pdf"

    try:
        import pymupdf as fitz
    except ImportError:
        _main.DOCUMENT_TASKS.fail(
            task_id,
            "本地 Python 环境未安装 PyMuPDF，请通过 pip install pymupdf 安装依赖！",
            document_type="pdf",
        )
        return

    try:
        _main.DOCUMENT_TASKS.check_cancelled(task_id)
        tmp_pdf_path.write_bytes(file_bytes)
        _main.DOCUMENT_TASKS.update(
            task_id,
            status="processing_images",
            progress=10,
            log="已接收文件，正在渲染 PDF 高清页面...",
            document_type="pdf",
            temp_assets=[],
        )

        # 先轻量打开一次只为拿页数并解析页码范围，渲染交给统一助手（MinerU 分支共用）。
        with fitz.open(tmp_pdf_path) as probe:
            total_pages = len(probe)
        if total_pages == 0:
            raise ValueError("此 PDF 没有有效页面，或者格式已损坏！")
        target_page_indices = parse_page_range(page_range, total_pages)
        if len(target_page_indices) > _main.MAX_PDF_TASK_PAGES:
            raise ValueError(
                f"单次最多解析 {_main.MAX_PDF_TASK_PAGES} 页，请填写较小的页码范围。"
            )
        page_images, page_urls = _render_pdf_page_images(
            tmp_pdf_path,
            task_id,
            temp_assets,
            page_indices=target_page_indices,
        )

        tmp_pdf_path.unlink(missing_ok=True)
        _main.DOCUMENT_TASKS.check_cancelled(task_id)
        total_target_pages = len(target_page_indices)
        render_took = _lap()  # 高清渲染阶段耗时

        if pdf_strategy == "force_ocr":
            inspector_result = {"pages": [], "pdf_type": "scanned"}
            inspector_pages = {}
        else:
            inspector_result = inspect_and_extract_pdf(
                file_bytes,
                task_id,
                page_indices=target_page_indices,
                upload_tmp_dir=_main.TMP_UPLOAD_DIR,
            )
            inspector_pages = {
                int(page.get("page_index")): page
                for page in inspector_result.get("pages", [])
                if page.get("page_index") is not None
            }
        _main.DOCUMENT_TASKS.check_cancelled(task_id)
        extract_took = _lap()  # 原生文字/公式提取（或跳过）阶段耗时

        ocr_results = [None] * total_target_pages
        pages_requiring_ocr = []
        native_page_count = 0
        for local_idx, page_num in enumerate(target_page_indices):
            page_info = inspector_pages.get(page_num)
            native_text = str((page_info or {}).get("markdown") or "").strip()
            if page_info and not page_info.get("needs_ocr") and native_text:
                ocr_results[local_idx] = (
                    f"<!-- MATHBANK_PDF_PAGE:{page_num + 1} -->\n{native_text}"
                )
                native_page_count += 1
            else:
                pages_requiring_ocr.append(local_idx)

        if native_page_count:
            print(
                f"[PDF Inspector Flow] 原生直提 {native_page_count} 页，"
                f"视觉 OCR {len(pages_requiring_ocr)} 页 "
                f"(Type: {inspector_result.get('pdf_type')})",
                flush=True,
            )

        if not pages_requiring_ocr:
            _main.DOCUMENT_TASKS.update(
                task_id,
                status="ai_splitting",
                progress=60,
                log=(
                    f"原生文字公式提取完成：{native_page_count}/{total_target_pages} 页直提"
                    f"（渲染 {render_took}、提取 {extract_took}、判定 {inspector_result.get('pdf_type')}），"
                    "正在连续拆题..."
                ),
                page_images=list(page_urls),
                temp_assets=list(temp_assets),
            )
        else:
            _main.DOCUMENT_TASKS.update(
                task_id,
                status="ocr_extraction",
                progress=30,
                log=(
                    f"原生提取 {native_page_count} 页、需视觉转译 {len(pages_requiring_ocr)} 页"
                    f"（渲染 {render_took}、提取 {extract_took}、判定 {inspector_result.get('pdf_type')}），"
                    "开始视觉 OCR..."
                ),
                page_images=list(page_urls),
                temp_assets=list(temp_assets),
            )

            def ocr_worker(local_idx, image_path):
                acquired = False
                try:
                    while not acquired:
                        _main.DOCUMENT_TASKS.check_cancelled(task_id)
                        acquired = _main.PDF_OCR_SEMAPHORE.acquire(timeout=0.25)
                    _main.DOCUMENT_TASKS.check_cancelled(task_id)
                    raw_text = _main.ocr_pdf_page_image(image_path)
                    real_page_num = target_page_indices[local_idx] + 1
                    print(
                        f"[PDF OCR] 第 {real_page_num} 页识别完成 "
                        f"(characters={len(raw_text)})."
                    )
                    return local_idx, raw_text, None
                except TaskCancelled:
                    raise
                except Exception as ocr_error:
                    return local_idx, "", str(ocr_error)
                finally:
                    if acquired:
                        _main.PDF_OCR_SEMAPHORE.release()

            executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=min(len(pages_requiring_ocr), 4),
                thread_name_prefix="mathbank-pdf-ocr",
            )
            futures = []
            try:
                for local_idx in pages_requiring_ocr:
                    _main.DOCUMENT_TASKS.check_cancelled(task_id)
                    futures.append(
                        executor.submit(ocr_worker, local_idx, page_images[local_idx])
                    )

                completed = 0
                for future in concurrent.futures.as_completed(futures):
                    _main.DOCUMENT_TASKS.check_cancelled(task_id)
                    local_idx, text, error = future.result()
                    if error:
                        real_page_num = target_page_indices[local_idx] + 1
                        raise RuntimeError(f"解析第 {real_page_num} 页出错: {error}")
                    # 先按 ILLUSTRATION_BOX 坐标裁图，替换成占位符，再擦除残留标签。
                    # 传入原 PDF 字节：视觉模型的百分比框经常小于真实图形甚至整体偏移，
                    # 裁图前要用该页内嵌位图的真实边界纠正，否则直方图会被裁成一角、
                    # 统计图会裁成上方的正文行。
                    cropped_text = _main.resolve_ocr_illustration_boxes(
                        text,
                        page_images[local_idx],
                        task_id,
                        target_page_indices[local_idx],
                        pdf_bytes=file_bytes,
                    )
                    processed_text = _main.process_ocr_illustrations(cropped_text)
                    real_page_num = target_page_indices[local_idx] + 1
                    ocr_results[local_idx] = (
                        f"<!-- MATHBANK_PDF_PAGE:{real_page_num} -->\n"
                        f"{processed_text.strip()}"
                    )
                    completed += 1
                    progress = 30 + int(
                        (completed / len(pages_requiring_ocr)) * 40
                    )
                    _main.DOCUMENT_TASKS.update(
                        task_id,
                        progress=progress,
                        log=(
                            f"视觉转译进度: {completed} / "
                            f"{len(pages_requiring_ocr)} 页已完成..."
                        ),
                    )
            finally:
                cancelled = _main.DOCUMENT_TASKS.is_cancelled(task_id)
                if cancelled:
                    for future in futures:
                        future.cancel()
                executor.shutdown(wait=not cancelled, cancel_futures=True)

        _main.DOCUMENT_TASKS.check_cancelled(task_id)
        full_latex_content = merge_pdf_page_texts(ocr_results)
        if not full_latex_content.strip():
            raise ValueError("所选 PDF 页面未能提取出可解析的文字内容。")
        text_ready_took = _lap()  # 视觉 OCR + 文本合并阶段耗时

        _main.DOCUMENT_TASKS.update(
            task_id,
            status="ai_splitting",
            progress=80,
            log=(
                f"文本与公式准备就绪（提取阶段合计 {text_ready_took}，"
                f"正文 {len(full_latex_content)} 字）！正在调用大模型拆解题目与标注属性..."
            ),
        )
        _main.DOCUMENT_TASKS.check_cancelled(task_id)

        paper_title = _resolve_task_paper_title(paper_title, filename)
        auto_title = extract_title_from_latex(full_latex_content)
        if auto_title:
            paper_title = auto_title
        parsed_questions = _main.parse_paper_text_internal(
            full_latex_content,
            generate_answers,
        )
        _main.DOCUMENT_TASKS.check_cancelled(task_id)
        final_questions = _main.post_process_pdf_parsed_questions(
            parsed_questions,
            paper_title,
            task_id,
            ocr_results,
        )
        _main.DOCUMENT_TASKS.check_cancelled(task_id)
        # 图形对账：逐页比较「PDF 里真实有几幅图形」与「有几幅归到了题目上」。
        # 视觉模型整幅漏掉图形时不会报错，只会让用户的图凭空消失，所以这里必须
        # 把缺口算出来写进诊断与拆卷日志。
        figure_report = illustration_crop.summarize_figure_coverage(
            file_bytes,
            list(zip(target_page_indices, page_images)),
            final_questions,
        )
        split_took = _lap()  # 大模型拆题 + 元数据后处理阶段耗时
        total_took = format_duration(time.monotonic() - run_started)
        finish_log = (
            f"完成！共拆出 {len(final_questions)} 道题。"
            f"分步耗时：渲染 {render_took}、文字公式提取 {extract_took}、"
            f"OCR/合成 {text_ready_took}、AI 拆题 {split_took}；总计 {total_took}。"
        )
        if figure_report.get("warnings"):
            finish_log += " 插图待核对：" + "；".join(figure_report["warnings"])
        _main.DOCUMENT_TASKS.complete(
            task_id,
            log=finish_log,
            data=final_questions,
            page_images=list(page_urls),
            temp_assets=list(temp_assets),
            document_type="pdf",
            diagnostics={"figures": figure_report},
        )
    except TaskCancelled:
        _delete_task_temp_assets(temp_assets)
    except Exception as ex:
        _delete_task_temp_assets(temp_assets)
        _main.DOCUMENT_TASKS.fail(
            task_id,
            f"PDF 智能拆解解析失败: {str(ex)}",
            document_type="pdf",
        )
    finally:
        tmp_pdf_path.unlink(missing_ok=True)


def parse_page_range(range_str: str, total_pages: int) -> list:
    """
    解析用户输入的页码范围字符串（1-indexed），转换为包含 0-indexed 页面索引的列表。
    支持格式如 "1-5", "1,3,5", "1-3,5,7-9"。
    """
    if total_pages <= 0:
        raise ValueError("PDF 没有有效页面。")
    if not range_str or not range_str.strip():
        return list(range(total_pages))

    pages = set()
    parts = str(range_str).replace(" ", "").split(",")
    for part in parts:
        if not part:
            raise ValueError("页码范围格式无效。")
        if "-" in part:
            sub_parts = part.split("-")
            if len(sub_parts) != 2:
                raise ValueError("页码范围格式无效。")
            try:
                start = int(sub_parts[0])
                end = int(sub_parts[1])
            except ValueError as exc:
                raise ValueError("页码范围必须使用数字。") from exc
            if start < 1 or end < start or end > total_pages:
                raise ValueError(f"页码范围必须位于 1 到 {total_pages}。")
            pages.update(range(start - 1, end))
        else:
            try:
                page_number = int(part)
            except ValueError as exc:
                raise ValueError("页码范围必须使用数字。") from exc
            if page_number < 1 or page_number > total_pages:
                raise ValueError(f"页码范围必须位于 1 到 {total_pages}。")
            pages.add(page_number - 1)

    if not pages:
        raise ValueError("页码范围不能为空。")
    return sorted(pages)


@router.post("/api/upload/pdf-task")
def upload_pdf_task(
    file: UploadFile = File(...),
    generate_answers: str = Form("false"),
    page_range: Optional[str] = Form(None),
    pdf_strategy: str = Form("native_preferred"),
    paper_title: Optional[str] = Form(None),
    force_fresh: str = Form("false"),
):
    try:
        generate_answers_bool = generate_answers.lower() in ("true", "1", "yes")
        
        # 验证文件扩展名
        filename = file.filename or ""
        if not filename.lower().endswith(".pdf"):
            return JSONResponse(
                content={"status": "error", "message": "上传文件格式不正确，必须为 .pdf 格式！"},
                status_code=400
            )
            
        # Incremental cap avoids loading an arbitrarily large multipart file.
        try:
            content = read_stream_limited(file.file, _main.MAX_PDF_BYTES)
        except UploadTooLargeError:
            return JSONResponse(
                content={"status": "error", "message": "PDF 文件过大，请上传 30MB 以内的试卷文件！"},
                status_code=413
            )
        if not content.lstrip().startswith(b"%PDF-"):
            return JSONResponse(
                content={"status": "error", "message": "文件内容不是有效的 PDF 文档！"},
                status_code=400,
            )
            
        task_id = str(uuid.uuid4())
        
        _main.DOCUMENT_TASKS.create(
            task_id,
            status="pending",
            log="任务已排队，正在准备运行异步切片分析...",
            document_type="pdf",
            temp_assets=[],
        )
        try:
            _main.DOCUMENT_TASKS.submit(
                task_id,
                run_pdf_parsing_task,
                task_id,
                content,
                filename,
                generate_answers_bool,
                page_range,
                pdf_strategy,
                paper_title,
                force_fresh.lower() in ("true", "1", "yes"),
            )
        except TaskQueueFull as exc:
            _main.DOCUMENT_TASKS.remove(task_id)
            return JSONResponse(
                content={"status": "error", "message": str(exc)},
                status_code=429,
            )
        
        return {
            "status": "success",
            "task_id": task_id
        }
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"创建 PDF 解析任务失败: {str(e)}"},
            status_code=500
        )


def _delete_task_temp_assets(paths: list) -> int:
    """Delete only explicit files below this instance's upload tmp directory."""
    removed = 0
    tmp_root = Path(_main.TMP_UPLOAD_DIR).resolve()
    for url in paths or []:
        try:
            full_path = resolve_upload_asset(
                str(url),
                uploads_dir=_main.UPLOAD_DIR,
                url_prefix=_main.UPLOAD_DIR_REL,
                require_file=False,
            )
        except AssetSecurityError:
            continue
        if full_path.parent != tmp_root:
            continue
        if full_path.is_file():
            try:
                full_path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def run_docx_parsing_task(
    task_id: str,
    file_bytes: bytes,
    filename: str,
    generate_answers: bool = False,
    paper_title: Optional[str] = None,
):
    temp_assets = []
    try:
        _main.DOCUMENT_TASKS.check_cancelled(task_id)
        _main.DOCUMENT_TASKS.update(
            task_id,
            status="extracting_docx",
            progress=25,
            log="已接收 Word 试卷，正在安全提取 OMML 公式、文字与配图...",
            document_type="docx",
            temp_assets=[],
        )

        # 2. 安全提取 Word Markdown；资产先放入 tmp，入库时再晋升。
        docx_res = _main.extract_docx_markdown(
            file_bytes,
            output_dir=_main.TMP_UPLOAD_DIR,
            url_prefix=f"/{_main.UPLOAD_DIR_REL}/tmp",
            asset_prefix=f"word_{task_id}",
        )
        temp_assets = docx_res.get("image_paths", [])
        if not docx_res.get("success") or not docx_res.get("markdown"):
            raise ValueError(docx_res.get("error") or "未能从 Word 文档中提取出有效试题内容！")

        full_markdown_content = docx_res["markdown"]
        img_count = docx_res.get("image_count", 0)
        diagnostics = docx_res.get("diagnostics", {})
        converted_count = diagnostics.get("omml_converted", 0) + diagnostics.get("mtef_converted", 0)
        review_count = diagnostics.get("review_required", 0)
        extraction_log = (
            f"Word 提取完成：{converted_count} 个公式已转换，{img_count} 张图片已保留"
            + (f"，{review_count} 处需人工核对。" if review_count else "，未发现需人工核对的内容。")
        )

        _main.DOCUMENT_TASKS.check_cancelled(task_id)
        _main.DOCUMENT_TASKS.update(
            task_id,
            status="ai_splitting",
            progress=70,
            log=extraction_log + " 正在调用教研模型拆题...",
            document_type="docx",
            diagnostics=diagnostics,
            temp_assets=list(temp_assets),
        )

        # 3. 智能提取标题与题目切片
        paper_title = _resolve_task_paper_title(paper_title, filename)
        auto_title = extract_title_from_latex(full_markdown_content)
        if auto_title:
            paper_title = auto_title

        # Keep every formula visible in-place for the model's mathematical
        # understanding, while assigning an immutable ID. The model returns
        # the ID and the server restores the exact Word-extracted source.
        locked_markdown_content, math_locks = lock_visible_math(
            full_markdown_content,
            task_id.replace("-", "")[:16],
        )
        diagnostics["math_locks_created"] = len(math_locks)
        _main.DOCUMENT_TASKS.check_cancelled(task_id)
        parsed_questions = _main.parse_paper_text_internal(locked_markdown_content, generate_answers)
        lock_report = restore_visible_math(parsed_questions, math_locks)
        diagnostics.update(lock_report)

        _main.DOCUMENT_TASKS.check_cancelled(task_id)
        final_questions = _main.post_process_pdf_parsed_questions(parsed_questions, paper_title, task_id, [full_markdown_content])
        _main.DOCUMENT_TASKS.check_cancelled(task_id)
        _main.DOCUMENT_TASKS.complete(
            task_id,
            log="完成！已提取并拆分 Word 题目，请优先检查带“公式待核对”标记的内容。" if review_count else "完成！已提取并拆分全部 Word 题目卡片。",
            data=final_questions,
            document_type="docx",
            diagnostics=diagnostics,
            temp_assets=list(temp_assets),
        )
    except TaskCancelled:
        _delete_task_temp_assets(temp_assets)
    except Exception as ex:
        _delete_task_temp_assets(temp_assets)
        _main.DOCUMENT_TASKS.fail(
            task_id,
            f"Word 试卷拆解失败: {str(ex)}",
            document_type="docx",
        )


@router.post("/api/upload/docx-task")
def upload_docx_task(
    file: UploadFile = File(...),
    generate_answers: str = Form("false"),
    paper_title: Optional[str] = Form(None),
):
    try:
        generate_answers_bool = generate_answers.lower() in ("true", "1", "yes")
        
        # 验证文件扩展名
        filename = file.filename or ""
        if not filename.lower().endswith(".docx"):
            return JSONResponse(
                content={"status": "error", "message": "上传文件格式不正确，必须为 .docx 格式！"},
                status_code=400
            )
            
        try:
            content = read_stream_limited(file.file, _main.MAX_PDF_BYTES)
        except UploadTooLargeError:
            return JSONResponse(
                content={"status": "error", "message": "Word 文件过大，请上传 30MB 以内的试卷文件！"},
                status_code=413
            )
        if len(content) < 4 or content[:4] != b"PK\x03\x04":
            return JSONResponse(
                content={"status": "error", "message": "文件内容不是有效的 Word DOCX 压缩包！"},
                status_code=400,
            )
            
        task_id = str(uuid.uuid4())
        
        _main.DOCUMENT_TASKS.create(
            task_id,
            status="pending",
            log="Word 任务已排队，正在准备安全提取公式与配图...",
            document_type="docx",
            temp_assets=[],
        )
        try:
            _main.DOCUMENT_TASKS.submit(
                task_id,
                run_docx_parsing_task,
                task_id,
                content,
                filename,
                generate_answers_bool,
                paper_title,
            )
        except TaskQueueFull as exc:
            _main.DOCUMENT_TASKS.remove(task_id)
            return JSONResponse(
                content={"status": "error", "message": str(exc)},
                status_code=429,
            )
        
        return {
            "status": "success",
            "task_id": task_id
        }
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"创建 Word 解析任务失败: {str(e)}"},
            status_code=500
        )


@router.get("/api/tasks/latest")
def get_latest_document_task(document_type: str = "pdf"):
    """Return the most recently completed task so the UI can recover results."""
    task = _main.DOCUMENT_TASKS.latest(
        document_type=document_type or None,
        statuses={"completed"},
    )
    if not task:
        return JSONResponse(
            content={"status": "error", "message": "没有可找回的已完成任务。"},
            status_code=404,
        )
    return task


@router.get("/api/tasks/{task_id}/status")
def get_pdf_task_status(task_id: str):
    task = _main.DOCUMENT_TASKS.snapshot(task_id)
    if not task:
        return JSONResponse(
            content={"status": "error", "message": "未找到对应的任务 ID！"},
            status_code=404
        )
    return task


@router.post("/api/tasks/{task_id}/cancel")
def cancel_pdf_task(task_id: str):
    current = _main.DOCUMENT_TASKS.snapshot(task_id)
    if current is None:
        return JSONResponse(
            content={"status": "error", "message": "未找到对应的任务 ID！"},
            status_code=404
        )

    current_status = current.get("status")
    if current_status in {"completed", "error"}:
        # Never delete assets belonging to a task that already produced a
        # result.  The previous behavior reported success and could remove
        # completed PDF crop files after a late ESC/click.
        return JSONResponse(
            content={
                "status": "error",
                "message": "任务已结束，无法再中止。",
                "task_status": current_status,
            },
            status_code=409,
        )
    if current_status == "cancelled":
        return {
            "status": "success",
            "message": "任务已中止。",
            "task_status": "cancelled",
        }

    task = _main.DOCUMENT_TASKS.cancel(task_id)
    if task is None:  # Defensive race guard; records are not normally removed here.
        return JSONResponse(
            content={"status": "error", "message": "未找到对应的任务 ID！"},
            status_code=404,
        )
    if task.get("status") != "cancelled":
        # The worker may have completed between the snapshot above and the
        # atomic cancel call.  Never delete assets from that completed result.
        return JSONResponse(
            content={
                "status": "error",
                "message": "任务已结束，无法再中止。",
                "task_status": task.get("status"),
            },
            status_code=409,
        )
    removed = _delete_task_temp_assets(list(task.get("temp_assets", [])))
    return {
        "status": "success",
        "message": f"任务已成功中止，已清理 {removed} 个临时资产",
        "task_status": "cancelled",
    }


@router.post("/api/ai/clear-temp-crops")
def clear_temp_crops(payload: dict):
    """物理删除传递来的未入库临时裁剪图片路径"""
    try:
        paths = payload.get("paths", [])
        if not isinstance(paths, list):
            raise ValueError("paths 必须是数组。")
        removed_count = _delete_task_temp_assets(paths)
        return {"status": "success", "message": f"成功物理清除 {removed_count} 张废弃插图图片。"}
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"清理临时插图出错: {str(e)}"},
            status_code=500
        )


@router.get("/api/paper/questions")
def get_paper_questions(ids: str = "", db: Session = Depends(get_db)):
    """获取指定 ID 列表的完整题目数据（组卷试题篮批量拉取）"""
    if not ids:
        return {"status": "success", "data": []}
    try:
        id_list = [int(i.strip()) for i in ids.split(",") if i.strip().isdigit()]
        if not id_list:
            return {"status": "success", "data": []}
        questions = db.query(Question).filter(Question.id.in_(id_list)).all()
        q_map = {q.id: _decorate_topic_field(q.to_dict()) for q in questions}
        result = [q_map[qid] for qid in id_list if qid in q_map]
        return {"status": "success", "data": result}
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)


@router.post("/api/paper/save")
def save_paper(payload: dict, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """保存排版好的试卷，自增被选中题目的 usage_count"""
    try:
        if not isinstance(payload, dict):
            raise ValueError("试卷数据格式不正确。")
        title = str(payload.get("title", "未命名试卷")).strip()
        subtitle = str(payload.get("subtitle", "")).strip()
        paper_type = payload.get("paper_type", "exam")
        questions_payload = payload.get("questions", [])

        if not title or len(title) > 200 or len(subtitle) > 200:
            raise ValueError("试卷标题不能为空，且标题与副标题均不能超过 200 字。")
        if paper_type not in {"exam", "quiz", "exam_19"}:
            raise ValueError("不支持的试卷模板。")
        if not isinstance(questions_payload, list) or not questions_payload:
            raise ValueError("试卷中至少需要包含一道题目。")
        if len(questions_payload) > 200:
            raise ValueError("单份试卷不能超过 200 道题。")

        normalized_items = []
        seen_question_ids = set()
        for item in questions_payload:
            if not isinstance(item, dict):
                raise ValueError("试卷题目数据格式不正确。")
            question_id = int(item.get("id"))
            score = int(item.get("score", 5))
            if question_id <= 0 or score < 0 or score > 100:
                raise ValueError("题目 ID 或分值不在有效范围内。")
            if question_id in seen_question_ids:
                raise ValueError("同一道题不能在一份试卷中重复出现。")
            seen_question_ids.add(question_id)
            normalized_items.append((question_id, score))

        questions = db.query(Question).filter(
            Question.id.in_(seen_question_ids)
        ).all()
        question_map = {question.id: question for question in questions}
        missing_ids = sorted(seen_question_ids - set(question_map))
        if missing_ids:
            raise ValueError("试卷中包含已删除或不存在的题目。")

        total_score = sum(score for _question_id, score in normalized_items)
        
        meta = payload.get("metadata", {})
        if not isinstance(meta, dict):
            meta = {}
        meta["show_secret"] = payload.get("show_secret", True)
        meta["show_notice"] = payload.get("show_notice", True)

        paper = Paper(
            title=title,
            subtitle=subtitle,
            paper_type=paper_type,
            total_score=total_score,
            metadata_json=json.dumps(meta)
        )
        db.add(paper)
        db.flush()
        
        for idx, (qid, score) in enumerate(normalized_items):
            pq = PaperQuestion(
                paper_id=paper.id,
                question_id=qid,
                order_index=idx + 1,
                score=score
            )
            db.add(pq)
            question = question_map[qid]
            question.usage_count = (question.usage_count or 0) + 1
            pq.snapshot_json = json.dumps(question.to_dict(), ensure_ascii=False)
                
        db.commit()
        schedule_database_export(background_tasks, operation="save_paper")
        return {"status": "success", "message": "试卷保存成功！", "paper_id": paper.id}
    except (TypeError, ValueError) as e:
        db.rollback()
        return JSONResponse(
            content={"status": "error", "message": str(e)}, status_code=400
        )
    except Exception as e:
        db.rollback()
        return JSONResponse(content={"status": "error", "message": f"保存试卷失败: {str(e)}"}, status_code=500)


@router.get("/api/papers")
def list_papers(db: Session = Depends(get_db)):
    """获取所有历史试卷列表"""
    try:
        from sqlalchemy import func

        rows = (
            db.query(Paper, func.count(PaperQuestion.id))
            .outerjoin(PaperQuestion, PaperQuestion.paper_id == Paper.id)
            .group_by(Paper.id)
            .order_by(Paper.created_at.desc())
            .all()
        )
        result = []
        for p, q_count in rows:
            d = p.to_dict()
            d["question_count"] = int(q_count)
            result.append(d)
        return {"status": "success", "data": result}
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)


@router.get("/api/papers/{paper_id}")
def get_paper_detail(paper_id: int, db: Session = Depends(get_db)):
    """获取单张试卷的详细信息及关联题目列表（用于一键载入）"""
    try:
        paper = db.query(Paper).filter(Paper.id == paper_id).first()
        if not paper:
            return JSONResponse(content={"status": "error", "message": "试卷不存在"}, status_code=404)
        
        rows = (
            db.query(PaperQuestion, Question)
            .outerjoin(Question, Question.id == PaperQuestion.question_id)
            .filter(PaperQuestion.paper_id == paper_id)
            .order_by(PaperQuestion.order_index.asc())
            .all()
        )

        questions_list = []
        for paper_question, question in rows:
            if question is not None:
                q_dict = _decorate_topic_field(question.to_dict())
                qid = question.id
            else:
                snap = paper_question.snapshot_json
                if not snap or snap in ("", "{}"):
                    continue
                try:
                    snap_dict = json.loads(snap)
                except Exception:
                    continue
                q_dict = _decorate_topic_field(snap_dict)
                qid = snap_dict.get("id")
            questions_list.append({
                "id": qid,
                "score": paper_question.score,
                "question": q_dict,
            })
                
        result = paper.to_dict()
        result["questions"] = questions_list
        return {"status": "success", "data": result}
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)


@router.delete("/api/papers/{paper_id}")
def delete_paper(paper_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """删除指定的历史试卷，并同步扣减关联题目的 usage_count"""
    try:
        paper = db.query(Paper).filter(Paper.id == paper_id).first()
        if not paper:
            return JSONResponse(content={"status": "error", "message": "试卷不存在"}, status_code=404)
            
        pqs = db.query(PaperQuestion).filter(PaperQuestion.paper_id == paper_id).all()
        reference_counts = {}
        for paper_question in pqs:
            reference_counts[paper_question.question_id] = (
                reference_counts.get(paper_question.question_id, 0) + 1
            )
        questions = db.query(Question).filter(
            Question.id.in_(reference_counts)
        ).all()
        for question in questions:
            if question.usage_count:
                question.usage_count = max(
                    0,
                    question.usage_count - reference_counts.get(question.id, 0),
                )
                
        db.query(PaperQuestion).filter(PaperQuestion.paper_id == paper_id).delete()
        db.delete(paper)
        db.commit()
        schedule_database_export(background_tasks, operation="delete_paper")
        return {"status": "success", "message": "试卷记录已成功删除"}
    except Exception as e:
        db.rollback()
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)


def _build_paper_questions_data(questions_input, db):
    """Resolve paper export items from live questions, falling back to the
    frozen paper_questions snapshot when a referenced question was deleted."""

    q_ids = [int(q.get("id")) for q in questions_input if q.get("id")]
    questions_db = db.query(Question).filter(Question.id.in_(q_ids)).all()
    q_map = {q.id: _decorate_topic_field(q.to_dict()) for q in questions_db}
    questions_data = []
    for item in questions_input:
        qid = int(item.get("id"))
        if qid in q_map:
            q_dict = dict(q_map[qid])
        else:
            snap_row = (
                db.query(PaperQuestion.snapshot_json)
                .filter(PaperQuestion.question_id == qid)
                .filter(PaperQuestion.snapshot_json.isnot(None))
                .filter(PaperQuestion.snapshot_json != "{}")
                .order_by(PaperQuestion.id.desc())
                .first()
            )
            if not snap_row:
                continue
            try:
                q_dict = _decorate_topic_field(json.loads(snap_row[0]))
            except Exception:
                continue
        if item.get("figure_align"):
            q_dict["figure_align"] = item.get("figure_align")
        # 入库 LaTeX 图片还原为 Markdown 图：导出侧排版逻辑（尾随图右置/居中、
        # 表格内 adjustsbox）全部按 Markdown 图识别，不还原则 figure_align 失效。
        # 老数据（仍存 Markdown 图）无 includegraphics，原样返回，幂等安全。
        if isinstance(q_dict.get("content"), str):
            q_dict["content"] = restore_storage_images_to_markdown(q_dict["content"])
        if isinstance(q_dict.get("answer_markdown"), str):
            q_dict["answer_markdown"] = restore_storage_images_to_markdown(
                q_dict["answer_markdown"]
            )
        q_item = {"question": q_dict, "score": int(item.get("score", 5))}
        if item.get("solution_space"):
            q_item["solution_space"] = item.get("solution_space")
        questions_data.append(q_item)
    return questions_data


@router.post("/api/paper/export/tex")
def export_paper_tex(payload: dict, db: Session = Depends(get_db)):
    """导出 LaTeX 源码 ZIP 压缩包"""
    try:
        title = payload.get("title", "2026年高中数学模拟考试试卷")
        subtitle = payload.get("subtitle", "")
        paper_type = payload.get("paper_type", "exam")
        show_secret = payload.get("show_secret", True)
        show_notice = payload.get("show_notice", True)
        questions_input = payload.get("questions", [])
        
        questions_data = _build_paper_questions_data(questions_input, db)
                
        tex_main = build_latex_document(title, subtitle, paper_type, questions_data, include_answers=False, show_secret=show_secret, show_notice=show_notice)
        tex_ans = build_latex_document(title + " (参考答案与解析)", subtitle, paper_type, questions_data, include_answers=True, show_secret=show_secret, show_notice=show_notice)
        
        if paper_type == "exam_19":
            tex_answer_sheet = build_answer_sheet_latex(title, subtitle, questions_data)
        else:
            tex_answer_sheet = None

        image_paths = collect_referenced_images(questions_data, _main.UPLOAD_DIR, _main.UPLOAD_DIR_REL)
        zip_bytes = create_tex_zip_package(title, tex_main, tex_ans, image_paths, answer_sheet_tex=tex_answer_sheet)
        
        from urllib.parse import quote
        safe_title = re.sub(r'[/\\?%*:|"<>]', '_', title.strip()) or "试卷"
        encoded_filename = quote(f"{safe_title}.zip")
        return Response(content=zip_bytes, media_type="application/zip", headers={
            "Content-Disposition": f"attachment; filename=\"paper_export.zip\"; filename*=utf-8''{encoded_filename}"
        })
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"生成 LaTeX 源码失败: {str(e)}"}, status_code=500)


@router.post("/api/paper/export/bundle")
def export_paper_bundle(payload: dict, db: Session = Depends(get_db)):
    """一键导出合并全套 Zip 压缩包（包含 LaTeX 源码、相关插图以及已编译好的 PDF）"""
    try:
        title = payload.get("title", "2026年高中数学模拟考试试卷")
        subtitle = payload.get("subtitle", "")
        paper_type = payload.get("paper_type", "exam")
        show_secret = payload.get("show_secret", True)
        show_notice = payload.get("show_notice", True)
        questions_input = payload.get("questions", [])
        
        questions_data = _build_paper_questions_data(questions_input, db)
                
        tex_main = build_latex_document(title, subtitle, paper_type, questions_data, include_answers=False, show_secret=show_secret, show_notice=show_notice)
        tex_ans = build_latex_document(title + " (参考答案与解析)", subtitle, paper_type, questions_data, include_answers=True, show_secret=show_secret, show_notice=show_notice)
        
        if paper_type == "exam_19":
            tex_answer_sheet = build_answer_sheet_latex(title, subtitle, questions_data)
        else:
            tex_answer_sheet = None

        image_paths = collect_referenced_images(questions_data, _main.UPLOAD_DIR, _main.UPLOAD_DIR_REL)

        # Pre-compile PDFs
        main_pdf_bytes, _ = _main.compile_tex_to_pdf(tex_main, image_paths)
        ans_pdf_bytes, _ = _main.compile_tex_to_pdf(tex_ans, image_paths)
        if paper_type == "exam_19" and tex_answer_sheet:
            answer_sheet_pdf_bytes, _ = _main.compile_tex_to_pdf(tex_answer_sheet, image_paths)
        else:
            answer_sheet_pdf_bytes = None

        zip_bytes = create_full_bundle_zip_package(
            title, tex_main, tex_ans, image_paths,
            answer_sheet_tex=tex_answer_sheet,
            main_pdf_bytes=main_pdf_bytes,
            ans_pdf_bytes=ans_pdf_bytes,
            answer_sheet_pdf_bytes=answer_sheet_pdf_bytes
        )
        
        from urllib.parse import quote
        safe_title = re.sub(r'[/\\?%*:|"<>]', '_', title.strip()) or "试卷"
        filename = f"{safe_title}_全套归档.zip"
        encoded_filename = quote(filename)
        return Response(content=zip_bytes, media_type="application/zip", headers={
            "Content-Disposition": f"attachment; filename=\"paper_bundle.zip\"; filename*=utf-8''{encoded_filename}"
        })
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"生成全套合并包失败: {str(e)}"}, status_code=500)


def explain_latex_compile_error(log_text: str, tex_content: str) -> dict:
    """Explain one compile failure locally, then enrich it with the parse model."""
    diagnostic = build_local_latex_diagnostic(log_text, tex_content)
    parse_model = os.getenv("PREFER_PARSE_MODEL") or os.getenv(
        "DEEPSEEK_PARSE_MODEL", "deepseek-flash"
    )
    provider = _main.resolve_text_provider(parse_model)
    if not provider.api_key:
        diagnostic["ai_note"] = "试卷拆解模型未配置，当前显示本地诊断结果。"
        return diagnostic

    system_prompt, user_prompt = build_latex_error_explanation_prompts(diagnostic)
    payload = {
        "model": provider.model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
        "max_tokens": 1200,
    }
    payload = inject_reasoning_effort(payload, provider.reasoning_effort)
    # LaTeX 诊断是结构化 JSON 任务：DeepSeek 关闭思考。
    payload = apply_deepseek_thinking_policy(
        payload,
        model_name=provider.model_name,
        api_base=provider.api_base or "",
        provider_code=provider.provider_code,
        task="latex_diagnostic",
        thinking_enabled=False,
    )
    payload = apply_bailian_thinking_policy(
        payload,
        provider_code=provider.provider_code,
        model_name=provider.model_name,
        task="latex_diagnostic",
    )

    try:
        response = _main.post_chat_completion(
            provider,
            payload,
            timeout=45,
            provider_name=provider.provider_label,
        )
        raw_text = response.json()["choices"][0]["message"]["content"].strip()
        ai_value = parse_ai_json(raw_text)
        return merge_ai_latex_diagnostic(diagnostic, ai_value)
    except Exception:
        diagnostic["ai_note"] = "AI 暂时无法解释该错误，当前显示本地诊断结果。"
        return diagnostic


@router.post("/api/paper/export/pdf")
def export_paper_pdf(payload: dict, db: Session = Depends(get_db)):
    """在线静默编译生成高清 PDF"""
    try:
        title = payload.get("title", "2026年高中数学模拟考试试卷")
        subtitle = payload.get("subtitle", "")
        paper_type = payload.get("paper_type", "exam_19")
        target = payload.get("target", "paper")  # "paper" or "sheet"
        include_answers = payload.get("include_answers", False)
        show_secret = payload.get("show_secret", True)
        show_notice = payload.get("show_notice", True)
        questions_input = payload.get("questions", [])
        
        questions_data = _build_paper_questions_data(questions_input, db)
                
        if target == "sheet":
            tex_content = build_answer_sheet_latex(title, subtitle, questions_data)
        else:
            tex_content = build_latex_document(title, subtitle, paper_type, questions_data, include_answers=include_answers, show_secret=show_secret, show_notice=show_notice)

        image_paths = collect_referenced_images(questions_data, _main.UPLOAD_DIR, _main.UPLOAD_DIR_REL)
        pdf_bytes, log_or_err = _main.compile_tex_to_pdf(tex_content, image_paths)
        
        if pdf_bytes:
            filename = f"sheet_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf" if target == "sheet" else f"paper_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            return Response(content=pdf_bytes, media_type="application/pdf", headers={
                "Content-Disposition": f'inline; filename="{filename}"'
            })
        else:
            diagnostic = _main.explain_latex_compile_error(log_or_err, tex_content)
            return JSONResponse(
                content={
                    "status": "error",
                    "message": diagnostic.get("summary", "PDF 编译失败"),
                    "diagnostic": diagnostic,
                },
                status_code=400,
            )
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"编译 PDF 异常: {str(e)}"}, status_code=500)


@router.post("/api/paper/export/word")
def export_paper_word(payload: dict, db: Session = Depends(get_db)):
    """导出包含试卷正文与含答案解析两个 Word 文件的 ZIP 压缩包。"""
    try:
        title = payload.get("title", "2026年高中数学模拟考试试卷")
        subtitle = payload.get("subtitle", "")
        paper_type = payload.get("paper_type", "exam")
        show_secret = payload.get("show_secret", True)
        show_notice = payload.get("show_notice", True)
        questions_input = payload.get("questions", [])
        as_single_docx = bool(payload.get("as_single_docx", False))
        include_answers = bool(payload.get("include_answers", False))

        questions_data = _build_paper_questions_data(questions_input, db)

        if not questions_data:
            return JSONResponse(
                content={"status": "error", "message": "卷面为空，无法导出 Word。"},
                status_code=400,
            )

        from urllib.parse import quote
        safe_title = re.sub(r'[/\\?%*:|"<>]', "_", title.strip()) or "试卷"

        if as_single_docx:
            docx_bytes, diagnostics = build_word_document(
                title,
                subtitle,
                paper_type,
                questions_data,
                include_answers=include_answers,
                show_secret=show_secret,
                show_notice=show_notice,
                uploads_dir=_main.UPLOAD_DIR,
            )
            suffix = "_含答案与解析" if include_answers else ""
            filename = f"{safe_title}{suffix}.docx"
            encoded_filename = quote(filename)
            return Response(
                content=docx_bytes,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={
                    "Content-Disposition": f"attachment; filename=\"paper.docx\"; filename*=utf-8''{encoded_filename}",
                    "X-Word-Native-Formulas": str(diagnostics.get("native_formulas", 0)),
                    "X-Word-Fallback-Formulas": str(diagnostics.get("fallback_formulas", 0)),
                    "X-Word-Failed-Formulas": str(diagnostics.get("failed_formulas", 0)),
                    "X-Word-Missing-Images": str(diagnostics.get("missing_images", 0)),
                    "X-Word-Answer-Card-Omitted": "1" if diagnostics.get("answer_card_omitted") else "0",
                },
            )

        # Default: build clean student docx and full teacher docx with answers into a ZIP bundle
        main_docx, main_diag = build_word_document(
            title,
            subtitle,
            paper_type,
            questions_data,
            include_answers=False,
            show_secret=show_secret,
            show_notice=show_notice,
            uploads_dir=_main.UPLOAD_DIR,
        )
        ans_docx, ans_diag = build_word_document(
            title,
            subtitle,
            paper_type,
            questions_data,
            include_answers=True,
            show_secret=show_secret,
            show_notice=show_notice,
            uploads_dir=_main.UPLOAD_DIR,
        )

        zip_bytes = create_word_bundle_zip(title, main_docx, ans_docx)
        filename = f"{safe_title}_Word打包.zip"
        encoded_filename = quote(filename)
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename=\"paper_word_bundle.zip\"; filename*=utf-8''{encoded_filename}",
                "X-Word-Native-Formulas": str(main_diag.get("native_formulas", 0) + ans_diag.get("native_formulas", 0)),
                "X-Word-Fallback-Formulas": str(main_diag.get("fallback_formulas", 0) + ans_diag.get("fallback_formulas", 0)),
                "X-Word-Failed-Formulas": str(main_diag.get("failed_formulas", 0) + ans_diag.get("failed_formulas", 0)),
                "X-Word-Missing-Images": str(main_diag.get("missing_images", 0) + ans_diag.get("missing_images", 0)),
                "X-Word-Answer-Card-Omitted": "1" if main_diag.get("answer_card_omitted") else "0",
            },
        )
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"生成 Word 试卷包失败: {str(e)}"},
            status_code=500,
        )
