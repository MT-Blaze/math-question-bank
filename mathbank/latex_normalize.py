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

# 补集号自愈与导出前预检共用同一份实现（见 mathbank/latex_preflight R9）：
# 入库时就写对，卡片预览与题库源码才不会带着错字形示人；导出前再跑一遍是幂等兜底。
# ``math_region_flags`` 同时供解析板块标题扁平化避让数学区（见 flatten_solution_headings）。
from mathbank.latex_preflight import (
    math_region_flags,
    repair_complement_glyphs,
    wrap_bare_math_variables,
)
from mathbank.math_markdown import (
    compact_solution_markdown,
    normalize_fillin_macro,
    normalize_question_math_markdown,
    restore_doubled_control_words,
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


# choices 环境内「一个 \item 挤了多个选项」的程序侧兜底拆分。
# 提示词已要求逐项拆开，但 AI 偶尔仍把 A. B. C. D. 挤成一行、甚至漏掉首个标号，
# 如 `\item 1 B. 1或3 C 3 D.-3`。标号 = A-H 单个字母，前不接字母/数字
# （避免「点B在圆C上」这类被误判）、后接 . ／ ．／ 、 或空白。
_MERGED_OPTION_LABEL_RE = re.compile(r"(?<![A-Za-z0-9])([A-H])(?=[.．、]|\s)(?![A-Za-z0-9])")

# 「只剩一个标号」的形态（Q9：A/B/C 无标号或各占一行，挤压行里只剩一个 D）
# 不能光靠连续递增判据，否则整行拆不开。但单标号极易误伤正文里的数学变量
# （「点 B. 在圆上」），故单标号必须同时过下面四道闸门才算分割点。
_MERGED_SINGLE_LABEL_RANGE = "BCDEFGH"


def _merged_mark_is_split_point(protected: str, index: int, letter: str) -> bool:
    """单个标号是否可当分割点（仅供「整行只有一个标号」时使用）。

    ``\\item`` 之后的位置等价于「隐含的首选项」。A 常已被
    :func:`_strip_item_choice_labels` 剥掉，于是挤压行只剩一个 B-H 标号。
    四道防误伤闸门：

      1. 必须是 B-H（A 单标号更可能是行首残留，不是"隐含 A + 显式 X"；
         显式 A 的情形已由 strip 处理）；
      2. 标号前必须是空白（紧贴中文/字母/数字的不算）；
      3. 标号后必须紧跟标点 ``.``/``．``/``、``（纯空白分隔不算标号）；
      4. 标号前一段要"像选项内容"——非空、且不是孤立单字
         （``点 B.`` 里紧邻的「点」只有 1 个汉字，是句子前缀而非选项内容）。
    """

    if letter not in _MERGED_SINGLE_LABEL_RANGE:
        return False
    if index <= 0 or index + 1 >= len(protected):
        return False
    # 闸门 2：前一字符必须是空白
    if not protected[index - 1].isspace():
        return False
    # 闸门 3：后一字符必须是标点分隔符
    if protected[index + 1] not in ".．、":
        return False
    prev = protected[:index].strip()
    nxt = protected[index + 1:].lstrip(" .．、")
    if not prev or not nxt:
        return False
    # 闸门 4：前一段要像选项内容——≥2 字符，或含数字/字母
    if len(prev) < 2 and not re.search(r"[0-9A-Za-z]", prev):
        return False
    return True


def _split_merged_option_content(content: str) -> list[str]:
    """把 ``1 B. 1或3 C 3 D.-3`` 拆成 ``['1', '1或3', '3', '-3']``（缺首标号也认）。

    ``\\item`` 之后的位置视为「隐含首选项」：行内出现 **≥1** 个可用标号即切。
    多标号仍要求构成连续递增的一段（B C D…，防「A 在… B 在…」误伤）；
    只剩一个标号时（Q9 形态 ``<内容> D．<内容>``）走
    :func:`_merged_mark_is_split_point` 的防误伤闸门。
    """

    if not content or not content.strip():
        return [content]
    spans: dict[str, str] = {}

    def _stash(match):
        key = f"\x00MBSPAN{len(spans)}\x00"
        spans[key] = match.group(0)
        return key

    # 先保护 $...$ 数学段，避免把数学里的字母当选项标号切开。
    protected = re.sub(r"\$[^$]+\$", _stash, content)
    marks = list(_MERGED_OPTION_LABEL_RE.finditer(protected))
    if not marks:
        return [content]
    if len(marks) == 1:
        # 「只剩一个标号」：\item 之后是隐含首选项，过闸门才切成两段。
        only = marks[0]
        if not _merged_mark_is_split_point(protected, only.start(), only.group(1)):
            return [content]
        cuts = [0, only.start(), len(protected)]
    else:
        # 多标号：仍要求整体构成连续递增的一段（允许缺首个 A，如 B C D）。
        labels = "".join(m.group(1) for m in marks)
        start = ord(labels[0]) - ord("A")
        expected = "".join(chr(ord("A") + start + i) for i in range(len(labels)))
        if labels != expected:
            return [content]
        cuts = [0] + [m.start() for m in marks] + [len(protected)]
    parts: list[str] = []
    for left, right in zip(cuts[:-1], cuts[1:]):
        seg = protected[left:right].strip()
        if not seg:
            continue
        for key, value in spans.items():
            seg = seg.replace(key, value)
        # 剥掉该段开头的标号（编号由 exam-zh choices 自动生成）。
        seg = re.sub(r"^[A-H]\s*[.．、]?\s*", "", seg).strip()
        if seg:
            parts.append(seg)
    return parts if len(parts) >= 2 else [content]


_ITEM_CHOICE_INDENT = "    "  # choices 内 \item 的统一前导缩进


def _split_merged_choice_items(md: str) -> str:
    """在已有 ``choices`` 环境内，把挤在同一 ``\\item`` 的多选项拆成多项，并统一项缩进。

    顺带把块内所有 ``\\item`` 的前导空白统一成 ``_ITEM_CHOICE_INDENT``：拆出的项
    与原有项缩进不一致（曾出现「两条 4 空格、两条 2 空格」）会让拆卷编辑框里
    ``\\item`` 看着错位，也让同一条归一化链产出两种源码。统一后该函数幂等。
    """

    lines = md.split("\n")
    out: list[str] = []
    in_choices = False
    for line in lines:
        in_choices = _choices_env_state(line, in_choices)
        stripped = line.strip()
        if in_choices and stripped.startswith("\\item"):
            body = stripped[len("\\item"):].strip()
            pieces = _split_merged_option_content(body)
            if len(pieces) < 2:
                pieces = [body]
            out.extend(_ITEM_CHOICE_INDENT + "\\item " + piece for piece in pieces)
            continue
        out.append(line)
    return "\n".join(out)


# 行内选项：高中数学选择题**恒为 4 个选项**。AI 常把选项挤在题干行的中后部
# （如「... 的最小值为 A. 8 B. 9 C. 11 D. 13」），不在行首 —— 行首判据
# （_CHOICE_LABEL_LINE_RE 的 `^`）认不出来，于是选项原样留在题干里。
# 标号 = 单个 A-D 字母，前不接字母/数字（避免「点B」「AB」误判），
# 后接 . ／ ．／ 、／ , ／ ，／ : ／ ：。
_INLINE_ABCD_MARK_RE = re.compile(r"(?<![A-Za-z0-9])([A-D])[\.、．,，:：]")


def _extract_inline_choice_run(line: str) -> tuple[str, list[str]] | None:
    """识别单行里的「题干 + 行内 4 个 ABCD 选项」。

    返回 ``(题干前缀, [选项A, 选项B, 选项C, 选项D])``；不满足
    「恰好 A/B/C/D 四个标号，且每个标号后都紧跟内容（不能紧跟下一个标号）」
    时返回 ``None``。先保护 ``$...$`` 数学段，避免把数学变量 ``$A$`` 当标号。
    """

    if not line or not line.strip():
        return None
    spans: dict[str, str] = {}

    def _stash(match):
        key = f"\x00MBINLINE{len(spans)}\x00"
        spans[key] = match.group(0)
        return key

    protected = re.sub(r"\$[^$]+\$", _stash, line)
    marks = list(_INLINE_ABCD_MARK_RE.finditer(protected))
    if [m.group(1) for m in marks] != ["A", "B", "C", "D"]:
        return None
    options: list[str] = []
    for i, mark in enumerate(marks):
        right = marks[i + 1].start() if i + 1 < len(marks) else len(protected)
        seg = protected[mark.end():right].strip()
        if not seg:
            # 标号后紧跟下一个标号（如「已知 A、B、C、D …」）→ 不是选项，放弃。
            return None
        options.append(seg)

    # 末尾 ``\paren``（AI 直出的作答括号，或上一轮归一化留下的）属于题干，
    # 不属于最后一个选项 —— 挪到前缀（exam-zh 的作答括号在题干末、choices 之前）。
    trailing_paren = ""
    m = re.search(r"\s*\\paren\s*$", options[-1])
    if m:
        options[-1] = options[-1][: m.start()].rstrip()
        if not options[-1]:
            return None
        trailing_paren = " \\paren"
    prefix = protected[: marks[0].start()].rstrip() + trailing_paren

    def _restore(text: str) -> str:
        for key, value in spans.items():
            text = text.replace(key, value)
        return text

    return _restore(prefix), [_restore(option) for option in options]


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
        return _split_merged_choice_items(_strip_item_choice_labels(md))
    # 守卫：正文里没有任何选项就不碰，避免误伤普通文本里的「A．」。
    # 两条触发条件（任一成立即继续）：
    #   ① 行首 A-H 标号（原有）；
    #   ② 行内恰好 4 个 ABCD 标号、且每个标号后都有内容（高中数学选择题恒 4 选项，
    #      覆盖「... 的最小值为 A. 8 B. 9 C. 11 D. 13」这种不在行首的形态）。
    has_line_label = re.search(r"^\s*[-*]?\s*[A-H][\.、．\s]", md, re.MULTILINE)
    has_inline_run = any(
        _extract_inline_choice_run(line) is not None for line in md.split("\n")
    )
    if not (has_line_label or has_inline_run):
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
    blank_run = 0

    def flush() -> None:
        if items:
            out.append("  \\begin{choices}")
            out.extend(items)
            out.append("  \\end{choices}")
            items.clear()

    for line in lines:
        if not line.strip():
            blank_run += 1
            continue
        # 病灶②：行内 4 个 ABCD 选项（不在行首）——切成「题干前缀 + 选项块」，
        # 就地插入（与行首选项同口径，不把选项块挪到正文末尾）。
        inline_run = _extract_inline_choice_run(line)
        if inline_run is not None:
            inline_prefix, inline_options = inline_run
            flush()
            out.extend([""] * blank_run)
            blank_run = 0
            if inline_prefix.strip():
                out.append(inline_prefix.strip())
            items.extend("    \\item " + option for option in inline_options)
            flush()
            continue
        # min_labels=2：两选项判断题「A．对 B．错」也要正确拆成两个 \item，
        # 否则 B 标号与内容会被吞进 A 的 \item（导出侧默认 3 不改）。
        pieces = _split_inline_option_labels(line, min_labels=2)
        matched = [_CHOICE_LABEL_LINE_RE.match(piece) for piece in pieces]
        if not matched[0]:
            flush()
            # 选项块已经收完，之前的空行属于「题干与选项之间」，必须留在原位；
            # 选项块之后的空行由 flush 后的正文行自然承接。
            out.extend([""] * blank_run)
            blank_run = 0
            # 就地替换：选项块插在选项原本出现的位置，**不挪到正文末尾**。
            # 否则尾随图会被挤到选项块之前，导出侧 `_should_preserve_inline_image_positions`
            # 判定变化（图后有内容 → 内联居中），「尾随图右置」版面失效。
            out.append(line)
            continue
        if items:
            # 选项之间只隔着空行（MinerU/OCR 常把四幅选项图各写成一段）：
            # 这仍是**同一个**选项块，空行丢掉。旧实现在这里 flush，于是四个选项
            # 变成四个独立 `\begin{choices}`，每题都从 A 重新编号，版面彻底散架。
            blank_run = 0
        else:
            out.extend([""] * blank_run)
            blank_run = 0
        for piece, m in zip(pieces, matched):
            if m:
                items.append("    \\item " + m.group(2).strip())
            else:
                # 同一行里标号壳后面接了非选项文字（如 `A．1 B．2 如图`）：
                # 已收的选项照常成块，剩下的文字按正文留在原位。
                flush()
                out.append(piece)
    flush()
    # 病灶 2d：包装分支收尾也要复跑一次挤压拆分 —— 行内形态的选项内容里
    # 仍可能被 AI 挤进第二个标号（如 A 项里又出现「 C. …」），顶部那处
    # `_split_merged_choice_items` 只在「已有 choices 环境」的分支里跑，覆盖不到这里。
    return _split_merged_choice_items("\n".join(out))


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
    md = normalize_choice_label_dots(md)       # 选项标号：统一为全角点 A．
    md = latexify_bold(md)           # 加粗：**x** -> textbf
    return md


# ---------------------------------------------------------------------------
# 题干前导原题号：剥离出来交回给 paper_question_no
# ---------------------------------------------------------------------------
# 两条分支都必须避开小数：`3.5 的相反数` 既不是「数字+分隔符+非数字」，也不是
# 「数字紧跟小问括号」，因此原样保留。`2019·全国` 超过 3 位数字、`（1）已知` 不带阿拉伯
# 题号，同样不匹配。
_QNO_WITH_SEP_RE = re.compile(r"^(\d{1,3})[ \t]*[.．、][ \t]*(?![0-9])")
_QNO_WITH_SUB_RE = re.compile(r"^(\d{1,3})[ \t]*(?=\(\d{1,2}\))")


def extract_leading_question_number(md: str) -> tuple[str, str]:
    r"""取出题干开头的原卷题号，返回 ``(题号, 去掉题号后的题干)``。

    题号必须离开 ``content``：A4 组卷预览（``paper.js`` 的 ``globalQIndex``）与
    exam-zh 的 PDF（``\arabic*.`` 计数器，``exam_19`` 还按 1/9/12/15 跳号）都会**自己
    编号**，题干里再带一个就会排成「1. 1. 下列各曲线中」—— 实测 1.pdf 四题全部双编号。
    原卷编号的归属地是 ``questions.paper_question_no``（schema v23 专门加的列，
    ``prompts.py`` 也早已要求模型剥离题号填该字段），本函数负责兜住模型没剥的情况。

    只剥开头一个；无题号时返回 ``("", md)``，因此可重复调用。
    """
    if not md:
        return "", md or ""
    stripped = md.lstrip()
    for pattern in (_QNO_WITH_SEP_RE, _QNO_WITH_SUB_RE):
        m = pattern.match(stripped)
        if m:
            return m.group(1), stripped[m.end():].lstrip(" \t")
    return "", md


# ---------------------------------------------------------------------------
# 唯一入库归一化入口（三条录入路径在此汇合）
# ---------------------------------------------------------------------------
_EMPTY_ITEM_SHELL_RE = re.compile(
    r"^\\item(?:\s*[A-H\uFF21-\uFF28]\s*[.．、,，:：)）]?)?$"
)
# choices 环境整体块（含跨行的环境体）。非贪婪：连写多个环境时逐个判定。
_CHOICES_ENV_BLOCK_RE = re.compile(
    r"^[ \t]*\\begin\{choices\}[ \t]*\n(.*?)\n[ \t]*\\end\{choices\}[ \t]*$",
    re.S | re.M,
)


def strip_empty_solution_choices(text: str) -> str:
    r"""删掉**解析**里彻底空壳的 ``\begin{choices}...\end{choices}`` 环境。

    实测形态（2024 大庆实验高一期中第 2 题）：MinerU 在解析区误留了一个孤零零的
    「B.」标号行，归一化的选项包装器把它当成选项，于是【分析】与【详解】之间凭空多出
    一个只有空 ``\item`` 的 choices 环境——导出后卷面上是一片诡异的空白选项。

    判据只收「空壳」：环境体内除了空白与**不带任何内容的** ``\item``（``\item A．``
    这种只剩标号壳的也算）以外什么都没有。带文字、带图片的环境一律不碰——那是真选项，
    删了就是篡改教师的内容。

    只对解析侧调用（``normalize_for_storage(is_answer=True)``）：题干里的空壳环境是
    「选项丢了」的现场证据，必须留着让人看见、交给回填逻辑或人工核对，不能静默删。
    """
    if not text or r"\begin{choices}" not in text:
        return text

    def _is_shell_body(body: str) -> bool:
        for line in body.split("\n"):
            stripped = line.strip()
            if not stripped or _EMPTY_ITEM_SHELL_RE.match(stripped):
                continue
            return False
        return True

    cleaned = _CHOICES_ENV_BLOCK_RE.sub(
        lambda m: "" if _is_shell_body(m.group(1)) else m.group(0), text
    )
    if cleaned == text:
        return text
    # 删掉整块后留下的连续空行压回一个自然段间隔，首尾空白一并去掉。
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip("\n")


# ---------------------------------------------------------------------------
# 解析越界拦截：绝不允许把下一大题的章节标题吞进本题解析
# ---------------------------------------------------------------------------
# 「三、填空题（每小题 5 分，共 20 分）」这种行只可能属于**卷面结构**，不可能是本题解析
# 的内容：MinerU 按版面块切题，上一题解析末尾没有题号/「故选」之类的收束锚点时，下一节
# 的大标题就被并进了解析尾部（组卷时会在解析里凭空多出一行 section 名）。
# 判据收紧到「中文数字 + 顿号/句点 + 以『××题』收尾（可带分值/说明尾巴）」，
# 所以解析正文里「三、由题意可知」这类自述条目不会被误杀。
# 真实 MinerU 卷面还会把这些标题写成 Markdown 标题（``## 三、填空题：…``），并在题型词
# 后用冒号接一长串分值/作答说明（「本题共4小题，每小题5分，共20分.」「解答应写出…」），
# 末尾可能是全角句号也可能是半角句点——全部收进来，否则整段下一节内容会留在解析尾部。
# 另外「第II卷（非选择题，共90分）」这类卷标与三、四、标题同属下一节版面，一并截断。
_SECTION_HEADING_RE = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*[ \t]*)?"
    r"(?:"
    r"[一二三四五六七八九十]{1,3}[ \t]*[、.．][ \t]*"
    r"(?:[^\n]{0,12}?)?"
    r"(?:选择题|填空题|单选题|多选题|判断题|解答题|计算题|应用题|证明题|必考题|选考题|综合题)"
    r"[ \t\r]*"
    r"(?:"
    r"[（(][^\n]*[）)]"
    # 冒号尾巴只认带「分」或「题」的说明（分值/题量），避免把正文里的
    # 「三、解答题：由题意…」这种真实行内说明当成版面标题。
    r"|[：:](?=[^\n]*(?:分|题))[^\n]*"
    r")?"
    r"|第[ \t]*(?:[一二三四五六七八九十]+|[0-9]+|[IVXⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+)[ \t]*卷"
    r"[ \t]*(?:[（(][^\n]*[）)])?"
    r")"
    r"[ \t]*[。．.]?[ \t]*(?:\*\*)?[ \t]*$"
)


