# -*- coding: utf-8 -*-
"""AI 能力域路由：OCR、AI 解题、AI 分类、TikZ 生成/纠正/渲染、整页 OCR 与插图裁图。

2026-09-17 自 main.py 下沉（AST 精确源区间重建）。main.py 保留同名薄包装，
供既有 patch("main.xxx") / from main import xxx 测试接缝使用。
凡测试会 patch 的依赖（post_chat_completion、resolve_*、DOCUMENT_TASKS、
UPLOAD_DIR/TMP_UPLOAD_DIR 等）一律经 `import main as _main` 运行时查找，
避免导入期冻结取值（见 .workbuddy/memory 的「_tmp_upload_dir 补丁陷阱」）。
"""

from PIL import Image
from fastapi import APIRouter, File, Form, HTTPException, Header, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from mathbank import illustration_crop
from mathbank.ai_providers import MultimodalProviderConfig, apply_bailian_thinking_policy, apply_deepseek_thinking_policy, inject_reasoning_effort, is_deepseek_thinking_capable
from mathbank.asset_security import AssetSecurityError, InvalidImageError, UploadTooLargeError, normalize_raster_image, normalize_upload_asset_reference, read_stream_limited, resolve_upload_asset
from mathbank.auth import LOCAL_TOKEN
from mathbank.database import engine
from mathbank.math_markdown import compact_solution_markdown, normalize_question_math_markdown
from mathbank.paper_helper import build_restricted_tex_environment
from mathbank.prompts import COMMON_OCR_PROMPT, ILLUSTRATION_BOX_PROMPT, PDF_PAGE_ILLUSTRATION_BOX_PROMPT, build_ai_solve_prompts, build_classification_system_prompt, build_tikz_correction_prompt, build_tikz_draw_prompt
from mathbank.question_types import detect_structured_question_form, normalize_ai_question_form
from pathlib import Path
from typing import Optional
import io
import json
import os
import re
import requests
import secrets
import shutil
import uuid

import main as _main  # noqa: E402  测试接缝：延迟绑定

router = APIRouter()


def auto_crop_image(image):
    try:
        from PIL import ImageOps, ImageStat
        # 估算灰度均值，判断主色调（暗色背景还是亮色背景）
        gray = image.convert("L")
        stat = ImageStat.Stat(gray)
        mean_val = stat.mean[0]
        
        if mean_val < 100:  # 偏暗，可能含有大面积黑边背景
            bbox = image.getbbox()
            if bbox:
                # 留出 8 像素的边距以防文字贴边影响识别
                w, h = image.size
                left = max(0, bbox[0] - 8)
                upper = max(0, bbox[1] - 8)
                right = min(w, bbox[2] + 8)
                lower = min(h, bbox[3] + 8)
                return image.crop((left, upper, right, lower))
        elif mean_val > 220:  # 偏亮，可能含有大面积白边背景
            inverted = ImageOps.invert(image.convert("RGB"))
            bbox = inverted.getbbox()
            if bbox:
                w, h = image.size
                left = max(0, bbox[0] - 8)
                upper = max(0, bbox[1] - 8)
                right = min(w, bbox[2] + 8)
                lower = min(h, bbox[3] + 8)
                return image.crop((left, upper, right, lower))
    except Exception as e:
        print(f"[Auto Crop] 裁剪失败，返回原图. Error: {str(e)}")
    return image



def ocr_via_provider(
    image_path: str,
    provider: MultimodalProviderConfig,
    include_illustration_box: bool = False,
    illustration_box_prompt: str = None,
) -> str:
    """Use one resolved multimodal provider for formula and text OCR.

    ``illustration_box_prompt`` selects which box-instruction paragraph is
    appended when ``include_illustration_box`` is set: the single-question
    wording by default, or the whole-page wording for PDF page OCR.
    """
    import base64

    print(
        f"[OCR Flow] 正在向 {provider.provider_label} 提交多模态识别任务: "
        f"{image_path} (模型: {provider.model_name})..."
    )
    try:
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"读取并对图片进行 Base64 编码失败: {str(e)}")

    prompt = COMMON_OCR_PROMPT
    if include_illustration_box:
        prompt += illustration_box_prompt or ILLUSTRATION_BOX_PROMPT

    payload = {
        "model": provider.model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{encoded_string}"
                        }
                    }
                ]
            }
        ],
        "stream": False
    }

    payload = inject_reasoning_effort(payload, provider.reasoning_effort)
    payload = apply_bailian_thinking_policy(
        payload,
        provider_code=provider.provider_code,
        model_name=provider.model_name,
        task="ocr",
    )

    timeout = 240
    # Chat-completion POSTs are not idempotent: a read timeout can happen after
    # the provider has accepted (and billed) the request.  Do not automatically
    # send the same image two or three times.  The shared transport still
    # retries a connection-establishment failure where no response was read.
    response = _main.post_chat_completion(
        provider,
        payload,
        timeout=timeout,
        check_status=False,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"{provider.provider_label} API 识别失败: HTTP {response.status_code}"
        )

    res_json = response.json()
    try:
        choices = res_json.get("choices", [])
        if choices and len(choices) > 0:
            content = choices[0].get("message", {}).get("content", "")
            return content.strip()
        else:
            raise RuntimeError(
                f"{provider.provider_label} 返回的数据中未包含 Choices 结果。"
            )
    except Exception as e:
        raise RuntimeError(
            f"解析 {provider.provider_label} 响应数据失败: {str(e)}"
        )



def extract_tikz_source(ai_message: str) -> str:
    """Extract one complete TikZ environment from a model response."""

    message = str(ai_message or "").strip()
    match = re.search(
        r"\\begin\s*\{\s*tikzpicture\s*\}.*?\\end\s*\{\s*tikzpicture\s*\}",
        message,
        re.DOTALL | re.IGNORECASE,
    )
    if match:
        return match.group(0).strip()

    match_block = re.search(
        r"```(?:latex|tex)?\s*(.*?)```",
        message,
        re.DOTALL | re.IGNORECASE,
    )
    if match_block:
        code = match_block.group(1).strip()
        if code and "tikzpicture" not in code.lower():
            return f"\\begin{{tikzpicture}}\n{code}\n\\end{{tikzpicture}}"

    raise RuntimeError("绘图模型未返回完整的 tikzpicture 源码。")



