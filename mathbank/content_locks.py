"""Lossless source-fragment locks for AI-assisted document splitting.

The model still sees each formula in its original sentence.  It returns only a
stable reference, and the server restores the exact source bytes afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


_LOCK_TAG = "mathbank-math"


class ContentLockIntegrityError(ValueError):
    """Raised when a splitting response loses or duplicates protected content."""

    def __init__(
        self,
        message: str,
        missing: list[str] | tuple[str, ...] = (),
        duplicated: list[str] | tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.missing = list(missing)
        self.duplicated = list(duplicated)


@dataclass(frozen=True)
class ContentLock:
    lock_id: str
    original: str


def _is_escaped(value: str, index: int) -> bool:
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and value[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def _find_math_end(value: str, start: int, delimiter: str) -> int:
    cursor = start + len(delimiter)
    while cursor < len(value):
        end = value.find(delimiter, cursor)
        if end < 0:
            return -1
        if not _is_escaped(value, end):
            return end + len(delimiter)
        cursor = end + len(delimiter)
    return -1


_MATH_ENVIRONMENTS = (
    "equation", "equation*", "align", "align*", "gather", "gather*",
    "aligned", "alignedat", "gathered", "split", "multline", "multline*",
    "eqnarray", "eqnarray*", "displaymath", "math", "array", "cases",
    "matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix",
    "smallmatrix",
)


def _next_math_span(source: str, cursor: int) -> tuple[int, int] | None:
    candidates: list[tuple[int, str, str]] = []
    for opening, closing in (("$$", "$$"), ("$", "$"), (r"\(", r"\)"), (r"\[", r"\]")):
        start = source.find(opening, cursor)
        while start >= 0 and _is_escaped(source, start):
            start = source.find(opening, start + len(opening))
        if start >= 0:
            candidates.append((start, opening, closing))
    for environment in _MATH_ENVIRONMENTS:
        opening = rf"\begin{{{environment}}}"
        start = source.find(opening, cursor)
        while start >= 0 and _is_escaped(source, start):
            start = source.find(opening, start + len(opening))
        if start >= 0:
            candidates.append((start, opening, rf"\end{{{environment}}}"))
    if not candidates:
        return None
    start, opening, closing = min(candidates, key=lambda item: (item[0], -len(item[1])))
    end = _find_math_end(source, start, closing)
    if end < 0:
        return None
    return start, end


def lock_visible_math(value: str, scope: str) -> tuple[str, list[ContentLock]]:
    """Wrap common balanced TeX math forms while keeping formulas visible."""
    source = str(value or "")
    safe_scope = re.sub(r"[^A-Za-z0-9_-]", "", str(scope or "DOCX"))[:48] or "DOCX"
    parts: list[str] = []
    locks: list[ContentLock] = []
    cursor = 0
    formula_index = 0

    while cursor < len(source):
        span = _next_math_span(source, cursor)
        if span is None:
            parts.append(source[cursor:])
            break
        start, end = span
        original = source[start:end]
        if not original.strip():
            parts.append(source[cursor:end])
            cursor = end
            continue

        formula_index += 1
        lock_id = f"MBM_{safe_scope}_{formula_index:04d}"
        parts.append(source[cursor:start])
        parts.append(f'<{_LOCK_TAG} id="{lock_id}">{original}</{_LOCK_TAG}>')
        locks.append(ContentLock(lock_id=lock_id, original=original))
        cursor = end

    return "".join(parts), locks


def _lock_reference_pattern(lock_id: str) -> tuple[re.Pattern, int]:
    r"""容忍模型对 ID 的三种无害改写：大小写漂移、下划线后夹空格、前导零被吃掉。

    ``MBM_27b26bd6d24742eb_0003`` 实测会被写成 ``..._3``、``MBM_27B26..._0003``，
    或在换行处断开。这些都不是「公式丢了」，硬判丢失会让整卷拆题失败（实测 Word
    路线 20 个锁丢 2 个就整卷报错）。序号按整数比较，且两侧语法（``]]`` / 引号）
    仍然是边界，所以**真的没写回来的锁依旧匹配不上**，安全属性不变。
    """
    prefix, _, number = lock_id.rpartition("_")
    return re.compile(
        re.escape(prefix) + r"_\s*0*(\d{1,4})",
        re.IGNORECASE,
    ), int(number)


def _lock_matches(pattern: re.Pattern, expected: int, text: str, span: tuple[int, int]):
    """在 ``span`` 区间内找该锁的引用；序号必须完全等于 expected。"""
    for match in pattern.finditer(text, span[0], span[1]):
        if int(match.group(1)) == expected:
            return match
    return None


def _restore_lock_in_text(value: str, lock: ContentLock) -> tuple[str, int, bool]:
    text = str(value or "")
    pattern, expected = _lock_reference_pattern(lock.lock_id)
    token_matches = []
    tag_matches = []
    for match in re.finditer(r"\[\[([^\[\]]*)\]\]", text):
        ref = _lock_matches(pattern, expected, match.group(1), (0, len(match.group(1))))
        if ref:
            token_matches.append(match)
    for match in re.finditer(
        rf"<{_LOCK_TAG}\b[^>]*\bid\s*=\s*(['\"])([^'\"]*)\1[^>]*>(.*?)</{_LOCK_TAG}\s*>",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    ):
        if _lock_matches(pattern, expected, match.group(2), (0, len(match.group(2)))):
            tag_matches.append(match)
    count = len(token_matches) + len(tag_matches)
    modified = any(match.group(3) != lock.original for match in tag_matches)
    if count == 1:
        if token_matches:
            text = text[: token_matches[0].start()] + lock.original + text[token_matches[0].end():]
        else:
            text = text[: tag_matches[0].start()] + lock.original + text[tag_matches[0].end():]
    return text, count, modified


_RESTORE_FIELDS = ("content", "answer_markdown")


def _snapshot_fields(questions: list[dict[str, Any]]) -> list[str]:
    """替换前的全部字段原文，用于判断模型是否把公式逐字带回了原处。"""
    return [
        str(question.get(field))
        for question in questions
        for field in _RESTORE_FIELDS
        if isinstance(question.get(field), str)
    ]


def _verbatim_returned_locks(fields: list[str], locks: list[ContentLock]) -> set[str]:
    """筛出「id 没写回，但公式本体一字不差地出现在输出里」的锁。

    实测 Word 路线的拆题模型有概率直接把 `<mathbank-math>` 里的公式原样抄回、省掉
    定位标记。公式本身没有丢，此时整卷报错会让用户白付一次 MinerU/Word 解析钱。

    只认**无歧义**的那一种：这条原文在整份输出里只出现一次，且没有别的锁共用同一份
    原文。两处都满足才谈得上「放回了原处」；同一条公式被两道题引用时无法判断归属，
    仍旧按丢失中断（与旧行为一致），不做猜测式恢复。
    """
    originals = [lock.original for lock in locks]
    joined = "\x00".join(fields)
    safe: set[str] = set()
    for lock in locks:
        text = lock.original
        if not text.strip() or originals.count(text) != 1:
            continue
        if joined.count(text) == 1:
            safe.add(lock.lock_id)
    return safe


def restore_visible_math(
    questions: list[dict[str, Any]],
    locks: list[ContentLock],
) -> dict[str, int]:
    """Restore every lock exactly once across question content and original answers."""
    report = {
        "math_locks_created": len(locks),
        "math_locks_restored": 0,
        "math_locks_overwritten": 0,
        "math_locks_implicit": 0,
        "math_locks_missing": 0,
        "math_locks_duplicated": 0,
    }
    missing: list[str] = []
    duplicated: list[str] = []
    verbatim = _verbatim_returned_locks(_snapshot_fields(questions), locks)

    for lock in locks:
        occurrences: list[tuple[dict[str, Any], str, str, int, bool]] = []
        total = 0
        for question in questions:
            for field in ("content", "answer_markdown"):
                field_value = question.get(field, "")
                if not isinstance(field_value, str):
                    continue
                restored, count, modified = _restore_lock_in_text(field_value, lock)
                if count:
                    occurrences.append((question, field, restored, count, modified))
                    total += count
        if total == 0:
            if lock.lock_id in verbatim:
                report["math_locks_implicit"] += 1
                continue
            missing.append(lock.lock_id)
            continue
        if total != 1:
            duplicated.append(lock.lock_id)
            continue
        question, field, restored, _count, modified = occurrences[0]
        question[field] = restored
        report["math_locks_restored"] += 1
        if modified:
            report["math_locks_overwritten"] += 1

    report["math_locks_missing"] = len(missing)
    report["math_locks_duplicated"] = len(duplicated)
    if missing or duplicated:
        details = []
        if missing:
            details.append("丢失 " + ", ".join(missing[:6]))
        if duplicated:
            details.append("重复 " + ", ".join(duplicated[:6]))
        raise ContentLockIntegrityError(
            "AI 拆题时未完整保留公式定位标记（" + "；".join(details) + "），"
            "系统已停止本次结果，避免公式静默丢失或串题。",
            missing=missing,
            duplicated=duplicated,
        )
    return report
