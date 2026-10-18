"""两段式试卷（前半纯题干原卷 + 后半含答案解析）切分检测的回归测试。

夹具按**真实样本的形态**构造（大庆实验中学 2023-2024 / 2024-2025、
东北师大附中 2024-2025 三份实测）：

* 前半每一页都**没有**逐题标记；
* 后半逐题标记密集，但**末页往往没有标记**（解析的尾巴）——
  所以「持续」判据用的是占比而不是「每页都必须有」；
* 后半净字数约为前半的 3.5~4.0 倍。
"""

import os

import pymupdf
import pytest

from mathbank.pdf_split import (
    build_trimmed_pdf,
    detect_solution_split,
    read_page_texts,
)


def _front_page(first_no: int, count: int, filler: str = "") -> str:
    """一页纯题干原卷：只有题号与题干，没有任何答案/详解标记。"""

    lines = ["一、单项选择题（每小题 5 分）"]
    for offset in range(count):
        lines.append(f"{first_no + offset}. 已知集合 A 满足条件，则实数 a 的取值范围是（ ）")
    return "\n".join(lines) + (f"\n{filler}" if filler else "")


def _back_page(first_no: int, count: int) -> str:
    """一页「题干 + 答案 + 详解」：逐题标记密集，且正文明显更长。"""

    blocks = []
    for offset in range(count):
        no = first_no + offset
        blocks.append(
            f"{no}. 已知集合 A 满足条件，则实数 a 的取值范围是（ ）\n"
            "A. 甲  B. 乙  C. 丙  D. 丁\n"
            "【答案】C\n"
            "【详解】由题意得集合 A 的元素关系，逐项检验可知只有选项 C 符合要求，"
            "其余选项均可举出反例，故正确答案为 C。"
        )
    return "\n".join(blocks)


def _two_section_pages(front_pages: int = 4, back_pages: int = 8, per_page: int = 4):
    texts = [_front_page(1 + i * per_page, per_page) for i in range(front_pages)]
    texts += [_back_page(1 + i * per_page, per_page) for i in range(back_pages)]
    # 真实样本里末页常常没有独立标记（解析收尾），故意抹掉以便锁住「占比」判据。
    texts[-1] = "综上，实数 a 的取值范围已全部求出，解答完毕。"
    return texts


def test_detects_two_section_paper_and_reports_evidence():
    texts = _two_section_pages()
    info = detect_solution_split(texts)

    assert info is not None, "形态完全符合的三份真实卷必须能判出来"
    assert info["start_page"] == 4
    assert info["total_pages"] == len(texts)
    # 闸门要的是「前半的题号全部能在后半找到」，后半可以更多（多印了答案/附加题）。
    assert set(info["front_question_numbers"]) <= set(info["back_question_numbers"])
    assert info["length_ratio"] >= 2.0
    assert info["marked_page_ratio"] >= 0.5


def test_returns_none_when_no_solution_marks_at_all():
    """整卷没有答案/解析段（纯题面 PDF）不得误判。"""

    assert detect_solution_split([_front_page(1 + i * 4, 4) for i in range(10)]) is None


def test_returns_none_when_first_page_already_has_marks():
    """逐题标记从第 1 页就有 = 单段卷（题干与解析混排），没有可跳过的前半段。"""

    texts = [_back_page(1, 4)] + [_back_page(5 + i * 4, 4) for i in range(7)]
    assert detect_solution_split(texts) is None


def test_returns_none_when_front_is_too_short_to_be_a_full_paper():
    """只跳过 1 页不算「两段式」，直接放弃（避免把正常的答案附录当成原卷）。"""

    texts = [_front_page(1, 4)] + [_back_page(5 + i * 4, 4) for i in range(8)]
    assert detect_solution_split(texts) is None