def request_tikz_completion(provider, content_payload, *, timeout: int = 120) -> str:
    """Send one TikZ model request with the shared reasoning and parser policy."""

    payload = {
        "model": provider.model_name,
        "messages": [{"role": "user", "content": content_payload}],
        "stream": False,
    }
    payload = inject_reasoning_effort(payload, provider.reasoning_effort)
    payload = apply_bailian_thinking_policy(
        payload,
        provider_code=provider.provider_code,
        model_name=provider.model_name,
        task="draw",
    )
    response = _main.post_chat_completion(
        provider,
        payload,
        timeout=timeout,
        check_status=False,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"{provider.provider_label} 绘图接口返回 HTTP {response.status_code}"
        )
    choices = response.json().get("choices", [])
    if not choices:
        raise RuntimeError(f"{provider.provider_label} 绘图接口未返回 choices。")
    ai_message = choices[0].get("message", {}).get("content", "")
    return extract_tikz_source(ai_message)



def draw_tikz_via_high_model(
    image_path: Optional[str],
    prefer_draw: str,
    latex_content: Optional[str] = None,
    *,
    instruction: str = "",
    existing_tikz: str = "",
    require_image_support: bool = False,
    image_kind: str = "",
) -> Optional[str]:
    """使用指定的高级绘图模型（多模态或纯文本自适应）生成 TikZ 代码。"""
    import base64
    provider = _main.resolve_draw_provider(prefer_draw)
    if not provider.api_key:
        print(
            f"[High Model Draw] 未配置 {provider.credential_label}，降级跳过。"
        )
        return None

    has_reference_image = bool(image_path)
    if has_reference_image and require_image_support and not provider.supports_image_input:
        raise RuntimeError(
            f"当前绘图模型 {provider.model_name} 不支持参考图输入，"
            "请在 API 设置中选择支持图像的 TikZ 绘图模型。"
        )
    use_image_input = has_reference_image and provider.supports_image_input

    if use_image_input:
        # 多模态图文输入模式
        try:
            with open(image_path, "rb") as f:
                encoded_image = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            print(f"[High Model Draw] 读取裁剪小图 Base64 失败: {str(e)}")
            return None
            
        suffix = Path(image_path).suffix.lower()
        image_media_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(suffix, "image/png")
        prompt = build_tikz_draw_prompt(
            latex_content,
            multimodal=True,
            instruction=instruction,
            existing_tikz=existing_tikz,
            image_kind=image_kind,
        )
        
        content_payload = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image_media_type};base64,{encoded_image}"
                }
            }
        ]
    else:
        # 纯文本推理模式。带有视觉能力的模型也可以在没有参考图时走此分支。
        if not any((latex_content, instruction, existing_tikz)):
            print("[High Model Draw] 绘图模型未获得任何可用输入，跳过。")
            return None
            
        prompt = build_tikz_draw_prompt(
            latex_content,
            multimodal=False,
            instruction=instruction,
            existing_tikz=existing_tikz,
        )
        content_payload = prompt

    try:
        return request_tikz_completion(
            provider,
            content_payload,
            timeout=120,
        )
    except Exception as e:
        print(f"[High Model Draw Error] 大模型请求发生异常: {str(e)}")
    return None



