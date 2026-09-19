# -*- coding: utf-8 -*-
"""考向树 TXT 底层文件 ↔ Web 大纲双向同步。

目录约定
--------
项目根目录下的 ``考向树/`` 存放用户可直接编辑的考向树纯文本，文件名形如::

    必修一 第一章 集合与常用逻辑用语.txt        （册次 + 章标题）

文本版式为「章 → 节 → L1~L5 难度层 → 考向（含序号与例题）」，同时兼容三种
历史写法：

* 带树线的（``├─`` / ``│`` / ``└─``，例：第一章、第二章）
* 三级小节（``3.1.1 函数的表示与函数值``，例：第三章）
* 纯空格缩进（例：第四章）

同步方向
--------
* **TXT → Web**：读取/启动时比对，TXT 与大纲不一致就以 TXT 为准重写对应章；
* **Web → TXT**：任何写大纲的动作（设置页保存、AI 自动建考向）之后立即回写
  TXT，保证两边始终互为镜像。

考向的 ``level`` / ``no`` / ``example`` 以及节上的分组、层级标题都会原样
保留，因此反复同步不会丢失信息。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime

from mathbank.asset_security import write_private_text_atomic
from mathbank.paths import CURRICULUMS_DIR, IS_TESTING, SYSTEM_GENERATED_DIR, project_path
from mathbank.topics_helper import topic_id_for

KAOXIANG_DIR = project_path("考向树")
DEFAULT_KAOXIANG_DIR = KAOXIANG_DIR
STATE_FILE = SYSTEM_GENERATED_DIR / "kaoxiang_tree_state.json"
PRESET_PATH = CURRICULUMS_DIR / "A.json"

LEVELS = ("L1", "L2", "L3", "L4", "L5")
DEFAULT_LEVEL_TITLES = {
    "L1": "概念理解",
    "L2": "基础运算",
    "L3": "性质应用",
    "L4": "综合运用",
    "L5": "创新拓展",
}

_FILL_CHARS = "│├└┬┼┌┐┘┗┃"
_LINE_CHARS = "─━"
_BRANCH_CHARS = "├└┬┼┌┐"

_SECTION_RE = re.compile(r"^(\d+(?:[.．]\d+)*)\s*(.*)$")
_KAOXIANG_RE = re.compile(r"^考向\s*(\d+)?\s*[:：]\s*(.*)$")
_LEVEL_RE = re.compile(r"^L([1-5])\s*(.*)$", re.IGNORECASE)
_CONT_KEYWORDS = "例|方法|规则|正向|逆向|要点|说明|易错|步骤"
_CONTINUATION_RE = re.compile(r"^(%s)\s*[:：]\s*(.*)$" % _CONT_KEYWORDS)
_LEADING_CONT_RE = re.compile(r"^(%s)\s*[:：]" % _CONT_KEYWORDS)
# 用户手工编辑 TXT 时常用「（留空）」占位表示该层级暂无考向；它不是题目
# 示例，必须在解析阶段忽略，否则会被拼到上一个考向的 example 里。
_EMPTY_MARK_RE = re.compile(r"^[（(]?\s*(?:留空|空|暂无|无)\s*[）)]?$")
_BOOK_RE = re.compile(
    r"^(必修[一二三四五六七八九十]|选修[一二三四五]|选择性必修[一二三四五])"
)

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_to_int(text: str):
    """把“一”~“二十”之类的中文数字转成 int（无法识别返回 None）。"""

    text = str(text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    total = 0
    for ch in text:
        if ch not in _CN_DIGITS:
            return None
        total = total * 10 + _CN_DIGITS[ch]
    return total


def _cn_chapter_repl(match) -> str:
    value = _cn_to_int(match.group(1))
    return f"{value}." if value is not None else match.group(0)


def normalize_chapter_key(text: str) -> str:
    """把“第一章 集合与常用逻辑用语”与“1. 集合与常用逻辑用语”归一到同一比较键。"""

    raw = str(text or "").strip()
    raw = re.sub(r"第\s*([一二三四五六七八九十]+|\d+)\s*章", _cn_chapter_repl, raw)
    raw = re.sub(r"第\s*([一二三四五六七八九十]+|\d+)\s*部分", _cn_chapter_repl, raw)
    raw = raw.replace("．", ".").replace("、", ".")
    return re.sub(r"[\s.]+", "", raw)


def ensure_tree_dir() -> str:
    os.makedirs(KAOXIANG_DIR, exist_ok=True)
    return str(KAOXIANG_DIR)


# --------------------------------------------------------------------------
# 解析：TXT -> 结构化考向树
# --------------------------------------------------------------------------
def _split_prefix(line: str) -> tuple[str, str]:
    index = 0
    while index < len(line) and (
        line[index] in _FILL_CHARS or line[index] in _LINE_CHARS or line[index] in " \t"
    ):
        index += 1
    return line[:index], line[index:]


def _tokenize(text: str) -> tuple[list, str]:
    """把原始文本拆成 ``(depth, content)`` 列表，自动识别树线/缩进两种风格。"""

    stripped = []
    for line in str(text or "").splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        prefix, content = _split_prefix(line)
        content = content.strip()
        if content:
            stripped.append((prefix, content))

    style = "indent"
    for prefix, content in stripped:
        if content.startswith("考向") and any(ch in _BRANCH_CHARS for ch in prefix):
            style = "tree"
            break

    tokens = []
    for prefix, content in stripped:
        if style == "tree":
            depth = sum(1 for ch in prefix if ch in _FILL_CHARS)
            depth = max(depth, 1)
        else:
            spaces = len(prefix) - len(prefix.lstrip(" \t"))
            depth = spaces // 2 + 1
        tokens.append((depth, content))
    return tokens, style


def parse_tree_text(text: str) -> dict:
    """解析考向树文本为 ``{title, sections:[{name, group, level_titles, topics}]}``。"""

    tokens, _style = _tokenize(text)
    title = ""
    sections: list[dict] = []
    current: dict | None = None
    current_level = ""
    current_topic: dict | None = None
    pending_group = ""
    pending_depth = 0

    for index, (depth, content) in enumerate(tokens):
        if not title:
            title = content
            continue

        section_match = _SECTION_RE.match(content)
        level_match = _LEVEL_RE.match(content)
        kaoxiang_match = _KAOXIANG_RE.match(content)

        if section_match and not level_match and not kaoxiang_match:
            name = (section_match.group(1) + " " + (section_match.group(2) or "")).strip()
            nxt = tokens[index + 1] if index + 1 < len(tokens) else None
            is_group = False
            if nxt and nxt[0] > depth:
                nxt_content = nxt[1]
                if (
                    _SECTION_RE.match(nxt_content)
                    and not _LEVEL_RE.match(nxt_content)
                    and not _KAOXIANG_RE.match(nxt_content)
                ):
                    is_group = True
            if is_group:
                pending_group = name
                pending_depth = depth
                continue
            if pending_group and depth <= pending_depth:
                pending_group = ""
            current = {"name": name, "topics": [], "level_titles": {}}
            if pending_group:
                current["group"] = pending_group
            sections.append(current)
            current_level = ""
            current_topic = None
            continue

        if level_match:
            current_level = "L" + level_match.group(1)
            # 进入新层级后，后续的「例：/说明：」等续行不应再挂到上一层的
            # 最后一个考向上。
            current_topic = None
            label = (level_match.group(2) or "").strip()
            if current is not None and label:
                current["level_titles"][current_level] = label
            continue

        if kaoxiang_match:
            if current is None:
                current = {"name": "未命名节", "topics": [], "level_titles": {}}
                sections.append(current)
            order = kaoxiang_match.group(1)
            name = (kaoxiang_match.group(2) or "").strip()
            if not name:
                continue
            current_topic = {
                "no": int(order) if order and order.isdigit() else len(current["topics"]) + 1,
                "level": current_level or "L1",
                "name": name,
                "example": "",
            }
            current["topics"].append(current_topic)
            continue

        if _EMPTY_MARK_RE.match(content):
            continue

        cont_match = _CONTINUATION_RE.match(content)
        if cont_match and current_topic is not None:
            piece = (cont_match.group(1) + "：" + (cont_match.group(2) or "")).strip(" ：")
            if piece:
                current_topic["example"] = (current_topic["example"] + " " + piece).strip()
            continue

        if current_topic is not None and content:
            current_topic["example"] = (current_topic["example"] + " " + content).strip()

    for sec in sections:
        for order, topic in enumerate(sec.get("topics", []), start=1):
            topic["no"] = order
    return {"title": title, "sections": [s for s in sections if s.get("name")]}


def parse_tree_file(path: str) -> dict:
    """解析单个 TXT 源文件，并从文件名推断册次与章标题。"""

    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    tree = parse_tree_text(text)
    filename = os.path.basename(path)
    stem = os.path.splitext(filename)[0].strip()
    book_match = _BOOK_RE.match(stem)
    if book_match:
        compulsory = book_match.group(1)
        remainder = stem[len(compulsory):].strip()
    else:
        parts = stem.split()
        compulsory = parts[0] if parts else ""
        remainder = stem[len(compulsory):].strip()
    tree.update(
        {
            "path": str(path),
            "filename": filename,
            "compulsory": compulsory,
            "chapter_title": remainder or tree.get("title") or stem,
        }
    )
    return tree


def load_sources() -> list[dict]:
    """读取 ``考向树/`` 下所有 TXT（跳过以 _ 开头的说明文件）。"""

    ensure_tree_dir()
    sources = []
    try:
        names = sorted(os.listdir(KAOXIANG_DIR))
    except OSError:
        return sources
    for name in names:
        if not name.lower().endswith(".txt") or name.startswith("_"):
            continue
        path = os.path.join(KAOXIANG_DIR, name)
        try:
            sources.append(parse_tree_file(path))
        except (OSError, UnicodeDecodeError) as exc:
            print(f"[KaoxiangTree] 跳过无法解析的考向树文件 {name}: {exc}")
    return sources


# --------------------------------------------------------------------------
# 渲染：结构化考向树 -> TXT
# --------------------------------------------------------------------------
def _render_nodes(nodes, prefix="", style="tree"):
    """渲染节点树；节点为 ``(label, children, sep_after_children)``。"""

    lines = []
    for index, node in enumerate(nodes):
        label, children, sep = node
        last = index == len(nodes) - 1
        if style == "tree":
            lines.append(prefix + ("└─ " if last else "├─ ") + label)
            child_prefix = prefix + ("   " if last else "│  ")
        elif not prefix:
            lines.append(prefix + ("└─ " if last else "├─ ") + label)
            child_prefix = "  "
        else:
            lines.append(prefix + label)
            child_prefix = prefix + "  "
        if children:
            lines.extend(_render_nodes(children, child_prefix, style))
            # 树线风格在块之间补一条竖线（与既有范本一致）；纯缩进风格不加空行
            if sep and not last and style == "tree":
                lines.append(child_prefix.rstrip())
    return lines


def _split_example(example: str) -> list:
    """把“方法：… 例：…”这类合并的示例拆回多行，保持 TXT 原有观感。"""

    text = str(example or "").strip()
    if not text:
        return []
    parts = re.split(r"\s+(?=(?:%s)：)" % _CONT_KEYWORDS, text)
    return [p.strip() for p in parts if p and p.strip()]


def _section_children(sec: dict):
    """按 L1~L5 归组渲染某个节下的考向（五个层级都输出，方便直接补考向）。"""

    titles = dict(DEFAULT_LEVEL_TITLES)
    stored = sec.get("level_titles") if isinstance(sec.get("level_titles"), dict) else {}
    for key, value in (stored or {}).items():
        if str(value or "").strip():
            titles[str(key)] = str(value).strip()

    buckets: dict[str, list] = {}
    for topic in sec.get("topics", []) or []:
        if not isinstance(topic, dict):
            continue
        level = str(topic.get("level") or "").strip().upper()
        if level not in LEVELS:
            level = "L1"
        buckets.setdefault(level, []).append(topic)

    nodes = []
    for level in LEVELS:
        suffix = titles.get(level, "")
        label = f"{level} {suffix}".strip() if suffix else level
        children = []
        for topic in buckets.get(level, []):
            name = str(topic.get("name") or "").strip()
            if not name:
                continue
            try:
                number = int(topic.get("no") or 0)
            except (TypeError, ValueError):
                number = 0
            prefix_no = f"{number}：" if number > 0 else ""
            example_lines = [
                line if _LEADING_CONT_RE.match(line) else "例：" + line
                for line in _split_example(topic.get("example"))
            ]
            children.append(
                (
                    f"考向{prefix_no}{name}",
                    [(line, [], False) for line in example_lines],
                    False,
                )
            )
        nodes.append((label, children, True))
    return nodes


def render_tree_text(tree: dict, style: str = "tree") -> str:
    """把考向树渲染成 TXT（默认与第一章范本完全一致的树线版式）。"""

    title = str(tree.get("title") or tree.get("chapter_title") or "").strip()
    lines = [title] if title else []

    grouped: list[tuple[str, list]] = []
    for sec in tree.get("sections", []) or []:
        if not isinstance(sec, dict):
            continue
        group = str(sec.get("group") or "").strip()
        if group and grouped and grouped[-1][0] == group:
            grouped[-1][1].append(sec)
        else:
            grouped.append((group, [sec]))

    section_nodes = []
    for group, members in grouped:
        children = [
            (str(sec.get("name") or "").strip(), _section_children(sec), True)
            for sec in members
        ]
        if group:
            section_nodes.append((group, children, True))
        else:
            section_nodes.extend(children)

    if section_nodes:
        lines.append("│")
        lines.extend(_render_nodes(section_nodes, "", style))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# 与大纲（curriculum）互转
# --------------------------------------------------------------------------
def match_chapter_key(curriculum: dict, compulsory: str, chapter_title: str) -> str:
    """在大纲里找到该 TXT 对应的章键（兼容“第一章”与“1.”两种写法）。"""

    books = (curriculum or {}).get(compulsory, {})
    if not isinstance(books, dict):
        return ""
    target = normalize_chapter_key(chapter_title)
    if not target:
        return ""
    for key in books:
        if normalize_chapter_key(key) == target:
            return key
    core = re.sub(r"[\s]+", "", re.sub(r"^[\d.．\s]+", "", str(chapter_title)))
    for key in books:
        key_core = re.sub(r"[\s]+", "", re.sub(r"^[\d.．\s]+", "", str(key)))
        if core and (core in key_core or key_core in core):
            return key
    return ""


def _old_topic_ids(sections) -> dict:
    mapping = {}
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        for topic in sec.get("topics", []) or []:
            if not isinstance(topic, dict):
                continue
            name = str(topic.get("name") or "").strip()
            tid = str(topic.get("id") or "").strip()
            if name and tid and name not in mapping:
                mapping[name] = tid
    return mapping


def build_sections(compulsory: str, chapter_key: str, parsed: dict, old_sections):
    """把解析结果转成大纲的节结构，尽量复用已有考向 id。"""

    old_ids = _old_topic_ids(old_sections)
    sections: list[dict] = []
    added, reused = 0, 0
    for parsed_sec in parsed.get("sections", []):
        sec_name = str(parsed_sec.get("name") or "").strip()
        topics = []
        for order, item in enumerate(parsed_sec.get("topics", []), start=1):
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            tid = old_ids.get(name)
            if tid:
                reused += 1
            else:
                tid = topic_id_for(compulsory, chapter_key, sec_name, name)
                added += 1
            topic = {
                "id": tid,
                "name": name,
                "level": str(item.get("level") or "L1").upper(),
                "no": order,
            }
            example = str(item.get("example") or "").strip()
            if example:
                topic["example"] = example
            topics.append(topic)
        if not topics or not sec_name:
            continue
        # 规范顺序：按 L1→L5 归组、组内保持 TXT 顺序，保证「渲染→再解析」收敛
        topics.sort(key=lambda t: (LEVELS.index(t["level"]), t["no"]))
        for order, topic in enumerate(topics, start=1):
            topic["no"] = order
        sec = {"name": sec_name, "topics": topics}
        group = str(parsed_sec.get("group") or "").strip()
        if group:
            sec["group"] = group
        titles = {
            key: str(value).strip()
            for key, value in (parsed_sec.get("level_titles") or {}).items()
            if str(value or "").strip()
        }
        if titles:
            sec["level_titles"] = titles
        sections.append(sec)
    return sections, {"added": added, "reused": reused}


def _level_rank(level: str) -> int:
    level = str(level or "").strip().upper()
    return LEVELS.index(level) if level in LEVELS else len(LEVELS)


def _canonical_signature(sections) -> list:
    """按 L1→L5 归组后的规范签名。

    TXT 与大纲里考向的书写顺序可能不同（网页里新加的考向往往排在末尾，
    渲染回 TXT 时会归到对应层级），比对时统一按层级排序，才能判定为已同步。
    """

    canonical = []
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        topics = [t for t in sec.get("topics", []) or [] if isinstance(t, dict)]
        if not topics:
            # 空节不落大纲（解析器也会丢掉），比对时一并忽略
            continue
        topics = sorted(topics, key=lambda t: (_level_rank(t.get("level")), _topic_no(t)))
        canonical.append([str(sec.get("name") or "").strip()] + [_topic_entry(t) for t in topics])
    return canonical


def _topic_no(topic: dict) -> int:
    try:
        return int(topic.get("no") or 0)
    except (TypeError, ValueError):
        return 0


def _topic_entry(topic: dict) -> tuple:
    return (
        str(topic.get("level") or "").strip().upper(),
        str(topic.get("name") or "").strip(),
        str(topic.get("example") or "").strip(),
    )


def _signature_from_sections(sections) -> list:
    """用于比对“两棵树是否等价”的规范签名（忽略 id 与顺序无关的字段）。"""

    signature = []
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        entry = [str(sec.get("name") or "").strip()]
        for topic in sec.get("topics", []) or []:
            if not isinstance(topic, dict):
                entry.append(("", str(topic), ""))
                continue
            entry.append(_topic_entry(topic))
        signature.append(entry)
    return signature


def _signature_from_parsed(parsed: dict) -> list:
    return _signature_from_sections(parsed.get("sections", []))


def protected_topic_ids() -> set:
    """已被题目引用的考向 id：TXT 里删掉的考向若仍被引用则保留，避免悬空。"""

    ids = set()
    try:
        from mathbank.database import QuestionTopic, SessionLocal

        db = SessionLocal()
        try:
            for (topic_id,) in db.query(QuestionTopic.topic_id).distinct():
                if topic_id:
                    ids.add(str(topic_id))
        finally:
            db.close()
    except Exception as exc:  # 读不到库就退化为“不保护”，不影响同步本身
        print(f"[KaoxiangTree] 读取题目考点引用失败 (type={type(exc).__name__}): {exc}")
    return ids


def _carry_over_referenced_topics(old_sections, new_sections, protected) -> int:
    """TXT 里已删除、但仍被题目引用的考向原样保留（附在对应节末尾）。"""

    if not protected:
        return 0
    kept_by_section: dict[str, list] = {}
    for sec in old_sections or []:
        if not isinstance(sec, dict):
            continue
        for topic in sec.get("topics", []) or []:
            if isinstance(topic, dict) and str(topic.get("id") or "") in protected:
                kept_by_section.setdefault(str(sec.get("name") or ""), []).append(topic)
    if not kept_by_section:
        return 0
    carried = 0
    for sec in new_sections:
        sec_name = str(sec.get("name") or "")
        existing = {
            str(t.get("id") or "")
            for t in sec.get("topics", [])
            if isinstance(t, dict)
        }
        for topic in kept_by_section.get(sec_name, []):
            if str(topic.get("id") or "") in existing:
                continue
            sec["topics"].append(dict(topic))
            carried += 1
    return carried


# --------------------------------------------------------------------------
# 同步入口
# --------------------------------------------------------------------------
def _count_topics(sections) -> int:
    return sum(
        len(sec.get("topics", []) or []) for sec in sections or [] if isinstance(sec, dict)
    )


def sync_from_txt(curriculum: dict, sources=None, protected=None) -> dict:
    """TXT → 大纲。以 TXT 为准重写受管章（原地修改 curriculum）。

    ``protected`` 是被题目引用的考向 id 集合；TXT 里删掉但仍在引用的考向会被
    保留下来，避免出现没有考向指向的题目。
    """

    sources = load_sources() if sources is None else sources
    if protected is None:
        protected = protected_topic_ids()
    report: dict = {"changed": False, "files": [], "carried_over": 0}
    for source in sources:
        compulsory = source.get("compulsory") or ""
        chapter_key = match_chapter_key(
            curriculum, compulsory, source.get("chapter_title", "")
        )
        entry = {
            "filename": source.get("filename"),
            "compulsory": compulsory,
            "chapter": chapter_key,
            "matched": bool(chapter_key),
            "action": "skipped",
            "before": 0,
            "after": 0,
            "added": 0,
            "reused": 0,
        }
        if not chapter_key:
            entry["action"] = "unmatched"
            report["files"].append(entry)
            continue

        chapters = curriculum.setdefault(compulsory, {})
        old_sections = chapters.get(chapter_key, []) or []
        entry["before"] = _count_topics(old_sections)
        if _canonical_signature(old_sections) == _canonical_signature(source['sections']):
            entry["after"] = entry["before"]
            entry["action"] = "in-sync"
            report["files"].append(entry)
            continue

        new_sections, stats = build_sections(compulsory, chapter_key, source, old_sections)
        report["carried_over"] += _carry_over_referenced_topics(
            old_sections, new_sections, protected
        )
        chapters[chapter_key] = new_sections
        entry.update(
            {
                "action": "pulled",
                "after": _count_topics(new_sections),
                "added": stats["added"],
                "reused": stats["reused"],
            }
        )
        report["changed"] = True
        report["files"].append(entry)
    return report


def _txt_writes_allowed() -> bool:
    """测试环境默认禁止改写项目里真实的考向树。

    跑 pytest 时 IS_TESTING 为真，此时只有把 KAOXIANG_DIR 指向临时目录的
    用例（显式 monkeypatch）才允许回写，避免其它用例的“保存大纲”把用户的
    TXT 覆盖掉。
    """

    if not IS_TESTING:
        return True
    try:
        return os.path.realpath(str(KAOXIANG_DIR)) != os.path.realpath(
            str(DEFAULT_KAOXIANG_DIR)
        )
    except OSError:
        return False


def _same_tree(left: str, right: str) -> bool:
    """忽略空行与尾随空格，比较两棵文本树是否等价。"""

    def flat(text: str):
        return [ln.rstrip() for ln in str(text or "").splitlines() if ln.strip()]

    return flat(left) == flat(right)


def sync_to_txt(curriculum: dict, sources=None) -> dict:
    """大纲 → TXT。把受管章重新渲染回 ``考向树/``（内容不变则不落盘）。"""

    sources = load_sources() if sources is None else sources
    report: dict = {"changed": False, "files": []}
    writable = _txt_writes_allowed()
    for source in sources:
        compulsory = source.get("compulsory") or ""
        chapter_key = match_chapter_key(
            curriculum, compulsory, source.get("chapter_title", "")
        )
        entry = {
            "filename": source.get("filename"),
            "chapter": chapter_key,
            "matched": bool(chapter_key),
            "action": "skipped",
            "topics": 0,
        }
        if not chapter_key:
            entry["action"] = "unmatched"
            report["files"].append(entry)
            continue
        if not writable:
            entry["action"] = "read-only"
            report["files"].append(entry)
            continue

        sections = (curriculum.get(compulsory, {}) or {}).get(chapter_key, []) or []
        entry["topics"] = _count_topics(sections)
        try:
            with open(source["path"], encoding="utf-8") as handle:
                original = handle.read()
        except OSError as exc:
            entry["action"] = "error"
            entry["error"] = str(exc)
            report["files"].append(entry)
            continue
        # 内容一致就不动文件，保留用户原有的排版习惯
        if _canonical_signature(parse_tree_text(original)["sections"]) == _canonical_signature(
            sections
        ):
            entry["action"] = "in-sync"
            report["files"].append(entry)
            continue
        rendered = render_tree_text(
            {"title": source.get("chapter_title") or "", "sections": sections},
            _tokenize(original)[1],
        )
        if _same_tree(original, rendered):
            entry["action"] = "in-sync"
            report["files"].append(entry)
            continue
        write_private_text_atomic(source["path"], rendered)
        entry["action"] = "pushed"
        report["changed"] = True
        report["files"].append(entry)
    return report


def status(curriculum: dict | None = None) -> dict:
    """返回每个受管 TXT 的同步状态，用于设置页展示。"""

    from mathbank import metadata as metadata_module  # 延迟导入，避免循环依赖

    if curriculum is None:
        curriculum = _load_metadata_cache().get("curriculum", {})
    sources = load_sources()
    files = []
    state = _read_state()
    for source in sources:
        compulsory = source.get("compulsory") or ""
        key = match_chapter_key(curriculum, compulsory, source.get("chapter_title", ""))
        sections = ((curriculum.get(compulsory, {}) or {}).get(key, []) or [])
        files.append(
            {
                "filename": source.get("filename"),
                "path": source.get("path"),
                "compulsory": compulsory,
                "chapter": key,
                "matched": bool(key),
                "in_sync": bool(
                    key
                    and _canonical_signature(sections)
                    == _canonical_signature(source["sections"])
                ),
                "txt_topics": sum(
                    len(s.get("topics", []) or []) for s in source.get("sections", [])
                ),
                "web_topics": _count_topics(sections),
                "sections": len(source.get("sections", [])),
                "synced_at": (state.get("files", {}) or {}).get(
                    source.get("filename"), {}
                ).get("synced_at", ""),
            }
        )
    return {"dir": str(KAOXIANG_DIR), "files": files, "count": len(files)}


def _load_metadata_cache():
    """取得活跃 metadata 字典；命令行/测试环境下按需从磁盘加载。"""

    from mathbank import metadata as metadata_module

    cache = metadata_module.get_metadata_cache()
    if cache.get("curriculum"):
        return cache
    try:
        with open(metadata_module.METADATA_FILE, encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, ValueError):
        return cache
    if isinstance(loaded, dict) and loaded.get("curriculum"):
        metadata_module.set_metadata_cache(loaded)
        return loaded
    return cache


def _read_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        write_private_text_atomic(
            STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2)
        )
    except OSError as exc:
        print(f"[KaoxiangTree] 同步状态写入失败: {exc}")


def _hash(text: str) -> str:
    return hashlib.sha1(str(text or "").encode("utf-8")).hexdigest()


def _record_state(sources) -> None:
    state = _read_state()
    files_state = state.setdefault("files", {})
    for source in sources:
        try:
            with open(source["path"], encoding="utf-8") as handle:
                files_state[source["filename"]] = {
                    "hash": _hash(handle.read()),
                    "synced_at": datetime.now().isoformat(timespec="seconds"),
                }
        except OSError:
            continue
    _write_state(state)


def push_to_txt(curriculum: dict | None = None) -> dict:
    """Web → TXT：把当前大纲回写进 ``考向树/``（供保存与自动建档调用）。"""

    if curriculum is None:
        curriculum = _load_metadata_cache().get("curriculum", {})
    if not curriculum:
        return {"changed": False, "files": []}
    sources = load_sources()
    if not sources:
        return {"changed": False, "files": []}
    try:
        report = sync_to_txt(curriculum, sources)
    except Exception as exc:  # 回写失败绝不能影响主流程
        print(f"[KaoxiangTree] 回写考向树失败 (type={type(exc).__name__}): {exc}")
        return {"changed": False, "files": [], "error": str(exc)}
    if report["changed"]:
        _record_state(sources)
    return report


def pull_from_txt(write_preset: bool = True) -> dict:
    """TXT → Web：以 TXT 为准刷新活跃大纲（默认同步到预设 A.json）。"""

    from mathbank import metadata as metadata_module

    ensure_tree_dir()
    sources = load_sources()
    if not sources:
        return {"changed": False, "files": []}

    cache = _load_metadata_cache()
    curriculum = cache.setdefault("curriculum", {})
    protected = protected_topic_ids()
    report = sync_from_txt(curriculum, sources, protected)
    if not report["changed"]:
        return report

    metadata_module.set_metadata_cache(cache)
    from mathbank.curriculums import set_current_curriculum

    set_current_curriculum(curriculum)
    write_private_text_atomic(
        metadata_module.METADATA_FILE, json.dumps(cache, ensure_ascii=False, indent=2)
    )
    metadata_module._record_metadata_mtime()

    # 预设 A.json 是随包发布的种子，测试环境绝不改动它
    if write_preset and not IS_TESTING and os.path.exists(PRESET_PATH):
        try:
            with open(PRESET_PATH, encoding="utf-8") as handle:
                preset = json.load(handle)
            preset_report = sync_from_txt(preset, sources, protected)
            if preset_report["changed"]:
                write_private_text_atomic(
                    PRESET_PATH, json.dumps(preset, ensure_ascii=False, indent=2)
                )
            report["preset"] = preset_report
        except (OSError, ValueError) as exc:
            print(f"[KaoxiangTree] 预设大纲同步失败: {exc}")

    _record_state(sources)
    return report


_MTIME_CACHE: dict[str, int] = {}


def _sources_changed(sources) -> bool:
    """以 mtime（纳秒）判断是否有 TXT 被外部改动过。"""

    changed = False
    for source in sources:
        try:
            mtime = os.stat(source["path"]).st_mtime_ns
        except OSError:
            continue
        if _MTIME_CACHE.get(source["path"]) != mtime:
            _MTIME_CACHE[source["path"]] = mtime
            changed = True
    return changed


def auto_pull_if_needed(force: bool = False) -> dict:
    """低成本的自动拉取：只有 TXT 真的被动过（或首次/强制）才比对内容。

    供启动与常用接口调用；未发生变化时只做一次 stat，开销可忽略。
    """

    sources = load_sources()
    if not sources:
        return {"changed": False, "files": []}
    if not force and not _sources_changed(sources):
        return {"changed": False, "files": [], "skipped": True}
    try:
        return pull_from_txt()
    except Exception as exc:  # 拉取失败不能让接口挂掉
        print(f"[KaoxiangTree] 自动拉取考向树失败 (type={type(exc).__name__}): {exc}")
        return {"changed": False, "files": [], "error": str(exc)}


def _cli(argv: list[str]) -> int:
    """命令行入口：``python -m mathbank.kaoxiang_tree [status|pull|push]``。"""

    command = argv[1] if len(argv) > 1 else "status"
    if command == "status":
        info = status()
        print(f"考向树目录：{info['dir']}（{info['count']} 个文件）")
        for item in info["files"]:
            flag = "已同步" if item["in_sync"] else ("未匹配" if not item["matched"] else "待同步")
            print(
                f"  [{flag}] {item['filename']} -> {item['chapter'] or '—'} "
                f"(TXT {item['txt_topics']} 考向 / 网页 {item['web_topics']} 考向)"
            )
        return 0
    if command == "pull":
        report = pull_from_txt()
    elif command == "push":
        report = push_to_txt()
    else:
        print("用法：python -m mathbank.kaoxiang_tree [status|pull|push]")
        return 2
    print(f"方向：{command}  是否变更：{report.get('changed')}")
    for item in report.get("files", []):
        print(
            f"  {item['action']}  {item['filename']}  "
            f"{item.get('before', item.get('topics', ''))} -> {item.get('after', '')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(__import__("sys").argv))
