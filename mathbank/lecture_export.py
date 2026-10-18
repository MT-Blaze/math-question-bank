"""讲义导出：把 preview 数据排成 exam-zh LaTeX，交给组卷引擎编译 PDF。

导言区复用 ``paper_helper.build_exam_preamble``、题目正文复用
``clean_content_for_latex``，保证讲义与试卷的公式、图片、``choices``
环境同一套排版。知识块（``lecture_notes.content``）存的是 HTML 或
``@mindmap`` JSON，本模块只做保守转换：转不了的部分一律记进返回值里的
degradations 清单，由调用方如实告知用户，严禁静默丢弃内容。
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterator

from mathbank.asset_security import AssetSecurityError, resolve_upload_asset
from mathbank.latex_normalize import _convert_one_table
from mathbank.paper_helper import (
    _apply_option_columns,
    clean_content_for_latex,
    extract_choice_answer_letter,
)

MIND_PREFIX = "@mindmap"

# 排版口径（待办 #8 P1 拍板）：讲义导出并存两套 LaTeX 类。默认仍是与组卷同款的
# exam-zh 试卷体；elegantbook 书本体由前端「排版口径」下拉选择，路由层负责校验。
EXPORT_FMT_EXAM = "exam-zh"
EXPORT_FMT_BOOK = "elegantbook"

# 书本体垫片：唯一来源是 resources/mblecture.sty（编译冒烟测试同样钉它）。
# 导出时把垫片正文直接内联进导言区：TEX 导出的 .tex 自解释、换台机器也能编译；
# 编译临时目录不用多塞 .sty 文件，compile_tex_to_pdf 管线一行不动。
_MB_SHIM_PATH = Path(__file__).resolve().parent / "resources" / "mblecture.sty"
# \NeedsTeXFormat / \ProvidesPackage / \endinput 只允许出现在真正的包文件里
_MB_SHIM_SKIP = (r"\NeedsTeXFormat", r"\ProvidesPackage", r"\endinput")
_MB_SHIM_LINES: list = []

# 书本体列数估算的版心参数（ElegantBook 12pt + a4 + 默认 normal device）：
# 实测 \the\textwidth=483.7pt，12pt 下折约 40.3em；垫片里 columnsep=1em、
# enumerate leftmargin=1.7em+labelsep=0.2em，再留余量合计 2em 作标号与取整。
_BOOK_TEXTW_EM = 40.3
_BOOK_COLSEP_EM = 1.0
_BOOK_LIST_INSET_EM = 2.0

# 知识块正文的图片引用三形态（collect_lecture_images 扫 notes 用）
_NOTE_IMG_SRC_RE = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)
_NOTE_MARKDOWN_IMG_RE = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)")
_NOTE_INCLUDEGRAPHICS_RE = re.compile(
    r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")

# 数学定界符与 mathbank.math_markdown 保持在同一集合上
_MATH_DELIMITERS = (("$$", "$$"), (r"\[", r"\]"), (r"\(", r"\)"), ("$", "$"))

_LATEX_ESCAPES = (
    ("\\", r"\textbackslash{}"),
    ("&", r"\&"),
    ("%", r"\%"),
    ("#", r"\#"),
    ("_", r"\_"),
    ("$", r"\$"),
    ("~", r"\textasciitilde{}"),
    ("^", r"\textasciicircum{}"),
    ("<", r"\textless{}"),
    (">", r"\textgreater{}"),
)


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def split_math(text: str) -> Iterator[tuple[bool, str]]:
    """把文本切成 (是否数学区, 片段)。数学区内容原样保留，不做转义。"""
    value = text or ""
    copied = 0
    cursor = 0
    while cursor < len(value):
        if _is_escaped(value, cursor):
            cursor += 1
            continue
        for opening, closing in _MATH_DELIMITERS:
            if not value.startswith(opening, cursor):
                continue
            end = value.find(closing, cursor + len(opening))
            while end >= 0 and _is_escaped(value, end):
                end = value.find(closing, end + len(closing))
            if end < 0:
                continue
            end += len(closing)
            if copied < cursor:
                yield False, value[copied:cursor]
            yield True, value[cursor:end]
            copied = cursor = end
            break
        else:
            cursor += 1
    if copied < len(value):
        yield False, value[copied:]


def escape_text(text: str) -> str:
    r"""转义普通文本里的 LaTeX 特殊字符（数学区与花括号交给调用方保证配对）。

    故意不转义 ``{``/``}``：知识块正文里常见 ``A=\{x|x^2<4\}`` 这类已经写好
    的转义花括号，二次转义会把结构打散。
    """
    out: list[str] = []
    for is_math, chunk in split_math(text):
        if is_math:
            out.append(chunk)
            continue
        piece = chunk
        for char, replacement in _LATEX_ESCAPES:
            piece = piece.replace(char, replacement)
        out.append(piece)
    return "".join(out)


_LIST_ENV = {"ul": "itemize", "ol": "enumerate"}


class _BlockParser(HTMLParser):
    """知识块 HTML → LaTeX 片段。

    只认这批内容实际用到的标签（p/div/br/strong/b/em/i/span/ul/ol/li/table）；
    未知标签保留其中文字并记进 degradations。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.text: list[str] = []
        self.list_stack: list[str] = []
        self.inline_open = 0
        self.unknown: set[str] = set()
        self.tables: list[str] = []
        self._table_html: list[str] = []
        self._table_depth = 0

    def _flush_text(self) -> None:
        raw = "".join(self.text)
        self.text = []
        cleaned = re.sub(r"\s+", " ", raw).strip()
        if cleaned:
            self.parts.append(escape_text(cleaned))

    def _open_list_if_needed(self) -> None:
        if not self.list_stack:
            self.list_stack.append("ul")
            self.parts.append("\n\\begin{itemize}\n")

    def handle_starttag(self, tag, attrs) -> None:
        tag = tag.lower()
        if tag == "table":
            self._flush_text()
            self._table_depth += 1
            if self._table_depth == 1:
                self._table_html = ["<table>"]
                return
        if self._table_depth:
            self._table_html.append(self.get_starttag_text() or "")
            return
        if tag in ("strong", "b"):
            self._flush_text()
            self.parts.append(r"\textbf{")
            self.inline_open += 1
        elif tag in ("em", "i"):
            self._flush_text()
            self.parts.append(r"\emph{")
            self.inline_open += 1
        elif tag == "br":
            self._flush_text()
            self.parts.append(r"\\" + "\n")
        elif tag in _LIST_ENV:
            self._flush_text()
            self.list_stack.append(tag)
            self.parts.append("\n\\begin{%s}\n" % _LIST_ENV[tag])
        elif tag == "li":
            self._flush_text()
            self._open_list_if_needed()
            self.parts.append("\\item ")
        elif tag in ("p", "div"):
            self._flush_text()
            self.parts.append("\n\n")
        elif tag == "span":
            pass
        else:
            self.unknown.add(tag)

    def handle_endtag(self, tag) -> None:
        tag = tag.lower()
        if tag == "table" and self._table_depth:
            self._table_depth -= 1
            if self._table_depth == 0:
                self._table_html.append("</table>")
                self.tables.append("".join(self._table_html))
            return
        if self._table_depth:
            self._table_html.append("</%s>" % tag)
            return
        if tag in ("strong", "b", "em", "i"):
            self._flush_text()
            if self.inline_open:
                self.parts.append("}")
                self.inline_open -= 1
        elif tag == "li":
            self._flush_text()
            self.parts.append("\n")
        elif tag in _LIST_ENV:
            self._flush_text()
            if self.list_stack:
                popped = self.list_stack.pop()
                self.parts.append("\n\\end{%s}\n" % _LIST_ENV[popped])
        elif tag in ("p", "div"):
            self._flush_text()
            self.parts.append("\n\n")

    def handle_data(self, data) -> None:
        if self._table_depth:
            self._table_html.append(data)
            return
        self.text.append(data)

    def finish(self) -> tuple[list[str], list[str]]:
        self._flush_text()
        while self.list_stack:
            self.parts.append("\n\\end{%s}\n" % _LIST_ENV[self.list_stack.pop()])
        if self.inline_open:
            # HTML 片段缺 </strong> 之类闭合标签时补齐，否则整个 PDF 编译会被
            # 一个未闭合的 \textbf{ 带崩
            self.parts.append("}" * self.inline_open)
            self.inline_open = 0
        return self.parts, sorted(self.unknown)