@router.post("/api/ocr")
def ocr_formula(
    file: UploadFile = File(...),
    engine: str = Form(None),
    skip_tikz: bool = Form(False)
):
    import re
    temp_filepath = None
    try:
        # OCR route is synchronous and runs in FastAPI's worker pool.  Stream
        # only up to the endpoint cap, then fully decode/re-encode the image.
        file_bytes = read_stream_limited(file.file, _main.MAX_OCR_IMAGE_BYTES)
        normalized = normalize_raster_image(file_bytes)
        image = Image.open(io.BytesIO(normalized.data)).convert("RGB")
        
        # 1. 运行自适应图像去噪/自动切边预处理
        image = auto_crop_image(image)
        
        # 将裁剪后的图片保存为持久化 OCR 文件，未来可作为题目配图
        filename = f"ocr_original_{uuid.uuid4().hex[:12]}.png"
        temp_filepath = os.path.join(_main.UPLOAD_DIR, filename)
        image.save(temp_filepath, format="PNG")
        
        # 确定调用的具体引擎。
        # 临时传参 engine 取值: default, siliconflow, simpletex, ali_bailian
        if not engine or engine == "default":
            engine = os.getenv("OCR_PREFER_ENGINE", "siliconflow")
            
        print(f"[OCR Flow] 当前决策分配识图引擎: {engine}")
        
        latex_content = None
        confidence = 0.95
        provider = ""

        known_ocr_engines = {
            "siliconflow",
            "ali_bailian",
            "bailian",
            "zhongzhan",
            "zhongzhan_gpt",
            "zhongzhan_claude",
        }
        if engine in known_ocr_engines:
            ocr_provider = _main.resolve_ocr_provider(engine)
            if ocr_provider.api_key and ocr_provider.api_key.strip():
                try:
                    latex_content = _main.ocr_via_provider(
                        temp_filepath,
                        ocr_provider,
                        include_illustration_box=True,
                    )
                    confidence = 0.99
                    provider = (
                        f"{ocr_provider.provider_label} "
                        f"({ocr_provider.model_name})"
                    )
                except Exception as e:
                    print(
                        f"[{ocr_provider.provider_label} 识别失败] "
                        f"发生异常: {str(e)}"
                    )
            else:
                print(
                    f"[OCR Flow Warning] 未配置 {ocr_provider.credential_label}，"
                    "当前识图引擎无法启动！"
                )

        if not latex_content:
            raise RuntimeError("当前分配的识图引擎均无法启动或识别失败。请检查右上角「API设置」中是否正确配置了 硅基流动(SiliconFlow) 或是 阿里百炼(Alibaba Bailian) 的 API Key。")

        # 成功，返回且进一步清洗
        if latex_content:
            # 过滤干扰字符
            latex_content = latex_content.replace("\\,", "").replace("\\!", "")
            # 自动清洗规范化下划线/连续划线/任何 \underline 变体为标准的 \fillin 宏
            latex_content = _main.normalize_fillin_macro(latex_content)

        # ----------------- 双阶段多模态识图与高级 TikZ 绘图模型联动 -----------------
        tikz_code_from_high_model = None
        tikz_image_path = None
        
        if latex_content:
            import re
            # 提取可能由默认模型标注的示意图 Bounding Box 标记
            box_match = re.search(r"\[ILLUSTRATION_BOX:\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\]", latex_content, re.IGNORECASE)
            if box_match:
                # 第一步先确保擦除标记，防止乱入题干文本框
                latex_content = re.sub(r"\[ILLUSTRATION_BOX:.*?\]", "", latex_content).strip()
                
                if not skip_tikz:
                    try:
                        # 不再执行物理分割裁剪，直接将整张原始题目截图发送给高级视觉绘图模型进行图形分析与重画
                        prefer_draw = os.getenv("PREFER_DRAW_MODEL", "Qwen/Qwen3-VL-32B-Instruct")
                        print(f"[Illustration Draw] 检测到插图标记，直接将整张原图送往高级模型 {prefer_draw} 进行 TikZ 解析绘图...")
                        
                        tikz_code_from_high_model = _main.draw_tikz_via_high_model(
                            temp_filepath, # 传入整图
                            prefer_draw,
                            latex_content=latex_content
                        )
                    except Exception as draw_err:
                        print(f"[Illustration Draw Fail] 高级多模态模型整图分析绘图失败: {str(draw_err)}")
                else:
                    print("[Illustration Draw] 检测到插图标记，但由于已勾选跳过，故未调用高级绘图模型进行 TikZ 绘制")
            else:
                # 剔除可能存在的由于大模型幻觉或者部分输出造成的残缺标记
                latex_content = re.sub(r"\[ILLUSTRATION_BOX:.*?\]", "", latex_content).strip()

            # Remove OCR protocol markers before repairing naked math. Otherwise
            # the underscore in ILLUSTRATION_BOX can be mistaken for a subscript
            # and leave behind an empty ``$$`` pair after marker cleanup.
            latex_content = normalize_question_math_markdown(latex_content)

        # 如果高级模型成功生成了 TikZ 代码，我们在后台自动进行编译预览，并格式化追加到 latex 文本中！
        if tikz_code_from_high_model:
            try:
                print(f"[Illustration Draw] 高级绘图模型成功输出 TikZ 源码！正在开始编译为预览图...")
                compiled_path = _main.compile_tikz_to_png(tikz_code_from_high_model)
                if compiled_path:
                    tikz_image_path = compiled_path
                    # 自动在题干文本的尾部追加 Markdown 插图引用
                    latex_content += f"\n\n![]({compiled_path})"
                    print(f"[Illustration Draw] 编译成功: {compiled_path}")
            except Exception as compile_err:
                print(f"[Illustration Draw] 编译高级模型生成的 TikZ 失败: {str(compile_err)}")

        # 将 temp_filepath 置为 None，避免在 finally 块中被删除
        saved_filepath = temp_filepath
        temp_filepath = None

        return {
            "status": "success",
            "latex": latex_content,
            "confidence": confidence,
            "provider": provider,
            "image_path": f"/{_main.UPLOAD_DIR_REL}/{os.path.basename(saved_filepath)}",
            "tikz_code": tikz_code_from_high_model,
            "tikz_image_path": tikz_image_path
        }
    except UploadTooLargeError:
        return JSONResponse(
            content={"status": "error", "message": "公式识图失败: 图片不能超过 10MB。"},
            status_code=413,
        )
    except InvalidImageError as e:
        return JSONResponse(
            content={"status": "error", "message": f"公式识图失败: {str(e)}"},
            status_code=400,
        )
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"公式识图失败: {str(e)}"},
            status_code=500
        )
    finally:
        # 确保清理临时文件
        if temp_filepath and os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
            except Exception as e_cleanup:
                print(f"[OCR Flow Cleanup Error] 无法删除临时文件 {temp_filepath}: {str(e_cleanup)}")



