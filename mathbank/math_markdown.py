"""Conservative normalization for mixed Chinese text and LaTeX math.

The AI import paths occasionally return valid LaTeX commands without math
delimiters.  This module repairs only high-confidence math-shaped runs while
leaving ordinary prose and document-structure commands untouched.
"""

from __future__ import annotations

import re
from collections.abc import Callable


_MATH_RUN_RE = re.compile(r"[A-Za-z0-9\\{}_^+\-*/=<>|(),.:\[\]\t ]+")
_MATH_COMMAND_RE = re.compile(r"\\([A-Za-z]+)")
# 乘法星号：夹在两个数学操作数（数字/字母/右括号/右花括号/右中括号）之间。
_MATH_MUL_STAR_RE = re.compile(r"(?<=[A-Za-z0-9)}\]])\*(?=[A-Za-z0-9({\[])")
# 连续下划线（OCR/Word 常见的填空占位符）统一规范化为标准 LaTeX \fillin 宏。
_FILLIN_UNDERSCORE_RE = re.compile(r"(?<!\\)_{3,}")
# 转义后的连续下划线（AI 常把填空空位写成 `\_\_\_\_\_\_`，每个下划线都带反斜杠，
# 上面那条 `(?<!\\)` 判据因此漏掉）同样归一为 \fillin。
_FILLIN_ESCAPED_UNDERSCORE_RE = re.compile(r"(?:\\_){3,}")
# \fillin 之后允许出现的「中文句末标点 → ASCII 半角」映射（待办 #16）。
# 只服务于紧跟 \fillin 的那一个标点，中文正文里任何无关的标点都不在这个表里。
_FILLIN_TRAILING_PUNCTUATION = {
    "。": ".",
    "，": ",",
    "；": ";",
    "！": "!",
    "？": "?",
    "．": ".",
}
_TABLE_ENVIRONMENTS = {
    "tabular", "tabular*", "tabularx", "longtable", "tblr", "longtblr", "talltblr",
}
# These environments already enter math mode. Adding dollars makes their
# otherwise valid source fail when it is later exported to LaTeX.
_STANDALONE_MATH_ENVIRONMENTS = {
    "equation", "equation*", "align", "align*", "alignat", "alignat*",
    "gather", "gather*", "multline", "multline*", "displaymath", "math",
}
_INNER_MATH_ENVIRONMENTS = {
    "cases", "aligned", "alignedat", "gathered", "matrix", "pmatrix",
    "bmatrix", "Bmatrix", "vmatrix", "Vmatrix", "smallmatrix", "array", "split",
}
_ENVIRONMENT_TOKEN_RE = re.compile(r"\\(begin|end)\{([^{}\n]+)\}")
# 页标（跨页输入协议标记）与其在流水线跑飞后残留的变体。
# 变体来自「注释里的 `-` 被当成数学减号包进 $」这一历史 bug：
#   `<!-- MATHBANK_PDF_PAGE:3 -->`  ->  `<!$-- MATHBANK_PDF_PAGE:3 -->$`
# 两种形态一并认掉（老任务重新保存时也能自愈）。判据要求出现
# `MATHBANK_PDF_PAGE` 字样，且前后跨度有界，不会误吃正文。
_PAGE_MARKER_RE = re.compile(
    r"<![^\n]{0,8}?MATHBANK_PDF_PAGE[^\n]{0,48}?-->[ \t]*\$?",
    re.IGNORECASE,
)
# 字面「反斜杠 + n」= AI/OCR 常把换行写成两个字符。判据用 `(?![a-z])`：LaTeX 以
# `\n` 开头的真命令（`\nabla`、`\neq`、`\nu`、`\newline`、`\not`、`\nmid`）一律
# 小写，必须原样保留；后面跟大写字母/数字/标点/空白时不可能是控制词，按换行还原。
_LITERAL_NEWLINE_RE = re.compile(r"\\n(?![a-z])")
# 管道表格整行判据必须与 latex_normalize._is_pipe_row 完全一致，否则「补定界符」会
# 把 `| 数量 | 160 | x_1 |` 包成 `$| 数量 | 160 | x_1 |$`，让结构层再也认不出这是
# 表格行 —— 表格会当场丢行（三列比较表尤甚）。
_MD_PIPE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_NON_MATH_COMMANDS = {
    "begin",
    "bottomrule",
    "centering",
    "cline",
    "end",
    "hline",
    "item",
    "fillin",
    "includegraphics",
    "midrule",
    "multicolumn",
    "multirow",
    "paren",
    "renewcommand",
    "textbf",
    "toprule",
}


