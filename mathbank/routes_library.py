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
_DISPLAY_ORDER_JSON = _Path(__file__).resolve().parent / "resources" / "display_order.json"
# 词库的已建模字段。新增字段要同时改这里：投影与 merge 写回都以它为准，
# 漏一个键就等于该字段会在任何一次改名时被静默抹掉。
_SCHOOL_KEYS = ("id", "name", "full_name", "short_name", "province", "city",
                "tier", "nature", "source")
_schools_cache: list[dict] | None = None
_schools_raw: dict[str, dict] = {}
_schools_fingerprint: tuple | None = None
_display_order_cache: dict | None = None


def _schools_disk_fingerprint() -> tuple | None:
    """磁盘词库的 (mtime_ns, size)；读不到返回 None。用于发现「服务跑着时有人离线改了文件」。"""
    try:
        st = _SCHOOLS_JSON.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _load_schools(*, force: bool = False) -> list[dict]:
    """加载内置学校词库（进程内缓存）。格式 [{id, name, full_name, short_name, province, city,
    tier, nature, source}]。

    id 必须随投影返回：PUT 改名要按 id 定位，前端下拉要靠 data-id 落 school_id；
    曾漏掉 id → _save_schools 写回把全库 id 抹掉，显示层永久回落快照旧文本（2026-10-03 实测）。

    tier/nature/source 同样必须透传：`_save_schools` 是把本函数的返回值整文件写回的，
    这里少带一个键就等于下一次改名静默抹掉该字段（实测已抹掉过全库 347 行）。
    原始行由 `_school_raw` 随投影一并缓存，供 merge 写回时补回未建模的字段。
    """
    global _schools_cache, _schools_raw, _schools_fingerprint
    if _schools_cache is not None and not force:
        # 磁盘被离线改过（回填脚本、手工编辑）就必须重读：否则接下来的整文件写回
        # 会拿旧快照把新改动静默覆盖掉（实测吞过一次 21 条简称修复）。
        if _schools_disk_fingerprint() == _schools_fingerprint:
            return _schools_cache
    try:
        data = _json.loads(_SCHOOLS_JSON.read_text(encoding="utf-8"))
    except Exception:
        _schools_cache, _schools_raw, _schools_fingerprint = [], {}, None
        return _schools_cache
    rows = []
    raw: dict = {}
    for d in data:
        if not isinstance(d, dict) or not str(d.get("name") or "").strip():
            continue
        row = {key: str(d.get(key) or "").strip() for key in _SCHOOL_KEYS}
        sid = row["id"]
        rows.append(row)
        if sid:
            raw[sid] = d
    _schools_cache = rows
    _schools_raw = raw
    _schools_fingerprint = _schools_disk_fingerprint()
    return _schools_cache