@router.post("/api/ai/solve")
def ai_solve(
    content: str = Form(...),
    question_type: str = Form("detailed_answer"),
    ocr_result: str = Form(""),
    custom_prompt: str = Form(""),
    thinking: str = Form("enabled"),
    model: str = Form("deepseek-flash"),
    stream: str = Form("false")
):
    provider = _main.resolve_text_provider(model)
    api_key = provider.api_key
    api_base = provider.api_base
    model_name = provider.model_name
    provider_name = provider.credential_label

    if not api_key:
        return JSONResponse(
            content={
                "status": "error", 
                "message": f"未配置对应的 API Key ({provider_name})，无法智能解答！请在工作台右上角设置面板进行配置。"
            },
            status_code=400
        )
        
    try:
        system_instructions, user_prompt = build_ai_solve_prompts(
            question_type=question_type,
            content=content,
            ocr_result=ocr_result,
            custom_prompt=custom_prompt,
        )

        # Keep the legacy fallback cap for older Bailian models. Current
        # Qwen3.7/3.8 requests are converted below to max_completion_tokens.
        max_output_tokens = 8192 if provider.provider_code == "bailian" else 16384
            
        explicit_effort = provider.reasoning_effort

        data = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": max_output_tokens
        }
        
        # DeepSeek V4 系列（deepseek-flash / deepseek-v4-pro）走 thinking 对象，
        # 旧模型（deepseek-chat / deepseek-reasoner）不支持该字段。这里与
        # apply_deepseek_thinking_policy 共用同一个判定，避免规则漂移。
        is_deepseek = is_deepseek_thinking_capable(
            model_name=model_name,
            api_base=api_base or "",
            provider_code=provider.provider_code,
        )
        is_siliconflow = api_base and "siliconflow" in api_base.lower()
        
        is_bailian = provider.provider_code == "bailian"
        if is_bailian:
            # Connect the front-end '深度思考' toggle button to Alibaba Bailian's 'enable_thinking' API parameter
            if thinking == "enabled":
                data["enable_thinking"] = True
            else:
                data["enable_thinking"] = False

        if is_siliconflow:
            # Native R1 models on SiliconFlow do not use enable_thinking (they are always reasoning)
            # Other models (V3, V4 Pro, Flash, etc.) use enable_thinking and reasoning_effort
            if "r1" not in model_name.lower():
                is_deepseek = False  # Bypass OpenAI standard thinking parameter
                if thinking == "enabled":
                    data["enable_thinking"] = True
                    if "v4" in model_name.lower():
                        data["reasoning_effort"] = "max"
                else:
                    data["enable_thinking"] = False

        # Support OpenAI reasoning models (gpt-5, o1, o3, etc.) on transit APIs
        is_openai_reasoning = ("gpt-5" in model_name.lower() or "o1" in model_name.lower() or "o3" in model_name.lower())
        if is_openai_reasoning:
            is_deepseek = False  # Bypass DeepSeek thinking parameter
            if thinking == "enabled":
                data["reasoning_effort"] = "high"    # Maximum mathematical depth and verification
            else:
                data["reasoning_effort"] = "medium"  # Balanced speed and analytical quality
        
        # The 7:3 model selector may provide an explicit allowlisted effort.
        # Apply it last so it intentionally overrides the generic toggle.
        data = inject_reasoning_effort(data, explicit_effort)
        # DeepSeek V4 系列（deepseek-flash / deepseek-v4-pro）用 thinking 对象 +
        # reasoning_effort，并且不接受百炼的 enable_thinking：统一在此收敛，
        # 断言字段与取值都在官方允许范围内。
        solve_thinking = None
        if thinking in ["enabled", "disabled"]:
            solve_thinking = thinking == "enabled"
        data = apply_deepseek_thinking_policy(
            data,
            model_name=model_name,
            api_base=api_base,
            provider_code=provider.provider_code,
            task="solve",
            thinking_enabled=solve_thinking,
        )
        data = apply_bailian_thinking_policy(
            data,
            provider_code=provider.provider_code,
            model_name=model_name,
            task="solve",
            thinking_enabled=thinking == "enabled",
        )
            
        # When thinking mode is active, temperature is ignored/deprecated by DeepSeek.
        # But when thinking is disabled or non-DeepSeek model, specify it.
        if not is_deepseek or thinking == "disabled":
            data["temperature"] = 0.2
            
        if stream == "true":
            def event_generator():
                data["stream"] = True
                try:
                    response = _main.post_chat_completion(
                        provider,
                        data,
                        timeout=300,
                        stream=True,
                        check_status=False,
                    )
                    if response.status_code != 200:
                        error_msg = f"{provider_name} 接口错误: HTTP {response.status_code}"
                        yield f"data: {json.dumps({'status': 'error', 'message': error_msg}, ensure_ascii=False)}\n\n"
                        return
                    
                    reasoning_count = 0
                    content_count = 0
                    
                    for line in response.iter_lines():
                        if not line:
                            continue
                        line_str = line.decode("utf-8").strip()
                        if line_str.startswith("data:"):
                            data_content = line_str[5:].strip()
                            if data_content == "[DONE]":
                                break
                            try:
                                chunk_json = json.loads(data_content)
                                
                                # 优先读取接口可能返回的官方 usage 统计
                                usage = chunk_json.get("usage")
                                if usage and isinstance(usage, dict):
                                    c_tok = usage.get("completion_tokens")
                                    r_tok = usage.get("completion_tokens_details", {}).get("reasoning_tokens") if isinstance(usage.get("completion_tokens_details"), dict) else None
                                    if c_tok is not None:
                                        content_count = max(content_count, c_tok)
                                    if r_tok is not None:
                                        reasoning_count = max(reasoning_count, r_tok)

                                delta = chunk_json.get("choices", [{}])[0].get("delta", {})
                                reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                                content_piece = delta.get("content") or ""
                                
                                # 针对不同模型的流式数据块进行动态 Token 数量估算（兼容大 Chunk 输出模型如 Gemini Flash）
                                if reasoning:
                                    cjk_c = sum(1 for c in reasoning if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f' or '\uff00' <= c <= '\uffef')
                                    oth_c = len(reasoning) - cjk_c
                                    reasoning_count += max(1, int(cjk_c * 1.2 + oth_c / 4.0 + 0.99))
                                if content_piece:
                                    cjk_c = sum(1 for c in content_piece if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f' or '\uff00' <= c <= '\uffef')
                                    oth_c = len(content_piece) - cjk_c
                                    content_count += max(1, int(cjk_c * 1.2 + oth_c / 4.0 + 0.99))
                                    
                                if reasoning or content_piece:
                                    yield f"data: {json.dumps({'status': 'processing', 'reasoning': reasoning, 'content': content_piece, 'reasoning_count': reasoning_count, 'content_count': content_count}, ensure_ascii=False)}\n\n"
                            except Exception:
                                continue
                    yield f"data: {json.dumps({'status': 'done'}, ensure_ascii=False)}\n\n"
                except requests.exceptions.Timeout:
                    friendly_msg = (
                        f"AI 解析生成超时（限制为 300 秒）。这通常是因为 {provider_name} "
                        f"服务端当前排队拥堵或推理速度过慢。建议您稍后再试，或在设置中切换为「DeepSeek 官方」或「阿里百炼」等更稳定的接口平台。"
                    )
                    yield f"data: {json.dumps({'status': 'error', 'message': friendly_msg}, ensure_ascii=False)}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'status': 'error', 'message': f'AI 解析生成出错: {str(e)}'}, ensure_ascii=False)}\n\n"
            
            return StreamingResponse(event_generator(), media_type="text/event-stream")

        # Generous 300 seconds timeout (5 minutes) for high-school math reasoning and network proxies
        response = _main.post_chat_completion(
            provider,
            data,
            timeout=300,
            provider_name=provider_name,
        )
            
        res_json = response.json()
        
        msg_obj = res_json.get("choices", [{}])[0].get("message", {})
        ai_message = msg_obj.get("content") or ""
        reasoning_content = msg_obj.get("reasoning_content") or ""
        
        # Robust fallback: if content is empty but reasoning is present, use reasoning as explanation
        if not ai_message and reasoning_content:
            ai_message = f"【深度思考推理过程】\n{reasoning_content}\n\n【参考解析】已成功生成推理步骤。如果需要标准的三板块排版，请尝试在控制面板中关闭「AI 深度思考推理」再次生成。"
            
        if not ai_message:
            print(
                f"[Solve API] Provider returned an empty message "
                f"(provider={provider.provider_code}, status={response.status_code})."
            )
            raise Exception(f"{provider_name} 返回了空消息，请检查 API 或账户余额。")

        # 排版紧凑化：模型偶发仍用 $$...$$ 逐式独立成行，这里兜底降级为行内 $...$。
        ai_message = compact_solution_markdown(ai_message)

        return {
            "status": "success",
            "solution": ai_message
        }
    except requests.exceptions.Timeout:
        friendly_msg = (
            f"AI 解析生成超时（限制为 300 秒）。这通常是因为 {provider_name} "
            f"服务端当前排队拥堵或推理速度过慢。建议您稍后再试，或在设置中切换为「DeepSeek 官方」或「阿里百炼」等更稳定的接口平台。"
        )
        return JSONResponse(
            content={"status": "error", "message": friendly_msg},
            status_code=500
        )
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"AI 解析生成出错: {str(e)}"},
            status_code=500
        )