def _replace_with_placeholders(
    value: str,
    pattern: re.Pattern[str],
    placeholders: list[tuple[str, str]],
    transform: Callable[[str], str] | None = None,
) -> str:
    def save(match: re.Match[str]) -> str:
        marker = f"\ue000{len(placeholders)}\ue001"
        original = match.group(0)
        placeholders.append((marker, transform(original) if transform else original))
        return marker

    return pattern.sub(save, value)


def _is_escaped(value: str, index: int) -> bool:
    cursor = index - 1
    while cursor >= 0 and value[cursor] == "\\":
        cursor -= 1
    return (index - cursor - 1) % 2 == 1


def _find_matching_brace(value: str, start: int) -> int:
    """返回 ``value[start]`` 这个 ``{`` 对应的 ``}`` 下标；找不到时给 -1。"""
    depth = 0
    k = start
    n = len(value)
    while k < n:
        if value[k] == "{":
            depth += 1
        elif value[k] == "}":
            depth -= 1
            if depth == 0:
                return k
        k += 1
    return -1


def _replace_delimited_math(value: str, replace: Callable[[str], str]) -> str:
    """Visit complete math spans without treating escaped dollars as delimiters."""
    parts: list[str] = []
    cursor = 0
    copied = 0
    while cursor < len(value):
        opening = next((token for token in ("$$", "$", r"\(", r"\[")
                        if value.startswith(token, cursor)), None)
        if opening is None or _is_escaped(value, cursor):
            cursor += 1
            continue
        closing = {r"\(": r"\)", r"\[": r"\]"}.get(opening, opening)
        end = value.find(closing, cursor + len(opening))
        while end >= 0 and _is_escaped(value, end):
            end = value.find(closing, end + len(closing))
        if end < 0:
            cursor += len(opening)
            continue
        end += len(closing)
        parts.extend((value[copied:cursor], replace(value[cursor:end])))
        cursor = copied = end
    parts.append(value[copied:])
    return "".join(parts)


def _replace_latex_environments(value: str, replace: Callable[[str, str], str]) -> str:
    """Protect complete outer structures, including nested cases/matrices/tables."""
    recognized = _TABLE_ENVIRONMENTS | _STANDALONE_MATH_ENVIRONMENTS | _INNER_MATH_ENVIRONMENTS
    parts: list[str] = []
    copied = 0
    cursor = 0
    while match := _ENVIRONMENT_TOKEN_RE.search(value, cursor):
        cursor = match.end()
        kind, name = match.groups()
        if kind != "begin" or name not in recognized or _is_escaped(value, match.start()):
            continue
        stack = [name]
        for token in _ENVIRONMENT_TOKEN_RE.finditer(value, cursor):
            if _is_escaped(value, token.start()):
                continue
            action, nested_name = token.groups()
            if action == "begin":
                stack.append(nested_name)
            elif nested_name != stack[-1]:
                break  # Preserve malformed structure; do not guess a repair.
            else:
                stack.pop()
            if not stack:
                parts.extend((value[copied:match.start()], replace(value[match.start():token.end()], name)))
                cursor = copied = token.end()
                break
    parts.append(value[copied:])
    return "".join(parts)


