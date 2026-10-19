# -*- coding: utf-8 -*-
r"""导出前 LaTeX 预检 / 自愈层（待办 #8 P0）。

题库存量内容里有一类「入库时就坏了」的公式：导入链路把 LaTeX 源码当转义串解过
一遍，``\\`` 掉成单个 ``\``、``\nRightarrow`` 掉成一个换行；另有 ``\left``/``\right``
被中文打断、宏名紧贴汉字被 TeX 吞掉等形态。XeLaTeX 撞上这些形态必然报错，而生产
编译带 ``-halt-on-error``（见 ``paper_helper.compile_tex_to_pdf``），于是**一道坏题
炸掉整卷**——与 2026-10-18 的缺图事故同一条路径。缺图那张网是
``patch_missing_latex_images``，本模块是公式侧的同语义网：宁缺勿炸，能自愈的自愈，
救不了的降级成卷面上看得见的字面符号，**绝不把注定报错的源码直接丢给编译器**。

五条规则统一遵循一个取舍：**能确定还原的就还原，不能确定的只上报不改写**。
例如被换行劈成 ``<换行>Rightarrow`` 的宏，究竟是 ``\Rightarrow`` 还是
``\nRightarrow``（「推出」与「推不出」语义相反）无法从字面判断，因此这里只把
断掉的行内公式并回同一段落让它可编译，不去猜宏名——猜错等于篡改教师的解析。

调用方（routes_paper / routes_lecture）与编译入口 ``compile_tex_to_pdf`` 会各跑一次，
契约是**第二次调用零改写、零提示**：每条提示都必须对应一次真实的改写，重跑不该把
同一件事报两遍。为此每条规则都只处理「修完就不再匹配自身判据」的形态——例如降级
未闭合 ``$`` 时，要把这个数学段里**所有** ``$`` 一次转干净，只转开界符会留下新的孤立
 ``$``，第二遍又报一次。全库 411 题实测：命中 6 题、二次调用提示为空。
"""

from __future__ import annotations

import re

__all__ = ["repair_latex_before_compile", "repair_complement_glyphs",
           "wrap_bare_math_variables", "MAX_NOTE_SAMPLES"]

# 每条规则最多附几个样例进上报文案：够定位即可，整卷样例会把响应头撑爆。
MAX_NOTE_SAMPLES = 3
_SAMPLE_CHARS = 72

# 汉字与全角标点：xeCJK 下它们是「字母」，紧跟在控制序列后会并入宏名。
_CJK_LIKE = r"[\u2e80-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]"

# 题库正文里会出现、且必须与后随汉字隔开的 exam-zh 专有宏。
# \paren 在选择题分支已被 clean_content_for_latex 展开成字面括号，这里防的是
# 其它题型漏展开的存量数据；补 {} 对可选参数型宏是无害空组。
_CJK_EATEN_MACROS = ("fillin", "paren")

# 行式数学环境：这些环境里「单个反斜杠 + 一个字母」几乎必然是丢失的行分隔 \\。
# 曾只认 \x \y \z（LaTeX 无此宏、误伤面为零）；2026-10-18 全库书本体压测翻出
# id=340 的 \a——kernel 恰好把 \a 定义成 NFSS 重音切换宏（\a#1 会改写 \, 的定义），
# 不报错、不崩宏，却悄悄毁掉后续全部 \fillin 之类含 \, 的排版，整本书 rc=1。
# cases/array 一类环境是纯数学区，单字母重音宏（\a \b \c \d \H \k \r \u \v）与
# \i \j \o 都是文本模式产物，数学内容里不存在合法用法；mhchem v4 也不再预定义
# 单字母元素宏。因此字母表放开到全部 A-Za-z：真 \\ 由 (?<!\\) 挡住，多字母宏
# （\left \complement \lvert …）由 (?![A-Za-z]) 挡住。
_ROW_ENVS = (
    "cases", "dcases", "rcases", "aligned", "align", "alignat", "gather",
    "split", "array", "matrix", "pmatrix", "bmatrix", "vmatrix", "Vmatrix",
    "smallmatrix", "subarray",
)

