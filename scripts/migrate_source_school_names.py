#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""存量题库「来源学校名」规范化（一次性迁移，默认 dry-run）。

目标：把 ``questions.source`` 与 ``question_sources.region`` 里的学校写法统一到
``mathbank/resources/schools.json`` 当前的 ``name``。录题页的文件名预填用的就是
``best.name``（见 ``static/js/import.js`` 的 ``matchSchoolFromFilename``），所以
这里跟随同一口径，而不是另造一套「标准名」。

匹配规则复用录题页的别名归一化 + 省域消歧：

  * 全称 / 简称 / name 三路同时参与最长匹配；
  * ``师范大学``→``师大``、``附属中学``→``附中``、``大学``→``大`` 等归一；
  * 城市名首字缩写（哈尔滨师大附中 → 哈师大附中）；
  * 文件名/来源里出现省、市时强制同省（同名「省实验」不跨省猜）；
  * 仍不唯一则判为 ambiguous，**不写库**。

在录题页规则之外，历史数据还需要一层「历史别名」扩展（只用于比对，不改变目标名）：

  * ``师大附中`` ↔ ``师附``（东北师附 → 东北师大附中）；
  * ``第一中学`` → ``一中``（双鸭山一中 → 双山一中）；
  * 去掉尾部 ``学校``（长春外国语 → 长春外国语学校）。

**默认 dry-run**：以 ``mode=ro`` 只读打开数据库，输出逐题报告，不写库。
``--apply`` 必须携带 dry-run 生成的 plan JSON；脚本先复核题库与词库未漂移、
再创建完整备份，然后单事务改写并重算受影响题目的 ``question_fingerprints``，
最后刷新 JSON / AI 题库导出。

``source`` 本身不在查重指纹的输入里；重算指纹是为了满足「来源改动后仍按当前
内容重建派生索引」的一致性要求，正常应得到与原来相同的哈希。

用法::

    python -m scripts.migrate_source_school_names
    python -m scripts.migrate_source_school_names --apply --plan outputs/source_school_cleanup_xxx.json
    python -m scripts.migrate_source_school_names --apply --plan ... --fill-school-ids
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

from mathbank.paths import DATABASE_FILE, PROJECT_ROOT

SCHOOLS_FILE = PROJECT_ROOT / "mathbank" / "resources" / "schools.json"
DISPLAY_ORDER_FILE = PROJECT_ROOT / "mathbank" / "resources" / "display_order.json"
REPORT_DIR = PROJECT_ROOT / "outputs"

# 与 static/js/import.js 的 SCHOOL_ADMIN_SUFFIX_RE 同口径。
SCHOOL_ADMIN_SUFFIX_RE = re.compile(r"(?:省|市|自治区|特别行政区|地区|盟)$")
_ORDINAL_MIDDLE_RE = re.compile(r"第([一二三四五六七八九十百零〇两\d]+)(?:高级)?中学(?:校)?")
_JUNK_TEXT_RE = re.compile(r"^[\s\d.、,，;；:：()（）\-—_/]+$")
_SCHOOL_SUFFIX_AFTER_TOKEN_RE = re.compile(r"^(?:中学|学校|高中|初中)")


# ---------------------------------------------------------------------------
# 学校匹配：import.js 的 Python 镜像 + 历史别名扩展
# ---------------------------------------------------------------------------


