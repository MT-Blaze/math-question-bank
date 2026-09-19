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

# 内嵌位图小于这个边长（pt）时不当作图形锚点：水印 / Logo / 装饰点。
# 与 ``pdf_inspector_helper._extract_fitz_pages`` 的过滤口径保持一致。
_MIN_ANCHOR_PT = 20

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


def _rect_overlap(a, b) -> float:
    """两个 (left, upper, right, lower) 矩形的交集面积（px²，无交集为 0）。"""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return float(w * h) if w > 0 and h > 0 else 0.0


def _rect_area(r) -> float:
    return float(max(0, r[2] - r[0]) * max(0, r[3] - r[1]))


def _merge_touching_rects(rects):
    """把**互相交叠**的矩形并成簇（一幅图由多块位图拼成时不能裁成几段）。

    只合并真正相交者：选择题四个选项曲线是四个互不相交的矩形，必须保持四簇，
    否则一道题会把四幅选项图并成一张大图。
    """
    merged = [tuple(int(v) for v in r) for r in rects]
    changed = True
    while changed:
        changed = False
        out = []
        while merged:
            cur = merged.pop(0)
            for other in list(merged):
                if _rect_overlap(cur, other) > 0:
                    merged.remove(other)
                    cur = (min(cur[0], other[0]), min(cur[1], other[1]),
                           max(cur[2], other[2]), max(cur[3], other[3]))
                    changed = True
            out.append(cur)
        merged = out
    return merged


def page_figure_anchor_px(pdf_bytes, page_index, img_width, img_height):
    """该页**真实图形**的像素矩形列表（坐标相对整页渲染 PNG）。

    视觉模型的百分比框只能当「这一带有图」的线索，不能当裁剪边界：实测整页 OCR
    给出的框经常小于真实图形（频率分布直方图只框到「频率/组距」那一角，裁出来
    281x149px 的碎片），甚至整体偏移到图形上方的正文行（折线统计图裁成一段文字）。
    PDF 里的内嵌位图边界是确定信息，因此把它作为锚点去纠正模型框。

    无 PDF（单题识图路线）、PyMuPDF 不可用或该页没有位图时返回 ``[]``，
    调用方保持旧行为（直接采用模型框）。
    """
    if not pdf_bytes or img_width <= 0 or img_height <= 0:
        return []
    try:
        import pymupdf as fitz
    except Exception:  # pragma: no cover - PyMuPDF 随应用一起分发
        return []
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            if page_index is None or page_index < 0 or page_index >= len(doc):
                return []
            page = doc.load_page(page_index)
            pw, ph = float(page.rect.width), float(page.rect.height)
            if pw <= 0 or ph <= 0:
                return []
            sx, sy = img_width / pw, img_height / ph
            rects = []
            for info in page.get_image_info(xrefs=True):
                x0, y0, x1, y1 = info["bbox"]
                if (x1 - x0) < _MIN_ANCHOR_PT or (y1 - y0) < _MIN_ANCHOR_PT:
                    continue
                rects.append((
                    max(0, int(x0 * sx)),
                    max(0, int(y0 * sy)),
                    min(img_width, int(round(x1 * sx))),
                    min(img_height, int(round(y1 * sy))),
                ))
            return _merge_touching_rects(rects)
    except Exception as exc:  # pragma: no cover - 解析器异常不该拖垮 OCR 流程
        print(f"[OCR Illustration Crop] 读取页面图形锚点失败: {exc}", flush=True)
        return []


def _snap_box_to_anchor(box, anchors):
    """把模型框纠正为它所指的真实图形矩形；找不到可信锚点时返回 ``None``。

    ① 与锚点有任何交集 → 用该锚点（模型框落在图形内部或边缘都算）；命中多个互不相交
       的锚点时取**重合比例最高**的那一个，而不是把它们并起来——一个框吞两幅图就是
       串题。
    ② 完全不相交（模型框整块压在文字上）→ 只接受「横向基本对齐、纵向间隙小于一幅图
       高度」的最近锚点；仍不满足就放弃纠正，宁可用原框也不凭空搬一幅远处的图过来。
    """
    if not anchors:
        return None
    left, upper, right, lower = box
    hits = [a for a in anchors if _rect_overlap(a, box) > 0]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        best = max(
            hits,
            key=lambda a: (_rect_overlap(a, box) / max(1.0, _rect_area(a)), -_rect_area(a)),
        )
        print(
            "[OCR Illustration Crop] 模型框同时压到多幅图形，按重合比例只取其一"
            f"（避免一框吞多图）: {best}",
            flush=True,
        )
        return best
    best, best_gap = None, None
    for anchor in anchors:
        al, au, ar, ab = anchor
        horiz = min(right, ar) - max(left, al)
        if horiz < 0.4 * max(1, ar - al):
            continue
        gap = max(au - lower, upper - ab, 0)
        if gap > max(60.0, ab - au):
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = anchor, gap
    if best:
        print(
            "[OCR Illustration Crop] 模型框落在文字上，已吸附到同列最近的真实图形"
            f"（纵向偏移 {best_gap}px）: {best}",
            flush=True,
        )
    return best