_DOLLAR_RE = re.compile(r"(?<!\\)\$")
_LEFT_RE = re.compile(r"\\left\b")
_RIGHT_RE = re.compile(r"\\right\b")
# 被中文/文本切断的 \left … \right：$A\left\{…$ 或 $…\right\}B$
_SPLIT_DELIM_RE = re.compile(
    r"\$([^$]*?\\left[^$]*?)\$\s*([^$\\]*?)\s*\$([^$]*?\\right[^$]*?)\$", re.S
)
_MACRO_CJK_RE = re.compile(
    r"(\\(?:%s))(?=%s)" % ("|".join(_CJK_EATEN_MACROS), _CJK_LIKE)
)
# 数学区里直接写 Unicode 符号（∞ ① ∪ ≤…）：exam-zh 与书本体的拉丁/CM 数学字体
# 都没有这些字形，XeLaTeX 只报 Missing character 警告就静默丢字，教师拿到 PDF 才
# 发现缺了一角。源头换成等价的纯宏（宏名后补空格终止），产物不含 Unicode，天然幂等。
# 带 ASCII 同位符的全角符号顺手归半角。顿号、全角逗号需要 \text{} 兜底、会把 Unicode
# 留在产物里破坏幂等，不进本表——那两类留给入库清洗（待办 #30 家族）从源头治。
_UNICODE_MATH_TO_MACRO = {
    "\u221e": r"\infty ", "\u222a": r"\cup ", "\u2229": r"\cap ",
    "\u2208": r"\in ", "\u2209": r"\notin ", "\u2286": r"\subseteq ",
    "\u2287": r"\supseteq ", "\u2282": r"\subset ", "\u2283": r"\supset ",
    "\u2264": r"\leq ", "\u2265": r"\geq ", "\u2260": r"\neq ",
    "\u2248": r"\approx ", "\u00d7": r"\times ", "\u00f7": r"\div ",
    "\u00b1": r"\pm ", "\u2213": r"\mp ", "\u00b7": r"\cdot ",
    "\u221a": r"\surd ", "\u2211": r"\sum ", "\u220f": r"\prod ",
    "\u2234": r"\therefore ", "\u2235": r"\because ", "\u25b3": r"\triangle ",
    "\u00b0": r"^{\circ} ",
    "\u21d2": r"\Rightarrow ", "\u21d0": r"\Leftarrow ", "\u21d4": r"\Leftrightarrow ",
    "\u2460": r"\textcircled{1}", "\u2461": r"\textcircled{2}",
    "\u2462": r"\textcircled{3}", "\u2463": r"\textcircled{4}",
    "\u2464": r"\textcircled{5}", "\u2465": r"\textcircled{6}",
    "\u2466": r"\textcircled{7}", "\u2467": r"\textcircled{8}",
    "\u2468": r"\textcircled{9}", "\u2469": r"\textcircled{10}",
    "\uff0d": "-", "\uff0b": "+", "\uff1d": "=", "\uff1c": "<", "\uff1e": ">",
    "\uff08": "(", "\uff09": ")", "\uff3b": "[", "\uff3d": "]",
}


def _restore_unicode_math_symbols(text: str) -> tuple[str, int, list[str]]:
    r"""把数学区（$…$ / $$…$$ / \(…\) / \[…\]）里的 Unicode 符号换成等价宏。

    必须排在 R5 之后跑：R5 把未配对的收尾 $ 全部转义后，剩下的 $ 才一定成对，
    「按未转义 $ 配对」的扫描模型才成立。被 R5 降级段落里的符号留在文本模式，
    本就不该进数学宏，保持原样由宿主字体决定。
    """
    if not any(ch in text for ch in _UNICODE_MATH_TO_MACRO):
        return text, 0, []

    out: list = []
    samples: list = []
    count = 0
    closers: list = []  # 当前数学区的闭合定界符栈：display 可套 inline
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\\":
            two = text[index:index + 2]
            if closers and len(closers[-1]) == 2 and two == closers[-1]:
                closers.pop()
                out.append(two)
                index += 2
                continue
            nxt = text[index + 1:index + 2]
            if nxt == "(":
                closers.append(r"\)")
                out.append(two)
                index += 2
                continue
            if nxt == "[":
                closers.append(r"\]")
                out.append(two)
                index += 2
                continue
            out.append(two)
            index += 2
            continue
        if char == "$" and index + 1 < length and text[index + 1] == "$":
            if not closers:
                closers.append("$$")
                out.append("$$")
            elif closers[-1] == "$$":
                closers.pop()
                out.append("$$")
            elif closers[-1] == "$":
                closers.pop()
                out.append("$")
                out.append("$")
            else:
                out.append("$$")
            index += 2
            continue
        if char == "$":
            if not closers:
                closers.append("$")
                out.append("$")
            elif closers[-1] == "$":
                closers.pop()
                out.append("$")
            else:
                out.append("$")
            index += 1
            continue
        if closers and char in _UNICODE_MATH_TO_MACRO:
            macro = _UNICODE_MATH_TO_MACRO[char]
            count += 1
            if len(samples) < MAX_NOTE_SAMPLES:
                samples.append("%s→%s" % (char, macro.strip()))
            out.append(macro)
            index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out), count, samples