def compile_tikz_to_png(tikz_code):
    """\n编译 TikZ 代码为 PNG 并存放在静态资源目录中。\n如果编译成功，返回相对路径（如 /static/uploads/tikz_xxx.png）。\n如果编译失败，抛出 Exception 详细说明原因。\n"""
    import shutil
    import uuid
    import subprocess
    import os
    import platform

    if platform.system() == 'Darwin':
        mactex_bin = '/Library/TeX/texbin'
        if os.path.exists(mactex_bin):
            os.environ['PATH'] = os.environ.get('PATH', '') + os.pathsep + mactex_bin

    if not shutil.which('xelatex'):
        raise RuntimeError("系统未检测到 'xelatex' 编译器。请确保您的系统已安装 MacTeX/TeX Live 并将其加入 PATH。")

    try:
        import pymupdf as fitz
    except ImportError:
        raise RuntimeError("Python 环境中未安装 'pymupdf'，无法将 PDF 转换为图像，请运行 'pip install pymupdf' 安装。")

    temp_dir = os.path.join(_main.UPLOAD_DIR, '.tikz_temp')
    os.makedirs(temp_dir, exist_ok=True)
    unique_id = uuid.uuid4().hex
    tex_path = os.path.join(temp_dir, f"{unique_id}.tex")
    pdf_path = os.path.join(temp_dir, f"{unique_id}.pdf")
    png_path = os.path.join(temp_dir, f"{unique_id}.png")
    aux_path = os.path.join(temp_dir, f"{unique_id}.aux")
    log_path = os.path.join(temp_dir, f"{unique_id}.log")

    tex_content = (
        r"\documentclass[tikz, border=2mm]{standalone}"
        "\n\\usepackage{ctex}"
        "\n\\usepackage{amsmath}"
        "\n\\usepackage{amssymb}"
        "\n\\usepackage{tikz}"
        "\n\\usepackage{pgfplots}"
        "\n\\pgfplotsset{compat=1.16}"
        "\n\\usetikzlibrary{patterns}"
        "\n\\usetikzlibrary{calc,positioning,intersections,arrows}"
        "\n\\usetikzlibrary{shapes.geometric,through,decorations.pathmorphing,arrows.meta,quotes,mindmap,shapes.symbols,shapes.arrows,automata,angles,3d,trees,shadows,shapes.callouts,decorations.pathreplacing,decorations.markings}"
        "\n\\begin{document}\n"
        + tikz_code
        + "\n\\end{document}"
    )

    with open(tex_path, 'w', encoding='utf-8') as f:
        f.write(tex_content)

    try:
        result = subprocess.run(
            ['xelatex', '-no-shell-escape', '-interaction=nonstopmode', '-halt-on-error',
             '-file-line-error', '-output-directory=.', os.path.basename(tex_path)],
            cwd=temp_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            env=build_restricted_tex_environment(temp_dir),
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError('编译超时 (15秒)，可能是您的 TikZ 绘图循环出现了死循环。')
    except Exception as e:
        raise RuntimeError(str(e))

    if result.returncode != 0:
        log_content = ''
        if os.path.exists(log_path):
            with open(log_path, 'rb') as lf:
                raw_log = lf.read()
            try:
                log_text = raw_log.decode('utf-8')
            except UnicodeDecodeError:
                log_text = raw_log.decode('gbk', errors='replace')
            lines = log_text.splitlines()
            error_lines = [line.strip() for line in lines if line.startswith('!')]
            if error_lines:
                log_content = '\n'.join(error_lines[:3])
        error_msg = log_content if log_content else 'LaTeX 语法错误，编译失败。'
        raise RuntimeError('编译错误: ' + error_msg)

    if not os.path.exists(pdf_path):
        raise RuntimeError('编译未生成 PDF 文件。')

    with fitz.open(pdf_path) as doc:
        if len(doc) == 0:
            raise RuntimeError('生成的 PDF 文件为空。')
        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=150)
        pix.save(png_path)

    if not os.path.exists(png_path):
        raise RuntimeError('PDF 转换 PNG 失败。')

    final_filename = f"tikz_{unique_id}.png"
    final_dest = os.path.join(_main.UPLOAD_DIR, final_filename)
    shutil.copy2(png_path, final_dest)

    for temp_file in (tex_path, pdf_path, png_path, aux_path, log_path):
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass

    return f"/{_main.UPLOAD_DIR_REL}/{final_filename}"



@router.post("/api/render_tikz")
def render_tikz_endpoint(tikz_code: str = Form("")):
    """接收 TikZ 代码并编译成静态 PNG，返回其相对路径"""
    try:
        image_path = _main.compile_tikz_to_png(tikz_code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "image_path": image_path}



