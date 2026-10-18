# -*- coding: utf-8 -*-
r"""考向树结构合规检查：按《标准.txt》量化每一节的 L1–L5 配比。

用法（从项目根目录运行）::

    python3 -m scripts.check_kaoxiang_tree                    # 查活跃大纲
    python3 -m scripts.check_kaoxiang_tree --txt 考向树        # 查 TXT 目录
    python3 -m scripts.check_kaoxiang_tree --file a.txt       # 查单个 TXT
    python3 -m scripts.check_kaoxiang_tree --only 必修一       # 只看某册
    python3 -m scripts.check_kaoxiang_tree --json report.json # 机器可读结果

只读工具：不写大纲、不写 TXT、不碰数据库。整树重写时必须逐节验收，否则「改完了」
和「达标了」是两件事——本项目此前就是 12% 合规而无人察觉。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from typing import Any, Iterable

# 《标准.txt》第六节：每层考向数建议区间与每节总数区间。
LEVEL_LIMITS: dict[str, tuple[int, int]] = {
    "L1": (1, 3),
    "L2": (2, 5),
    "L3": (2, 4),
    "L4": (0, 2),
    "L5": (0, 1),
}
SECTION_TOTAL_LIMIT = (6, 12)

# 《标准.txt》第三节：各层的命名线索。命中不了不算错（模型/教师措辞多样），
# 只作为「这一层可能被放错位置」的提示，因此默认不参与合规判定。
LEVEL_NAME_HINTS: dict[str, tuple[str, ...]] = {
    "L1": ("判断", "概念", "意义", "定义", "表示", "识别"),
    "L2": ("求", "化简", "运算", "互化", "计算", "作图"),
    "L3": ("比较", "由", "利用", "解不等式", "求参数", "符号"),
    "L4": ("含参", "综合", "讨论", "证明"),
    "L5": ("新定义", "开放", "探究", "建模", "拓展"),
}

_LEVEL_RE = re.compile(r"^L([1-5])\s*(.*)$", re.IGNORECASE)
_KAOXIANG_RE = re.compile(r"^考向\s*(\d+)?\s*[:：]\s*(.*)$")
_SECTION_RE = re.compile(r"^(\d+(?:[.．]\d+)*)\s*(.*)$")
_EMPTY_MARK_RE = re.compile(r"^[（(]?\s*(?:留空|空|暂无|无)\s*[）)]?$")


def _norm_level(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if text in LEVEL_LIMITS else ""


def sections_from_curriculum(curriculum: dict) -> list[dict]:
    """把活跃大纲 / A.json 的 dict 形态摊平成统一的节记录。"""

    records: list[dict] = []
    for compulsory, chapters in (curriculum or {}).items():
        if not isinstance(chapters, dict):
            continue
        for chapter, sections in chapters.items():
            if not isinstance(sections, list):
                continue
            for section in sections:
                if not isinstance(section, dict):
                    continue
                topics = [t for t in (section.get("topics") or []) if isinstance(t, dict)]
                records.append(
                    {
                        "compulsory": str(compulsory),
                        "chapter": str(chapter),
                        "section": str(section.get("name") or ""),
                        "levels": Counter(_norm_level(t.get("level")) for t in topics),
                        "names": {
                            _norm_level(t.get("level")): str(t.get("name") or "")
                            for t in topics
                        },
                        "total": len(topics),
                    }
                )
    return records


def parse_txt_tree(text: str) -> list[dict]:
    """解析 TXT 版式为节记录，兼容树线版与纯缩进版。

    刻意不复用 ``kaoxiang_tree.parse_tree_text``：那个解析器要保真回写，会吃掉
    「（留空）」占位；而检查器恰恰需要知道某一层是**显式留空**还是**漏写了**。
    """

    sections: list[dict] = []
    current: dict | None = None
    level = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"^[│├└┬┼┌┐┘┗┃\s]*[─━]*\s*", "", line).strip()
        if not line:
            continue
        level_match = _LEVEL_RE.match(line)
        topic_match = _KAOXIANG_RE.match(line)
        section_match = _SECTION_RE.match(line)
        if level_match and not topic_match:
            level = "L" + level_match.group(1)
            if current is not None:
                current["declared_levels"].add(level)
            continue
        if topic_match:
            name = (topic_match.group(2) or "").strip()
            if not name:
                continue
            if current is None:
                current = _new_section("(未命名节)")
                sections.append(current)
            current["levels"][level or "L1"] += 1
            current["names"].setdefault(level or "L1", name)
            current["total"] += 1
            continue
        if _EMPTY_MARK_RE.match(line):
            if current is not None and level:
                current["explicit_empty"].add(level)
            continue
        if section_match and not level_match and not topic_match:
            head = (section_match.group(1) + " " + (section_match.group(2) or "")).strip()
            # 章标题行（如「1. 集合与常用逻辑用语」下面还挂 1.1）也会被本正则认成节，
            # 交给调用方按 --chapter-as-group 处理；这里统一按节记录，重复者自然合并。
            current = _new_section(head)
            sections.append(current)
            level = ""
    return sections


def _new_section(name: str) -> dict:
    return {
        "section": name,
        "levels": Counter(),
        "names": {},
        "total": 0,
        "declared_levels": set(),
        "explicit_empty": set(),
    }


def sections_from_txt_dir(path: str) -> list[dict]:
    records: list[dict] = []
    for name in sorted(os.listdir(path)):
        if not name.lower().endswith(".txt") or name.startswith("_"):
            continue
        stem = os.path.splitext(name)[0]
        book = next((b for b in ("必修一", "必修二", "选修一", "选修二", "选修三") if b in stem), stem)
        with open(os.path.join(path, name), encoding="utf-8") as handle:
            for record in parse_txt_tree(handle.read()):
                record.setdefault("compulsory", book)
                record.setdefault("chapter", stem)
                records.append(record)
    return records


def check_section(record: dict) -> list[str]:
    """返回这一节的违规说明；空列表即合规。"""

    problems: list[str] = []
    levels = record["levels"]
    total = record["total"]
    lo, hi = SECTION_TOTAL_LIMIT
    if not (lo <= total <= hi):
        problems.append(f"本节考向 {total} 个，超出建议区间 {lo}~{hi}")
    for level, (l_lo, l_hi) in LEVEL_LIMITS.items():
        count = levels.get(level, 0)
        if count < l_lo:
            problems.append(f"{level} 只有 {count} 个（应 {l_lo}~{l_hi}）")
        elif count > l_hi:
            problems.append(f"{level} 有 {count} 个（应 {l_lo}~{l_hi}）")
    return problems


def naming_hints(record: dict) -> list[str]:
    """命名与层级不符的提示，仅供参考、不影响合规判定。"""

    hints: list[str] = []
    for level, name in (record.get("names") or {}).items():
        if not level or not name:
            continue
        clues = LEVEL_NAME_HINTS.get(level, ())
        if clues and not any(c in name for c in clues):
            hints.append(f"{level}「{name}」未含该层常见用字（{'/'.join(clues)}）")
    return hints


def summarize(records: Iterable[dict], *, with_hints: bool = False) -> dict:
    rows = list(records)
    report = {
        "sections": len(rows),
        "topics": sum(r["total"] for r in rows),
        "compliant": 0,
        "issues": [],
    }
    for row in rows:
        problems = check_section(row)
        if problems:
            report["issues"].append(
                {
                    "compulsory": row.get("compulsory", ""),
                    "chapter": row.get("chapter", ""),
                    "section": row.get("section", ""),
                    "total": row["total"],
                    "levels": {k: v for k, v in sorted(row["levels"].items()) if k},
                    "problems": problems,
                    "naming_hints": naming_hints(row) if with_hints else [],
                }
            )
        else:
            report["compliant"] += 1
    report["compliance_rate"] = (
        round(report["compliant"] / report["sections"], 4) if report["sections"] else 0.0
    )
    return report


def _load_active_curriculum() -> dict:
    from mathbank.paths import DATA_BACKUP_DIR

    path = DATA_BACKUP_DIR / "custom_metadata.json"
    with path.open(encoding="utf-8") as handle:
        return (json.load(handle) or {}).get("curriculum", {})


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 CP936，报告里的中文与「~」「（）」会在重定向时炸掉；
    # 与 main.py 的日志编码口径保持一致，优先 UTF-8。
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass
    parser = argparse.ArgumentParser(description="考向树结构合规检查（只读）")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--txt", help="考向树 TXT 目录")
    source.add_argument("--file", help="单个 TXT 文件")
    source.add_argument("--json-file", help="大纲 JSON 文件（curriculum 或完整 metadata）")
    parser.add_argument("--only", help="只检查指定册次，如 必修一")
    parser.add_argument("--hints", action="store_true", help="附带命名线索提示（不影响判定）")
    parser.add_argument("--json", dest="json_out", help="把报告写成 JSON 文件")
    args = parser.parse_args(argv)

    if args.txt:
        records = sections_from_txt_dir(args.txt)
    elif args.file:
        with open(args.file, encoding="utf-8") as handle:
            records = parse_txt_tree(handle.read())
    elif args.json_file:
        with open(args.json_file, encoding="utf-8") as handle:
            payload = json.load(handle)
        curriculum = payload.get("curriculum", payload) if isinstance(payload, dict) else {}
        records = sections_from_curriculum(curriculum)
    else:
        records = sections_from_curriculum(_load_active_curriculum())

    if args.only:
        records = [r for r in records if args.only in (r.get("compulsory") or "")]
    report = summarize(records, with_hints=args.hints)

    print(
        f"小节 {report['sections']} / 考向 {report['topics']} / "
        f"完全合规 {report['compliant']}（{report['compliance_rate']:.0%}）"
    )
    for issue in report["issues"]:
        where = " / ".join(x for x in (issue["compulsory"], issue["chapter"], issue["section"]) if x)
        levels = " ".join(f"{k}:{v}" for k, v in issue["levels"].items()) or "无"
        print(f"[不合规] {where}  （{levels}）")
        for problem in issue["problems"]:
            print(f"    - {problem}")
        if args.hints:
            for hint in issue["naming_hints"]:
                print(f"    · {hint}")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(f"报告已写入 {args.json_out}")
    # 检查器本身不因不合规而失败退出：它是给人看的体检报告，不是门禁。
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