# 蕴含箭头 ⇒ ⇐ ⇔：拉丁正文（Termes）、中文兜底（Fandol）、CM 正文都没有字形，
# 字符类/字体配置救不了（2026-10-18 讲义压测实测：charclass 修好后 ⇒/⇔ 仍缺字）。
# 数学区里的已由 R6 换成 \Rightarrow 等纯宏，本函数只收文本区的漏网——整句
# str.replace，产物不含 Unicode 箭头，对整条流水线幂等。
_GLYPHLESS_ARROWS = {
    "\u21d2": r"\ensuremath{\Rightarrow}",
    "\u21d0": r"\ensuremath{\Leftarrow}",
    "\u21d4": r"\ensuremath{\Leftrightarrow}",
}


def _replace_glyphless_arrows(text: str) -> tuple[str, int, list[str]]:
    r"""把中拉丁字体通缺字形的蕴含箭头换成 \ensuremath 宏（R7）。"""
    count = 0
    samples: list = []
    for char, macro in _GLYPHLESS_ARROWS.items():
        hit = text.count(char)
        if not hit:
            continue
        count += hit
        if len(samples) < MAX_NOTE_SAMPLES:
            samples.append("%s→%s" % (char, macro))
        text = text.replace(char, macro)
    return text, count, samples


_BEGIN_DOC_RE = re.compile(r"\\begin\{document\}")
_BARE_HASH_RE = re.compile(r"(?<!\\)#")


def _escape_bare_hashes(text: str) -> tuple[str, int, list[str]]:
    r"""把正文区的裸 # 转义成 \#（R8）。

    库内实测脏数据（id=132「$...$ ##0.5」、id=329「##$\{...\}$」，2026-10-18 全库
    压测翻出）：正文里裸 # 是 LaTeX 保留字符，XeLaTeX 当场报 "macro parameter
    character # in horizontal mode"。只处理 \begin{document} 之后——垫片/导言区的
    宏定义参数引用（#1）绝不能碰，碰了整条渲染链就散架。"""
    anchor = _BEGIN_DOC_RE.search(text)
    if not anchor:
        return text, 0, []
    head, body = text[: anchor.start()], text[anchor.start():]
    positions = [m.start() for m in _BARE_HASH_RE.finditer(body)]
    if not positions:
        return text, 0, []
    samples: list = []
    for pos in positions[:MAX_NOTE_SAMPLES]:
        start = max(0, pos - 8)
        samples.append(body[start:pos + 9].strip())
    body = _BARE_HASH_RE.sub(r"\\#", body)
    return head + body, len(positions), samples


_ROW_ENV_RE = re.compile(
    r"(\\begin\{(?:%s)\}\*?(?:\[[^\]]*\])?)(.*?)(\\end\{(?:%s)\}\*?)"
    % ("|".join(_ROW_ENVS), "|".join(_ROW_ENVS)),
    re.S,
)
_LOST_ROWBREAK_RE = re.compile(r"(?<!\\)\\([A-Za-z])(?![A-Za-z])")
_PARAGRAPH_RE = re.compile(r"\n[ \t]*\n")
# 段落开头是命令/结构件时不许并段：那说明奇数个 $ 属于「正常跨段的展示公式」
# 或下一段是 choices 环境，并进去反而制造新错误。
_STRUCTURAL_HEAD_RE = re.compile(r"^\s*\\(?:begin|end|item|noindent|includegraphics|lect|blockhead)")
_STRUCTURAL_BODY_RE = re.compile(r"\\(?:begin|end)\{|^\s*\\item", re.M)


def _dollar_count(chunk: str) -> int:
    return len(_DOLLAR_RE.findall(chunk))


def _sample(chunk: str) -> str:
    """把命中片段压成一行短样例，供上报文案定位用。"""
    flat = " ".join(str(chunk or "").split())
    if len(flat) > _SAMPLE_CHARS:
        flat = flat[:_SAMPLE_CHARS] + "…"
    return flat


def _escape_text_arg(value: str) -> str:
    """把并入 \\text{} 的普通文本里的 TeX 特殊字符转义掉。"""
    out = []
    for ch in value:
        out.append(r"\%s" % ch if ch in "&%#_{}~^$" else ch)
    return "".join(out)


