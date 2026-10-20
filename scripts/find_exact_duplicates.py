#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读排查全库「完全重复」题目（双胞胎）。

以 ``question_fingerprints`` 的最新版本为唯一数据源，按 ``exact_hash``
（题干 + 选项的有序规范化结果）分组，再结合 ``answer_hash`` 与配图签名字段
把结果分成四档：

  * 完全重复：题干、选项、解析、配图签名全部一致；
  * 解析不同/缺失：题干与选项一致，但答案/解析哈希不同（常见于重复导入时
    漏掉解析）；
  * 配图不同：题干、选项、解析一致，但可见配图或 TikZ 签名不同；
  * 排版变体：``exact_hash`` 不同，但 ``critical_math_hash`` 相同且去掉空白、
    全角标点与 ``\\le/\\leq`` 一类等价写法后正文逐字一致——同一道题在不同
    录入路径下留下的排版差异。

脚本**只读**：数据库以 SQLite ``mode=ro`` + ``PRAGMA query_only`` 打开，
绝不写库。默认在 ``outputs/`` 下生成 Markdown 报告与 JSON 清理计划，
``--no-report`` 可只打印到终端。

用法::

    python -m scripts.find_exact_duplicates
    python -m scripts.find_exact_duplicates --json
    python -m scripts.find_exact_duplicates --db path/to/other.db --no-report

清理方案只做预览、不执行。确认后才另写 ``--apply`` 脚本：
方案 A 保留最早录入的一条，方案 B 保留最新录入的一条；其余题目先归档到
隔离区（完整行 JSON + 关联表快照），再移出在线题库。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from mathbank.paths import DATABASE_FILE, PROJECT_ROOT
from mathbank.question_duplicates import normalize_math_text


OUTPUT_DIR = PROJECT_ROOT / "outputs"
QUARANTINE_DIR = PROJECT_ROOT / "data_backup" / "duplicate_quarantine"

# 每张子表都有 question_id；questions 自身用 id。apply 前后都按这份清单对账，
# 新增引用题目的表时必须同步登记，否则孤儿引用检查会静默漏项。
CHILD_TABLES = (
    "question_curriculums",
    "question_fingerprints",
    "question_knowledge",
    "question_tags",
    "question_topics",
    "question_sources",
    "tikz_versions",
    "paper_questions",
)


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except Exception:
            pass