def count_pdf_figures(pdf_bytes):
    """逐页数「PDF 里真实有几幅位图图形」（相邻矩形合并成一簇），不渲染页面。

    云路线（MinerU）不产生整页 PNG，拿不到像素尺寸，但图形**数量**只取决于内嵌
    位图的边界，用页面点数当坐标走同一套判据即可。返回 ``{页码: 簇数}``（页码从 1
    开始，只含至少有一幅图形的页）；PDF 缺失或 PyMuPDF 不可用时返回 ``{}``。
    """
    if not pdf_bytes:
        return {}
    try:
        import pymupdf as fitz
    except Exception:  # pragma: no cover - PyMuPDF 随应用分发
        return {}
    counts = {}
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for index in range(len(doc)):
                page = doc.load_page(index)
                rects = []
                for info in page.get_image_info(xrefs=True):
                    x0, y0, x1, y1 = info["bbox"]
                    if (x1 - x0) < _MIN_ANCHOR_PT or (y1 - y0) < _MIN_ANCHOR_PT:
                        continue
                    rects.append((x0, y0, x1, y1))
                clusters = len(_merge_touching_rects(rects))
                if clusters:
                    counts[index + 1] = clusters
    except Exception as exc:  # pragma: no cover - 解析器异常不该拖垮整卷
        print(f"[配图对账] 读取 PDF 图形数量失败: {exc}", flush=True)
        return {}
    return counts


def _summarize_document_coverage(pdf_bytes, parsed_questions):
    """整卷口径的对账：只报「一共几幅、认领几幅」，不假装知道图在哪一页。

    MinerU 落盘的插图名是 `mineru_*.jpg`，不带页码，因此无法像 `pdf_crop_*` 那样
    按文件名回到具体页。整卷计数仍然能抓住真正的风险——模型整幅漏掉了图形。
    """
    figures = sum(count_pdf_figures(pdf_bytes).values())
    claimed = {os.path.basename(str(path)) for q in (parsed_questions or [])
               for path in (q.get("image_paths") or []) if str(path).strip()}
    attributed = len(claimed)
    missing = max(0, figures - attributed)
    warnings = []
    if figures and missing:
        warnings.append(
            f"全卷 PDF 共有 {figures} 幅图形，只有 {attributed} 幅归到了题目上，"
            "其余未被识别，请对照原页【手动截图】补图。"
        )
    return {"pages": [], "figures": figures, "attributed": attributed,
            "missing": missing, "warnings": warnings}


def summarize_figure_coverage(pdf_bytes, page_images, parsed_questions):
    """逐页对账「PDF 里真实有几幅图形」与「拆出来的题认领了几幅」。

    视觉模型偶尔整幅漏掉一个图形（实测同一份卷子重跑，第 3 页的频率分布直方图
    这一轮就完全没有输出坐标框）。漏掉时既不报错也不留痕，用户只能在导入后
    发现「图呢？」。这里把缺口算出来交给任务诊断，拆卷日志与前端都能提示。

    ``page_images`` 是 ``[(真实页码, 整页 PNG 路径), ...]``；云路线没有整页位图，
    传空即退化到整卷口径（见 ``_summarize_document_coverage``）。
    """
    if not page_images:
        return _summarize_document_coverage(pdf_bytes, parsed_questions)

    attributed = {}
    for q in parsed_questions or []:
        for path in (q.get("image_paths") or []):
            match = re.search(r"pdf_crop_[A-Za-z0-9_-]+?_(\d+)_(\d+)\.png$", str(path))
            if match:
                page = int(match.group(1))
                attributed[page] = attributed.get(page, 0) + 1

    pages = []
    try:
        from PIL import Image
    except Exception:  # pragma: no cover - Pillow 随应用分发
        return {"pages": pages, "missing": 0, "warnings": []}
    for page_index, image_path in page_images or []:
        if not image_path or not os.path.exists(image_path):
            continue
        try:
            with Image.open(image_path) as img:
                width, height = img.size
        except Exception:  # pragma: no cover - 坏页图不该拖垮整卷
            continue
        figures = len(page_figure_anchor_px(pdf_bytes, page_index, width, height))
        got = attributed.get(page_index, 0)
        missing = max(0, figures - got)
        pages.append({
            "page": page_index + 1,
            "figures": figures,
            "attributed": got,
            "missing": missing,
        })
    warnings = [
        f"第 {row['page']} 页有 {row['figures']} 幅图形，只有 {row['attributed']} 幅"
        "归到题目上，其余未被识别，请对照原页【手动截图】补图。"
        for row in pages
        if row["missing"]
    ]
    return {
        "pages": pages,
        "missing": sum(row["missing"] for row in pages),
        "warnings": warnings,
    }


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
    pdf_bytes=None,
) -> str:
    """按 ``[ILLUSTRATION_BOX: ymin, xmin, ymax, xmax]`` 百分比坐标从整页 PNG 裁图，
    并在原位置替换为 ``![插图](tmp/pdf_crop_{task}_{page}_{idx}.png)`` 占位符。

    传入 ``pdf_bytes`` 时会先用该页内嵌位图的真实边界纠正模型框（见
    :func:`page_figure_anchor_px`）；无法解析或退化的坐标会被跳过
    （随后由 :func:`process_ocr_illustrations` 擦除）。
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
            anchors = page_figure_anchor_px(pdf_bytes, page_num, width, height)
            cropped_by_anchor = {}
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
                # 先用该页内嵌位图的真实边界纠正模型框。命中锚点时**不再收紧**：
                # 锚点本身就是图形外接框，再收紧一次反而可能削掉贴边的坐标轴标签。
                snapped = _snap_box_to_anchor((left, upper, right, lower), anchors)
                if snapped:
                    if snapped in cropped_by_anchor:
                        # 两个标记指向同一幅图（模型重复输出）：复用同一张裁图，
                        # 不产生第二份文件，也就不会被两题分别认领。
                        replacements[match.group(0)] = cropped_by_anchor[snapped]
                        continue
                    left, upper, right, lower = snapped
                else:
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
                placeholder = f"![插图](tmp/{fname})"
                replacements[match.group(0)] = placeholder
                if snapped:
                    cropped_by_anchor[snapped] = placeholder
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