def html_to_latex(html: str) -> tuple[str, list[str]]:
    r"""知识块 HTML → LaTeX，返回 (正文, 降级说明)。"""
    parser = _BlockParser()
    parser.feed(html or "")
    parser.close()
    parts, unknown = parser.finish()
    degradations: list[str] = []

    body = "".join(parts)

    for index, table_html in enumerate(parser.tables):
        converted = _convert_one_table(table_html)
        if converted:
            body += "\n\n%s\n\n" % converted
        else:
            # 复杂表格（rowspan/嵌套）转不动：退化成可读文字，绝不整块消失
            flattened = re.sub(r"<[^>]+>", " ", table_html)
            plain = escape_text(re.sub(r"\s+", " ", flattened).strip())
            body += "\n\n{\\small 表格未能按原结构排版：%s}\n\n" % plain
            degradations.append("第 %d 张表格结构过复杂，已按纯文本排版" % (index + 1))

    if unknown:
        degradations.append("HTML 标签 %s 未识别，仅保留其中文字" % "、".join(unknown))

    body = re.sub(r"[ \t]+\n", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip(), degradations


def mindmap_to_latex(data: dict) -> tuple[str, list[str]]:
    """思维导图知识梳理 → 层级列表。彩色连线与二维布局无法直译。"""
    lines: list[str] = []

    def emit_table(table: list) -> None:
        cols = max((len(row or []) for row in table), default=1) or 1
        lines.append("\\begin{tabular}{|%s|}" % ("l" * cols))
        lines.append("\\hline")
        for row in table:
            cells = [escape_text(str(cell or "")).replace("\n", r"\newline ") for cell in (row or [])]
            cells += [""] * (cols - len(cells))
            lines.append("  %s \\\\" % " & ".join(cells))
            lines.append("  \\hline")
        lines.append("\\end{tabular}")

    def emit_items(items: list, depth: int) -> None:
        pad = "  " * depth
        for item in items or []:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            if label:
                # 用户敲的真换行 → LaTeX 硬换行（escape_text 不动 \n）
                lines.append("%s\\item %s" % (pad, escape_text(label).replace("\n", r"\newline ")))
            if item.get("table"):
                emit_table(item["table"])
            children = item.get("items") or []
            if children:
                lines.append("%s\\begin{itemize}" % pad)
                emit_items(children, depth + 1)
                lines.append("%s\\end{itemize}" % pad)

    title = str((data or {}).get("title") or "").strip()
    if title:
        lines.append("\\textbf{%s}" % escape_text(title))
    branches = (data or {}).get("branches") or []
    if branches:
        lines.append("\\begin{itemize}")
        emit_items(branches, 1)
        lines.append("\\end{itemize}")
    return "\n".join(lines), ["思维导图的分支配色与连线布局未保留，已改为层级列表"]


def _lt_gt_in_math(text: str) -> str:
    r"""数学区（\(..\)、$$..$$、\[..\]）内的裸 < > 换成 \lt \gt。

    与前端正文管线同一口径：`0<a` 里的 `<a` 会被 HTML 解析器当成未知标签开
    （Python html.parser / 浏览器同款行为），整段公式就此丢失；\lt/\gt 则
    任何一侧都安全。已有的 ``\<`` ``\>`` 转义写法原样保留。"""
    def repl(match: "re.Match[str]") -> str:
        seg = match.group(0)
        for opening, closing in ((r"\(", r"\)"), ("$$", "$$"), (r"\[", r"\]")):
            if seg.startswith(opening):
                body = seg[len(opening):-len(closing)]
                body = body.replace(r"\<", "@@L@@").replace("<", r"\lt ").replace("@@L@", r"\<")
                body = body.replace(r"\>", "@@G@@").replace(">", r"\gt ").replace("@@G@", r"\>")
                return opening + body + closing
        return seg

    # 定界符逐个单独跑一遍 re.sub——把 \[ 写进字符类会触发 Python 的
    # nested-set FutureWarning，分开写既无告警又等价。
    out = re.sub(r"\\\(.*?\\\)", repl, text or "")
    out = re.sub(r"\$\$[\s\S]*?\$\$", repl, out)
    out = re.sub(r"\\\[([^\[\]]*?)\\\]", repl, out)
    return out


def _preescape_note_math(html: str) -> str:
    """修订稿/知识梳理 HTML 在进 html_to_latex **之前**先把数学段里的裸 < > 换掉。

    html.parser（与浏览器同款行为）会把 `0<a` 里的 `<a` 当未知标签开、一路吞到
    下一个 `>`，整段公式就此丢失；先转成 \\lt/\\gt 后解析器与 LaTeX 都安全。"""
    return _lt_gt_in_math(html)


def note_body_to_latex(content: str) -> tuple[str, list[str]]:
    """知识块正文分派：@mindmap 前缀走 JSON，其余按 HTML 处理。"""
    text = _preescape_note_math(str(content or ""))
    if text.startswith(MIND_PREFIX):
        try:
            data = json.loads(text[len(MIND_PREFIX):].strip())
        except ValueError:
            return "", ["知识梳理的思维导图数据无法解析，本块已跳过（请在讲义工作台修复后重新导出）"]
        if isinstance(data, dict):
            return mindmap_to_latex(data)
        return "", ["知识梳理的思维导图数据格式异常，本块已跳过"]
    return html_to_latex(text)


def iter_note_image_references(content: str) -> list:
    r"""知识块正文里的插图引用：``<img src>``、Markdown ``![]()``、
    ``\includegraphics{}``，以及 @mindmap JSON 的 "image" 字段（前向兼容）。

    只吐原始引用字符串；校验与归一化交给 resolve_upload_asset。
    """
    text = str(content or "")
    refs: list[str] = []
    if not text:
        return refs
    if text.startswith(MIND_PREFIX):
        try:
            data = json.loads(text[len(MIND_PREFIX):].strip())
        except ValueError:
            data = None

        def walk(node) -> None:
            if isinstance(node, dict):
                image = node.get("image")
                if isinstance(image, str) and image.strip():
                    refs.append(image.strip())
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(data)
        # 表格单元格里可能手滑贴了 markdown 图片，同一条正文继续走文本扫描
    refs.extend(_NOTE_IMG_SRC_RE.findall(text))
    refs.extend(_NOTE_MARKDOWN_IMG_RE.findall(text))
    refs.extend(_NOTE_INCLUDEGRAPHICS_RE.findall(text))
    return [r for r in refs if r]


def collect_lecture_images(
    questions: list, uploads_dir: str, url_prefix: str = "static/uploads",
    notes: dict | None = None,
) -> list:
    r"""题干/解析与知识梳理正文里的图片 → 绝对路径。

    ``notes`` 是 ``note_key → content`` 映射（preview 原样携带）；知识梳理
    将来会引用上传图，ledger 缺文件会让【导出 TEX】的 PDF 静默丢图。
    """
    paths: list[str] = []

    def add(reference: str) -> None:
        try:
            resolved = resolve_upload_asset(reference, uploads_dir=uploads_dir, url_prefix=url_prefix)
        except (AssetSecurityError, TypeError):
            return
        absolute = str(resolved)
        if absolute not in paths:
            paths.append(absolute)

    pattern = re.compile(
        r"!\[[^\]]*\]\((?:/static/uploads/|static/uploads/|/uploads/|uploads/)?([^)]+)\)"
        r"|\\includegraphics(?:\[[^\]]*\])?\{([^}]*)\}"
    )
    for question in questions or []:
        blob = " ".join((question.get("content") or "", question.get("answer") or ""))
        for match in pattern.finditer(blob):
            for group in match.groups():
                if group:
                    add(group.strip())
    for content in ((notes or {}) if isinstance(notes, dict) else {}).values():
        for reference in iter_note_image_references(content):
            add(reference)
    return paths


_ROLE_LABELS = {"example": "例", "variant": "变式", "practice": "趁热打铁", "homework": "实战"}

_CN_DIGITS = "零一二三四五六七八九"


def cn_numeral(n) -> str:
    """讲次/考向的中文数字，与 lecture.js 的 cnNumeral 同一口径（1..99）。"""
    try:
        k = int(n)
    except (TypeError, ValueError):
        return str(n)
    if not 1 <= k <= 99:
        return str(n)
    if k < 10:
        return _CN_DIGITS[k]
    tens, ones = divmod(k, 10)
    if tens == 1:
        return "十" + (_CN_DIGITS[ones] if ones else "")
    return _CN_DIGITS[tens] + "十" + (_CN_DIGITS[ones] if ones else "")


def lecture_title(lecture_no, knowledge: str) -> str:
    """页眉统一「第X讲 <小节名>」：章名与教材章节号不再出现，重编号只发生在渲染层。"""
    cleaned = re.sub(r"^\s*\d+(\.\d+)*[.．、]?\s*", "", str(knowledge or "")).strip()
    return "第%s讲 %s" % (cn_numeral(lecture_no), cleaned or str(knowledge or ""))

_PLACEHOLDER = r"\textcolor{gray}{（待补充）}"


def _outline_topic(section: dict, topic_id: str) -> dict:
    for outline in (section.get("topics") or []):
        if isinstance(outline, dict) and outline.get("id") == topic_id:
            return outline
    return {}


def _note(notes: dict, kind: str, section: dict, topic_id: str) -> str:
    if not topic_id and kind != "knowledge":
        return ""
    # 必须走 lecture_helper.note_key：段内 ":"/"\\" 要转义，裸拼接会查不回
    # 「小节名里带冒号」的修订稿（split_note_key 反解同样只认转义后的键）
    from mathbank.lecture_helper import note_key

    return notes.get(note_key(
        kind,
        section.get("compulsory", ""),
        section.get("chapter", ""),
        section.get("knowledge", ""),
        topic_id,
    )) or ""


def _section_entries(preview: dict) -> list:
    """统一单节与多节两种 preview 形态为条目列表。"""
    entries = preview.get("sections")
    if isinstance(entries, list) and entries:
        return [e for e in entries if isinstance(e, dict)]
    return [{
        "section": preview.get("section") or {},
        "notes": preview.get("notes") or {},
        "topics": preview.get("topics") or [],
        "extra_ids": preview.get("extra_ids") or [],
    }]


def lecture_page_counts(entry: dict) -> int:
    """一讲占的页数（不含思维导图溢出）：知识梳理 + 考向地图（有大纲考向时）
    + 每个考向一页 + 实战演练一页（有题时）。

    目录页码的唯一来源：前端 buildLectureHtml 经 countLeaves 数出来的 sheet
    页数与本函数必须同一口径，改任何一侧的分页结构都要同步另一侧。
    """
    n = 2 if (entry.get("section") or {}).get("topics") else 1
    n += len(entry.get("topics") or [])
    if entry.get("extra_ids"):
        n += 1
    return n


def toc_entries(entries: list) -> list:
    """目录行序列 [(level, name, page)]：仅讲级（用户 2026-09-22 决定，
    知识梳理 / 考向地图 / 各考向 / 实战演练等子级一律不进目录）。

    行序列与前端 lecture.js 的 tocRowsForEntry 必须同口径；页码从第 3 页起
    （标题页 1 + 目录页 1），每讲占 lecture_page_counts 页。"""
    rows = []
    start = 3
    for index, entry in enumerate(entries):
        section = entry.get("section") or {}
        lecture_no = index + 1
        rows.append(("lec", lecture_title(lecture_no, section.get("knowledge") or ""), start))
        start += lecture_page_counts(entry)
    return rows


def _map_box(outline: dict, order: int) -> str:
    """考向地图的单个方框：「考向N 名字」，N 取大纲 no（与前端同一口径）。"""
    name = escape_text(str((outline or {}).get("name") or ""))
    label = r"\textbf{考向%s}" % cn_numeral(order)
    return (r"\begin{tcolorbox}[colback=blue!4!white,colframe=blue!35!gray,"
            r"boxrule=0.4pt,arc=1mm,left=2mm,right=2mm,top=1.5mm,bottom=1.5mm,"
            r"halign=left,valign=center]"
            + "%s\\ %s" % (label, name) +
            r"\end{tcolorbox}")


def _topic_map_boxes(outline_topics: list) -> list:
    """考向地图页正文：左右两栏方框，左列 ceil(n/2)、右列其余。

    与前端 lecture.js 的 buildMap 同一分配口径。整块只占一页，不分页。
    """
    total = len(outline_topics)
    left_count = (total + 1) // 2
    columns = []
    for start, stop in ((0, left_count), (left_count, total)):
        cells = [_map_box(outline_topics[i], i + 1) for i in range(start, stop)]
        if not cells:
            continue
        col = [r"\noindent\begin{minipage}[t]{0.48\textwidth}"]
        for index, cell in enumerate(cells):
            if index:
                col.append(r"\par\vspace{2mm}")
            col.append(cell)
        col.append(r"\end{minipage}")
        columns.append(col)
    lines: list[str] = []
    lines.extend(columns[0])
    if len(columns) > 1:
        lines[-1] = lines[-1] + r"\hfill"
        lines.extend(columns[1])
    return lines


def _mb_shim_lines() -> list:
    r"""读取书本体垫片正文，进程内缓存。两类行不进导出源码：

    ① \NeedsTeXFormat/\ProvidesPackage/\endinput —— 只允许出现在真正的包文件里，
       \endinput 混进 .tex 会让 XeLaTeX 把整份讲义后面全部当文件结尾吞掉；
    ② 整行注释 —— TeX 本来就不读注释，留着反而会被导出前预检层当成脏数据
       「修复」（垫片注释里的 ``\fillin、`` 实测触发过宏补 {} 的假告警 Toast）。
    """
    if not _MB_SHIM_LINES:
        raw = _MB_SHIM_PATH.read_text(encoding="utf-8")
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped.startswith(_MB_SHIM_SKIP) or stripped.startswith("%"):
                continue
            _MB_SHIM_LINES.append(line)
    return list(_MB_SHIM_LINES)


def _book_preamble(title: str) -> list:
    r"""书本体（ElegantBook）导言区：类选项 + 垫片正文内联。

    lang=cn 才会加载 ctex（汉字通道）；chinesefont=nofont 让类跳过自带字体，
    由垫片统一焊 Fandol，与组卷侧 fontset=fandol 同一口径。答案可见性在书本体
    不需要全局开关：答案/解析本来就是正文自己排印的，include_answers 只影响
    正文装不装【答案】段，与导言区无关。
    """
    lines = [r"\documentclass[12pt,lang=cn,chinesefont=nofont]{elegantbook}"]
    lines.extend(_mb_shim_lines())
    lines.append(r"\everymath{\displaystyle}")
    lines.append(r"\title{%s}" % escape_text(title))
    lines.append("")
    return lines


_CHOICES_BLOCK_RE = re.compile(r"\\begin\{choices\}.*?\\end\{choices\}", re.S)
_CHOICE_ITEM_SPLIT_RE = re.compile(r"\\item(?:\[[^\]]*\])?")
_MACRO_NAME_RE = re.compile(r"\\[a-zA-Z@]+\*?")


def _option_width_em(text: str) -> float:
    """粗估一段选项源码排出来的宽度（em）。只用于选列数，宁可保守不收宽。"""
    stripped = _MACRO_NAME_RE.sub("", text)
    stripped = stripped.replace("$", "").replace("{", "").replace("}", "").replace("\\", "")
    width = 0.0
    for ch in stripped:
        code = ord(ch)
        if ch == " ":
            width += 0.3
        elif code < 128:
            width += 0.5
        elif code >= 0x2E80 or 0xFF00 <= code <= 0xFFEF:
            width += 1.0
        else:
            width += 0.6
    return width


def _book_col_available_em(columns: int) -> float:
    """N 列版式下单个选项可用的宽度（em）：版心先切列，再扣列表左缩进。"""
    return ((_BOOK_TEXTW_EM - (columns - 1) * _BOOK_COLSEP_EM) / columns
            - _BOOK_LIST_INSET_EM)


def _estimate_choice_columns(block: str) -> int:
    """按最宽选项估一个 choices 块的列数（书本体专用，exam-zh 自己有实测算法）。

    含图选项一律单列（multicols 里位图撑爆列宽比挤成两行难看得多）；
    4 个及以上选项才允许试 4 列，其余最多 2 列——与试卷排版习惯一致。
    返回 0 表示块里根本没有可测的选项，调用方不必注入列数。
    """
    if r"\includegraphics" in block:
        return 1
    chunks = _CHOICE_ITEM_SPLIT_RE.split(block)
    widths = [_option_width_em(chunk) for chunk in chunks[1:] if chunk.strip()]
    if not widths:
        return 0
    if len(widths) < 2:
        return 1
    longest = max(widths)
    candidates = [4, 2, 1] if len(widths) >= 4 else [2, 1]
    for cols in candidates:
        if longest <= _book_col_available_em(cols):
            return cols
    return 1


def _book_fit_choice_columns(body: str) -> str:
    """给书本体正文里每个「未显式标列数」的 choices 块注入 [columns=N]。

    题目字段 option_columns>0 时 clean_content_for_latex 已经注入过列数，绝不
    重猜；注入复用 paper_helper._apply_option_columns，与试卷体强制列数同一实现
    （含选项图放宽宽度那半段），两条链路不允许分叉。
    """
    def _repl(match: "re.Match[str]") -> str:
        block = match.group(0)
        if r"[columns" in block:
            return block
        est = _estimate_choice_columns(block)
        if est <= 0:
            return block
        return _apply_option_columns(block, est)

    return _CHOICES_BLOCK_RE.sub(_repl, body)


def _question_block(question: dict, label: str, include_answers: bool, book: bool = False) -> str:
    _q_type = question.get("question_type") or ""
    # 教师版选择题：把答案字母印进作答括号；学生版留空。
    # ⚠️ 讲义数据源里答案键是 ``answer``（前端 preview 数据），与组卷链路的
    #    ``answer_markdown`` 不同名。
    _answer_letter = (
        extract_choice_answer_letter(question.get("answer") or "")
        if include_answers and _q_type in ("single_choice", "multi_choice")
        else ""
    )
    body = clean_content_for_latex(
        question.get("content") or "",
        _q_type,
        answer_letter=_answer_letter,
        option_columns=int(question.get("option_columns") or 0),
    )
    if book:
        # 书本体没有 exam-zh 的实测自动列数：Python 侧按选项宽度估 N 注入 [columns=N]
        body = _book_fit_choice_columns(body)
    lines = [r"\noindent\textbf{%s}\quad %s" % (escape_text(label), body.strip()), ""]
    answer = (question.get("answer") or "").strip()
    if include_answers:
        lines.append(r"\noindent\textbf{【答案】}")
        lines.append(clean_content_for_latex(answer, is_answer=True).strip() or _PLACEHOLDER)
        lines.append("")
    return "\n".join(lines)


_INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]*)\}")


