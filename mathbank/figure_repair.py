"""MinerU 插图归属修复：拼图切片错位、解析重复图与缺失标号。

背景（2024 大庆实验高一期中第 4 题实测）：MinerU 把一张 2×2 选项拼图切成 4 张
独立图后阅读顺序并不可靠——「B.」标号丢失、第 4 片被塞进【解析】开头。快路径的
设计是「本地正文逐字保留」，这类源头脏会原样进卡：choices 环境被无标号浮图炸成
两段、D 选项成空壳、解析多出一张幽灵配图。本模块在入库归一化（step 8）之前对
markdown 形态做确定性修复，**只搬 100% 判得对归属的形态**，拿不准一律不动。

归属判定分两条路线，**坐标优先、文本规则后备**：

**GEO 坐标仲裁（优先）**：``content_list.json`` 的每个块都带 ``bbox`` 与 ``page_idx``，
MinerU 的阅读顺序可以乱（实测每卷都有 2~13 处页内 y 倒序，第 4 题那片 D 图正是被排到
【答案】之后），但坐标不会骗人。把卡片按「``full.md`` 与清单同序」对回块区间，再用
题干带 / 解析带 / 选项标号锚点这三个几何证据判归属；证据不足一律返回「判不准」。
入参由 ``mineru_client.collect_content_blocks`` + ``build_figure_layout`` 提供，
判准时**整题接管**（几何证据同时否决下面的文本规则，避免两套规则互相搬同一张图）。

**四条文本规则（无坐标 / 判不准时的后备，执行顺序 R2 → R1 → R3 → R4）**：
- R2 解析重复图剔除：同一张图在题干与解析都出现 → 删解析侧「整行只有图」的行；
  嵌在文字行里的行内图不动。
- R1 空选项回填：解析开头是独立图簇（前面只允许空白与【答案】/【解析】类标题行）、
  图未被题干引用 → 按序搬回空选项；空位不够时在选项区末尾**强制补出标号行**。
- R3 缺失标号补全：标号 X 与标号 X+2 之间恰好一张无标号浮动图 → 为它补标号 X+1，
  让选项图回到同一个 choices 序列。
- R4 原卷配图回补：题干明确提到图（如图/图中/阴影部分…），但正文里没有任何引用，
  而本题 image_paths 里恰好只有一张这样的未引用 MinerU 图 → 把引用补回题干（选项区
  之前）。不补的话这张图只活在 image_paths 里，预览会把它当题干配图吊在选项下方，
  成一张没有归属的「幽灵图」（实测 2024 大庆卷第 2 题的韦恩图）。

R1 为什么要「强行凑齐 4 个 \\item」：光把图搬回已有的空选项不够——MinerU 丢标号时
选项区可能压根没有第 4 行，图搬回去依然凑不成四选项，导出侧的 choices 环境就剩三个
\\item，多出来的那张图只能挂在选项外面。因此图簇有剩余、且题干标号正好是 A 开头的
连续前缀（A / AB / ABC）时，按尾部缺几个字母补几个标号行；中间空缺（只有 A、C）
位置判不准，一律不猜。

纯函数模块：零网络、零数据库、不 import main。只认 MinerU 落盘图
（``mineru_<task>_<seq>.jpg``，``stage_images`` 的产物），因此天然只作用于
MinerU 分支；题干已含 ``\\begin{choices}``（老路 AI 产物）或题型明确非选择题的
题整题跳过。修复动作记录在卡片 ``_figure_repair``，供任务诊断与日志。
"""

import json
import re

from mathbank.image_layout import image_key, normalize_image_layouts

# 整行只有一张 Markdown 插图（允许首尾空白）
_IMG_ONLY_RE = re.compile(r"^\s*!\[[^\]]*\]\(\s*([^)\s]+)[^)]*\)\s*$")
# 任意行内图片（收集题干已引用图集合）
_IMG_ANY_RE = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)[^)]*\)")
# 选项标号行：A. / A．/ A、/ A）… 标号后可带行内内容
_LABEL_RE = re.compile(r"^\s*(?:[-*]\s*)?([A-DＡ-Ｄ])\s*[.．、,，:：)）]\s*(.*)$")
# 解析板块标题行：R1 扫「解析开头图簇」时允许图簇前出现这些行
_ANSWER_HEADER_RE = re.compile(r"^\s*【(答案|解析|解答|评析)】.*$")
# MinerU 落盘插图文件名——本模块的分支标记（Word/原生 OCR 路线的图不碰）
_MINERU_ASSET_RE = re.compile(r"mineru_\d+_\d+\.(?:jpg|jpeg|png|gif|webp)\b", re.I)
# 行内选项标号（「A. x B. y C. z D. w」挤在同一段）：R4 用它定位选项区起点。
_INLINE_LABEL_RE = re.compile(r"(?:^|(?<=\s))([A-D\uFF21-\uFF24])\s*[.．、,，:：)）]")
# 题干提到图：R4 只在卷面明确「有图」时才把未引用的配图补回正文。
_FIGURE_REF_RE = re.compile(
    r"如图|图中|图象|图像|图形|阴影部分|则图|图意|所示的图|示意图|读图|看图"
)
# 高中数学单选题的恒定选项数：R1 强制补标号只按这个口径补**尾部**连续字母。
_STANDARD_LABELS = "ABCD"