def _crs_inside_math(text: str) -> int:
    r"""数「落在未闭合行内公式里」的裸回车字符个数。

    这是 ``\nRightarrow`` 一类宏被转义解码劈坏的指纹：宏名断点处留下一个 CR。
    只统计数学段内的 CR，正常的 CRLF 段间换行不计，避免每次导出都刷 Toast。
    """
    inside = False
    count = 0
    index = 0
    length = len(text)
    while index < length:
        ch = text[index]
        if ch == "\\":
            index += 2
            continue
        if ch == "$":
            if text[index:index + 2] == "$$":
                index += 2
                continue
            inside = not inside
        elif ch == "\r" and inside:
            count += 1
        index += 1
    return count


def _normalize_line_endings(text: str) -> tuple[str, int]:
    r"""把 CRLF / 裸 CR 一律折成 LF。

    题库正文按 Windows 习惯存了 CRLF，而 ``compile_tex_to_pdf`` 写盘时用的是默认
    换行翻译（Windows 下 ``\n`` 再变 ``\r\n``）。两者叠加后，被转义解码劈坏的宏名
    处那个 CR 会与写盘产生的 CRLF 并成「CR + CRLF」——TeX 视作两个行尾，于是段内
    换行升格成空行、行内公式跨段，``Missing $ inserted`` 直接炸掉整卷（题库 id=105
    实测）。在源头统一成 LF 即可彻底消掉这条放大链，且对排版零影响。

    返回 ``(归一化后的文本, 数学段内的 CR 数)``，后者用于上报。
    """
    inside_math_crs = _crs_inside_math(text)
    if "\r" not in text:
        return text, inside_math_crs
    return text.replace("\r\n", "\n").replace("\r", "\n"), inside_math_crs


def _restore_split_delimiters(text: str) -> tuple[str, int, list[str]]:
    r"""把被中文打断的 ``\left`` … ``\right`` 并回同一个行内公式。

    判据严格：前一段必须 ``\left`` 多于 ``\right``、后一段必须反过来，中间只允许
    普通文本（不含反斜杠与 ``$``）。满足才合并成 ``$…\left\{…\text{或}…\right\}…$``，
    数学内容一字不改，只把断开处的文本包进 ``\text{}``。
    """
    samples: list[str] = []
    fixed = 0

    def _repl(match: re.Match) -> str:
        nonlocal fixed
        left_seg, middle, right_seg = match.group(1), match.group(2), match.group(3)
        if len(_LEFT_RE.findall(left_seg)) <= len(_RIGHT_RE.findall(left_seg)):
            return match.group(0)
        if len(_RIGHT_RE.findall(right_seg)) <= len(_LEFT_RE.findall(right_seg)):
            return match.group(0)
        fixed += 1
        if len(samples) < MAX_NOTE_SAMPLES:
            samples.append(_sample(match.group(0)))
        glue = r"\text{%s}" % _escape_text_arg(middle.strip()) if middle.strip() else ""
        return "$%s%s%s$" % (left_seg, glue, right_seg)

    return _SPLIT_DELIM_RE.sub(_repl, text), fixed, samples


def _isolate_macros_before_cjk(text: str) -> tuple[str, int, list[str]]:
    r"""给紧贴汉字的宏名补 ``{}``，防止汉字被并进宏名。

    xeCJK 把汉字当字母，``中有\fillin个元素`` 会被 TeX 读成 ``\fillin个元素``
    这个不存在的宏，直接 Undefined control sequence 崩掉整卷。补一对花括号
    既终止宏名，又不改变排版结果。
    """
    samples: list[str] = []
    hits = list(_MACRO_CJK_RE.finditer(text))
    for match in hits[:MAX_NOTE_SAMPLES]:
        samples.append(_sample(text[match.start():match.start() + 24]))
    patched, count = _MACRO_CJK_RE.subn(r"\g<1>{}", text)
    return patched, count, samples


def _restore_lost_row_breaks(text: str) -> tuple[str, int, list[str]]:
    r"""在行式数学环境里把丢成 ``\x`` 的行分隔补回 ``\\ x``。

    存量 cases 常见形态 ``2x^{2}-x+3,x\geq0\x+3,x<0`` —— 原文是 ``…\geq0\\ x+3``，
    ``\\`` 在入库时被转义解码吃掉一个反斜杠，于是 ``\x`` 成了未定义宏。只在
    cases/aligned/matrix 这类「按行排」的环境里还原，且只认 ``\x \y \z``
    （LaTeX 无此单字母宏），误伤面为零。
    """
    samples: list[str] = []
    count = 0

    def _fix_env(match: re.Match) -> str:
        nonlocal count
        head, body, tail = match.group(1), match.group(2), match.group(3)
        found = list(_LOST_ROWBREAK_RE.finditer(body))
        if not found:
            return match.group(0)
        count += len(found)
        if len(samples) < MAX_NOTE_SAMPLES:
            samples.append(_sample(body[max(0, found[0].start() - 20):found[0].end() + 12]))
        return head + _LOST_ROWBREAK_RE.sub(lambda m: r"\\ %s" % m.group(1), body) + tail

    return _ROW_ENV_RE.sub(_fix_env, text), count, samples


