# -*- coding: utf-8 -*-
"""MinerU 云端解析结果缓存。

一次 MinerU 云解析要烧掉一份真实额度，而「同一份 PDF 反复拆」是日常操作：
换提示词、换拆题模型、上一次任务被取消、纯粹想再导一遍。这些情况下云端返回的
Markdown 与插图完全等价，没有任何理由再付一次钱。

缓存键 = （PDF 字节 SHA-256，model_version，api_base）。三者任一变化都会落到
未命中：文件内容不同自然要重解；``model_version`` 变了结果口径就变了；自建与官方
地址之间更不可能互通。

⚠️ 命中后必须把结果**复写进本次 workdir**，不得直接把缓存目录交给调用方：
上层会 ``shutil.rmtree(workdir)``（见 routes_paper 的 finally），指向缓存目录等于
让缓存自我删除。

缓存不可用（缺文件、清单损坏、配额）一律静默回退真实调用 —— 省额度的优化永远
不能变成解析失败的新原因。
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import shutil
import time
from pathlib import Path

from mathbank.paths import IS_TESTING, SYSTEM_GENERATED_DIR

CACHE_FORMAT_VERSION = 1
_MANIFEST_NAME = "manifest.json"
_RESULT_ARCHIVE_NAME = "mineru_result.zip"
_staging_counter = itertools.count(1)


def cache_root() -> Path:
    """缓存根目录。测试态单独落一份，避免污染真实缓存。"""

    if IS_TESTING:
        return SYSTEM_GENERATED_DIR / "mineru_cache_test"
    override = (os.getenv("MINERU_CACHE_DIR") or "").strip()
    if override:
        return Path(override)
    return SYSTEM_GENERATED_DIR / "mineru_cache"


def is_enabled() -> bool:
    """默认开启；设 MINERU_CACHE_DISABLE=1 可强制每次真实调用。"""

    return (os.getenv("MINERU_CACHE_DISABLE") or "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }


def cache_key(file_bytes: bytes, model_version: str, api_base: str) -> str:
    digest = hashlib.sha256(file_bytes).hexdigest()
    scope = hashlib.sha256(
        f"{CACHE_FORMAT_VERSION}|{model_version}|{api_base.rstrip('/')}".encode(
            "utf-8"
        )
    ).hexdigest()[:16]
    return f"{digest[:32]}-{scope}"


def _entry_dir(key: str) -> Path:
    # key 由哈希生成，但仍做显式校验，杜绝任何路径穿越写法。
    if not _is_safe_key(key):
        raise ValueError("非法的缓存键")
    return cache_root() / key


def _is_safe_key(key: str) -> bool:
    # 32 位摘要 + "-" + 16 位作用域摘要，全部十六进制字符。
    if not isinstance(key, str) or len(key) != 49 or key[32] != "-":
        return False
    allowed = set("0123456789abcdef")
    return all(c in allowed for c in key[:32] + key[33:])


def read_manifest(key: str) -> dict | None:
    try:
        entry = _entry_dir(key)
    except ValueError:
        return None
    manifest = entry / _MANIFEST_NAME
    if not manifest.is_file():
        return None
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("format_version") != CACHE_FORMAT_VERSION:
        return None
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        return None
    return payload


def load(key: str, workdir: str | Path) -> dict | None:
    """命中则把缓存复写到 ``workdir`` 并返回 parse_pdf 同构结果；否则返回 None。"""

    if not is_enabled():
        return None
    payload = read_manifest(key)
    if payload is None:
        return None
    entry = _entry_dir(key)
    target = Path(workdir)
    copied: list[Path] = []
    try:
        target.mkdir(parents=True, exist_ok=True)
        for name, meta in payload["files"].items():
            if not isinstance(meta, dict):
                return None
            relative = _safe_relative(name)
            if relative is None:
                return None
            source = entry / relative
            if not source.is_file():
                return None
            size = meta.get("size")
            if isinstance(size, int) and source.stat().st_size != size:
                return None
            digest = meta.get("sha256")
            if isinstance(digest, str) and digest and _sha256_file(source) != digest:
                return None
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.append(destination)
    except OSError:
        return None

    markdown_name = str(payload.get("markdown_name") or "")
    markdown_path = next(
        (p for p in copied if p.name == markdown_name), None
    )
    if markdown_path is None:
        return None
    # 图片按扩展名重新收集，而不是照清单里的名字捞：必须与未命中路径的
    # collect_images() 完全同构（含 images/ 子目录前缀），否则上层两条下游会退化——
    # stage_images 的键形态不一致，collect_page_texts 更会因为清单没被复写而恒空。
    image_paths = mineru_image_files(target)
    return {
        "markdown": _read_text(markdown_path),
        "markdown_path": str(markdown_path),
        "image_paths": [str(p) for p in image_paths],
        "workdir": str(target),
        "cache_hit": True,
    }


_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})


def mineru_image_files(target_dir: str | Path) -> list[Path]:
    """与 ``mineru_client.collect_images`` 同口径地收集结果包内图片。

    刻意不 import mineru_client（它会拉进 requests），保持本模块可独立导入。
    """

    target = Path(str(target_dir))
    return sorted(
        path
        for path in target.rglob("*")
        if path.is_file()
        and path.suffix.lower() in _IMAGE_EXTENSIONS
        and path.name != _MANIFEST_NAME
    )


def store(key: str, workdir: str | Path, markdown_path: str) -> bool:
    """把本次结果**整包**写入缓存（保留相对路径）。任何失败只意味着下次继续付费调用。"""

    if not is_enabled():
        return False
    try:
        entry = _entry_dir(key)
    except ValueError:
        return False
    source_dir = Path(str(workdir))
    if not source_dir.is_dir():
        return False
    staging = cache_root() / f".tmp-{os.getpid()}-{next(_staging_counter):06d}"
    try:
        staging.mkdir(parents=True, exist_ok=False)
        files: dict[str, dict] = {}
        markdown_name = ""
        if markdown_path:
            markdown_name = Path(markdown_path).name
        for source in sorted(path for path in source_dir.rglob("*") if path.is_file()):
            try:
                relative = source.relative_to(source_dir)
            except ValueError:
                continue
            # 云端产物里唯一不需要归档的是结果包 zip 本体（可由内容重新解压）。
            if relative.name == _RESULT_ARCHIVE_NAME:
                continue
            entry_meta = _stage_file(source, staging, relative)
            if entry_meta is None:
                return False
            files[relative.as_posix()] = entry_meta
        if not files:
            return False
        manifest = {
            "format_version": CACHE_FORMAT_VERSION,
            "markdown_name": markdown_name,
            "files": files,
        }
        (staging / _MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        final = entry
        if final.exists():
            shutil.rmtree(final, ignore_errors=True)
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, final)
        return True
    except OSError:
        return False
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _safe_relative(name: str) -> Path | None:
    relative = Path(str(name))
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        return None
    return relative


def _stage_file(source: Path, staging: Path, relative: Path) -> dict | None:
    safe = _safe_relative(relative.as_posix())
    if safe is None:
        return None
    destination = staging / safe
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, destination)
    except OSError:
        return None
    return {"size": destination.stat().st_size, "sha256": _sha256_file(destination)}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def purge(*, older_than_days: int | None = None) -> int:
    """清理缓存目录，返回删除的条目数。供运维脚本调用。"""

    root = cache_root()
    if not root.is_dir():
        return 0
    cutoff = None
    if older_than_days is not None:
        cutoff = time.time() - max(0, int(older_than_days)) * 86400
    removed = 0
    for candidate in sorted(root.iterdir()):
        if not candidate.is_dir():
            continue
        if cutoff is not None and not candidate.name.startswith(".tmp-"):
            try:
                if candidate.stat().st_mtime > cutoff:
                    continue
            except OSError:
                continue
        shutil.rmtree(candidate, ignore_errors=True)
        removed += 1
    return removed


__all__ = [
    "CACHE_FORMAT_VERSION",
    "cache_key",
    "cache_root",
    "is_enabled",
    "load",
    "mineru_image_files",
    "purge",
    "read_manifest",
    "store",
]