def truncate_solution_at_section_heading(text: str) -> str:
    """解析尾部越界吞进下一节标题时，从那一行起整段丢弃。

    只认**非首行**的章节标题：整条解析就是一行章节标题时，那是「这题没解析」的真实信号，
    删掉会把数据问题抹平，留给卡片与人工核对。
    """
    if not text:
        return ""
    lines = text.split("\n")
    for index in range(1, len(lines)):
        # 真实卷面是 CRLF：``split("\n")`` 后行尾还挂着 ``\r``，而正则的 ``$`` 不接受它，
        # 「第II卷（非选择题，共90分）」这类带括号尾巴的卷标会整行漏判。
        if _SECTION_HEADING_RE.match(lines[index].rstrip("\r")):
            return "\n".join(lines[:index]).rstrip()
    return text


# ---------------------------------------------------------------------------
# 解析层级扁平化：最外层只留一个【解析】，其余方括号小标题改成加粗短语
# ---------------------------------------------------------------------------
_SOLUTION_HEADING_RE = re.compile(r"【[ \t]*([^【】\n]{1,12}?)[ \t]*】")
# 【答案】是**结构契约**，不是排版层级：教师版答案字母提取（paper_helper）、
# 讲义/答案册的板块切分（lecture_export）、前端「已填答案」判定（import.js）都按它定位，
# 一个字都不能动。
_ANSWER_MARKER_LABELS = {"答案", "参考答案", "标准答案", "正确答案", "答案与解析", "答案见解析"}
# 外层解析标题的同义词：第一个出现的（且仅第一个）升级为标准的【解析】，其余全部降级。
_OUTER_SOLUTION_LABELS = {"解析", "详解", "解答", "分析", "解析与详解", "详解与解析", "点拨"}
# 协议性标记（占位/待核对）必须原文保留，不能因为「看起来像小标题」就改写。
_HEADING_KEEP_HINTS = ("待补", "待核对", "插图")


