"""Settings, version-check and curriculum-config HTTP endpoints.

Extracted from the application entry point (main.py) so the root module stays
thin. Shared state lives in mathbank.metadata / mathbank.curriculums / mathbank.paths;
this router only wires FastAPI routes to those helpers.

Routes:
  GET  /api/settings
  POST /api/settings/save
  GET  /api/version
  GET  /api/version/check-update
  GET  /api/config/metadata
  POST /api/config/metadata
  POST /api/admin/reload-metadata
  POST /api/ai/models
  GET  /api/config/curriculum-presets/{version}
  POST /api/config/curriculum-presets/{version}
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

import requests

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from mathbank import __version__, GITHUB_REPO
from mathbank import metadata as metadata_module
from mathbank.ai_http import build_bearer_headers, robust_request_get
from mathbank.ai_providers import (
    build_models_url,
    parse_model_ids,
    resolve_provider_endpoint,
)
from mathbank.asset_security import write_private_text_atomic
from mathbank.auth import LOCAL_TOKEN
from mathbank.curriculums import (
    get_curriculum_preset,
    load_curriculum,
    save_curriculum_preset,
    set_current_curriculum,
)
from mathbank.database import Question, QuestionCurriculum, get_db
from mathbank.metadata import (
    get_active_version_code,
    get_metadata_cache,
    normalize_metadata_curriculum,
    parse_version_tuple,
    reload_metadata_if_changed,
    schedule_database_export,
    set_metadata_cache,
)
from mathbank.paths import ENV_FILE, PROJECT_ROOT, SERVER_INSTANCE_ID


router = APIRouter(tags=["settings"])


def _load_custom_providers_from_env() -> list:
    """读取 .env 中的 CUSTOM_PROVIDERS_JSON，返回自定义 OpenAI 兼容厂商列表。

    仅做宽松校验：必须是 list[dict] 且每项含非空 name。解析失败或为空时返回 []。
    """

    raw = os.getenv("CUSTOM_PROVIDERS_JSON", "") or ""
    if not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    result = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        result.append({
            "name": name,
            "base_url": str(item.get("base_url") or "").strip(),
            "api_key": str(item.get("api_key") or "").strip(),
            "model": str(item.get("model") or "").strip(),
        })
    return result


@router.get("/api/settings")
def get_settings():
    ds_key = os.getenv("DEEPSEEK_API_KEY", "")
    sf_key = os.getenv("SILICONFLOW_API_KEY", "")
    ali_key = os.getenv("ALI_BAILIAN_API_KEY", "")
    mineru_token = os.getenv("MINERU_API_TOKEN", "")
    mineru_api_base = os.getenv("MINERU_API_BASE", "")

    # 三家内置厂商可自定义的接口地址（Base URL）
    ds_base = os.getenv("DEEPSEEK_API_BASE", "")
    sf_base = os.getenv("SILICONFLOW_API_BASE", "")
    ali_base = os.getenv("ALI_BAILIAN_API_BASE", "")

    # 兼容老版 ZHONGZHAN 环境变量
    zz_gpt_key = os.getenv("ZHONGZHAN_GPT_API_KEY") or os.getenv("ZHONGZHAN_API_KEY", "")
    zz_gpt_base = os.getenv("ZHONGZHAN_GPT_BASE_URL") or os.getenv("ZHONGZHAN_BASE_URL", "")
    zz_gpt_ocr_model = os.getenv("ZHONGZHAN_GPT_OCR_MODEL") or os.getenv("ZHONGZHAN_OCR_MODEL", "gpt-4o")
    
    zz_claude_key = os.getenv("ZHONGZHAN_CLAUDE_API_KEY", "")
    zz_claude_base = os.getenv("ZHONGZHAN_CLAUDE_BASE_URL", "")
    zz_claude_ocr_model = os.getenv("ZHONGZHAN_CLAUDE_OCR_MODEL", "claude-3-5-sonnet")
    
    prefer_engine = os.getenv("OCR_PREFER_ENGINE", "siliconflow")
    sf_model = os.getenv("SILICONFLOW_OCR_MODEL", "Qwen/Qwen3-VL-8B-Instruct")
    ali_model = os.getenv("ALI_BAILIAN_OCR_MODEL", "qwen3.7-flash")
    prefer_solve_model = os.getenv("PREFER_SOLVE_MODEL", "deepseek-flash")
    prefer_parse_model = os.getenv("PREFER_PARSE_MODEL", "deepseek-flash")
    prefer_classify_model = os.getenv("PREFER_CLASSIFY_MODEL") or os.getenv("DEEPSEEK_CLASSIFY_MODEL", "deepseek-flash")
    prefer_draw_model = os.getenv("PREFER_DRAW_MODEL", "Qwen/Qwen3-VL-32B-Instruct")
    
    masked_ds = ""
    if ds_key:
        masked_ds = ds_key[:4] + "••••" + ds_key[-4:] if len(ds_key) > 8 else "••••••••"
        
    masked_sf = ""
    if sf_key:
        masked_sf = sf_key[:4] + "••••" + sf_key[-4:] if len(sf_key) > 8 else "••••••••"
        
    masked_ali = ""
    if ali_key:
        masked_ali = ali_key[:4] + "••••" + ali_key[-4:] if len(ali_key) > 8 else "••••••••"

    masked_zz_gpt = ""
    if zz_gpt_key:
        masked_zz_gpt = zz_gpt_key[:4] + "••••" + zz_gpt_key[-4:] if len(zz_gpt_key) > 8 else "••••••••"
        
    masked_zz_claude = ""
    if zz_claude_key:
        masked_zz_claude = zz_claude_key[:4] + "••••" + zz_claude_key[-4:] if len(zz_claude_key) > 8 else "••••••••"

    masked_mineru = ""
    if mineru_token:
        masked_mineru = mineru_token[:4] + "••••" + mineru_token[-4:] if len(mineru_token) > 8 else "••••••••"
        
    return {
        "deepseek_key": masked_ds,
        "siliconflow_key": masked_sf,
        "ali_bailian_key": masked_ali,
        "zhongzhan_gpt_key": masked_zz_gpt,
        "zhongzhan_gpt_base_url": zz_gpt_base,
        "zhongzhan_gpt_ocr_model": zz_gpt_ocr_model,
        "zhongzhan_claude_key": masked_zz_claude,
        "zhongzhan_claude_base_url": zz_claude_base,
        "zhongzhan_claude_ocr_model": zz_claude_ocr_model,
        "mineru_token": masked_mineru,
        "mineru_api_base": mineru_api_base,
        "deepseek_base_url": ds_base,
        "siliconflow_base_url": sf_base,
        "ali_bailian_base_url": ali_base,
        "custom_providers": _load_custom_providers_from_env(),
        "prefer_engine": prefer_engine,
        "siliconflow_model": sf_model,
        "ali_bailian_model": ali_model,
        "prefer_solve_model": prefer_solve_model,
        "prefer_parse_model": prefer_parse_model,
        "prefer_classify_model": prefer_classify_model,
        "prefer_draw_model": prefer_draw_model
    }

@router.post("/api/settings/save")
def save_settings(
    deepseek_key: str = Form(""),
    siliconflow_key: str = Form(""),
    ali_bailian_key: str = Form(""),
    deepseek_base_url: str = Form(""),
    siliconflow_base_url: str = Form(""),
    ali_bailian_base_url: str = Form(""),
    zhongzhan_gpt_key: str = Form(""),
    zhongzhan_gpt_base_url: str = Form(""),
    zhongzhan_gpt_ocr_model: str = Form(""),
    zhongzhan_claude_key: str = Form(""),
    zhongzhan_claude_base_url: str = Form(""),
    zhongzhan_claude_ocr_model: str = Form(""),
    mineru_token: str = Form(""),
    mineru_api_base: str = Form(""),
    custom_providers: str = Form(""),
    prefer_engine: str = Form("siliconflow"),
    siliconflow_model: str = Form("Qwen/Qwen3-VL-8B-Instruct"),
    ali_bailian_model: str = Form("qwen3.7-flash"),
    prefer_solve_model: str = Form("deepseek-flash"),
    prefer_parse_model: str = Form("deepseek-flash"),
    prefer_classify_model: str = Form("deepseek-flash"),
    prefer_draw_model: str = Form("Qwen/Qwen3-VL-32B-Instruct")
):
    try:
        settings_values = {
            "deepseek_key": deepseek_key,
            "siliconflow_key": siliconflow_key,
            "ali_bailian_key": ali_bailian_key,
            "zhongzhan_gpt_key": zhongzhan_gpt_key,
            "zhongzhan_gpt_base_url": zhongzhan_gpt_base_url,
            "zhongzhan_gpt_ocr_model": zhongzhan_gpt_ocr_model,
            "zhongzhan_claude_key": zhongzhan_claude_key,
            "zhongzhan_claude_base_url": zhongzhan_claude_base_url,
            "zhongzhan_claude_ocr_model": zhongzhan_claude_ocr_model,
            "mineru_token": mineru_token,
            "mineru_api_base": mineru_api_base,
            "custom_providers": custom_providers,
            "deepseek_base_url": deepseek_base_url,
            "siliconflow_base_url": siliconflow_base_url,
            "ali_bailian_base_url": ali_bailian_base_url,
            "prefer_engine": prefer_engine,
            "siliconflow_model": siliconflow_model,
            "ali_bailian_model": ali_bailian_model,
            "prefer_solve_model": prefer_solve_model,
            "prefer_parse_model": prefer_parse_model,
            "prefer_classify_model": prefer_classify_model,
            "prefer_draw_model": prefer_draw_model,
        }
        if any("\r" in value or "\n" in value for value in settings_values.values()):
            raise ValueError("配置值不能包含换行符。")

        # 自动清洗 API Key/Token：strip 空白、去除非 ASCII 字符（用户粘贴时常带全角空格/中文）
        def _clean_key(v: str) -> str:
            if not isinstance(v, str):
                return v
            v = v.strip()
            # 保留掩码占位符
            if "•" in v:
                return v
            return "".join(ch for ch in v if ord(ch) < 128)
        deepseek_key = _clean_key(deepseek_key)
        siliconflow_key = _clean_key(siliconflow_key)
        ali_bailian_key = _clean_key(ali_bailian_key)
        zhongzhan_gpt_key = _clean_key(zhongzhan_gpt_key)
        zhongzhan_claude_key = _clean_key(zhongzhan_claude_key)
        mineru_token = _clean_key(mineru_token)

        # If masked, preserve current key
        if "••••" in deepseek_key:
            deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        if "••••" in siliconflow_key:
            siliconflow_key = os.getenv("SILICONFLOW_API_KEY", "")
        if "••••" in ali_bailian_key:
            ali_bailian_key = os.getenv("ALI_BAILIAN_API_KEY", "")
        if "••••" in zhongzhan_gpt_key:
            zhongzhan_gpt_key = os.getenv("ZHONGZHAN_GPT_API_KEY") or os.getenv("ZHONGZHAN_API_KEY", "")
        if "••••" in zhongzhan_claude_key:
            zhongzhan_claude_key = os.getenv("ZHONGZHAN_CLAUDE_API_KEY", "")

        if "••••" in mineru_token:
            mineru_token = os.getenv("MINERU_API_TOKEN", "")

        # 校验并清洗自定义厂商 JSON（仅 OpenAI 兼容第三方，文本模型）
        custom_providers_clean = ""
        if custom_providers.strip():
            try:
                cp_list = json.loads(custom_providers)
            except Exception as exc:
                raise ValueError(f"自定义厂商配置不是合法 JSON: {exc}")
            if not isinstance(cp_list, list):
                raise ValueError("自定义厂商配置必须是 JSON 数组")
            cleaned = []
            for item in cp_list:
                if not isinstance(item, dict):
                    raise ValueError("自定义厂商配置每项必须是对象")
                name = str(item.get("name") or "").strip()
                if not name:
                    raise ValueError("自定义厂商配置每项必须含 name")
                cleaned.append({
                    "name": name,
                    "base_url": str(item.get("base_url") or "").strip(),
                    "api_key": str(item.get("api_key") or "").strip(),
                    "model": str(item.get("model") or "").strip(),
                })
            custom_providers_clean = json.dumps(
                cleaned, ensure_ascii=False, separators=(",", ":")
            )
            
        # Read current .env
        env_lines = []
        if ENV_FILE.exists():
            with ENV_FILE.open("r", encoding="utf-8") as f:
                env_lines = f.readlines()
        
        keys_replaced = {
            "DEEPSEEK_API_KEY": False,
            "SILICONFLOW_API_KEY": False,
            "ALI_BAILIAN_API_KEY": False,
            "ZHONGZHAN_GPT_API_KEY": False,
            "ZHONGZHAN_GPT_BASE_URL": False,
            "ZHONGZHAN_GPT_OCR_MODEL": False,
            "ZHONGZHAN_CLAUDE_API_KEY": False,
            "ZHONGZHAN_CLAUDE_BASE_URL": False,
            "ZHONGZHAN_CLAUDE_OCR_MODEL": False,
            "MINERU_API_TOKEN": False,
            "MINERU_API_BASE": False,
            "CUSTOM_PROVIDERS_JSON": False,
            "DEEPSEEK_API_BASE": False,
            "SILICONFLOW_API_BASE": False,
            "ALI_BAILIAN_API_BASE": False,
            "OCR_PREFER_ENGINE": False,
            "SILICONFLOW_OCR_MODEL": False,
            "ALI_BAILIAN_OCR_MODEL": False,
            "PREFER_SOLVE_MODEL": False,
            "PREFER_PARSE_MODEL": False,
            "PREFER_CLASSIFY_MODEL": False,
            "PREFER_DRAW_MODEL": False
        }
        new_lines = []
        
        for line in env_lines:
            line_strip = line.strip()
            # Skip old Pix2Text settings to clean .env
            if line_strip.startswith("PIX2TEXT_API_KEY=") or line_strip.startswith("PIX2TEXT_SERVER_TYPE="):
                continue
                
            if line_strip.startswith("DEEPSEEK_API_KEY="):
                new_lines.append(f"DEEPSEEK_API_KEY={deepseek_key}\n")
                keys_replaced["DEEPSEEK_API_KEY"] = True
            elif line_strip.startswith("SILICONFLOW_API_KEY="):
                new_lines.append(f"SILICONFLOW_API_KEY={siliconflow_key}\n")
                keys_replaced["SILICONFLOW_API_KEY"] = True
            elif line_strip.startswith("ALI_BAILIAN_API_KEY="):
                new_lines.append(f"ALI_BAILIAN_API_KEY={ali_bailian_key}\n")
                keys_replaced["ALI_BAILIAN_API_KEY"] = True
            elif line_strip.startswith("ZHONGZHAN_GPT_API_KEY="):
                new_lines.append(f"ZHONGZHAN_GPT_API_KEY={zhongzhan_gpt_key}\n")
                keys_replaced["ZHONGZHAN_GPT_API_KEY"] = True
            elif line_strip.startswith("ZHONGZHAN_GPT_BASE_URL="):
                new_lines.append(f"ZHONGZHAN_GPT_BASE_URL={zhongzhan_gpt_base_url}\n")
                keys_replaced["ZHONGZHAN_GPT_BASE_URL"] = True
            elif line_strip.startswith("ZHONGZHAN_GPT_OCR_MODEL="):
                new_lines.append(f"ZHONGZHAN_GPT_OCR_MODEL={zhongzhan_gpt_ocr_model}\n")
                keys_replaced["ZHONGZHAN_GPT_OCR_MODEL"] = True
            elif line_strip.startswith("ZHONGZHAN_CLAUDE_API_KEY="):
                new_lines.append(f"ZHONGZHAN_CLAUDE_API_KEY={zhongzhan_claude_key}\n")
                keys_replaced["ZHONGZHAN_CLAUDE_API_KEY"] = True
            elif line_strip.startswith("ZHONGZHAN_CLAUDE_BASE_URL="):
                new_lines.append(f"ZHONGZHAN_CLAUDE_BASE_URL={zhongzhan_claude_base_url}\n")
                keys_replaced["ZHONGZHAN_CLAUDE_BASE_URL"] = True
            elif line_strip.startswith("ZHONGZHAN_CLAUDE_OCR_MODEL="):
                new_lines.append(f"ZHONGZHAN_CLAUDE_OCR_MODEL={zhongzhan_claude_ocr_model}\n")
                keys_replaced["ZHONGZHAN_CLAUDE_OCR_MODEL"] = True
            elif line_strip.startswith("MINERU_API_TOKEN="):
                new_lines.append(f"MINERU_API_TOKEN={mineru_token}\n")
                keys_replaced["MINERU_API_TOKEN"] = True
            elif line_strip.startswith("MINERU_API_BASE="):
                new_lines.append(f"MINERU_API_BASE={mineru_api_base}\n")
                keys_replaced["MINERU_API_BASE"] = True
            elif line_strip.startswith("CUSTOM_PROVIDERS_JSON="):
                new_lines.append(f"CUSTOM_PROVIDERS_JSON={custom_providers_clean}\n")
                keys_replaced["CUSTOM_PROVIDERS_JSON"] = True
            elif line_strip.startswith("DEEPSEEK_API_BASE="):
                new_lines.append(f"DEEPSEEK_API_BASE={deepseek_base_url}\n")
                keys_replaced["DEEPSEEK_API_BASE"] = True
            elif line_strip.startswith("SILICONFLOW_API_BASE="):
                new_lines.append(f"SILICONFLOW_API_BASE={siliconflow_base_url}\n")
                keys_replaced["SILICONFLOW_API_BASE"] = True
            elif line_strip.startswith("ALI_BAILIAN_API_BASE="):
                new_lines.append(f"ALI_BAILIAN_API_BASE={ali_bailian_base_url}\n")
                keys_replaced["ALI_BAILIAN_API_BASE"] = True
            elif line_strip.startswith("OCR_PREFER_ENGINE="):
                new_lines.append(f"OCR_PREFER_ENGINE={prefer_engine}\n")
                keys_replaced["OCR_PREFER_ENGINE"] = True
            elif line_strip.startswith("SILICONFLOW_OCR_MODEL="):
                new_lines.append(f"SILICONFLOW_OCR_MODEL={siliconflow_model}\n")
                keys_replaced["SILICONFLOW_OCR_MODEL"] = True
            elif line_strip.startswith("ALI_BAILIAN_OCR_MODEL="):
                new_lines.append(f"ALI_BAILIAN_OCR_MODEL={ali_bailian_model}\n")
                keys_replaced["ALI_BAILIAN_OCR_MODEL"] = True
            elif line_strip.startswith("PREFER_SOLVE_MODEL="):
                new_lines.append(f"PREFER_SOLVE_MODEL={prefer_solve_model}\n")
                keys_replaced["PREFER_SOLVE_MODEL"] = True
            elif line_strip.startswith("PREFER_PARSE_MODEL="):
                new_lines.append(f"PREFER_PARSE_MODEL={prefer_parse_model}\n")
                keys_replaced["PREFER_PARSE_MODEL"] = True
            elif line_strip.startswith("PREFER_CLASSIFY_MODEL=") or line_strip.startswith("DEEPSEEK_CLASSIFY_MODEL="):
                new_lines.append(f"PREFER_CLASSIFY_MODEL={prefer_classify_model}\n")
                keys_replaced["PREFER_CLASSIFY_MODEL"] = True
            elif line_strip.startswith("PREFER_DRAW_MODEL="):
                new_lines.append(f"PREFER_DRAW_MODEL={prefer_draw_model}\n")
                keys_replaced["PREFER_DRAW_MODEL"] = True
            else:
                new_lines.append(line)
                
        # Append keys if not replaced
        if not keys_replaced["DEEPSEEK_API_KEY"]:
            new_lines.append(f"DEEPSEEK_API_KEY={deepseek_key}\n")
        if not keys_replaced["SILICONFLOW_API_KEY"]:
            new_lines.append(f"SILICONFLOW_API_KEY={siliconflow_key}\n")
        if not keys_replaced["ALI_BAILIAN_API_KEY"]:
            new_lines.append(f"ALI_BAILIAN_API_KEY={ali_bailian_key}\n")
        if not keys_replaced["ZHONGZHAN_GPT_API_KEY"]:
            new_lines.append(f"ZHONGZHAN_GPT_API_KEY={zhongzhan_gpt_key}\n")
        if not keys_replaced["ZHONGZHAN_GPT_BASE_URL"]:
            new_lines.append(f"ZHONGZHAN_GPT_BASE_URL={zhongzhan_gpt_base_url}\n")
        if not keys_replaced["ZHONGZHAN_GPT_OCR_MODEL"]:
            new_lines.append(f"ZHONGZHAN_GPT_OCR_MODEL={zhongzhan_gpt_ocr_model}\n")
        if not keys_replaced["ZHONGZHAN_CLAUDE_API_KEY"]:
            new_lines.append(f"ZHONGZHAN_CLAUDE_API_KEY={zhongzhan_claude_key}\n")
        if not keys_replaced["ZHONGZHAN_CLAUDE_BASE_URL"]:
            new_lines.append(f"ZHONGZHAN_CLAUDE_BASE_URL={zhongzhan_claude_base_url}\n")
        if not keys_replaced["ZHONGZHAN_CLAUDE_OCR_MODEL"]:
            new_lines.append(f"ZHONGZHAN_CLAUDE_OCR_MODEL={zhongzhan_claude_ocr_model}\n")
        if not keys_replaced["MINERU_API_TOKEN"]:
            new_lines.append(f"MINERU_API_TOKEN={mineru_token}\n")
        if not keys_replaced["MINERU_API_BASE"]:
            new_lines.append(f"MINERU_API_BASE={mineru_api_base}\n")
        if not keys_replaced["CUSTOM_PROVIDERS_JSON"]:
            new_lines.append(f"CUSTOM_PROVIDERS_JSON={custom_providers_clean}\n")
        if not keys_replaced["DEEPSEEK_API_BASE"]:
            new_lines.append(f"DEEPSEEK_API_BASE={deepseek_base_url}\n")
        if not keys_replaced["SILICONFLOW_API_BASE"]:
            new_lines.append(f"SILICONFLOW_API_BASE={siliconflow_base_url}\n")
        if not keys_replaced["ALI_BAILIAN_API_BASE"]:
            new_lines.append(f"ALI_BAILIAN_API_BASE={ali_bailian_base_url}\n")
        if not keys_replaced["OCR_PREFER_ENGINE"]:
            new_lines.append(f"OCR_PREFER_ENGINE={prefer_engine}\n")
        if not keys_replaced["SILICONFLOW_OCR_MODEL"]:
            new_lines.append(f"SILICONFLOW_OCR_MODEL={siliconflow_model}\n")
        if not keys_replaced["ALI_BAILIAN_OCR_MODEL"]:
            new_lines.append(f"ALI_BAILIAN_OCR_MODEL={ali_bailian_model}\n")
        if not keys_replaced["PREFER_SOLVE_MODEL"]:
            new_lines.append(f"PREFER_SOLVE_MODEL={prefer_solve_model}\n")
        if not keys_replaced["PREFER_PARSE_MODEL"]:
            new_lines.append(f"PREFER_PARSE_MODEL={prefer_parse_model}\n")
        if not keys_replaced["PREFER_CLASSIFY_MODEL"]:
            new_lines.append(f"PREFER_CLASSIFY_MODEL={prefer_classify_model}\n")
        if not keys_replaced["PREFER_DRAW_MODEL"]:
            new_lines.append(f"PREFER_DRAW_MODEL={prefer_draw_model}\n")
            
        write_private_text_atomic(ENV_FILE, "".join(new_lines))
            
        # Clean current process env
        os.environ.pop("PIX2TEXT_API_KEY", None)
        os.environ.pop("PIX2TEXT_SERVER_TYPE", None)
        
        os.environ["DEEPSEEK_API_KEY"] = deepseek_key
        os.environ["SILICONFLOW_API_KEY"] = siliconflow_key
        os.environ["ALI_BAILIAN_API_KEY"] = ali_bailian_key
        os.environ["ZHONGZHAN_GPT_API_KEY"] = zhongzhan_gpt_key
        os.environ["ZHONGZHAN_GPT_BASE_URL"] = zhongzhan_gpt_base_url
        os.environ["ZHONGZHAN_GPT_OCR_MODEL"] = zhongzhan_gpt_ocr_model
        os.environ["ZHONGZHAN_CLAUDE_API_KEY"] = zhongzhan_claude_key
        os.environ["ZHONGZHAN_CLAUDE_BASE_URL"] = zhongzhan_claude_base_url
        os.environ["ZHONGZHAN_CLAUDE_OCR_MODEL"] = zhongzhan_claude_ocr_model
        
        os.environ["MINERU_API_TOKEN"] = mineru_token
        os.environ["MINERU_API_BASE"] = mineru_api_base
        os.environ["CUSTOM_PROVIDERS_JSON"] = custom_providers_clean
        os.environ["DEEPSEEK_API_BASE"] = deepseek_base_url
        os.environ["SILICONFLOW_API_BASE"] = siliconflow_base_url
        os.environ["ALI_BAILIAN_API_BASE"] = ali_bailian_base_url

        os.environ["OCR_PREFER_ENGINE"] = prefer_engine
        os.environ["SILICONFLOW_OCR_MODEL"] = siliconflow_model
        os.environ["ALI_BAILIAN_OCR_MODEL"] = ali_bailian_model
        os.environ["PREFER_SOLVE_MODEL"] = prefer_solve_model
        os.environ["PREFER_PARSE_MODEL"] = prefer_parse_model
        os.environ["PREFER_CLASSIFY_MODEL"] = prefer_classify_model
        os.environ["PREFER_DRAW_MODEL"] = prefer_draw_model
        
        return {"status": "success", "message": "API 与首选大模型配置已成功保存并即时生效！"}
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"保存配置失败: {str(e)}"},
            status_code=500
        )


@router.get("/api/mineru/test")
def test_mineru_connection():
    """验证已保存的 MinerU API Token 是否有效（不真正解析文件）。"""

    from mathbank import mineru_client

    if not mineru_client.is_configured():
        return {
            "status": "error",
            "message": "尚未配置 MinerU API Token，请先在设置面板填写后保存。",
        }
    try:
        result = mineru_client.test_connection()
    except mineru_client.MineruError as exc:
        return {"status": "error", "message": str(exc)}
    if result.get("ok"):
        return {"status": "success", "message": result.get("message", "连接成功。")}
    return {"status": "error", "message": result.get("message", "连接失败。")}




@router.get("/api/version")
def get_version_info():
    """Return local version info."""
    from mathbank import __version__, GITHUB_REPO
    is_git_repo = (PROJECT_ROOT / ".git").exists()
    return {
        "current_version": __version__,
        "repo": GITHUB_REPO,
        "is_git_repo": is_git_repo,
        "server_instance_id": SERVER_INSTANCE_ID,
    }



@router.get("/api/version/check-update")
def check_version_update():
    """Check for latest release on GitHub."""
    from mathbank import __version__, GITHUB_REPO
    from mathbank.ai_http import robust_request_get
    
    is_git_repo = (PROJECT_ROOT / ".git").exists()
    current_ver = __version__
    
    result = {
        "status": "success",
        "current_version": current_ver,
        "latest_version": current_ver,
        "has_update": False,
        "release_title": "",
        "release_body": "",
        "release_url": f"https://github.com/{GITHUB_REPO}/releases/latest",
        "published_at": "",
        "assets": {},
        "is_git_repo": is_git_repo
    }
    
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "MathBank-Question-Bank-App"
        }
        resp = robust_request_get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            latest_tag = data.get("tag_name", "").strip()
            latest_ver = latest_tag.lstrip("vV")
            
            # Compare versions
            current_tuple = parse_version_tuple(current_ver)
            latest_tuple = parse_version_tuple(latest_ver)
            
            has_update = latest_tuple > current_tuple
            
            assets_map = {}
            for asset in data.get("assets", []):
                name = asset.get("name", "")
                download_url = asset.get("browser_download_url", "")
                size_mb = round(asset.get("size", 0) / (1024 * 1024), 1)
                download_count = asset.get("download_count", 0)
                if "macOS" in name or "mac" in name.lower() or "darwin" in name.lower():
                    assets_map["macOS"] = {"name": name, "url": download_url, "size_mb": size_mb, "downloads": download_count}
                elif "Windows" in name or "win" in name.lower():
                    assets_map["Windows"] = {"name": name, "url": download_url, "size_mb": size_mb, "downloads": download_count}
            
            result.update({
                "latest_version": latest_tag,
                "has_update": has_update,
                "release_title": data.get("name", "") or latest_tag,
                "release_body": data.get("body", ""),
                "release_url": data.get("html_url", result["release_url"]),
                "published_at": data.get("published_at", ""),
                "assets": assets_map
            })
        else:
            result["status"] = "warning"
            result["message"] = f"GitHub API 返回状态码: {resp.status_code}"
    except Exception as e:
        result["status"] = "warning"
        result["message"] = f"检查更新超时或失败: {str(e)}"
        
    return result



@router.get("/api/config/metadata")
def get_metadata_config():
    return get_metadata_cache()


@router.post("/api/admin/reload-metadata")
def reload_metadata_config(x_local_token: str = Header(None, alias="X-Local-Token")):
    """Force the running server to re-read custom_metadata.json from disk.

    Useful after repairing the knowledge tree out of band (removing misplaced
    kaoxiang, restoring a section from the canonical preset) so the change takes
    effect without restarting the app.
    """

    if not x_local_token or not secrets.compare_digest(x_local_token, LOCAL_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")
    reloaded = reload_metadata_if_changed(force=True)
    return {
        "status": "success",
        "reloaded": reloaded,
        "message": "知识点大纲已重新加载。" if reloaded else "大纲文件无变化，无需重载。",
    }


@router.post("/api/ai/models")
def list_ai_provider_models(
    payload: dict,
    x_local_token: str = Header(None, alias="X-Local-Token"),
):
    """Pull the live model list from a provider's OpenAI-compatible /models API.

    Lets the settings UI offer the models that actually exist upstream instead of
    a hard-coded guess. The API key is never echoed back: only model ids, the
    resolved base URL and a count are returned.  ``api_key`` / ``api_base`` may be
    supplied in the body so a key typed but not yet saved can be verified.
    """

    if not x_local_token or not secrets.compare_digest(x_local_token, LOCAL_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = payload if isinstance(payload, dict) else {}
    provider = str(body.get("provider") or "").strip()
    api_base_override = str(body.get("api_base") or "").strip()
    api_key_override = str(body.get("api_key") or "").strip()

    code, label, api_base, api_key = resolve_provider_endpoint(provider)
    if code is None:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": f"未知的模型提供商: {provider or '(未填写)'}",
            },
        )
    if api_base_override:
        api_base = api_base_override
    if api_key_override:
        api_key = api_key_override
    if not api_key:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": f"未配置 {label} 的 API Key，无法拉取模型列表。",
            },
        )

    # HTTP header 只允许 latin-1；用户粘贴时常带中文/全角空格，提前校验避免 requests 内部抛 UnicodeEncodeError
    try:
        api_key.encode("latin-1")
    except UnicodeEncodeError:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": f"{label} 的 API Key 包含非 ASCII 字符（可能是全角空格、中文或复制时多带了空白），请重新粘贴纯 ASCII 的 Key。",
            },
        )

    url = build_models_url(api_base)
    if not url:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": f"未配置 {label} 的 API Base，无法拉取模型列表。",
            },
        )

    try:
        response = robust_request_get(
            url,
            headers=build_bearer_headers(api_key),
            timeout=20,
        )
    except requests.exceptions.RequestException as exc:
        return JSONResponse(
            status_code=502,
            content={
                "status": "error",
                "message": f"连接 {label} 失败（{type(exc).__name__}），请检查网络或 Base URL。",
            },
        )
    except Exception as exc:  # noqa: BLE001 - 兜底未预期异常
        import traceback
        tb = traceback.format_exc()
        print(f"[/api/ai/models] unexpected error: {tb}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"拉取 {label} 模型列表时发生未预期错误（{type(exc).__name__}: {exc}）。",
                "traceback": tb[-2000:],
            },
        )

    if response.status_code != 200:
        return JSONResponse(
            status_code=502,
            content={
                "status": "error",
                "message": f"{label} 返回 HTTP {response.status_code}，请检查 API Key 与 Base URL。",
            },
        )

    try:
        model_payload = response.json()
    except ValueError:
        return JSONResponse(
            status_code=502,
            content={
                "status": "error",
                "message": f"{label} 返回了非 JSON 响应，无法解析模型列表。",
            },
        )

    model_ids = parse_model_ids(model_payload)
    return {
        "status": "success",
        "provider": code,
        "provider_label": label,
        "api_base": api_base,
        "count": len(model_ids),
        "models": model_ids,
    }



@router.get("/api/config/curriculum-presets/{version}")
def get_curriculum_preset_config(version: str):
    try:
        return get_curriculum_preset(version)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc



@router.post("/api/config/curriculum-presets/{version}")
def save_curriculum_preset_config(version: str, payload: dict, x_local_token: str = Header(None, alias="X-Local-Token")):
    if not x_local_token or not secrets.compare_digest(x_local_token, LOCAL_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        from mathbank.curriculums import save_curriculum_preset
        curriculum = payload.get("curriculum") if isinstance(payload, dict) else None
        if curriculum is None:
            raise HTTPException(status_code=400, detail="缺少 curriculum 字段")
        save_curriculum_preset(version, curriculum)
        return {"ok": True, "version": version}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



def route_chapter(comp: str, chap: str, know: str, target: str) -> tuple[str, str, str]:
    """跨大纲版本智能章节与小节路由翻译算法，返回 (new_compulsory, new_chapter, new_knowledge)"""
    combined = f"{comp} {chap} {know}"
    new_comp, new_chap = "", ""
    if target == "A":
        if "集合" in combined: new_comp, new_chap = "必修一", "1. 集合与常用逻辑用语"
        elif "逻辑" in combined: new_comp, new_chap = "必修一", "1. 集合与常用逻辑用语"
        elif "等式" in combined or "不等式" in combined: new_comp, new_chap = "必修一", "2. 一元二次函数、方程和不等式"
        elif "指数" in combined or "对数" in combined: new_comp, new_chap = "必修一", "4. 指数函数与对数函数"
        elif "三角函数" in combined or "三角恒等" in combined: new_comp, new_chap = "必修一", "5. 三角函数"
        elif "函数" in combined: new_comp, new_chap = "必修一", "3. 函数的概念与性质"
        elif "解三角形" in combined or "正弦" in combined or "余弦" in combined: new_comp, new_chap = "必修二", "6. 平面向量及其应用"
        elif "数量积" in combined or "平面向量" in combined: new_comp, new_chap = "必修二", "6. 平面向量及其应用"
        elif "复数" in combined: new_comp, new_chap = "必修二", "7. 复数"
        elif "立体几何" in combined and "空间向量" not in combined: new_comp, new_chap = "必修二", "8. 立体几何初步"
        elif "空间向量" in combined: new_comp, new_chap = "选修一", "1. 空间向量与立体几何"
        elif "直线" in combined or "圆的方程" in combined: new_comp, new_chap = "选修一", "2. 直线和圆的方程"
        elif "圆" in combined and "圆锥曲线" not in combined: new_comp, new_chap = "选修一", "2. 直线和圆的方程"
        elif "圆锥曲线" in combined or "椭圆" in combined or "双曲线" in combined or "抛物线" in combined: new_comp, new_chap = "选修一", "3. 圆锥曲线的方程"
        elif "解析几何" in combined: new_comp, new_chap = "选修一", "2. 直线和圆的方程"
        elif "数列" in combined: new_comp, new_chap = "选修二", "4. 数列"
        elif "导数" in combined: new_comp, new_chap = "选修二", "5. 一元函数的导数及其应用"
        elif "计数" in combined or "排列" in combined or "组合" in combined or "二项式" in combined: new_comp, new_chap = "选修三", "6. 计数原理"
        elif "概率" in combined or "随机变量" in combined or "分布" in combined: new_comp, new_chap = "选修三", "7. 随机变量及其分布"
        elif "统计" in combined or "回归" in combined or "独立性" in combined or "成对" in combined: new_comp, new_chap = "选修三", "8. 成对数据的统计分析"
        else: new_comp, new_chap = "必修一", "1. 集合与常用逻辑用语"
    elif target == "B":
        if "集合" in combined: new_comp, new_chap = "必修一", "第一章 集合与常用逻辑用语"
        elif "逻辑" in combined: new_comp, new_chap = "必修一", "第一章 集合与常用逻辑用语"
        elif "等式" in combined or "不等式" in combined: new_comp, new_chap = "必修一", "第二章 等式与不等式"
        elif "指数" in combined or "对数" in combined: new_comp, new_chap = "必修二", "第四章 指数函数、对数函数与幂函数"
        elif "三角函数" in combined: new_comp, new_chap = "必修三", "第七章 三角函数"
        elif "函数" in combined: new_comp, new_chap = "必修一", "第三章 函数"
        elif "解三角形" in combined or "正弦" in combined or "余弦" in combined: new_comp, new_chap = "必修四", "第九章 解三角形"
        elif "数量积" in combined or "三角恒等" in combined: new_comp, new_chap = "必修三", "第八章 向量的数量积与三角恒等变换"
        elif "平面向量" in combined: new_comp, new_chap = "必修二", "第六章 平面向量初步"
        elif "复数" in combined: new_comp, new_chap = "必修四", "第十章 复数"
        elif "立体几何" in combined and "空间向量" not in combined: new_comp, new_chap = "必修四", "第十一章 立体几何初步"
        elif "空间向量" in combined: new_comp, new_chap = "选修一", "第一章 空间向量与立体几何"
        elif "直线" in combined or "圆" in combined or "圆锥曲线" in combined or "椭圆" in combined or "双曲线" in combined or "抛物线" in combined: new_comp, new_chap = "选修一", "第二章 平面解析几何"
        elif "解析几何" in combined: new_comp, new_chap = "选修一", "第二章 平面解析几何"
        elif "数列" in combined: new_comp, new_chap = "选修三", "第五章 数列"
        elif "导数" in combined: new_comp, new_chap = "选修三", "第六章 导数及其应用"
        elif "计数" in combined or "排列" in combined or "组合" in combined or "二项式" in combined: new_comp, new_chap = "选修二", "第三章 排列、组合与二项式定理"
        elif "随机变量" in combined or "条件概率" in combined or "回归" in combined or "独立性" in combined or "成对" in combined: new_comp, new_chap = "选修二", "第四章 概率与统计"
        elif "统计" in combined or "概率" in combined: new_comp, new_chap = "必修二", "第五章 统计与概率"
        else: new_comp, new_chap = "必修一", "第一章 集合与常用逻辑用语"
    elif target == "S":
        if "集合" in combined: new_comp, new_chap = "必修一", "第1章 集合"
        elif "逻辑" in combined: new_comp, new_chap = "必修一", "第2章 常用逻辑用语"
        elif "等式" in combined or "不等式" in combined: new_comp, new_chap = "必修一", "第3章 不等式"
        elif "指数" in combined or "对数" in combined: new_comp, new_chap = "必修一", "第4章 指数与对数"
        elif "三角函数" in combined: new_comp, new_chap = "必修一", "第7章 三角函数"
        elif "函数" in combined: new_comp, new_chap = "必修一", "第5章 函数概念与性质"
        elif "解三角形" in combined or "正弦" in combined or "余弦" in combined: new_comp, new_chap = "必修二", "第11章 解三角形"
        elif "数量积" in combined or "平面向量" in combined: new_comp, new_chap = "必修二", "第9章 平面向量"
        elif "三角恒等" in combined: new_comp, new_chap = "必修二", "第10章 三角恒等变换"
        elif "复数" in combined: new_comp, new_chap = "必修二", "第12章 复数"
        elif "立体几何" in combined and "空间向量" not in combined: new_comp, new_chap = "必修二", "第13章 立体几何初步"
        elif "空间向量" in combined: new_comp, new_chap = "选修二", "第6章 空间向量与立体几何"
        elif "直线" in combined: new_comp, new_chap = "选修一", "第1章 直线与方程"
        elif "圆" in combined and "圆锥曲线" not in combined: new_comp, new_chap = "选修一", "第2章 圆与方程"
        elif "圆锥曲线" in combined or "椭圆" in combined or "双曲线" in combined or "抛物线" in combined: new_comp, new_chap = "选修一", "第3章 圆锥曲线与方程"
        elif "解析几何" in combined: new_comp, new_chap = "选修一", "第1章 直线与方程"
        elif "数列" in combined: new_comp, new_chap = "选修一", "第4章 数列"
        elif "导数" in combined: new_comp, new_chap = "选修一", "第5章 导数及其应用"
        elif "计数" in combined or "排列" in combined or "组合" in combined or "二项式" in combined: new_comp, new_chap = "选修二", "第7章 计数原理"
        elif "随机变量" in combined or "条件概率" in combined: new_comp, new_chap = "选修二", "第8章 概率"
        elif "回归" in combined or "独立性" in combined or "成对" in combined: new_comp, new_chap = "选修二", "第9章 统计"
        elif "统计" in combined: new_comp, new_chap = "必修二", "第14章 统计"
        elif "概率" in combined: new_comp, new_chap = "必修二", "第15章 概率"
        else: new_comp, new_chap = "必修一", "第1章 集合"
    elif target == "H":
        if "集合与逻辑" in combined or ("集合" in combined and "选修" not in comp): new_comp, new_chap = "必修一", "第 1 章 集合与逻辑"
        elif "等式" in combined or "不等式" in combined: new_comp, new_chap = "必修一", "第 2 章 等式与不等式"
        elif "幂、指数" in combined or "指数与对数" in combined or ("指数" in combined and "函数" not in combined) or ("对数" in combined and "函数" not in combined): new_comp, new_chap = "必修一", "第 3 章 幂、指数与对数"
        elif "幂函数" in combined or "指数函数" in combined or "对数函数" in combined: new_comp, new_chap = "必修一", "第 4 章 幂函数、指数函数与对数函数"
        elif "反函数" in combined or "函数的概念" in combined or ("函数" in combined and "三角" not in combined and "导数" not in combined and "选修" not in comp and "必修二" not in comp and "必修三" not in comp): new_comp, new_chap = "必修一", "第 5 章 函数的概念、性质及应用"
        elif "解三角形" in combined or "正弦定理" in combined or "余弦定理" in combined or "常用三角公式" in combined or ("三角" in combined and "函数" not in combined): new_comp, new_chap = "必修二", "第 6 章 三角"
        elif "三角函数" in combined: new_comp, new_chap = "必修二", "第 7 章 三角函数"
        elif "平面向量" in combined or ("向量" in combined and "空间" not in combined): new_comp, new_chap = "必修二", "第 8 章 平面向量"
        elif "复数" in combined: new_comp, new_chap = "必修二", "第 9 章 复数"
        elif "空间直线" in combined or "空间点" in combined or ("立体几何" in combined and "空间向量" not in combined and "简单几何体" not in combined and "球" not in combined and "柱体" not in combined and "锥体" not in combined): new_comp, new_chap = "必修三", "第 10 章 空间直线与平面"
        elif "简单几何体" in combined or "柱体" in combined or "锥体" in combined or "多面体" in combined or "球" in combined: new_comp, new_chap = "必修三", "第 11 章 简单几何体"
        elif "古典概" in combined or "随机现象" in combined or ("概率" in combined and "条件概率" not in combined and "随机变量" not in combined and "分布" not in combined and "选修" not in comp): new_comp, new_chap = "必修三", "第 12 章 概率初步"
        elif "总体与样本" in combined or "抽样" in combined or "统计图表" in combined or ("统计" in combined and "成对" not in combined and "回归" not in combined and "列联表" not in combined and "选修" not in comp): new_comp, new_chap = "必修三", "第 13 章 统计"
        elif "红绿灯" in combined or "优惠券" in combined or "车辆转弯" in combined or "雨中行" in combined or "出租车" in combined or "家具" in combined or "登山" in combined or "包装彩带" in combined or "削菠萝" in combined or "高度测量" in combined or "外卖" in combined or "必修四" in comp: new_comp, new_chap = "必修四", "第 1 部分 数学建模活动案例"
        elif "平面直角坐标系中的直线" in combined or "直线与方程" in combined or ("直线" in combined and "空间" not in combined and "圆锥曲线" not in combined): new_comp, new_chap = "选修一", "第 1 章 平面直角坐标系中的直线"
        elif "圆锥曲线" in combined or "椭圆" in combined or "双曲线" in combined or "抛物线" in combined or ("圆" in combined and "圆锥曲线" in combined): new_comp, new_chap = "选修一", "第 2 章 圆锥曲线"
        elif "空间向量" in combined: new_comp, new_chap = "选修一", "第 3 章 空间向量及其应用"
        elif "数列" in combined or "等差数列" in combined or "等比数列" in combined or "数学归纳法" in combined: new_comp, new_chap = "选修一", "第 4 章 数列"
        elif "导数" in combined: new_comp, new_chap = "选修二", "第 5 章 导数及其应用"
        elif "计数原理" in combined or "排列" in combined or "组合" in combined or "二项式" in combined: new_comp, new_chap = "选修二", "第 6 章 计数原理"
        elif "条件概率" in combined or "随机变量" in combined or "常用分布" in combined or "二项分布" in combined or "正态分布" in combined: new_comp, new_chap = "选修二", "第 7 章 概率初步（续）"
        elif "成对数据" in combined or "线性回归" in combined or "列联表" in combined or "独立性检验" in combined or "回归" in combined: new_comp, new_chap = "选修二", "第 8 章 成对数据的统计分析"
        elif "刹车距离" in combined or "易拉罐" in combined or "珠穆朗玛峰" in combined or "水葫芦" in combined or "铅球" in combined or "电梯调度" in combined or "存款计划" in combined or "民生巨变" in combined or "教室里的照明" in combined or "选修三" in comp: new_comp, new_chap = "选修三", "第 1 部分 数学建模活动案例"
        else: new_comp, new_chap = "必修一", "第 1 章 集合与逻辑"

    active_v = get_active_version_code()
    if target == active_v:
        c_tree = get_metadata_cache().get("curriculum", {})
    else:
        try:
            c_tree = load_curriculum(target)
        except ValueError:
            c_tree = {}
    
    valid_knows = c_tree.get(new_comp, {}).get(new_chap, [])
    valid_know_names = {
        section.get("name") if isinstance(section, dict) else section
        for section in valid_knows
    }
    new_know = know if know in valid_know_names else ""
    return new_comp, new_chap, new_know



@router.post("/api/config/metadata")
def save_metadata_config(
    payload: dict,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    # Validation
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求 Payload 格式错误")
        
    for field in ["applicable_grades", "question_types", "difficulties", "curriculum"]:
        if field not in payload:
            raise HTTPException(status_code=400, detail=f"元数据配置缺少核心字段: '{field}'")
            
    # Simple validate lists
    if not isinstance(payload["applicable_grades"], list) or not isinstance(payload["question_types"], list) or not isinstance(payload["difficulties"], list):
        raise HTTPException(status_code=400, detail="applicable_grades、question_types 或 difficulties 必须是数组列表")
        
    if not isinstance(payload["curriculum"], dict):
        raise HTTPException(status_code=400, detail="curriculum 必须是字典对象")

    # Normalize curriculum so section leaves are always {name, topics} objects.
    payload = dict(payload)
    normalized_curriculum, _ = normalize_metadata_curriculum(payload.get("curriculum", {}))
    payload["curriculum"] = normalized_curriculum

    old_metadata = get_metadata_cache()
    metadata_path = Path(metadata_module.METADATA_FILE)
    old_file_contents = (
        metadata_path.read_text(encoding="utf-8") if metadata_path.exists() else None
    )
    file_replaced = False
    transaction_committed = False

    # Update the curriculum mirror and metadata as one compensated operation.
    try:
        source_version = get_active_version_code()
        # Detect target version
        curriculum = payload.get("curriculum", {})
        combined_chapters = ""
        for book_content in curriculum.values():
            if isinstance(book_content, dict):
                combined_chapters += " ".join(book_content.keys())
        if "不等式·比较大小·选填技巧" in curriculum or "集合·复数·逻辑" in curriculum:
            target_version = "G"
        elif "第一章" in combined_chapters:
            target_version = "B"
        elif "第 1 章 集合与逻辑" in combined_chapters or "数学建模活动案例" in combined_chapters or "第 2 章 等式与不等式" in combined_chapters or "第 3 章 幂、指数与对数" in combined_chapters:
            target_version = "H"
        elif "第1章" in combined_chapters:
            target_version = "S"
        else:
            target_version = "A"

        # Incremental migration if curriculum version shifts
        # G (高三专题复习) is not isomorphic with textbook trees: skip auto-migration.
        if source_version != target_version and target_version != "G":
            # Check and run incremental migration for all questions that do not have classifications for target_version
            all_questions = db.query(Question).all()
            for q in all_questions:
                target_map = db.query(QuestionCurriculum).filter(
                    QuestionCurriculum.question_id == q.id,
                    QuestionCurriculum.version_code == target_version
                ).first()
                if not target_map or not target_map.compulsory:
                    source_map = db.query(QuestionCurriculum).filter(
                        QuestionCurriculum.question_id == q.id,
                        QuestionCurriculum.version_code == source_version
                    ).first()
                    if source_map and source_map.compulsory:
                        new_comp, new_chap, new_know = route_chapter(
                            source_map.compulsory, source_map.chapter, source_map.knowledge, target_version
                        )
                        if not target_map:
                            target_map = QuestionCurriculum(
                                question_id=q.id,
                                version_code=target_version
                            )
                            db.add(target_map)
                        target_map.compulsory = new_comp
                        target_map.chapter = new_chap
                        target_map.knowledge = new_know
        # Batch update main questions table categories with target version values
        from sqlalchemy import text
        db.flush()
        db.execute(text("""
            UPDATE questions 
            SET category_compulsory = COALESCE((SELECT compulsory FROM question_curriculums WHERE question_id = questions.id AND version_code = :v), ''),
                category_chapter = COALESCE((SELECT chapter FROM question_curriculums WHERE question_id = questions.id AND version_code = :v), ''),
                category_knowledge = COALESCE((SELECT knowledge FROM question_curriculums WHERE question_id = questions.id AND version_code = :v), '')
        """), {"v": target_version})

        write_private_text_atomic(
            metadata_path,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
        file_replaced = True
        db.commit()
        transaction_committed = True
    except Exception as e:
        db.rollback()
        if not transaction_committed:
            set_metadata_cache(old_metadata)
            set_current_curriculum(old_metadata.get("curriculum"))
        if file_replaced and not transaction_committed:
            try:
                if old_file_contents is None:
                    metadata_path.unlink(missing_ok=True)
                else:
                    write_private_text_atomic(metadata_path, old_file_contents)
            except OSError as restore_error:
                print(
                    "[Metadata] Failed to restore metadata after DB rollback "
                    f"(type={type(restore_error).__name__})."
                )
        raise HTTPException(status_code=500, detail=f"保存元数据失败: {str(e)}")

    # Everything below is post-commit and must not change the successful save
    # into an error response or compensate already-durable database changes.
    set_metadata_cache(payload)
    set_current_curriculum(payload.get("curriculum"))
    print(
        f"[Metadata] Saved new custom metadata to {metadata_module.METADATA_FILE} "
        f"(Detected version: {target_version})"
    )
    schedule_database_export(background_tasks, operation="save_metadata")
    return {"status": "success", "message": "元数据配置保存成功！"}