def _clean_len(text: str) -> int:
    return sum(1 for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fa5")


def _back_page_without_stem(first_no: int, count: int) -> str:
    """「只给答案与解析、不重印题干」的形态：题号后面紧跟着【答案】。"""

    blocks = []
    for offset in range(count):
        blocks.append(
            f"{first_no + offset}. 【答案】C\n"
            "【详解】由题意逐项检验可得正确选项为 C，其余选项均可举出反例，故选择 C；"
            "若需进一步验证，可代入特殊值检验。"
        )
    return "\n".join(blocks)


def test_rejects_when_back_section_does_not_reprint_stems():
    """后半「只给答案解析、不重印题干」时必须拒绝跳页 —— 否则题干整个丢失。

    这种形态**过得了前六道闸门**：拿三份真实卷把后半按「题号→首个答案标记」的份额
    扣掉来模拟，长度比仍有 2.43 / 3.16 / 2.75（照旧 ≥2）。区分点只剩「题号后面跟不跟
    题干文字」—— 真实样本中位数 57 / 59 / 58 字，这里是 0。
    """

    front = [_front_page(1 + i * 4, 4) for i in range(4)]
    back = [_back_page_without_stem(1 + i * 4, 4) for i in range(6)]

    # 先确认旧闸门确实拦不住：后半比前半长得多，长度比远超 2
    assert _clean_len(" ".join(back)) >= 2 * _clean_len(" ".join(front))

    assert detect_solution_split(front + back) is None


def test_reports_back_stem_median_for_auditing():
    """诊断里要能看见「后半重印的题干有多长」，方便事后核对为何跳过/未跳过。"""

    info = detect_solution_split(_two_section_pages())
    assert info is not None
    assert info["back_stem_median"] >= 12


def test_returns_none_when_back_section_is_answers_only():
    """后半如果只是答案（甚至比前半短），跳页会把题干整个删掉 —— 必须拒绝。

    这是最关键的一道闸：三份真实卷的「后半净字数 / 前半净字数」是 3.52 / 4.00 / 3.86，
    而只给答案的后半会明显更短。
    """

    front = [_front_page(1 + i * 4, 4) for i in range(6)]
    back = ["【答案】1.C 2.B 3.D 4.A"] * 4  # 纯答案表，正文远短于前半
    assert detect_solution_split(front + back) is None


def test_returns_none_when_a_front_question_is_missing_from_back():
    """前半有、后半没有的题号 → 跳过会丢题 → 必须拒绝。"""

    texts = _two_section_pages()
    # 后半整段改写题号，使它不再覆盖前半的题号
    texts[4:] = [page.replace("4.", "44.").replace("3.", "43.") for page in texts[4:]]
    assert detect_solution_split(texts) is None


def test_returns_none_for_tiny_documents():
    assert detect_solution_split([]) is None
    assert detect_solution_split(["a", "b", "c", "d"]) is None


def test_marker_page_ratio_ignores_trailing_plain_page():
    """末页没有标记不影响判定（真实样本三份都这样）——锁住「占比」而非「每页」。"""

    texts = _two_section_pages(front_pages=3, back_pages=6)
    info = detect_solution_split(texts)
    assert info is not None
    assert info["start_page"] == 3
    assert info["marked_page_ratio"] < 1.0, "夹具里末页是无标记的收尾页"


def _make_pdf(tmp_path, lines, name="doc.pdf"):
    """生成一份每页一行的 ASCII PDF（pymupdf 默认字体不含中文，测试用英文即可）。"""

    document = pymupdf.open()
    for text in lines:
        page = document.new_page()
        page.insert_text((72, 96), text, fontsize=14)
    path = tmp_path / name
    document.save(str(path))
    document.close()
    return path


def test_build_trimmed_pdf_keeps_tail_and_readback(tmp_path):
    path = _make_pdf(tmp_path, [f"PAGE{i} content" for i in range(1, 6)])
    trimmed = build_trimmed_pdf(str(path), 2)

    document = pymupdf.open(stream=trimmed, filetype="pdf")
    try:
        assert document.page_count == 3
        assert "PAGE3" in document[0].get_text()
        assert "PAGE5" in document[2].get_text()
    finally:
        document.close()


def test_build_trimmed_pdf_is_noop_when_nothing_to_trim(tmp_path):
    path = _make_pdf(tmp_path, ["A", "B", "C"])
    assert build_trimmed_pdf(str(path), 0) == path.read_bytes()


def test_read_page_texts_reads_text_layer(tmp_path):
    path = _make_pdf(tmp_path, ["ALPHA", "BETA", "GAMMA"])
    texts = read_page_texts(str(path))
    assert len(texts) == 3
    assert "BETA" in texts[1]


def _write_lines(page, lines):
    """在合成页面上写若干行中文（``china-s`` 是 pymupdf 内置简体字体）。"""

    y = 72
    for line in lines:
        page.insert_text((56, y), line, fontsize=10, fontname="china-s")
        y += 20


def _two_section_pdf_bytes(front_pages: int = 4, back_pages: int = 4, per_page: int = 4) -> bytes:
    """合成一份「前半纯题干 + 后半含答案解析」的 PDF（同一份卷子印了两遍）。

    前半与后半的题号都是从 1 开始的同一套（这正是真实样本的形态）。
    """

    document = pymupdf.open()
    for _ in range(front_pages):
        page = document.new_page()
        _write_lines(
            page,
            [
                f"{no}. 已知集合 A 满足条件，求实数 a 的取值范围。"
                for no in range(1, per_page + 1)
            ],
        )
    for _ in range(back_pages):
        page = document.new_page()
        lines = []
        for no in range(1, per_page + 1):
            lines.append(f"{no}. 已知集合 A 满足条件，求实数 a 的取值范围。")
            lines.append("【答案】C")
            lines.append(
                "【详解】由题意逐项检验可得正确选项为 C，其余选项均可举出反例，"
                "故选择 C；若需进一步验证，可代入特殊值检验。"
            )
        _write_lines(page, lines)
    try:
        return document.tobytes()
    finally:
        document.close()


def test_pdf_task_skips_front_half_end_to_end():
    """端到端：两段式卷只把后半段交给拆题模型，前半段整段不进流程。

    真跑 ``run_pdf_parsing_task``（OCR 与拆题都 mock，不花钱），断言三件事：
      1. 诊断里留下切分信息；
      2. 交给模型的正文里**只有后半段**的页标记；
      3. 整页原图也只剩后半段，且文件名带的是**原始页号**（不是裁后的 0 起编号）。
    """

    from unittest.mock import patch

    from main import DOCUMENT_TASKS, run_pdf_parsing_task

    captured = {}

    def fake_split(text, *args, **kwargs):
        captured["text"] = text
        return [{"content": "1. 已知集合 A 满足条件", "answer_markdown": ""}]

    task_id = "test-two-section-skip"
    if DOCUMENT_TASKS.exists(task_id):
        DOCUMENT_TASKS.remove(task_id)
    DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    try:
        with patch("main.ocr_pdf_page_image", return_value="（视觉转译占位）"), patch(
            "main.parse_paper_text_internal", side_effect=fake_split
        ):
            run_pdf_parsing_task(
                task_id,
                _two_section_pdf_bytes(),
                "two-section.pdf",
                generate_answers=False,
                page_range=None,
                pdf_strategy="force_ocr",
            )

        task = DOCUMENT_TASKS.snapshot(task_id)
        assert task["status"] == "completed", task.get("message")
        assert task["diagnostics"]["split"]["start_page"] == 4

        assert "MATHBANK_PDF_PAGE" in captured["text"]
        for page_no in (1, 2, 3, 4):
            assert f"MATHBANK_PDF_PAGE:{page_no} -->" not in captured["text"]
        for page_no in (5, 6, 7, 8):
            assert f"MATHBANK_PDF_PAGE:{page_no} -->" in captured["text"]

        assert task["page_images"] == [
            f"/static/test_uploads/tmp/pdf_page_{task_id}_{index}.png"
            for index in range(4, 8)
        ]
    finally:
        DOCUMENT_TASKS.remove(task_id)


def test_pdf_task_honours_explicit_page_range_over_detection():
    """用户手填「页码范围」时一律以用户为准 —— 那是「我要整本解析」的覆盖入口。"""

    from unittest.mock import patch

    from main import DOCUMENT_TASKS, run_pdf_parsing_task

    captured = {}

    def fake_split(text, *args, **kwargs):
        captured["text"] = text
        return [{"content": "1. 已知集合 A 满足条件", "answer_markdown": ""}]

    task_id = "test-two-section-explicit-range"
    if DOCUMENT_TASKS.exists(task_id):
        DOCUMENT_TASKS.remove(task_id)
    DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    try:
        with patch("main.ocr_pdf_page_image", return_value="（视觉转译占位）"), patch(
            "main.parse_paper_text_internal", side_effect=fake_split
        ):
            run_pdf_parsing_task(
                task_id,
                _two_section_pdf_bytes(),
                "two-section.pdf",
                generate_answers=False,
                page_range="1-8",  # 显式要整本
                pdf_strategy="force_ocr",
            )

        task = DOCUMENT_TASKS.snapshot(task_id)
        assert task["status"] == "completed", task.get("message")
        assert task["diagnostics"]["split"] is None
        for page_no in (1, 4, 8):
            assert f"MATHBANK_PDF_PAGE:{page_no} -->" in captured["text"]
    finally:
        DOCUMENT_TASKS.remove(task_id)


def test_mineru_task_trims_pdf_but_keeps_original_page_numbers(tmp_path):
    """MinerU 路线：送云端的是**裁过**的 PDF，但页号仍按原始 PDF 编号。

    MinerU 不认识页码范围，只能物理裁掉前半段；裁完它的 ``page_idx`` 从 0 重新数起，
    所以逐页文本必须补齐 ``start_page`` 个空页，否则「源自第 N 页 / 查看原页」整体偏移。
    """

    from unittest.mock import patch

    from main import DOCUMENT_TASKS, run_pdf_parsing_task
    from mathbank import mineru_client

    captured = {}

    def capture_parse(file_bytes, filename, workdir, on_progress=None, **kwargs):
        document = pymupdf.open(stream=file_bytes, filetype="pdf")
        try:
            captured["sent_pages"] = document.page_count
        finally:
            document.close()
        if on_progress:
            on_progress("uploading", {})
        return {
            "markdown": "# 试卷标题\n\n1. 已知集合 A 满足条件",
            "image_paths": [],
            "workdir": str(workdir),
        }

    def capture_post(parsed_questions, paper_title, task_id_arg, page_texts=None, source_text="", **kwargs):
        captured["page_texts"] = page_texts
        return parsed_questions

    tmp_upload = tmp_path / "tmp"
    tmp_upload.mkdir()

    task_id = "test-mineru-two-section"
    if DOCUMENT_TASKS.exists(task_id):
        DOCUMENT_TASKS.remove(task_id)
    DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    try:
        with patch("main.TMP_UPLOAD_DIR", str(tmp_upload)), patch.object(
            mineru_client, "is_configured", return_value=True
        ), patch.object(mineru_client, "parse_pdf", side_effect=capture_parse), patch.object(
            mineru_client, "stage_images", return_value={}
        ), patch.object(
            mineru_client, "replace_image_refs", side_effect=lambda md, mapping: md
        ), patch.object(
            mineru_client,
            "collect_page_texts",
            # 裁过之后 MinerU 返回的逐页文本只有后半段的 4 页（下标从 0 起）
            return_value=[f"后半第{i + 1}页正文" for i in range(4)],
        ), patch(
            "main.parse_paper_text_internal",
            return_value=[{"content": "1. 已知集合 A 满足条件", "answer_markdown": ""}],
        ), patch(
            "main.post_process_pdf_parsed_questions", side_effect=capture_post
        ):
            run_pdf_parsing_task(
                task_id,
                _two_section_pdf_bytes(),
                "two-section.pdf",
                False,
                None,
                "mineru_cloud",
            )

        task = DOCUMENT_TASKS.snapshot(task_id)
        assert task["status"] == "completed", task.get("message")
        assert task["diagnostics"]["split"]["start_page"] == 4
        assert captured["sent_pages"] == 4, "送 MinerU 的 PDF 应当只剩后半段 4 页"

        # 逐页文本补齐了 4 个空页 → 下标还原成原始页号
        page_texts = captured["page_texts"]
        assert page_texts is not None and len(page_texts) == 8
        assert page_texts[:4] == ["", "", "", ""]
        assert page_texts[4] == "后半第1页正文"

        # 整页原图按**原始页号**命名（4..7），不是裁后的 0..3
        assert [os.path.basename(url) for url in task["page_images"]] == [
            f"pdf_page_{task_id}_{index}.png" for index in range(4, 8)
        ]
    finally:
        DOCUMENT_TASKS.remove(task_id)


def test_find_source_page_by_overlap_returns_absolute_page(tmp_path):
    """回归 P0：页码基必须统一成**绝对页号**。

    ``ocr_results`` 只装本次选中的页，而裁剪图文件名 ``pdf_crop_{task}_{页}_{序}.png``
    与整页原图 ``pdf_page_{task}_{页}.png`` 用的都是**整本 PDF 的绝对页号**。
    旧实现直接返回 ``enumerate`` 的局部下标 —— 一旦用了「页码范围」，题目的来源页
    就与配图编号错位一个偏移，插图兜底整体错挂。
    """

    import pytest as _pytest  # noqa: F401  — 让 IS_TESTING 生效，避免抢 runtime.lock

    import main  # noqa: F401  — 必须先导 main，否则 routes_ai 循环导入
    from mathbank.routes_ai import find_source_page_by_overlap

    pages = [
        "<!-- MATHBANK_PDF_PAGE:6 -->\n三角函数与单位圆",
        "<!-- MATHBANK_PDF_PAGE:9 -->\n立体几何与体积",
    ]
    assert find_source_page_by_overlap("三角函数与单位圆", pages) == 5
    assert find_source_page_by_overlap("立体几何与体积", pages) == 8
    assert find_source_page_by_overlap("完全不相干的内容", pages) == -1

    # 没有页标记的列表（老数据 / 手工构造）退回局部下标，行为与改动前一致。
    legacy = ["三角函数与单位圆", "立体几何与体积"]
    assert find_source_page_by_overlap("立体几何与体积", legacy) == 1


# ─────────────────────────────────────────────────────────────────────────────
# parse_paper_text_internal 答案静默净化的单测（纯函数 _sanitize_answers）
#
# 直接测那 6 行净化逻辑，绕开 AI 调用：两段式切分时 AI 输入就是原卷「参考答案+
# 详解」段，答案必然来自原卷，即便没打 [EXTRACTED_ORIGINAL] 也必须保留。
# ─────────────────────────────────────────────────────────────────────────────


def _answer_q(answer: str) -> dict:
    return {"content": "题干占位", "answer_markdown": answer}


def test_sanitize_preserves_when_split_and_no_marker():
    """用例1：未勾生成 + 切分保留 + AI 没打标记 → 答案必须保留原文。"""
    from mathbank.parsing_pipeline import _sanitize_answers

    qs = [_answer_q("【答案】C\n【解析】由题意得 C 正确。")]
    _sanitize_answers(qs, generate_answers_bool=False, preserve_extracted_answers=True)
    assert qs[0]["answer_markdown"] == "【答案】C\n【解析】由题意得 C 正确。"


def test_sanitize_wipes_when_no_preserve_and_no_marker():
    """用例2：未勾生成 + 不保留 + AI 没打标记 → 答案必须清空（锁现有行为）。"""
    from mathbank.parsing_pipeline import _sanitize_answers

    qs = [_answer_q("【答案】C\n【解析】由题意得 C 正确。")]
    _sanitize_answers(qs, generate_answers_bool=False, preserve_extracted_answers=False)
    assert qs[0]["answer_markdown"] == ""


def test_sanitize_strips_marker_when_preserve():
    """用例3：未勾生成 + 切分保留 + AI 打了 [EXTRACTED_ORIGINAL] → 标记必须被清理。"""
    from mathbank.parsing_pipeline import _sanitize_answers

    raw = "[EXTRACTED_ORIGINAL]【答案】C\n【解析】由题意得 C 正确。"
    qs = [_answer_q(raw)]
    _sanitize_answers(qs, generate_answers_bool=False, preserve_extracted_answers=True)
    assert qs[0]["answer_markdown"] == "【答案】C\n【解析】由题意得 C 正确。"


def test_sanitize_always_preserves_when_generate_answers_on():
    """勾选生成答案时，无论是否打标记都保留并清理标记。"""
    from mathbank.parsing_pipeline import _sanitize_answers

    qs = [_answer_q("[EXTRACTED_ORIGINAL]【答案】C"), _answer_q("【答案】D（模型自推）")]
    _sanitize_answers(qs, generate_answers_bool=True)
    assert qs[0]["answer_markdown"] == "【答案】C"
    assert qs[1]["answer_markdown"] == "【答案】D（模型自推）"


def test_sanitize_empty_answer_stays_empty():
    """空答案 / None 答案保持为空，不被任何分支误填。"""
    from mathbank.parsing_pipeline import _sanitize_answers

    qs = [_answer_q(""), _answer_q(None)]
    _sanitize_answers(qs, generate_answers_bool=False, preserve_extracted_answers=True)
    assert qs[0]["answer_markdown"] == ""
    assert qs[1]["answer_markdown"] == ""


# ─────────────────────────────────────────────────────────────────────────────
# 逐题比对原卷：原卷无【详解】则截断 AI 编造的解析段（post_process 落点）。
# ─────────────────────────────────────────────────────────────────────────────


def _numbered_q(no: str, answer: str) -> dict:
    return {"paper_question_no": no, "answer_markdown": answer}


def test_truncate_when_source_has_no_solution():
    """源段（题号1）只有【答案】无【详解】→ 截掉 AI 编的【解析】。"""
    from mathbank.parsing_pipeline import _truncate_ai_solutions

    source = "1. 题一\n【答案】D\n2. 题二\n【答案】C\n【详解】原卷解析"
    qs = [_numbered_q("1", "【答案】D\n\n【解析】AI 编的")]
    _truncate_ai_solutions(qs, source, generate_answers_bool=False)
    assert qs[0]["answer_markdown"] == "【答案】D"


def test_keep_when_source_has_solution():
    """源段（题号2）含【详解】→ 整段保留不动。"""
    from mathbank.parsing_pipeline import _truncate_ai_solutions

    source = "2. 题二\n【答案】C\n【详解】原卷的解析"
    qs = [_numbered_q("2", "【答案】C\n\n【详解】原卷的解析")]
    _truncate_ai_solutions(qs, source, generate_answers_bool=False)
    assert qs[0]["answer_markdown"] == "【答案】C\n\n【详解】原卷的解析"


def test_conservative_when_question_no_not_found():
    """源段里找不到该题号 → 保守跳过，answer_markdown 不变。"""
    from mathbank.parsing_pipeline import _truncate_ai_solutions

    source = "1. 题一\n【答案】A\n2. 题二\n【答案】B\n【详解】原卷解析"
    qs = [_numbered_q("99", "【答案】X\n\n【解析】AI 编的")]
    _truncate_ai_solutions(qs, source, generate_answers_bool=False)
    assert qs[0]["answer_markdown"] == "【答案】X\n\n【解析】AI 编的"


def test_no_truncate_when_generate_answers_on():
    """勾选生成答案时完全不动（即便源无【详解】）。"""
    from mathbank.parsing_pipeline import _truncate_ai_solutions

    source = "1. 题一\n【答案】D"
    qs = [_numbered_q("1", "【答案】D\n\n【解析】AI 编的")]
    _truncate_ai_solutions(qs, source, generate_answers_bool=True)
    assert qs[0]["answer_markdown"] == "【答案】D\n\n【解析】AI 编的"


def test_source_with_hash_prefix_marker():
    """源段是 '## 【答案】X\\n## 【详解】Y' → 剥 ## 前缀后仍识别到【详解】。"""
    from mathbank.parsing_pipeline import _source_has_solution

    assert _source_has_solution("## 【答案】X\n## 【详解】Y") is True


def test_split_source_by_question_no():
    """源 markdown 按题号切成 {题号: 题段}。"""
    from mathbank.parsing_pipeline import _split_source_by_question_no

    source = "1. 题一\n【答案】A\n2. 题二\n【答案】B\n【详解】原卷解析"
    result = _split_source_by_question_no(source)
    assert set(result.keys()) == {"1", "2"}
    assert result["1"].startswith("1. 题一")
    assert "【详解】" in result["2"]


def test_truncate_fires_after_storage_normalization_in_real_order():
    """集成顺序锁：apply_storage_normalization 必须先于 post_process
    跑，否则 paper_question_no 还没派生，_truncate_ai_solutions 会静默
    跳过——直测全绿但功能失效。"""
    from mathbank.parsing_pipeline import (
        apply_storage_normalization,
        _truncate_ai_solutions,
    )
    # 模拟 AI 返回：content 行首有题号，answer 里 AI 编了解析
    qs = [{
        "content": "1. 题一题干",
        "answer_markdown": "【答案】D\n\n【解析】AI 编的",
        "question_type": "single_choice",
    }]
    qs = apply_storage_normalization(qs)
    assert qs[0].get("paper_question_no") == "1", \
        "归一化必须派生 paper_question_no，否则集成顺序被破坏"
    # 源段只有【答案】无【详解】
    source = "1. 题一\n【答案】D\n2. 题二\n【答案】C\n【详解】原卷解析"
    _truncate_ai_solutions(qs, source, generate_answers_bool=False)
    assert qs[0]["answer_markdown"] == "【答案】D", \
        "集成顺序正确时截断必须生效"


# ─────────────────────────────────────────────────────────────────────────────
# 后处理检测「选项丢失」：源段选项数 > AI 输出 \item 数 → 往 review 写警告。
# 只认行首单字母判据（安全），数不出保守跳过（不误报），仅选择题生效。
# ─────────────────────────────────────────────────────────────────────────────


def _choice_q(no: str, item_count: int, qtype: str = "single_choice") -> dict:
    items = "\n".join(f"\\item 选项{i}" for i in range(item_count))
    content = f"题{no} \\paren\n\\begin{{choices}}\n{items}\n\\end{{choices}}"
    return {"paper_question_no": no, "question_type": qtype, "content": content}


def test_flag_missing_options_when_ai_drops_one():
    """源段 4 个行首标号（A/B/C/D 各自成行，图插在标号上方），
    AI 只输出 3 个 \\item → review 必须含缺失警告。"""
    from mathbank.parsing_pipeline import _flag_missing_options

    source = (
        "1. 题一\n\n![](a.jpg)\n\nA\n\n![](b.jpg)\n\nB\n\n"
        "![](c.jpg)\n\nC\n\n![](d.jpg)\n\nD\n\n【答案】D\n"
    )
    qs = [_choice_q("1", 3)]
    _flag_missing_options(qs, source)
    assert "原卷 4 项，识别 3 项" in qs[0]["review"]


def test_no_flag_when_counts_match():
    """纯文字题：源段 4 个文字选项、AI 也输出 4 个 \\item（src_imgs=0）→ 不写警告。

    注意：旧版用 ai_imgs >= 1 守卫生效，曾把"源段含 4 张内联图但 AI 0 图"也误判为不报警；
    该内联图场景属"图片选项全丢"，现已归到 test_flag_all_images_missing 覆盖。
    本测试只守护"纯文字题且字数匹配"不误报。
    """
    from mathbank.parsing_pipeline import _flag_missing_options

    source = "1. 题一\nA. 甲 B. 乙 C. 丙 D. 丁\n【答案】D\n"
    qs = [_choice_q("1", 4)]
    _flag_missing_options(qs, source)
    assert "review" not in qs[0]


def test_skip_when_source_has_too_few_options():
    """源段只有 A/B（2 项）、AI 也 2 个 \\item → 相等不告警。"""
    from mathbank.parsing_pipeline import _flag_missing_options

    source = "1. 题一\nA\nB\n【答案】"
    qs = [_choice_q("1", 2)]
    _flag_missing_options(qs, source)
    assert "review" not in qs[0]


def test_skip_non_choice_question():
    """非选择题（如 detailed_answer）即便少 \\item 也不告警。"""
    from mathbank.parsing_pipeline import _flag_missing_options

    source = "1. 题一\nA\nB\nC\nD\n【答案】D"
    qs = [_choice_q("1", 3, qtype="detailed_answer")]
    _flag_missing_options(qs, source)
    assert "review" not in qs[0]


def test_skip_when_source_options_unparseable():
    """选项挤在一行（"A. 1 B. 2 C. 3 D. 4"）行首判据数不出 → 保守跳过。"""
    from mathbank.parsing_pipeline import _flag_missing_options

    source = "1. 题一\nA. 1 B. 2 C. 3 D. 4\n【答案】D"
    qs = [_choice_q("1", 3)]
    _flag_missing_options(qs, source)
    assert "review" not in qs[0]


def test_count_source_options():
    """_count_source_options 取值：行首单字母连续计数，内联/脏数据按规则处理。"""
    from mathbank.parsing_pipeline import _count_source_options

    assert _count_source_options("A\nB\nC\nD") == 4
    # 缺 C → 从 A 起最大连续 = 2
    assert _count_source_options("A\nB\nD") == 2
    # 同行内联 → 数不出，保守返回 0
    assert _count_source_options("A. 1 B. 2") == 0
    # 带 ## 脏数据前缀 → 剥离后仍按行首单字母计数 = 3
    assert _count_source_options("## A\n## B\n## C") == 3


# ─────────────────────────────────────────────────────────────────────────────
# 题号为空（AI 未返回 paper_question_no）时，用「序数兜底 + 题干文本校验」匹配源段，
# 让 _truncate_ai_solutions / _flag_missing_options 仍能命中；文本校验失败则保守跳过。
# ─────────────────────────────────────────────────────────────────────────────


def test_match_by_number_when_present():
    """题号有值且在 segments 里 → 直接按题号返回对应源段（不走兜底）。"""
    from mathbank.parsing_pipeline import _match_source_segment

    q = {"paper_question_no": "2", "content": "题二题干文字"}
    segments = {
        "1": "1. 题一\n【答案】A",
        "2": "2. 题二\n【答案】C\n【详解】原卷解析",
    }
    assert _match_source_segment(q, 0, segments) == segments["2"]


def test_match_by_index_when_number_empty():
    """题号为空（index=0）+ 源段有 '1' + AI 题干前若干汉字能在源段找到 →
    序数兜底匹配成功，返回 segments['1']。"""
    from mathbank.parsing_pipeline import _match_source_segment

    q = {"paper_question_no": None, "content": "题一题干文字内容很长"}
    segments = {
        "1": "1. 题一题干文字内容很长\n【答案】D",
        "2": "2. 题二\n【答案】C",
    }
    assert _match_source_segment(q, 0, segments) == segments["1"]


def test_skip_when_index_fallback_text_mismatch():
    """题号为空 + 源段 '1' 内容与 AI 题干不重叠 → 文本校验失败，保守跳过返回 ''。"""
    from mathbank.parsing_pipeline import _match_source_segment

    q = {"paper_question_no": None, "content": "另一些字完全不同的题面"}
    segments = {
        "1": "1. 完全不同的题干内容\n【答案】D",
        "2": "2. 题二\n【答案】C",
    }
    assert _match_source_segment(q, 0, segments) == ""


def test_truncate_fires_without_question_no():
    """题号为空 + 源段（序数兜底命中）无【详解】+ AI 答案含【解析】→ 截断生效。"""
    from mathbank.parsing_pipeline import _truncate_ai_solutions

    q = {
        "paper_question_no": None,
        "content": "题一题干文字内容很长",
        "answer_markdown": "【答案】D\n\n【解析】AI 编的",
    }
    source = (
        "1. 题一题干文字内容很长\n【答案】D\n"
        "2. 题二\n【答案】C\n【详解】原卷解析"
    )
    _truncate_ai_solutions([q], source, generate_answers_bool=False)
    assert q["answer_markdown"] == "【答案】D"


def test_flag_fires_without_question_no():
    """题号为空 + 源段（序数兜底命中）4 项 + AI 只输出 3 个 \\item → review 有警告。"""
    from mathbank.parsing_pipeline import _flag_missing_options

    items = "\n".join(f"\\item 选项{i}" for i in range(3))
    content = "题一题干\n\\begin{choices}\n" + items + "\n\\end{choices}"
    q = {"paper_question_no": None, "question_type": "single_choice", "content": content}
    source = (
        "1. 题一题干\n\n![](a.jpg)\n\nA\n\n![](b.jpg)\n\nB\n\n"
        "![](c.jpg)\n\nC\n\n![](d.jpg)\n\nD\n\n【答案】D\n"
    )
    _flag_missing_options([q], source)
    assert "原卷 4 项，识别 3 项" in q["review"]


def _image_choice_q(item_count: int, image_paths: list, no: str = "3") -> dict:
    """构造图片型选择题：choices 内每个 item 后挂一张 includegraphics 配图。"""
    items = "\n".join(
        f"    \\item \\includegraphics[max width=0.42\\linewidth]{{{image_paths[i]}}}"
        for i in range(item_count)
    )
    content = (
        "设 $abc > 0$ ，二次函数 $f(x) = ax^2 + bx + c$ 的图象可能是 \\paren\n"
        f"\\begin{{choices}}\n{items}\n\\end{{choices}}\nD"
    )
    return {
        "paper_question_no": no,
        "question_type": "single_choice",
        "content": content,
        "image_paths": image_paths,
    }


def test_flag_image_options_missing_when_ai_drops_one():
    """图片型选项：源段 4 张配图、AI 只输出 3 张 → 走图片守恒判据写 review。

    复现大庆卷第 3 题：图片型选项的标号常与图挤在一行，行首判据数不出
    （_count_source_options 返回 0），旧逻辑直接跳过、静默漏报；新增的图片
    守恒判据（源段图数 > AI choices 内 \\includegraphics 数）必须兜住。
    """
    from mathbank.parsing_pipeline import _flag_missing_options

    src_images = [
        "mineru_1790122189_1.jpg",
        "mineru_1790122189_2.jpg",
        "mineru_1790122189_3.jpg",
        "mineru_1790122189_5.jpg",
    ]
    source = (
        "3. 设 $abc > 0$ ，二次函数 $f(x) = ax^2 + bx + c$ 的图象可能是\n"
        "A ![](" + src_images[0] + ") B ![](" + src_images[1] + ") "
        "C ![](" + src_images[2] + ") D ![](" + src_images[3] + ")\n"
    )
    # AI 输出只保留了 3 张图（丢了 _1）
    ai_images = src_images[1:]  # _2,_3,_5
    q = _image_choice_q(3, ai_images)
    _flag_missing_options([q], source)
    assert q.get("review") is not None
    assert "原卷 4 张配图，识别 3 张" in q["review"]


def test_no_flag_when_image_counts_match():
    """图片型选项：源段 4 张、AI 也输出 4 张 → 不报警。"""
    from mathbank.parsing_pipeline import _flag_missing_options

    src_images = [
        "mineru_1790122189_1.jpg",
        "mineru_1790122189_2.jpg",
        "mineru_1790122189_3.jpg",
        "mineru_1790122189_5.jpg",
    ]
    source = (
        "3. 设 $abc > 0$ ，二次函数图象可能是\n"
        "A ![](" + src_images[0] + ") B ![](" + src_images[1] + ") "
        "C ![](" + src_images[2] + ") D ![](" + src_images[3] + ")\n"
    )
    q = _image_choice_q(4, src_images)
    _flag_missing_options([q], source)
    assert "review" not in q


def test_flag_works_without_question_no_image():
    """图片型 + 题号为空：靠序数兜底（index→源段号）命中源段，仍能报警。

    模拟线上排列：该题在完整卷里位于第 3 题（list index=2），源段号为 '3'。
    """
    from mathbank.parsing_pipeline import _flag_missing_options

    src_images = [
        "mineru_1790122189_1.jpg",
        "mineru_1790122189_2.jpg",
        "mineru_1790122189_3.jpg",
        "mineru_1790122189_5.jpg",
    ]
    source = (
        "1. 题一\nA. 甲 B. 乙 C. 丙 D. 丁\n\n"
        "2. 题二\nA. 子 B. 丑 C. 寅 D. 卯\n\n"
        "3. 设 $abc > 0$ ，二次函数图象可能是\n"
        "A ![](" + src_images[0] + ") B ![](" + src_images[1] + ") "
        "C ![](" + src_images[2] + ") D ![](" + src_images[3] + ")\n"
    )
    # 真实 q3：题号为空、只输出 3 图，且位于 list 第 3 位（index=2）
    q = _image_choice_q(3, src_images[1:], no=None)
    others = [
        {"paper_question_no": "1", "question_type": "single_choice", "content": "题一"},
        {"paper_question_no": "2", "question_type": "single_choice", "content": "题二"},
    ]
    _flag_missing_options(others + [q], source)
    assert q.get("review") is not None
    assert "原卷 4 张配图，识别 3 张" in q["review"]


def test_no_flag_pure_text_question_no_images():
    """纯文字题（源段无图）→ 不报警（图片守恒判据的 src_imgs>=2 守卫生效）。"""
    from mathbank.parsing_pipeline import _flag_missing_options

    source = "1. 题一\nA. 甲 B. 乙 C. 丙 D. 丁\n【答案】D"
    q = {"paper_question_no": "1", "question_type": "single_choice",
         "content": "题一\n\\begin{choices}\n\\item 甲\n\\item 乙\n\\item 丙\n\\item 丁\n\\end{choices}"}
    _flag_missing_options([q], source)
    assert "review" not in q


def test_flag_missing_options_idempotent():
    """重复跑不重复写：第二次不应追加重复文案。"""
    from mathbank.parsing_pipeline import _flag_missing_options

    src_images = [
        "mineru_1790122189_1.jpg",
        "mineru_1790122189_2.jpg",
        "mineru_1790122189_3.jpg",
        "mineru_1790122189_5.jpg",
    ]
    source = (
        "3. 设 $abc > 0$ ，二次函数图象可能是\n"
        "A ![](" + src_images[0] + ") B ![](" + src_images[1] + ") "
        "C ![](" + src_images[2] + ") D ![](" + src_images[3] + ")\n"
    )
    q = _image_choice_q(3, src_images[1:])
    _flag_missing_options([q], source)
    first = q["review"]
    _flag_missing_options([q], source)
    assert q["review"] == first, "重复跑不应追加重复警告"


def test_count_segment_images():
    """_count_segment_images 计数两种配图形态。"""
    from mathbank.parsing_pipeline import _count_segment_images

    seg = "题干\n![](a.jpg)\n\\includegraphics{b.jpg}\n文字\n![](c.png)"
    assert _count_segment_images(seg) == 3
    assert _count_segment_images("") == 0
    assert _count_segment_images("无图纯文字") == 0


def test_flag_all_images_missing():
    """源段 4 图 + AI choices 里 0 个 includegraphics（全丢）→ 必须报警。"""
    from mathbank.parsing_pipeline import _flag_missing_options

    source = (
        "1. 题一\nA ![](a.jpg) B ![](b.jpg) C ![](c.jpg) D ![](d.jpg)\n"
        "【答案】D\n"
    )
    # AI 输出有 choices 但一个 includegraphics 都没有
    content = (
        "题一 \\paren\n\\begin{choices}\n"
        "\\item 选项甲\n\\item 选项乙\n\\item 选项丙\n\\item 选项丁\n"
        "\\end{choices}"
    )
    qs = [{"paper_question_no": "1", "question_type": "single_choice",
           "content": content}]
    _flag_missing_options(qs, source)
    assert "review" in qs[0], "4 图全丢必须报警"
    assert "原卷 4 张配图" in qs[0]["review"]