def flatten_solution_headings(text: str) -> str:
    r"""把解析里的多层 ``【XX】`` 小标题压平：最外层只留一个 ``【解析】``。

    【分析】/【详解】/【点睛】/【点评】…… 是教辅排版的套娃层级，进题库后与
    「答案 + 解析」的两段式模型对不上，导出时每个都自成一行标题。本函数**一个字正文都不删**，
    只把它们降级成加粗短语：``【点睛】文字`` → ``**点睛：** 文字``，随后由结构层的
    :func:`latexify_bold` 归一为库内唯一加粗写法 ``\textbf{点睛：}``（因此本函数必须
    跑在结构层**之前**）。

    幂等：降级后的文本里不再有方括号标题，第二次执行原样返回。
    """
    if not text or "【" not in text:
        return text
    flags = math_region_flags(text)
    matches = list(_SOLUTION_HEADING_RE.finditer(text))
    if not matches:
        return text
    outer_taken = False
    edits: list = []
    for match in matches:
        label = match.group(1).strip()
        if not label or flags[match.start()]:
            # 数学区里的【】是 MinerU 把中文写进公式内部的形态，插 ``**`` 会炸定界符。
            continue
        if label in _ANSWER_MARKER_LABELS or any(hint in label for hint in _HEADING_KEEP_HINTS):
            continue
        if not outer_taken and label in _OUTER_SOLUTION_LABELS:
            outer_taken = True
            if label != "解析":
                edits.append((match.start(), match.end(), "【解析】"))
            continue
        # 加粗标签后补一个空格再接正文：「**点睛：** 文字」读得下去，归一化成
        # ``\textbf{点睛：} 文字`` 后 LaTeX 侧也只是多一个普通词间隔，不会粘连。
        edits.append((match.start(), match.end(), "**%s：** " % label))
    if not edits:
        return text
    pieces: list = []
    cursor = 0
    for start, end, new_text in edits:
        pieces.append(text[cursor:start])
        pieces.append(new_text)
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


