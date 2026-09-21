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
from typing import Iterator

from mathbank.asset_security import AssetSecurityError, resolve_upload_asset
from mathbank.latex_normalize import _convert_one_table
from mathbank.paper_helper import clean_content_for_latex

MIND_PREFIX = "@mindmap"

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
            cells = [escape_text(str(cell or "")) for cell in (row or [])]
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
                lines.append("%s\\item %s" % (pad, escape_text(label)))
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


def note_body_to_latex(content: str) -> tuple[str, list[str]]:
    """知识块正文分派：@mindmap 前缀走 JSON，其余按 HTML 处理。"""
    text = str(content or "")
    if text.startswith(MIND_PREFIX):
        try:
            data = json.loads(text[len(MIND_PREFIX):].strip())
        except ValueError:
            return "", ["知识梳理的思维导图数据无法解析，本块已跳过（请在讲义工作台修复后重新导出）"]
        if isinstance(data, dict):
            return mindmap_to_latex(data)
        return "", ["知识梳理的思维导图数据格式异常，本块已跳过"]
    return html_to_latex(text)


def collect_lecture_images(
    questions: list, uploads_dir: str, url_prefix: str = "static/uploads"
) -> list:
    r"""题干/解析里的图片（Markdown 与 ``\includegraphics`` 两种形态）→ 绝对路径。"""
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
    return paths


_ROLE_LABELS = {"example": "例", "variant": "变式", "practice": "趁热打铁", "homework": "实战"}

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


def _question_block(question: dict, label: str, include_answers: bool) -> str:
    body = clean_content_for_latex(question.get("content") or "", question.get("question_type") or "")
    lines = [r"\noindent\textbf{%s}\quad %s" % (escape_text(label), body.strip()), ""]
    answer = (question.get("answer") or "").strip()
    if include_answers:
        lines.append(r"\noindent\textbf{【答案】}")
        lines.append(clean_content_for_latex(answer, is_answer=True).strip() or _PLACEHOLDER)
        lines.append("")
    return "\n".join(lines)


def build_lecture_latex(preview: dict, view: str = "jiexi") -> tuple[str, list[str]]:
    """把 preview 数据组装成完整 LaTeX 源码，返回 (源码, 降级说明)。

    view：yuan 学生版（不含答案）/ jiexi 教师版（含答案）/ daan 答案册。
    多节合并时按 ``sections`` 顺序逐节输出知识梳理与考向页，实战演练与
    答案册整本只出一份；单节旧载荷行为不变。
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
    degradations: list[str] = []

    title = (settings.get("cover_title") or first_section.get("chapter") or first_section.get("knowledge") or "讲义").strip()
    subtitle = (settings.get("cover_subtitle") or "同步讲义").strip()

    lines = _preamble(include_answers=include_answers, title=title)
    lines.append("\\begin{document}")
    lines.append(r"\raggedbottom")
    lines.append(r"\begin{center}{\Large\bfseries %s}\\[1mm]{\normalsize %s}\end{center}" % (
        escape_text(title), escape_text(subtitle)))
    lines.append("")

    if view == "daan":
        lines.append(r"\section*{答案册}")
        counter = 0
        for entry in entries:
            section = entry.get("section") or {}
            for index, topic in enumerate(entry.get("topics") or []):
                topic_id = str(topic.get("topic_id") or "")
                outline = _outline_topic(section, topic_id)
                number = outline.get("no") or (index + 1)
                for item in (topic.get("items") or []):
                    question = questions_by_id.get(item.get("question_id"))
                    if not question:
                        continue
                    counter += 1
                    label = "考向%s %s%d" % (
                        number, _ROLE_LABELS.get(str(item.get("role") or "example"), "例"), counter)
                    answer = (question.get("answer") or "").strip()
                    lines.append(r"\noindent\textbf{%s}" % escape_text(label))
                    lines.append(clean_content_for_latex(answer, is_answer=True).strip() if answer else _PLACEHOLDER)
                    lines.append("")
        extra_counter = 0
        for entry in entries:
            for qid in (entry.get("extra_ids") or []):
                question = questions_by_id.get(qid)
                if not question:
                    continue
                extra_counter += 1
                answer = (question.get("answer") or "").strip()
                lines.append(r"\noindent\textbf{实战%d}" % extra_counter)
                lines.append(clean_content_for_latex(answer, is_answer=True).strip() if answer else _PLACEHOLDER)
                lines.append("")
        lines.append("\\end{document}")
        return "\n".join(lines), degradations

    for entry in entries:
        section = entry.get("section") or {}
        notes = entry.get("notes") or {}
        knowledge = _note(notes, "knowledge", section, "")
        lines.append(r"\section*{知识梳理}")
        if knowledge.strip():
            body, warns = note_body_to_latex(knowledge)
            degradations.extend(warns)
            lines.append(body or _PLACEHOLDER)
        else:
            lines.append(_PLACEHOLDER)
        lines.append("")

        outline_topics = section.get("topics") or []
        if outline_topics:
            lines.append(r"\section*{考向地图}")
            lines.append("\\begin{enumerate}")
            for outline in outline_topics:
                lines.append(r"\item %s" % escape_text(str((outline or {}).get("name") or "")))
            lines.append("\\end{enumerate}")
            lines.append("")

        for index, topic in enumerate(entry.get("topics") or []):
            topic_id = str(topic.get("topic_id") or "")
            outline = _outline_topic(section, topic_id)
            number = outline.get("no") or (index + 1)
            lines.append(r"\subsection*{考向%s　%s}" % (number, escape_text(str(topic.get("name") or ""))))
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
            example = 0
            variant = 0
            practice = 0
            for item in (topic.get("items") or []):
                question = questions_by_id.get(item.get("question_id"))
                if not question:
                    continue
                role = str(item.get("role") or "example")
                if role == "example":
                    example += 1
                    variant = 0
                    label = "例%d" % example
                elif role == "variant":
                    variant += 1
                    label = "变式%d.%d" % (max(example, 1), variant)
                elif role == "practice":
                    practice += 1
                    label = "趁热打铁%d" % practice
                else:
                    label = "例%d·练" % max(example, 1)
                lines.append(_question_block(question, label, include_answers))

    extra_sections = [e for e in entries if (e.get("extra_ids") or [])]
    if extra_sections:
        lines.append(r"\section*{实战演练}")
        position = 0
        for entry in extra_sections:
            section = entry.get("section") or {}
            multi = len(extra_sections) > 1
            for qid in (entry.get("extra_ids") or []):
                question = questions_by_id.get(qid)
                if not question:
                    continue
                position += 1
                heading = ""
                if multi:
                    heading = r"\noindent{\small\itshape %s · %s}\par" % (
                        escape_text(str(section.get("chapter") or "")),
                        escape_text(str(section.get("knowledge") or "")))
                lines.append(heading)
                lines.append(_question_block(question, "实战%d" % position, include_answers))

    lines.append("\\end{document}")
    return "\n".join(lines), degradations


def _preamble(include_answers: bool, title: str) -> list:
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
