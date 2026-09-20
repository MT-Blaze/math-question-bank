"""AI 拆题输出的可数不变量守恒校验。

`lock_visible_math` 已经保证**公式原文**不会在拆题时丢失或串题，但它管不到另外几类
同样致命、却完全静默的改写：

- 选项被合并或丢掉（四选一变成三选一，卷面看着正常，答案必错）；
- 表格整张消失（实测 OCR/模型常写漏分隔行那一格）；
- `\\fillin` 下划线被吞（填空题退化成没有空可填）；
- `[插图待补: 图N]` 占位符凭空多出或对不上（说明模型在编造图形位置）。

这些都不会触发锁异常，只能靠**数量对账**发现。判据刻意只用「两边都用同一种朴素语法
书写」的量——选择题环境、`\\fillin`、`tabular`、占位符字面量都由我们的提示词强制要求
（见 `prompts.COMMON_OCR_PROMPT` 第 3/5/6 条），所以不存在「源侧本来就不写」的系统性误报。

⚠️ 只报告，不阻断：这类比对是启发式的，宁可让用户核对一道正常题，也不能因为一次
误判把整卷白花的解析钱废掉。标记 `_fidelity_review` 与既有的 `_image_review` 同一口径。
"""

from __future__ import annotations

import re
from typing import Any


# 我们自己的录入链统一用 choices 环境（prompts 第 3 条），不接受其它写法。
# ⚠️ 不能用 `^\s*\\item` + MULTILINE：切片与题面都是**去掉空白**后的文本，行首锚点
#    永远匹配不上，实测会让 items 恒为 0、丢选项检测整体失效。`\item` 本身以反斜杠
#    起头即边界，后面只允许空白再接非字母（排除 `\itemize`）。
_ITEM_RE = re.compile(r"\\item(?![A-Za-z])")
_CHOICES_BEGIN_RE = re.compile(r"\\begin\{choices\}")
_FILLIN_RE = re.compile(r"\\fillin\b")
_TABULAR_BEGIN_RE = re.compile(
    r"\\begin\{(?:tabular|tabularx|longtable|tblr|longtblr|talltblr)\b"
)
# 占位符两种形态都要认：带图号 `[插图待补: 图12]` 与无图号 `[插图待补]`。
_FIGURE_PLACEHOLDER_RE = re.compile(r"\[插图待补(?::\s*(?:图)?(\d+))?\]")

_FIELD_KEYS = ("content", "answer_markdown")
_WHITESPACE_RE = re.compile(r"\s+")
# 锚点上限：够区分同卷题目，又不至于被一处改写就整体匹配不上。
_ANCHOR_LIMIT = 24
# 题干前缀短于这个长度就不拿来定位——必然错位。
_MIN_ANCHOR_CHARS = 8


def _strip_space(value: str) -> str:
    """压掉所有空白但**保留反斜杠**：定位与计数共用这一份文本。

    ⚠️ 定位用的归一化与计数用的文本必须由同一个函数产出，否则切片下标会错位到别的题目
    上。更早的版本一侧剥掉 `\\begin` 的反斜杠、另一侧保留，两侧永远匹配不上，每道题都
    定位失败、自检静默空转——这类失效不报错，只会让功能悄悄消失。这里只去空白，因此
    `\\begin{choices}` 原样保留，切片可以直接送去 `_count_features`。
    """

    return _WHITESPACE_RE.sub("", str(value or ""))


def _anchor_of(stem: str) -> str:
    r"""取题干锚点前缀：只去空白，保留 LaTeX 命令名与中文。

    ⚠️ 严禁删掉整条命令。曾用 `re.sub(r"\\[A-Za-z]+\\s*", "")` 连命令名一起删，
    `\\begin` 里的 `\\b` 遂被当成退格符，源侧变成退格 + `egin{choices}`，而输出侧是正常的
    `\\begin{choices}`，于是每道题都定位失败。
    """

    return _strip_space(stem)[:_ANCHOR_LIMIT]


def _count_features(text: str) -> dict[str, Any]:
    """统计一段文本里的可数不变量。"""

    source = text if isinstance(text, str) else ""
    return {
        "choices": len(_CHOICES_BEGIN_RE.findall(source)),
        "items": len(_ITEM_RE.findall(source)),
        "fillins": len(_FILLIN_RE.findall(source)),
        "tables": len(_TABULAR_BEGIN_RE.findall(source)),
        "figures": {
            # 无图号的占位符折叠成空串，避免把「编号方式不同」误判成「内容不同」
            (match.group(1) or "")
            for match in _FIGURE_PLACEHOLDER_RE.finditer(source)
        },
    }


def _question_text(question: dict[str, Any]) -> str:
    return "\n".join(
        str(question.get(key) or "")
        for key in _FIELD_KEYS
        if isinstance(question.get(key), str)
    )


def _common_prefix(a: str, b: str, limit: int = _ANCHOR_LIMIT) -> str:
    out: list[str] = []
    for index in range(min(len(a), len(b), limit)):
        if a[index] != b[index]:
            break
        out.append(a[index])
    return "".join(out)