# ---------------------------------------------------------------------------
# 解析外层标题兜底：扁平化之后必须仍有且只有一个【解析】
# ---------------------------------------------------------------------------
# 退化形态来自「上一轮已经扁平化过」的卡片：源文里的【分析】/【详解】这时已经是
# ``**分析：**`` 或 ``\textbf{分析：}``，flatten 认不出方括号标题，于是整篇解析没有
# 最外层【解析】，导出后只剩一串加粗小标题。这里在 flatten 之前补一层外壳：
#   - 已有方括号外层标题（解析/详解/解答/分析…）→ 交给 flatten 升级，绝不重复插；
#   - 只有加粗小标题或裸正文 → 在【答案】块之后插一个【解析】；
#   - 只有【答案】、没有解析正文 → 不插（空解析是真实信号，不能靠加标题掩盖）。
_ANSWER_BLOCK_LINE_RE = re.compile(
    r"(?m)^[ \t]*【(?:参考答案|答案|答案解析|答案与解析)】"
)
# 已扁平化的外层标题（markdown / 结构层两种加粗写法），含冒号与闭合定界符。
_BOLD_SOLUTION_HEADING_RE = re.compile(
    r"(?:\*\*[ \t]*|\\textbf\{[ \t]*)"
    r"(?P<label>解析与详解|详解与解析|解析|详解|解答|分析|点拨)"
    r"[ \t]*[：:][ \t]*(?:\*\*|\})"
)
# 裸文本外层标题（「解析：」「详解：」），同样是合法外壳，不重复插。
_PLAIN_SOLUTION_HEADING_RE = re.compile(
    r"(?m)^[ \t]*(?:解析与详解|详解与解析|解析|详解|解答|分析)[ \t]*[：:]"
)
# 空【答案】后面紧跟的纯字母答案行（``【答案】\nA``）属于答案块，不是解析正文。
_ANSWER_LETTER_LINE_RE = re.compile(
    r"^[ \t]*[A-H](?:[ \t、,，/|]*[A-H])*[ \t]*[。.]?[ \t]*$"
)


def ensure_outer_solution_heading(text: str) -> str:
    r"""解析必须有统一的外层 ``【解析】``：缺失时在【答案】块之后补一个。

    只补标题、不动正文；幂等（第二次调用必然命中已有的 ``【解析】``）。
    """
    if not text or not text.strip():
        return text
    stripped = text.strip()
    # 整条解析就是一行越界章节标题：那是「本题没解析」的真实信号，留给人工核对。
    if "\n" not in stripped and _SECTION_HEADING_RE.match(stripped):
        return text

    flags = math_region_flags(text)
    for match in _SOLUTION_HEADING_RE.finditer(text):
        if flags[match.start()]:
            continue
        if match.group(1).strip() in _OUTER_SOLUTION_LABELS:
            return text

    # 退化到加粗的「解析：」就是外层标题本身，原位升级成方括号形态。
    has_bold_solution_heading = False
    for match in _BOLD_SOLUTION_HEADING_RE.finditer(text):
        if flags[match.start()]:
            continue
        if match.group("label") == "解析":
            return text[:match.start()] + "【解析】" + text[match.end():]
        has_bold_solution_heading = True
        break

    if _PLAIN_SOLUTION_HEADING_RE.search(text):
        return text

    marker = _ANSWER_BLOCK_LINE_RE.search(text)
    if marker is None:
        # 纯文字答案（``见解析`` / ``A`` / 单段结论）不是解析正文，补一个【解析】
        # 会把答案本身变成标题下的正文；只有确实存在退化后的加粗解析小标题时才补壳。
        if not has_bold_solution_heading:
            return text
        return "【解析】\n\n" + text.lstrip("\n")

    cursor = marker.end()
    size = len(text)
    while True:
        newline = text.find("\n", cursor)
        if newline < 0:
            return text
        probe = newline + 1
        while probe < size and text[probe] in " \t\r\n":
            probe += 1
        if probe >= size:
            return text
        if _ANSWER_BLOCK_LINE_RE.match(text, probe):
            cursor = probe
            continue
        next_newline = text.find("\n", probe)
        line = text[probe:next_newline if next_newline >= 0 else size]
        if _ANSWER_LETTER_LINE_RE.match(line):
            if next_newline < 0:
                return text
            cursor = next_newline
            continue
        return text[:probe] + "【解析】\n\n" + text[probe:]