@router.post("/api/correct_tikz")
def correct_tikz_endpoint(
    tikz_code: str = Form(""),
    original_image_path: str = Form(""),
    user_prompt: str = Form(""),
):
    """利用用户指定的高级绘图模型进行 TikZ 纠错，支持人工指导意见注入"""
    import base64

    prefer_draw = os.getenv('PREFER_DRAW_MODEL', 'Qwen/Qwen3-VL-32B-Instruct')
    draw_provider = _main.resolve_draw_provider(prefer_draw)
    if not draw_provider.api_key:
        raise HTTPException(
            status_code=400,
            detail='未配置 ' + draw_provider.credential_label + '！请在设置面板中配置后重试。',
        )

    print(
        '[TikZ Correction] 启用 '
        + draw_provider.provider_label
        + ' 高级模型进行纠错: '
        + draw_provider.model_name
        + ', Base URL: '
        + draw_provider.chat_completions_url
    )

    clean_original_path = resolve_upload_asset(
        original_image_path, uploads_dir=_main.UPLOAD_DIR, url_prefix=_main.UPLOAD_DIR_REL
    )
    try:
        with open(clean_original_path, 'rb') as f:
            encoded_original = base64.b64encode(f.read()).decode('utf-8')
    except AssetSecurityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    rendered_image_path = None
    compile_error_log = None
    try:
        rendered_image_path = _main.compile_tikz_to_png(tikz_code)
    except Exception as e:
        compile_error_log = str(e)

    if rendered_image_path:
        clean_rendered_path = resolve_upload_asset(
            rendered_image_path, uploads_dir=_main.UPLOAD_DIR, url_prefix=_main.UPLOAD_DIR_REL
        )
        try:
            with open(clean_rendered_path, 'rb') as f:
                encoded_rendered = base64.b64encode(f.read()).decode('utf-8')
        except AssetSecurityError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        prompt = build_tikz_correction_prompt(
            tikz_code, user_prompt, rendered_comparison=True
        )
        content_payload = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded_original}"},
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded_rendered}"},
            },
        ]
        try:
            clean_rendered_path.unlink()
        except Exception:
            pass
    else:
        prompt = build_tikz_correction_prompt(
            tikz_code,
            user_prompt,
            compile_error_log=compile_error_log,
            rendered_comparison=False,
        )
        content_payload = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded_original}"},
            },
        ]

    try:
        corrected_code = request_tikz_completion(draw_provider, content_payload, timeout=90)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"AI 纠错请求失败: {str(e)}")

    return {
        "status": "success",
        "corrected_code": corrected_code,
        "mode": "visual_diff" if rendered_image_path else "error_recovery",
    }



