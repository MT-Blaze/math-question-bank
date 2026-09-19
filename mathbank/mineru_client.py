# -*- coding: utf-8 -*-
"""MinerU 云端解析客户端（Open API v4）。

本地**不需要**安装 MinerU：本模块只用 HTTP 把 PDF 交给 MinerU 云端，
拿回包含 Markdown 与 images/ 的结果包，解压到本地 uploads 目录。

流程（官方 v4）：
1. POST /api/v4/file-urls/batch  申请上传地址，返回 batch_id 与 file_urls
2. PUT  file_url                 上传文件字节（不要带 Content-Type）
3. GET  /api/v4/extract-results/batch/{batch_id}  轮询，state == done 取 full_zip_url
4. 下载 zip 并解压，得到 full.md 与 images/

Token 从环境变量 MINERU_API_TOKEN 读取（在设置面板填写，落在 .env）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

import requests

from mathbank.paths import STATIC_DIR

DEFAULT_API_BASE = "https://mineru.net/api/v4"
DEFAULT_MODEL = os.getenv("MINERU_MODEL_VERSION", "vlm")


def get_api_base() -> str:
    """返回 MinerU API 基础地址。

    仅当用户在设置面板填写了以 http(s) 开头的**完整地址**（自建 / 私有部署）时才采用，
    否则一律回退到官方默认地址。这样可以避免把“接口路径”误当成“基础地址”
    （例如 ``/api/v4/extract/task``），否则拼接出的请求 URL 非法，连接直接失败。
    """

    raw = (os.getenv("MINERU_API_BASE") or "").strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw.rstrip("/")
    return DEFAULT_API_BASE

# 上传/轮询的超时（秒）
UPLOAD_TIMEOUT = 120
POLL_TIMEOUT = 900
POLL_INTERVAL = 3.0

_TOKEN_ENV = "MINERU_API_TOKEN"


class MineruError(RuntimeError):
    """MinerU 调用失败（错误信息可直接展示给用户）。"""


def get_token() -> str:
    return (os.getenv(_TOKEN_ENV) or "").strip()


def is_configured() -> bool:
    return bool(get_token())


def _headers() -> dict:
    token = get_token()
    if not token:
        raise MineruError("未配置 MinerU API Token，请在设置面板填写。")
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }


def _mask(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return "••••"
    return f"{token[:4]}••••{token[-4:]}"


def masked_token() -> str:
    return _mask(get_token())


def _request(method: str, url: str, **kwargs) -> requests.Response:
    try:
        return requests.request(method, url, timeout=kwargs.pop("timeout", 60), **kwargs)
    except requests.exceptions.Timeout:
        raise MineruError("连接 MinerU 超时，请检查网络后重试。")
    except requests.exceptions.RequestException as exc:
        raise MineruError(f"无法连接 MinerU：{exc}")


def _check_json(response: requests.Response, action: str) -> dict:
    if response.status_code in (401, 403):
        raise MineruError("MinerU 拒绝了请求（Token 无效或已过期，错误码 A0202）。")
    if response.status_code == 429:
        raise MineruError("MinerU 触发限流（429），请稍后再试。")
    if response.status_code >= 400:
        raise MineruError(f"{action}失败：HTTP {response.status_code} {response.text[:200]}")
    try:
        payload = response.json()
    except ValueError:
        raise MineruError(f"{action}返回了非 JSON 内容。")
    if payload.get("code") not in (0, None) or payload.get("msg") not in (None, "ok", ""):
        detail = payload.get("msg") or payload.get("message") or ""
        if detail and payload.get("code") not in (0, None):
            raise MineruError(f"{action}失败：{detail}")
    return payload


def create_batch(filename: str, model_version: str = "") -> tuple[str, str]:
    """申请上传地址，返回 ``(batch_id, upload_url)``。"""

    payload = {
        "files": [{"name": filename, "data_id": filename}],
        "model_version": model_version or DEFAULT_MODEL,
        "enable_formula": True,
        "enable_table": True,
    }
    response = _request(
        "POST",
        f"{get_api_base()}/file-urls/batch",
        headers=_headers(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=60,
    )
    data = _check_json(response, "创建解析任务").get("data") or {}
    batch_id = str(data.get("batch_id") or "")
    file_urls = data.get("file_urls") or []
    if not batch_id or not file_urls:
        raise MineruError("MinerU 未返回上传地址，请稍后重试。")
    return batch_id, str(file_urls[0])


def upload_bytes(upload_url: str, file_bytes: bytes) -> None:
    """把文件字节 PUT 到 MinerU 给的上传地址。"""

    response = _request("PUT", upload_url, data=file_bytes, timeout=UPLOAD_TIMEOUT)
    if response.status_code >= 400:
        raise MineruError(f"上传文件失败：HTTP {response.status_code}")


def poll_batch(batch_id: str, timeout: float = POLL_TIMEOUT, on_progress=None) -> dict:
    """轮询直到完成，返回单个文件的解析结果字典。"""

    deadline = time.time() + timeout
    last_state = ""
    while time.time() < deadline:
        response = _request(
            "GET",
            f"{get_api_base()}/extract-results/batch/{batch_id}",
            headers=_headers(),
            timeout=60,
        )
        data = _check_json(response, "查询解析结果").get("data") or {}
        results = data.get("extract_result") or []
        if not results:
            raise MineruError("MinerU 未返回解析结果。")
        item = results[0]
        state = str(item.get("state") or "")
        if state != last_state:
            last_state = state
            if on_progress:
                on_progress(state, item)
        if state == "done":
            return item
        if state in ("failed", "error"):
            raise MineruError(
                "MinerU 解析失败：" + str(item.get("err_msg") or item.get("err_code") or "未知原因")
            )
        time.sleep(POLL_INTERVAL)
    raise MineruError("MinerU 解析超时，请稍后查询结果或减小文件页数。")


def download_zip(zip_url: str, target_dir: str | Path) -> Path:
    """下载结果包并解压，返回解压目录。"""

    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    archive = target / "mineru_result.zip"
    response = _request("GET", zip_url, timeout=180, stream=True)
    if response.status_code >= 400:
        raise MineruError(f"下载解析结果失败：HTTP {response.status_code}")
    with archive.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=65536):
            if chunk:
                handle.write(chunk)
    try:
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(target)
    except zipfile.BadZipFile:
        raise MineruError("下载的结果包损坏，请重试。")
    finally:
        try:
            archive.unlink()
        except OSError:
            pass
    return target


def find_markdown(target_dir: str | Path):
    """在结果目录里找到 Markdown 主文件。"""

    target = Path(target_dir)
    candidates = sorted(target.rglob("*.md"))
    if not candidates:
        return None
    for path in candidates:
        if path.name.lower() in ("full.md", "result.md"):
            return path
    return max(candidates, key=lambda p: p.stat().st_size)


def collect_images(target_dir: str | Path) -> list[Path]:
    """收集结果包里的图片文件。"""

    target = Path(target_dir)
    extensions = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
    return sorted(
        path
        for path in target.rglob("*")
        if path.is_file() and path.suffix.lower() in extensions
    )


# MinerU 结果包的结构化清单（``*_content_list.json``）里每一项都带页码，
# 是「这段内容来自第几页」的唯一线索。不同版本用的键名不完全一致，逐一尝试。
_PAGE_INDEX_KEYS = ("page_idx", "page_no", "page")


def collect_page_texts(target_dir: str | Path) -> list[str]:
    """从结果包里重建「每页文本」，列表索引即页码；拿不到清单时返回空列表。

    原先只读 ``full.md``，逐页信息被整体丢弃，于是 MinerU 路径下无法推断
    「题目来自第几页」（前端也就没有「源自第 N 页 / 查看原页」入口，`_source_page`
    恒缺）。有了这份逐页文本，既有的话题重合度推断（``find_source_page_by_overlap``）
    与插图兜底都能直接复用。

    结果与整页 OCR 产出的 ``ocr_results`` **同构**，所以可以直接替换那个参数。

    ⚠️ 兜底不会因此误挂图：MinerU 的插图落在静态根目录、文件名也不是
    ``pdf_crop_*``，而兜底只认 tmp 下的裁剪图 → 这里实际只产出 ``_source_page``。
    """

    target = Path(target_dir)
    payload: list | None = None
    for path in sorted(target.rglob("*content_list.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if isinstance(data, list) and data:
            payload = data
            break
    if not payload:
        return []

    buckets: dict[int, list[str]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        page_idx = None
        for key in _PAGE_INDEX_KEYS:
            value = item.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)) and value >= 0:
                page_idx = int(value)
                break
        if page_idx is None:
            continue
        chunks = []
        for key in ("text", "table_body"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                chunks.append(value.strip())
        if chunks:
            buckets.setdefault(page_idx, []).append("\n".join(chunks))
    if not buckets:
        return []

    pages = [""] * (max(buckets) + 1)
    for idx, chunks in buckets.items():
        pages[idx] = "\n".join(chunks)
    return pages


def parse_pdf(file_bytes: bytes, filename: str, workdir: str | Path, on_progress=None) -> dict:
    """一步到位：提交 → 轮询 → 下载解压。

    返回 ``{"markdown": str, "markdown_path": str, "image_paths": [str], "workdir": str}``。
    ``image_paths`` 是结果包内图片的**本地磁盘路径**，调用方负责复制到 uploads。
    """

    if not file_bytes:
        raise MineruError("文件内容为空。")
    batch_id, upload_url = create_batch(filename)
    if on_progress:
        on_progress("uploading", {})
    upload_bytes(upload_url, file_bytes)
    if on_progress:
        on_progress("processing", {})
    result = poll_batch(batch_id, on_progress=on_progress)
    zip_url = result.get("full_zip_url") or ""
    if not zip_url:
        raise MineruError("MinerU 未返回结果包地址。")
    workdir = Path(workdir)
    download_zip(zip_url, workdir)
    markdown_path = find_markdown(workdir)
    markdown = ""
    if markdown_path and markdown_path.exists():
        markdown = markdown_path.read_text(encoding="utf-8", errors="replace")
    return {
        "markdown": markdown,
        "markdown_path": str(markdown_path or ""),
        "image_paths": [str(p) for p in collect_images(workdir)],
        "workdir": str(workdir),
    }


def test_connection() -> dict:
    """用一次轻量调用验证 Token 是否有效（不会真正解析文件）。"""

    token = get_token()
    if not token:
        return {"ok": False, "message": "未配置 Token。"}
    response = requests.get(
        f"{get_api_base()}/extract-results/batch/__selftest__",
        headers=_headers(),
        timeout=30,
    )
    if response.status_code in (401, 403):
        return {"ok": False, "message": "Token 无效或已过期（A0202）。"}
    if response.status_code == 429:
        return {"ok": False, "message": "触发限流（429），稍后再试。"}
    # 其他状态码（含 404）都说明鉴权通过了，只是这个 batch 不存在
    return {"ok": True, "message": "Token 校验通过，可以调用解析服务。"}


def stage_images(image_paths: list[str], upload_dir: str | Path, prefix: str = "mineru") -> dict:
    """把结果包里的图片复制进静态目录，返回 ``{原文件名: 相对URL}`` 映射。"""

    upload_dir = Path(upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {}
    for index, raw in enumerate(image_paths or [], start=1):
        source = Path(raw)
        if not source.exists():
            continue
        suffix = source.suffix.lower() or ".png"
        target_name = f"{prefix}_{int(time.time())}_{index}{suffix}"
        target = upload_dir / target_name
        try:
            shutil.copy2(source, target)
        except OSError:
            continue
        try:
            relative = target.relative_to(STATIC_DIR).as_posix()
            # STATIC_DIR 在本应用以 /static 挂载，落盘图片需带此前缀才能被访问。
            mapping[source.name] = f"/static/{relative}"
        except ValueError:
            # 上传目录不在静态根下时退化为按文件名引用（生产环境不会走到这里）。
            mapping[source.name] = f"/{target.name}"
        # 同一文件若有子目录路径，也按带目录的键登记一份，便于匹配
        mapping[str(source)] = mapping[source.name]
    return mapping


def replace_image_refs(markdown: str, mapping: dict) -> str:
    """把 Markdown 中的本地图片引用原位替换为已落盘的 URL。

    MinerU 输出的图片引用可能是 ``images/xxx.png``、``./images/xxx.png``
    或 ``xxx.png``，统一按**文件名**匹配 ``stage_images`` 返回的映射表。
    """

    if not mapping or not markdown:
        return markdown

    by_base = {}
    for key, url in mapping.items():
        by_base[os.path.basename(key)] = url

    img_re = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

    def _replace(match: "re.Match") -> str:
        alt = match.group(1)
        target = match.group(2).strip()
        base = os.path.basename(target)
        url = by_base.get(base) or mapping.get(target) or mapping.get(target.lstrip("./"))
        if url:
            return f"![{alt}]({url})"
        return match.group(0)

    return img_re.sub(_replace, markdown)