def _save_schools(items: list[dict]) -> None:
    """原子写回 schools.json 并刷新进程内缓存（改简称 / 新增学校接口用）。

    **merge 而非替换**：调用方传进来的多是 `_load_schools()` 的投影结果，只含已建模的
    键；直接落盘会把磁盘上的其它字段一起抹平（这正是 tier/nature/source 全库消失的原因）。
    因此按 id 取回原始行再叠加本次改动，未建模字段原样保留。
    """
    from mathbank.asset_security import write_private_text_atomic
    from mathbank.school_library import invalidate_school_index

    global _schools_cache, _schools_raw, _schools_fingerprint
    # 写之前先按磁盘现状重读（指纹变了才重读）：merge 的基线必须是**当前文件**，
    # 不能是进程内旧快照——否则离线回填的行会被这次整文件写回抹掉。
    _load_schools()
    raw = dict(_schools_raw)
    merged = []
    seen_ids = set()
    for row in items:
        sid = str(row.get("id") or "").strip()
        # raw 只收非空 id，这里必须用 get(sid) or {}：旧词库存在 id 为空的行（前端「手输地区」
        # 建档），直接 dict(None) 会把整次写回炸成 500。
        base = dict(raw.get(sid) or {}) if sid else {}
        entry = {**base, **{k: v for k, v in row.items() if k in _SCHOOL_KEYS}}
        if sid:
            seen_ids.add(sid)
        merged.append(entry)
    # 缓存里已被删掉、但磁盘上还在的行不得被顺手清空：本函数只做「按投影覆盖」，
    # 真正的删除是独立入口的职责。
    for sid, base in raw.items():
        if sid not in seen_ids:
            merged.append(dict(base))

    write_private_text_atomic(_SCHOOLS_JSON, _json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
    _schools_cache = None
    _schools_raw = {}
    _schools_fingerprint = None
    # 显示层按 id 现查简称的独立缓存也要同步失效，否则改完简称历史题还挂旧名
    invalidate_school_index()


# 卷种分组默认序：黑吉辽置顶 → 全国II卷 → 全国I卷 → 自主命题；其余按拼音外任意稳定序兜底。
_DEFAULT_PROVINCE_GROUPS = [
    ["黑龙江省", "吉林省", "辽宁省"],
    ["内蒙古自治区", "海南省", "重庆市", "贵州省", "广西壮族自治区", "甘肃省", "四川省",
     "云南省", "陕西省", "山西省", "青海省", "宁夏回族自治区", "新疆维吾尔自治区", "西藏自治区"],
    ["河北省", "江苏省", "浙江省", "安徽省", "福建省", "江西省", "山东省", "河南省",
     "湖北省", "湖南省", "广东省"],
    ["北京市", "天津市", "上海市"],
]


def _default_display_order() -> dict:
    provinces = [p for group in _DEFAULT_PROVINCE_GROUPS for p in group]
    return {"provinces": {"_": provinces}, "cities": {}, "schools": {}}


def _normalize_pinned_layer(layer, layer_name) -> dict | None:
    """置顶表：{父级键: [条目...]}；兼容旧的裸字符串列表（视为无父级）。非法返回 None。"""
    if layer is None:
        return {}
    if not isinstance(layer, dict):
        return None
    result = {}
    for key, arr in layer.items():
        if not isinstance(key, str) or not isinstance(arr, list):
            return None
        if not all(isinstance(x, str) and x.strip() for x in arr):
            return None
        cleaned = [x.strip() for x in arr]
        if cleaned:
            result[key.strip() or "_"] = cleaned
    return result


def _normalize_display_order(payload) -> dict | None:
    """校验并规范化前端提交的展示配置；非法返回 None。

    provinces / cities 是**置顶列表**（父级内生效，未列出的按引用次数→字母序排）；
    schools 保留旧三层载荷兼容，面板不再读写它。
    """
    if not isinstance(payload, dict):
        return None
    # 三层键必须齐备（值可以是 null）：缺键多半是半截载荷，静默当成「无置顶」
    # 会把用户排好的顺序整层抹掉。
    if not {"provinces", "cities", "schools"} <= set(payload):
        return None
    raw_provinces = payload.get("provinces")
    if raw_provinces is None:
        prov_pinned = {}
    elif isinstance(raw_provinces, list) and all(
            isinstance(p, str) and p.strip() for p in raw_provinces):
        prov_pinned = {"_": [p.strip() for p in raw_provinces]}
    elif isinstance(raw_provinces, dict):
        prov_pinned = _normalize_pinned_layer(raw_provinces, "provinces") or {}
        if prov_pinned == {} and raw_provinces:
            return None
    else:
        return None
    result = {
        "provinces": prov_pinned,
        "cities": _normalize_pinned_layer(payload.get("cities"), "cities"),
        "schools": _normalize_pinned_layer(payload.get("schools"), "schools"),
    }
    if result["cities"] is None or result["schools"] is None:
        return None
    return result


def _pinned_list(order: dict, layer: str, parent_key: str) -> list:
    """取某父级下的置顶列表（省层父级键为 "_"）。"""
    return (order.get(layer) or {}).get(parent_key or "_") or []


def _reference_counts() -> dict:
    """引用次数：questions.source_school_id + question_sources.school_id 相加。

    按 id 统计后映射到名称，供面板与下拉排序用；库不可用时返回全零而不是报错——
    排序是展示层，绝不能因为统计失败让表单打不开。
    """
    out = {"schools": {}, "provinces": {}, "cities": {}}
    try:
        from sqlalchemy import func
        from mathbank.database import QuestionSource, SessionLocal
    except Exception:
        return out
    db = None
    try:
        db = SessionLocal()
        by_id: dict = {}
        for sid, n in (db.query(Question.source_school_id, func.count(Question.id))
                       .filter(Question.source_school_id.isnot(None), Question.source_school_id != "")
                       .group_by(Question.source_school_id).all()):
            by_id[sid] = by_id.get(sid, 0) + int(n or 0)
        for sid, n in (db.query(QuestionSource.school_id, func.count(QuestionSource.id))
                       .filter(QuestionSource.school_id.isnot(None), QuestionSource.school_id != "")
                       .group_by(QuestionSource.school_id).all()):
            by_id[sid] = by_id.get(sid, 0) + int(n or 0)
    except Exception:
        return out
    finally:
        if db is not None:
            db.close()
    for it in _load_schools():
        c = by_id.get(it.get("id"), 0)
        name = it.get("name") or ""
        if name:
            out["schools"][name] = out["schools"].get(name, 0) + c
        prov, city = it.get("province") or "", it.get("city") or ""
        if prov:
            out["provinces"][prov] = out["provinces"].get(prov, 0) + c
        if prov and city:
            key = prov + "|" + city
            out["cities"][key] = out["cities"].get(key, 0) + c
    return out


def _apply_pin_order(names: list, pinned: list, counts: dict, parent_key=None) -> list:
    """置顶（按给定顺序）→ 引用次数降序 → 名称字母序。

    parent_key 非空时 schools 层还要把同城学校并入同一父级分组。
    """
    rank = {n: i for i, n in enumerate(pinned)}
    pool = [n for n in names if n in rank] + [n for n in names if n not in rank]
    if parent_key is None:
        return sorted(pool, key=lambda n: (
            rank.get(n, len(rank)), -(counts.get(n, 0) or 0), str(n)))
    groups: dict = {}
    for n in pool:
        groups.setdefault(parent_key(n), []).append(n)
    out = []
    for gkey in sorted(groups, key=lambda g: min(rank.get(n, len(rank)) for n in groups[g])):
        out.extend(sorted(groups[gkey], key=lambda n: (
            rank.get(n, len(rank)), -(counts.get(n, 0) or 0), str(n))))
    return out


def _load_display_order() -> dict:
    """读 display_order.json；缺失/损坏时回落内置默认序，绝不抛错。"""
    global _display_order_cache
    if _display_order_cache is not None:
        return _display_order_cache
    try:
        data = _json.loads(_DISPLAY_ORDER_JSON.read_text(encoding="utf-8"))
        normalized = _normalize_display_order(data)
        _display_order_cache = normalized or _default_display_order()
    except Exception:
        _display_order_cache = _default_display_order()
    return _display_order_cache


@router.get("/api/display-order")
def get_display_order():
    """返回省/市/校三层的置顶列表与引用次数（面板排序的唯一数据源）。"""
    order = _load_display_order()
    counts = _reference_counts()
    return {"status": "success", **order, "counts": counts}


@router.get("/api/display-config")
def get_display_config():
    """录入表单三个下拉用的合并视图：置顶 + 引用次数 + 字母序，一次算好。"""
    order = _load_display_order()
    counts = _reference_counts()
    items = _load_schools()

    prov_names = sorted({it["province"] for it in items if it.get("province")})
    provinces = _apply_pin_order(prov_names, _pinned_list(order, "provinces", "_"), {})

    cities_by_prov = {}
    for prov in provinces:
        names = sorted({it["city"] for it in items
                        if it.get("province") == prov and it.get("city")})
        cities_by_prov[prov] = _apply_pin_order(
            names, _pinned_list(order, "cities", prov), {})

    schools_by_key = {}
    for prov in provinces:
        for city in list(cities_by_prov[prov]) + [""]:
            key = prov + "|" + city
            rows = [it for it in items
                    if it.get("province") == prov and (not city or it.get("city") == city)]
            names = [it["name"] for it in rows if it.get("name")]
            by_name = {it["name"]: it for it in rows if it.get("name")}
            ordered = _apply_pin_order(
                sorted(set(names)), _pinned_list(order, "schools", key),
                counts["schools"], parent_key=lambda n: (by_name.get(n) or {}).get("city") or "")
            schools_by_key[key] = ordered

    return {"status": "success", "provinces": provinces,
            "cities": cities_by_prov, "schools": schools_by_key,
            "counts": counts["schools"]}


@router.post("/api/display-order")
def save_display_order(request_payload: dict):
    """保存三层展示顺序：原子写文件 + 立即刷新进程内缓存（无需重启）。"""
    normalized = _normalize_display_order(request_payload)
    if normalized is None:
        return JSONResponse(
            content={"status": "error", "message": "顺序数据格式不合法"}, status_code=400
        )
    from mathbank.asset_security import write_private_text_atomic

    try:
        write_private_text_atomic(
            _DISPLAY_ORDER_JSON, _json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
        )
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"保存排序失败: {e}"}, status_code=500
        )
    global _display_order_cache
    _display_order_cache = normalized
    return {"status": "success", **normalized}


