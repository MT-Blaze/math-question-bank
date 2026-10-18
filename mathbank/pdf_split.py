"""两段式试卷（前半纯题干原卷 + 后半含答案解析）的切分检测。

相当一部分真题 PDF 把**同一份卷子印了两遍**：前半是原卷（纯题干、无答案），
后半是「题干 + 答案 + 详解」。整本丢给拆题模型会得到两份重复的题目卡片；更麻烦的是
同一道题的插图在两部分各出现一次，模型很容易把整批图挂到最后一题上
（真实样本：大庆实验中学 2023-2024 / 2024-2025、东北师大附中 2024-2025）。

本模块只做一件事：**用文本层判定「解析段」从第几页开始**，调用方据此把前半段丢掉。
判据全部基于「文本层 + 页码」，不调 AI、不触碰网络，因此可在上传瞬间完成。

判据（六道闸门，全部通过才给结论 —— 猜错会丢题，宁可退回整本解析）：

1. 前半至少 2 页、后半至少 3 页；
2. 解析段必须「持续」：其内至少一半的页带逐题标记（【答案】/【详解】/【解析】…）；
3. 前半**一整页都不能**出现逐题标记（保证切在真正的分界上，而不是切在中间）；
4. 前半的题号必须**全部**能在后半找到（直接防「前半有、后半没有」的丢题）；
5. 两边的题号都不少于 3 个；
6. **后半的净字数 ≥ 前半的 2 倍** —— 后半是「题干+解析」而不是纯答案；
7. **后半确实重印了题干**：后半里「题号 → 首个逐题标记」之间的净字数中位数 ≥ 12。

第 7 条专治「前半原题 + 后半只给答案解析、不重印题干」的卷子 —— 这种卷子
**过得了前六条**（把三份真实卷的后半扣掉题干份额来模拟，长度比仍有 2.43 / 3.16 / 2.75），
但跳页会把题干整个丢掉。真实样本该值的中位数是 57 / 59 / 58 字，不重印题干的形态是 0。

第 6 条是替换「题干回响率」后定下来的：真实样本里有大庆 2023-2024 那份的文本层
**公式顺序被打乱**（``已知集合Axxx20Bxax10``），连续字符串匹配的回响率只有 9%，
按它做闸会把这份明明安全的卷子挡在门外；而「后半净字数 / 前半净字数」三份分别是
3.52 / 4.00 / 3.86，极其稳定且与文字顺序无关。回响率仍会算出来写进诊断信息供人核对。
"""

import re
from pathlib import Path

# 逐题标记：出现即说明这一页属于「含答案解析」的那一段。
SOLUTION_MARK_RE = re.compile(
    r"【答案】|【详解】|【解析】|【参考详解】|参考答案|答案与解析|答案解析|试题详解"
)
# 题号：**行首**的 ``17.`` / ``17．`` / ``17、``。只认行首，避免命中正文里的编号。
_QUESTION_NO_RE = re.compile(r"^[ \t]*(\d{1,2})\s*[.．、]", re.MULTILINE)

_MIN_FRONT_PAGES = 2
_MIN_SOLUTION_PAGES = 3
_MIN_QUESTION_COUNT = 3
_MIN_MARKED_PAGE_RATIO = 0.5
_MIN_LENGTH_RATIO = 2.0
# 闸门 7：后半每道题的「题号 → 首个逐题标记」之间必须有题干文字（中位数门槛）。
# 真实样本的中位数是 57 / 59 / 58 字；若后半只有答案与解析、不重印题干，该值 ≈ 0。
_MIN_STEM_CHARS_IN_BACK = 12
_STEM_SCAN_WINDOW = 400
# 回响率只用于「报告」，不参与裁决（见模块 docstring 第 6 条说明）。
_ECHO_PREFIX_LEN = 8


def _clean(text: str) -> str:
    """只保留中英文字母与数字，去掉标点/空白/公式噪声，便于跨版本比对。"""
    return "".join(re.findall(r"[\u4e00-\u9fa5a-zA-Z0-9]", text or ""))


def _question_numbers(text: str) -> set:
    return {
        int(m.group(1))
        for m in _QUESTION_NO_RE.finditer(text or "")
        if 1 <= int(m.group(1)) <= 60
    }


def _stem_echo_ratio(front_text: str, back_clean: str, prefix_len: int = _ECHO_PREFIX_LEN) -> float:
    """前半每道题的题干开头有多少能在后半找到（仅作诊断，不参与裁决）。"""
    hit = total = 0
    for match in _QUESTION_NO_RE.finditer(front_text or ""):
        tail = _clean(front_text[match.end():])[:prefix_len]
        if len(tail) < prefix_len:
            continue
        total += 1
        if tail in back_clean:
            hit += 1
    return (hit / total) if total else 0.0


def _stem_lengths_before_mark(text: str, window: int = _STEM_SCAN_WINDOW) -> list:
    """后半里每个「行首题号 → 其后第一个逐题标记」之间的净字数。

    后半若重印了题干，这个值就是题干的长度；若后半只有答案与解析、不重印题干，
    题号后面紧跟着 `【答案】`，该值就是 0。返回样本列表。
    """

    lengths = []
    for match in _QUESTION_NO_RE.finditer(text or ""):
        tail = (text or "")[match.end(): match.end() + window]
        mark = SOLUTION_MARK_RE.search(tail)
        if not mark:
            continue
        lengths.append(len(_clean(tail[: mark.start()])))
    return lengths