@router.post("/api/ai/draw_tikz")
def draw_tikz_workbench_endpoint(
    instruction: str = Form(""),
    context: str = Form(""),
    existing_tikz: str = Form(""),
    reference_image_path: str = Form(""),
    reference_image: Optional[UploadFile] = File(None),
    image_kind: str = Form(""),
):
    """从文字、参考图或已有源码生成/修改 TikZ，供手动录题工作台使用。

    image_kind: 图类型提示，可选 geometry / stat / object / auto(空)。
    auto 时由绘图模型根据参考图或上下文自行判断选用 tikzpicture / pgfplots / 示意线稿。
    """

    instruction = (instruction or "").strip()
    context = (context or "").strip()
    existing_tikz = (existing_tikz or "").strip()
    reference_image_path = (reference_image_path or "").strip()
    has_uploaded_reference = bool(reference_image and reference_image.filename)
    has_reference = has_uploaded_reference or bool(reference_image_path)
    if not instruction and not existing_tikz and not has_reference:
        raise HTTPException(status_code=400, detail="请输入绘图要求、上传参考图或提供已有 TikZ 源码。")
    if len(instruction) > 4000:
        raise HTTPException(status_code=400, detail="绘图要求不能超过 4000 个字符。")
    if len(context) > 30000:
        raise HTTPException(status_code=400, detail="绘图上下文不能超过 30000 个字符。")
    if len(existing_tikz) > 200000:
        raise HTTPException(status_code=400, detail="TikZ 源码不能超过 200000 个字符。")

    reference_path: Optional[Path] = None
    temporary_reference_path: Optional[Path] = None
    persisted_reference_url = ""
    try:
        if has_uploaded_reference:
            raw = read_stream_limited(reference_image.file, _main.MAX_SINGLE_IMAGE_BYTES)
            normalized = normalize_raster_image(raw)
            temporary_reference_path = Path(_main.TMP_UPLOAD_DIR) / (
                f"tikz_reference_{uuid.uuid4().hex}{normalized.extension}"
            )
            temporary_reference_path.write_bytes(normalized.data)
            reference_path = temporary_reference_path
        elif reference_image_path:
            normalized_reference = normalize_upload_asset_reference(
                reference_image_path,
                uploads_dir=_main.UPLOAD_DIR,
                url_prefix=_main.UPLOAD_DIR_REL,
            )
            reference_path = resolve_upload_asset(
                normalized_reference,
                uploads_dir=_main.UPLOAD_DIR,
                url_prefix=_main.UPLOAD_DIR_REL,
            )
            persisted_reference_url = normalized_reference

        prefer_draw = (
            os.getenv("PREFER_DRAW_MODEL")
            or os.getenv("PREFER_PARSE_MODEL")
            or "Qwen/Qwen3-VL-32B-Instruct"
        )
        tikz_code = _main.draw_tikz_via_high_model(
            str(reference_path) if reference_path else None,
            prefer_draw,
            latex_content=context,
            instruction=instruction,
            existing_tikz=existing_tikz,
            require_image_support=has_reference,
            image_kind=image_kind,
        )
        if not tikz_code:
            raise RuntimeError(
                "TikZ 绘图模型未返回可用源码，请检查绘图模型与 API 密钥设置。"
            )
        if temporary_reference_path is not None:
            persisted_name = (
                f"tikz_reference_{uuid.uuid4().hex}"
                f"{temporary_reference_path.suffix.lower()}"
            )
            persisted_path = Path(_main.UPLOAD_DIR) / persisted_name
            temporary_reference_path.replace(persisted_path)
            temporary_reference_path = None
            persisted_reference_url = f"/{_main.UPLOAD_DIR_REL}/{persisted_name}"
        return {
            "status": "success",
            "tikz_code": tikz_code,
            "used_reference_image": has_reference,
            "reference_image_path": persisted_reference_url,
        }
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail="参考图不能超过 10MB。") from exc
    except InvalidImageError as exc:
        raise HTTPException(status_code=400, detail=f"参考图无效: {str(exc)}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"AI TikZ 绘图失败: {str(exc)}") from exc
    finally:
        if temporary_reference_path is not None:
            try:
                temporary_reference_path.unlink(missing_ok=True)
            except OSError:
                pass



@router.post("/api/ai/draw_tikz_from_image")
def draw_tikz_from_image_endpoint(
    image_path: str = Form(...),
    latex_content: str = Form(None),
    x_local_token: str = Header(None, alias="X-Local-Token")
):
    """根据指定的题目图片，调用高级多模态模型生成对应的 LaTeX TikZ 代码"""
    # Middleware already enforces this header for HTTP calls.  Keep the direct
    # function guard tied to the same single token source for test/internal use.
    if not x_local_token or not secrets.compare_digest(x_local_token, LOCAL_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        physical_path = resolve_upload_asset(
            image_path,
            uploads_dir=_main.UPLOAD_DIR,
            url_prefix=_main.UPLOAD_DIR_REL,
        )
    except AssetSecurityError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
        
    # 动态读取绘图高级模型配置
    prefer_draw = os.getenv("PREFER_DRAW_MODEL") or os.getenv("PREFER_PARSE_MODEL") or "Qwen/Qwen3-VL-32B-Instruct"
    
    try:
        print(f"[API Draw TikZ] 正在调用高级模型 {prefer_draw} 对插图 {image_path} 进行多模态 TikZ 绘图分析...")
        tikz_code = _main.draw_tikz_via_high_model(
            physical_path,
            prefer_draw,
            latex_content=latex_content
        )
        
        if not tikz_code:
            raise RuntimeError(f"多模态高级模型 {prefer_draw} 未能生成有效的 TikZ 代码")
            
        return {
            "status": "success",
            "tikz_code": tikz_code
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=f"AI 识图绘图失败: {str(e)}")



@router.post("/api/ai/classify")
def ai_classify(content: str = Form(...)):
    classify_model = (
        os.getenv("PREFER_CLASSIFY_MODEL") 
        or os.getenv("DEEPSEEK_CLASSIFY_MODEL") 
        or os.getenv("PREFER_PARSE_MODEL") 
        or "deepseek-flash"
    )
    
    provider = _main.resolve_text_provider(classify_model)
    api_key = provider.api_key
    api_base = provider.api_base
    model_name = provider.model_name
    provider_name = provider.credential_label

    if not api_key:
        return JSONResponse(
            content={
                "status": "error", 
                "message": f"未配置对应的 API Key ({provider_name})，无法自动智能分类！请在工作台右上角设置面板进行配置。"
            },
            status_code=400
        )
        
    try:
        system_instructions = build_classification_system_prompt(_main.get_current_curriculum())
        data = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": f"题目内容:\n{content}"}
            ],
            "response_format": {
                "type": "json_object"
            },
            "temperature": 0.2,
            "max_tokens": 512
        }
        
        # Only add thinking if using a DeepSeek model or DeepSeek base URL, excluding legacy models that don't support it
        data = inject_reasoning_effort(data, provider.reasoning_effort)
        # 分类是结构化轻量任务，DeepSeek 一律关闭思考：V4.1-Flash 默认开启思考
        # 且 effort=high，对批量打标既慢又贵。
        data = apply_deepseek_thinking_policy(
            data,
            model_name=model_name,
            api_base=api_base,
            provider_code=provider.provider_code,
            task="classify",
            thinking_enabled=False,
        )
        data = apply_bailian_thinking_policy(
            data,
            provider_code=provider.provider_code,
            model_name=model_name,
            task="classify",
        )
        
        response = _main.post_chat_completion(
            provider,
            data,
            timeout=30,
            provider_name=provider_name,
        )
            
        res_json = response.json()
        ai_message = res_json.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        
        # Strip potential markdown formatting if returned
        if ai_message.startswith("```"):
            lines = ai_message.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].strip() == "```":
                lines = lines[:-1]
            ai_message = "\n".join(lines).strip()
            
        result = json.loads(ai_message)
        compulsory = result.get("compulsory", "")
        chapter = result.get("chapter", "")
        knowledge = result.get("knowledge", "") or ""
        topic = result.get("topic", "") or ""
        structured_question_form = detect_structured_question_form(content)
        question_form = structured_question_form or normalize_ai_question_form(
            result.get("question_form")
        )
        question_form_source = "structure" if structured_question_form else "ai"
        
        def _strip_num_prefix(s):
            return re.sub(r"^\s*\d+(\.\d+)*\s*", "", str(s or "")).strip()

        def _match_dict_key(d, ai_name):
            ai_name = str(ai_name or "").strip()
            if not ai_name:
                return ""
            if ai_name in d:
                return ai_name
            norm = _strip_num_prefix(ai_name)
            if norm:
                for k in d:
                    if _strip_num_prefix(k) == norm:
                        return k
                for k in d:
                    k_norm = _strip_num_prefix(k)
                    if k_norm and (norm in k_norm or k_norm in norm):
                        return k
            return ""

        def _match_section_name(sections, ai_name):
            ai_name = str(ai_name or "").strip()
            if not ai_name:
                return ""
            names = [s.get("name", "") if isinstance(s, dict) else str(s) for s in (sections or [])]
            names = [n for n in names if n]
            if ai_name in names:
                return ai_name
            norm = _strip_num_prefix(ai_name)
            if norm:
                for n in names:
                    if _strip_num_prefix(n) == norm:
                        return n
                for n in names:
                    n_norm = _strip_num_prefix(n)
                    if n_norm and (norm in n_norm or n_norm in norm):
                        return n
            return ""

        def _match_topic_name(topics, ai_name):
            ai_name = str(ai_name or "").strip()
            if not ai_name:
                return ""
            names = [t.get("name", "") if isinstance(t, dict) else str(t) for t in (topics or [])]
            names = [n for n in names if n]
            if ai_name in names:
                return ai_name
            for n in names:
                if ai_name in n or n in ai_name:
                    return n
            return ""

        # Verification: match returned values against _main.get_current_curriculum()
        curr = _main.get_current_curriculum()
        matched_comp = _match_dict_key(curr, compulsory)
        matched_chap = ""
        if matched_comp:
            matched_chap = _match_dict_key(curr[matched_comp], chapter)
        matched_know = ""
        matched_topic = ""
        if matched_comp and matched_chap:
            sections = curr[matched_comp][matched_chap]
            matched_know = _match_section_name(sections, knowledge)
            if matched_know:
                sec_obj = next(
                    (s for s in sections if (s.get("name") if isinstance(s, dict) else str(s)) == matched_know),
                    None,
                )
                if isinstance(sec_obj, dict):
                    matched_topic = _match_topic_name(sec_obj.get("topics", []), topic)

        if matched_comp and matched_chap:
            return {
                "status": "success",
                "compulsory": matched_comp,
                "chapter": matched_chap,
                "knowledge": matched_know,
                "topic": matched_topic,
                "question_form": question_form,
                "question_form_source": question_form_source,
            }
        else:
            # Fallback dynamically to the first available category book/chapter
            first_comp = list(curr.keys())[0] if curr else "必修一"
            first_chap = list(curr[first_comp].keys())[0] if curr and first_comp in curr and curr[first_comp] else "1. 集合与常用逻辑用语"
            return {
                "status": "success",
                "compulsory": first_comp,
                "chapter": first_chap,
                "knowledge": "",
                "topic": "",
                "question_form": question_form,
                "question_form_source": question_form_source,
                "is_fallback": True,
                "raw_recommendation": f"{compulsory} -> {chapter} -> {knowledge} -> {topic}"
            }
            
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"AI 智能分类失败: {str(e)}"},
            status_code=500
        )