def _connect_read_only(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(f"数据库不存在：{path}")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _latest_fingerprint_version(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT MAX(fingerprint_version) FROM question_fingerprints"
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _seq_numbers(conn: sqlite3.Connection) -> dict[int, int]:
    rows = conn.execute("SELECT id FROM questions ORDER BY id ASC").fetchall()
    return {int(row[0]): index + 1 for index, row in enumerate(rows)}


def _answer_present(raw: str | None) -> bool:
    return bool(str(raw or "").strip())


def _preview(text: str | None, limit: int = 100) -> str:
    raw = " ".join(str(text or "").replace("\r", "").split())
    return raw if len(raw) <= limit else raw[: limit - 1] + "…"


def _load_instances(conn: sqlite3.Connection, version: int) -> dict[int, dict]:
    rows = conn.execute(
        """
        SELECT q.id, q.content, q.answer_markdown, q.question_type, q.source,
               q.tags, q.created_at, q.category_compulsory, q.category_chapter,
               q.category_knowledge,
               f.exact_hash, f.answer_hash, f.content_revision_hash,
               f.critical_math_hash, f.visible_image_hashes, f.tikz_hashes,
               f.figure_count, f.choice_count, f.status
        FROM question_fingerprints f
        JOIN questions q ON q.id = f.question_id
        WHERE f.fingerprint_version = ?
        ORDER BY q.id ASC
        """,
        (version,),
    ).fetchall()
    instances: dict[int, dict] = {}
    for row in rows:
        instances[int(row["id"])] = {
            "id": int(row["id"]),
            "content": row["content"] or "",
            "answer_markdown": row["answer_markdown"] or "",
            "question_type": row["question_type"] or "",
            "source": row["source"] or "",
            "tags": row["tags"] or "",
            "created_at": row["created_at"] or "",
            "compulsory": row["category_compulsory"] or "",
            "chapter": row["category_chapter"] or "",
            "knowledge": row["category_knowledge"] or "",
            "exact_hash": row["exact_hash"] or "",
            "answer_hash": row["answer_hash"] or "",
            "content_revision_hash": row["content_revision_hash"] or "",
            "critical_math_hash": row["critical_math_hash"] or "",
            "visible_image_hashes": row["visible_image_hashes"] or "[]",
            "tikz_hashes": row["tikz_hashes"] or "[]",
            "figure_count": int(row["figure_count"] or 0),
            "choice_count": int(row["choice_count"] or 0),
            "fingerprint_status": row["status"] or "",
            "has_answer": _answer_present(row["answer_markdown"]),
            "answer_chars": len(str(row["answer_markdown"] or "").strip()),
        }
    return instances


def _group_by(instances: Iterable[dict], key_fields: tuple[str, ...]) -> list[list[dict]]:
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for item in instances:
        buckets[tuple(item[field] for field in key_fields)].append(item)
    groups = [sorted(items, key=lambda value: value["id"]) for items in buckets.values()]
    groups = [group for group in groups if len(group) > 1]
    groups.sort(key=lambda group: (-len(group), group[0]["id"]))
    return groups


def _presentation_key(value: str) -> str:
    """把只剩排版差异的正文折叠成同一个比较键。

    ``normalize_math_text`` 已统一全角括号/逗号、``\\le``/``\\leq``、
    ``\\quad`` 与数学定界符；这里再折叠句号、顿号与全部空白。空白在中文
    混排里纯属排版，而数学含义是否一致由 ``critical_math_hash`` 先行把关。
    """

    text = normalize_math_text(value)
    text = text.replace("。", ".").replace("、", ",")
    return re.sub(r"\s+", "", text)


def _layout_groups(
    instances: Iterable[dict], excluded_ids: set[int]
) -> list[list[dict]]:
    """在 ``critical_math_hash`` 相同的候选桶里找仅排版不同的重复题。"""

    buckets: dict[str, list[dict]] = defaultdict(list)
    for item in instances:
        if item["id"] in excluded_ids or not item["critical_math_hash"]:
            continue
        buckets[item["critical_math_hash"]].append(item)

    groups: list[list[dict]] = []
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        by_key: dict[str, list[dict]] = defaultdict(list)
        for item in bucket:
            by_key[_presentation_key(item["content"])].append(item)
        for keyed in by_key.values():
            if len(keyed) < 2:
                continue
            # 同一个 exact_hash 的组已由更严格的三档处理，这里只收排版分叉。
            if len({item["exact_hash"] for item in keyed}) < 2:
                continue
            groups.append(sorted(keyed, key=lambda value: value["id"]))
    groups.sort(key=lambda group: (-len(group), group[0]["id"]))
    return groups


def _extract_question_ids(raw: str) -> set[int]:
    try:
        payload = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return set()
    found: set[int] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"question_id", "questionId"}:
                    try:
                        found.add(int(value))
                    except (TypeError, ValueError):
                        pass
                elif key in {"extra_ids", "question_ids", "questionIds"} and isinstance(value, list):
                    for entry in value:
                        try:
                            found.add(int(entry))
                        except (TypeError, ValueError):
                            pass
                else:
                    walk(value)
        elif isinstance(node, list):
            for entry in node:
                walk(entry)

    walk(payload)
    return found


def _collect_references(conn: sqlite3.Connection) -> dict[int, set[str]]:
    references: dict[int, set[str]] = defaultdict(set)
    for row in conn.execute(
        "SELECT paper_id, question_id FROM paper_questions WHERE question_id IS NOT NULL"
    ):
        references[int(row["question_id"])].add(f"试卷#{row['paper_id']}")
    for table, column, label in (
        ("lesson_plans", "question_refs", "教案"),
        ("lecture_drafts", "config", "讲义草稿"),
    ):
        try:
            rows = conn.execute(f"SELECT id, {column} FROM {table}").fetchall()
        except sqlite3.Error:
            continue
        for row in rows:
            for question_id in _extract_question_ids(row[column]):
                references[question_id].add(f"{label}#{row['id']}")
    return references


def _sort_key(instance: dict) -> tuple:
    return (str(instance["created_at"] or ""), int(instance["id"]))


def _keeper(instances: list[dict], scheme: str) -> dict:
    ordered = sorted(instances, key=_sort_key)
    return ordered[0] if scheme == "A" else ordered[-1]


def _recommended_keeper(instances: list[dict]) -> dict:
    """优先保留有解析的一条，其次保留最早录入的一条。"""

    answered = [item for item in instances if item["has_answer"]]
    pool = answered or instances
    return sorted(pool, key=_sort_key)[0]


def _scheme_payload(
    instances: list[dict],
    scheme: str,
    seq_map: dict[int, int],
    references: dict[int, set[str]],
) -> dict:
    keeper = _keeper(instances, scheme)
    removed = [item for item in instances if item["id"] != keeper["id"]]
    warnings: list[str] = []
    if not keeper["has_answer"]:
        answered_removed = [item for item in removed if item["has_answer"]]
        if answered_removed:
            warnings.append(
                "保留项没有解析，但将被隔离的 "
                + "、".join(f"#{item['id']}" for item in answered_removed)
                + " 有解析；直接执行会丢失解析。"
            )
    return {
        "scheme": scheme,
        "label": "保留最早录入" if scheme == "A" else "保留最新录入",
        "keeper": {
            "id": keeper["id"],
            "seq_num": seq_map.get(keeper["id"]),
            "created_at": keeper["created_at"],
            "has_answer": keeper["has_answer"],
        },
        "remove": [
            {
                "id": item["id"],
                "seq_num": seq_map.get(item["id"]),
                "created_at": item["created_at"],
                "has_answer": item["has_answer"],
                "references": sorted(references.get(item["id"], set())),
            }
            for item in removed
        ],
        "warnings": warnings,
    }


def _build_group(instances: list[dict], tier: str, seq_map, references) -> dict:
    recommended = _recommended_keeper(instances)
    signature_kind = "critical_math_hash" if tier == "layout_variant" else "exact_hash"
    return {
        "tier": tier,
        "signature_kind": signature_kind,
        "signature": instances[0][signature_kind],
        "exact_hash": instances[0]["exact_hash"],
        "size": len(instances),
        "recommended_keeper": recommended["id"],
        "instances": [
            {
                "id": item["id"],
                "seq_num": seq_map.get(item["id"]),
                "created_at": item["created_at"],
                "question_type": item["question_type"],
                "has_answer": item["has_answer"],
                "answer_chars": item["answer_chars"],
                "source": item["source"],
                "tags": item["tags"],
                "category": " / ".join(
                    part for part in (item["compulsory"], item["chapter"], item["knowledge"]) if part
                ),
                "preview": _preview(item["content"]),
                "references": sorted(references.get(item["id"], set())),
            }
            for item in sorted(instances, key=lambda value: value["id"])
        ],
        "plans": {
            scheme: _scheme_payload(instances, scheme, seq_map, references)
            for scheme in ("A", "B")
        },
    }


def collect_report(db_path: Path = DATABASE_FILE) -> dict:
    conn = _connect_read_only(db_path)
    try:
        version = _latest_fingerprint_version(conn)
        total_questions = int(
            conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        )
        if version is None:
            return {
                "database": str(db_path),
                "fingerprint_version": None,
                "total_questions": total_questions,
                "fingerprint_rows": 0,
                "missing_fingerprints": total_questions,
                "groups": [],
                "stats": {
                    "strict_groups": 0,
                    "strict_affected": 0,
                    "strict_redundant": 0,
                    "answer_mismatch_groups": 0,
                    "answer_mismatch_affected": 0,
                    "image_mismatch_groups": 0,
                    "image_mismatch_affected": 0,
                    "layout_variant_groups": 0,
                    "layout_variant_affected": 0,
                    "layout_variant_redundant": 0,
                    "total_groups": 0,
                    "total_affected": 0,
                    "total_redundant": 0,
                },
            }

        instances = _load_instances(conn, version)
        seq_map = _seq_numbers(conn)
        references = _collect_references(conn)
        missing = total_questions - len(instances)

        strict_groups = _group_by(
            instances.values(),
            (
                "exact_hash",
                "answer_hash",
                "visible_image_hashes",
                "tikz_hashes",
                "figure_count",
            ),
        )
        strict_ids = {item["id"] for group in strict_groups for item in group}

        answer_groups = [
            group
            for group in _group_by(instances.values(), ("exact_hash",))
            if len({item["answer_hash"] for item in group}) > 1
        ]
        answer_ids = {item["id"] for group in answer_groups for item in group} - strict_ids

        image_groups = []
        for group in _group_by(
            instances.values(),
            ("exact_hash", "answer_hash"),
        ):
            image_signatures = {
                (item["visible_image_hashes"], item["tikz_hashes"], item["figure_count"])
                for item in group
            }
            if len(image_signatures) > 1:
                filtered = [
                    item for item in group if item["id"] not in strict_ids
                ]
                if len(filtered) > 1 and not {
                    item["id"] for item in filtered
                } <= answer_ids:
                    image_groups.append(filtered)
        image_ids = {
            item["id"] for group in image_groups for item in group
        } - strict_ids - answer_ids

        layout_groups = _layout_groups(
            instances.values(), strict_ids | answer_ids | image_ids
        )

        groups = []
        for group in strict_groups:
            groups.append(_build_group(group, "strict", seq_map, references))
        for group in answer_groups:
            groups.append(_build_group(group, "answer_mismatch", seq_map, references))
        for group in image_groups:
            groups.append(_build_group(group, "image_mismatch", seq_map, references))
        for group in layout_groups:
            groups.append(_build_group(group, "layout_variant", seq_map, references))

        total_groups = len(groups)
        total_affected = sum(len(group["instances"]) for group in groups)
        return {
            "database": str(db_path),
            "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "fingerprint_version": version,
            "total_questions": total_questions,
            "fingerprint_rows": len(instances),
            "missing_fingerprints": max(0, missing),
            "groups": groups,
            "stats": {
                "strict_groups": len(strict_groups),
                "strict_affected": sum(len(group) for group in strict_groups),
                "strict_redundant": sum(len(group) - 1 for group in strict_groups),
                "answer_mismatch_groups": len(answer_groups),
                "answer_mismatch_affected": sum(len(group) for group in answer_groups),
                "answer_mismatch_redundant": sum(
                    len(group) - 1 for group in answer_groups
                ),
                "image_mismatch_groups": len(image_groups),
                "image_mismatch_affected": sum(len(group) for group in image_groups),
                "image_mismatch_redundant": sum(len(group) - 1 for group in image_groups),
                "layout_variant_groups": len(layout_groups),
                "layout_variant_affected": sum(len(group) for group in layout_groups),
                "layout_variant_redundant": sum(
                    len(group) - 1 for group in layout_groups
                ),
                "total_groups": total_groups,
                "total_affected": total_affected,
                "total_redundant": total_affected - total_groups,
            },
        }
    finally:
        conn.close()


TIER_TITLES = {
    "strict": "完全重复（题干 + 选项 + 解析 + 配图一致）",
    "answer_mismatch": "题干 + 选项一致，解析不同或缺失",
    "image_mismatch": "题干 + 选项 + 解析一致，配图不同",
    "layout_variant": "仅排版 / 标点 / 等价数学写法不同（需人工确认）",
}

TIER_NOTES = {
    "layout_variant": (
        "判据：`critical_math_hash` 相同，且正文去掉空白、全角标点与 "
        "`\\le`/`\\leq` 一类等价写法后逐字一致。"
    ),
}


def render_markdown(report: dict) -> str:
    stats = report["stats"]
    lines: list[str] = [
        "# 全库完全重复题目排查报告",
        "",
        f"- 生成时间：{report.get('generated_at') or '-'}",
        f"- 数据库：`{report['database']}`",
        f"- 指纹版本：{report['fingerprint_version']}",
        f"- 题目总数：{report['total_questions']}",
        f"- 已建指纹：{report['fingerprint_rows']}（缺失 {report['missing_fingerprints']}）",
        "",
        "## 结论",
        "",
        f"- 完全重复：**{stats['strict_groups']} 组 / {stats['strict_affected']} 题**，"
        f"可减少 **{stats['strict_redundant']} 题**。",
        f"- 题干+选项一致但解析不同/缺失：**{stats['answer_mismatch_groups']} 组 / "
        f"{stats['answer_mismatch_affected']} 题**，可减少 "
        f"**{stats['answer_mismatch_redundant']} 题**（删除前需先合并解析）。",
        f"- 题干+选项+解析一致但配图不同：**{stats['image_mismatch_groups']} 组 / "
        f"{stats['image_mismatch_affected']} 题**（不自动处理，需人工看图）。",
        f"- 仅排版/标点/等价写法不同：**{stats['layout_variant_groups']} 组 / "
        f"{stats['layout_variant_affected']} 题**，可减少 "
        f"**{stats['layout_variant_redundant']} 题**（正文逐字一致，需人工确认后合并解析）。",
        "",
        f"> 合计：**{stats['total_groups']} 组 / {stats['total_affected']} 题**，"
        f"最多可减少 **{stats['total_redundant']} 题**。",
        "",
        "> 本报告只读生成，未对数据库做任何写入。清理方案仅为预览，等待确认后才执行。",
        "",
    ]

    if not report["groups"]:
        lines.append("未发现重复组。")
        return "\n".join(lines) + "\n"

    for tier in ("strict", "answer_mismatch", "image_mismatch", "layout_variant"):
        tier_groups = [group for group in report["groups"] if group["tier"] == tier]
        if not tier_groups:
            continue
        lines.extend([f"## {TIER_TITLES[tier]}（{len(tier_groups)} 组）", ""])
        if tier in TIER_NOTES:
            lines.extend([f"> {TIER_NOTES[tier]}", ""])
        for index, group in enumerate(tier_groups, start=1):
            signature = group.get("signature") or group.get("exact_hash") or ""
            signature_label = (
                "结构指纹"
                if group.get("signature_kind") == "critical_math_hash"
                else "指纹"
            )
            lines.extend(
                [
                    f"### 组 {index} · {signature_label} `{signature[:16]}…` · "
                    f"{group['size']} 题 · 建议保留 #{group['recommended_keeper']}",
                    "",
                    "| 内部 ID | 题号 | 创建时间 | 解析 | 题型 | 来源 | 引用 | 题干预览 |",
                    "| --- | --- | --- | --- | --- | --- | --- | --- |",
                ]
            )
            for item in group["instances"]:
                refs = "、".join(item["references"]) or "—"
                answer = f"{item['answer_chars']} 字" if item["has_answer"] else "缺失"
                lines.append(
                    f"| #{item['id']} | #{item['seq_num']} | {item['created_at']} | "
                    f"{answer} | {item['question_type']} | {item['source'] or '—'} | "
                    f"{refs} | {item['preview'].replace('|', '\\|')} |"
                )
            lines.append("")
            for scheme in ("A", "B"):
                plan = group["plans"][scheme]
                keeper = plan["keeper"]
                removed = "、".join(
                    f"#{item['id']}（题号 #{item['seq_num']}，"
                    f"{'有解析' if item['has_answer'] else '无解析'}）"
                    for item in plan["remove"]
                )
                lines.append(
                    f"- **方案 {scheme} · {plan['label']}**：保留 #{keeper['id']}"
                    f"（题号 #{keeper['seq_num']}）；隔离 {removed}。"
                )
                for warning in plan["warnings"]:
                    lines.append(f"  - ⚠️ {warning}")
            lines.append("")

    lines.extend(
        [
            "## 执行方案（待确认，尚未执行）",
            "",
            "1. 先创建并通过清单校验的完整备份，再写入隔离区"
            "（`data_backup/duplicate_quarantine/<时间戳>/`），保存被移除题目的完整行、"
            "考点/来源/标签等关联数据。",
            "2. 把 `paper_questions`、教案 `question_refs`、讲义草稿 `config` 里指向"
            "被移除题目的引用改指向保留题；同一试卷/教案若已同时引用两者，先合并去重，"
            "避免出现重复题位或空题位。",
            "3. 在同一事务中删除被移除题目及其关联行；任何一步失败整批回滚。",
            "4. 若保留题缺少解析而被移除题有解析，先合并解析再删除（方案 A/B 都不例外）。",
            "",
            "请在确认方案后回复 A 或 B（或指定自定义保留规则），再生成 `--apply` 脚本。",
            "",
        ]
    )
    return "\n".join(lines)


def _replace_id_list(entries: Iterable, mapping: dict[int, int]) -> tuple[list, bool]:
    """按映射替换题目 ID 并去重，保留原有顺序。"""

    result: list = []
    seen: set[int] = set()
    changed = False
    for entry in entries:
        try:
            numeric = int(entry)
        except (TypeError, ValueError):
            result.append(entry)
            continue
        replacement = mapping.get(numeric, numeric)
        if replacement != numeric:
            changed = True
        if replacement in seen:
            changed = True
            continue
        seen.add(replacement)
        result.append(replacement)
    return result, changed


def _rewrite_lesson_refs(raw: str, mapping: dict[int, int]) -> tuple[str, bool]:
    """把教案 ``question_refs`` 里的 ID 改指保留题，并按题去重。"""

    try:
        payload = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return raw, False
    if not isinstance(payload, dict):
        return raw, False

    changed = False
    items = payload.get("items")
    if isinstance(items, list):
        rewritten: list = []
        seen: set[int] = set()
        for item in items:
            if not isinstance(item, dict) or "question_id" not in item:
                rewritten.append(item)
                continue
            try:
                numeric = int(item["question_id"])
            except (TypeError, ValueError):
                rewritten.append(item)
                continue
            replacement = mapping.get(numeric, numeric)
            if replacement != numeric:
                item = {**item, "question_id": replacement}
                changed = True
            if replacement in seen:
                changed = True
                continue
            seen.add(replacement)
            rewritten.append(item)
        if changed:
            payload["items"] = rewritten
    else:
        # 兼容旧 ``{stage: [ids]}`` 结构。
        for stage, entries in list(payload.items()):
            if isinstance(entries, list):
                replaced, list_changed = _replace_id_list(entries, mapping)
                if list_changed:
                    payload[stage] = replaced
                    changed = True
    return json.dumps(payload, ensure_ascii=False), changed


def _rewrite_lecture_config(raw: str, mapping: dict[int, int]) -> tuple[str, bool]:
    """递归改写讲义草稿 ``config`` 中的编排引用。"""

    try:
        payload = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return raw, False

    changed = False
    id_keys = {"question_id", "questionId"}
    list_keys = {"extra_ids", "question_ids", "questionIds"}

    def walk(node):
        nonlocal changed
        if isinstance(node, dict):
            for key, value in list(node.items()):
                if key in id_keys:
                    try:
                        numeric = int(value)
                    except (TypeError, ValueError):
                        continue
                    replacement = mapping.get(numeric, numeric)
                    if replacement != numeric:
                        node[key] = replacement
                        changed = True
                elif key in list_keys and isinstance(value, list):
                    replaced, list_changed = _replace_id_list(value, mapping)
                    if list_changed:
                        node[key] = replaced
                        changed = True
                else:
                    walk(value)
        elif isinstance(node, list):
            for entry in node:
                walk(entry)
            if node and all(
                isinstance(entry, dict) and "question_id" in entry for entry in node
            ):
                deduped: list = []
                seen: set[int] = set()
                for entry in node:
                    try:
                        numeric = int(entry["question_id"])
                    except (TypeError, ValueError):
                        deduped.append(entry)
                        continue
                    if numeric in seen:
                        changed = True
                        continue
                    seen.add(numeric)
                    deduped.append(entry)
                if len(deduped) != len(node):
                    node[:] = deduped

    walk(payload)
    return json.dumps(payload, ensure_ascii=False), changed


def _resolve_apply_pairs(report: dict, scheme: str) -> list[dict]:
    pairs: list[dict] = []
    for group in report.get("groups", []):
        plan = (group.get("plans") or {}).get(scheme)
        if not plan:
            continue
        keeper = int(plan["keeper"]["id"])
        for item in plan.get("remove") or []:
            pairs.append(
                {
                    "keeper": keeper,
                    "remove": int(item["id"]),
                    "tier": group.get("tier"),
                }
            )
    return pairs


def _verify_pairs_still_duplicates(db_path: Path, pairs: list[dict]) -> None:
    """执行前重扫一次，确保每个 待删/保留 对仍属于同一重复组。"""

    fresh = collect_report(db_path)
    group_index: dict[int, set[int]] = defaultdict(set)
    for index, group in enumerate(fresh.get("groups", [])):
        for item in group["instances"]:
            group_index[int(item["id"])].add(index)
    for pair in pairs:
        common = group_index.get(pair["remove"], set()) & group_index.get(
            pair["keeper"], set()
        )
        if not common:
            raise RuntimeError(
                f"计划已过期：#{pair['remove']} 与 #{pair['keeper']} 不再是同一重复组"
            )


def _quarantine_duplicates(
    db_path: Path, pairs: list[dict], report_path: Path | None
) -> Path:
    """把待删题目及其全部关联行写成只增不改的隔离区快照。"""

    removed_ids = sorted({pair["remove"] for pair in pairs})
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = QUARANTINE_DIR / f"{stamp}_schemeA"
    target.mkdir(parents=True, exist_ok=False)
    try:
        target.chmod(0o700)
    except OSError:
        pass

    conn = _connect_read_only(db_path)
    try:
        placeholders = ",".join("?" * len(removed_ids))
        questions = [
            dict(row)
            for row in conn.execute(
                f"SELECT * FROM questions WHERE id IN ({placeholders}) ORDER BY id",
                removed_ids,
            )
        ]
        related: dict[str, list[dict]] = {}
        for table in CHILD_TABLES:
            related[table] = [
                dict(row)
                for row in conn.execute(
                    f"SELECT * FROM {table} WHERE question_id IN ({placeholders}) "
                    "ORDER BY question_id",
                    removed_ids,
                )
            ]
        import_batches = []
        for row in conn.execute("SELECT * FROM import_batches ORDER BY id"):
            try:
                ids = {int(value) for value in json.loads(row["question_ids"] or "[]")}
            except (TypeError, ValueError):
                continue
            if ids & set(removed_ids):
                import_batches.append(dict(row))
        lesson_plans = [
            dict(row)
            for row in conn.execute("SELECT * FROM lesson_plans ORDER BY id")
            if _extract_question_ids(row["question_refs"]) & set(removed_ids)
        ]
        lecture_drafts = [
            dict(row)
            for row in conn.execute("SELECT * FROM lecture_drafts ORDER BY id")
            if _extract_question_ids(row["config"]) & set(removed_ids)
        ]
    finally:
        conn.close()

    snapshot = {
        "format": "mathbank-duplicate-quarantine",
        "format_version": 1,
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "database": str(db_path),
        "scheme": "A",
        "plan_source": str(report_path) if report_path else None,
        "pairs": pairs,
        "questions": questions,
        "related": related,
        "import_batches": import_batches,
        "lesson_plans": lesson_plans,
        "lecture_drafts": lecture_drafts,
    }
    temp = target / "quarantine.json.tmp"
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, target / "quarantine.json")
    (target / "README.txt").write_text(
        "本目录是「完全重复题目」清理前的隔离区快照。\n"
        "quarantine.json 保存被移除题目的完整行、全部关联表行，以及被改写的\n"
        "试卷/教案/讲义草稿/导入批次原始 JSON。\n"
        "恢复任意一题时，可按 questions 中的整行 INSERT 回库，再补回 related 里\n"
        "对应的子表行；改指前的引用原文同样在 lesson_plans / lecture_drafts /\n"
        "import_batches 中。\n",
        encoding="utf-8",
    )
    return target