def sanitize_dead_image_references(tex: str, uploads_dir: str) -> tuple[str, list]:
    """把解析不到文件的 ``\\includegraphics`` 降级成可见占位文字。

    存量题可能引用已被清理/晋升掉的图片（collect_ledger_images 只收在盘上的），
    死链原样进 PDF 会让 XeLaTeX 直接崩掉整本讲义。宁缺勿炸：降级 + 上报。
    """
    from mathbank.asset_security import AssetSecurityError, resolve_upload_asset

    notes: list[str] = []

    def repl(match: re.Match) -> str:
        name = match.group(1).strip()
        try:
            resolved = resolve_upload_asset(name, uploads_dir=uploads_dir, url_prefix="static/uploads")
            if resolved.exists():
                return match.group(0)
        except (AssetSecurityError, TypeError, OSError):
            pass
        if name and name not in notes:
            notes.append(name)
        return r"\textcolor{gray}{【图片待补：%s】}" % escape_text(name or "?")

    out = _INCLUDEGRAPHICS_RE.sub(repl, tex)
    warnings = ["题目引用的图片已不存在，正文降级为占位文字：%s" % "、".join(notes)] if notes else []
    return out, warnings


def _topic_items(entries, questions_by_id):
    """逐条目展开 (讲次号, 考向序, role, 题目)：例题/变式共用一个跨考向游标，
    与 lecture.js buildTopicSheet 的编号归零口径完全一致。"""
    rows = []
    for lecture_no, entry in enumerate(entries, start=1):
        section = entry.get("section") or {}
        for index, topic in enumerate(entry.get("topics") or []):
            outline = _outline_topic(section, str(topic.get("topic_id") or ""))
            number = outline.get("no") or (index + 1)
            for item in (topic.get("items") or []):
                question = questions_by_id.get(item.get("question_id"))
                if not question:
                    continue
                rows.append((lecture_no, number, str(item.get("role") or "example"), question))
    return rows