def read_page_texts(source) -> list:
    """按页读出 PDF 的文本层（bytes / 路径皆可）。

    扫描件没有文本层 → 返回一串空串，检测自然判为「无解析段」，
    退回整本解析（视觉 OCR 路线照常工作，行为不比改动前差）。
    """

    import pymupdf as fitz

    if isinstance(source, (bytes, bytearray)):
        document = fitz.open(stream=bytes(source), filetype="pdf")
    else:
        document = fitz.open(str(source))
    with document:
        return [(page.get_text("text") or "") for page in document]


def detect_solution_split(page_texts) -> dict:
    """判定「解析段」的起始页；无法高置信判定时返回 ``None``。

    返回 ``{"start_page": int, "total_pages": int, "front_question_numbers": [...],
    "back_question_numbers": [...], "length_ratio": float, "stem_echo_ratio": float,
    "marked_page_ratio": float}``，其中 ``start_page`` 是 **0-based 页下标**
    （即前半段 = ``[0, start_page)``）。
    """

    texts = [t or "" for t in (page_texts or [])]
    total = len(texts)
    if total < _MIN_FRONT_PAGES + _MIN_SOLUTION_PAGES:
        return None

    marks = [len(SOLUTION_MARK_RE.findall(t)) for t in texts]
    start = next((i for i, count in enumerate(marks) if count >= 1), None)

    # 闸门 1：分界必须真的把文档切成两段（前面有原卷、后面有解析段）。
    if start is None or start < _MIN_FRONT_PAGES or total - start < _MIN_SOLUTION_PAGES:
        return None

    # 闸门 2：解析段必须「持续」。末页往往是解析的尾巴、没有独立标记（真实样本三份
    # 都是这样），所以用「占比」而不是「每页都必须有」。
    back_marks = marks[start:]
    marked_page_ratio = sum(1 for count in back_marks if count >= 1) / len(back_marks)
    if marked_page_ratio < _MIN_MARKED_PAGE_RATIO:
        return None

    # 闸门 3：前半一整页都不能有逐题标记（``start`` 是首个命中页，故天然成立；
    # 这里写成显式断言，防止以后有人改坏 ``start`` 的取法）。
    if any(count >= 1 for count in marks[:start]):
        return None

    front_text = "\n".join(texts[:start])
    back_text = "\n".join(texts[start:])

    # 闸门 4/5：前半的题号必须全部能在后半找到，且两边题号都够多。
    front_numbers = _question_numbers(front_text)
    back_numbers = _question_numbers(back_text)
    if len(front_numbers) < _MIN_QUESTION_COUNT or len(back_numbers) < len(front_numbers):
        return None
    missing = front_numbers - back_numbers
    if missing:
        return None

    # 闸门 6：后半是「题干+解析」，净字数必然显著多于前半的纯题干。
    front_len = len(_clean(front_text))
    back_len = len(_clean(back_text))
    length_ratio = (back_len / front_len) if front_len else 0.0
    if length_ratio < _MIN_LENGTH_RATIO:
        return None

    # 闸门 7：后半必须**重印了题干**。
    # 「前半原题 + 后半只给答案与解析（不重印题干）」这种卷子，跳页会把题干整个丢掉，
    # 而它**过得了前面所有闸门** —— 实测把三份真实卷的后半按「题号→首个答案标记」的
    # 份额扣掉来模拟这种形态，长度比仍有 2.43 / 3.16 / 2.75（照旧 ≥2）。
    # 唯一能区分的是「题号后面到底跟不跟题干文字」：真实样本中位数 57 / 59 / 58 字，
    # 不重印题干的形态则是 0。
    stem_lengths = _stem_lengths_before_mark(back_text)
    if len(stem_lengths) < _MIN_QUESTION_COUNT:
        # 连「题号 + 答案标记」的配对都凑不出来，说明形态与两段式卷不符 → 不跳。
        return None
    stem_lengths.sort()
    back_stem_median = stem_lengths[len(stem_lengths) // 2]
    if back_stem_median < _MIN_STEM_CHARS_IN_BACK:
        return None

    return {
        "start_page": start,
        "total_pages": total,
        "front_question_numbers": sorted(front_numbers),
        "back_question_numbers": sorted(back_numbers),
        "length_ratio": round(length_ratio, 2),
        "stem_echo_ratio": round(_stem_echo_ratio(front_text, _clean(back_text)), 2),
        "marked_page_ratio": round(marked_page_ratio, 2),
        "back_stem_median": back_stem_median,
    }


def build_trimmed_pdf(source, start_page: int) -> bytes:
    """只保留 ``start_page``（0-based）起的页面，返回新的 PDF 字节。

    MinerU 路线不支持页码范围，只能先把前半段**物理裁掉**再送云端；
    裁完之后 MinerU 的 ``page_idx`` 是从 0 重新数起的，调用方需要用
    ``start_page`` 把页码补齐回原始页号。
    """

    import pymupdf as fitz

    if isinstance(source, (bytes, bytearray)):
        raw = bytes(source)
        document = fitz.open(stream=raw, filetype="pdf")
    else:
        # 传路径时也要能原样返回（``bytes("…/a.pdf")`` 会直接抛 TypeError）。
        raw = Path(source).read_bytes()
        document = fitz.open(str(source))
    with document:
        total = len(document)
        keep = list(range(max(0, min(int(start_page), total)), total))
        if not keep or len(keep) == total:
            return raw
        document.select(keep)
        return document.tobytes()