def _protect_non_candidates(value: str) -> tuple[str, Callable[[str], str]]:
    placeholders: list[tuple[str, str]] = []
    protected = value
    protected = _replace_with_placeholders(
        protected,
        re.compile(r"```[\s\S]*?```"),
        placeholders,
    )
    protected = _replace_with_placeholders(
        protected,
        re.compile(r"`[^`\n]*`"),
        placeholders,
    )
    protected = _replace_with_placeholders(
        protected,
        re.compile(r"!\[[^\]\n]*\]\([^\n)]*\)"),
        placeholders,
    )
    protected = _replace_with_placeholders(
        protected,
        re.compile(r"\[\[MBM_[A-Za-z0-9_:-]+\]\]"),
        placeholders,
    )
    protected = _replace_with_placeholders(
        protected,
        re.compile(r"\[ILLUSTRATION_BOX:\s*[^\]\n]*\]", re.IGNORECASE),
        placeholders,
    )
    # 页标 <!-- MATHBANK_PDF_PAGE:N --> 必须**整体**保护。
    # 少了这一步，正则会把注释里的连字符当成数学减号，把 `<!--` 包成 `<!$-$`，
    # 于是题干里残留 `<!$-- MATHBANK_PDF_PAGE:3 -->$`：页标既没被清掉，又被塞进了
    # 一对 $（实测第 4 题就是这样）。页标是机器生成的结构标记，不属于数学内容。
    protected = _replace_with_placeholders(
        protected,
        re.compile(r"<!--\s*MATHBANK_PDF_PAGE[^>\n]*-->"),
        placeholders,
    )
    # 同理，任何 HTML 注释都不该被当成公式（`<!-- ... -->` 里常有连字符/数字）。
    protected = _replace_with_placeholders(
        protected,
        re.compile(r"<!--[^>\n]*-->"),
        placeholders,
    )
    # ⚠️ AI（尤其 MinerU 云端路线）常把换行写成**字面**「反斜杠 + n」。若不先还原成真换行，
    #    下一步的数学 run 补定界符会把这串字符当成高置信度数学：`_MATH_COMMAND_RE`
    #    匹配到 `\nA`（把 `nA` 当成 LaTeX 控制词），于是整段被包成 `$\nA$` 并且 `$` 还会
    #    和题干的纯文本行合并 —— 实测第 1 题正文直接变成 `（ ）$\nA \n$$\nB \n$…`，
    #    选项全毁。
    #    判据用 `(?![a-z])` 而非 `(?![a-zA-Z])`：LaTeX 以 `\n` 开头的真命令（`\nabla`、
    #    `\neq`、`\nu`、`\newline`、`\not`、`\nmid`）**一律小写**，所以「反斜杠+n+小写字母」
    #    才可能是真命令，必须原样保留；后面跟大写字母 / 数字 / 标点 / 空白时绝无可能是
    #    控制词，一律按换行还原。
    protected = _LITERAL_NEWLINE_RE.sub("\n", protected)
    # 逐行保护管道表格行：必须在还原字面换行**之后**，否则字面 `\n` 拼成的多行表格会被
    # 当成一整行吞掉；这些行的 `|` 是表格分隔符而不是绝对值，交给数学补定界符就会毁掉
    # 结构层判据（表格丢行）。
    protected = _replace_with_placeholders(protected, _MD_PIPE_ROW_RE, placeholders)

    def save(original: str) -> str:
        marker = f"\ue000{len(placeholders)}\ue001"
        placeholders.append((marker, original))
        return marker

    protected = _replace_delimited_math(protected, save)
    protected = _replace_latex_environments(
        protected,
        lambda environment, name: save(
            f"${environment}$" if name in _INNER_MATH_ENVIRONMENTS else environment
        ),
    )
    patterns = (
        re.compile(r"\\(?:begin|end)\{[^}\n]+\}"),
        re.compile(r"\\item\b"),
        re.compile(r"\\fillin\b"),
        re.compile(r"\\paren\b"),
        re.compile(r"\\textbf\{[^{}\n]*\}"),
        re.compile(r"\\includegraphics(?:\s*\[[^\]\n]*\])?\s*\{[^}\n]+\}"),
        re.compile(r"</?[A-Za-z][^>\n]*>"),
    )
    for pattern in patterns:
        protected = _replace_with_placeholders(protected, pattern, placeholders)

    def restore(result: str) -> str:
        for marker, original in reversed(placeholders):
            result = result.replace(marker, original)
        return result

    return protected, restore