def _join_broken_inline_math(text: str) -> tuple[str, int, list[str]]:
    r"""把跨了空行的行内公式并回同一段落。

    奇数个 ``$`` 的段落意味着行内公式没闭合，TeX 会一路吞到段尾甚至文档尾才报
    ``Missing $ inserted``。成因同上：``\nRightarrow`` 掉成换行后，清洗逻辑把这个
    换行升格成了空行。这里只把相邻段落用单个换行并起来（数学内容不动），并且
    **不去猜**该补什么宏名。

    不动数学内容，也绝不猜该补什么宏名；并完仍不闭合的交给 R5 降级收口。
    """
    paragraphs = _PARAGRAPH_RE.split(text)
    samples: list[str] = []
    joined = 0
    index = 0
    while index < len(paragraphs) - 1:
        current = paragraphs[index]
        if not _dollar_count(current) % 2:
            index += 1
            continue
        following = paragraphs[index + 1]
        if (
            _STRUCTURAL_HEAD_RE.match(following)
            or _STRUCTURAL_BODY_RE.search(current)
            or _STRUCTURAL_BODY_RE.search(following)
        ):
            index += 1
            continue
        if len(samples) < MAX_NOTE_SAMPLES:
            samples.append(_sample(current) + " ⏎ " + _sample(following))
        paragraphs[index + 1] = current + "\n" + following
        joined += 1
        del paragraphs[index]
    return "\n\n".join(paragraphs), joined, samples


def _degrade_unclosed_dollars(text: str) -> tuple[str, int, list[str]]:
    r"""把段落结束时仍未闭合的 ``$`` 降级成字面 ``\$``。

    R4 只能并段，救不了「这道题从头到尾就少一个收尾 ``$``」的形态：TeX 会一路吞到段尾报
    ``Missing $ inserted``，配上 ``-halt-on-error`` 就是**整卷出不了 PDF**。而在真实导出链路
    里 R4 常常无权并段——每道题都被 ``\begin{problem}`` 包着，并段会撞进下一题的结构件。
    所以这里按「宁缺勿炸」收口：卷面上多出一个可见的 ``$``，教师一眼能找到，其余各题照常出版。

    判据是左起配对（与 TeX 同模型）：一个数学段（``$`` 或 ``$$`` 开启）跨过空行仍未闭合，
    就把**这个段里已出现的全部 ``$``** 一起转义。只转开界符会留下尾部的孤立 ``$``——那种
    ``$$a=1$`` 形态（入库链把未闭合公式翻倍后的产物）第二次调用还得再修一遍，既不幂等也
    让同一问题被报两次。空行在数学模式里本身非法（``\par`` 不许出现在数学模式），因此
    「跨过空行仍未闭合」必然是坏形态，不存在误伤合法展示公式的可能；文档末尾未闭合同样收口。
    """
    pending: list = []  # 当前未闭合数学段里出现的每个 $ 的位置（展示公式的开界符算两个）
    runs: list = []     # 每个未闭合段的起始位置，用于取样与计数
    escape_at: list = []
    state = None  # None / "inline" / "display"
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\\":
            index += 2  # 控制序列或已转义字符：整体跳过，绝不把 \$ 当成开界符
            continue
        if char == "$":
            width = 2 if text[index:index + 2] == "$$" else 1
            if state is None:
                state = "display" if width == 2 else "inline"
                pending = list(range(index, index + width))
                runs.append(index)
            elif state == "inline" and width == 1:
                state, pending = None, []
            elif state == "display" and width == 2:
                state, pending = None, []
            else:
                pending.extend(range(index, index + width))
            index += width
            continue
        if char == "\n":
            probe = index + 1
            while probe < length and text[probe] in " \t\r":
                probe += 1
            # 必须再撞到一个行尾才算空行（段末）。换行后紧跟缩进只是同一句里换行，
            # 数学段本该继续；把它当成段末就会把好端端的 $ 误降级。
            if state is not None and probe < length and text[probe] == "\n":
                escape_at.extend(pending)  # 跨过空行仍未闭合：整段降级
                state, pending = None, []
        index += 1
    if state is not None:
        escape_at.extend(pending)

    if not runs:
        return text, 0, []
    hit = [position for position in runs if position in set(escape_at)]
    samples = [_sample(text[position:position + 60]) for position in hit[:MAX_NOTE_SAMPLES]]
    chars = list(text)
    for position in sorted(set(escape_at), reverse=True):
        chars.insert(position, "\\")
    return "".join(chars), len(hit), samples


