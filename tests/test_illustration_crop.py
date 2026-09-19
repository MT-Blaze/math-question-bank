# -*- coding: utf-8 -*-
"""OCR 插图坐标 -> 物理裁剪图 -> 占位符回填 的端到端回归。

背景：PDF 整页 OCR 曾经只发 ``COMMON_OCR_PROMPT``，从不请求 ``[ILLUSTRATION_BOX]``
坐标，导致下游 ``resolve_ocr_illustration_boxes`` 恒匹配不到标签、日志里
"实际裁剪图片 0 张"，题面里的插图永久丢失。本文件锁住修复后的契约。
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from main import LOCAL_TOKEN  # noqa: F401  (import side-effect: app 装配)


@pytest.fixture
def page_png(tmp_path):
    """一张 200x400 的整页图，便于按百分比断言裁剪区域。"""
    path = tmp_path / "page.png"
    image = Image.new("RGB", (200, 400), (255, 255, 255))
    # 在左上画一块黑区，确认裁剪确实落在预期位置。
    for x in range(20, 40):
        for y in range(40, 80):
            image.putpixel((x, y), (0, 0, 0))
    image.save(path)
    return str(path)


@pytest.fixture
def tmp_uploads(tmp_path):
    target = tmp_path / "uploads_tmp"
    target.mkdir()
    with patch("main.TMP_UPLOAD_DIR", str(target)):
        yield target


def test_resolve_crops_every_box_and_inserts_placeholders(page_png, tmp_uploads):
    from main import resolve_ocr_illustration_boxes

    text = (
        "1. 如图 [ILLUSTRATION_BOX: 10, 10, 20, 20] 所示，求角度。\n"
        "2. 再看下图 [ILLUSTRATION_BOX: 50, 40, 80, 90]，求面积。"
    )

    resolved = resolve_ocr_illustration_boxes(text, page_png, "taskA", 3)

    assert "ILLUSTRATION_BOX" not in resolved
    assert resolved.count("![插图](tmp/pdf_crop_taskA_3_") == 2
    produced = sorted(os.listdir(tmp_uploads))
    assert produced == ["pdf_crop_taskA_3_0.png", "pdf_crop_taskA_3_1.png"]

    # 10%..20% of 200x400 (+4px padding) -> 非空且远小于整页
    with Image.open(tmp_uploads / "pdf_crop_taskA_3_0.png") as crop:
        assert crop.size == (28, 48)
    with Image.open(tmp_uploads / "pdf_crop_taskA_3_1.png") as crop:
        # x: 40%..90% of width=200 -> 80..180 (+4px padding) = 108
        # y: 50%..80% of height=400 -> 200..320 (+4px padding) = 128
        assert crop.size == (108, 128)


def test_full_width_brackets_commas_and_percent_are_tolerated(page_png, tmp_uploads):
    from main import resolve_ocr_illustration_boxes

    text = "如图（ILLUSTRATION_BOX： 10，10，20，20%）所示"
    resolved = resolve_ocr_illustration_boxes(text, page_png, "taskB", 1)

    assert "ILLUSTRATION_BOX" not in resolved
    assert resolved == "如图![插图](tmp/pdf_crop_taskB_1_0.png)所示"
    assert os.listdir(tmp_uploads) == ["pdf_crop_taskB_1_0.png"]


def test_degenerate_and_out_of_range_boxes_are_skipped(page_png, tmp_uploads):
    from main import resolve_ocr_illustration_boxes

    text = (
        "[ILLUSTRATION_BOX: 80, 60, 20, 40]\n"   # ymax < ymin
        "[ILLUSTRATION_BOX: 10, 10, 10, 90]\n"   # 零高度
        "[ILLUSTRATION_BOX: 10, 10, 20, 20]\n"   # 有效
    )
    resolved = resolve_ocr_illustration_boxes(text, page_png, "taskC", 2)

    assert resolved.count("pdf_crop_taskC_2_") == 1
    assert os.listdir(tmp_uploads) == ["pdf_crop_taskC_2_0.png"]


def test_duplicate_boxes_produce_a_single_crop(page_png, tmp_uploads):
    from main import resolve_ocr_illustration_boxes

    text = (
        "[ILLUSTRATION_BOX: 10, 10, 20, 20]"
        "[ILLUSTRATION_BOX: 10, 10, 20, 20]"
    )
    resolved = resolve_ocr_illustration_boxes(text, page_png, "taskD", 0)

    assert os.listdir(tmp_uploads) == ["pdf_crop_taskD_0_0.png"]
    assert resolved.count("pdf_crop_taskD_0_0.png") == 1


def test_missing_page_image_is_a_noop(tmp_uploads):
    from main import resolve_ocr_illustration_boxes

    text = "如图 [ILLUSTRATION_BOX: 10, 10, 20, 20] 所示"
    assert resolve_ocr_illustration_boxes(text, "", "taskE", 1) == text
    assert resolve_ocr_illustration_boxes(text, "no/such/page.png", "taskE", 1) == text
    assert os.listdir(tmp_uploads) == []


def test_parse_box_treats_unit_range_coordinates_as_percentages():
    """部分视觉模型按 0-1 归一化输出坐标，必须乘 100 对齐，否则整页偏移。"""
    from mathbank.illustration_crop import _parse_box

    assert _parse_box(("0.1", "0.2", "0.3", "0.4")) == (10.0, 20.0, 30.0, 40.0)
    assert _parse_box(("0", "0", "1.0", "1.0")) == (0.0, 0.0, 100.0, 100.0)
    # 已是百分比的坐标保持原样
    assert _parse_box(("10", "20", "30", "40")) == (10.0, 20.0, 30.0, 40.0)
    assert _parse_box(("10.5", "20.25", "30", "40")) == (
        10.5,
        20.25,
        30.0,
        40.0,
    )
    # 四个值全为 0 不触发归一化（否则会放大一个退化框），仍然拒绝
    assert _parse_box(("0", "0", "0", "0")) is None
    # 只要有一个值超出 0-1 就按百分比解释，越界照旧拒绝
    assert _parse_box(("0", "0", "120", "40")) is None


def test_parse_box_folds_thousand_scale_coordinates():
    r"""按 0-1000 口径输出的坐标要折合成百分比，而不是静默丢弃。

    实测整页 OCR 会给出 `115, 180, 240, 360` 这类值（0-1000 口径）。旧实现
    一律拒绝，随后标签被擦除，AI 拆题时完全看不到这里有图——现象就是「图凭空
    消失」，且没有任何报错可查。

    判据是「**至少两个**值越过 100」：只要求一个值越界仍保守放弃，
    因为单个小幅越界（如 ``ymax=105``）不足以证明整组是 0-1000 口径，
    硬除会得到完全错误的裁剪区。
    """
    from mathbank.illustration_crop import _parse_box

    assert _parse_box(("115", "180", "240", "360")) == (11.5, 18.0, 24.0, 36.0)
    assert _parse_box(("725", "150", "855", "355")) == (72.5, 15.0, 85.5, 35.5)
    # 折合后仍越界（>1000）则照旧拒绝
    assert _parse_box(("115", "180", "240", "1200")) is None
    # 仅单个值越界时不做折合，直接拒绝（避免产出错误裁剪区）
    assert _parse_box(("10", "20", "120", "40")) is None


def test_parse_box_folds_mixed_scale_coordinates():
    r"""**混合刻度**：前几个值看起来像百分比、其实整组是 0-1000。

    实测第 4 题直方图的标记是 `90, 150, 230, 430`：第一个值 90 落在
    0-100 区间内，旧判据「四个值全部 > 100」认不出来，于是整个框被丢弃，
    直方图凭空消失。现在改为「至少两个值越界」即折合。
    """
    from mathbank.illustration_crop import _parse_box

    assert _parse_box(("90", "150", "230", "430")) == (9.0, 15.0, 23.0, 43.0)
    # 折合后不是合法百分比框（ymax<=ymin）时不能硬折合
    assert _parse_box(("430", "150", "90", "230")) is None
    # 折合后仍有值越界（>100）则拒绝
    assert _parse_box(("90", "150", "230", "1430")) is None


def test_tighten_to_content_trims_surrounding_whitespace():
    """框内四周是大片空白时，收紧到图形内容外接框 + pad_px。"""
    from mathbank.illustration_crop import _tighten_to_content

    img = Image.new("RGB", (120, 100), (255, 255, 255))
    for x in range(40, 80):
        for y in range(30, 60):
            img.putpixel((x, y), (0, 0, 0))

    box = _tighten_to_content(img)
    assert box is not None
    # 图形 x 40-79 / y 30-59，收紧后四周各留 pad_px=6
    assert box == (34, 24, 86, 66)


def test_tighten_to_content_bails_when_graphic_touches_the_crop_edge():
    """图形已经贴住框边时不收紧，避免把图形自身裁掉。"""
    from mathbank.illustration_crop import _tighten_to_content

    img = Image.new("RGB", (60, 60), (255, 255, 255))
    for x in range(2, 58):
        for y in range(2, 58):
            img.putpixel((x, y), (0, 0, 0))

    assert _tighten_to_content(img) is None


def test_over_large_model_box_is_tightened_to_the_graphic(tmp_path, tmp_uploads):
    """端到端：模型框偏大（框进题号/图题/题干文字）时，最终裁图只剩图形。"""
    from main import resolve_ocr_illustration_boxes

    path = tmp_path / "wide.png"
    img = Image.new("RGB", (200, 400), (255, 255, 255))
    for x in range(90, 110):
        for y in range(180, 220):
            img.putpixel((x, y), (0, 0, 0))
    img.save(path)

    # 模型框 [40,30,70,70] 覆盖 x60-140 / y160-280，图形只占其中一小块
    resolved = resolve_ocr_illustration_boxes(
        "如图 [ILLUSTRATION_BOX: 40, 30, 70, 70] 所示", str(path), "taskH", 1
    )

    assert "pdf_crop_taskH_1_0.png" in resolved
    with Image.open(tmp_uploads / "pdf_crop_taskH_1_0.png") as crop:
        # 未收紧时（+4px padding）应为 88x128
        assert crop.size != (88, 128)
        assert crop.size == (32, 52)
        # 收紧后图形填满裁剪区，四周留白不超过 pad_px
        tight = crop.convert("L").point(lambda p: 255 if p < 128 else 0)
        bbox = tight.getbbox()
        assert bbox is not None
        left, upper, right, lower = bbox
        width, height = crop.size
        assert left <= 7 and (width - right) <= 7
        assert upper <= 7 and (height - lower) <= 7


def test_label_erasure_never_eats_surrounding_content():
    from main import process_ocr_illustrations

    assert process_ocr_illustrations(
        "如图 [ILLUSTRATION_BOX: 10, 20, 90, 80] 所示"
    ) == "如图 所示"
    # 畸形/无括号标签同样被清掉，但同行的正常文字保留
    assert process_ocr_illustrations(
        "求角度 ILLUSTRATION_BOX 10,20,90,80，并说明理由"
    ) == "求角度，并说明理由"
    assert process_ocr_illustrations(
        "<|box_start|>12 34<|box_end|>正文"
    ) == "正文"
    assert process_ocr_illustrations("无标签正文") == "无标签正文"
    assert process_ocr_illustrations("") == ""


def _sent_prompt(mock_post) -> str:
    """把 post_chat_completion 收到的 payload 序列化出来，便于断言 prompt。"""
    assert mock_post.call_args is not None, "未发出 chat-completion 请求"
    payload = mock_post.call_args.args[1] if len(mock_post.call_args.args) > 1 else None
    if payload is None:
        payload = mock_post.call_args.kwargs.get("payload")
    return json.dumps(payload, ensure_ascii=False)


def test_pdf_page_ocr_requests_illustration_coordinates(page_png):
    """PDF 整页 OCR 必须在 prompt 里带上整页版插图坐标说明。"""
    from mathbank.prompts import (
        ILLUSTRATION_BOX_PROMPT,
        PDF_PAGE_ILLUSTRATION_BOX_PROMPT,
    )
    from main import ocr_pdf_page_image

    provider = MagicMock(provider_label="fake", provider_code="fake", model_name="m")
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "choices": [{"message": {"content": "整页识别结果"}}]
    }

    with patch("main.resolve_ocr_fallbacks", return_value=[provider]), patch(
        "main.post_chat_completion", return_value=response
    ) as mock_post:
        result = ocr_pdf_page_image(page_png)

    assert result == "整页识别结果"
    prompt = _sent_prompt(mock_post)
    assert "ILLUSTRATION_BOX" in prompt, "整页 OCR 必须请求插图坐标"
    # 整页版说明必须覆盖「仅一幅」的限制
    assert "多幅" in PDF_PAGE_ILLUSTRATION_BOX_PROMPT
    assert PDF_PAGE_ILLUSTRATION_BOX_PROMPT.strip()[:16] in prompt
    assert "多图或无图时绝不输出该标记" not in prompt
    # 单题版文案保持原样，未被整页版污染
    assert "多图或无图时绝不输出该标记" in ILLUSTRATION_BOX_PROMPT


def test_single_question_ocr_still_uses_the_narrow_box_prompt(page_png):
    from mathbank.prompts import ILLUSTRATION_BOX_PROMPT
    from main import ocr_via_provider

    provider = MagicMock(provider_label="fake", provider_code="fake", model_name="m")
    response = MagicMock(status_code=200)
    response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}

    with patch("main.post_chat_completion", return_value=response) as mock_post:
        ocr_via_provider(page_png, provider, include_illustration_box=True)

    prompt = _sent_prompt(mock_post)
    assert ILLUSTRATION_BOX_PROMPT.strip()[:10] in prompt
    assert "多图或无图时绝不输出该标记" in prompt


def test_ocr_without_box_flag_sends_no_box_instructions(page_png):
    from main import ocr_via_provider

    provider = MagicMock(provider_label="fake", provider_code="fake", model_name="m")
    response = MagicMock(status_code=200)
    response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}

    with patch("main.post_chat_completion", return_value=response) as mock_post:
        ocr_via_provider(page_png, provider)

    assert "ILLUSTRATION_BOX" not in _sent_prompt(mock_post)


def test_post_process_maps_crops_by_filename_not_document_position(tmp_path):
    """多页并行 OCR 时 mtime 顺序 ≠ 文档顺序，映射必须按文件名。"""
    from main import post_process_pdf_parsed_questions

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    for name in (
        "pdf_crop_taskF_5_0.png",
        "pdf_crop_taskF_2_0.png",
    ):
        (tmp_dir / name).write_bytes(b"png")
    # 故意让 mtime 顺序与文档顺序相反
    os.utime(tmp_dir / "pdf_crop_taskF_2_0.png", (2_000_000_000, 2_000_000_000))
    os.utime(tmp_dir / "pdf_crop_taskF_5_0.png", (1_000_000_000, 1_000_000_000))

    questions = [
        {
            "content": "第 5 页的题 ![图](tmp/pdf_crop_taskF_5_0.png)",
            "answer_markdown": "",
        },
        {
            "content": "第 2 页的题 ![图](tmp/pdf_crop_taskF_2_0.png)",
            "answer_markdown": "",
        },
    ]

    with patch("main.TMP_UPLOAD_DIR", str(tmp_dir)):
        result = post_process_pdf_parsed_questions(questions, "测试卷", "taskF")

    # 图片被晋升进 image_paths；按位置硬映射会把两张图对调。
    assert [os.path.basename(u) for u in result[0]["image_paths"]] == [
        "pdf_crop_taskF_5_0.png"
    ]
    assert [os.path.basename(u) for u in result[1]["image_paths"]] == [
        "pdf_crop_taskF_2_0.png"
    ]


def test_post_process_falls_back_to_positional_mapping_for_model_invented_names(tmp_path):
    from main import post_process_pdf_parsed_questions

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    for name in ("pdf_crop_taskG_1_0.png", "pdf_crop_taskG_1_1.png"):
        (tmp_dir / name).write_bytes(b"png")

    questions = [
        {"content": "第一题 ![图](图1.png)", "answer_markdown": ""},
        {"content": "第二题 ![图](图2.png)", "answer_markdown": ""},
    ]

    with patch("main.TMP_UPLOAD_DIR", str(tmp_dir)):
        result = post_process_pdf_parsed_questions(questions, "测试卷", "taskG")

    assert [os.path.basename(u) for u in result[0]["image_paths"]] == [
        "pdf_crop_taskG_1_0.png"
    ]
    assert [os.path.basename(u) for u in result[1]["image_paths"]] == [
        "pdf_crop_taskG_1_1.png"
    ]
    joined = " ".join(q.get("content", "") for q in result)
    assert "图1.png" not in joined
    assert "图2.png" not in joined


def test_resolved_crop_is_registered_as_a_task_temp_asset(page_png, tmp_uploads):
    """裁出来的插图必须登记到任务记录，否则会被启动孤儿清理当垃圾删掉。"""

    import main
    from mathbank.task_manager import TaskManager
    from main import resolve_ocr_illustration_boxes

    manager = TaskManager()
    task_id = "task-registers-crops"
    manager.create(task_id, document_type="pdf")

    original = main.DOCUMENT_TASKS
    main.DOCUMENT_TASKS = manager
    try:
        resolved = resolve_ocr_illustration_boxes(
            "如图 [ILLUSTRATION_BOX: 10, 10, 20, 20] 所示",
            page_png,
            task_id,
            2,
        )
        assert "pdf_crop_task-registers-crops_2_0.png" in resolved

        registered = manager.live_temp_assets()
        assert (
            f"/{main.UPLOAD_DIR_REL}/tmp/pdf_crop_task-registers-crops_2_0.png"
            in registered
        )
    finally:
        main.DOCUMENT_TASKS = original


# --------------------------------------------------------------------------
# 兜底归属：末题吞掉整卷图片的回归
# --------------------------------------------------------------------------

def test_overlap_helper_returns_negative_when_nothing_matches():
    """零重合时必须返回 -1；旧实现退回第 0 页会让下游误挂第 1 页的全部插图。"""
    from mathbank.routes_ai import find_source_page_by_overlap

    pages = ["第一页讲的是三角函数与单位圆", "第二页讲的是立体几何与体积"]
    assert find_source_page_by_overlap("完全不相干的题干内容", pages) == -1
    assert find_source_page_by_overlap("", pages) == -1
    assert find_source_page_by_overlap("三角函数与单位圆", pages) == 0
    assert find_source_page_by_overlap("立体几何与体积", pages) == 1


def test_failsafe_skips_questions_without_any_page_overlap(tmp_path):
    """与任何页都对不上的题（如末题）不得被兜底塞进第 1 页的全部插图。"""
    from main import post_process_pdf_parsed_questions

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    for name in ("pdf_crop_taskH_0_0.png", "pdf_crop_taskH_0_1.png"):
        (tmp_dir / name).write_bytes(b"png")

    crop_a = "/static/uploads/tmp/pdf_crop_taskH_0_0.png"
    crop_b = "/static/uploads/tmp/pdf_crop_taskH_0_1.png"
    ocr_results = [f"第一页题干。插图一 {crop_a} 插图二 {crop_b}"]

    questions = [
        # 显式带图引用，正常认领一张
        {"content": f"第一题 ![图]({crop_a})", "answer_markdown": ""},
        # 末题：题干文字与该页毫无重合，且没有任何图片引用
        {"content": "最后一道题没有任何图像。", "answer_markdown": ""},
    ]

    with patch("main.TMP_UPLOAD_DIR", str(tmp_dir)):
        result = post_process_pdf_parsed_questions(
            questions, "测试卷", "taskH", ocr_results=ocr_results
        )

    assert [os.path.basename(u) for u in result[0]["image_paths"]] == [
        "pdf_crop_taskH_0_0.png"
    ]
    # 关键断言：末题不得继承任何插图
    assert not result[1].get("image_paths")


def test_failsafe_does_not_steal_crops_claimed_by_another_question(tmp_path):
    """兜底只能分配「无人认领」的裁剪图，不能把别题已认领的图搬走。"""
    from main import post_process_pdf_parsed_questions

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    for name in ("pdf_crop_taskI_0_0.png", "pdf_crop_taskI_0_1.png"):
        (tmp_dir / name).write_bytes(b"png")

    crop_a = "/static/uploads/tmp/pdf_crop_taskI_0_0.png"
    crop_b = "/static/uploads/tmp/pdf_crop_taskI_0_1.png"
    shared_text = "同一个页面上的两个小问都讲三角函数"
    ocr_results = [f"{shared_text} 插图 {crop_a} 与 {crop_b}"]

    questions = [
        # 显式认领 crop_a
        {"content": f"第一小问（三角函数）![图]({crop_a})", "answer_markdown": ""},
        # 未带引用但与本页高度重合：只应拿到剩下的 crop_b
        {"content": "第二小问也在讲三角函数", "answer_markdown": ""},
    ]

    with patch("main.TMP_UPLOAD_DIR", str(tmp_dir)):
        result = post_process_pdf_parsed_questions(
            questions, "测试卷", "taskI", ocr_results=ocr_results
        )

    assert [os.path.basename(u) for u in result[0]["image_paths"]] == [
        "pdf_crop_taskI_0_0.png"
    ]
    assert [os.path.basename(u) for u in result[1]["image_paths"]] == [
        "pdf_crop_taskI_0_1.png"
    ]


def test_failsafe_page_crops_are_in_numeric_document_order(tmp_path):
    """页内插图顺序必须按「页序_序号」数值排，不能受 set 哈希顺序影响。"""
    from main import post_process_pdf_parsed_questions

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    for name in ("pdf_crop_taskJ_0_2.png", "pdf_crop_taskJ_0_10.png", "pdf_crop_taskJ_0_1.png"):
        (tmp_dir / name).write_bytes(b"png")

    urls = " ".join(
        f"/static/uploads/tmp/{n}"
        for n in ("pdf_crop_taskJ_0_2.png", "pdf_crop_taskJ_0_10.png", "pdf_crop_taskJ_0_1.png")
    )
    ocr_results = [f"三角函数页面 {urls}"]

    questions = [{"content": "三角函数与单位圆", "answer_markdown": ""}]

    with patch("main.TMP_UPLOAD_DIR", str(tmp_dir)):
        result = post_process_pdf_parsed_questions(
            questions, "测试卷", "taskJ", ocr_results=ocr_results
        )

    assert [os.path.basename(u) for u in result[0]["image_paths"]] == [
        "pdf_crop_taskJ_0_1.png",
        "pdf_crop_taskJ_0_2.png",
        "pdf_crop_taskJ_0_10.png",
    ]


def test_post_process_records_source_page_for_every_question(tmp_path):
    """每一道题都要写入 `_source_page`，供前端把题号对到「本卷原页」。

    前端「配图总览 → 本卷原页」靠它标出「这道题疑似来自第 N 页」，用户才能
    对着原页核对「这题到底有没有图」。若只给没配图的题算，有图的题就反查不了。
    推断不出时必须是 -1，绝不能退回第 0 页（那会把题号和第一页错误绑定）。
    """
    from main import post_process_pdf_parsed_questions

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()

    ocr_results = [
        "第一页讲三角函数与单位圆的性质",
        "第二页讲立体几何与三视图",
    ]
    questions = [
        {"content": "三角函数与单位圆", "answer_markdown": ""},
        {"content": "立体几何与三视图", "answer_markdown": ""},
        {"content": "完全不相干的收尾套话", "answer_markdown": ""},
    ]

    with patch("main.TMP_UPLOAD_DIR", str(tmp_dir)):
        result = post_process_pdf_parsed_questions(
            questions, "测试卷", "taskK", ocr_results=ocr_results
        )

    assert result[0]["_source_page"] == 0
    assert result[1]["_source_page"] == 1
    assert result[2]["_source_page"] == -1


# --------------------------------------------------------------------------
# MinerU 落盘插图的认领（静态根目录、mineru_ 前缀、不带 /tmp/）
# --------------------------------------------------------------------------

def test_post_process_collects_staged_mineru_images(tmp_path):
    """MinerU 落盘插图必须进 image_paths。

    历史 bug：收集插图引用的正则只认 `/static/<uploads>/tmp/...`，而 MinerU
    (`stage_images`) 把插图复制到静态**根目录**、URL 形如
    `/static/uploads/mineru_<ts>_<idx>.jpg`，于是整卷配图恒为「0 张」——
    配图总览显示无配图、插图全被列成「未归属」，而图其实一直躺在根目录里。
    """
    from main import post_process_pdf_parsed_questions

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    staged = "/static/uploads/mineru_1758100000_1.jpg"
    page = "/static/uploads/pdf_page_taskM_0.png"

    questions = [{
        "content": f"第 3 题：如图 ![图]({staged}) 所示，函数图象为（ ）",
        "answer_markdown": "",
    }]

    with patch("main.TMP_UPLOAD_DIR", str(tmp_dir)):
        result = post_process_pdf_parsed_questions(questions, "测试卷", "taskM", None)

    assert result[0]["image_paths"] == [staged]
    # 整页原图绝不能混进 image_paths（否则每道题都会挂一张整页图）
    assert page not in result[0]["image_paths"]
    # 题干里的内联引用被静默摘掉，改由配图条展示（避免重复渲染）
    assert "mineru_1758100000_1.jpg" not in result[0]["content"]


def test_post_process_collects_staged_images_from_referenced_images(tmp_path):
    """模型把 MinerU 插图只写在 referenced_images 里时也要认领。"""
    from main import post_process_pdf_parsed_questions

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    staged = "/static/uploads/mineru_1758100000_2.jpg"

    questions = [{
        "content": "第 5 题：求函数的最值。",
        "answer_markdown": "",
        "referenced_images": [staged],
    }]

    with patch("main.TMP_UPLOAD_DIR", str(tmp_dir)):
        result = post_process_pdf_parsed_questions(questions, "测试卷", "taskN", None)

    assert result[0]["image_paths"] == [staged]


def test_post_process_ignores_unrelated_static_images(tmp_path):
    """静态根目录里与题目无关的图（整页原图、用户自行上传的图）不得被认领。"""
    from main import post_process_pdf_parsed_questions

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    page = "/static/uploads/pdf_page_taskO_0.png"
    manual = "/static/uploads/user_manual_upload.png"

    questions = [{
        "content": f"第 1 题：共 {page} 与 {manual} 两张无关图片。",
        "answer_markdown": "",
    }]

    with patch("main.TMP_UPLOAD_DIR", str(tmp_dir)):
        result = post_process_pdf_parsed_questions(questions, "测试卷", "taskO", None)

    assert result[0]["image_paths"] == []


# --------------------------------------------------------------------------
# 选项图：必须留在题干里跟着选项走
# --------------------------------------------------------------------------

def test_post_process_keeps_choice_images_in_content(tmp_path):
    """选项图引用必须留在题干里（`A．![](url)`），不能当普通配图删掉。

    第 6 步原先把题干里所有已认领的图片引用**一律**删除（避免与配图条重复渲染），
    结果选项图被抽走，题干退化成「A．/B．/C．/D．」四个空壳，选项与图形彻底错位。
    """
    from main import post_process_pdf_parsed_questions

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    img_a = "/static/uploads/mineru_1_1.jpg"
    img_b = "/static/uploads/mineru_1_2.jpg"
    img_body = "/static/uploads/mineru_1_3.jpg"

    questions = [{
        "content": (
            "1.下列各曲线中，不能表示 y 是 x 的函数的是（ ）\n\n"
            "A．![](" + img_a + ")\n\n"
            "B．![](" + img_b + ")\n\n"
            "C．\n\n"
            "D．\n\n"
            "如图 ![](" + img_body + ") 所示"
        ),
        "answer_markdown": "",
    }]

    with patch("main.TMP_UPLOAD_DIR", str(tmp_dir)):
        result = post_process_pdf_parsed_questions(questions, "测试卷", "taskP", None)

    content = result[0]["content"]
    # 选项图（全角「A．」/「B．」行）必须保留
    assert "mineru_1_1.jpg" in content
    assert "mineru_1_2.jpg" in content
    # 普通题干配图应被摘掉，改由配图条统一展示（避免同一张图渲染两次）
    assert "mineru_1_3.jpg" not in content
    # 三张图仍然都归本题（生命周期依据不能丢）
    assert len(result[0]["image_paths"]) == 3


def test_duplicated_image_reference_is_removed_from_content(tmp_path):
    """被跨题去重摘掉的图，正文里的引用也要一并删掉。

    否则界面会在第二题里渲染出一张**属于第一题**的图（用户实测遇到过
    「第 5 题题干里挂着第 4 题的直方图」这种错位）。
    """
    from main import post_process_pdf_parsed_questions

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    shared = "/static/uploads/mineru_2_1.jpg"

    questions = [
        {"content": "第 1 题 如图 ![](" + shared + ") 所示", "answer_markdown": ""},
        {"content": "第 2 题 又引用了它 ![](" + shared + ") 所示", "answer_markdown": ""},
    ]

    with patch("main.TMP_UPLOAD_DIR", str(tmp_dir)):
        result = post_process_pdf_parsed_questions(questions, "测试卷", "taskQ", None)

    assert result[0]["image_paths"] == [shared]


def test_escaped_newline_before_uppercase_is_restored(tmp_path):
    r"""字面 ``\n`` 后面跟大写字母时也必须还原成换行（MinerU 路线选项被污染的根因）。

    旧判据是 ``\\n(?![a-zA-Z])``，会放过 ``\nA``；残留的「反斜杠+n+大写字母」
    随后被公式补定界符逻辑当成 LaTeX 命令 ``nA`` 包成 ``$\nA$``，整段选项
    被污染成 ``$\nA$．2 条 B．4 条…``。
    """
    from main import post_process_pdf_parsed_questions

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()

    questions = [{
        "content": "1.如图，与棱 AB 垂直的棱有（ ）\\nA．2 条 B．4 条 C．6 条 D．8 条",
        "question_type": "single_choice",
        "answer_markdown": "",
    }]

    with patch("main.TMP_UPLOAD_DIR", str(tmp_dir)):
        result = post_process_pdf_parsed_questions(questions, "测试卷", "taskN", None)

    content = result[0]["content"]
    assert "\\nA" not in content          # 字面转义已还原
    assert "$\\n" not in content          # 没有把转义符包进公式
    # 还原成真换行后，选项行能被正常转成 choices 环境
    assert "\\begin{choices}" in content
    assert "\\item 2 条" in content
    assert "\\item 8 条" in content


def test_escaped_newline_before_lowercase_command_is_preserved(tmp_path):
    r"""``\nabla`` / ``\neq`` / ``\nu`` 这类**真** LaTeX 命令不得被当成转义换行。

    判据收紧到小写字母之后，必须保证这些以 ``\n`` 开头的真命令原样保留。
    """
    from main import post_process_pdf_parsed_questions

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()

    questions = [{
        "content": r"设 $\nabla f$ 与 $\nu$ 满足 $a\neq b$，求值。",
        "question_type": "detailed_answer",
        "answer_markdown": "",
    }]

    with patch("main.TMP_UPLOAD_DIR", str(tmp_dir)):
        result = post_process_pdf_parsed_questions(questions, "测试卷", "taskO", None)

    content = result[0]["content"]
    assert r"\nabla" in content
    assert r"\nu" in content
    assert r"\neq" in content


def test_choice_images_backfilled_when_ai_drops_them(tmp_path):
    r"""AI 只把选项图列进 referenced_images、正文不写引用时，要按顺序补回。

    这是 step 6「选项图必须留下」的反向补丁。回填必须发生在 step 6 **之后**：
    AI 若把图写成独立的 `![...]` 行，step 6 会先剥掉，此时才看得出选项区缺图。
    """
    from main import post_process_pdf_parsed_questions

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    urls = [f"/static/uploads/tmp/pdf_crop_taskM_0_{i}.png" for i in range(4)]

    questions = [{
        "content": "1.下列各曲线中，不能表示 y 是 x 的函数的是（ ）\\nA\\nB\\nC\\nD",
        "question_type": "single_choice",
        "answer_markdown": "",
        "referenced_images": [u.replace("/static/uploads", "/tmp") for u in urls],
    }]
    # 让 image_paths 直接落位（模拟 step 4 已认领）
    for u in urls:
        (tmp_dir / u.rsplit("/", 1)[-1]).write_bytes(b"x")

    with patch("main.TMP_UPLOAD_DIR", str(tmp_dir)):
        result = post_process_pdf_parsed_questions(questions, "测试卷", "taskM", None)

    content = result[0]["content"]
    for i in range(4):
        assert f"pdf_crop_taskM_0_{i}.png" in content, f"第 {i} 个选项图未回填"
    # 四个空壳字母被图片替换，且图片走选项内联限宽（0.42）
    assert content.count("\\includegraphics") == 4
    assert "max width=0.42\\linewidth" in content
    assert "\\begin{choices}" not in content  # choices 环境由后续 latexify_choices 生成


def test_choice_image_kept_even_when_owned_by_another_question(tmp_path):
    """即便某张图已被别题认领，它在本题的**选项行**上的引用也要保留。

    选项图是选项本身的一部分，删了选项就成空壳；宁可留图（再由人工判断归属），
    也不要留一个没有图形的选项。
    """
    from main import post_process_pdf_parsed_questions

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    shared = "/static/uploads/mineru_3_1.jpg"

    questions = [
        {"content": "第 1 题 如图 ![](" + shared + ") 所示", "answer_markdown": ""},
        {"content": "第 2 题\n\nA．![](" + shared + ")\n\nB．", "answer_markdown": ""},
    ]

    with patch("main.TMP_UPLOAD_DIR", str(tmp_dir)):
        result = post_process_pdf_parsed_questions(questions, "测试卷", "taskR", None)

        assert result[1]["image_paths"] == []
        # 彻底 LaTeX 化后正文只写 \includegraphics{文件名}（basename，无路径），
        # 选项图必须仍以 basename 形式内联保留在 content 里（不能因被别题认领而被剥离）。
        assert "mineru_3_1.jpg" in result[1]["content"]


def test_choice_images_are_recognized_across_lines(tmp_path):
    """选项标号单独一行、图形另起一行时，选项图同样必须保留。

    这是 AI 的**主要输出形态**（用户那卷实测就是这种）。早期版本只认「标号与图同行」，
    结果选项图照样被删 —— 判据必须覆盖真实形态，而不是照 PDF 原文的排版想当然。

    同时要防误伤：紧跟选项行之后的**普通**配图（行内还有别的文字，如
    `如图 ![](url) 所示`）不能被当成选项图留下。
    """
    from main import post_process_pdf_parsed_questions

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    imgs = ["/static/uploads/mineru_%d.jpg" % i for i in (2, 5, 7, 9)]
    body = "/static/uploads/mineru_body.jpg"

    content = (
        "下列各曲线中，不能表示 $y$ 是 $x$ 的函数的是（ ）\n\n"
        + "".join(
            label + "\n\n![](" + url + ")\n\n" for label, url in zip("ABCD", imgs)
        )
        + "如图 ![](" + body + ") 所示"
    )

    with patch("main.TMP_UPLOAD_DIR", str(tmp_dir)):
        result = post_process_pdf_parsed_questions(
            [{"content": content, "answer_markdown": ""}], "测试卷", "taskS", None
        )

        out = result[0]["content"]
        for url in imgs:
            # 彻底 LaTeX 化后正文只写 \includegraphics{文件名}（basename），断言 basename 仍内联保留。
            assert url.split("/")[-1] in out, url
    assert body not in out


# --------------------------------------------------------------------------
# A. 一图只归一道题 + 题内图序 = 正文首次出现顺序
# --------------------------------------------------------------------------

def test_cross_question_dedup_keeps_first_owner(tmp_path):
    """同一张裁剪图不允许同时归属两道题（业务硬约束：共用会让删题互相踩引用）。"""
    from main import post_process_pdf_parsed_questions

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    (tmp_dir / "pdf_crop_taskK_1_0.png").write_bytes(b"png")

    crop = "/static/uploads/tmp/pdf_crop_taskK_1_0.png"
    questions = [
        {"content": f"第一题 ![图]({crop})", "answer_markdown": ""},
        {"content": f"第二题也引用了同一张 ![图]({crop})", "answer_markdown": ""},
    ]

    with patch("main.TMP_UPLOAD_DIR", str(tmp_dir)):
        result = post_process_pdf_parsed_questions(questions, "测试卷", "taskK")

    assert [os.path.basename(u) for u in result[0]["image_paths"]] == [
        "pdf_crop_taskK_1_0.png"
    ]
    # 后引用者必须被摘掉，而不是两题共用
    assert result[1]["image_paths"] == []


def test_question_images_keep_first_appearance_order(tmp_path):
    """题内配图顺序 = 正文首次出现顺序，不受 set 哈希顺序影响。"""
    from main import post_process_pdf_parsed_questions

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    for name in (
        "pdf_crop_taskL_1_0.png",
        "pdf_crop_taskL_1_1.png",
        "pdf_crop_taskL_1_2.png",
    ):
        (tmp_dir / name).write_bytes(b"png")

    p0 = "/static/uploads/tmp/pdf_crop_taskL_1_0.png"
    p1 = "/static/uploads/tmp/pdf_crop_taskL_1_1.png"
    p2 = "/static/uploads/tmp/pdf_crop_taskL_1_2.png"
    questions = [
        {"content": f"先看 ![c]({p2}) 再看 ![b]({p1}) 最后 ![a]({p0})", "answer_markdown": ""}
    ]

    with patch("main.TMP_UPLOAD_DIR", str(tmp_dir)):
        result = post_process_pdf_parsed_questions(questions, "测试卷", "taskL")

    assert [os.path.basename(u) for u in result[0]["image_paths"]] == [
        "pdf_crop_taskL_1_2.png",
        "pdf_crop_taskL_1_1.png",
        "pdf_crop_taskL_1_0.png",
    ]


# --------------------------------------------------------------------------
# C. 页眉/页脚/水印/装饰块过滤
# --------------------------------------------------------------------------

def test_page_furniture_filter_identifies_header_and_tiny_blocks():
    """页边距带内的小块、碎块判为页眉页脚装饰；正常插图与细长数轴不得误伤。"""
    from mathbank.illustration_crop import _page_furniture_reason

    # 页眉带内的 logo 块（页面 1000x1400，页眉带 = 顶部 56px）
    assert _page_furniture_reason(20, 2, 300, 40, 1000, 1400)
    # 页脚带内的机构条
    assert _page_furniture_reason(100, 1380, 900, 1398, 1000, 1400)
    # 整页 0.3% 以下的碎块
    assert _page_furniture_reason(500, 600, 510, 610, 1000, 1400)
    # 页面中部的正常插图
    assert _page_furniture_reason(100, 400, 500, 700, 1000, 1400) == ""
    # 细长但面积不小的图（数轴、区间条）不能被误删
    assert _page_furniture_reason(100, 500, 900, 540, 1000, 1400) == ""


def test_header_logo_box_is_not_cropped(page_png, tmp_uploads):
    """落在页眉带里的小框不应产出裁剪图，避免页眉 logo 混进题目插图。"""
    from main import resolve_ocr_illustration_boxes

    # page_png 是 200x400 -> 页眉带 = 顶部 16px；该框 1%~3% 高度全部落在带内
    text = "1. 如图 [ILLUSTRATION_BOX: 1, 10, 3, 30] 所示，求角度。"

    resolved = resolve_ocr_illustration_boxes(text, page_png, "taskM", 0)

    assert "![插图]" not in resolved
    assert os.listdir(tmp_uploads) == []


# --------------------------------------------------------------------------
# D. 配图自检标记
# --------------------------------------------------------------------------

def test_image_review_flags_failsafe_assignments(tmp_path):
    """兜底推断出来的配图必须打上待核对标记，正常认领的不带标记。"""
    from main import post_process_pdf_parsed_questions

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    for name in ("pdf_crop_taskN_0_0.png", "pdf_crop_taskN_0_1.png"):
        (tmp_dir / name).write_bytes(b"png")

    crop_a = "/static/uploads/tmp/pdf_crop_taskN_0_0.png"
    crop_b = "/static/uploads/tmp/pdf_crop_taskN_0_1.png"
    ocr_results = [f"三角函数与单位圆。插图 {crop_a} 与 {crop_b}"]

    questions = [
        {"content": f"三角函数题 ![图]({crop_a})", "answer_markdown": ""},
        {"content": "也讲三角函数与单位圆", "answer_markdown": ""},
    ]

    with patch("main.TMP_UPLOAD_DIR", str(tmp_dir)):
        result = post_process_pdf_parsed_questions(
            questions, "测试卷", "taskN", ocr_results=ocr_results
        )

    assert "_image_review" not in result[0]
    assert result[1]["_image_review"] == ["failsafe"]


def test_image_review_flags_suspiciously_many_images(tmp_path):
    """一题挂到 >=3 张图时提示人工核对；正常题不带标记。"""
    from main import post_process_pdf_parsed_questions

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    names = [f"pdf_crop_taskO_1_{i}.png" for i in range(3)]
    for name in names:
        (tmp_dir / name).write_bytes(b"png")

    links = " ".join(f"![图](/static/uploads/tmp/{n})" for n in names)
    questions = [
        {"content": f"挂了很多图 {links}", "answer_markdown": ""},
        {"content": "只有一张 ![图](/static/uploads/tmp/pdf_crop_taskO_1_0.png)", "answer_markdown": ""},
    ]

    with patch("main.TMP_UPLOAD_DIR", str(tmp_dir)):
        result = post_process_pdf_parsed_questions(questions, "测试卷", "taskO")

    assert result[0]["_image_review"] == ["many"]
    # 第二题因跨题去重拿不到图，也不应被标记
    assert "_image_review" not in result[1]