# ---------------------------------------------------------------------------
# 坐标仲裁（GEO）用的清单常量
# ---------------------------------------------------------------------------
# 可参与几何判定的图形块类型。``table`` 的 img_path 是**表格截图**，按选项搬动会把
# 整张表格毁成一张图，永不参与（清单与 md 的同序口径也只有 image+chart 才 35/35 成立）。
_FIGURE_BLOCK_TYPES = frozenset({"image", "chart"})
# 版式噪声：带 bbox 也带文本，但既不是题干也不是解析。不剔除的话页眉会在页顶撑出一条
# 幽灵「题干带」，把图误判进题干（实测 header 例：[104,74,189,93]「常见考法」）。
_NOISE_BLOCK_TYPES = frozenset({"header", "footer", "page_number"})
# 解析区起点：MinerU 的参考答案小标题是字面量，比文本规则可靠得多。
_ANSWER_ZONE_RE = re.compile(
    r"^\s*【\s*(?:答案|解析|分析|详解|解答|评析|考点|点评|易错|总结)"
    r"|^\s*(?:故选|故选择)"
)
# 整块只有一个裸标号（实测大庆卷第 4 题：「B.」被切成孤立小块，还掉进了第 2 题解析）。
_LABEL_BLOCK_RE = re.compile(r"^\s*(?:[-*]\s*)?([A-DＡ-Ｄ])\s*[.．、,，:：)）]\s*$")
# 块文本开头的卷面题号：卡片里已被剥进 paper_question_no，清单里还留着，比对前先抹平。
_LEAD_NO_RE = re.compile(r"^\s*[（(]?\d{1,3}[）)]?\s*[.．、,，:：]")
# 行内标号（判断本题有没有「文字选项」用）。
_INLINE_OPTION_RE = re.compile(r"(?:^|(?<=\s))([A-DＡ-Ｄ])\s*[.．、,，:：)）]")
# 网格标号的上限：只有 A~D，五选项之类的形态一律不猜。
_MAX_GRID_LABELS = len(_STANDARD_LABELS)
# 行高基准缺失时的兜底单位。**坐标尺度每卷不同**（实测 x 上界 847~990、行高中位数
# 21~45），所有容差必须按本页行高折算，绝对像素会跨卷失效。
_FALLBACK_UNIT = 20.0

_CHOICE_TYPES = {"single_choice", "multi_choice"}


def _center(bbox):
    """矩形中心点 (cx, cy)。"""
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def _band(rects):
    """若干矩形并集成的纵向区带：顶边 / 底边 / 横向跨度。"""
    return {
        "top": min(r[1] for r in rects),
        "bottom": max(r[3] for r in rects),
        "x0": min(r[0] for r in rects),
        "x1": max(r[2] for r in rects),
    }


def _norm_bbox(value):
    """bbox → 四元浮点组；缺失、非数值、零面积一律 None（坐标不可用即降级）。"""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def _norm_page(item):
    """页码：不同版本清单的键名不一致，逐一尝试；拿不到返回 None。"""
    for key in ("page_idx", "page_no", "page"):
        value = item.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value >= 0:
            return int(value)
    return None


def _match_key(text):
    """卡片与清单块共用的比对键：去图片引用、去行首题号、去所有空白。

    中文比对不需要空白（``$x = 1$`` 与 ``$x=1$`` 必须等价），去掉空白后两侧口径一致。
    """
    stripped = _IMG_ANY_RE.sub("", text or "")
    stripped = _LEAD_NO_RE.sub("", stripped, count=1)
    return re.sub(r"\s", "", stripped)


def _label_block_letter(text):
    """这个块是不是「裸选项标号」（整块只有 ``B.`` / ``A．``）？不是就返回空串。"""
    t = (text or "").strip()
    if not t or _ANSWER_ZONE_RE.match(t):
        return ""
    m = _LABEL_BLOCK_RE.match(t)
    return _norm_label(m.group(1)) if m else ""


def build_figure_layout(content_blocks, staged_mapping):
    """把结构化清单 + ``stage_images`` 映射编译成坐标仲裁索引；无可用坐标返回 ``None``。

    ``content_blocks`` 是 ``mineru_client.collect_content_blocks`` 的原样输出，
    ``staged_mapping`` 是 ``{images/<sha>.jpg: /static/.../mineru_<task>_<n>.jpg}``。
    两边唯一的连接键是**图片文件名**：被装饰图过滤器丢掉的图拿不到映射，自动不参与仲裁；
    ``stage_images`` 的编号顺序与清单顺序无关，所以绝不能按「第几张」对齐。
    """
    if not isinstance(content_blocks, list) or not content_blocks:
        return None
    mapping = {}
    for key, url in (staged_mapping or {}).items():
        asset = _asset_name(url)
        if asset:
            mapping[_asset_name(key)] = asset
    blocks = []
    figures = {}
    for i, item in enumerate(content_blocks):
        if not isinstance(item, dict):
            continue
        btype = str(item.get("type") or "")
        bbox = _norm_bbox(item.get("bbox"))
        page = _norm_page(item)
        raw = str(item.get("img_path") or "")
        asset = mapping.get(_asset_name(raw), "") if raw else ""
        # seq 缺失时按下标补：它是「卡片对回块区间」的顺序锚，不能是 None。
        blocks.append({
            "seq": item["seq"] if isinstance(item.get("seq"), int) else i,
            "type": btype, "text": str(item.get("text") or ""),
            "page": page, "bbox": bbox, "asset": asset,
        })
        if btype in _FIGURE_BLOCK_TYPES and bbox and asset and page is not None:
            prev = figures.get(asset)
            area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            if prev is None or area > prev["area"]:
                figures[asset] = {"bbox": bbox, "page": page,
                                  "seq": blocks[-1]["seq"], "area": area}
    if not figures:
        return None
    return {"blocks": blocks, "figures": figures, "pages": _page_geometry(blocks)}


