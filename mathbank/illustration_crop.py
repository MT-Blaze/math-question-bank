"""Illustration coordinate handling for OCR flows.

Two responsibilities, both kept here so ``main.py`` stays a thin entry point:

* :func:`resolve_ocr_illustration_boxes` turns ``[ILLUSTRATION_BOX: ...]``
  labels emitted by the vision model into real cropped PNG assets and replaces
  the label in place with a ``tmp/...`` markdown placeholder.
* :func:`process_ocr_illustrations` erases any leftover box label so that raw
  coordinates never leak into question content.

The label grammar is deliberately tolerant: models drift between square /
round / full-width brackets, between spaces and full-width commas, and
occasionally append a ``%`` sign.  Coordinates are always percentages of the
whole page (0-100), so anything outside that range is treated as a
mis-formatted label rather than being clamped into a nonsense crop.
"""

import os
import re

from main import TMP_UPLOAD_DIR, UPLOAD_DIR_REL

# Number token shared by the tolerant parsers below (integers or decimals).
_NUM = r"\d{1,3}(?:\.\d+)?"

# `[ILLUSTRATION_BOX: ymin, xmin, ymax, xmax]` with permissive brackets/commas.
_BOX_LABEL_RE = re.compile(
    r"[\[\(（]\s*ILLUSTRATION_BOX\s*[:：]?\s*"
    r"(" + _NUM + r")\s*[%％]?\s*[,，]\s*"
    r"(" + _NUM + r")\s*[%％]?\s*[,，]\s*"
    r"(" + _NUM + r")\s*[%％]?\s*[,，]\s*"
    r"(" + _NUM + r")\s*[%％]?\s*[\]\)）]",
    re.IGNORECASE,
)

# Erasers for labels the model mangled so badly that no crop was possible.
_LABEL_ERASERS = (
    re.compile(r"(?is)<\|box_start\|>.{0,200}?<\|box_end\|>"),
    re.compile(
        r"(?i)[\[\(（]\s*ILLUSTRATION_BOX[^\n\r\]\)）]{0,120}[\]\)）]?"
    ),
    re.compile(
        r"(?i)ILLUSTRATION_BOX\s*[:：]?\s*"
        + _NUM
        + r"\s*[%％]?"
        + r"(?:\s*[,，]\s*" + _NUM + r"\s*[%％]?){0,3}"
        + r"\s*[\]\)）]?"
    ),
)

# A crop smaller than this (px, either axis) is almost certainly a bad label.
_MIN_CROP_PX = 8

# 页眉/页脚/水印过滤阈值（都是相对整页的比例）。
# 视觉模型偶尔会把页眉的机构 logo、页脚装饰条也框进来，那些块和真正的题目插图
# 的区别是「位置贴页边 + 面积小」，所以用组合条件判断，避免误伤正常插图。
_PAGE_FURNITURE_BAND_RATIO = 0.04   # 页顶/页底 4% 视为页眉页脚带
_PAGE_FURNITURE_MAX_AREA = 0.10     # 页边距带内、小于整页 10% 才判为 logo/装饰
_TINY_CROP_AREA_RATIO = 0.003       # 小于整页 0.3% 的碎块（角标/装饰点）


def _page_furniture_reason(left, upper, right, lower, page_width, page_height) -> str:
    """裁剪块是否像页眉/页脚/水印/装饰块？返回原因字符串，正常插图返回 ``""``。

    刻意**不**使用「极端宽高比」这条判据：数学里细长的图很常见（数轴、区间条），
    误判会把真正的插图删掉。宁可漏过滤，也不要删掉合法图形。
    """
    if page_width <= 0 or page_height <= 0:
        return ""
    box_w = right - left
    box_h = lower - upper
    if box_w <= 0 or box_h <= 0:
        return ""
    area_ratio = (box_w * box_h) / float(page_width * page_height)

    if area_ratio < _TINY_CROP_AREA_RATIO:
        return f"面积过小({area_ratio:.3%})"

    top_band = upper < page_height * _PAGE_FURNITURE_BAND_RATIO
    bottom_band = lower > page_height * (1 - _PAGE_FURNITURE_BAND_RATIO)
    if (top_band or bottom_band) and area_ratio < _PAGE_FURNITURE_MAX_AREA:
        where = "页眉" if top_band else "页脚"
        return f"{where}带内小块(面积 {area_ratio:.1%})"

    return ""