def ocr_pdf_page_image(image_path: str) -> str:
    """自动选择已配置的 VLM 识别引擎进行单页识别，支持故障转移（Fallback）与兜底识别

    整页 OCR 必须同时请求插图坐标，否则 ``_main.resolve_ocr_illustration_boxes``
    永远匹配不到标签、下游"实际裁剪图片"恒为 0 张。
    """
    errors = []
    prefer_engine = os.getenv("OCR_PREFER_ENGINE", "siliconflow")
    providers_to_try = _main.resolve_ocr_fallbacks(prefer_engine)

    if not providers_to_try:
        raise ValueError("未配置任何识图 Key，请在右上角「API设置」面板中配置 硅基流动、阿里百炼 或 中转站 API 密钥。")

    for ocr_provider in providers_to_try:
        label = ocr_provider.provider_label
        try:
            print(f"[PDF OCR Flow] 正在尝试调用识图引擎: {label}...")
            return _main.ocr_via_provider(
                image_path,
                ocr_provider,
                include_illustration_box=True,
                illustration_box_prompt=PDF_PAGE_ILLUSTRATION_BOX_PROMPT,
            )
        except requests.exceptions.ReadTimeout as e_single:
            # The first provider may already have accepted the image.  Sending
            # it immediately to another provider can create a duplicate bill.
            raise RuntimeError(
                f"{label} 读取超时，请求是否已被处理尚不确定。"
                "为避免重复计费，本次未自动切换到下一家模型，请稍后手动重试。"
            ) from e_single
        except Exception as e_single:
            err_msg = f"{label} 出错: {str(e_single)}"
            print(f"[PDF OCR Flow Warning] {err_msg}")
            errors.append(err_msg)
            
    # 如果全部都失败了，抛出包含所有尝试错误细节的汇总异常
    raise RuntimeError("所有配置的识图引擎均尝试失败。详情:\n" + "\n".join(errors))



def process_ocr_illustrations(text: str) -> str:
    """安全标签清洗：擦除视觉定位标签或 box 坐标标记，返回纯净 OCR 结果"""
    return illustration_crop.process_ocr_illustrations(text)



def resolve_ocr_illustration_boxes(
    text: str,
    page_image_path: str,
    task_id: str,
    page_num: int,
    pdf_bytes=None,
) -> str:
    """按 `[ILLUSTRATION_BOX: ...]` 百分比坐标从整页 PNG 裁图并替换为占位符。

    实现位于 ``mathbank.illustration_crop``；此处保留同名入口以维持既有
    补丁/导入接缝。``pdf_bytes`` 用于把模型框纠正到该页真实位图边界。
    """
    return illustration_crop.resolve_ocr_illustration_boxes(
        text, page_image_path, task_id, page_num, pdf_bytes=pdf_bytes
    )



def find_source_page_by_overlap(q_text: str, ocr_results: list, min_overlap: int = 1) -> int:
    """利用 3-shingle（三字符切片）特征重合度，计算题目最可能所属的 PDF 原始物理页码。

    返回 ``-1`` 表示没有任何一页达到 ``min_overlap``（默认 1，即要求真实重合）。
    旧实现无论重合度多少都退回第 0 页，导致「与任何页都对不上」的题目被误判为
    第 1 页，进而把该页全部插图挂过去（末题吞掉整卷图片的根因之一）。
    """
    if not q_text or not ocr_results:
        return -1
    
    import re
    def clean_for_compare(t: str) -> str:
        # 仅保留中文字符、英文字母和数字，过滤掉干扰公式渲染的标点符号
        return "".join(re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9]', t))
        
    cleaned_q = clean_for_compare(q_text)
    if not cleaned_q:
        return -1
        
    best_page = 0
    max_overlap = -1
    
    for idx, page_text in enumerate(ocr_results):
        if not page_text:
            continue
        cleaned_page = clean_for_compare(page_text)
        
        # 构建 3-shingle 切片集合
        if len(cleaned_q) >= 3:
            shingles_q = set(cleaned_q[i:i+3] for i in range(len(cleaned_q)-2))
        else:
            shingles_q = {cleaned_q}
            
        if len(cleaned_page) >= 3:
            shingles_page = set(cleaned_page[i:i+3] for i in range(len(cleaned_page)-2))
        else:
            shingles_page = {cleaned_page}
            
        overlap = len(shingles_q.intersection(shingles_page))
        if overlap > max_overlap:
            max_overlap = overlap
            best_page = idx

    if max_overlap < min_overlap:
        return -1
    return best_page