@router.put("/api/school-short-name")
def update_school_short_name(payload: dict):
    """修改某所学校的简称（全称只读）。一律按稳定 id 定位——同名校（如「省实验」）
    按名称匹配会误伤；旧客户端只发 full_name/name 时兼容兜底，但多条候选即拒绝。"""
    sid = str(payload.get("id") or "").strip()
    short = str(payload.get("short_name") or "").strip()
    if not short or len(short) > 64:
        return JSONResponse(content={"status": "error", "message": "简称不能为空且不超过 64 字"}, status_code=400)
    items = _load_schools()
    target = None
    if sid:
        for it in items:
            if it.get("id") == sid:
                target = it
                break
        if target is None:
            return JSONResponse(content={"status": "error", "message": "未找到该学校"}, status_code=404)
    else:
        # 兼容旧载荷：full_name(+province/city) → name；命中多条直接拒绝，绝不猜
        full = str(payload.get("full_name") or "").strip()
        prov = str(payload.get("province") or "").strip()
        city = str(payload.get("city") or "").strip()
        legacy_name = str(payload.get("name") or "").strip()
        candidates = []
        for it in items:
            if prov and it["province"] != prov:
                continue
            if city and it["city"] != city:
                continue
            if full and (it["full_name"] == full or (not it["full_name"] and it["name"] == full)):
                candidates.append(it)
            elif not full and legacy_name and it["name"] == legacy_name:
                candidates.append(it)
        if len(candidates) > 1:
            return JSONResponse(
                content={"status": "error", "message": "定位到多所同名学校，请改用 id 定位"}, status_code=409)
        if not candidates:
            return JSONResponse(content={"status": "error", "message": "未找到该学校"}, status_code=404)
        target = candidates[0]

    # 简称是用户识别学校的唯一入口：撞车的两所摆在下拉里根本分不开。
    # 判据到省级——面板按省分组，跨省同名（「省实验」吉林/黑龙江各一所）本来就不相认。
    conflict = next((it for it in items
                     if it is not target and it["province"] == target["province"]
                     and it["short_name"] == short), None)
    if conflict is not None:
        return JSONResponse(
            content={"status": "error",
                     "message": f"「{conflict['full_name']}」已使用该简称，请换一个"}, status_code=409)
    target["short_name"] = short
    target["name"] = short
    _save_schools(items)
    return {"status": "success", "school": dict(target)}