def _upload_dir_rel() -> str:
    """Relative upload prefix, read from ``main`` at call time (patchable)."""

    try:
        import main as _main

        return getattr(_main, "UPLOAD_DIR_REL", None) or UPLOAD_DIR_REL
    except Exception:  # pragma: no cover - defensive
        return UPLOAD_DIR_REL


def _register_task_temp_asset(task_id: str, filename: str) -> None:
    """把裁出来的插图登记到任务记录。

    登记后它才受「完成结果保留 1 小时供前端导入」的契约保护：任务过期时由
    task_manager 统一删除。**不登记的话**，它既不在任务资产表里、也不在数据库
    image_paths 里，启动时的孤儿清理会直接把它当垃圾抹掉——用户看到的现象就是
    "刚拆完的卷子，插图怎么又没了"。
    """

    if not task_id:
        return
    try:
        import main as _main

        tasks = getattr(_main, "DOCUMENT_TASKS", None)
        if tasks is None:
            return
        tasks.add_temp_asset(task_id, f"/{_upload_dir_rel()}/tmp/{filename}")
    except Exception:  # pragma: no cover - 登记失败不该影响裁图
        pass


def _tmp_upload_dir() -> str:
    """Resolve the scratch upload dir at call time.

    Reading ``main.TMP_UPLOAD_DIR`` live (rather than only at import time) keeps
    the value patchable from tests, matching how the rest of the project is
    exercised.
    """
    try:
        import main as _main

        return getattr(_main, "TMP_UPLOAD_DIR", None) or TMP_UPLOAD_DIR
    except Exception:  # pragma: no cover - defensive
        return TMP_UPLOAD_DIR


def _parse_box(raw):
    """Return ``(ymin, xmin, ymax, xmax)`` as floats, or ``None`` when unusable.

    坐标口径有三种，模型并不总遵守提示词的「0-100 百分比」约定：

    ① **0-100 百分比**（提示词约定值）——原样采用；
    ② **0-1 归一化**（``0.12`` 表示 12%）——四个值全 ≤ 1 时乘 100；
    ③ **0-1000**（实测 ``115, 180, 240, 360``）——折合除以 10。

    判据刻意**不要求「四个值全部越界」**：实测出现过 ``90, 150, 230, 430``
    这种前几个值看起来像百分比、其实整组都是 0-1000 的混合形态，旧判据认不出，
    框会被静默丢弃，现象就是「这道题的图凭空消失」（第 4 题直方图）。
    现在改为「**至少两个**值超出 100」即认定 0-1000；只要求一个值越界
    仍保守放弃（``ymax=105`` 这类小幅越界不足以证明是 0-1000，
    硬除会得到完全错误的裁剪区）。
    """

    try:
        ymin, xmin, ymax, xmax = (float(v) for v in raw)
    except (TypeError, ValueError):
        return None
    values = (ymin, xmin, ymax, xmax)

    def _valid_as_percent(box) -> bool:
        a, b, c, d = box
        return 0 <= a < c <= 100 and 0 <= b < d <= 100

    # ① 0-1 归一化口径
    if not all(v == 0 for v in values) and all(v <= 1.0 for v in values):
        values = tuple(v * 100 for v in values)
    # ② 0-1000 口径：≥2 个值越界，且折合后是合法的百分比框
    elif (
        sum(1 for v in values if v > 100) >= 2
        and all(0 <= v <= 1000 for v in values)
    ):
        folded = tuple(v / 10 for v in values)
        if _valid_as_percent(folded):
            print(
                "[OCR Illustration Crop] 插图坐标按 0-1000 口径输出，已折合为百分比:"
                f" {values} -> ({', '.join(f'{v:g}' for v in folded)})",
                flush=True,
            )
            values = folded

    if not _valid_as_percent(values):
        return None
    return values


