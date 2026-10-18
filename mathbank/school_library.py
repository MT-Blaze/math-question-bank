# -*- coding: utf-8 -*-
"""学校词库访问层（唯一入口）。

schools.json 每条带稳定 id（sch_+sha1[:10]，一经分配即冻结，同考点 topic_id 口径）。
显示层按 school_id 现查 short_name——教师改简称后历史记录自动跟随；
词库删校后回落该行存的 region 快照，不得显示空白。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

_SCHOOLS_JSON = Path(__file__).resolve().parent / "resources" / "schools.json"
_lock = threading.Lock()
_cache: dict | None = None   # {id: {"name","full_name","short_name","province","city"}}


def _read_library() -> dict:
    index = {}
    try:
        data = json.loads(_SCHOOLS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict):
            continue
        sid = str(entry.get("id") or "").strip()
        if not sid:
            continue
        index[sid] = {
            "name": str(entry.get("name") or "").strip(),
            "full_name": str(entry.get("full_name") or "").strip(),
            "short_name": str(entry.get("short_name") or entry.get("name") or "").strip(),
            "province": str(entry.get("province") or "").strip(),
            "city": str(entry.get("city") or "").strip(),
        }
    return index


def get_school_index(*, refresh: bool = False) -> dict:
    """进程内缓存的词库索引；PUT 改名/离线回填后经 invalidate_school_index() 失效。"""
    global _cache
    with _lock:
        if _cache is None or refresh:
            _cache = _read_library()
        return _cache


def invalidate_school_index() -> None:
    global _cache
    with _lock:
        _cache = None


def resolve_school_label(school_id: str, fallback_region: str = "") -> str:
    """school_id → 当前简称；未建档/已删校回落 region 快照原文本。"""
    sid = str(school_id or "").strip()
    if sid:
        entry = get_school_index().get(sid)
        if entry:
            return entry["short_name"] or entry["name"] or (fallback_region or "")
    return fallback_region or ""
