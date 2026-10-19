"""MinerU 插图归属修复：拼图切片错位、解析重复图与缺失标号。

背景（2024 大庆实验高一期中第 4 题实测）：MinerU 把一张 2×2 选项拼图切成 4 张
独立图后阅读顺序并不可靠——「B.」标号丢失、第 4 片被塞进【解析】开头。快路径的
设计是「本地正文逐字保留」，这类源头脏会原样进卡：choices 环境被无标号浮图炸成
两段、D 选项成空壳、解析多出一张幽灵配图。本模块在入库归一化（step 8）之前对
markdown 形态做确定性修复，**只搬 100% 判得对归属的形态**，拿不准一律不动。

三条规则（执行顺序 R2 → R1 → R3）：
- R2 解析重复图剔除：同一张图在题干与解析都出现 → 删解析侧「整行只有图」的行；
  嵌在文字行里的行内图不动。
- R1 空选项回填：解析开头是独立图簇（前面只允许空白与【答案】/【解析】类标题行）、
  题干空选项个数与图簇张数相等、且图未被题干引用 → 按序搬回空选项。
- R3 缺失标号补全：标号 X 与标号 X+2 之间恰好一张无标号浮动图 → 为它补标号 X+1，
  让选项图回到同一个 choices 序列。

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

_CHOICE_TYPES = {"single_choice", "multi_choice"}


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


def _repair_r1(q, records):
    """R1：解析开头独立图簇与题干空选项数量相等才整体搬回，按序配对。"""
    content = q.get("content") or ""
    answer = q.get("answer_markdown") or ""
    if not content.strip() or not answer.strip():
        return
    lines = content.split("\n")
    if _choice_labels([_line_kind(ln) for ln in lines]) is None:
        return
    empties = _find_empty_options(lines)
    if not empties:
        return
    urls, cluster_idxs = _find_leading_image_cluster(answer.split("\n"))
    if not urls or len(urls) != len(empties):
        return
    content_keys = _content_image_keys(content)
    if any((not _is_mineru_ref(u)) or _asset_name(u) in content_keys for u in urls):
        return
    answer_lines = answer.split("\n")
    for (line_idx, letter), url in sorted(
        zip(empties, urls), key=lambda t: t[0][0], reverse=True
    ):
        lines.insert(line_idx + 1, "![](%s)" % url)
        records.append({"rule": "R1", "image": _asset_name(url), "target_label": letter})
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


def _seed_choice_layouts(q):
    """修复发生过的题：为选项区图片播种 {align:center,size:small}，已有键不覆盖。"""
    content = q.get("content") or ""
    urls = []
    for line in content.split("\n"):
        kind, payload = _line_kind(line)
        if kind == "image":
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


def repair_parsed_questions(parsed_questions):
    """就地修复一批卡片；返回人类可读 note 列表（进 post_process 日志与任务诊断）。

    保守闸门（任一不满足即整题跳过）：题干是 markdown 形态（不含
    ``\\begin{choices}``）、题内至少一张 ``mineru_*`` 落盘图、题型为空或选择题。
    """
    notes = []
    for idx, q in enumerate(parsed_questions, start=1):
        if not isinstance(q, dict):
            continue
        content = q.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if "\\begin{choices}" in content:
            continue
        q_type = q.get("question_type") or ""
        if q_type and q_type not in _CHOICE_TYPES:
            continue
        answer = q.get("answer_markdown") or ""
        if not _MINERU_ASSET_RE.search(content + "\n" + answer):
            continue
        records = []
        _repair_r2(q, records)
        _repair_r1(q, records)
        _repair_r3(q, records)
        if not records:
            continue
        _seed_choice_layouts(q)
        q["_figure_repair"] = (q.get("_figure_repair") or []) + records
        for rec in records:
            if rec["rule"] == "R2":
                notes.append("第 %d 题 R2：解析重复图 %s 已删除" % (idx, rec["image"]))
            elif rec["rule"] == "R1":
                notes.append(
                    "第 %d 题 R1：解析首图 %s 已回填选项 %s"
                    % (idx, rec["image"], rec["target_label"])
                )
            else:
                notes.append(
                    "第 %d 题 R3：浮动图 %s 已补标号 %s" % (idx, rec["image"], rec["label"])
                )
    return notes