def _tighten_to_content(img, pad_px=6, guard_px=4):
    """把模型给的偏大框收紧到图形内容本身，返回新的裁剪框或 ``None``。

    整页 OCR 时模型给出的百分比框经常**偏大**：把题号「1.」、图题「如图」、
    上下两行题干文字一并框进去，用户看到的就是「框到文字上」。这里用
    「四边像素的中位数 = 纸白背景」估算非背景像素的外接框，把裁剪结果收紧过去。

    安全线：只要图形任一边缘贴到裁剪框边（``guard_px`` 以内），说明模型的框
    本来就贴着图形，此时不收紧——避免把图形自身裁掉。
    """
    width, height = img.size
    if width < _MIN_CROP_PX or height < _MIN_CROP_PX:
        return None

    gray = img.convert("L")
    scale = 1.0
    if max(width, height) > 240:
        scale = max(width, height) / 240.0
        gray = gray.resize(
            (max(1, int(width / scale)), max(1, int(height / scale)))
        )
    gw, gh = gray.size
    # 灰度模式下每个像素一个字节，直接取 bytes 比 getdata() 快且不触发弃用告警
    pixels = list(gray.tobytes())

    # 背景色：四边一圈像素的中位数（试卷插图几乎总贴着纸白）
    border = []
    for x in range(gw):
        border.append(pixels[x])
        border.append(pixels[(gh - 1) * gw + x])
    for y in range(gh):
        border.append(pixels[y * gw])
        border.append(pixels[y * gw + (gw - 1)])
    border.sort()
    bg = border[len(border) // 2]
    # 深底图片（bg 偏暗）用相对差，浅底用固定阈值
    threshold = max(26, int((255 - bg) * 0.22)) if bg < 235 else 28

    min_x, min_y, max_x, max_y = gw, gh, -1, -1
    for y in range(gh):
        row = y * gw
        for x in range(gw):
            if abs(pixels[row + x] - bg) > threshold:
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y
    if max_x < 0 or max_y < 0:
        return None

    left = int(min_x * scale)
    upper = int(min_y * scale)
    right = int((max_x + 1) * scale)
    lower = int((max_y + 1) * scale)
    # 图形贴边就不收紧，保住图本身
    if (
        left <= guard_px
        or upper <= guard_px
        or right >= width - guard_px
        or lower >= height - guard_px
    ):
        return None

    left = max(0, left - pad_px)
    upper = max(0, upper - pad_px)
    right = min(width, right + pad_px)
    lower = min(height, lower + pad_px)
    if right - left < _MIN_CROP_PX or lower - upper < _MIN_CROP_PX:
        return None
    return left, upper, right, lower


def erase_illustration_labels(text: str) -> str:
    """Strip every remaining illustration-box label from ``text``."""
    if not text:
        return text
    cleaned = text
    for pattern in _LABEL_ERASERS:
        cleaned = pattern.sub("", cleaned)
    # Collapse whitespace left behind by a removed inline label, including the
    # space that often ends up in front of following CJK punctuation.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+([，。；：、！？）】」》])", r"\1", cleaned)
    cleaned = re.sub(r"([（【「《])[ \t]+", r"\1", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def process_ocr_illustrations(text: str) -> str:
    """安全标签清洗：擦除视觉定位标签或 box 坐标标记，返回纯净 OCR 结果"""
    return erase_illustration_labels(text)


def resolve_ocr_illustration_boxes(
    text: str,
    page_image_path: str,
    task_id: str,
    page_num: int,
) -> str:
    """按 ``[ILLUSTRATION_BOX: ymin, xmin, ymax, xmax]`` 百分比坐标从整页 PNG 裁图，
    并在原位置替换为 ``![插图](tmp/pdf_crop_{task}_{page}_{idx}.png)`` 占位符。

    无法解析或退化的坐标会被跳过（随后由 :func:`process_ocr_illustrations` 擦除）。
    """
    if not text or not page_image_path or not os.path.exists(page_image_path):
        return text

    matches = list(_BOX_LABEL_RE.finditer(text))
    if not matches:
        return text

    parsed = []
    seen_boxes = set()
    for match in matches:
        box = _parse_box(match.groups())
        if box is None:
            # 坐标被丢弃时**必须留痕**：丢弃意味着这一步不裁图，随后
            # ``process_ocr_illustrations`` 会把标签一并擦掉，AI 拆题时完全看不到
            # 这里有图 —— 现象就是「图凭空消失」，且无任何报错可查。
            # 实测有整页 OCR 按 **0-1000** 口径输出（如 `115, 180, 240, 360`），
            # 提示词约定的是 0-100 百分比，这类标记会被静默丢掉。宁可吵一点，
            # 也不要让「图没了」变成一桩无头案。
            print(
                "[OCR Illustration Crop] 丢弃无法使用的插图坐标标记"
                f"（第 {page_num} 页）: {match.group(0)!r}",
                flush=True,
            )
            continue
        if box in seen_boxes:
            # Model repeated the same figure; keep a single crop.
            continue
        seen_boxes.add(box)
        parsed.append((match, box))
    if not parsed:
        return text

    try:
        from PIL import Image
    except Exception as exc:  # pragma: no cover - Pillow ships with the app
        print(f"[OCR Illustration Crop] 缺少图像库，跳过裁图: {exc}", flush=True)
        return text

    tmp_dir = _tmp_upload_dir()
    os.makedirs(tmp_dir, exist_ok=True)
    replacements = {}

    try:
        with Image.open(page_image_path) as img:
            width, height = img.size
            for idx, (match, (ymin, xmin, ymax, xmax)) in enumerate(parsed):
                left = int(xmin / 100 * width)
                upper = int(ymin / 100 * height)
                right = int(xmax / 100 * width)
                lower = int(ymax / 100 * height)
                pad = 4
                left = max(0, left - pad)
                upper = max(0, upper - pad)
                right = min(width, right + pad)
                lower = min(height, lower + pad)
                # 整页 OCR 时模型给的框经常偏大：把题号「1.」、图题「如图」、上下两行
                # 题干文字一并框进去，用户看到的就是「框到文字上」。这里按像素收紧到
                # 图形本身；图形已经贴框边时 _tighten_to_content 返回 None，保持原框。
                # 注意：返回值是相对**中间裁剪图**的坐标，必须平移回整页坐标系。
                tightened = _tighten_to_content(img.crop((left, upper, right, lower)))
                if tightened:
                    tl, tu, tr, lb = tightened
                    left, upper, right, lower = (
                        left + tl,
                        upper + tu,
                        left + tr,
                        upper + lb,
                    )
                if right - left < _MIN_CROP_PX or lower - upper < _MIN_CROP_PX:
                    continue
                # Hard bound: all coordinates are percentages of the page.
                if not (0 <= left < right <= width and 0 <= upper < lower <= height):
                    continue
                # 页眉/页脚/水印过滤：这类块一旦被当成题目插图，会顺着兜底逻辑到处
                # 乱挂（曾出现末题挂上页眉吉祥物 logo 的情况）。判定理由会打日志，
                # 便于发现误伤时回调阈值。
                furniture = _page_furniture_reason(left, upper, right, lower, width, height)
                if furniture:
                    print(
                        "[OCR Illustration Crop] 已丢弃疑似页眉/页脚/装饰块"
                        f"（{furniture}）: 第 {page_num} 页"
                        f" 框=({left}, {upper}, {right}, {lower}) 页面={width}x{height}",
                        flush=True,
                    )
                    continue
                crop = img.crop((left, upper, right, lower))
                fname = f"pdf_crop_{task_id}_{page_num}_{idx}.png"
                crop.save(os.path.join(tmp_dir, fname))
                _register_task_temp_asset(task_id, fname)
                replacements[match.group(0)] = f"![插图](tmp/{fname})"
    except Exception as exc:
        print(f"[OCR Illustration Crop] 裁图失败: {exc}", flush=True)
        return text

    if not replacements:
        return text

    # Replace literal label text (not a regex) so odd punctuation cannot
    # re-match something else in the page.
    for label, placeholder in replacements.items():
        text = text.replace(label, placeholder, 1)
    return text