def normalize_school_alias(value: str) -> str:
    """与 import.js 的 normalizeSchoolAlias 逐条同序。"""
    text = re.sub(r"\s+", "", str(value or ""))
    replacements = (
        ("师范大学", "师大"),
        ("师范学院", "师院"),
        ("附属高级中学", "附中"),
        ("附属中学", "附中"),
        ("大学", "大"),
        ("学院", "院"),
        ("附属", "附"),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def _legacy_alias_keys(normalized: str) -> set[str]:
    """历史卷面里真实出现、但当前词库 name/short/full 都覆盖不到的写法。"""
    keys: set[str] = set()
    if normalized.endswith("学校") and len(normalized) > 3:
        keys.add(normalized[:-2])
    if "师大附中" in normalized:
        keys.add(normalized.replace("师大附中", "师附"))
    for match in _ORDINAL_MIDDLE_RE.finditer(normalized):
        keys.add(normalized[: match.start()] + match.group(1) + "中" + normalized[match.end():])
    return keys


def _raw_school_names(school: dict) -> list[str]:
    names = [
        str(school.get("name") or "").strip(),
        str(school.get("short_name") or "").strip(),
        str(school.get("full_name") or "").strip(),
    ]
    aliases = school.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    if isinstance(aliases, (list, tuple)):
        names.extend(str(alias or "").strip() for alias in aliases)
    return [name for name in names if name]


def school_match_keys(school: dict) -> list[str]:
    """返回该校可参与「文本包含」匹配的全部候选写法（长名在前）。"""
    raw_names = _raw_school_names(school)
    keys: set[str] = set(raw_names)
    city = SCHOOL_ADMIN_SUFFIX_RE.sub("", str(school.get("city") or ""))
    province = SCHOOL_ADMIN_SUFFIX_RE.sub("", str(school.get("province") or ""))
    for name in raw_names:
        normalized = normalize_school_alias(name)
        if normalized:
            keys.add(normalized)
        # 全称 → 城市缩写：哈尔滨师范大学附属中学 → 哈尔滨师大附中 → 哈师大附中
        if len(city) >= 2 and city in normalized:
            keys.add(normalized.replace(city, city[0]))
        if len(province) >= 2 and province in normalized:
            keys.add(normalized.replace(province, province[0]))
        no_admin = re.sub(r"省|市|自治区", "", normalized)
        if len(no_admin) >= 3:
            keys.add(no_admin)
        for base in (normalized, no_admin):
            keys.update(_legacy_alias_keys(base))
    return sorted((key for key in keys if len(key) >= 2), key=len, reverse=True)


def canonical_school_name(school: dict) -> str:
    return (
        str(school.get("name") or "").strip()
        or str(school.get("short_name") or "").strip()
        or str(school.get("full_name") or "").strip()
    )


def text_mentions_place(text: str, full: str, short: str = "") -> bool:
    """与 import.js 的 textMentionsPlace 同口径：短名紧跟「省/自治区」时只算省。"""
    if not full:
        return False
    if full in text:
        return True
    if not short or len(short) < 2 or short not in text:
        return False
    index = text.find(short)
    while index >= 0:
        tail = text[index + len(short): index + len(short) + 3]
        if not re.match(r"^(?:省|自治区)", tail):
            return True
        index = text.find(short, index + 1)
    return False


def build_regions(schools: list[dict], display_order: dict | None = None) -> list[dict]:
    """把词库 + 排序配置收敛成 [{province, cities: [...]}]，供省/市上下文检测。"""
    mapping: dict[str, set[str]] = {}
    for school in schools:
        province = str(school.get("province") or "").strip()
        city = str(school.get("city") or "").strip()
        if not province:
            continue
        mapping.setdefault(province, set())
        if city:
            mapping[province].add(city)
    order_cities = (display_order or {}).get("cities") or {}
    if isinstance(order_cities, dict):
        for province, cities in order_cities.items():
            if not province or not isinstance(cities, (list, tuple)):
                continue
            mapping.setdefault(str(province), set()).update(str(c) for c in cities if c)
    return [
        {"province": province, "cities": sorted(cities)}
        for province, cities in mapping.items()
    ]


def detect_source_geo_context(text: str, schools: list[dict], regions: list[dict]) -> dict:
    """与 import.js 的 detectSourceGeoContext 同口径。"""
    text = str(text or "")
    provinces: set[str] = set()
    cities: set[str] = set()
    for school in schools:
        if school.get("province"):
            provinces.add(str(school["province"]).strip())
        if school.get("city"):
            cities.add(str(school["city"]).strip())
    for region in regions:
        if region.get("province"):
            provinces.add(str(region["province"]).strip())
        for city in region.get("cities") or []:
            cities.add(str(city).strip())

    def pick(values: set[str]) -> str:
        hits = [
            value
            for value in values
            if value
            and text_mentions_place(
                text, value, SCHOOL_ADMIN_SUFFIX_RE.sub("", value)
            )
        ]
        hits.sort(key=len, reverse=True)
        return hits[0] if hits else ""

    province = pick(provinces)
    city = pick(cities)
    if not province and city:
        short_city = SCHOOL_ADMIN_SUFFIX_RE.sub("", city)
        node = next(
            (
                region
                for region in regions
                if city in (region.get("cities") or [])
                or short_city
                in [SCHOOL_ADMIN_SUFFIX_RE.sub("", str(c)) for c in (region.get("cities") or [])]
            ),
            None,
        )
        if node and node.get("province"):
            province = str(node["province"])
        else:
            school = next(
                (
                    item
                    for item in schools
                    if str(item.get("city") or "").strip() == city and item.get("province")
                ),
                None,
            )
            if school:
                province = str(school["province"])
    return {"province": province, "city": city}


def _apply_geo(items: list[dict], geo: dict) -> list[dict]:
    if geo.get("province"):
        return [
            item
            for item in items
            if str(item["school"].get("province") or "").strip() == geo["province"]
        ]
    if geo.get("city"):
        same_city = [
            item
            for item in items
            if str(item["school"].get("city") or "").strip() == geo["city"]
        ]
        if same_city:
            return same_city
    return items


def _public_school(school: dict) -> dict:
    return {
        "id": str(school.get("id") or ""),
        "name": canonical_school_name(school),
        "province": str(school.get("province") or ""),
        "city": str(school.get("city") or ""),
    }


def analyze_text(text: str, schools: list[dict], regions: list[dict]) -> dict:
    """从任意文本中找唯一学校；返回 school/token/reason/candidates。"""
    geo = detect_source_geo_context(text, schools, regions)
    scored: list[dict] = []
    for school in schools:
        token = max(
            (key for key in school_match_keys(school) if key in text),
            key=len,
            default="",
        )
        if token:
            scored.append({"school": school, "token": token, "length": len(token)})

    pool = _apply_geo(scored, geo)
    if not pool:
        # 「师大附中」这种只写共同后缀的写法：只有省/市上下文能筛到唯一候选才采用。
        fuzzy = [
            {"school": school, "token": "师大附中", "length": 4}
            for school in schools
            if "师大附中" in text
            and any("师大附中" in key for key in school_match_keys(school))
        ]
        pool = _apply_geo(fuzzy, geo)
        if len(pool) == 1:
            chosen = pool[0]
            return {
                "school": chosen["school"],
                "token": chosen["token"],
                "reason": "matched",
                "geo": geo,
                "candidates": [_public_school(chosen["school"])],
            }
        if pool:
            return {
                "school": None,
                "token": "",
                "reason": "ambiguous",
                "geo": geo,
                "candidates": [_public_school(item["school"]) for item in pool],
            }
        return {
            "school": None,
            "token": "",
            "reason": "no_school",
            "geo": geo,
            "candidates": [],
        }

    longest = max(item["length"] for item in pool)
    top = [item for item in pool if item["length"] == longest]
    unique: list[dict] = []
    for item in top:
        if not any(existing["school"] is item["school"] for existing in unique):
            unique.append(item)
    if len(unique) == 1:
        chosen = unique[0]
        return {
            "school": chosen["school"],
            "token": chosen["token"],
            "reason": "matched",
            "geo": geo,
            "candidates": [_public_school(chosen["school"])],
        }
    return {
        "school": None,
        "token": "",
        "reason": "ambiguous",
        "geo": geo,
        "candidates": [_public_school(item["school"]) for item in unique],
    }


def analyze_known_school(
    text: str, school: dict, schools: list[dict], regions: list[dict]
) -> dict:
    """已有 source_school_id 时以该 ID 为准，只负责在文本里定位并校验省域。"""
    token = max(
        (key for key in school_match_keys(school) if key in text),
        key=len,
        default="",
    )
    if not token:
        fallback = analyze_text(text, schools, regions)
        return {
            "school": None,
            "token": "",
            "reason": "linked_token_missing",
            "geo": fallback.get("geo") or {},
            "candidates": fallback.get("candidates") or [],
        }
    geo = detect_source_geo_context(text, schools, regions)
    school_province = str(school.get("province") or "").strip()
    school_city = str(school.get("city") or "").strip()
    if geo.get("province") and school_province and geo["province"] != school_province:
        return {
            "school": None,
            "token": token,
            "reason": "geo_conflict",
            "geo": geo,
            "candidates": [_public_school(school)],
        }
    if geo.get("city") and school_city and geo["city"] != school_city:
        return {
            "school": None,
            "token": token,
            "reason": "geo_conflict",
            "geo": geo,
            "candidates": [_public_school(school)],
        }
    return {
        "school": school,
        "token": token,
        "reason": "matched",
        "geo": geo,
        "candidates": [_public_school(school)],
    }


def replace_school_token(text: str, token: str, canonical: str) -> tuple[str | None, str]:
    """把文本中的校名 token 替换成词库当前 name；不安全时返回 (None, reason)。"""
    if not token or not canonical or token not in text:
        return text, ""
    if token == canonical:
        return text, ""
    for match in re.finditer(re.escape(token), text):
        tail = text[match.end(): match.end() + 2]
        if _SCHOOL_SUFFIX_AFTER_TOKEN_RE.match(tail):
            # 例：token 恰是更长校名的前缀（德强 + 高中），直接替换会拼出重复后缀。
            return None, "suffix_conflict"
    return text.replace(token, canonical), ""


def _looks_like_junk(text: str) -> bool:
    return not text.strip() or bool(_JUNK_TEXT_RE.match(text.strip()))


# ---------------------------------------------------------------------------
# 只读扫描与计划生成
# ---------------------------------------------------------------------------


def load_schools(path: Path = SCHOOLS_FILE) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("schools.json 顶层必须是数组")
    return [entry for entry in data if isinstance(entry, dict) and entry.get("name")]


def load_display_order(path: Path = DISPLAY_ORDER_FILE) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _open_readonly(database_file: Path) -> sqlite3.Connection:
    uri = "file:" + Path(database_file).resolve().as_posix() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def build_plan(
    database_file: Path = DATABASE_FILE,
    *,
    schools_file: Path = SCHOOLS_FILE,
    display_order_file: Path = DISPLAY_ORDER_FILE,
) -> dict:
    """只读生成清洗计划；不写数据库、不写文件。"""
    schools = load_schools(schools_file)
    regions = build_regions(schools, load_display_order(display_order_file))
    schools_by_id = {
        str(school.get("id") or "").strip(): school
        for school in schools
        if str(school.get("id") or "").strip()
    }

    con = _open_readonly(database_file)
    try:
        question_rows = con.execute(
            "SELECT id, source, source_school_id FROM questions ORDER BY id"
        ).fetchall()
        source_rows = con.execute(
            """
            SELECT qs.id, qs.question_id, qs.region, qs.school_id,
                   qs.province, qs.city, qs.paper_title,
                   q.id IS NOT NULL AS question_exists
            FROM question_sources qs
            LEFT JOIN questions q ON q.id = qs.question_id
            ORDER BY qs.id
            """
        ).fetchall()
    finally:
        con.close()

    changes: list[dict] = []
    id_backfills: list[dict] = []
    manual: list[dict] = []
    unchanged = 0
    province_conflicts: list[dict] = []

    for qid, source, source_school_id in question_rows:
        qid = int(qid)
        before = source or ""
        sid = str(source_school_id or "").strip()
        linked = schools_by_id.get(sid) if sid else None

        if linked is not None:
            analysis = analyze_known_school(before, linked, schools, regions)
        else:
            analysis = analyze_text(before, schools, regions)
            if sid and analysis.get("reason") == "matched":
                analysis = {
                    **analysis,
                    "reason": "orphan_school_id",
                    "detail": f"source_school_id={sid} 不在词库中",
                }

        if analysis.get("reason") == "matched":
            school = analysis["school"]
            canonical = canonical_school_name(school)
            token = analysis.get("token") or ""
            after, replace_reason = replace_school_token(before, token, canonical)
            if after is None:
                manual.append(
                    {
                        "qid": qid,
                        "source": before,
                        "reason": replace_reason,
                        "detail": f"匹配到「{token}」→「{canonical}」，但替换边界不安全",
                        "candidates": analysis.get("candidates") or [],
                        "source_school_id_before": sid,
                    }
                )
                continue
            if after != before:
                changes.append(
                    {
                        "qid": qid,
                        "before": before,
                        "after": after,
                        "token": token,
                        "school_id": str(school.get("id") or ""),
                        "school_name": canonical,
                        "province": str(school.get("province") or ""),
                        "city": str(school.get("city") or ""),
                        "source_school_id_before": sid,
                    }
                )
            else:
                unchanged += 1
            if not sid and str(school.get("id") or "").strip():
                id_backfills.append(
                    {
                        "qid": qid,
                        "source": before,
                        "token": token,
                        "school_id": str(school.get("id") or "").strip(),
                        "school_name": canonical,
                    }
                )
            continue

        reason = analysis.get("reason") or "no_school"
        if reason == "no_school" and _looks_like_junk(before):
            reason = "junk"
        manual.append(
            {
                "qid": qid,
                "source": before,
                "reason": reason,
                "detail": analysis.get("detail") or "",
                "candidates": analysis.get("candidates") or [],
                "source_school_id_before": sid,
            }
        )

    qs_changes: list[dict] = []
    qs_manual: list[dict] = []
    qs_geo_suggestions: list[dict] = []
    for (
        row_id,
        question_id,
        region,
        school_id,
        province,
        city,
        paper_title,
        question_exists,
    ) in source_rows:
        row_id = int(row_id)
        question_id = int(question_id)
        region = region or ""
        sid = str(school_id or "").strip()
        linked = schools_by_id.get(sid) if sid else None
        if not region.strip():
            if linked is not None or not question_exists:
                qs_manual.append(
                    {
                        "row_id": row_id,
                        "question_id": question_id,
                        "region": region,
                        "paper_title": paper_title or "",
                        "reason": "orphan_row" if not question_exists else "empty_region",
                        "detail": (
                            "question_id 在 questions 表中不存在（孤儿来源行）"
                            if not question_exists
                            else "region 为空"
                        ),
                    }
                )
            continue

        analysis = (
            analyze_known_school(region, linked, schools, regions)
            if linked is not None
            else analyze_text(region, schools, regions)
        )
        if analysis.get("reason") == "matched":
            school = analysis["school"]
            canonical = canonical_school_name(school)
            token = analysis.get("token") or ""
            after, replace_reason = replace_school_token(region, token, canonical)
            if after is None:
                qs_manual.append(
                    {
                        "row_id": row_id,
                        "question_id": question_id,
                        "region": region,
                        "paper_title": paper_title or "",
                        "reason": replace_reason,
                        "detail": f"匹配到「{token}」→「{canonical}」，但替换边界不安全",
                    }
                )
                continue
            target_sid = "" if sid else str(school.get("id") or "").strip()
            if after != region or target_sid:
                # 同一行只允许一条计划项：region 改写与 school_id 回填必须一起提交，
                # 拆成两条会让第二条在 region 已是目标值时被误判为「无需处理」。
                qs_changes.append(
                    {
                        "row_id": row_id,
                        "question_id": question_id,
                        "before": region,
                        "after": after,
                        "token": token,
                        "school_id": str(school.get("id") or ""),
                        "school_name": canonical,
                        "set_school_id": bool(target_sid),
                    }
                )
            school_province = str(school.get("province") or "").strip()
            school_city = str(school.get("city") or "").strip()
            if school_province and not str(province or "").strip():
                qs_geo_suggestions.append(
                    {
                        "row_id": row_id,
                        "question_id": question_id,
                        "field": "province",
                        "suggested": school_province,
                    }
                )
            if school_city and not str(city or "").strip():
                qs_geo_suggestions.append(
                    {
                        "row_id": row_id,
                        "question_id": question_id,
                        "field": "city",
                        "suggested": school_city,
                    }
                )
            if province and school_province and province != school_province:
                province_conflicts.append(
                    {
                        "row_id": row_id,
                        "question_id": question_id,
                        "field": "province",
                        "stored": province,
                        "school": school_province,
                    }
                )
            if city and school_city and city != school_city:
                province_conflicts.append(
                    {
                        "row_id": row_id,
                        "question_id": question_id,
                        "field": "city",
                        "stored": city,
                        "school": school_city,
                    }
                )
            continue

        reason = analysis.get("reason") or "no_school"
        if reason == "no_school" and _looks_like_junk(region):
            reason = "junk"
        elif reason == "no_school" and re.fullmatch(r".*(?:省|市|自治区|盟|地区)$", region.strip()):
            reason = "administrative_region"
        qs_manual.append(
            {
                "row_id": row_id,
                "question_id": question_id,
                "region": region,
                "paper_title": paper_title or "",
                "reason": reason,
                "detail": analysis.get("detail") or "",
                "candidates": analysis.get("candidates") or [],
            }
        )

    # 全量文本覆盖表：每种 source 文本（按是否已绑定 school_id 分开）对应什么动作。
    pair_counts: dict[tuple[str, str], int] = {}
    for _qid, source, source_school_id in question_rows:
        pair = (source or "", str(source_school_id or "").strip())
        pair_counts[pair] = pair_counts.get(pair, 0) + 1

    breakdown: list[dict] = []
    for (text, sid), count in sorted(
        pair_counts.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1])
    ):
        linked = schools_by_id.get(sid) if sid else None
        analysis = (
            analyze_known_school(text, linked, schools, regions)
            if linked is not None
            else analyze_text(text, schools, regions)
        )
        if analysis.get("reason") == "matched":
            school = analysis["school"]
            canonical = canonical_school_name(school)
            after, _replace_reason = replace_school_token(
                text, analysis.get("token") or "", canonical
            )
            if after is None:
                action, after = "manual", text
            else:
                action = "rewrite" if after != text else "keep"
            breakdown.append(
                {
                    "source": text,
                    "count": count,
                    "action": action,
                    "after": after,
                    "school_name": canonical,
                    "school_id": str(school.get("id") or ""),
                    "source_school_id": sid,
                }
            )
        else:
            reason = analysis.get("reason") or "no_school"
            if reason == "no_school" and _looks_like_junk(text):
                reason = "junk"
            breakdown.append(
                {
                    "source": text,
                    "count": count,
                    "action": reason,
                    "after": text,
                    "school_name": "",
                    "school_id": "",
                    "source_school_id": sid,
                }
            )

    summary = {
        "total_questions": len(question_rows),
        "distinct_sources": len({(row[1] or "") for row in question_rows}),
        "changed_questions": len(changes),
        "id_backfills": len(id_backfills),
        "manual_questions": len(manual),
        "unchanged_questions": unchanged,
        "question_sources_rows": len(source_rows),
        "question_sources_changed": len(qs_changes),
        "question_sources_manual": len(qs_manual),
        "province_conflicts": len(province_conflicts),
    }
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "database": str(Path(database_file).resolve()),
        "schools_file": str(Path(schools_file).resolve()),
        "schools_sha256": hashlib.sha256(Path(schools_file).read_bytes()).hexdigest(),
        "display_order_sha256": hashlib.sha256(
            Path(display_order_file).read_bytes()
        ).hexdigest()
        if Path(display_order_file).is_file()
        else "",
        "canonical_rule": "schools.json 的 name（与录题页文件名预填一致）",
        "summary": summary,
        "changes": changes,
        "id_backfills": id_backfills,
        "manual": manual,
        "source_breakdown": breakdown,
        "question_sources": {
            "changes": qs_changes,
            "manual": qs_manual,
            "geo_suggestions": qs_geo_suggestions,
            "province_conflicts": province_conflicts,
        },
    }


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def _short_ids(ids: list[int], limit: int = 16) -> str:
    shown = ", ".join(str(qid) for qid in ids[:limit])
    return shown + (" …" if len(ids) > limit else "")


