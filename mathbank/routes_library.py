"""Read-only library endpoints: statistics, category tree and source list."""

from __future__ import annotations

import datetime
from copy import deepcopy

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from mathbank.database import Question, get_db
from mathbank.curriculums import DEFAULT_DIFFICULTIES, get_current_curriculum
from mathbank.metadata import get_metadata_cache, reload_metadata_if_changed

router = APIRouter(tags=["library"])


def _compulsory_sort_key(comp_name: str):
    if not comp_name or comp_name == "未分类":
        return (99, 99, comp_name or "")
    num_map = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6}
    is_comp = 0 if ("必修" in comp_name and "选" not in comp_name) else 1
    num = 99
    for k, v in num_map.items():
        if k in comp_name:
            num = min(num, v)
    return (is_comp, num, comp_name)


@router.get("/api/stats")
def get_db_stats(db: Session = Depends(get_db)):
    try:
        total = db.query(Question).count()
        # 难度层级取自活跃元数据：在设置里增删难度层级后，统计口径同步变化
        difficulty_defs = get_metadata_cache().get("difficulties") or DEFAULT_DIFFICULTIES
        difficulty_stats = {}
        for item in difficulty_defs:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value") or "").strip()
            if not value or value in difficulty_stats:
                continue
            difficulty_stats[value] = db.query(Question).filter(
                Question.difficulty == value
            ).count()

        # Cascaded Stage & Chapter Counts
        rows = db.query(
            Question.category_compulsory,
            Question.category_chapter
        ).all()

        comp_chap_stats = {}
        for comp, chap in rows:
            comp_val = comp or "未分类"
            chap_val = chap or "未分章节"
            if comp_val not in comp_chap_stats:
                comp_chap_stats[comp_val] = {}
            if chap_val not in comp_chap_stats[comp_val]:
                comp_chap_stats[comp_val][chap_val] = 0
            comp_chap_stats[comp_val][chap_val] += 1

        sorted_comp_chap_stats = {
            k: comp_chap_stats[k]
            for k in sorted(comp_chap_stats.keys(), key=_compulsory_sort_key)
        }

        # Daily additions in local time (UTC+8)
        date_rows = db.query(Question.created_at).all()
        daily_adds = {}
        for (created_at,) in date_rows:
            if created_at:
                local_time = created_at + datetime.timedelta(hours=8)
                date_str = local_time.strftime("%Y-%m-%d")
                daily_adds[date_str] = daily_adds.get(date_str, 0) + 1

        return {
            "status": "success",
            "total_count": total,
            "difficulty_labels": { d["value"]: d["label"] for d in difficulty_defs },
            "difficulty_counts": difficulty_stats,
            "l1_count": difficulty_stats.get("L1", 0),
            "l2_count": difficulty_stats.get("L2", 0),
            "l3_count": difficulty_stats.get("L3", 0),
            "l4_count": difficulty_stats.get("L4", 0),
            "l5_count": difficulty_stats.get("L5", 0),
            "compulsory_chapter_counts": sorted_comp_chap_stats,
            "daily_adds": daily_adds
        }
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"获取统计数据失败: {str(e)}"},
            status_code=500
        )


@router.get("/api/categories")
def list_categories(db: Session = Depends(get_db)):
    # Pick up out-of-band edits to custom_metadata.json (repair scripts, manual
    # cleanup of misplaced kaoxiang) without requiring a server restart.
    reload_metadata_if_changed()
    # 用户在「考向树/」里改了 TXT 之后，刷新页面即可生效。
    try:
        from mathbank.kaoxiang_tree import auto_pull_if_needed

        auto_pull_if_needed()
    except Exception as exc:
        print(f"[KaoxiangTree] 自动拉取跳过 (type={type(exc).__name__})")

    hierarchy = {}
    for comp, chapters in get_current_curriculum().items():
        hierarchy[comp] = {}
        for chap, sections in chapters.items():
            hierarchy[comp][chap] = deepcopy(list(sections))

    # Also fetch any custom entries from DB
    results = db.query(
        Question.category_compulsory,
        Question.category_chapter,
        Question.category_knowledge
    ).distinct().all()

    for comp, chap, know in results:
        if not comp:
            continue
        if comp not in hierarchy:
            hierarchy[comp] = {}
        if not chap:
            continue
        if chap not in hierarchy[comp]:
            hierarchy[comp][chap] = []
        section_names = {
            s.get("name") if isinstance(s, dict) else s
            for s in hierarchy[comp][chap]
        }
        if know and know not in section_names:
            hierarchy[comp][chap].append({"name": know, "topics": []})

    return hierarchy


@router.get("/api/sources")
def get_sources(db: Session = Depends(get_db)):
    results = db.query(Question.source).distinct().all()
    sources = []
    for r in results:
        val = r[0]
        if val and val.strip():
            sources.append(val.strip())

    # Sort alphabetically (case-insensitive)
    sources.sort(key=str.lower)
    return sources


import json as _json
from pathlib import Path as _Path

_SCHOOLS_JSON = _Path(__file__).resolve().parent / "resources" / "schools.json"
_schools_cache: list[dict] | None = None


def _load_schools() -> list[dict]:
    """加载内置学校词库（进程内缓存）。格式 [{name, province, city}]。"""
    global _schools_cache
    if _schools_cache is not None:
        return _schools_cache
    try:
        data = _json.loads(_SCHOOLS_JSON.read_text(encoding="utf-8"))
        _schools_cache = [
            {"name": str(d.get("name") or "").strip(),
             "province": str(d.get("province") or "").strip(),
             "city": str(d.get("city") or "").strip()}
            for d in data if isinstance(d, dict) and str(d.get("name") or "").strip()
        ]
    except Exception:
        _schools_cache = []
    return _schools_cache


@router.get("/api/school-suggestions")
def school_suggestions(prefix: str = "", limit: int = 20, province: str = "", city: str = ""):
    """学校简称智能联想：返回 [{name, province, city}]。
    - prefix 非空：做子串匹配
    - province/city 非空：按省/市过滤（前端级联用）
    - 全空：返回全表 cap 500（前端预填用）
    """
    items = _load_schools()
    p = (prefix or "").strip()
    prov = (province or "").strip()
    cit = (city or "").strip()
    if prov:
        items = [it for it in items if it["province"] == prov]
    if cit:
        items = [it for it in items if it["city"] == cit]
    if p:
        items = [it for it in items if p in it["name"]]
        cap = max(1, min(int(limit or 20), 50))
    else:
        cap = 500
    return {"suggestions": items[:cap]}


@router.get("/api/school-regions")
def school_regions():
    """返回 [{province, cities: [市...]}] 供前端省/市级联下拉。"""
    items = _load_schools()
    m: dict = {}
    for it in items:
        prov = it.get("province") or ""
        city = it.get("city") or ""
        if not prov:
            continue
        m.setdefault(prov, set())
        if city:
            m[prov].add(city)
    return {
        "regions": [
            {"province": prov, "cities": sorted(cities)}
            for prov, cities in sorted(m.items())
        ]
    }