# ---------------------------------------------------------------------------
# R9 补集符号 ∁ 的字形错乱（2026-10-19 大庆卷第 2 题实测）
# ---------------------------------------------------------------------------
# 原卷的「∁_R(A)」是补集号，MinerU 按字形映射常给出三种坏形态：
#   \check{\partial}_{R}(A\cap B)   —— 认成「带帽的偏导号」
#   \check{\mathrm{o}}_{\mathrm{R}} —— 认成「带帽的字母 o」（同一卷重跑换版本后就这样）
#   \partial_{R}(A)                —— 帽子整个丢了
# XeLaTeX 不会报错（\partial、\check 都是合法宏），于是卷面安静地排出
# 「ȯ_R(A∩B)」「∂_R(A∩B)」——教师看到的是错的集合运算。补集号 \complement 由 amssymb
# 提供，导出模板（paper_helper / word_export_helper）都已 \usepackage{amssymb}。
#
# 判据刻意收紧，偏导号是微积分的正常符号，绝不能一律改写：
#   1) \check{...} 里只可能是 \partial 或字母 o（含 \mathrm/\mathbb 包法），
#      且后面紧跟 _{下标} 或 ( ——\check{A} 这类「点的记号」不在此列；
#   2) 裸 \partial 只在「下标是数集字母 R/N/Z/Q/U（可带 \mathbf 等字体包装）」时改写，
#      \partial_{x}、\frac{\partial y}{\partial x} 这些偏导写法一个都不碰。
_CHECK_COMPLEMENT_RE = re.compile(
    r"\\check\s*\{\s*(?:\\partial|\\(?:mathrm|mathbb|mathbf|operatorname)\s*\{\s*[oO]\s*\}|[oO])\s*\}"
    r"(?=\s*(?:_|[\(（]))"
)
_SET_LETTER = r"(?:\\(?:mathbf|mathrm|mathbb)\s*\{\s*[RNZQU]\s*\}|[RNZQU])"
_PARTIAL_COMPLEMENT_RE = re.compile(
    r"\\partial(?=\s*_\s*(?:\{" + _SET_LETTER + r"\}|" + _SET_LETTER + r"))"
)


# lambda 替换：字符串替换会先把反斜杠交给 re 解释（bad escape），换成图里的字面量最稳。
_COMPLEMENT_MACRO = "\\complement"


def repair_complement_glyphs(text: str) -> tuple[str, int, list[str]]:
    r"""把 MinerU 误映射的补集号还原成 \complement（R9）。"""
    if not text:
        return text, 0, []
    samples: list = []
    count = 0
    for pattern in (_CHECK_COMPLEMENT_RE, _PARTIAL_COMPLEMENT_RE):
        matches = list(pattern.finditer(text))
        if not matches:
            continue
        count += len(matches)
        if len(samples) < MAX_NOTE_SAMPLES:
            for m in matches[: MAX_NOTE_SAMPLES - len(samples)]:
                start = max(0, m.start() - 6)
                samples.append(text[start:m.end() + 12].strip())
        text = pattern.sub(lambda m: _COMPLEMENT_MACRO, text)
    return text, count, samples



# ---------------------------------------------------------------------------
# R10 裸变量的数学定界符（技术债 #36）
# ---------------------------------------------------------------------------
# 「若 p 是 q 的充分不必要条件」里的 p、q 是数学命题变元，但字符层的高置信度判据要求
# run 里带运算符或控制命令（= 、< 、^ 、\frac …），单个字母不算数学串，于是它们以**正文字面
# 形态**入库：卷面上是与中文同字号的正体 p，和旁边的 $f(x)$、$a$ 明显不是一套东西，
# Word 导出与 MathJax 预览也跟着不一致。
#
# 判据（四条同时成立才包，任一条不过就放行）：
#   ① 字母单独成词：左右紧邻都不许是字母/数字（app 的 p、pH 的 p、x2 一律不碰），
#      也不许是 ``\``、``$``、``_``、``^``、``{``、``}``、``%`` 这类结构字符
#      —— 于是 ``\p`` 与已在数学态里的 ``$p$`` 天然被排除，也保证本规则**幂等**；
#   ② 至少一侧（跳过空格后）是中文字符或全角标点：英文句子里的孤立字母不会被包；
#   ③ 只认小写：大写单字母既可能是集合名（A）又可能是选项标号/点名（「故选：D」「点 A」），
#      歧义太大，一律交给字符层的高置信度判据；
#   ④ 不在已有数学区里（$...$ / $$...$$ / \(...\) / \[...\] 与 equation、align 一类行式
#      环境）：数学态里的字母本就是斜体，而 MinerU 会把中文连接词写在公式**内部**
#      （存量 id=200：``$A\otimes B=\{x\mid x\in A\cup B 且 x\notin A\cap B\}$``），
#      在这里补 $ 等于提前关掉定界符，会把整条公式炸成文本。
# 单位形态（「5 m」）与函数名后的变元（「sin x」）靠「跳过空格后仍贴 ASCII 字母/数字」
# 这条邻位判据挡掉。
_BARE_VAR_RE = re.compile(r"[a-z]")
# 紧邻处出现即否决的字符：单词的一部分、LaTeX 命令、已有数学定界符/结构件。
_BARE_VAR_REJECT_NEIGHBOR = set("\\$_{}^%&~") | set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


