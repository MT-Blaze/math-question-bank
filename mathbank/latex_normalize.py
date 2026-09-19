"""入库前 LaTeX 归一化（三条录入路径的共享汇合点）。

目的：让「编辑器手敲/单题 OCR」「试卷拆解导入（MinerU / 原生文字公式提取 / 全图
视觉 OCR）」「二次编辑保存」这三条路径在**入库**这一步产出完全一致的 LaTeX 风味
源码——做到「源码一致」而非「渲染一致」。统一入口是 ``normalize_for_storage``，
它同时负责结构层（图片 / 表格 / 选项 / 加粗 / \\fillin）与字符层（补 ``$`` 定界符、
``*`` -> ``\\cdot``、``$$`` 降级、多余空行压缩）。

设计要点：
- 本模块的两个入口都在**图片收集/剥离之后**调用（``post_process`` 最末尾与写库
  边界），彼时 image_paths 资源清单已生成，正文里只剩未归属的内联图（含选项图）。
- ``normalize_for_storage`` 必须幂等：写库边界与 post_process 都会调用它，且题目
  二次保存时会再走一遍，任何一次都必须字节不变，否则同一道题会被越改越走形。
- 图片一律写成 ``\\includegraphics[选项]{文件名}``（只留 basename，不写路径）：
  路径由 questions.image_paths 资源清单决定，搬家也不怕断链。
- 选项图必须保持内联（0.42\\linewidth），不能变成居中块，否则 exam-zh 横排废掉。
"""
import os
import re
from html.parser import HTMLParser

from mathbank.math_markdown import (
    compact_solution_markdown,
    normalize_fillin_macro,
    normalize_question_math_markdown,
    restore_literal_newlines,
    strip_page_markers,
)

# ---------------------------------------------------------------------------
# 图片：Markdown / LaTeX 两种形态 -> \\includegraphics{文件名}
# ---------------------------------------------------------------------------
_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_LATEX_IMG_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")

# 选项行判据：行首是 A-H 标号（带标点）。用于决定图片走「内联」还是「居中」。
_CHOICE_LINE_RE = re.compile(r"^\s*[-*]?\s*[A-H][\.、．]")
# choices 环境内的 `\item` 行：OCR 提示词要求剥离 A-D 标号，产出
# `\item ![插图](...)`。与上面同属「选项内容」，图片同样要走内联限宽。
_CHOICES_ITEM_RE = re.compile(r"\\item\b")


def _img_options(md: str, pos: int) -> str:
    """根据图片所在行是否属于选项，选内联或通栏参数。"""
    line_start = md.rfind("\n", 0, pos) + 1
    line_end = md.find("\n", pos)
    if line_end == -1:
        line_end = len(md)
    line = md[line_start:line_end]
    # 形态一：标号与图片同行（`A．![](url)`）。
    if _CHOICE_LINE_RE.match(line):
        return r"max width=0.42\linewidth,max height=3.2cm,keepaspectratio"
    # 形态二：choices 环境内的 `\item` 行（OCR 提示词要求剥离标号，产出
    # `\item ![插图](...)`，行首没有 A-D 标号）。不认这种形态的话，选项图会
    # 按通栏 0.85 处理，夹在 exam-zh choices 横排里显得突兀。
    if _CHOICES_ITEM_RE.match(line.strip()):
        return r"max width=0.42\linewidth,max height=3.2cm,keepaspectratio"
    return r"max width=0.85\linewidth,keepaspectratio"


def _basename_of(url: str) -> str:
    return os.path.basename(url.split("?")[0].split("#")[0])


def latexify_images(md: str) -> str:
    """把正文里所有图片引用统一成 ``\\includegraphics[选项]{文件名}``。"""
    if not md:
        return md

    def repl_md(m: re.Match) -> str:
        base = _basename_of(m.group(1).strip())
        opts = _img_options(md, m.start())
        return rf"\includegraphics[{opts}]{{{base}}}"

    # 先处理 Markdown 形态；选项图此时仍在「A．![](url)」行，行首判据可命中 -> 内联。
    md = _MD_IMG_RE.sub(repl_md, md)

    def repl_latex(m: re.Match) -> str:
        base = _basename_of(m.group(1).strip())
        opts = _img_options(md, m.start())
        return rf"\includegraphics[{opts}]{{{base}}}"

    # 再归一化已有的 LaTeX 形态（去掉路径、统一参数），幂等。
    md = _LATEX_IMG_RE.sub(repl_latex, md)
    return md


