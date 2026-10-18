# -*- coding: utf-8 -*-
"""考向树 TXT ↔ Web 大纲的解析与渲染工具（TXT 同步链路已下线）。

数据源唯一化
------------
考向树的唯一事实来源是 JSON：活跃大纲 `data_backup/custom_metadata.json`
与教材预设 `mathbank/resources/curriculums/{A,B,S,G,H}.json`。项目不再读写
``考向树/``，也不会因为外部文本文件而改写任何 JSON —— 历史上启动时的
``auto_pull_if_needed(force=True)`` 会在 TXT 与 A.json 不一致时直接覆盖预设，
这个隐患随同步链路一起删除。历史 TXT 已归档，不在项目目录内。

保留能力
--------
* ``parse_tree_text`` / ``render_tree_text``：把 TXT 文本与结构化考向树互转，
  供 ``scripts/check_kaoxiang_tree.py`` 这类只读合规检查复用；
* ``status``：只读比对某个 TXT 目录与大纲，用于排查历史归档。

考向的 ``level`` / ``no`` / ``example`` 以及节上的分组、层级标题都会原样
保留，因此反复解析不会丢失信息。"""

from __future__ import annotations

import json
import os
import re

from mathbank.paths import project_path
from mathbank.topics_helper import topic_id_for


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
# 「序号+冒号」整段可选：`考向3：某某` 与用户手打的裸行 `考向某某` 都要认。
# 旧写法强制冒号，裸行匹配不上就落到「续行」分支，被拼进上一个考向的
# example —— 手写进 TXT 的考向会静默消失。名字用 (.+?) 而非 (.*)，
# 光杆 `考向` / `考向 ` 仍判为非考向，不会被当成空名条目。
_KAOXIANG_RE = re.compile(r"^考向\s*(?:(\d+)\s*[:：]\s*)?(.+?)\s*$")
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
            name = (kaoxiang_match.group(2) or "").strip().lstrip("：:").strip()
            if not name:
                continue
            # 纯数字名一律丢弃：裸行形态放宽后 `考向3` 会被读成名叫「3」的
            # 考向，而它其实是用户漏了冒号。宁可忽略这一行，也不造出幽灵考点。
            if name.isdigit():
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


def _topics_with_render_defaults(sec: dict) -> list:
    """渲染前补齐 `no`/`level`，保证每个考向都输出成可解析的行。

    手动「新增考向」与 AI 自动建档往大纲里只写 `{id, name}`。缺 `no` 时
    `_section_children` 会输出不带序号的裸行 `考向某某`，而 `parse_tree_text`
    认不出这种形态，把它当上一行的续行拼进别人的 `example` —— 于是大纲写入
    返回 200、TXT 也回写了，下一次以 TXT 为准的拉取却把这条考向彻底吞掉。

    两个字段必须一起补：只补 `no` 的话该条目仍无 `level`，渲染按 L1 归组、
    拉回后签名与大纲不等，`sync_to_txt` 每次保存都会重写一遍 TXT 文件。

    只在渲染用的副本上补，绝不写回传入对象：大纲里的真实条目保持原样。
    """

    topics = [t for t in (sec.get("topics") or []) if isinstance(t, dict)]
    prepared = []
    for topic in topics:
        level = str(topic.get("level") or "").strip().upper()
        if level in LEVELS and _topic_no(topic) > 0:
            prepared.append(topic)
            continue
        patched = dict(topic)
        if level not in LEVELS:
            patched["level"] = "L1"
        if _topic_no(patched) <= 0:
            patched["no"] = max([_topic_no(t) for t in topics] or [0]) + 1
        prepared.append(patched)
    return prepared


def pull_stable_sections(sections: list) -> list:
    """把大纲校正到「存进去就与 TXT 逐字节相等」的编号形态。

    大纲里的 `no` 只有等于「L1→L5 归组后的连续序号」时，才和 `build_sections`
    从 TXT 拉回的结果一致。新追加的考向若直接拿列表末尾号（比如 6），首次渲染
    成 `考向6：某某`，TXT 拉回后它却是 L1 桶里的第 3 条，第二次渲染变成
    `考向3：某某`。`_canonical_signature` 只看 `(level, name, example)`、不看
    `no`，所以签名比对查不出这种错位——内容明明没变，用户每存一次大纲却被多
    写一遍 TXT 文件。这里做一次纯内存 render→parse 把编号推到稳定点；
    `build_sections` 按名字复用旧 id，因此不会凭空造出新考点。

    只在大纲与 TXT 确实不一致时才被调用（`sync_to_txt` 已先比过签名）。
    """

    # 必须带 title：parse_tree_text 把第一行当章标题吃掉，缺了它整棵树会错位。
    rendered = render_tree_text({"title": "编号校正", "sections": sections})
    stable, _ = build_sections("", "", parse_tree_text(rendered), sections)
    return stable


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
        children = []
        for sec in members:
            prepared = dict(sec)
            prepared["topics"] = _topics_with_render_defaults(sec)
            children.append((str(sec.get("name") or "").strip(), _section_children(prepared), True))
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

# --------------------------------------------------------------------------
# 只读诊断：TXT 与大纲的比对状态（TXT 同步链路已下线，仅供排查历史归档）
# --------------------------------------------------------------------------
def _count_topics(sections) -> int:
    return sum(
        len(sec.get("topics", []) or []) for sec in sections or [] if isinstance(sec, dict)
    )


def status(curriculum: dict | None = None) -> dict:
    """只读比对：列出 ``考向树/`` 下每个 TXT 与大纲的对应关系。

    TXT 不再是数据源，本函数只为排查历史归档而保留（无产品入口）。
    """

    if curriculum is None:
        curriculum = _load_metadata_cache().get("curriculum", {})
    sources = load_sources()
    files = []
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
            }
        )
    return {"dir": str(KAOXIANG_DIR), "files": files, "count": len(files)}