# 数学区定界符与行式数学环境：R10 用它判断字母是否已经在数学态里。
_MATH_DELIMS = (("$$", "$$"), (r"\[", r"\]"), (r"\(", r"\)"), ("$", "$"))
_MATH_ENV_RE = re.compile(
    r"\\begin\{(equation|eqnarray|align|alignat|flalign|gather|multline|math|displaymath)\*?\}"
    r".*?\\end\{\1\*?\}",
    re.S,
)


# _CJK_LIKE 本身已经是字符类（自带方括号），再套一层会变成嵌套集合、判据全灭。
_CJK_CHAR_RE = re.compile(_CJK_LIKE)


def _is_ascii_alnum(ch: str) -> bool:
    return bool(ch) and ch.isascii() and ch.isalnum()


def _is_cjk_like(ch: str) -> bool:
    return bool(ch) and bool(_CJK_CHAR_RE.match(ch))


def _neighbor(text: str, index: int, step: int) -> str:
    """沿 step 方向跳过空格后的第一个有意义字符（越界返回空串）。"""
    j = index
    while 0 <= j < len(text) and text[j] == " ":
        j += step
    return text[j] if 0 <= j < len(text) else ""


def _tex_is_escaped(text: str, index: int) -> bool:
    """index 处的字符前有奇数个反斜杠即为「被转义」，此时它不是定界符。"""
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def _math_region_flags(text: str) -> bytearray:
    r"""逐字符标记「已经在数学区里」，供 R10 避让。

    定界符按就近配对（与 ``lecture_export.split_math`` 同一模型）：库里存量数据有
    落单的 ``$``，配不上对就当正文字面放过——若按奇偶一刀切，一个脏 ``$`` 会把后面
    整页正文都当成数学区，规则当场失效，得不偿失。
    """
    flags = bytearray(len(text))
    cursor = 0
    length = len(text)
    while cursor < length:
        for opening, closing in _MATH_DELIMS:
            if not text.startswith(opening, cursor) or _tex_is_escaped(text, cursor):
                continue
            end = text.find(closing, cursor + len(opening))
            while end >= 0 and _tex_is_escaped(text, end):
                end = text.find(closing, end + len(closing))
            if end < 0:
                continue
            end += len(closing)
            flags[cursor:end] = b"\x01" * (end - cursor)
            cursor = end
            break
        else:
            cursor += 1
    for env in _MATH_ENV_RE.finditer(text):
        flags[env.start():env.end()] = b"\x01" * (env.end() - env.start())
    return flags


def wrap_bare_math_variables(text: str) -> tuple[str, int, list[str]]:
    r"""给中文语境里单独成词的小写数学变量补上 $ 定界符（R10）。"""
    if not text:
        return text, 0, []
    flags = _math_region_flags(text)
    pieces: list = []
    cursor = 0
    count = 0
    samples: list = []
    for match in _BARE_VAR_RE.finditer(text):
        start, end = match.start(), match.end()
        if flags[start]:
            continue  # 已经在数学区里：再包 $ 会把定界符配错
        prev_char = text[start - 1] if start else ""
        next_char = text[end] if end < len(text) else ""
        if prev_char in _BARE_VAR_REJECT_NEIGHBOR or next_char in _BARE_VAR_REJECT_NEIGHBOR:
            continue
        left = _neighbor(text, start - 1, -1)
        right = _neighbor(text, end, 1)
        if not (_is_cjk_like(left) or _is_cjk_like(right)):
            continue
        # 跳过空格之后仍然贴着 ASCII 字母或数字的，不包：
        #   「5 m」「2 mol」——数字打头的是单位；
        #   「sin x」「log 2 x」——前面是函数名的尾巴，包成 ``sin $x$`` 只会把
        #   「函数名正体 + 变元斜体」的毛病摆得更显眼（那属于 \sin 归一化的事）。
        if _is_ascii_alnum(left) or _is_ascii_alnum(right):
            continue
        count += 1
        if len(samples) < MAX_NOTE_SAMPLES:
            samples.append(_sample(text[max(0, start - 12):end + 12]))
        pieces.append(text[cursor:start])
        pieces.append("$" + match.group(0) + "$")
        cursor = end
    if not count:
        return text, 0, []
    pieces.append(text[cursor:])
    return "".join(pieces), count, samples