# ---------------------------------------------------------------------------
# 选项：纯文本 / 分行图片 -> \\begin{choices}\\item ... \\end{choices}
# ---------------------------------------------------------------------------
# 同时认 Markdown 与 \\includegraphics 两种「整行只有图」形态（归一化可能在
# 图片转换之后才做选项包装，所以这里两种都要兜底）。
_IMG_ONLY_LINE_RE = re.compile(
    r"^\s*(?:"
    r"!\[[^\]]*\]\([^)]+\)"
    r"|\\[a-zA-Z]*includegraphics(?:\[[^\]]*\])?\{[^}]+\}"
    r")\s*$"
)
_BARE_LABEL_LINE_RE = re.compile(r"^\s*[-*]?\s*([A-D])[\.、．]?\s*$")
# 裸 `\item` 行：AI 实测产出「`\item` 单独一行、图形另起一行」（OCR 提示词要求
# 剥离 A-D 标号，所以选项内容形态是 `\item` 而非 `A．`）。与裸标号行同构，
# 必须一起合并，否则图片会被 step6 当成普通配图剥掉（Q1 四选项变空壳）。
_BARE_ITEM_LINE_RE = re.compile(r"^\s*\\item\s*$")


def _merge_stacked_choice_images(text: str) -> str:
    """把「标号一行、图形另起一行」合并成 ``标号．\\includegraphics{...}`` 同行形态。

    两种标号行都要认：Markdown 形态的裸字母标号 ``A``，以及 choices 环境内
    由 OCR 提示词产出的裸 ``\\item``（AI 实测的主要形态）。合并后图片行首
    没有标号，``_img_options`` 才能按「选项图」给它内联限宽。
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = _BARE_LABEL_LINE_RE.match(lines[i])
        item_m = _BARE_ITEM_LINE_RE.match(lines[i])
        if m or item_m:
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and _IMG_ONLY_LINE_RE.match(lines[j]):
                prefix = f"{m.group(1)}．" if m else "\\item "
                out.append(f"{prefix}{lines[j].strip()}")
                i = j + 1
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


# choices 环境内 `\item` 行上残留的 A-H 标号，如 `\item A．2 条`。
# 要求标号后**必须还有内容**（`\S.*`）：`\item A` / `\item A．` 这种
# 本身就是选项内容或坏数据，不剥，避免把选项改成空壳。
_ITEM_CHOICE_LABEL_RE = re.compile(
    r"^(\s*\\item\s*)([A-Ha-hＡ-Ｈ])\s*[.．、,，:：)）]\s*(\S.*)$"
)


def _choices_env_state(line: str, in_choices: bool) -> bool:
    """按一行文本更新「是否位于 choices 环境内」的状态。

    一行里同时出现起止标记时，以**靠后的那个**为准（`\\end{choices}` 后紧跟
    `\\begin{choices}` 的紧凑排版也判得对）。
    """
    stripped = line.strip()
    last_begin = stripped.rfind("\\begin{choices}")
    last_end = stripped.rfind("\\end{choices}")
    if last_begin < 0 and last_end < 0:
        return in_choices
    return last_begin > last_end


def _strip_item_choice_labels(md: str) -> str:
    """剥掉已有 ``choices`` 环境内 ``\\item`` 行上的 A-H 标号。

    AI 直出 ``\\begin{choices}`` 时（实测 **MinerU 路线**）常写成
    ``\\item A．2 条``，标号与内容混在一起；而原生 / 视觉 OCR 路线的提示词
    要求剥离标号，产出的是 ``\\item 2 条``。

    留着原文标号有两个后果：
      ① exam-zh 的 choices 环境会**自己生成**标号，导出后出现「A. A．2 条」
         双重标号；
      ② 三条录入路线的入库源码不一致，违背「统一 LaTeX 源码」的契约
         （这正是本次验证暴露出的横向不一致）。

    choices 环境是机器生成的确定性结构标记，环境内 ``\\item`` 行开头的
    「字母+标点」按定义就是标号，剥掉比任何启发式都可靠。
    """

    lines = md.split("\n")
    out: list[str] = []
    in_choices = False
    for line in lines:
        in_choices = _choices_env_state(line, in_choices)
        if in_choices:
            match = _ITEM_CHOICE_LABEL_RE.match(line)
            if match:
                line = match.group(1) + match.group(3)
        out.append(line)
    return "\n".join(out)


def latexify_choices(md: str, question_type: str = "") -> str:
    """把纯文本选项行包装成 exam-zh 的 ``\\begin{choices}`` 环境。

    复用导出侧已验证过的切分正则（_split_inline_option_labels /
    _CHOICE_LABEL_LINE_RE），保证入库结构与导出结构同源。

    ``question_type`` 用于防误伤：只有明确的选择题（single_choice /
    multi_choice）才做选项包装；否则题干里「A．表示集合 A」这类纯文本会被
    误转成选项环境。未传时（兼容旧调用）退回纯文本判据。
    """
    if not md:
        return md
    # AI 直出 choices 环境（实测 MinerU 路线）：先统一剥掉 `\item` 行上的标号。
    # 必须放在下面「无选项行就不碰」的守卫**之前**——choices 环境里标号位于
    # `\item ` 之后而非行首，那条守卫的 `^` 判据根本认不出来，会直接放行。
    if r"\begin{choices}" in md:
        return _strip_item_choice_labels(md)
    # 守卫：正文里没有任何选项行就不碰，避免误伤普通文本里的「A．」。
    if not re.search(r"^\s*[-*]?\s*[A-H][\.、．\s]", md, re.MULTILINE):
        return md
    # 防误伤（Q3）：明确非选择题时不做选项包装。
    if question_type and question_type not in {"single_choice", "multi_choice"}:
        return md

    from mathbank.paper_helper import (  # 惰性导入，避免模块加载期循环依赖
        _CHOICE_LABEL_LINE_RE,
        _split_inline_option_labels,
    )

    text = _merge_stacked_choice_images(md)
    lines = text.split("\n")
    out: list[str] = []
    items: list[str] = []

    def flush() -> None:
        if items:
            out.append("  \\begin{choices}")
            out.extend(items)
            out.append("  \\end{choices}")
            items.clear()

    for line in lines:
        # min_labels=2：两选项判断题「A．对 B．错」也要正确拆成两个 \item，
        # 否则 B 标号与内容会被吞进 A 的 \item（导出侧默认 3 不改）。
        for piece in _split_inline_option_labels(line, min_labels=2):
            m = _CHOICE_LABEL_LINE_RE.match(piece)
            if m:
                items.append("    \\item " + m.group(2).strip())
            else:
                # 就地替换：选项块插在选项原本出现的位置，**不挪到正文末尾**。
                # 否则尾随图会被挤到选项块之前，导出侧 `_should_preserve_inline_image_positions`
                # 判定变化（图后有内容 → 内联居中），「尾随图右置」版面失效。
                flush()
                out.append(piece)
    flush()
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 表格：HTML <table> -> \\begin{tabular}...\\end{tabular}
# ---------------------------------------------------------------------------
def _convert_one_table(html: str):
    """解析单个 HTML <table> 为 LaTeX tabular。过于复杂（rowspan / 嵌套表）返回 None。"""

    class _P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows: list[list[str]] = []
            self.cur: list[str] | None = None
            self.buf: list[str] = []
            self.in_cell = False
            self.colspan = 1
            self.too_complex = False
            self.table_depth = 0

        def handle_starttag(self, tag, attrs):
            tag = tag.lower()
            if tag == "table":
                self.table_depth += 1
                if self.table_depth > 1:
                    self.too_complex = True
            elif tag in ("tr",):
                if self.cur is None:
                    self.cur = []
            elif tag in ("td", "th"):
                self.in_cell = True
                self.buf = []
                d = dict(attrs)
                try:
                    self.colspan = int(d.get("colspan", 1) or 1)
                except (TypeError, ValueError):
                    self.colspan = 1
                if int(d.get("rowspan", 1) or 1) > 1:
                    self.too_complex = True
            # thead/tbody/tfoot 等容器忽略

        def handle_endtag(self, tag):
            tag = tag.lower()
            if tag == "table":
                self.table_depth = max(0, self.table_depth - 1)
            elif tag in ("td", "th"):
                if self.in_cell:
                    self.in_cell = False
                    text = "".join(self.buf).strip()
                    cell = text
                    if self.colspan > 1:
                        cell = r"\multicolumn{%d}{c|}{%s}" % (self.colspan, text)
                    if self.cur is not None:
                        self.cur.append(cell)
                    self.colspan = 1
            elif tag == "tr":
                if self.cur is not None:
                    self.rows.append(self.cur)
                self.cur = None

        def handle_data(self, data):
            if self.in_cell:
                self.buf.append(data)

    p = _P()
    try:
        p.feed(html)
    except Exception:
        return None
    if p.too_complex or not p.rows:
        return None
    ncols = max((len(r) for r in p.rows), default=0)
    if ncols == 0:
        return None

    def esc(s: str) -> str:
        # 只转义会在 tabular 文本模式出错的字符；保留 $...$ 数学与反斜杠。
        return s.replace("&", "\\&").replace("%", "\\%").replace("#", "\\#")

    body: list[str] = [r"\begin{tabular}{|" + "c|" * ncols + "}"]
    body.append(r"\hline")
    for r in p.rows:
        cells = [esc(c) for c in r]
        while len(cells) < ncols:
            cells.append("")
        body.append(" & ".join(cells) + r" \\ \hline")
    body.append(r"\end{tabular}")
    return "\n".join(body)


def _esc_cell(s: str) -> str:
    # 只转义会在 tabular 文本模式出错的字符；保留 $...$ 数学与反斜杠。
    return s.replace("&", "\\&").replace("%", "\\%").replace("#", "\\#")


def _is_pipe_row(s: str) -> bool:
    return bool(re.match(r"^\s*\|.*\|\s*$", s))


def _is_sep_row(s: str) -> bool:
    return bool(re.match(r"^\s*\|?\s*:?-{2,}(?:\s*\|\s*:?-{2,})*\s*\|?\s*$", s)) and "|" in s and "-" in s


def pipe_table_to_tabular(md: str) -> str:
    """把 Markdown 管道表格（原生 / 视觉 OCR 路线的主要表格形态）转成 tabular。

    MinerU 路线产出 HTML <table>，原生 / 视觉 OCR 路线产出管道表格。两者都归一成
    tabular，三路线在「表格」这一项上才能源码一致。
    """
    if not md or "\n" not in md:
        return md
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        # 表格起点：当前行是管道行，且下一行是分隔行（|---|---|）。
        if _is_pipe_row(line) and i + 1 < n and _is_sep_row(lines[i + 1]):
            header_cells = [c.strip() for c in line.strip().strip("|").split("|")]
            j = i + 2  # 跳过表头行与分隔行
            body: list[list[str]] = []
            while j < n and _is_pipe_row(lines[j]):
                body.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            ncols = max(len(header_cells), *(len(r) for r in body), 1)
            tex: list[str] = [r"\begin{tabular}{|" + "c|" * ncols + "}"]
            tex.append(r"\hline")
            tex.append(" & ".join(_esc_cell(c) for c in header_cells) + r" \\ \hline")
            for r in body:
                while len(r) < ncols:
                    r.append("")
                tex.append(" & ".join(_esc_cell(c) for c in r) + r" \\ \hline")
            tex.append(r"\end{tabular}")
            out.append("\n".join(tex))
            i = j
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def html_table_to_tabular(md: str) -> str:
    if not md or "<table" not in md.lower():
        return md
    out: list[str] = []
    last = 0
    for m in re.finditer(r"<table\b.*?</table>", md, re.DOTALL | re.IGNORECASE):
        converted = _convert_one_table(m.group(0))
        out.append(md[last : m.start()])
        out.append(converted if converted is not None else m.group(0))
        last = m.end()
    out.append(md[last:])
    return "".join(out)


# ---------------------------------------------------------------------------
# 加粗：**x** -> \\textbf{x}
# ---------------------------------------------------------------------------
def latexify_bold(md: str) -> str:
    if not md:
        return md
    return re.sub(r"\*\*([^*]+?)\*\*", r"\\textbf{\1}", md)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def normalize_content_for_latex_storage(md: str, question_type: str = "") -> str:
    """入库前把解析产物统一成 LaTeX 风味源码（结构层一致）。

    ``question_type`` 用于选项转换防误伤（仅选择题才包装 choices）。
    指纹计算走不带 question_type 的路径（两侧一致），故查重不受影响。
    """
    if not md:
        return md
    md = html_table_to_tabular(md)  # 表格：HTML <table> -> tabular（MinerU 路线）
    md = pipe_table_to_tabular(md)  # 表格：管道表格 -> tabular（原生/视觉 OCR 路线）
    # ⚠️ 必须在 latexify_images **之前**合并「标号/`\item` 一行、图形另起一行」：
    #    图片限宽是靠图片**自身所在行**的行首判据决定的，合并晚了图片已被判成
    #    普通配图（0.85 通栏），夹进 exam-zh choices 横排里会顶掉布局。
    md = _merge_stacked_choice_images(md)
    md = latexify_images(md)         # 图片：Markdown/LaTeX -> includegraphics{文件名}
    md = latexify_choices(md, question_type)  # 选项：纯文本/分行图 -> choices 环境
    md = latexify_bold(md)           # 加粗：**x** -> textbf
    return md


# ---------------------------------------------------------------------------
# 题干前导原题号：统一成「数字 + 半角点 + 一个空格」
# ---------------------------------------------------------------------------
# 只认「1~3 位数字 + 分隔符」且分隔符后不接数字的形态，因此 `3.5 的相反数`、
# `2019年…`、`（1）已知…` 都不会被误改。
_LEADING_QNO_RE = re.compile(r"^(\d{1,3})[ \t]*[.．、][ \t]*(?![0-9])")


def normalize_leading_question_number(md: str) -> str:
    r"""把题干开头的原题号统一成 ``<n>. `` 一种写法。

    视觉 OCR / 原生提取 / 手敲三种来源给出的前缀分别是 `1.下列`、`2. 如图`、
    `3、某地`，同一份卷子进库后风格三种，组卷预览与导出里看起来就是「排版不齐」。
    注意：本函数**只统一写法、不删除题号** —— 题库详情与 A4 预览直接渲染题干文本，
    题号一删，网页上就没有编号了（而 exam-zh 的 PDF 会自动编号，两者本就不一致）。
    原卷号该不该从题干里彻底剥离、改由 `questions.paper_question_no` 承载，是一个
    会影响网页编号显示的独立决策，未拍板前不得在此处顺带改掉。
    """
    if not md:
        return md
    stripped = md.lstrip()
    if not stripped:
        return md
    m = _LEADING_QNO_RE.match(stripped)
    if not m:
        return md
    return f"{m.group(1)}. {stripped[m.end():]}"


# ---------------------------------------------------------------------------
# 唯一入库归一化入口（三条录入路径在此汇合）
# ---------------------------------------------------------------------------
def normalize_for_storage(value: str, question_type: str = "", is_answer: bool = False) -> str:
    """入库前对题干/解析执行的**唯一**完整归一化链，顺序即语义，不可调换。

    三条录入路径（① 编辑器手敲/单题 OCR、② 试卷拆解卡片、③ 二次编辑保存）
    都必须只经这一条链得到入库源码，因此要求它**幂等**：同一段文字无论是原始
    形态还是已经被本函数处理过的形态，再过一遍都必须字节不变。这样卡片里手动
    改过的一个字、草稿箱重新保存、或对已入库题目再保存，都不会让格式漂移。

    顺序理由：
    1. 字面「反斜杠 + n」与页标必须先还原/清除 —— 两者都会骗过后面的行级判据。
    2. \\fillin 规范化必须先于字符层：字符层把 ``\\fillin`` 当成结构命令整体保护，
       而 ``____`` 会被它就地换成 ``\\fillin`` 后再判断，两种输入形态因此必须先进到
       同一形态，否则「手敲 ____」与「卡片里留 ____」会得到不同的 ``$`` 包裹结果。
    3. **结构层必须先于字符层**：字符层给裸公式补 ``$`` 时若表格还是管道文本形态，
       整行会被包成 ``$| a | b |$``，结构层就再也认不出这是表格行（表格丢行）。
       字符层认识 tabular/choices/includegraphics 并整体保护它们。
    4. ``$$..$$`` 降级与多余空行压缩只属于解析；题干的独立公式是合理的。

    ⚠️ 本函数必须**只在真正要落地的时候跑**（``post_process`` 末段与写库边界），
    任何录入路径都不许在它之前单独跑其中的某一步：这条链只对「自己的输出」幂等
    （``T(T(x)) == T(x)``），并不对别人的半成品保证合流。历史上正是拆题阶段先跑了
    字符层、写库时又只跑结构层，才让手敲题与拆卷题的同一种写法进了库变成两种源码。
    """
    if not value or not isinstance(value, str):
        return value or ""
    value = restore_literal_newlines(value)
    value = strip_page_markers(value)
    value = normalize_fillin_macro(value)
    value = normalize_content_for_latex_storage(value, question_type)
    value = normalize_question_math_markdown(value)
    if is_answer:
        value = compact_solution_markdown(value)
    else:
        value = normalize_leading_question_number(value)
        # 题干与解析共用同一条空行口径：3 个以上换行 = 一个自然段，压回 \n\n。
        value = re.sub(r"\n{3,}", "\n\n", value)
    return value