def _apply_cleanup(db_path: Path, pairs: list[dict]) -> dict:
    """单事务改指引用、重算派生数据并删除副本题目。

    事务边界覆盖：试卷题位改指、教案/讲义/导入批次引用改写、卷面总分与
    ``usage_count`` 重算、副本题目及其子表行删除。任何一步失败整批回滚，
    不会留下「引用改了一半、题还在」的中间态。
    """

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from mathbank.database import Question

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    session = sessionmaker(bind=engine)()
    mapping = {pair["remove"]: pair["keeper"] for pair in pairs}
    removed_ids = sorted(mapping)
    keeper_ids = sorted(set(mapping.values()))
    affected_paper_ids: set[int] = set()
    document_counts = {"lesson_plans": 0, "lecture_drafts": 0, "import_batches": 0}

    try:
        with session.begin():
            for removed_id in removed_ids:
                keeper_id = mapping[removed_id]
                keeper = session.get(Question, keeper_id)
                removed = session.get(Question, removed_id)
                if keeper is None:
                    raise RuntimeError(f"保留题 #{keeper_id} 不存在，已中止")
                if removed is None:
                    continue

                paper_rows = session.execute(
                    text(
                        "SELECT id, paper_id FROM paper_questions "
                        "WHERE question_id = :qid"
                    ),
                    {"qid": removed_id},
                ).all()
                for row in paper_rows:
                    affected_paper_ids.add(int(row.paper_id))
                    existing = session.execute(
                        text(
                            "SELECT id FROM paper_questions "
                            "WHERE paper_id = :pid AND question_id = :qid"
                        ),
                        {"pid": row.paper_id, "qid": keeper_id},
                    ).first()
                    if existing:
                        # 同一张卷已引用保留题：删除副本题位，保留原有题位与分值。
                        session.execute(
                            text("DELETE FROM paper_questions WHERE id = :id"),
                            {"id": row.id},
                        )
                    else:
                        session.execute(
                            text(
                                "UPDATE paper_questions SET question_id = :keeper "
                                "WHERE id = :id"
                            ),
                            {"keeper": keeper_id, "id": row.id},
                        )

                session.execute(
                    text("DELETE FROM question_sources WHERE question_id = :qid"),
                    {"qid": removed_id},
                )
                session.execute(
                    text("DELETE FROM tikz_versions WHERE question_id = :qid"),
                    {"qid": removed_id},
                )
                session.delete(removed)
            session.flush()

            for row in session.execute(
                text("SELECT id, question_refs FROM lesson_plans")
            ).all():
                rewritten, changed = _rewrite_lesson_refs(row.question_refs, mapping)
                if changed:
                    session.execute(
                        text(
                            "UPDATE lesson_plans SET question_refs = :value "
                            "WHERE id = :id"
                        ),
                        {"value": rewritten, "id": row.id},
                    )
                    document_counts["lesson_plans"] += 1

            for row in session.execute(
                text("SELECT id, config FROM lecture_drafts")
            ).all():
                rewritten, changed = _rewrite_lecture_config(row.config, mapping)
                if changed:
                    session.execute(
                        text(
                            "UPDATE lecture_drafts SET config = :value WHERE id = :id"
                        ),
                        {"value": rewritten, "id": row.id},
                    )
                    document_counts["lecture_drafts"] += 1

            for row in session.execute(
                text("SELECT id, question_ids FROM import_batches")
            ).all():
                try:
                    entries = json.loads(row.question_ids or "[]")
                except (TypeError, ValueError):
                    continue
                if not isinstance(entries, list):
                    continue
                replaced, changed = _replace_id_list(entries, mapping)
                if changed:
                    session.execute(
                        text(
                            "UPDATE import_batches SET question_ids = :value, "
                            "question_count = :count WHERE id = :id"
                        ),
                        {
                            "value": json.dumps(replaced, ensure_ascii=False),
                            "count": len(replaced),
                            "id": row.id,
                        },
                    )
                    document_counts["import_batches"] += 1

            for paper_id in sorted(affected_paper_ids):
                total = session.execute(
                    text(
                        "SELECT COALESCE(SUM(score), 0) FROM paper_questions "
                        "WHERE paper_id = :pid"
                    ),
                    {"pid": paper_id},
                ).scalar()
                session.execute(
                    text("UPDATE papers SET total_score = :total WHERE id = :pid"),
                    {"total": int(total or 0), "pid": paper_id},
                )

            # usage_count 的绝对口径 = 现存 paper_questions 绑定数（与删卷路由一致）。
            for keeper_id in keeper_ids:
                keeper = session.get(Question, keeper_id)
                if keeper is None:
                    continue
                count = session.execute(
                    text(
                        "SELECT COUNT(*) FROM paper_questions "
                        "WHERE question_id = :qid"
                    ),
                    {"qid": keeper_id},
                ).scalar()
                keeper.usage_count = int(count or 0)
                session.flush()
                snapshot = json.dumps(keeper.to_dict(), ensure_ascii=False)
                session.execute(
                    text(
                        "UPDATE paper_questions SET snapshot_json = :snapshot "
                        "WHERE question_id = :qid"
                    ),
                    {"snapshot": snapshot, "qid": keeper_id},
                )
    except Exception:
        session.rollback()
        raise
    else:
        session.close()
        return {
            "removed": removed_ids,
            "keeper_ids": keeper_ids,
            "mapping": mapping,
            "affected_paper_ids": sorted(affected_paper_ids),
            "documents": document_counts,
        }