# 档次与性质的合法取值：留空一律允许（tier 判断不了就空着是硬规矩）。
_SCHOOL_TIERS = ("国重", "省重", "市重", "普高")
_SCHOOL_NATURES = ("公办", "民办")


def _school_id_for(full_name: str, province: str, city: str) -> str:
    """确定性学校 ID：`sch_ + sha1(全称|省|市)[:10]`，与阶段 A 回填脚本同一口径。"""
    import hashlib
    key = f"{full_name}|{province}|{city}"
    return "sch_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


@router.post("/api/schools")
def create_school(payload: dict):
    """新增一所学校进内置词库（排序面板「+ 新增学校」用）。

    只追加，绝不覆盖既有行：写回走 `_save_schools` 的按 id merge，未建模字段原样保留。
    简称撞车与改名同口径（省级判据），全称查重到「同省同市」为止。
    """
    def clean(key: str, limit: int = 64) -> str:
        return str(payload.get(key) or "").strip()[:limit]

    full = clean("full_name", 100)
    short = clean("short_name", 64)
    province = clean("province", 40)
    city = clean("city", 40)
    tier = clean("tier", 8)
    nature = clean("nature", 8)

    missing = [label for value, label in ((province, "省份"), (city, "城市"),
                                          (full, "全称"), (short, "简称")) if not value]
    if missing:
        return JSONResponse(content={"ok": False,
                                     "error": "请填写" + "、".join(missing)}, status_code=400)
    if tier and tier not in _SCHOOL_TIERS:
        return JSONResponse(content={"ok": False, "error": "档次取值不合法"}, status_code=400)
    if nature and nature not in _SCHOOL_NATURES:
        return JSONResponse(content={"ok": False, "error": "性质取值不合法"}, status_code=400)

    items = _load_schools()
    same_city_full = next((it for it in items
                           if it["province"] == province and it["city"] == city
                           and it["full_name"] == full), None)
    if same_city_full is not None:
        return JSONResponse(content={"ok": False,
                                     "error": f"同省同市已收录「{full}」（{city}）"}, status_code=409)
    same_short = next((it for it in items
                       if it["province"] == province and it["short_name"] == short), None)
    if same_short is not None:
        return JSONResponse(content={"ok": False,
                                     "error": f"「{same_short['full_name']}」已使用简称「{short}」，请换一个"},
                            status_code=409)

    school_id = _school_id_for(full, province, city)
    if any(it["id"] == school_id for it in items):
        # 哈希撞车只可能来自历史冻结 ID（校区/迁市后全称变过），此时加后缀另开一行，
        # 绝不动已有 ID——被题目引用的 10 个 ID 一经分配即冻结。
        suffix = 1
        while any(it["id"] == f"{school_id}_{suffix}" for it in items):
            suffix += 1
        school_id = f"{school_id}{suffix}"

    row = {"id": school_id, "name": short, "full_name": full, "short_name": short,
           "province": province, "city": city, "tier": tier, "nature": nature,
           "source": "手动录入"}
    # 传「现有全部行 + 新行」：merge 基线取磁盘现状，其它行逐字保留。
    _save_schools([dict(it) for it in items] + [row])
    return {"ok": True, "id": school_id, "school": row}