def _note(label: str, count: int, samples: list[str]) -> str:
    parts = ["%s：%d 处" % (label, count)]
    if samples:
        parts.append("（例：" + "；".join(samples) + "）")
    return "".join(parts)


def repair_latex_before_compile(tex_content: str) -> tuple[str, list[str]]:
    """编译前跑一遍，返回 ``(改写后的源码, 人话说明列表)``。

    说明列表与讲义侧 ``X-MathBank-Export-Notes`` 同一口径：每条一句话，前端直接
    拼进 Toast，教师据此回题库修原始数据。
    """
    text = str(tex_content or "")
    if not text:
        return text, []

    notes: list[str] = []

    # 换行归一化必须排在最前：后面的跨段判据都按 LF 计算。
    text, stray_crs = _normalize_line_endings(text)
    if stray_crs:
        notes.append(
            "公式内部存在裸回车（宏名疑在入库时被转义解码打断），已归一化换行避免"
            "整卷编译中断，但公式可能缺字，请核对题库原文：%d 处" % stray_crs
        )

    text, n, samples = _restore_split_delimiters(text)
    if n:
        notes.append(_note(r"公式定界符 \left/\right 被中文打断，已并回同一公式", n, samples))

    text, n, samples = _isolate_macros_before_cjk(text)
    if n:
        notes.append(_note("宏名紧贴汉字会被 TeX 吞掉，已补 {}", n, samples))

    text, n, samples = _restore_lost_row_breaks(text)
    if n:
        notes.append(_note(r"分段公式的行分隔 \\ 在库里已丢失，已按行式环境补回", n, samples))

    text, n, samples = _join_broken_inline_math(text)
    if n:
        notes.append(_note("行内公式跨段落断裂，已并回同一段（数学内容未改）", n, samples))

    text, n, samples = _degrade_unclosed_dollars(text)
    if n:
        notes.append(_note(
            "公式在库里就缺少收尾的 $（无法安全补全），已降级成卷面字面符号以免整卷编译中断", n, samples
        ))

    # R6 放最后：它依赖 R5 之后「剩下的 $ 必成对」这个不变量。
    text, n, samples = _restore_unicode_math_symbols(text)
    if n:
        notes.append(_note("数学区里的 Unicode 符号在试卷字体中缺字形，已换成等价宏以免静默缺字", n, samples))

    # R7 必须排在 R6 后：数学区里的 ⇒⇔ 已被 R6 换成纯宏，这里只收文本区的漏网。
    text, n, samples = _replace_glyphless_arrows(text)
    if n:
        notes.append(_note(
            r"蕴含箭头 ⇒⇔ 在中文字体里亦缺字形，已换成 \ensuremath 等价宏以免静默缺字", n, samples))

    # R9 独立于其它规则：它只改写「补集号被认成偏导号/带帽 o」这一种字形错乱。
    text, n, samples = repair_complement_glyphs(text)
    if n:
        notes.append(_note(
            r"补集号 ∁ 在库里被写成了 \check{\partial}/\partial（字形映射错误），"
            r"已还原为 \complement", n, samples))

    # R8 的裸 # 判据按「\begin{document} 之后」划界，放在最后与前面规则互不干扰。
    text, n, samples = _escape_bare_hashes(text)
    if n:
        notes.append(_note(
            r"答案正文里的裸 # 是 LaTeX 保留字符，已转义为 \# 以免编译中断", n, samples))

    # R10 裸变量补 $：只处理 \begin{document} 之后的正文区，导言区的宏定义不碰。
    anchor = _BEGIN_DOC_RE.search(text)
    if anchor:
        # 从 \begin{document} 之后起算：定位样例里不该混进定界符本身，
        # 否则 Toast 会写成「例：document} 若 p 是 q …」这种看不懂的东西。
        body = text[anchor.end():]
        body, n, samples = wrap_bare_math_variables(body)
        if n:
            text = text[: anchor.end()] + body
            notes.append(_note(
                "中文语境里单独成词的数学变量没包 $（卷面会排成正体字母），已自动补上定界符",
                n, samples))

    return text, notes