def _cleanup_removed_images(db_path: Path, image_paths: list[str]) -> int:
    """删除副本题留下的、已无任何题目/知识块引用的图片（best-effort）。"""

    if not image_paths:
        return 0
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from mathbank.asset_security import AssetSecurityError, resolve_upload_asset, trash_upload_asset
    from mathbank.image_references import collect_referenced_assets
    from mathbank.paths import UPLOADS_DIR

    url_prefix = "static/uploads"
    candidates = set()
    for reference in image_paths:
        try:
            candidates.add(
                resolve_upload_asset(
                    reference,
                    uploads_dir=str(UPLOADS_DIR),
                    url_prefix=url_prefix,
                    require_file=False,
                )
            )
        except AssetSecurityError:
            continue
    if not candidates:
        return 0

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    session = sessionmaker(bind=engine)()
    removed = 0
    try:
        referenced = collect_referenced_assets(
            session, uploads_dir=str(UPLOADS_DIR), url_prefix=url_prefix
        )
        for candidate in candidates - referenced:
            if trash_upload_asset(str(candidate), str(UPLOADS_DIR)):
                removed += 1
    finally:
        session.close()
    return removed


def _verify_cleanup(
    db_path: Path, pairs: list[dict], expected_total: int
) -> dict:
    """独立复核：题数、孤儿引用、卷面分值、重复组。"""

    removed_ids = sorted({pair["remove"] for pair in pairs})
    conn = _connect_read_only(db_path)
    try:
        total = int(conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0])
        orphans: dict[str, int] = {}
        placeholders = ",".join("?" * len(removed_ids))
        for table in CHILD_TABLES:
            count = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM {table} "
                    f"WHERE question_id IN ({placeholders})",
                    removed_ids,
                ).fetchone()[0]
            )
            if count:
                orphans[table] = count
        orphan_batches = []
        for row in conn.execute("SELECT id, question_ids FROM import_batches"):
            try:
                ids = {int(value) for value in json.loads(row["question_ids"] or "[]")}
            except (TypeError, ValueError):
                continue
            if ids & set(removed_ids):
                orphan_batches.append(int(row["id"]))
        if orphan_batches:
            orphans["import_batches"] = len(orphan_batches)
        orphan_lessons = [
            int(row["id"])
            for row in conn.execute("SELECT id, question_refs FROM lesson_plans")
            if _extract_question_ids(row["question_refs"]) & set(removed_ids)
        ]
        if orphan_lessons:
            orphans["lesson_plans"] = len(orphan_lessons)
        orphan_drafts = [
            int(row["id"])
            for row in conn.execute("SELECT id, config FROM lecture_drafts")
            if _extract_question_ids(row["config"]) & set(removed_ids)
        ]
        if orphan_drafts:
            orphans["lecture_drafts"] = len(orphan_drafts)

        paper_rows = conn.execute(
            "SELECT id, total_score FROM papers ORDER BY id"
        ).fetchall()
        mismatched_papers = []
        for row in paper_rows:
            actual = int(
                conn.execute(
                    "SELECT COALESCE(SUM(score), 0) FROM paper_questions "
                    "WHERE paper_id = ?",
                    (row["id"],),
                ).fetchone()[0]
            )
            if actual != int(row["total_score"] or 0):
                mismatched_papers.append(
                    {
                        "paper_id": int(row["id"]),
                        "stored": int(row["total_score"] or 0),
                        "actual": actual,
                    }
                )
    finally:
        conn.close()

    fresh = collect_report(db_path)
    return {
        "total_questions": total,
        "expected_total": expected_total,
        "orphans": orphans,
        "paper_score_mismatches": mismatched_papers,
        "remaining_duplicate_groups": len(fresh.get("groups") or []),
        "remaining_duplicate_redundant": int(fresh["stats"]["total_redundant"]),
    }