def _is_high_confidence_math(value: str) -> bool:
    commands = {
        command.lower() for command in _MATH_COMMAND_RE.findall(value)
    } - _NON_MATH_COMMANDS
    if commands:
        return True
    if re.search(r"[A-Za-z0-9})\]]\s*[_^](?:\s*\{|\s*[A-Za-z0-9\\])", value):
        return True
    if re.search(
        r"[A-Za-z0-9})\]]\s*(?:=|<|>)\s*(?:[A-Za-z0-9({\[\\+\-])",
        value,
    ):
        return True
    if re.search(r"\b[A-Za-z]\s*\([^)]*[A-Za-z0-9_+\-,\\][^)]*\)", value):
        return True
    if re.search(r"\(\s*[+\-]?(?:\d+(?:\.\d+)?|[A-Za-z])\s*,[^)]*\)", value):
        return True
    return False


def _trim_run_to_command_structure(core: str) -> str:
    r"""把候选数学串裁到「最后一个完整的 LaTeX 命令结构」为止。

    ``_MATH_RUN_RE`` 的字符类**只含 ASCII**，所以题干里的中文（以及任何非 ASCII）
    天然是 run 的断点：``求 \underline{重点内容} 的值`` 会被切成
    ``" \underline{"`` 与 ``"} "`` 两段。旧实现在这里直接把整段包成
    ``$\underline{$``——反斜杠命令被从参数中间拦腰截断，定界符还多出一个，
    实测 ``\text{甲乙丙}`` 变 ``$\text{$甲乙丙}成立``、``\sqrt{甲}`` 变
    ``$\sqrt{$甲}``。

    判据不看 ASCII 与否，只看**结构是否闭合**：从左往右按 LaTeX 语法走一遍
    （控制词 + 可选 ``[...]`` + 可选 ``{...}``，花括号递归），要求末位落在
    「深度 0 且刚吃完一个完整单元」的位置；不满足就回退到上一个这样的位置。
    于是 ``\underline{``（缺右括号）整段不包，而 ``x=\frac{1}{2}``、
    ``\sqrt{x}+1`` 这些本来就完整的写法一字不动。
    """
    n = len(core)
    i = 0
    last_complete = 0          # 末位落在该下标时，前面的内容是一段合法数学
    while i < n:
        char = core[i]
        if char == "\\":
            command = _MATH_COMMAND_RE.match(core, i)
            if not command or _is_escaped(core, i):
                return core[:last_complete]
            i = command.end()
            # 可选的 `[...]` 与 `{...}` 参数都属于这个命令的一部分。
            while i < n and core[i] == "[":
                close = core.find("]", i + 1)
                if close < 0:
                    return core[:last_complete]
                i = close + 1
            while i < n and core[i] == "{":
                depth = 0
                k = i
                while k < n:
                    if core[k] == "{":
                        depth += 1
                    elif core[k] == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    k += 1
                if k >= n:
                    return core[:last_complete]   # 花括号没闭合 → 整个单元不完整
                i = k + 1
            last_complete = i
            continue
        if char in "^_":
            i += 1
            while i < n and core[i].isspace():
                i += 1
            if i < n and core[i] == "{":
                close = _find_matching_brace(core, i)
                if close < 0:
                    return core[:last_complete]
                i = close + 1
            elif i < n and (core[i].isalnum() or core[i] == "\\"):
                command = _MATH_COMMAND_RE.match(core, i)
                i = command.end() if command else i + 1
            else:
                return core[:last_complete]
            last_complete = i
            continue
        if char == "{":
            close = _find_matching_brace(core, i)
            if close < 0:
                return core[:last_complete]
            i = close + 1
            last_complete = i
            continue
        if char == "}":
            # 孤立的右括号：它属于 run 之外的结构，必须留在 $ 外面。
            return core[:last_complete]
        if char.isspace():
            i += 1
            continue
        i += 1
        last_complete = i
    return core[:last_complete]


def _wrap_math_run(match: re.Match[str]) -> str:
    raw = match.group(0)
    leading_length = len(raw) - len(raw.lstrip())
    trailing_length = len(raw) - len(raw.rstrip())
    leading = raw[:leading_length]
    core_end = len(raw) - trailing_length if trailing_length else len(raw)
    core = raw[leading_length:core_end]
    if not core:
        return raw
    wrapped = _trim_run_to_command_structure(core)
    if not wrapped or not _is_high_confidence_math(wrapped):
        return raw
    # 尾部（含被裁掉的残缺片段与其后空白）原样留在 $ 之外，绝不吞字。
    tail = raw[leading_length + len(wrapped):]
    return f"{leading}${wrapped}${tail}"


