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

__all__ = ["repair_latex_before_compile", "MAX_NOTE_SAMPLES"]

# 每条规则最多附几个样例进上报文案：够定位即可，整卷样例会把响应头撑爆。
MAX_NOTE_SAMPLES = 3
_SAMPLE_CHARS = 72

# 汉字与全角标点：xeCJK 下它们是「字母」，紧跟在控制序列后会并入宏名。
_CJK_LIKE = r"[\u2e80-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]"

# 题库正文里会出现、且必须与后随汉字隔开的 exam-zh 专有宏。
# \paren 在选择题分支已被 clean_content_for_latex 展开成字面括号，这里防的是
# 其它题型漏展开的存量数据；补 {} 对可选参数型宏是无害空组。
_CJK_EATEN_MACROS = ("fillin", "paren")

# 行式数学环境：这些环境里「单个反斜杠 + x/y/z」几乎必然是丢失的行分隔 \\。
# \x \y \z 在 LaTeX 里都不是合法单字母宏，误伤面为零；其它字母（\i \j \o …）
# 确实存在，一律不碰，只上报。
_ROW_ENVS = (
    "cases", "dcases", "rcases", "aligned", "align", "alignat", "gather",
    "split", "array", "matrix", "pmatrix", "bmatrix", "vmatrix", "Vmatrix",
    "smallmatrix", "subarray",
)
_LOST_ROWBREAK_LETTERS = "xyz"

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
_ROW_ENV_RE = re.compile(
    r"(\\begin\{(?:%s)\}\*?(?:\[[^\]]*\])?)(.*?)(\\end\{(?:%s)\}\*?)"
    % ("|".join(_ROW_ENVS), "|".join(_ROW_ENVS)),
    re.S,
)
_LOST_ROWBREAK_RE = re.compile(r"(?<!\\)\\([%s])(?![A-Za-z])" % _LOST_ROWBREAK_LETTERS)
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

    return text, notes