# ---------------------------------------------------------------------------
# 解析配图去重：同一张图只保留最后一处引用
# ---------------------------------------------------------------------------
# 一张解析配图在【分析】与【详解】里各出现一次时，前端会渲染两张一模一样的图。
# MinerU 会把同一张图写进两个版面块，搬图规则又按**整串 URL** 判重（同一文件的
# ``images/a.jpg`` 与 ``/static/uploads/mineru_a.jpg`` 不同形），于是旧引用删不掉。
# 这里按**文件名**收口：只保留最后一处。整行只有图片时连行删，嵌在句子里时只摘
# 图片语法、正文一字不动。
_ANSWER_IMAGE_REF_RE = re.compile(
    r"!\[[^\]]*\]\((?P<markdown>[^)]+)\)"
    r"|\\includegraphics(?:\[[^\]]*\])?\{(?P<latex>[^}]+)\}"
)


def _image_reference_basename(ref: str) -> str:
    name = (ref or "").strip().strip("\"'")
    name = name.split("?", 1)[0].split("#", 1)[0].replace("\\", "/")
    return name.rsplit("/", 1)[-1].strip()


def dedupe_answer_image_references(text: str) -> tuple[str, list]:
    """同一张图在解析里出现多次时只保留最后一处；返回 ``(清理后文本, 重复文件名)``。"""
    if not text or ("![" not in text and "\\includegraphics" not in text):
        return text, []
    groups: dict = {}
    for match in _ANSWER_IMAGE_REF_RE.finditer(text):
        name = _image_reference_basename(match.group("markdown") or match.group("latex") or "")
        if name:
            groups.setdefault(name, []).append(match)
    duplicates = sorted(name for name, matches in groups.items() if len(matches) > 1)
    if not duplicates:
        return text, []

    cuts: list = []
    for name in duplicates:
        for match in groups[name][:-1]:
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.start())
            if line_end < 0:
                line_end = len(text)
            line = text[line_start:line_end]
            if not _ANSWER_IMAGE_REF_RE.sub("", line).strip():
                cuts.append((line_start, line_end + 1 if line_end < len(text) else line_end))
            else:
                cuts.append((match.start(), match.end()))

    pieces: list = []
    cursor = 0
    for start, end in sorted(cuts):
        if start < cursor:
            continue
        pieces.append(text[cursor:start])
        cursor = end
    pieces.append(text[cursor:])
    cleaned = "".join(pieces)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, duplicates


# ---------------------------------------------------------------------------
# 分段函数 / 方程组排版标准化：只允许 cases
# ---------------------------------------------------------------------------
# 「``\left\{ \begin{aligned} … \end{aligned} \right.``」是 MinerU 与公式编辑器对
# 分段函数、方程组、不等式组的共同产出形态：exam-zh 与 KaTeX 都能排，但选项编号、行距与
# 对齐全都不受控，卷面丑且两处渲染不一致。题库的**唯一**写法是 ``\begin{cases}``
# （条件用 ``&`` 分栏），Word/讲义/试卷三个出口因此才共用同一份源码。
_PIECEWISE_ROW_ENVS = "aligned|alignedat|gathered|gathered*|cases|cases*|dcases|rcases|array|matrix|smallmatrix"
# 只有「大括号 + 行式环境」这一种组合才是分段结构：``\left\{ x \mid … \right\}`` 是集合，
# 不带环境，绝不在此列。
_BRACED_ROW_ENV_RE = re.compile(
    r"\\left[ \t]*\\?[ \t]*\{[ \t]*"
    r"\\begin[ \t]*\{(?P<env>" + _PIECEWISE_ROW_ENVS + r")\}[ \t]*"
    r"(?:\{(?P<cols>[^{}]*)\}[ \t]*)?"
    r"(?P<body>.*?)"
    r"\\end[ \t]*\{(?P=env)\}[ \t]*"
    r"\\right[ \t]*(?:\\?[}.|\]])",
    re.S,
)


def _braced_env_to_cases(match: "re.Match[str]") -> str:
    original = match.group(0)
    columns = match.group("cols")
    # 带竖线列规格的是「符号表 / 单调性表」那一类表格，不是分段结构，搬进 cases 会散架。
    if columns is not None and "|" in columns:
        return original
    body = (match.group("body") or "").strip()
    if not body:
        return original
    return "\\begin{cases} %s \\end{cases}" % body


def normalize_piecewise_math(text: str) -> str:
    r"""把「``\left\{`` + ``aligned``/``array``/``matrix`` + ``\right.``」统一改写成 ``cases``。

    只动**大括号包着行式环境**这一种形态，正文里独立的 ``aligned``（多行推导）不碰——
    它不是分段结构，强行改会把对齐搞坏。转换后 ``&`` 与 ``\\`` 原样保留，
    因此 ``cases`` 里依旧是「表达式 & 条件」两栏。幂等：改完不再含 ``\left\{`` 包裹。
    """
    if not text or "\\left" not in text:
        return text
    return _BRACED_ROW_ENV_RE.sub(_braced_env_to_cases, text)