def _numbered_rows(rows):
    """按讲次/考向归零地给每行算出正文标签（裸号）与板块头，返回
    [(lecture_no, topic_no, role, question, label, block_head)]；
    label 与 lecture.js 同口径：例<考向>-<序>、变式<考向内跨母题连续>、
    趁热打铁/实战纯裸号；block_head 非空时该题前要先出版块标题头，
    孤儿变式（本考向还没有例题）不出「典型例题」头。"""
    out = []
    ex = variant = practice = 0
    example_seen = False
    practice_seen = False
    current = None
    for lecture_no, topic_no, role, question in rows:
        if current != (lecture_no, topic_no):
            current = (lecture_no, topic_no)
            ex = variant = practice = 0
            example_seen = practice_seen = False
        head = ""
        if role == "example":
            ex += 1
            label = "例%s-%d" % (cn_numeral(topic_no), ex)
            if not example_seen:
                example_seen = True
                head = "典型例题"
        elif role == "variant":
            variant += 1
            label = "变式%d" % variant
            if ex > 0 and not example_seen:
                example_seen = True
                head = "典型例题"
        elif role == "practice":
            practice += 1
            label = str(practice)
            if not practice_seen:
                practice_seen = True
                head = "趁热打铁"
        else:
            label = "例%s-%d·练" % (cn_numeral(topic_no), max(ex, 1))
        out.append((lecture_no, topic_no, role, question, label, head))
    return out


