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
    protected = re.sub(r"\\n(?![a-z])", "\n", protected)

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


def _wrap_math_run(match: re.Match[str]) -> str:
    raw = match.group(0)
    leading_length = len(raw) - len(raw.lstrip())
    trailing_length = len(raw) - len(raw.rstrip())
    leading = raw[:leading_length]
    trailing = raw[len(raw) - trailing_length :] if trailing_length else ""
    core_end = len(raw) - trailing_length if trailing_length else len(raw)
    core = raw[leading_length:core_end]
    if not core or not _is_high_confidence_math(core):
        return raw
    return f"{leading}${core}${trailing}"


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
    protected, restore = _protect_non_candidates(value)
    # 把 OCR/Word 里常见的 "____" 连续下划线统一成标准填空宏 \fillin，
    # 避免前端把下划线当成普通文本渲染成 "_ _ _ _"。
    protected = _FILLIN_UNDERSCORE_RE.sub(r"\\fillin", protected)
    repaired_lines = [
        _MATH_RUN_RE.sub(_wrap_math_run, line)
        for line in protected.splitlines(keepends=True)
    ]
    result = restore("".join(repaired_lines))
    # 在定界符补齐之后再规范化运算符，避免重复包裹。
    return _replace_math_operators(result)