def _split_source_by_questions(
    source_text: str, questions: list[dict[str, Any]]
) -> list[str]:
    r"""按题干前缀在源文里定位每道题的切片，避免整卷汇总导致「一题丢、全卷报」。

    ⚠️ 锚点只用于**找起点**，绝不能拿它当右边界：模型常把题干改写得更短（去掉选项环境、
    合并小问），此时最长公共前缀会在选项中间停下，切出来的源文片段缺尾，数量对账必然
    漏判（实测四选一被切成三项，正好抵消了「丢一项」这个缺陷）。右边界一律取源文里
    **下一道题的起点**——题目顺序与分组怎么变都不影响这一点。
    找不到锚点的题返回空切片：该题不参与对账，也绝不拿别人的数字误报。
    返回值与 ``questions`` 等长、按下标对齐。
    """

    source = _strip_space(source_text)
    total = len(questions or [])
    if not source:
        return [""] * total
    starts: list[int] = []
    cursor = 0
    for question in questions:
        stem = _strip_space(
            str(question.get("content") or "") if isinstance(question, dict) else ""
        )
        probe = stem[:_MIN_ANCHOR_CHARS]
        if len(probe) < _MIN_ANCHOR_CHARS:
            starts.append(-1)
            continue
        position = source.find(probe, cursor)
        if position < 0:
            # 允许回退到全文重找：模型偶尔调整题目顺序或把题号挪进正文。
            position = source.find(probe)
        if position < 0:
            starts.append(-1)
            continue
        anchor = _common_prefix(stem, source[position:])
        if len(anchor) < _MIN_ANCHOR_CHARS:
            starts.append(-1)
            continue
        starts.append(position)
        cursor = position + len(anchor)
    located = sorted(p for p in starts if p >= 0)
    slices: list[str] = []
    for start in starts:
        if start < 0:
            slices.append("")
            continue
        nxt = min((p for p in located if p > start), default=len(source))
        slices.append(source[start:nxt])
    return slices


def _diff_reasons(
    expected: dict[str, Any], actual: dict[str, Any]
) -> list[str]:
    reasons: list[str] = []

    missing = expected["figures"] - actual["figures"]
    extra = actual["figures"] - expected["figures"]
    if missing:
        label = "、".join(sorted(("图" + n) if n else "未编号" for n in missing))
        reasons.append(f"插图占位符丢失（{label}）")
    elif extra:
        label = "、".join(sorted(("图" + n) if n else "未编号" for n in extra))
        reasons.append(f"插图占位符凭空多出（{label}）")

    if expected["choices"] and not actual["choices"]:
        reasons.append("选择题 choices 环境丢失")
    elif expected["items"] and actual["items"] < expected["items"]:
        # `\item` 减少与 choices 环境丢失互斥上报：模型把选项改写成 A. B. C. 纯文本时
        # 两项同时归零，各报一条会把同一个问题拆成两个假问题。
        reasons.append(
            f"选项变少（源 {expected['items']} 项 → 输出 {actual['items']} 项）"
        )
    if expected["tables"] and actual["tables"] < expected["tables"]:
        reasons.append(f"表格数量减少（源 {expected['tables']} → 输出 {actual['tables']}）")
    if expected["fillins"] and actual["fillins"] < expected["fillins"]:
        reasons.append(
            f"填空下划线减少（源 {expected['fillins']} → 输出 {actual['fillins']}）"
        )
    return reasons


def check_split_output_fidelity(
    questions: list[dict[str, Any]],
    source_text: str = "",
) -> dict[str, Any]:
    """给每题打 `_fidelity_review` 标记，并返回一份汇总供日志/diagnostics 使用。

    ``source_text`` 是喂给拆题模型的整卷源文（加锁后的 model_source，与卡片所见同口径）。
    没有源文时直接跳过——宁可不报，也不拿整卷总量冒充单题。
    """

    report: dict[str, Any] = {"review_count": 0, "checked": 0, "mode": "skipped"}

    if not isinstance(questions, list) or not questions:
        return report
    if not source_text:
        return report

    per_question_sources = _split_source_by_questions(source_text, questions)
    notes: list[str] = []
    checked = 0
    review_count = 0
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            continue
        source_slice = (
            per_question_sources[index]
            if index < len(per_question_sources)
            else ""
        )
        if not source_slice:
            continue
        expected = _count_features(source_slice)
        actual = _count_features(_question_text(question))
        reasons = _diff_reasons(expected, actual)
        checked += 1
        if reasons:
            merged = sorted(set(question.get("_fidelity_review") or []) | set(reasons))
            question["_fidelity_review"] = merged
            review_count += 1
            notes.append(f"第 {index + 1} 题（{'；'.join(reasons)}）")
        elif question.get("_fidelity_review"):
            question["_fidelity_review"] = sorted(question["_fidelity_review"])

    report["mode"] = "per_question"
    report["checked"] = checked
    report["review_count"] = review_count
    report["unmatched"] = len(questions) - checked
    report["notes"] = notes
    return report


def format_fidelity_log(report: dict[str, Any]) -> str:
    """把结果压成一行日志，风格对齐既有的 `[配图自检]`。"""

    if not isinstance(report, dict) or report.get("mode") != "per_question":
        return "[内容自检] 未取得源文，本次跳过选项/表格/填空/插图占位符对账。"
    checked = report.get("checked") or 0
    unmatched = report.get("unmatched") or 0
    scope = f"已对账 {checked} 题"
    if unmatched:
        # 定位失败必须说出来：否则「全部正常」会被误读成覆盖了一整卷。
        scope += f"（{unmatched} 题定位失败已跳过）"
    notes = report.get("notes") or []
    if not notes:
        return f"[内容自检] {scope}，选项/表格/填空/插图占位符数量未发现异常。"
    return (
        f"[内容自检] {scope}；以下题目的内容与原卷数量不一致，建议人工核对："
        + "；".join(notes[:12])
        + ("…" if len(notes) > 12 else "")
    )


__all__ = [
    "check_split_output_fidelity",
    "format_fidelity_log",
]