def build_lecture_latex(preview: dict, view: str = "jiexi", fmt: str = EXPORT_FMT_EXAM) -> tuple[str, list[str]]:
    """把 preview 数据组装成完整 LaTeX 源码，返回 (源码, 降级说明)。

    view：yuan 学生版（不含答案）/ jiexi 教师版（含答案）/ daan 答案册。
    fmt：exam-zh 试卷体（默认，与组卷同款）/ elegantbook 书本体（ElegantBook+垫片）。
    一个小节 = 一讲（中文数字），每讲输出知识梳理 → 考向页 → 本讲实战演练
    （课后作业，裸号归零）；答案册整本一份、只它带「第X讲」定位前缀。
    目录页紧跟标题页。单节旧载荷行为不变。
    """
    settings = preview.get("settings") or {}
    entries = _section_entries(preview)
    first_section = entries[0].get("section") or {} if entries else {}
    questions_by_id: dict = {}
    for entry in entries:
        for q in (entry.get("questions") or []):
            questions_by_id[q.get("id")] = q
    for q in (preview.get("questions") or []):
        questions_by_id.setdefault(q.get("id"), q)
    include_answers = view in ("jiexi", "daan")
    book_fmt = fmt == EXPORT_FMT_BOOK
    degradations: list[str] = []

    title = (settings.get("cover_title") or first_section.get("chapter") or first_section.get("knowledge") or "讲义").strip()
    subtitle = (settings.get("cover_subtitle") or "同步讲义").strip()

    lines = _preamble(include_answers=include_answers, title=title, book=book_fmt)
    # exam-zh 的 \section* 会画装饰竖条并自动补「【XX】」，讲义里换用无装饰宏；
    # 标题页/目录/每讲各板块单独起页，由 \lectpage 控制
    # 书本体下垫片已经 provide 过同名宏，这里同步换成 \providecommand 才不炸；
    # 试卷体维持 \newcommand 原状（历史断言钉着）。
    macro_def = r"\providecommand" if book_fmt else r"\newcommand"
    lines.append(macro_def + r"{\lectsec}[1]{\par\vspace{4mm}{\Large\bfseries #1}\par\nopagebreak\vspace{2mm}}")
    # 讲级页的板块名（知识梳理/考向地图）：大标题下另起一行、左对齐
    lines.append(macro_def + r"{\lectsub}[1]{\par\vspace{-1mm}{\large\bfseries #1}\par\nopagebreak\vspace{2mm}}")
    lines.append(macro_def + r"{\blockhead}[1]{\par\vspace{2.5mm}{\normalsize\bfseries 【#1】}\par\vspace{1mm}}")
    lines.append(macro_def + r"{\lectpage}{\clearpage}")
    lines.append("")

    lines.append("\\begin{document}")
    lines.append(r"\raggedbottom")
    # 讲义不用试卷的密封线/注意事项：标题页直接居中排，并显式开启页码
    # （exam-zh 默认 \pagestyle{empty}，不打开的话「第 N 页（共 M 页）」不会出现，
    # 目录页码就无从对照）；ElegantBook 自带页眉页脚与页码，书本体绝不能被 plain 顶掉
    if not book_fmt:
        lines.append(r"\pagestyle{plain}")
    lines.append(r"\begin{center}{\Large\bfseries %s}\\[1mm]{\normalsize %s}\end{center}" % (
        escape_text(title), escape_text(subtitle)))
    lines.append("")

    # 目录页：仅多节合并讲义出（单节本身就薄）；书籍/论文式逐条列，点线 + 页码。
    # 行序列与前端 tocRowsForEntry 同口径，页码起点见 toc_entries 注释。
    if len(entries) > 1:
        lines.append(r"\lectpage")
        lines.append(r"\lectsec{目　录}")
        for level, name, page in toc_entries(entries):
            indent = {"lec": r"\noindent\textbf{%s}", "sub": r"\hspace*{0.9em}%s",
                      "sub2": r"\hspace*{1.6em}%s"}[level] % escape_text(name)
            lines.append(r"%s\dotfill 第 %d 页\par" % (indent, page))
        lines.append("")

    if view == "daan":
        lines.append(r"\lectpage")
        lines.append(r"\lectsec{答案册}")
        for lecture_no, topic_no, role, question, label, head in _numbered_rows(
                _topic_items(entries, questions_by_id)):
            if head:
                lines.append(r"\blockhead{%s}" % head)
            answer = (question.get("answer") or "").strip()
            prefix = "第%s讲　考向%s　" % (cn_numeral(lecture_no), cn_numeral(topic_no))
            lines.append(r"\noindent\textbf{%s}" % escape_text(prefix + label))
            lines.append(clean_content_for_latex(answer, is_answer=True).strip() if answer else _PLACEHOLDER)
            lines.append("")
        hw_block = set()
        hw_counter = 0
        for lecture_no, entry in enumerate(entries, start=1):
            for qid in (entry.get("extra_ids") or []):
                question = questions_by_id.get(qid)
                if not question:
                    continue
                if lecture_no not in hw_block:
                    hw_block.add(lecture_no)
                    lines.append(r"\blockhead{实战演练}")
                    hw_counter = 0
                hw_counter += 1
                answer = (question.get("answer") or "").strip()
                prefix = "第%s讲　" % cn_numeral(lecture_no)
                lines.append(r"\noindent\textbf{%s}" % escape_text(prefix + str(hw_counter)))
                lines.append(clean_content_for_latex(answer, is_answer=True).strip() if answer else _PLACEHOLDER)
                lines.append("")
        lines.append("\\end{document}")
        return "\n".join(lines), degradations

    for lecture_no, entry in enumerate(entries, start=1):
        section = entry.get("section") or {}
        notes = entry.get("notes") or {}
        head = lecture_title(lecture_no, section.get("knowledge") or "")
        knowledge = _note(notes, "knowledge", section, "")
        lines.append(r"\lectpage")
        lines.append(r"\lectsec{%s}" % escape_text(head))
        lines.append(r"\lectsub{知识梳理}")
        if knowledge.strip():
            body, warns = note_body_to_latex(knowledge)
            degradations.extend(warns)
            lines.append(body or _PLACEHOLDER)
        else:
            lines.append(_PLACEHOLDER)
        lines.append("")

        outline_topics = section.get("topics") or []
        if outline_topics:
            lines.append(r"\lectpage")
            lines.append(r"\lectsec{%s}" % escape_text(head))
            lines.append(r"\lectsub{考向地图}")
            lines.extend(_topic_map_boxes(outline_topics))
            lines.append("")

        # 逐考向出题：考向标题 → 易错点/解题方法 → 【典型例题】/【趁热打铁】板块。
        # 题号一律走 _numbered_rows（与答案册同一实现），此处只负责排版；
        # 每个考向单独起页，与前端 A4「一考向一页」一致
        rows_iter = iter(_numbered_rows(_topic_items([entry], questions_by_id)))
        pending = next(rows_iter, None)
        for index, topic in enumerate(entry.get("topics") or []):
            topic_id = str(topic.get("topic_id") or "")
            outline = _outline_topic(section, topic_id)
            number = outline.get("no") or (index + 1)
            lines.append(r"\lectpage")
            lines.append(r"\lectsec{考向%s　%s}" % (cn_numeral(number), escape_text(str(topic.get("name") or ""))))
            for kind, heading in (("pit", "易错点"), ("method", "解题方法")):
                lines.append(r"\noindent\textbf{%s}\quad" % heading)
                revision = _note(notes, kind, section, topic_id)
                if revision.strip():
                    body, warns = note_body_to_latex(revision)
                    degradations.extend(warns)
                    lines.append(body or _PLACEHOLDER)
                elif kind == "method" and str(outline.get("example") or "").strip():
                    lines.append(r"{\small（大纲默认，未修订）}\ %s" % escape_text(str(outline["example"]).strip()))
                else:
                    lines.append(_PLACEHOLDER)
                lines.append("")
            while pending is not None and pending[1] == number:
                _, _, role, question, label, block_head = pending
                if block_head:
                    lines.append(r"\blockhead{%s}" % block_head)
                lines.append(_question_block(question, label, include_answers, book=book_fmt))
                pending = next(rows_iter, None)

        extra_ids = [qid for qid in (entry.get("extra_ids") or []) if qid in questions_by_id]
        if extra_ids:
            lines.append(r"\lectpage")
            lines.append(r"\lectsec{%s}" % escape_text(head))
            lines.append(r"\blockhead{实战演练}")
            for position, qid in enumerate(extra_ids, start=1):
                lines.append(_question_block(questions_by_id[qid], str(position), include_answers, book=book_fmt))

    lines.append("\\end{document}")
    return "\n".join(lines), degradations


def _preamble(include_answers: bool, title: str, book: bool = False) -> list:
    if book:
        return _book_preamble(title)
    from mathbank.paper_helper import build_exam_preamble

    lines = build_exam_preamble(include_answers=include_answers, title=title)
    lines.append(r"\usepackage{enumitem}")
    lines.append(r"\setlist{itemsep=1pt,topsep=2pt,parsep=0pt,leftmargin=1.6em}")
    lines.append("")
    return lines


__all__ = [
    "build_lecture_latex",
    "collect_lecture_images",
    "escape_text",
    "html_to_latex",
    "mindmap_to_latex",
    "note_body_to_latex",
    "split_math",
]