@router.get("/api/school-suggestions")
def school_suggestions(prefix: str = "", limit: int = 20, province: str = "", city: str = ""):
    """学校联想：返回 [{name, full_name, short_name, province, city}]。
    - prefix 非空：对 简称/全称/name 做子串匹配
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
        items = [it for it in items
                 if p in it["name"] or p in it["short_name"] or p in it["full_name"]]
        cap = max(1, min(int(limit or 20), 50))
    else:
        cap = 500
    return {"suggestions": items[:cap]}


def _apply_province_order(provs: list[str]) -> list[str]:
    order = _load_display_order()
    counts = _reference_counts()["provinces"]
    return _apply_pin_order(provs, _pinned_list(order, "provinces", "_"), counts)


@router.get("/api/school-regions")
def school_regions():
    """返回 [{province, cities: [市...]}] 供前端省/市级联下拉。
    省/市一律「置顶（父级内）→ 引用次数降序 → 字母序」；
    并集覆盖词库与用户自定义里出现过的全部省市。"""
    items = _load_schools()
    order = _load_display_order()
    m: dict = {}
    for it in items:
        prov = it.get("province") or ""
        city = it.get("city") or ""
        if not prov:
            continue
        m.setdefault(prov, set())
        if city:
            m[prov].add(city)
    # 置顶列表里出现、但词库暂无学校的省/市也要露出（用户刚排好序还没录题）
    for prov, cities in order["cities"].items():
        m.setdefault(prov, set()).update(cities)
    counts = _reference_counts()["cities"]
    regions = []
    for prov in _apply_province_order(list(m.keys())):
        pinned = _pinned_list(order, "cities", prov)
        ccounts = {c: counts.get(prov + "|" + c, 0) for c in m[prov]}
        cities = _apply_pin_order(sorted(m[prov]), pinned, ccounts)
        regions.append({"province": prov, "cities": cities})
    return {"regions": regions}
