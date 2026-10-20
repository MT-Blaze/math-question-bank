"""Pure helpers for conservative single-question type detection."""

import re


QUESTION_FORM_CHOICE = "choice"
QUESTION_FORM_FILL_IN_BLANK = "fill_in_blank"
QUESTION_FORM_DETAILED_ANSWER = "detailed_answer"
QUESTION_FORM_UNKNOWN = "unknown"


_CHOICES_ENV_PATTERN = re.compile(
    r"\\begin\s*\{\s*choices\s*\}",
    re.IGNORECASE,
)
_FILLIN_PATTERN = re.compile(r"\\fillin\b", re.IGNORECASE)

# 【答案】块首行：答案字母的唯一归属地。多小问答案（``（1）A（2）B``）与文字答案
# （``见解析``）都不算纯选项答案，宁可漏改也不把填空题改成多选题。
_ANSWER_BLOCK_RE = re.compile(r"【(?:参考答案|答案)】[ \t]*[：:]?[ \t]*([^\n]*)")
_ANSWER_LETTER_NOISE_RE = re.compile(r"[A-H\s，、。;；:：/|（）()\[\]{}\-—~或和与]*")
# 收束句里的最终答案（``故选：AD``）。真实拆卷里很常见：解析末尾有结语、但没有
# 【答案】块，旧实现只看【答案】，于是「AD 配单选题」漏改。
_CHOICE_ANSWER_MARK_RE = re.compile(
    r"(?:故选|应选|答案选|正确答案是)\s*[：:]?\s*"
    r"((?:[A-H](?:\s*[、,，/|]\s*|\s*)){1,8})"
    r"(?=[\s。；;.．，,]|$)"
)
# 题干里的行首选项标号（纯文本选项区，结构层包装之前也只有这个证据）。
_CHOICE_CONTENT_LINE_RE = re.compile(r"(?m)^[ \t]*([A-H])[ \t]*[.．、,，:：)）]")


def _choice_mark_letters(answer: str) -> str:
    r"""取解析收束句（``故选：AD``）里的选项字母，取最后一个收束句。"""
    if not answer:
        return ""
    chosen = None
    for chosen in _CHOICE_ANSWER_MARK_RE.finditer(answer):
        pass
    if chosen is None:
        return ""
    return "".join(dict.fromkeys(re.findall(r"[A-H]", chosen.group(1))))


def answer_choice_letters(answer: str) -> str:
    r"""从解析里取**独立**的选项字母串（如 ``ACD``），取不到返回空串。

    先看 ``【答案】`` 块（要求整行除字母与分隔符外没有别的内容），再看解析收束句
    ``故选：AD``。两处都按出现顺序去重，``A、A`` 不算第二个答案。
    """
    if not answer:
        return ""
    match = _ANSWER_BLOCK_RE.search(answer)
    if match:
        line = match.group(1).strip()
        if line and not _ANSWER_LETTER_NOISE_RE.sub("", line):
            letters = re.findall(r"[A-H]", line)
            if letters:
                return "".join(dict.fromkeys(letters))
    return _choice_mark_letters(answer)


def enforce_multi_choice_from_answer(question_type: str, content: str, answer: str) -> str:
    r"""答案给出 ≥2 个独立选项字母时，把单选题强制纠正为多选题。

    收束句 ``故选：AD`` 是**强证据**：只有选择题才会这样收尾，因此即使题型被拆成
    填空题/解答题也直接纠正（真实拆卷里出现过「答案 BCD、题型填空」）。``【答案】``
    块里的纯字母答案还要求有选择题语境（单选、``choices`` 环境或行首 A/B/C 选项区），
    避免把「A、B 两点」一类的文字答案误升成多选题。
    """
    current = question_type if isinstance(question_type, str) else ""
    if current == "multi_choice":
        return question_type
    if len(_choice_mark_letters(answer)) >= 2:
        return "multi_choice"
    letters = answer_choice_letters(answer)
    if len(letters) < 2:
        return question_type
    text = str(content or "")
    if current == "single_choice" or _CHOICES_ENV_PATTERN.search(text):
        return "multi_choice"
    starts = {match.group(1) for match in _CHOICE_CONTENT_LINE_RE.finditer(text)}
    if {"A", "B", "C"} <= starts:
        return "multi_choice"
    return question_type


def detect_structured_question_form(content: str) -> str | None:
    """Return a definitive form only when normalized LaTeX structure proves it.

    A choices environment takes precedence because a choice option can itself
    contain a blank-like expression without changing the enclosing question
    into a fill-in-the-blank item.
    """

    normalized = str(content or "")
    if _CHOICES_ENV_PATTERN.search(normalized):
        return QUESTION_FORM_CHOICE
    if _FILLIN_PATTERN.search(normalized):
        return QUESTION_FORM_FILL_IN_BLANK
    return None


def normalize_ai_question_form(value: object) -> str:
    """Collapse model output to a coarse form that cannot encode single/multi."""

    normalized = str(value or "").strip().lower()
    mapping = {
        "choice": QUESTION_FORM_CHOICE,
        "choice_question": QUESTION_FORM_CHOICE,
        "single_choice": QUESTION_FORM_CHOICE,
        "multi_choice": QUESTION_FORM_CHOICE,
        "选择题": QUESTION_FORM_CHOICE,
        "单选题": QUESTION_FORM_CHOICE,
        "多选题": QUESTION_FORM_CHOICE,
        "fill_in_blank": QUESTION_FORM_FILL_IN_BLANK,
        "fill-in-blank": QUESTION_FORM_FILL_IN_BLANK,
        "填空题": QUESTION_FORM_FILL_IN_BLANK,
        "detailed_answer": QUESTION_FORM_DETAILED_ANSWER,
        "解答题": QUESTION_FORM_DETAILED_ANSWER,
        "unknown": QUESTION_FORM_UNKNOWN,
        "未知": QUESTION_FORM_UNKNOWN,
    }
    return mapping.get(normalized, QUESTION_FORM_UNKNOWN)