def _page_geometry(blocks):
    """逐页坐标基准：行高中位数（容差按它折算）+ 正文列宽 + 双栏迹象。"""
    buckets = {}
    for b in blocks:
        if b["bbox"] is None or b["page"] is None or b["type"] in _NOISE_BLOCK_TYPES:
            continue
        buckets.setdefault(b["page"], []).append(b)
    geo = {}
    for page, items in buckets.items():
        texts = [i for i in items
                 if i["type"] not in _FIGURE_BLOCK_TYPES and i["text"].strip()]
        pool = texts or items
        heights = sorted(b["bbox"][3] - b["bbox"][1] for b in pool)
        unit = heights[len(heights) // 2] or _FALLBACK_UNIT
        x0 = min(b["bbox"][0] for b in pool)
        x1 = max(b["bbox"][2] for b in pool)
        mid = (x0 + x1) / 2.0
        left = sum(1 for b in texts if b["bbox"][2] <= mid)
        right = sum(1 for b in texts if b["bbox"][0] >= mid)
        geo[page] = {
            "unit": unit, "x0": x0, "x1": x1,
            # 同页左右各堆了 4 个以上窄文本块 = 双栏迹象，纵向区带在此失效
            "two_col": left >= 4 and right >= 4,
        }
    return geo


def _norm_label(ch):
    """全角标号归一成大写字母；非 A~D 返回空串。"""
    if ch in "ＡＢＣＤ":
        return "ABCD"["ＡＢＣＤ".index(ch)]
    return ch if ch in "ABCD" else ""


def _line_kind(line):
    """把一行分类成 (kind, payload)：image / label / blank / text。"""
    if not line.strip():
        return "blank", None
    m = _IMG_ONLY_RE.match(line)
    if m:
        return "image", m.group(1)
    m = _LABEL_RE.match(line)
    if m:
        letter = _norm_label(m.group(1))
        if letter:
            return "label", (letter, m.group(2).strip())
    return "text", None


def _asset_name(url):
    return str(url or "").split("?")[0].split("#")[0].rsplit("/", 1)[-1]


def _is_mineru_ref(url):
    return bool(_MINERU_ASSET_RE.search(_asset_name(url)))


def _content_image_keys(content):
    return {_asset_name(m.group(1)) for m in _IMG_ANY_RE.finditer(content or "")}


def _choice_labels(lines_kinds):
    """按出现顺序取标号字母；非严格递增（如「A、B 两点」式正文）返回 None。"""
    labels = [p[0] for k, p in lines_kinds if k == "label" and p]
    if len(labels) < 2 or any(b <= a for a, b in zip(labels, labels[1:])):
        return None
    return labels


def _repair_r2(q, records):
    """R2：题干已引用的图若以「整行只有图」的形态出现在解析里 → 删解析侧。"""
    content_keys = _content_image_keys(q.get("content") or "")
    answer = q.get("answer_markdown") or ""
    if not content_keys or not answer.strip():
        return
    kept, dropped = [], []
    for line in answer.split("\n"):
        kind, url = _line_kind(line)
        if kind == "image" and _asset_name(url) in content_keys:
            dropped.append(_asset_name(url))
            continue
        kept.append(line)
    if dropped:
        q["answer_markdown"] = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
        records.extend({"rule": "R2", "image": name} for name in dropped)


def _find_empty_options(lines):
    """空选项 = 无行内内容、且下一个非空行仍是标号行（或到文末）的标号行。"""
    kinds = [_line_kind(ln) for ln in lines]
    empties = []
    for i, (kind, payload) in enumerate(kinds):
        if kind != "label" or not payload or payload[1]:
            continue
        nxt = next((k for k, _ in kinds[i + 1:] if k != "blank"), None)
        if nxt is None or nxt == "label":
            empties.append((i, payload[0]))
    return empties


def _find_leading_image_cluster(answer_lines):
    """解析开头图簇：跳过空白与标题行后，连续的「整行只有图」行（可含行间空行）。"""
    kinds = [_line_kind(ln) for ln in answer_lines]
    i = 0
    while i < len(kinds) and (
        kinds[i][0] == "blank"
        or (kinds[i][0] == "text" and _ANSWER_HEADER_RE.match(answer_lines[i]))
    ):
        i += 1
    idxs = []
    while i < len(kinds):
        if kinds[i][0] == "image":
            idxs.append(i)
            i += 1
            continue
        if kinds[i][0] == "blank":
            j = i
            while j < len(kinds) and kinds[j][0] == "blank":
                j += 1
            if j < len(kinds) and kinds[j][0] == "image":
                i = j
                continue
        break
    return [kinds[k][1] for k in idxs], idxs


def _option_tail_is_clean(lines):
    """选项区是否就是题干的尾巴：最后一个标号行之后不允许再出现正文行。

    R1 强制补标号是在**题干末尾**追加标号行，只有「选项区确实收尾于文末」才敢这么干；
    后面还跟着作答括号、尾注之类正文时补的位置判不准，一律不动。
    """
    kinds = [_line_kind(ln) for ln in lines]
    last_label = -1
    for i, (kind, payload) in enumerate(kinds):
        if kind == "label" and payload:
            last_label = i
    if last_label < 0:
        return False
    return all(kind in ("blank", "image") for kind, _ in kinds[last_label + 1:])


def _repair_r1(q, records):
    """R1：解析开头独立图簇搬回选项区；空选项不够时按尾部缺的字母强制补标号。

    搬图的前提（与历史一致）：图簇里每张图都是 ``mineru_*`` 落盘图、且都没被题干引用过。
    数量口径：能填空位的先填，剩下的图要求「题干标号是 A 起的连续前缀 + 缺的正好是尾部
    连续字母 + 选项区在文末」才补标号；对不上就整题不动（宁可不修，绝不改坏干净题）。
    """
    content = q.get("content") or ""
    answer = q.get("answer_markdown") or ""
    if not content.strip() or not answer.strip():
        return
    lines = content.split("\n")
    labels = _choice_labels([_line_kind(ln) for ln in lines])
    if labels is None:
        return
    empties = _find_empty_options(lines)
    urls, cluster_idxs = _find_leading_image_cluster(answer.split("\n"))
    if not urls:
        return
    content_keys = _content_image_keys(content)
    if any((not _is_mineru_ref(u)) or _asset_name(u) in content_keys for u in urls):
        return
    leftover = urls[len(empties):]
    prefix = list(_STANDARD_LABELS[: len(labels)])
    missing_tail = _STANDARD_LABELS[len(labels):] if labels == prefix else ""
    if leftover and (not missing_tail or len(leftover) != len(missing_tail)
                     or not _option_tail_is_clean(lines)):
        return
    answer_lines = answer.split("\n")
    for (line_idx, letter), url in sorted(
        zip(empties, urls), key=lambda t: t[0][0], reverse=True
    ):
        lines.insert(line_idx + 1, "![](%s)" % url)
        records.append({"rule": "R1", "image": _asset_name(url), "target_label": letter})
    for letter, url in zip(missing_tail, leftover):
        while lines and not lines[-1].strip():
            lines.pop()
        lines.append("%s．" % letter)
        lines.append("![](%s)" % url)
        records.append({
            "rule": "R1", "image": _asset_name(url),
            "target_label": letter, "forced_label": True,
        })
    for idx in sorted(cluster_idxs, reverse=True):
        del answer_lines[idx]
    q["content"] = "\n".join(lines)
    q["answer_markdown"] = re.sub(r"\n{3,}", "\n\n", "\n".join(answer_lines)).strip()


def _repair_r3(q, records):
    """R3：标号 X 与 X+2 之间恰好一张浮动图（前一行也是图、无自己的标号）→ 补 X+1。"""
    content = q.get("content") or ""
    lines = content.split("\n")
    kinds = [_line_kind(ln) for ln in lines]
    if _choice_labels(kinds) is None:
        return
    inserts = []
    last_label = None
    i, n = 0, len(kinds)
    while i < n:
        kind, payload = kinds[i]
        if kind == "label" and payload:
            last_label = payload[0]
            i += 1
            continue
        if kind == "image" and last_label:
            prev = next(
                (kinds[j] for j in range(i - 1, -1, -1) if kinds[j][0] != "blank"), None
            )
            if prev and prev[0] == "image":
                # 数清这段浮动图链（图行 + 其间空行），直到下一个非空行为止
                j = i
                floats = 0
                while j < n:
                    if kinds[j][0] == "image":
                        floats += 1
                        j += 1
                        continue
                    if kinds[j][0] == "blank":
                        k = j
                        while k < n and kinds[k][0] == "blank":
                            k += 1
                        if k < n and kinds[k][0] == "image":
                            j = k
                            continue
                        # 空行后的首个非空行不是图：把 j 对齐到它，
                        # 下面的 nxt（标号判定）才拿得到真实边界行。
                        j = k
                    break
                nxt = kinds[j] if j < n else (None, None)
                if (
                    floats == 1
                    and nxt[0] == "label"
                    and nxt[1]
                    and ord(nxt[1][0]) - ord(last_label) == 2
                    and _is_mineru_ref(payload)
                ):
                    missing = chr(ord(last_label) + 1)
                    inserts.append((i, missing))
                    records.append({"rule": "R3", "image": _asset_name(payload), "label": missing})
        i += 1
    for idx, letter in sorted(inserts, reverse=True):
        lines.insert(idx, "%s．" % letter)
    if inserts:
        q["content"] = "\n".join(lines)


def _is_option_line(line):
    """这一行是不是「选项区的起点」：标号行、整行图，或行内挤了两个以上 ABCD 标号。"""
    kind, _payload = _line_kind(line)
    if kind in ("label", "image"):
        return True
    letters = {_norm_label(ch) for ch in _INLINE_LABEL_RE.findall(line)}
    return len(letters) >= 2


def _image_paths_list(q):
    """卡片的图片清单：兼容 list 与 JSON 字符串两种形态。"""
    paths = q.get("image_paths")
    if isinstance(paths, str):
        try:
            paths = json.loads(paths or "[]")
        except ValueError:
            return []
    return paths if isinstance(paths, list) else []


def _repair_r4(q, records):
    """R4：题干提到图、本题恰好只有一张未被任何正文引用的 MinerU 图 -> 把引用补回题干。

    MinerU 路线的原卷配图（韦恩图、几何图）常被 step 6「题干图剥离」从正文里删掉，
    只留在 image_paths。卡片预览于是把它当题干配图吊在选项区下方——正文里没有这行
    引用，图上也没有标号，看起来就是一张来路不明的幽灵图。与其在渲染层硬禁，更诚实的
    做法是把引用写回正文：卷面本来就要求「看图作答」，图属于题干。

    保守边界：题干必须明确提到图；候选必须**恰好一张**（多张判不准归属就不动）；
    必须是 ``mineru_*`` 落盘图；解析里用过的图不算（那属于解答）；题干开头就是选项时
    不前置（没地方放）。
    """
    content = q.get("content") or ""
    if not _FIGURE_REF_RE.search(content):
        return
    answer_text = q.get("answer_markdown") or ""
    used = _content_image_keys(content)
    # 「已被解析用过」按**文件名**判断，连裸文件名提及都算：那张图既然属于解答，
    # 再补进题干就会渲染两次（与 step 6.5 的候选口径一致）。
    candidates = [
        url for url in _image_paths_list(q)
        if isinstance(url, str) and _is_mineru_ref(url)
        and _asset_name(url) not in used
        and _asset_name(url) not in answer_text
    ]
    if len(candidates) != 1:
        return
    url = candidates[0]
    lines = content.split("\n")
    # 位置：贴着「提到图的那句话」放（卷面本来就是句子 + 图 + 选项）。找不到就退回
    # 「选项区之前」，两处都没有时（整段都是选项）放弃——图没地方放比放错地方好。
    mention = -1
    first_option = len(lines)
    for i, line in enumerate(lines):
        if _is_option_line(line):
            first_option = min(first_option, i)
            continue
        if _FIGURE_REF_RE.search(line):
            mention = i
    insert_at = mention + 1 if mention >= 0 else first_option
    if insert_at == 0:
        return
    lines.insert(insert_at, "![](%s)" % url)
    lines.insert(insert_at + 1, "")
    q["content"] = "\n".join(lines)
    records.append({"rule": "R4", "image": _asset_name(url)})


def _seed_choice_layouts(q, skip_keys=()):
    """修复发生过的题：为选项区图片播种 {align:center,size:small}，已有键不覆盖。

    ``skip_keys`` 里的图不播种：R4 补回题干的**原卷配图**（韦恩图、几何图）是题干插图，
    不是选项图，按选项图的 small 压一遍会把图缩到看不清，留 auto 让它走通栏默认口径。
    """
    content = q.get("content") or ""
    skip = set(skip_keys or ())
    urls = []
    for line in content.split("\n"):
        kind, payload = _line_kind(line)
        if kind == "image":
            if _asset_name(payload) in skip:
                continue
            urls.append(payload)
        elif kind == "label" and payload and payload[1]:
            urls.extend(m.group(1) for m in _IMG_ANY_RE.finditer(line))
    if not urls:
        return
    existing = q.get("image_layouts") or {}
    if isinstance(existing, str):
        try:
            existing = json.loads(existing or "{}")
        except ValueError:
            existing = {}
    if not isinstance(existing, dict):
        existing = {}
    merged = dict(existing)
    for url in urls:
        key = image_key(url)
        if key and key not in merged:
            merged[key] = {"align": "center", "size": "small"}
    try:
        q["image_layouts"] = normalize_image_layouts(merged, content)
    except ValueError:
        pass


def _card_figure_urls(q):
    """本题涉及的落盘图 → ``{文件名: 卡片里用的 URL}``（正文 + 解析 + image_paths 清单）。

    URL 必须取自卡片本身：搬图要写回 ``![](url)``，清单里的 ``images/<sha>.jpg`` 是渲染不出来的。
    """
    urls = {}
    for text in (q.get("content") or "", q.get("answer_markdown") or ""):
        for m in _IMG_ANY_RE.finditer(text):
            url = m.group(1)
            if _is_mineru_ref(url):
                urls.setdefault(_asset_name(url), url)
    for path in _image_paths_list(q):
        if isinstance(path, str) and _is_mineru_ref(path):
            urls.setdefault(_asset_name(path), path)
    return urls


def _figure_locations(q):
    """卡片现状 ``({图名: (所在区, 标号, 可搬)}, 同一张图出现两处以上的图名)``。

    所在区 ∈ ``stem`` / ``option`` / ``answer`` / ``none``（只在 image_paths 里、正文没引用，
    即被 step 6 剥掉的题干配图）。``可搬`` 为假的是**行内图**——它嵌在句子中间，
    搬走会把那句话撕坏（与 R1/R2 只动整行图是同一条保守口径）。
    """
    place = {}
    dupes = set()

    def _record(url, zone, label, movable):
        name = _asset_name(url)
        if not name or not _is_mineru_ref(name):
            return
        if name in place:
            dupes.add(name)
            return
        place[name] = (zone, label, movable)

    current = None
    for line in (q.get("content") or "").split("\n"):
        kind, payload = _line_kind(line)
        if kind == "image":
            _record(payload, "option" if current else "stem", current, True)
            continue
        if kind == "label" and payload:
            current = payload[0]
        for m in _IMG_ANY_RE.finditer(line):
            _record(m.group(1), "option" if current else "stem", current, False)
    for line in (q.get("answer_markdown") or "").split("\n"):
        kind, payload = _line_kind(line)
        if kind == "image":
            _record(payload, "answer", None, True)
            continue
        for m in _IMG_ANY_RE.finditer(line):
            _record(m.group(1), "answer", None, False)
    for name in _card_figure_urls(q):
        place.setdefault(name, ("none", None, True))
    return place, dupes


def _align_window(q, layout, cursor):
    """把卡片对回清单的块区间；对不上返回 None（整题降级走文本规则）。

    返回 ``(i0, i1, tail_from)``：``[i0, i1]`` 是块区间，``tail_from`` 之后是**窗口尾孤图**
    （最后一个匹配正文块之后、下一题正文之前的图块）。答案末尾挂一张图很常见，所以尾部图
    也要纳入；但「在解析标记之下」这种消极证据不足以把一张尾部孤图搬进解析（那正是
    「末题吞掉整卷图」的成因），所以判定端对尾部图只认题干带包含与选项网格锚点这类正向证据。

    依据是「``full.md`` 与 ``content_list.json`` 同序」这条实测不变式（35/35），所以这里是
    **按序贪心**而不是模糊相似度：从 ``cursor`` 起找到第一个属于本题的正文块，往后一路吃到
    第一个不属于本题的正文块为止。图形块与页码块不终止窗口（它们本来就夹在正文之间），
    题头图往前并入。
    """
    hay = (_match_key(q.get("content") or "") + "\x00"
           + _match_key(q.get("answer_markdown") or ""))
    blocks = layout["blocks"]
    i0 = None
    for idx in range(max(0, cursor), len(blocks)):
        block = blocks[idx]
        if block["type"] in _NOISE_BLOCK_TYPES or not block["text"]:
            continue
        key = _match_key(block["text"])
        if len(key) >= 6 and key in hay:
            i0 = idx
            break
    if i0 is None:
        return None
    while i0 > 0 and blocks[i0 - 1]["type"] in _FIGURE_BLOCK_TYPES:
        i0 -= 1
    i1 = i0
    last_text = i0
    idx = i0
    while idx < len(blocks):
        block = blocks[idx]
        if (block["type"] in _FIGURE_BLOCK_TYPES
                or block["type"] in _NOISE_BLOCK_TYPES or not block["text"]):
            idx += 1
            continue
        key = _match_key(block["text"])
        if not key or key not in hay:
            break
        last_text = idx
        i1 = idx
        idx += 1
    return (i0, i1, last_text + 1)


def _has_text_options(q):
    """本题有没有「文字选项」：标号行自带正文，或一行里挤了 ≥3 个标号。

    这是选项区判定的第一道否决票：选项是文字时，夹在题干与解析之间的图只能是题干配图
    （实测大庆卷第 1、2 题），把它们当选项图补标号会凭空造出四个选项。
    """
    for line in (q.get("content") or "").split("\n"):
        kind, payload = _line_kind(line)
        if kind == "label" and payload and payload[1]:
            return True
        if kind == "text":
            letters = {_norm_label(ch) for ch in _INLINE_OPTION_RE.findall(line)}
            letters.discard("")
            if len(letters) >= 3:
                return True
    return False


def _page_label_anchors(layout, page):
    """本页所有裸标号块。**不受卡片窗口限制**——泄漏到别题窗口里的「B.」正是靠它纠偏。"""
    out = []
    for block in layout["blocks"]:
        if block["page"] != page or block["bbox"] is None:
            continue
        if block["type"] in _NOISE_BLOCK_TYPES:
            continue
        letter = _label_block_letter(block["text"])
        if letter:
            out.append({"letter": letter, "bbox": block["bbox"],
                        "seq": block["seq"], "page": page})
    return out


def _anchor_cell(bbox, rows, unit):
    """裸标号块贴在哪一格：同一行紧贴左侧，或同一列紧贴上方；都对不上返回 None。"""
    ax0, ay0, ax1, ay1 = bbox
    acy = (ay0 + ay1) / 2.0
    best = None
    best_gap = None
    for row in rows:
        for cell in row["items"]:
            cx0, cy0, cx1, cy1 = cell["bbox"]
            ccx, ccy = _center(cell["bbox"])
            gap = None
            same_row = abs(acy - ccy) <= 0.55 * max(cy1 - cy0, ay1 - ay0, unit)
            if same_row and ax0 < cx0 and -unit <= cx0 - ax1 <= 4 * unit:
                gap = abs(cx0 - ax1)
            same_col = cx0 < ax1 and ax0 < cx1
            if gap is None and same_col and ay1 <= cy1 and 0 <= cy0 - ay1 <= 2 * unit:
                gap = abs(cy0 - ay1)
            if gap is not None and (best_gap is None or gap < best_gap):
                best, best_gap = cell, gap
    return best


def _index_of(items, target):
    for i, item in enumerate(items):
        if item is target:
            return i
    return None


def _grid_letters(figs, layout, page, used_anchors):
    """给带内图形块按阅读序分配 A/B/C/D；任何一处对不上就返回 None（宁缺毋滥）。

    顺序来源：按 y 聚成行、行内按 x 排。**多行多列**的网格必须有证据证实——「A B / C D」
    与「A C / B D」两种排法在几何上完全可能，靠猜会把两张图换错位置；单行或单列则是
    无歧义的线性序。证据 = 页内裸标号块贴在哪一格（实测第 4 题的「B.」贴住右上格，
    行优先得证）。``image_caption`` 不参与：实测它的挂载比阅读顺序还乱（「C.」「D.」
    一起挂到同一张图上，「B.」整个漏掉），只配当旁证。
    """
    if not 2 <= len(figs) <= _MAX_GRID_LABELS:
        return None
    unit = layout["pages"].get(page, {}).get("unit", _FALLBACK_UNIT)
    rows = []
    for block in sorted(figs, key=lambda b: _center(b["bbox"])[1]):
        cx, cy = _center(block["bbox"])
        height = block["bbox"][3] - block["bbox"][1]
        if rows and abs(cy - rows[-1]["cy"]) <= 0.55 * max(height, rows[-1]["h"]):
            rows[-1]["items"].append(block)
            rows[-1]["cy"] = (sum(_center(i["bbox"])[1] for i in rows[-1]["items"])
                              / len(rows[-1]["items"]))
        else:
            rows.append({"cy": cy, "h": height, "items": [block]})
    for row in rows:
        row["items"].sort(key=lambda b: b["bbox"][0])
    order = [block for row in rows for block in row["items"]]
    letters = list(_STANDARD_LABELS[:len(order)])
    if len(rows) < 2 or max(len(row["items"]) for row in rows) < 2:
        return letters
    evidence = 0
    for anchor in _page_label_anchors(layout, page):
        cell = _anchor_cell(anchor["bbox"], rows, unit)
        if cell is None:
            continue
        pos = _index_of(order, cell)
        if pos is None or letters[pos] != anchor["letter"]:
            return None
        evidence += 1
        used_anchors.append(anchor)
    return letters if evidence else None


def _by_page(items):
    grouped = {}
    for item in items:
        grouped.setdefault(item["page"], []).append(item)
    return grouped


def _geo_decide(q, layout, window, used_anchors):
    """按 bbox 判定本题每张落盘图该挂哪儿；判不准的一律不进 verdicts（留给 R1-R4）。

    三区带：``解析带`` = 窗口内第一个【答案】/【解析】类块之后的所有正文块（MinerU 的
    参考答案小标题是字面量，比「解析开头图簇」这类形态判断可靠）；``题干带`` = 它之前的
    正文块；夹在中间的就是**待定带**。图形块自己不撑区带（否则区带会被图本身撑大）。
    """
    blocks = layout["blocks"]
    lo, hi, tail_from = window
    own = blocks[lo:hi + 1]
    urls = _card_figure_urls(q)
    if not urls or not any(b["type"] in _FIGURE_BLOCK_TYPES and b["asset"] in urls
                           for b in own):
        return {}
    ans_seq = next(
        (b["seq"] for b in own
         if b["type"] not in _NOISE_BLOCK_TYPES and _ANSWER_ZONE_RE.match(b["text"])),
        None,
    )
    stem = {}
    answer = {}
    for block in own:
        if (block["bbox"] is None or block["page"] is None
                or block["type"] in _NOISE_BLOCK_TYPES
                or block["type"] in _FIGURE_BLOCK_TYPES
                or not block["text"].strip()):
            continue
        bucket = answer if (ans_seq is not None and block["seq"] >= ans_seq) else stem
        bucket.setdefault(block["page"], []).append(block["bbox"])
    verdicts = {}
    pending = []
    for block in own:
        if block["type"] not in _FIGURE_BLOCK_TYPES or block["asset"] not in urls:
            continue
        geo = layout["pages"].get(block["page"]) if block["page"] is not None else None
        if block["bbox"] is None or geo is None or geo["two_col"]:
            continue                     # 没坐标 / 双栏页：纵向区带失效，交给 R1-R4
        cx, cy = _center(block["bbox"])
        tol = geo["unit"] * 0.5
        if not (geo["x0"] - tol <= cx <= geo["x1"] + tol):
            continue                     # 图落在本页正文列宽之外：版式判不准
        st = _band(stem[block["page"]]) if block["page"] in stem else None
        an = _band(answer[block["page"]]) if block["page"] in answer else None
        if an is not None and cy >= an["top"] - tol:
            if block["seq"] is not None and block["seq"] >= tail_from:
                continue     # 窗口尾孤图：只有「在解析标记之下」的消极证据，不搬进解析
            verdicts[block["asset"]] = {"zone": "answer", "label": None,
                                        "url": urls[block["asset"]]}
        elif st is not None and cy <= st["bottom"] + tol:
            verdicts[block["asset"]] = {"zone": "stem", "label": None,
                                        "url": urls[block["asset"]]}
        else:
            pending.append(block)
    if _has_text_options(q):
        for block in pending:            # 选项是文字 → 带内的图只能是题干配图
            verdicts[block["asset"]] = {"zone": "stem", "label": None,
                                        "url": urls[block["asset"]]}
        return verdicts
    for page, figs in _by_page(pending).items():
        st = _band(stem[page]) if page in stem else None
        an = _band(answer[page]) if page in answer else None
        if st is None or an is None or an["top"] <= st["bottom"]:
            continue                     # 区带算不出来 → 判不准
        band_all = [
            b for b in own
            if b["type"] in _FIGURE_BLOCK_TYPES and b["bbox"] is not None
            and b["page"] == page and st["bottom"] < _center(b["bbox"])[1] < an["top"]
        ]
        if len(band_all) != len(figs) or any(b["asset"] not in urls for b in band_all):
            continue                     # 带里混着没挂到本题的图：标号顺序判不准
        letters = _grid_letters(band_all, layout, page, used_anchors)
        if letters is None:
            continue                     # 一题只有一张配图时判不出标号，交给文本规则
        for block, letter in zip(band_all, letters):
            verdicts[block["asset"]] = {"zone": "option", "label": letter,
                                        "url": urls[block["asset"]]}
    return verdicts


def _is_relocatable_image_line(line, urls):
    """整行只有一张待搬图 → 删它；行内图与别的行一律原样留着。"""
    kind, payload = _line_kind(line)
    return kind == "image" and payload in urls


def _option_start_index(lines):
    """选项区起点：第一个标号行，或行内挤了 ≥2 个标号的正文行（整行图不算起点）。"""
    for i, line in enumerate(lines):
        kind, payload = _line_kind(line)
        if kind == "label" and payload:
            return i
        if kind == "text":
            letters = {_norm_label(ch) for ch in _INLINE_OPTION_RE.findall(line)}
            letters.discard("")
            if len(letters) >= 2:
                return i
    return None


def _option_block_end(lines, idx):
    """标号行下面那段（含空行与整行图）的末尾下标。"""
    n = len(lines)
    i = idx + 1
    while i < n:
        kind, _payload = _line_kind(lines[i])
        if kind == "image":
            i += 1
            continue
        if kind == "blank":
            j = i
            while j < n and _line_kind(lines[j])[0] == "blank":
                j += 1
            if j < n and _line_kind(lines[j])[0] == "image":
                i = j
                continue
        break
    return i


def _insert_label_line(lines, letter):
    """缺标号时按阅读序补一行：挂在它后面第一个已有标号之前（R1 同款口径）。"""
    kinds = [_line_kind(ln) for ln in lines]
    later = [i for i, (k, p) in enumerate(kinds) if k == "label" and p and p[0] > letter]
    if later:
        at = min(later)
        while at > 0 and not lines[at - 1].strip():
            at -= 1
    else:
        earlier = [i for i, (k, p) in enumerate(kinds)
                   if k == "label" and p and p[0] < letter]
        at = _option_block_end(lines, max(earlier)) if earlier else len(lines)
    lines.insert(at, "%s．" % letter)
    lines.insert(at + 1, "")
    return at


def _insert_under_option(lines, letter, url):
    """把图插到标号行下（已有该选项图则接在后面）；标号行不存在就先补一行。"""
    idx = None
    for i, line in enumerate(lines):
        kind, payload = _line_kind(line)
        if kind == "label" and payload and payload[0] == letter:
            idx = _option_block_end(lines, i)
            break
    if idx is None:
        idx = _option_block_end(lines, _insert_label_line(lines, letter) + 1)
    lines.insert(idx, "![](%s)" % url)
    lines.insert(idx + 1, "")


def _insert_into_stem(lines, url):
    """题干图：贴着选项区之前插（卷面本来就是「题干 + 图 + 选项」）。"""
    at = _option_start_index(lines)
    if at is None:
        for i, line in enumerate(lines):
            if _ANSWER_HEADER_RE.match(line):
                at = i
                break
    if at is None:
        at = len(lines)
        while at > 0 and not lines[at - 1].strip():
            at -= 1
    while at > 0 and not lines[at - 1].strip():
        at -= 1
    lines.insert(at, "![](%s)" % url)
    lines.insert(at + 1, "")


def _insert_into_answer(answer_lines, url):
    """解析图：插在解析开头的标题行之后（卷面就是【答案】/【解析】+ 图的顺序）。"""
    at = 0
    for i, line in enumerate(answer_lines):
        if _ANSWER_HEADER_RE.match(line):
            at = i + 1
    while at < len(answer_lines) and not answer_lines[at].strip():
        at += 1
    answer_lines.insert(at, "![](%s)" % url)
    answer_lines.insert(at + 1, "")


def _insert_sort_key(item):
    """先补题干、再补解析，最后按标号倒序补选项（倒序插入不会互相挤 displaced）。"""
    zone, letter, _url = item
    order = {"stem": 0, "answer": 1}.get(zone, 2)
    return (order, -ord(letter) if letter else 0)


def _geo_apply(q, decision, records):
    """按坐标判定搬图：现状与判定**一致**就一行都不改（幂等），矛盾才动手。"""
    place, dupes = _figure_locations(q)
    drop = set()
    inserts = []
    for asset in sorted(decision["verdicts"]):
        verdict = decision["verdicts"][asset]
        url, zone, letter = verdict["url"], verdict["zone"], verdict["label"]
        now = place.get(asset, ("none", None, True))
        if now[2] is False:
            continue                     # 行内图嵌在句子里，搬走会撕坏那句话
        settled = (
            (zone == "option" and now[0] == "option" and now[1] == letter)
            or (zone == "stem" and now[0] in ("stem", "option"))
            or (zone == "answer" and now[0] == "answer")
        )
        if settled and asset not in dupes:
            continue
        drop.add(url)
        inserts.append((zone, letter, url))
        rec = {"rule": "GEO", "image": _asset_name(url), "zone": zone,
               "label": letter, "from": now[0]}
        if now[0] == "option":
            rec["from_label"] = now[1]
        records.append(rec)
    if not drop:
        return
    lines = [ln for ln in (q.get("content") or "").split("\n")
             if not _is_relocatable_image_line(ln, drop)]
    answer_lines = [ln for ln in (q.get("answer_markdown") or "").split("\n")
                    if not _is_relocatable_image_line(ln, drop)]
    for zone, letter, url in sorted(inserts, key=_insert_sort_key):
        if zone == "option":
            _insert_under_option(lines, letter, url)
        elif zone == "stem":
            _insert_into_stem(lines, url)
        else:
            _insert_into_answer(answer_lines, url)
    q["content"] = "\n".join(lines)
    q["content"] = re.sub(r"\n{3,}", "\n\n", q["content"])
    q["answer_markdown"] = re.sub(r"\n{3,}", "\n\n", "\n".join(answer_lines)).strip()


def _drop_isolated_label(q, letter):
    """删掉解析里「整行只有这一个标号」的泄漏行；删完解析没正文就不删。"""
    answer = q.get("answer_markdown") or ""
    if not answer.strip():
        return False
    kept = []
    hit = False
    for line in answer.split("\n"):
        kind, payload = _line_kind(line)
        if (not hit and kind == "label" and payload
                and payload[0] == letter and not payload[1]):
            hit = True
            continue
        kept.append(line)
    if not hit or not _match_key("\n".join(kept)):
        return False
    q["answer_markdown"] = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return True


def _geo_purge_leaked_labels(parsed_questions, decisions):
    """清理跨题泄漏的裸标号（实测：第 4 题的「B.」被切小块后掉进第 2 题解析中间）。

    坐标模型已经把这个块当成第 4 题选项网格的标号锚点，那它对第 2 题解析就是一行垃圾。
    只删「整行只有这一个标号」的行，正文一个字不碰；找不到认领卡片、或删空了解析都不动。
    """
    if not decisions:
        return []
    holders = {}
    for dec in decisions.values():
        for seq in range(dec["window"][0], dec["window"][1] + 1):
            holders.setdefault(seq, dec)
    positions = {id(q): i for i, q in enumerate(parsed_questions, start=1)
                 if isinstance(q, dict)}
    notes = []
    for dec in decisions.values():
        lo, hi = dec["window"][0], dec["window"][1]
        for anchor in dec["anchors"]:
            if anchor["seq"] is None or lo <= anchor["seq"] <= hi:
                continue                 # 本来就在自己窗口里：不是泄漏
            holder = holders.get(anchor["seq"])
            if holder is None or holder is dec:
                continue                 # 无人认领：不猜它从哪儿来
            card = holder["card"]
            if not _drop_isolated_label(card, anchor["letter"]):
                continue
            rec = {"rule": "GEO", "zone": "stray_label", "label": anchor["letter"]}
            card["_figure_repair"] = (card.get("_figure_repair") or []) + [rec]
            notes.append(_note_text(positions.get(id(card), 0), rec))
    return notes


def _repair_gate_open(q):
    """保守闸门（任一不满足即整题跳过）：题干是 markdown 形态（不含
    ``\\begin{choices}``）、题内至少一张 ``mineru_*`` 落盘图、题型为空或选择题。
    """
    content = q.get("content")
    if not isinstance(content, str) or not content.strip():
        return False
    if "\\begin{choices}" in content:
        return False
    q_type = q.get("question_type") or ""
    if q_type and q_type not in _CHOICE_TYPES:
        return False
    answer = q.get("answer_markdown") or ""
    if (not _MINERU_ASSET_RE.search(content + "\n" + answer)
            and not any(_is_mineru_ref(p) for p in _image_paths_list(q)
                        if isinstance(p, str))):
        return False
    return True


def _note_text(idx, rec):
    """修复记录 → 一行人类可读日志（进拆卷日志与 ``diagnostics.pdf_fast_path``）。"""
    rule = rec.get("rule")
    if rule == "GEO":
        if rec.get("zone") == "stray_label":
            return ("第 %d 题 GEO：解析里泄漏的孤立标号「%s.」按坐标属别题选项，已剔除"
                    % (idx, rec["label"]))
        where = {"stem": "题干", "answer": "解析"}.get(
            rec["zone"], "选项 %s" % rec["label"])
        if rec.get("from") == "none":
            return ("第 %d 题 GEO：%s 按坐标属%s，正文缺引用已补回"
                    % (idx, rec["image"], where))
        if rec.get("from") == "option" and rec.get("from_label"):
            return ("第 %d 题 GEO：%s 按坐标属%s，已从选项 %s 归位"
                    % (idx, rec["image"], where, rec["from_label"]))
        origin = {"stem": "题干", "answer": "解析", "option": "选项"}
        return ("第 %d 题 GEO：%s 按坐标属%s，已从%s搬正"
                % (idx, rec["image"], where, origin.get(rec.get("from"), "原位置")))
    if rule == "R2":
        return "第 %d 题 R2：解析重复图 %s 已删除" % (idx, rec["image"])
    if rule == "R1":
        return "第 %d 题 R1：解析首图 %s 已回填选项 %s%s" % (
            idx, rec["image"], rec["target_label"],
            "（并补出标号行）" if rec.get("forced_label") else "")
    if rule == "R4":
        return "第 %d 题 R4：题干提到图而正文无引用，原卷配图 %s 已补回题干" % (
            idx, rec["image"])
    return "第 %d 题 R3：浮动图 %s 已补标号 %s" % (idx, rec["image"], rec["label"])


def repair_parsed_questions(parsed_questions, figure_layout=None):
    """就地修复一批卡片；返回人类可读 note 列表（进 post_process 日志与任务诊断）。

    两条路线，**坐标优先、文本规则后备**：
    - 给了 ``figure_layout``（``build_figure_layout`` 的产物）就先用 bbox 判归属；
      判得准的题由 GEO **整题接管**——几何证据同时否决 R1-R4，两套规则不能同时搬同一张图。
    - 没有清单、清单对不上本题、或几何判不准时，才降级跑 R2 → R1 → R3 → R4。

    闸门（``_repair_gate_open``）与播种布局的口径两条路线共用。跨题泄漏的裸标号在最后
    统一清理（需要别的卡片已就位，所以必须在主循环之后跑）。
    """
    gated = [q for q in parsed_questions if isinstance(q, dict) and _repair_gate_open(q)]
    decisions = {}
    if figure_layout and figure_layout.get("figures"):
        cursor = 0
        for q in gated:
            window = _align_window(q, figure_layout, cursor)
            if window is None:
                continue
            cursor = max(cursor, window[1] + 1)
            anchors = []
            verdicts = _geo_decide(q, figure_layout, window, anchors)
            if verdicts:
                decisions[id(q)] = {"verdicts": verdicts, "window": window,
                                    "anchors": anchors, "card": q}
    notes = []
    for idx, q in enumerate(parsed_questions, start=1):
        if not isinstance(q, dict) or not _repair_gate_open(q):
            continue
        records = []
        dec = decisions.get(id(q))
        if dec:
            _geo_apply(q, dec, records)
        else:
            _repair_r2(q, records)
            _repair_r1(q, records)
            _repair_r3(q, records)
            _repair_r4(q, records)
        if not records:
            continue
        skip_keys = (
            # 播种口径只认文件名（``_seed_choice_layouts`` 按 ``_asset_name`` 比对）：
            # 题干图与解析图都不是选项图，不能按 small 压一遍。
            [name for name, v in dec["verdicts"].items() if v["zone"] != "option"]
            if dec else [rec["image"] for rec in records if rec["rule"] == "R4"]
        )
        _seed_choice_layouts(q, skip_keys=skip_keys)
        q["_figure_repair"] = (q.get("_figure_repair") or []) + records
        for rec in records:
            notes.append(_note_text(idx, rec))
    notes += _geo_purge_leaked_labels(parsed_questions, decisions)
    return notes