def _replace_star_with_cdot(match: re.Match[str]) -> str:
    """把乘法星号替换为 \\cdot；若后面紧跟字母则补空格，避免粘合成新命令。"""
    text = match.string
    next_char = text[match.end()] if match.end() < len(text) else ""
    if next_char.isalpha():
        return r"\cdot "
    return r"\cdot"


def _replace_math_operators(value: str) -> str:
    """把数学环境里的乘法 `*` 规范化为 `\\cdot`。

    只替换夹在操作数之间的星号（如 ``$a*x+b$`` → ``$a\\cdot x+b$``），
    保留上标/下标中的星号（如 ``$x^*$``）和已经是 ``\\cdot``/``\\times`` 的情况。
    """
    return _replace_delimited_math(
        value,
        lambda math: _MATH_MUL_STAR_RE.sub(_replace_star_with_cdot, math),
    )


# 非环境的 $$...$$ 块会被前端 KaTeX 以 display 模式渲染成居中独立行，是解析排版
# 松散（每个式子独占一行）的直接来源。真正的多行公式（aligned / cases / tabular
# 等环境，或含 `\\` 显式换行）必须保留，这里只降级"普通块"。
_DISPLAY_MATH_KEEP_RE = re.compile(r"\\begin\{|\\end\{|\\\\")


def _downgrade_display_span(span: str) -> str:
    """把安全的 ``$$...$$`` 块降级为行内 ``$...$``，其余原样返回。

    **跨行的块同样降级**：KaTeX 在 display 模式下本来就把裸换行当作空白，所以
    把换行压成空格不会改变公式语义，只是把"块级公式"换成"行内公式"，让它与
    前后文字排在同一行。之前只降级单行块，造成同一份解析里"简单式子被压平、
    跨行式子仍独立成行"的不一致。

    含 ``\\begin{...}`` 环境或 ``\\`` 显式换行的块是真正的多行公式，保留原样。
    """
    if len(span) < 4 or not span.startswith("$$") or not span.endswith("$$"):
        return span
    inner = span[2:-2]
    # 空块、或内部已出现 `$`（如 $$a$b$$）一律不降级——后者定界符配对本身可疑。
    if not inner.strip() or "$" in inner:
        return span
    if _DISPLAY_MATH_KEEP_RE.search(inner):
        return span
    flattened = re.sub(r"[ \t]*\n[ \t]*", " ", inner).strip()
    if not flattened:
        return span
    return f"${flattened}$"


def compact_solution_markdown(value: str) -> str:
    """Compact an answer/solution body so the preview stops looking airy.

    * ``$$...$$`` blocks with no math environment and no explicit ``\\\\`` break
      are downgraded to inline ``$...$`` — including blocks written across
      several lines — so the front end stops rendering every derivation step as
      a centred standalone line and formulas sit on the same line as the prose.
    * Runs of three or more newlines collapse to a single blank line.
    Fenced and inline code spans are preserved byte-for-byte.
    """

    if not isinstance(value, str) or not value:
        return value or ""
    placeholders: list[tuple[str, str]] = []
    protected = _replace_with_placeholders(value, re.compile(r"```[\s\S]*?```"), placeholders)
    protected = _replace_with_placeholders(protected, re.compile(r"`[^`\n]*`"), placeholders)
    protected = _replace_delimited_math(protected, _downgrade_display_span)
    protected = re.sub(r"\n{3,}", "\n\n", protected)
    for marker, original in reversed(placeholders):
        protected = protected.replace(marker, original)
    return protected