def _run_apply(args) -> int:
    from mathbank.backup import RuntimeLockError, acquire_runtime_lock, create_full_backup

    report_path = args.plan
    if report_path is None:
        candidates = sorted(OUTPUT_DIR.glob("exact_duplicates_*.json"))
        if not candidates:
            raise SystemExit("找不到可用的查重报告；请先运行只读扫描或显式传 --plan")
        report_path = candidates[-1]
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    pairs = _resolve_apply_pairs(report, args.scheme)
    if not pairs:
        raise SystemExit(f"{report_path} 中没有方案 {args.scheme} 的待清理题目")

    _verify_pairs_still_duplicates(args.db, pairs)
    fresh = collect_report(args.db)
    by_id = {
        int(item["id"]): item
        for group in fresh.get("groups", [])
        for item in group["instances"]
    }

    print(f"计划来源：{report_path}")
    print(f"方案：{args.scheme}（{'保留最早录入' if args.scheme == 'A' else '保留最新录入'}）")
    for pair in pairs:
        remove = by_id.get(pair["remove"], {})
        keeper = by_id.get(pair["keeper"], {})
        answer = "有解析" if keeper.get("has_answer") else "无解析"
        print(
            f"  #{pair['remove']}（题号 {remove.get('seq_num')}，"
            f"{'有解析' if remove.get('has_answer') else '无解析'}）"
            f" -> 保留 #{pair['keeper']}（题号 {keeper.get('seq_num')}，{answer}）"
        )
    expected_total = int(fresh["total_questions"]) - len(pairs)
    print(
        f"\n题目总数：{fresh['total_questions']} -> {expected_total}；"
        f"待删除 {len(pairs)} 道。"
    )

    if not args.yes:
        print("\n这是 dry-run：没有写入数据库。确认无误后追加 --yes 执行。")
        return 0

    try:
        lock_context = acquire_runtime_lock()
    except RuntimeLockError as exc:
        raise SystemExit(f"{exc}（请先关闭 27182 服务）") from exc

    with lock_context:
        backup_path = create_full_backup(database_path=args.db)
        print(f"\n完整备份：{backup_path}")

        quarantine_path = _quarantine_duplicates(args.db, pairs, Path(report_path))
        print(f"隔离区：{quarantine_path}")

        removed_image_paths: list[str] = []
        conn = _connect_read_only(args.db)
        try:
            placeholders = ",".join("?" * len(pairs))
            for row in conn.execute(
                f"SELECT image_paths FROM questions WHERE id IN ({placeholders})",
                [pair["remove"] for pair in pairs],
            ):
                try:
                    values = json.loads(row["image_paths"] or "[]")
                except (TypeError, ValueError):
                    continue
                if isinstance(values, list):
                    removed_image_paths.extend(str(value) for value in values)
        finally:
            conn.close()

        result = _apply_cleanup(args.db, pairs)
        print(
            "单事务提交完成；引用改写："
            f"教案 {result['documents']['lesson_plans']} 条、"
            f"讲义草稿 {result['documents']['lecture_drafts']} 条、"
            f"导入批次 {result['documents']['import_batches']} 条。"
        )

        try:
            removed_images = _cleanup_removed_images(args.db, removed_image_paths)
        except Exception as exc:
            removed_images = 0
            print(f"[清理] 图片回收失败，留待启动孤儿清理：{type(exc).__name__}")

        try:
            from mathbank.sync_helper import export_database_to_files

            export_info = export_database_to_files()
        except Exception as exc:
            export_info = f"失败：{type(exc).__name__}"
            print(f"[导出] JSON 同步导出失败，留待服务启动后的后台导出：{exc}")

    verification = _verify_cleanup(args.db, pairs, expected_total)
    print("\n复核结果：")
    print(json.dumps(verification, ensure_ascii=False, indent=2))
    print(f"图片回收：{removed_images}")
    print(f"JSON 导出：{export_info}")

    if verification["total_questions"] != expected_total:
        raise SystemExit("题数复核失败，请立即用完整备份恢复")
    if verification["orphans"]:
        raise SystemExit("仍存在孤儿引用，请立即用完整备份恢复")
    if verification["paper_score_mismatches"]:
        raise SystemExit("试卷总分复核失败，请立即用完整备份恢复")
    if verification["remaining_duplicate_groups"]:
        raise SystemExit("清理后仍有重复组，请检查报告")
    print("\n清理完成：题数、孤儿引用、卷面总分与重复组全部通过。")
    return 0


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="只读排查全库完全重复题目")
    parser.add_argument("--db", type=Path, default=DATABASE_FILE, help="SQLite 数据库路径")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出报告")
    parser.add_argument("--no-report", action="store_true", help="不写 outputs/ 报告文件")
    parser.add_argument("--apply", action="store_true", help="按报告执行清理（默认 dry-run）")
    parser.add_argument("--yes", action="store_true", help="确认实际写入数据库")
    parser.add_argument(
        "--scheme", choices=("A", "B"), default="A", help="保留策略：A 最早 / B 最新"
    )
    parser.add_argument(
        "--plan", type=Path, default=None, help="查重报告 JSON；默认取 outputs/ 最新一份"
    )
    args = parser.parse_args(argv)

    if args.apply:
        return _run_apply(args)

    report = collect_report(args.db)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        markdown = render_markdown(report)
        print(markdown)
        if not args.no_report:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            md_path = OUTPUT_DIR / f"exact_duplicates_{stamp}.md"
            json_path = OUTPUT_DIR / f"exact_duplicates_{stamp}.json"
            md_path.write_text(markdown, encoding="utf-8")
            json_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"\n报告已写入：{md_path}")
            print(f"清理计划已写入：{json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