# ---------------------------------------------------------------------------
# 分段函数补对齐符：``\begin{cases} 1, x>0 \\ -1, x<0 \end{cases}`` → ``1, & x>0``
# ---------------------------------------------------------------------------
# cases 缺 ``&`` 时条件列在 PDF 里跟着表达式长短左右漂，无法竖直对齐。判定极度保守：
# 整块**每一行**都必须恰好有一个顶层逗号、逗号前是不含关系符的表达式、逗号后是带
# 关系符（或「其他/否则」）的条件，才整块补；任一行不满足就整块不动。方程组 /
# 不等式组（行内没有分隔逗号）本来就是单栏，天然不受影响。
_CASES_BLOCK_RE = re.compile(r"(\\begin\{cases\})(.*?)(\\end\{cases\})", re.S)
_CASES_ROW_SPLIT_RE = re.compile(r"(\\\\(?:\[[^\]]*\])?)")
_CASES_RELATION_RE = re.compile(
    r"[<>=≤≥≠]|\\(?:le|ge|leq|geq|leqslant|geqslant|ne|neq|lt|gt|in|notin)(?![a-zA-Z])"
)
_CASES_OTHERWISE_RE = re.compile(r"其他|其它|否则|otherwise")
_CASES_TRAILING_PUNCT = ",，.。;；"


def _top_level_commas(row: str) -> list:
    positions: list = []
    depth = 0
    i = 0
    while i < len(row):
        ch = row[i]
        if ch == "\\":
            nxt = row[i + 1] if i + 1 < len(row) else ""
            if nxt.isalpha():
                i += 2
                while i < len(row) and row[i].isalpha():
                    i += 1
                continue
            if nxt == "{":
                depth += 1
            elif nxt == "}":
                depth -= 1
            i += 2  # ``\,`` 是细空格，不是分隔逗号
            continue
        if ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
        elif ch in ",，" and depth == 0:
            positions.append(i)
        if depth < 0:
            return [-1]
        i += 1
    return positions


def _align_cases_row(row: str):
    """返回 ``(新行, None)``；不认识时返回 ``(None, 原因)``；行内无分隔逗号返回 ``(None, "")``。"""
    core = row.rstrip()
    trailing_ws = row[len(core):]
    stripped = core.rstrip(_CASES_TRAILING_PUNCT + " \t")
    tail = core[len(stripped):]
    commas = _top_level_commas(stripped)
    if commas == [-1]:
        return None, "括号不配对"
    if not commas:
        return None, ""
    if len(commas) > 1:
        return None, "一行里有多个顶层逗号"
    cut = commas[0]
    left, comma, right = stripped[:cut], stripped[cut], stripped[cut + 1:]
    if not left.strip():
        return None, "逗号前没有表达式"
    if _CASES_RELATION_RE.search(left):
        return None, "逗号前含关系符（像方程/不等式而非取值）"
    if not (_CASES_RELATION_RE.search(right) or _CASES_OTHERWISE_RE.search(right)):
        return None, "逗号后不像条件（无关系符）"
    return left.rstrip() + comma + " & " + right.lstrip() + tail + trailing_ws, None


_ALIGNED_AMP_RE = re.compile(
    r"[ \t]*&[ \t]*(?=[<>=≤≥≠]|\\(?:le|ge|leq|geq|leqslant|geqslant|ne|neq|lt|gt|in|notin)(?![a-zA-Z]))"
)
_LEADING_AMP_RE = re.compile(r"^(\s*)&[ \t]*")


def _strip_stale_amps(body: str) -> str:
    """删 aligned 遗留的 ``&``：行首孤立的 ``&``，以及紧贴关系符之前的 ``a&=0`` 式 ``&``。"""
    pieces = _CASES_ROW_SPLIT_RE.split(body)
    for idx in range(0, len(pieces), 2):
        # 行首 & 紧贴 ``\\`` 时留一个空格：``\\&\dfrac`` 直接删成 ``\\\dfrac`` 会被认成多转义的控制词。
        row = _LEADING_AMP_RE.sub(lambda m: m.group(1) or (" " if idx else ""), pieces[idx])
        pieces[idx] = _ALIGNED_AMP_RE.sub("", row)
    return "".join(pieces)


def _fill_missing_amps(body: str):
    """部分行已是「值, & 条件」、其余行缺 ``&`` 时补齐；任一行判不准返回 None。"""
    pieces = _CASES_ROW_SPLIT_RE.split(body)
    filled = 0
    for idx in range(0, len(pieces), 2):
        row = pieces[idx]
        if not row.strip():
            continue
        count = row.count("&")
        if count > 1:
            return None
        if count == 1:
            continue
        new_row, _reason = _align_cases_row(row)
        if new_row is None:
            return None
        pieces[idx] = new_row
        filled += 1
    return "".join(pieces) if filled else None


def classify_cases_body(body: str) -> tuple:
    """判定单个 cases 块体：返回 ``(状态, 新块体或 None, 原因)``。

    状态：``has_amp`` 已有对齐符 / ``aligned`` 可补 / ``system`` 方程组·不等式组（单栏，
    不需要 ``&``）/ ``ambiguous`` 模棱两可（不改）/ ``stale_amp`` aligned 遗留的错位
    ``&``（行首孤立或紧贴关系符之前），删掉后重判的结果。
    """
    if "\\begin" in body:
        return "ambiguous", None, "块内嵌套环境"
    if "&" in body:
        # aligned 的 & 用来对齐等号，搬进 cases 后成了「表达式 | 条件」分栏，关系符前会空出一大段。
        cleaned = _strip_stale_amps(body)
        if cleaned != body:
            status, new_body, reason = classify_cases_body(cleaned)
            if status == "ambiguous":
                return status, None, reason
            return "stale_amp", new_body if new_body is not None else cleaned, ""
        filled = _fill_missing_amps(body)
        if filled is not None:
            return "aligned", filled, ""
        if any(
            "&" not in row for row in _CASES_ROW_SPLIT_RE.split(body)[::2] if row.strip()
        ):
            return "ambiguous", None, "部分行有 &、部分行没有，且补不齐"
        return "has_amp", None, ""
    pieces = _CASES_ROW_SPLIT_RE.split(body)
    out = list(pieces)
    aligned_rows = 0
    plain_rows = 0
    for idx in range(0, len(pieces), 2):
        row = pieces[idx]
        if not row.strip():
            continue
        new_row, reason = _align_cases_row(row)
        if new_row is not None:
            out[idx] = new_row
            aligned_rows += 1
        elif reason:
            return "ambiguous", None, reason
        else:
            plain_rows += 1
    if aligned_rows == 0:
        return "system", None, ""
    if plain_rows:
        return "ambiguous", None, "部分行有「值, 条件」、部分行没有"
    return "aligned", "".join(out), ""