def group_changes(changes: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str, str], list[int]] = {}
    for item in changes:
        # 分组键只关心「旧校名 → 新校名」，与年份/年级无关。
        key = (item["token"], item["school_name"], item["school_id"])
        groups.setdefault(key, []).append(item["qid"])
    return [
        {"token": token, "school_name": name, "school_id": sid, "ids": sorted(ids)}
        for (token, name, sid), ids in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0][0]))
    ]


def render_markdown(plan: dict) -> str:
    summary = plan["summary"]
    lines = [
        "# 存量题库来源学校名清洗 — dry-run 报告",
        "",
        f"- 生成时间：{plan['generated_at']}",
        f"- 数据库：`{plan['database']}`",
        f"- 词库：`{plan['schools_file']}`",
        f"- 目标口径：{plan['canonical_rule']}",
        f"- 题库总题数：{summary['total_questions']}；不同 source 文本：{summary['distinct_sources']}",
        "",
        "## 一、汇总",
        "",
        "| 项目 | 数量 |",
        "| --- | ---: |",
        f"| `source` 将改写 | {summary['changed_questions']} |",
        f"| 文本不变、建议回填 `source_school_id`（含在未改写内） | {summary['id_backfills']} |",
        f"| 无法匹配 / 歧义 / 需人工 | {summary['manual_questions']} |",
        f"| 无需改写 | {summary['unchanged_questions']} |",
        f"| `question_sources.region` 将改写 | {summary['question_sources_changed']} |",
        f"| `question_sources` 需人工 | {summary['question_sources_manual']} |",
        f"| 省域冲突 | {summary['province_conflicts']} |",
        "",
    ]

    groups = group_changes(plan["changes"])
    lines.append(f"## 二、`questions.source` 改写清单（{len(plan['changes'])} 题 / {len(groups)} 组）")
    lines.append("")
    if not groups:
        lines.append("无需改写。")
    for group in groups:
        lines.append(
            f"### `{group['token']}` → `{group['school_name']}`"
            f"（{len(group['ids'])} 题，{group['school_id'] or '无 ID'}）"
        )
        lines.append("")
        lines.append("题目 ID：" + "、".join(f"#{qid}" for qid in group["ids"]))
        lines.append("")
        sample = next(
            (item for item in plan["changes"] if item["school_id"] == group["school_id"]
             and item["token"] == group["token"]),
            None,
        )
        if sample:
            lines.append(f"- 改前：`{sample['before']}`")
            lines.append(f"- 改后：`{sample['after']}`")
            lines.append("")

    lines.append("### 逐题明细")
    lines.append("")
    lines.append("| 题目 ID | 改前 | 改后 | 旧 school_id |")
    lines.append("| ---: | --- | --- | --- |")
    for item in plan["changes"]:
        lines.append(
            f"| #{item['qid']} | {item['before']} | {item['after']} | "
            f"{item['source_school_id_before'] or '（空）'} |"
        )
    lines.append("")

    lines.append(f"## 三、建议回填 `source_school_id`（{len(plan['id_backfills'])} 题，默认不改）")
    lines.append("")
    if not plan["id_backfills"]:
        lines.append("无。")
    else:
        lines.append("| 题目 ID | source | 建议 school_id | 学校 |")
        lines.append("| ---: | --- | --- | --- |")
        for item in plan["id_backfills"]:
            lines.append(
                f"| #{item['qid']} | {item['source']} | {item['school_id']} | "
                f"{item['school_name']} |"
            )
    lines.append("")

    lines.append(f"## 四、无法匹配 / 歧义 / 需人工（{len(plan['manual'])} 题，脚本不改）")
    lines.append("")
    if not plan["manual"]:
        lines.append("无。")
    else:
        reason_labels = {
            "junk": "垃圾值（纯编号/符号）",
            "no_school": "文本中找不到词库学校",
            "ambiguous": "多个同长候选，省域无法消歧",
            "linked_token_missing": "已有 school_id，但文本里找不到该校任何写法",
            "geo_conflict": "文本省市与已绑定学校冲突",
            "orphan_school_id": "已有 school_id 已不在词库",
            "suffix_conflict": "校名后紧跟「中学/学校/高中/初中」，替换会拼出重复后缀",
        }
        for item in plan["manual"]:
            label = reason_labels.get(item["reason"], item["reason"])
            candidates = "；候选：" + "、".join(
                f"{c['name']}({c['id']}, {c['province']})" for c in item.get("candidates") or []
            ) if item.get("candidates") else ""
            detail = f"；{item['detail']}" if item.get("detail") else ""
            lines.append(
                f"- **#{item['qid']}** `{item['source']}` — {label}{detail}{candidates}"
            )
    lines.append("")

    qs = plan["question_sources"]
    lines.append(f"## 五、`question_sources.region`（{summary['question_sources_changed']} 条改写）")
    lines.append("")
    if not qs["changes"]:
        lines.append("无需改写。")
    else:
        lines.append("| row_id | question_id | 改前 region | 改后 region | 学校 |")
        lines.append("| ---: | ---: | --- | --- | --- |")
        for item in qs["changes"]:
            lines.append(
                f"| {item['row_id']} | #{item['question_id']} | {item['before']} | "
                f"{item['after']} | {item['school_name']}({item['school_id']}) |"
            )
    lines.append("")
    if qs["manual"]:
        lines.append("### `question_sources` 需人工")
        lines.append("")
        for item in qs["manual"]:
            lines.append(
                f"- row {item['row_id']} / question #{item['question_id']}："
                f"region=`{item['region']}`，reason={item['reason']}，"
                f"paper_title=`{item['paper_title']}`；{item.get('detail') or ''}"
            )
        lines.append("")
    if qs["geo_suggestions"]:
        lines.append("### 建议补全省/市（默认不改）")
        lines.append("")
        for item in qs["geo_suggestions"]:
            lines.append(
                f"- row {item['row_id']} / question #{item['question_id']}："
                f"{item['field']} → {item['suggested']}"
            )
        lines.append("")
    if qs["province_conflicts"]:
        lines.append("### 省域冲突（需人工，默认不改）")
        lines.append("")
        for item in qs["province_conflicts"]:
            lines.append(
                f"- row {item['row_id']} / question #{item['question_id']}："
                f"{item['field']} 存 `{item['stored']}`，学校属 `{item['school']}`"
            )
        lines.append("")

    lines.append(
        f"## 六、全量 `source` 文本覆盖（{len(plan.get('source_breakdown') or [])} 种，逐种核对）"
    )
    lines.append("")
    breakdown = plan.get("source_breakdown") or []
    if not breakdown:
        lines.append("无。")
    else:
        action_labels = {
            "rewrite": "改写",
            "keep": "保留",
            "junk": "垃圾值",
            "no_school": "找不到学校",
            "ambiguous": "歧义",
            "linked_token_missing": "school_id 与文本不符",
            "geo_conflict": "省域冲突",
            "orphan_school_id": "school_id 不在词库",
            "manual": "需人工（替换边界）",
        }
        lines.append("| 题数 | source 文本 | 动作 | 改后 / 命中学校 |")
        lines.append("| ---: | --- | --- | --- |")
        for item in breakdown:
            action = action_labels.get(item["action"], item["action"])
            if item["action"] == "rewrite":
                target = item["after"]
            elif item["action"] == "keep":
                target = "已是「%s」" % item["school_name"]
            else:
                target = ""
            lines.append(
                f"| {item['count']} | {item['source'] or '（空）'} | {action} | {target} |"
            )
    lines.append("")

    lines.extend(
        [
            "## 七、执行说明与待确认项",
            "",
            "1. 目标写法取 `schools.json` 的 `name`，与录题页文件名预填 `best.name` 一致。",
            "2. `双山一中` 是词库 `sch_a6d4cafd78` 的既定简称；若希望保留 `双鸭山一中`，"
            "应先改词库再重跑 dry-run（plan 会因词库哈希变化而失效）。",
            "3. `source_school_id` 回填默认不执行；确认后加 `--fill-school-ids`。",
            "4. 垃圾值（如 `1`）与词库未覆盖地区不会被改写。",
            "5. `question_sources.paper_title` 不在本次改写范围，只改 `region`。",
            "6. 正式执行：`python -m scripts.migrate_source_school_names --apply --plan <本报告同名 .json>`"
            "（服务必须已关闭）。",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(plan: dict) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    json_path = REPORT_DIR / f"source_school_cleanup_{stamp}.json"
    md_path = REPORT_DIR / f"source_school_cleanup_{stamp}.md"
    json_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(render_markdown(plan), encoding="utf-8")
    return json_path, md_path


def print_console_report(plan: dict, json_path: Path | None, md_path: Path | None) -> None:
    summary = plan["summary"]
    print("=" * 72)
    print("来源学校名清洗 — DRY-RUN（只读，不写库）")
    print("  database = %s" % plan["database"])
    print("  schools  = %s" % plan["schools_file"])
    print("=" * 72)
    print(
        "题库 %d 题；source 将改写 %d 题；文本不变但建议回填 school_id %d 题；"
        "需人工 %d 题；无需改写 %d 题"
        % (
            summary["total_questions"],
            summary["changed_questions"],
            summary["id_backfills"],
            summary["manual_questions"],
            summary["unchanged_questions"],
        )
    )
    print(
        "question_sources：改写 %d 条；需人工 %d 条；省域冲突 %d 条"
        % (
            summary["question_sources_changed"],
            summary["question_sources_manual"],
            summary["province_conflicts"],
        )
    )

    groups = group_changes(plan["changes"])
    print("\n== source 改写分组（%d 组） ==" % len(groups))
    for group in groups:
        print(
            "  · %s → %s：%d 题  ids=[%s]"
            % (
                group["token"],
                group["school_name"],
                len(group["ids"]),
                _short_ids(group["ids"]),
            )
        )

    if plan["id_backfills"]:
        print("\n== 建议回填 source_school_id（默认不改） ==")
        by_school: dict[tuple[str, str], list[int]] = {}
        for item in plan["id_backfills"]:
            by_school.setdefault((item["school_id"], item["school_name"]), []).append(item["qid"])
        for (sid, name), ids in sorted(by_school.items(), key=lambda kv: -len(kv[1])):
            print("  · %s(%s)：%d 题  ids=[%s]" % (name, sid, len(ids), _short_ids(ids)))

    print("\n== 需人工（脚本不改） ==")
    if not plan["manual"]:
        print("  无。")
    for item in plan["manual"]:
        print(
            "  - #%s 「%s」reason=%s %s"
            % (item["qid"], item["source"], item["reason"], item.get("detail") or "")
        )

    qs = plan["question_sources"]
    if qs["changes"] or qs["manual"] or qs["geo_suggestions"] or qs["province_conflicts"]:
        print("\n== question_sources ==")
        for item in qs["changes"]:
            print(
                "  · row %s / #%s：region「%s」→「%s」"
                % (item["row_id"], item["question_id"], item["before"], item["after"])
            )
        for item in qs["manual"]:
            print(
                "  - row %s / #%s：region=「%s」reason=%s %s"
                % (item["row_id"], item["question_id"], item["region"], item["reason"],
                   item.get("detail") or "")
            )
        for item in qs["geo_suggestions"]:
            print(
                "  · 建议 row %s：%s → %s（默认不改）"
                % (item["row_id"], item["field"], item["suggested"])
            )
        for item in qs["province_conflicts"]:
            print(
                "  - 省域冲突 row %s：%s 存 %s，学校属 %s"
                % (item["row_id"], item["field"], item["stored"], item["school"])
            )

    if json_path and md_path:
        print("\n报告：")
        print("  JSON: %s" % json_path)
        print("  MD  : %s" % md_path)
    print(
        "\nDRY-RUN 到此为止：未备份、未改库、未刷新导出。"
        "确认后执行 --apply --plan <上面的 JSON>。"
    )


# ---------------------------------------------------------------------------
# 正式执行
# ---------------------------------------------------------------------------


def _load_plan(path: Path) -> dict:
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or "changes" not in plan:
        raise ValueError("plan 文件格式不正确")
    return plan


def _verify_plan_fingerprint(plan: dict) -> None:
    current = hashlib.sha256(SCHOOLS_FILE.read_bytes()).hexdigest()
    if plan.get("schools_sha256") != current:
        raise RuntimeError(
            "schools.json 在 dry-run 之后发生了变化；请重新生成 dry-run 报告。"
        )
    if plan.get("display_order_sha256"):
        current_order = (
            hashlib.sha256(DISPLAY_ORDER_FILE.read_bytes()).hexdigest()
            if DISPLAY_ORDER_FILE.is_file()
            else ""
        )
        if plan["display_order_sha256"] != current_order:
            raise RuntimeError(
                "display_order.json 在 dry-run 之后发生了变化；请重新生成 dry-run 报告。"
            )


def _verify_no_drift(plan: dict, *, fill_school_ids: bool) -> list[str]:
    """写库前逐行复核 plan 里的 before 仍是当前值；不执行的回填不参与校验。"""
    con = _open_readonly(DATABASE_FILE)
    try:
        questions = {
            int(qid): (source or "", str(sid or "").strip())
            for qid, source, sid in con.execute(
                "SELECT id, source, source_school_id FROM questions"
            )
        }
        regions = {
            int(row_id): (region or "", str(sid or "").strip())
            for row_id, region, sid in con.execute(
                "SELECT id, region, school_id FROM question_sources"
            )
        }
    finally:
        con.close()

    problems: list[str] = []
    already_applied = 0
    for item in plan.get("changes") or []:
        current = questions.get(int(item["qid"]))
        if current is None:
            problems.append(f"题目 #{item['qid']} 已不存在")
        elif current[0] == item["before"]:
            continue
        elif current[0] == item["after"]:
            already_applied += 1
        else:
            problems.append(
                f"题目 #{item['qid']} 的 source 在执行前被改动（当前：{current[0]}）"
            )
    if fill_school_ids:
        for item in plan.get("id_backfills") or []:
            current = questions.get(int(item["qid"]))
            if current is None:
                problems.append(f"题目 #{item['qid']} 已不存在（school_id 回填）")
            elif current[0] != item["source"]:
                problems.append(
                    f"题目 #{item['qid']} 的 source 在执行前被改动（school_id 回填）"
                )
    for item in (plan.get("question_sources") or {}).get("changes") or []:
        current = regions.get(int(item["row_id"]))
        if current is None:
            problems.append(f"question_sources 行 {item['row_id']} 已不存在")
        elif current[0] == item["before"]:
            continue
        elif current[0] == item["after"]:
            already_applied += 1
        else:
            problems.append(
                f"question_sources 行 {item['row_id']} 的 region 在执行前被改动"
            )
    if already_applied:
        print("  提示：%d 行已是目标值，将跳过。" % already_applied)
    return problems


def _rebuild_fingerprints(db, question_ids, questions_by_id) -> tuple[int, int]:
    from mathbank.database import QuestionFingerprint
    from mathbank.question_duplicate_service import (
        fingerprint_for_question,
        upsert_question_fingerprint,
    )

    target = {int(qid) for qid in question_ids}
    if not target:
        return 0, 0
    deleted = (
        db.query(QuestionFingerprint)
        .filter(QuestionFingerprint.question_id.in_(list(target)))
        .delete(synchronize_session=False)
    )
    rebuilt = 0
    for question in questions_by_id.values():
        if question.id not in target:
            continue
        try:
            fingerprint = fingerprint_for_question(
                question,
                uploads_dir=PROJECT_ROOT / "static" / "uploads",
                url_prefix="static/uploads",
            )
        except Exception as exc:  # pragma: no cover - 仅供人工排查
            print("  ⚠️ 题目 #%s 指纹重算失败（%s）" % (question.id, type(exc).__name__))
            continue
        upsert_question_fingerprint(db, question, fingerprint)
        rebuilt += 1
    return int(deleted or 0), rebuilt


def apply_plan(
    plan_path: Path,
    *,
    fill_school_ids: bool = False,
    retain: int = 10,
    export: bool = True,
) -> int:
    from mathbank.backup import RuntimeLockError, acquire_runtime_lock, create_full_backup

    plan = _load_plan(plan_path)
    _verify_plan_fingerprint(plan)

    try:
        lock_context = acquire_runtime_lock()
    except RuntimeLockError as exc:
        print("✋ 检测到 27182 服务仍在运行：请先关闭服务，再执行 --apply。")
        return 2

    with lock_context:
        problems = _verify_no_drift(plan, fill_school_ids=fill_school_ids)
        if problems:
            print("✋ 数据在 dry-run 之后发生了变化，已中止：")
            for problem in problems[:20]:
                print("  - " + problem)
            if len(problems) > 20:
                print("  … 共 %d 项" % len(problems))
            return 3

        print("💾 正在创建完整备份 ...")
        backup_path = create_full_backup(retention=retain)
        print("   备份完成：%s" % backup_path)

        from mathbank.database import (
            Question,
            QuestionSource,
            SessionLocal,
            init_db,
        )
        from mathbank.sync_helper import export_database_to_files

        print("🧱 正在确认数据库 schema ...")
        init_db()

        db = SessionLocal()
        try:
            changed_qids: set[int] = set()
            skipped = 0

            for item in plan.get("changes") or []:
                question = db.query(Question).filter(Question.id == int(item["qid"])).first()
                if question is None:
                    skipped += 1
                    continue
                if (question.source or "") == item["after"]:
                    continue
                if (question.source or "") != item["before"]:
                    print("  ⚠️ 跳过 #%s：source 已变化" % item["qid"])
                    skipped += 1
                    continue
                question.source = item["after"]
                changed_qids.add(int(item["qid"]))

            backfilled = 0
            if fill_school_ids:
                for item in plan.get("id_backfills") or []:
                    question = (
                        db.query(Question).filter(Question.id == int(item["qid"])).first()
                    )
                    if question is None:
                        skipped += 1
                        continue
                    if (question.source or "") != item["source"]:
                        print("  ⚠️ 跳过 #%s：source 已变化（school_id 回填）" % item["qid"])
                        skipped += 1
                        continue
                    if str(question.source_school_id or "").strip():
                        continue
                    question.source_school_id = item["school_id"]
                    backfilled += 1
                    changed_qids.add(int(item["qid"]))

            qs_updated = 0
            qs_school_ids = 0
            for item in (plan.get("question_sources") or {}).get("changes") or []:
                row = (
                    db.query(QuestionSource)
                    .filter(QuestionSource.id == int(item["row_id"]))
                    .first()
                )
                if row is None:
                    skipped += 1
                    continue
                current_region = row.region or ""
                if current_region not in (item["before"], item["after"]):
                    print(
                        "  ⚠️ 跳过 question_sources 行 %s：region 已变化"
                        % item["row_id"]
                    )
                    skipped += 1
                    continue
                if current_region != item["after"]:
                    row.region = item["after"]
                    qs_updated += 1
                if (
                    fill_school_ids
                    and item.get("set_school_id")
                    and not str(row.school_id or "").strip()
                ):
                    row.school_id = item["school_id"]
                    qs_school_ids += 1
                if row.question_id:
                    changed_qids.add(int(row.question_id))

            db.flush()

            questions_by_id = {
                question.id: question
                for question in db.query(Question)
                .filter(Question.id.in_(list(changed_qids)))
                .all()
            }
            deleted, rebuilt = _rebuild_fingerprints(db, changed_qids, questions_by_id)

            db.commit()
            print(
                "\n✅ source 改写 %d 题；school_id 回填 %d 题；"
                "question_sources region 改写 %d 条、school_id 回填 %d 条；跳过 %d 项"
                % (len([i for i in plan.get('changes') or [] if int(i['qid']) in changed_qids]),
                   backfilled, qs_updated, qs_school_ids, skipped)
            )
            if not fill_school_ids and plan.get("id_backfills"):
                print(
                    "  提示：%d 条 source_school_id 回填未执行（加 --fill-school-ids 启用）。"
                    % len(plan["id_backfills"])
                )
            print("🗂  指纹：删除旧 %d 行，重算 %d 条" % (deleted, rebuilt))

            if export:
                print("\n🔄 正在同步导出 JSON 备份与 AI 专属题库 ...")
                export_database_to_files(db)
                print("🎉 自动同步完成！")
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    print("\n🔍 正在做幂等复核 ...")
    fresh = build_plan()
    remaining = len(fresh.get("changes") or []) + len(
        (fresh.get("question_sources") or {}).get("changes") or []
    )
    if remaining:
        print("  ⚠️ 仍有 %d 条待改写，请检查报告。" % remaining)
        return 4
    if not fill_school_ids and fresh.get("id_backfills"):
        print(
            "  ℹ️ source 已全部规范；仍有 %d 条 school_id 建议回填（未启用）。"
            % len(fresh["id_backfills"])
        )
    else:
        print("  ✅ source 与 region 已全部规范，重复运行不会再改任何行。")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="存量题库来源学校名规范化（默认 dry-run，不写库）"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="正式执行；必须同时提供 dry-run 生成的 --plan",
    )
    parser.add_argument(
        "--plan",
        type=Path,
        default=None,
        help="dry-run 生成的 plan JSON；--apply 时必填",
    )
    parser.add_argument(
        "--fill-school-ids",
        action="store_true",
        help="同时回填文本不变题目的 source_school_id（默认不改）",
    )
    parser.add_argument(
        "--no-export",
        action="store_true",
        help="跳过 JSON/Markdown 同步导出",
    )
    parser.add_argument(
        "--retain",
        type=int,
        default=10,
        help="完整备份保留最近多少份（默认 10）",
    )
    args = parser.parse_args(argv)

    if args.apply:
        if args.plan is None:
            print("✋ --apply 必须携带 dry-run 生成的 --plan <报告.json>。")
            return 1
        return apply_plan(
            args.plan,
            fill_school_ids=args.fill_school_ids,
            retain=args.retain,
            export=not args.no_export,
        )

    plan = build_plan()
    json_path, md_path = write_report(plan)
    print_console_report(plan, json_path, md_path)
    return 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())