def _strip_page_markers(value: str) -> str:
    """删除跨页协议页标（含 `$` 污染变体）及其留下的空行。

    页标只用于把多页 OCR 结果拼成一份连续输入（供模型判断跨页合并），
    题干/解析里出现它即为脏数据：前端会原样显示 `<!-- ... -->`，
    导出到 LaTeX 后还会因为 `-` 被当作减号而产生排版噪声。
    """

    cleaned = _PAGE_MARKER_RE.sub("", value)
    if cleaned == value:
        return value
    # 整行只剩页标时，删完会留下一行空白；压掉多余空行，避免题干中间出现空洞。
    cleaned = re.sub(r"[ \t]*\n[ \t]*\n[ \t]*\n+", "\n\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def strip_page_markers(value: str) -> str:
    """Public entry used by the storage chain: 页标必须在结构层之前就清掉。"""
    return _strip_page_markers(value)


def restore_literal_newlines(value: str) -> str:
    """Public entry used by the storage chain: 字面「反斜杠 + n」还原成真换行。"""
    return _LITERAL_NEWLINE_RE.sub("\n", value)


# 「两个及以上反斜杠 + 紧跟字母」= 多转义一层的控制词。后面**必须紧跟字母**（不留空格），
# 因为 `A & B \\ C` 这种「硬换行 + 空格」是合法写法，不能碰。
_DOUBLED_CONTROL_WORD_RE = re.compile(r"\\{2,}(?=[A-Za-z])")


def restore_doubled_control_words(value: str) -> str:
    r"""把多转义的控制词压回单反斜杠（实测 MinerU 云路线整份 markdown 多转义一层）。

    ``$B _ { l } C _ { \\mathit { l } }$`` 里的 ``\\`` 在数学模式是**换行命令**，
    后面的 ``mathit`` 会被当正文排出来，公式直接错。判据要求反斜杠后紧跟字母，
    所以表格里的 ``A & B \\ C``（换行后带空格）不受影响；用 `{2,}` 而不是固定两个，
    是为了让「跑两遍」的入库链得到同一结果（幂等）。
    """
    if not value or "\\\\" not in value:
        return value
    return _DOUBLED_CONTROL_WORD_RE.sub(r"\\", value)


# ``\underline{...}`` 组内**只允许**出现这些成分时，它才是「填空占位横线」。
# 交替项顺序有讲究：带参数的命令必须先于其前缀（``\\hspace*`` 在 ``\\hspace`` 之前、
# ``\\qquad`` 在 ``\\quad`` 之前），否则正则会先吃掉短的那个、把 ``*`` 或剩余字母留在
# 串里，误判成「有内容」。空花括号 ``{}`` 也算占位（``\underline{}`` 就是条裸横线）。
# ``\\fillin`` 也在白名单里：``\underline{\fillin}`` 是历史入库形态（五种填空写法
# 汇流用例钉着它），而且第 3 步跑之前第 2 步已把所有 ``\fillin[...]`` 削成裸形。
_UNDERLINE_BLANK_TOKEN_RE = re.compile(
    r"[ \t\r\n\xa0　_]+"
    r"|\\hspace\*?\s*\{[^{}]*\}"
    r"|\\qquad"
    r"|\\quad"
    r"|\\fillin"
    r"|{}"
)


def _underline_group_is_blank(inner: str) -> bool:
    r"""判断 ``\underline{inner}`` 的 inner 是否为纯占位（空白 / \hspace / \quad / _）。

    必须**逐个 token 从位置 0 连续消费到串尾**。这里踩过一次坑：写成整串 ``*`` 量词
    再判 ``match.end() in (0, len(inner))`` 看似等价，实则量词遇到第一个不匹配的字符时
    会「成功停住」返回零宽匹配（``end == 0``），于是 ``\underline{x}`` 被当成纯占位照转
    —— 判据形同虚设，而且失败方向仍是「继续吃内容」，比不加判据更难发现。
    """
    pos = 0
    n = len(inner)
    while pos < n:
        match = _UNDERLINE_BLANK_TOKEN_RE.match(inner, pos)
        if not match or match.end() == pos:
            return False
        pos = match.end()
    return True


def _strip_underline_group(text: str) -> str:
    r"""把**纯占位**的 ``\underline{...}`` 换成 ``\fillin``，含嵌套花括号扫描。

    填空占位最常见的写法就是 ``\underline{\hspace{2em}}`` 这种带内层花括号的形态；
    早先用 ``[^}]*?`` 只吃到第一个 ``}``，会在 \fillin 后面留下一个游离的 ``}``，
    于是「手敲 \underline{\hspace{2em}}」与「直接写 \fillin」入库源码不一致。

    ⚠️ 但**不能**无条件转换整组。数学里 ``\underline{M}``（重复数位/共轭）、
    ``\underline{a+b}``、``\underline{x}`` 都是带实际内容的记号，一律换成 \fillin
    等于静默吃掉题干内容。判据只看组内是否「纯占位」：是 → \fillin；
    含任何字母、数字、运算符、中文 → 原样保留（连内层一起留，不递归处理）。
    """
    out: list[str] = []
    i = 0
    token = "\\underline"
    n = len(text)
    while i < n:
        if text.startswith(token, i):
            j = i + len(token)
            while j < n and text[j] in " \t":
                j += 1
            if j < n and text[j] == "{":
                depth = 0
                k = j
                while k < n:
                    if text[k] == "{":
                        depth += 1
                    elif text[k] == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    k += 1
                if k < n:
                    if _underline_group_is_blank(text[j + 1:k]):
                        out.append("\\fillin")
                    else:
                        # 有实际内容：整段原样搬走，指针必须跟着跳过这组，
                        # 否则会从内层重新扫描（\underline{a\underline{b}} 之类
                        # 嵌套写法会被二次判定）。
                        out.append(text[i:k + 1])
                        i = k + 1
                        continue
                    i = k + 1
                    continue
        out.append(text[i])
        i += 1
    return "".join(out)


def normalize_fillin_macro(text: str) -> str:
    """将题干中的任何下划线格式（\\underline{...}、\\fillin[...]、连续划线 ___）一律统一规范化为最纯粹的 \\fillin 宏"""
    if not text or not isinstance(text, str):
        return text or ""
    # 1. 替换连续下划线 ___ (3个及以上) 为 \fillin；转义形态 \_\_\_ 一并处理
    text = _FILLIN_ESCAPED_UNDERSCORE_RE.sub(r"\\fillin", text)
    text = _FILLIN_UNDERSCORE_RE.sub(r"\\fillin", text)
    # 2. 替换任何带参数的 \fillin[...] 为纯净的 \fillin
    text = re.sub(r'\\fillin\s*\[[^\]]*?\](?:\[[^\]]*?\])?', r'\\fillin', text)
    # 3. 替换任何形式的 \underline{...} 为纯净的 \fillin
    text = _strip_underline_group(text)
    # 4. 清理可能残留的额外右花括号 }
    text = re.sub(r'\\fillin\}', r'\\fillin', text)
    # 5. (待办 #3) 清理空的 \fillin{} / \fillin{ }，还原成裸 \fillin。
    #    花括号组本身 \s*\{\s*\} 已经要求「空」，所以 \fillin{内容} 天然匹配不上；
    #    后面这条负向断言只负责「{} 之后不能紧跟字母数字/汉字」，理由有二：
    #      a. 真实语料是 `\fillin{}.`（id=67/68）、`\fillin{}。`——若用 `(?!\S)`
    #         会因为紧邻非空白字符而整条漏掉，恰好放过要修的形态；
    #      b. 反过来必须拦住 `\fillin{}个数为` 这种空花括号后直接粘连中文的写法，
    #         否则会把花括号吃掉后变成 `\fillin个数为`（该形态属待办 #15 盘子，这里不许碰）。
    text = re.sub(r'\\fillin\s*\{\s*\}(?![\w\u4e00-\u9fff])', r'\\fillin', text)
    # 6. (待办 #16①) \fillin 之后紧贴（或隔空白）的中文句末标点统一转 ASCII。
    #    三条子规则都只锚在 \fillin 上，所以中文正文里任何无关的 。，；！？ 都不会被碰到。
    def _fillin_trailing_punctuation(match: re.Match[str]) -> str:
        # 回调返回值替换的是「整条匹配」，不是某个命名组，所以前缀要用 group("head")
        # 老老实实拼出来，不能只回一组。
        punct = match.group("punctuation")
        return match.group("head") + _FILLIN_TRAILING_PUNCTUATION[punct]

    # 6a 紧贴、普通态：真实语料 id=205 ``\fillin。``
    text = re.sub(
        r'(?P<head>\\fillin)(?P<punctuation>[。，；！？．])',
        _fillin_trailing_punctuation,
        text,
    )
    # 6b 隔空白、普通态：真实语料 id=30 ``\fillin ？``。
    #    刻意不含「，」——``\fillin ，`` 后面隔一个空格接正文时，把空格吃掉会粘连成
    #    ``\fillin,然后``，属于非必要的风格改动，不如不动。
    text = re.sub(
        r'(?P<head>\\fillin)(?P<gap>\s*)(?P<punctuation>[。；！？．])',
        _fillin_trailing_punctuation,
        text,
    )
    # 6c 紧贴、数学态 ``$\fillin$``：真实语料 id=288。
    #    同样不含「，」——``$\fillin$`` 后面那个逗号多半是正文的并列分隔符
    #    （如「值域是 $\fillin$，定义域是 R。」），不是填空的附属标点。
    text = re.sub(
        r'(?P<head>\\fillin\$)(?P<punctuation>[。；！？．])',
        _fillin_trailing_punctuation,
        text,
    )
    # 7. (待办 #16②) `\fillin .`（空格 + 英文句点）归一为紧贴的 `\fillin.`。
    #    放在第 6 步之后：中文句点已被翻成 ASCII，这里不会重复处理。
    text = re.sub(r'\\fillin\s+\.', r'\\fillin.', text)
    return text


def _canonicalize_fillin_outside_math(value: str) -> str:
    """只在数学态之外执行 ``normalize_fillin_macro``。

    已有 ``$...$`` / ``$$...$$`` / ``\\(...\\)`` 片段整体藏成占位符再原样还原，
    从而保住「``$a_1 + a_{23} = ___$`` 一个字都不许动」这条既有约定。
    """
    hidden: list[tuple[str, str]] = []

    def hide(span: str) -> str:
        marker = f"\ue002{len(hidden)}\ue003"
        hidden.append((marker, span))
        return marker

    scaffold = _replace_delimited_math(value, hide)
    scaffold = normalize_fillin_macro(scaffold)
    for marker, span in reversed(hidden):
        scaffold = scaffold.replace(marker, span)
    return scaffold


def normalize_question_math_markdown(value: str) -> str:
    """Wrap high-confidence naked LaTeX/math runs in inline delimiters.

    Existing math blocks, Markdown images, content-lock references, table
    environments and structural LaTeX commands are preserved byte-for-byte.
    Inner math environments are wrapped as a whole; standalone display math
    retains its original, exportable delimiters. Explicit typography such
    as ``\\mathbf`` or ``\\boldsymbol`` is never inferred from prose.
    """

    if not isinstance(value, str) or not value:
        return value or ""
    # 页标是**跨页输入协议**的产物，只在拆题输入里有意义，绝不能进题干/解析
    # （提示词 4.1 明确要求「不得出现在输出题干中」，但模型偶尔照抄）。
    # 在补定界符**之前**清掉，否则注释里的连字符会被包成 `$-$`（见 _protect_non_candidates）。
    value = _strip_page_markers(value)
    # 先把数学态**之外**的填空占位统一成 \fillin，再做保护与补定界符。
    # ⚠️ 必须绕过已有 $...$ 片段（里面的 `___` 是数学下标，历史上就约定不动它），
    # 又必须早于 _protect_non_candidates：\fillin 是受保护的结构命令，而 "____"、
    # "\underline{...}" 当时还算普通数学字符。放晚会让「输入已有 \fillin」与
    # 「输入是 ____」走出两条不同的补定界符判定（实测 `则 a*b = ____` 一处被包成
    # $a\cdot b = \fillin$、另一处整段留在文本态），三条录入路径的入库源码永远对不齐。
    value = _canonicalize_fillin_outside_math(value)
    protected, restore = _protect_non_candidates(value)
    repaired_lines = [
        _MATH_RUN_RE.sub(_wrap_math_run, line)
        for line in protected.splitlines(keepends=True)
    ]
    result = restore("".join(repaired_lines))
    # 在定界符补齐之后再规范化运算符，避免重复包裹。
    return _replace_math_operators(result)