def align_cases_conditions(text: str) -> str:
    """给缺 ``&`` 的分段函数 cases 补对齐符、删 aligned 遗留的错位 ``&``；判不准的块原样保留。幂等。"""
    if not text or "\\begin{cases}" not in text:
        return text

    def _sub(match: "re.Match[str]") -> str:
        status, body, _reason = classify_cases_body(match.group(2))
        if status not in ("aligned", "stale_amp"):
            return match.group(0)
        return match.group(1) + body + match.group(3)

    return _CASES_BLOCK_RE.sub(_sub, text)


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
    5. **解析侧的文本级清洗（越界截断、配图去重、外层标题兜底、板块标题扁平化）
       必须走在结构层之前**：越界截断先做，配图去重必须基于截断后的正文（否则最后一处
       落在越界区时会把整题配图删空）；外层标题兜底必须先于扁平化（退化到
       ``**分析：**`` 的旧卡片认不出方括号标题）；扁平化产出的是 markdown 加粗
       ``**点睛：**``，正要靠结构层的 ``latexify_bold`` 归一成库内唯一加粗写法
       ``\textbf{点睛：}``；放在之后会把 ``**`` 直接落进库里，与「库里没有双星号」
       这条既有不变量冲突。
    6. ``cases`` 归一（分段函数 / 方程组 / 不等式组）排在字符层之前：它只认
       ``\\left\\{`` + 行式环境这一种纯数学形态，与表格、定界符互不干扰。

    行内分式**不在链上改写**：``\\frac`` 原样入库，统考卷口径由渲染层实现
    （网页端注入 ``\\displaystyle``，PDF 导言区 ``\\everymath{\\displaystyle}``）。

    ⚠️ 本函数必须**只在真正要落地的时候跑**（``post_process`` 末段与写库边界），
    任何录入路径都不许在它之前单独跑其中的某一步：这条链只对「自己的输出」幂等
    （``T(T(x)) == T(x)``），并不对别人的半成品保证合流。历史上正是拆题阶段先跑了
    字符层、写库时又只跑结构层，才让手敲题与拆卷题的同一种写法进了库变成两种源码。
    """
    if not value or not isinstance(value, str):
        return value or ""
    value = restore_literal_newlines(value)
    # MinerU 云路线整份 markdown 多转义一层（``\\mathit``、``\\frac``）：``\\`` 在数学
    # 模式是换行命令，后面紧跟的控制词会被当正文排出，公式直接错。必须早于结构层与
    # 字符层，否则 ``\\includegraphics`` 这类形态谁都认不出来。
    value = restore_doubled_control_words(value)
    # 补集号 ∁ 的字形错乱必须最早修：后面字符层会把 R 之类包成 \mathrm{R}，
    # 判据就认不出来了（针对 \check{\partial}_{R} 这类形态）。
    value = repair_complement_glyphs(value)[0]
    # 分段结构归一（题库只允许 cases）：题干与解析同一条规则，写法分歧在此终结。
    value = normalize_piecewise_math(value)
    # 必须紧跟 cases 归一：aligned 刚被改写成 cases，遗留的 ``a&=0`` 式 & 要在这里清掉。
    value = align_cases_conditions(value)
    value = strip_page_markers(value)
    if is_answer:
        # 越界标题必须先截：吞进来的「三、填空题」不是板块标题，扁平化会把它们
        # 降级成一堆加粗噪声，反而把越界藏起来。
        value = truncate_solution_at_section_heading(value)
        # 重复配图去重必须在截断之后：最后一处引用若落在越界区里，先删前一处的做法
        # 会让界内那张被删、界外那张又被截掉，整题配图凭空消失。
        value = dedupe_answer_image_references(value)[0]
        # 外层【解析】兜底必须早于扁平化：退化到 ``**分析：**`` 的旧卡片认不出方括号标题。
        value = ensure_outer_solution_heading(value)
        # 层级扁平化：最外层只留一个【解析】，其余【XX】降级为加粗短语（正文一字不删）。
        value = flatten_solution_headings(value)
    value = normalize_fillin_macro(value)
    value = normalize_content_for_latex_storage(value, question_type)
    value = normalize_question_math_markdown(value)
    # 变量语义包裹（R10）必须排在字符层之后：字符层已经包好的 ``$p$`` 这里会因为
    # 紧邻 ``$`` 被否决，天然幂等；反过来先包再跑字符层，裸字母会被并入 ASCII run 处理。
    value = wrap_bare_math_variables(value)[0]
    if is_answer:
        # 空壳选项环境必须在结构层之后删：那个垃圾环境正是选项包装器自己造出来的。
        value = strip_empty_solution_choices(value)
        value = compact_solution_markdown(value)
    else:
        # 题干与解析共用同一条空行口径：3 个以上换行 = 一个自然段，压回 \n\n。
        value = re.sub(r"\n{3,}", "\n\n", value)
    return value


def normalize_stem_for_storage(value: str, question_type: str = "") -> tuple[str, str]:
    """题干专用：先跑完整链，再剥离开头原卷题号，返回 ``(题号, 入库题干)``。

    顺序刻意是「先归一化再剥离」：还原字面 ``\\n`` 与清页标之后，题号才真正暴露在行首
    （例如 OCR 给出的 ``\\n1. 下列``）。调用方只在 ``paper_question_no`` 为空时回填，
    因此对同一题干重复执行结果不变。
    """
    text = normalize_for_storage(value, question_type)
    return extract_leading_question_number(text)


# ---------------------------------------------------------------------------
# 选择题作答括号：题库统一存 exam-zh 的 ``\paren``，导出侧（组卷 / 讲义 / Word）
# 再把它展开成字面括号。为什么要这样分成两步：
#   - 库里存 ``\paren`` 才能让三个出口共用同一个「作答括号」语义，不至于有的出口
#     留着全角 ``（）``、有的补 ``\paren``、有的漏出字面命令；
#   - 展开必须在导出侧做，因为教师版要把答案字母印进括号（依赖 answer_markdown）。
# ``clean_choice_stem_parentheses`` 与 ``paper_helper`` 中的同名函数**逐字同源**
# （行为必须一致，那边导出侧仍在用），这里复制一份是为了让入库层不必反向依赖导出层。
# ---------------------------------------------------------------------------
def clean_choice_stem_parentheses(text: str) -> str:
    """清洗选择题末尾的空括号，并保持数学定界符平衡。

    OCR 可能会把空括号识别为 ``$(\\quad)$`` 或 ``（$\\quad$）``。
    这些定界符必须和括号一起移除，否则会遗留孤立的 ``$$``
    并让 XeLaTeX 后续一直处于数学模式。

    2026-09-25 修「题干末尾双括号」：
      ① 摘 ``\\paren`` 必须**走在清括号之前**。旧实现反着来 —— 末尾空括号的
         正则带 ``$`` 锚定，而输入形如 ``…（　）\\paren`` 时末尾是 ``\\paren``，
         锚定落空、括号清不掉；等随后删掉 ``\\paren``，括号虽已成末尾却不再重清，
         归一化又无条件补一个 ``\\paren``，于是落库成 ``（　）\\paren`` 双括号。
      ② 末尾空括号后允许跟**句末标点**（``（　）.`` 这类）。旧正则要求括号正好
         收在文本末尾，而中文题面几乎总在作答括号后写句号，同样导致清不掉。
         只放行标点、不放行正文：``（　）条件`` 这种括号后接正文的形态不匹配，
         免得把嵌在句子中间的空括号误当成作答括号清掉。
    """
    if not text:
        return ""
    text = text.strip()

    # ① 先摘掉 ``\paren``：它若留在末尾，会顶掉下面 ``$`` 锚定的末尾匹配。
    text = re.sub(r'\\paren\b', '', text).strip()

    blank = r'(?:\\quad|\\qquad|\\hspace\{[^{}]*\}|[\s\xa0\u3000_])*'
    plain_parens = rf'[\(（]\s*{blank}\s*[\)）]'
    math_wrapped_parens = rf'\$\s*{plain_parens}\s*\$'
    math_inside_parens = rf'[\(（]\s*\$\s*{blank}\s*\$\s*[\)）]'
    # ② 末尾空括号，其后只允许再有**句末标点 / 空白 / 一个收尾的 ``$``**。
    #    用前瞻而非把这些字符一起吃掉，标点与 ``$`` 才会原样留在题面上。
    #    那个可选 ``$`` 是为了「数学模式内的作答括号」：中文题面常见
    #    ``$A \cup B = (\quad)$ \paren``，空括号**不紧贴**前导 ``$``，所以
    #    math_wrapped_parens 匹配不到，只能靠这条兜住（清完得 ``$A \cup B = $``，
    #    美元符号仍成对）。
    #    前置 ``(?<![\\$])`` 把「``$( )$`` 纯包裹」挡在 plain 分支外：那种形态交给
    #    math_wrapped_parens 整段删除，免得删剩一对孤立的 ``$$``。
    tail = r'[。．.，,；;！!？?\s\xa0\u3000]'
    pattern = (
        rf'(?:[\s\xa0\u3000]*(?:{math_wrapped_parens}|'
        rf'{math_inside_parens}|(?<![\\$]){plain_parens}))+[\s\xa0\u3000]*'
        rf'(?={tail}*\$?{tail}*$)'
    )
    cleaned = re.sub(pattern, '', text).strip()

    # 仅在未闭合的数学内容后补 "$"。若末尾本身就是孤立 "$"，
    # 直接移除；追加另一个 "$" 会把它变成未闭合的行间数学定界符 "$$"。
    dollars = list(re.finditer(r'(?<!\\)\$', cleaned))
    if len(dollars) % 2 != 0:
        last_dollar = dollars[-1]
        if cleaned[last_dollar.end():].strip():
            cleaned += "$"
        else:
            cleaned = cleaned[:last_dollar.start()].rstrip()

    return cleaned


def _split_trailing_figure_lines(head: str) -> tuple[str, str]:
    """把「题干文字」与题干末尾的**独立配图行**拆开，返回 ``(文字, 配图块)``。

    作答括号必须补在题干文字末尾，而不是尾随配图之后：MinerU 路线的原卷配图常常正好
    落在「题干句子」与「选项区」之间（figure_repair R4 补回题干的韦恩图就是这个位置），
    若不拆开，``clean_choice_stem_parentheses`` 的 ``$`` 锚定的末尾括号判据会失效——
    实测大庆卷第 2 题因此留下 ``…为（）`` 与 ``\\paren`` 并存的双括号，且括号还被排到
    韦恩图后面去了。配图行原样保留在文字之后，版面顺序不变。
    """
    lines = head.split("\n")
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if not stripped or _IMG_ONLY_LINE_RE.match(stripped):
            continue
        return "\n".join(lines[: i + 1]), "\n".join(lines[i + 1:]).strip()
    return head, ""


def normalize_choice_paren_macro(value: str, question_type: str = "") -> str:
    """把选择题题干末尾的作答空括号归一为 ``\\paren``（exam-zh 命令）。

    - 仅对 ``single_choice`` / ``multi_choice`` 生效；
    - 题干含 ``\\begin{choices}`` 时只在它之前补；
    - 幂等：已归一过的原样返回。
    """
    if not value:
        return value
    if question_type not in ("single_choice", "multi_choice"):
        return value
    if r"\begin{choices}" in value:
        head, sep, tail = value.partition(r"\begin{choices}")
        prose, figures = _split_trailing_figure_lines(head)
        head = clean_choice_stem_parentheses(prose) + r" \paren"
        if figures:
            head += "\n" + figures
        return head.rstrip() + "\n" + sep + tail
    prose, figures = _split_trailing_figure_lines(value)
    cleaned = clean_choice_stem_parentheses(prose) + r" \paren"
    if figures:
        cleaned += "\n" + figures
    return cleaned.rstrip()


# 选择题选项标号统一为全角点（U+FF0E）
# 覆盖形态：A. / A、/ A，/ A: / A： → A．
# ⚠️ 必须**消费**原标点而不是用前视断言：只看不吃会让 `\item A. 甲` 变成 `A．. 甲`、
# 让已经统一的 `\item B．` 变成 `B．．`，既不幂等又把脏标点写进库和 PDF。
_CHOICE_LABEL_DOT_RE = re.compile(
    r"(\\item\s+)([A-H])\s*[\.、．,，:：]",
)


def normalize_choice_label_dots(value: str) -> str:
    """把选择题选项标号的标点统一成全角点 `．`（U+FF0E）。

    仅作用于 ``\\begin{choices}...\\end{choices}`` 环境内的 ``\\item`` 行；
    幂等：已统一的不再改动。

    覆盖形态：
      - \\item A. ... → \\item A．...
      - \\item A、... → \\item A．...
      - \\item A，... → \\item A．...
      - \\item A:... → \\item A．...
      - \\item A．... → 不动（已是标准）

    数学区 `$...$` / `$$...$$` 内的字母绝不碰（choices 环境内不应有数学定界符包裹的标号）。
    """
    if not value or r"\begin{choices}" not in value:
        return value

    def _replace(match: re.Match) -> str:
        prefix = match.group(1)  # \item + 空白
        letter = match.group(2)  # A-H
        return f"{prefix}{letter}．"

    # 只在 choices 环境内替换
    lines = value.split("\n")
    out: list[str] = []
    in_choices = False
    for line in lines:
        stripped = line.strip()
        if stripped == r"\begin{choices}":
            in_choices = True
        elif stripped == r"\end{choices}":
            in_choices = False

        if in_choices and r"\item" in line:
            line = _CHOICE_LABEL_DOT_RE.sub(_replace, line)
        out.append(line)

    return "\n".join(out)

