"""Implementation of the document/PDF parsing helpers callable from main.

These bodies live here so ``main.py`` stays a thin entry point; ``main`` keeps
thin wrappers so existing ``patch("main.<name>")`` seams keep working.
"""

import os
import re

from main import (
    TMP_UPLOAD_DIR,
    UPLOAD_DIR_REL,
    apply_bailian_thinking_policy,
    apply_deepseek_thinking_policy,
    build_pdf_parse_system_prompt,
    find_source_page_by_overlap,
    get_current_curriculum,
    get_metadata_cache,
    inject_reasoning_effort,
    normalize_fillin_macro,
    parse_ai_json,
    post_chat_completion,
    resolve_text_provider,
)

from mathbank.latex_normalize import normalize_for_storage, normalize_stem_for_storage

# 一道题挂到这么多张配图基本可以断定归属出了偏差，导入后提请人工核对。
_IMAGE_COUNT_REVIEW_THRESHOLD = 3

# 题目正文里可能出现的插图引用有**两类形态**，都必须被认领：
#   ① tmp 下的临时资产：/static/<uploads>/tmp/xxx（pdf_crop_* 裁剪图、word_* 公式预览图…）
#      —— 任务期内的一切产图都落在这里，所以**宽口径**，文件名不限。
#   ② MinerU 落盘插图：/static/<uploads>/mineru_[local_]*.jpg
#      —— 云端解析路径，`mineru_client.stage_images` 直接把它复制到静态**根目录**，
#         文件名带 `mineru` 前缀，**不带 /tmp/**。
# ⚠️ 根目录必须**窄口径**（只认 mineru_ 前缀）：同一目录下还有整页原图 `pdf_page_*`
#     与用户自行上传的图片，一律不能进 image_paths（否则每道题都会挂一张整页图）。
# ⚠️ 历史 bug：曾把这两类混成一个「只认 tmp」的正则，于是 MinerU 路径下 image_paths
#     恒为空 —— 配图总览显示「配图 0 张」、插图全被列进「未归属图」，而图其实一直
#     躺在静态根目录里没人认领。
_UPLOAD_URL_RE = r"/static/(?:uploads|test_uploads|uploads_test)"
_TMP_ASSET_REF_RE = re.compile(_UPLOAD_URL_RE + r"/tmp/[a-zA-Z0-9_.-]+")
_STAGED_IMAGE_REF_RE = re.compile(_UPLOAD_URL_RE + r"/mineru_[a-zA-Z0-9_.-]+")

# 选项标号行判据：整行只有标号（`A` / `A．`），或以「标号+标点」开头（`A．106`）。
# ⚠️ 必须容忍**不带标点的裸标号行**：实测 AI 输出常见「A」单独一行、图形另起一行，
#    早期版本要求「图片必须与标号同行」，导致选项图照样被删（线上踩过）。
_CHOICE_LABEL_RE = re.compile(
    r"^[ \t]*(?:[A-D]|[Ａ-Ｄ])[ \t]*(?:[.．、,，:：)）][^\n]*)?$"
)

# **裸标号行**：整行只有标号（`A` / `A．`），后面没有任何正文。
# 「标号在上一行 → 图片属于该选项」这个推断**只允许**用裸标号行做依据：
# 像 `A．106 B．105 C．104 D．103` 这种把四个选项写在同一行的行虽然也以
# `A．` 开头，但它是**完整的选项行**，跟在它后面的图片是普通题干配图
# （实测：第 5 题末尾挂着第 1 题的解答图，就是这个误判放进去的）。
_BARE_CHOICE_LABEL_RE = re.compile(
    r"^[ \t]*(?:[A-D]|[Ａ-Ｄ])[ \t]*[.．、,，:：)）]?[ \t]*$"
)

# 「整行只有图片」判据：用于「标号一行、图形另起一行」的形态。
# 少了它，任何**紧跟选项行之后的普通配图**（如 `如图 ![](url) 所示` 里那种）
# 都会被误判成选项图而留在正文里。
_IMAGE_ONLY_RE = re.compile(r"^[ \t]*(?:!\[[^\]]*\]\([^)]*\)[ \t]*)+$")

# choices 环境内的 `\item`：该行承载的就是**选项内容本身**。
_CHOICES_ITEM_RE = re.compile(r"\\item\b")

# **裸 `\item` 行**：整行只有 `\item`（允许尾随空格），后面没有任何内容。
# AI 实测的另一种形态是「`\item` 单独一行、图形另起一行」（与裸标号行同构），
# 此时 `\item` 行剥掉图片语法后恰好只剩 `\item`，正是「下一行的图属于该选项」的
# 可靠依据。⚠️ 只认**裸** `\item`：`\item 106` 这种已带内容的行不在此列，
# 它后面若跟一张整行图，按上面第 5 题的经验那是普通配图，不能当选项图留下。
_BARE_ITEM_LINE_RE = re.compile(r"^[ \t]*\\item[ \t]*$")


def _choices_env_state(line: str, in_choices: bool) -> bool:
    """按一行文本更新「是否位于 choices 环境内」的状态。

    一行里同时出现起止标记时，以**靠后的那个**为准（`\\end{choices}` 后紧跟
    `\\begin{choices}` 的紧凑排版也判得对）。
    """
    stripped = line.strip()
    last_begin = stripped.rfind("\\begin{choices}")
    last_end = stripped.rfind("\\end{choices}")
    if last_begin < 0 and last_end < 0:
        return in_choices
    return last_begin > last_end


def _strip_image_reference(text: str, image_url: str) -> str:
    """删掉指向 ``image_url`` 的图片语法，**但保留属于选项的那些**。

    选项图的判定必须容忍三种排版（前两种是 AI 的形态、实测**分行是主要形态**，
    第三种是整页 OCR 的形态）：
      ① 同行：``A．![](url)`` —— 标号在图片之前，直接认。
      ② 分行：``A`` 单独一行、下一行只有图片 —— 此时要求**该行除图片外没有别的文字**，
         否则「如图 ![](url) 所示」这类普通题干图会被误判。
      ③ ``\\begin{choices}`` 环境内的 ``\\item`` 行 —— 见下方注释。

    选项图一旦被删，题干会退化成「A／B／C／D」四个空壳、选项与图形彻底错位；
    普通配图则从正文移除，改由配图条统一展示，避免同一张图渲染两次。
    """

    if not image_url or not isinstance(text, str) or not text:
        return text
    pattern = re.compile(
        r"!\[[^\]]*\]\([^)]*" + re.escape(image_url) + r"[^)]*\)[ \t]*"
    )
    kept = []
    last_meaningful = ""
    in_choices = False
    for line in text.split("\n"):
        in_choices = _choices_env_state(line, in_choices)
        stripped = line.strip()
        same_line = bool(_CHOICE_LABEL_RE.match(stripped))
        # ⚠️ OCR 提示词（COMMON_OCR_PROMPT 第 3 条）要求**剥离 A/B/C/D 标号**，
        #    它产出的是 `\item ![插图](...)`：行首是 `\item` 而不是标号，
        #    上面那条 same_line 判据完全认不出来 —— 于是四个选项图被逐个剥掉，
        #    题干只剩「\item / \item / \item / \item」四个空壳（线上第 1 题就这样）。
        #    choices 环境是**机器生成的确定性结构标记**，环境内 `\item` 行的图片
        #    按定义就是该选项的内容，比任何标号启发式都可靠，直接保留。
        choices_item = in_choices and bool(_CHOICES_ITEM_RE.search(stripped))
        # 「标号在上一行」只认**裸标号行**（整行只有标号）。用宽口径的
        # _CHOICE_LABEL_RE 会把 `A．106 B．105 C．104 D．103` 这种整行选项
        # 也当成标号行，导致紧随其后的普通配图被误判成选项图留在正文里。
        # 同构地，**裸 `\item` 行**也算「选项标号行」：AI 实测产出「`\item`
        # 单独一行、图形另起一行」时，`\item` 行剥掉图片后恰好只剩 `\item`，
        # 是比任何标号启发式都可靠的选项依据（漏认会让四个选项图被逐个剥掉，
        # 题干退化成四个空壳 `\item`）。
        below_choice_label = bool(
            _BARE_CHOICE_LABEL_RE.match(last_meaningful)
            or _BARE_ITEM_LINE_RE.match(last_meaningful)
        )
        only_image = bool(_IMAGE_ONLY_RE.match(stripped))
        if image_url in line and (
            same_line or choices_item or (below_choice_label and only_image)
        ):
            kept.append(line)
        else:
            kept.append(pattern.sub("", line))
        # 供下一行判断：去掉图片语法后仍然非空的行才算得上「标号行」。
        remainder = pattern.sub("", kept[-1]).strip()
        if remainder:
            last_meaningful = remainder
    return "\n".join(kept)


# 空壳选项行。两种形态都算：
#   ① ``\item`` 后面**没有实质内容**（空、只有一个 A-H 字母、或只有标点）；
#   ② **裸字母行**（整行只有一个 A-H 字母，可带标点）—— AI 把选项图引用全丢进
#      referenced_images 时，正文常只剩 `A`/`B`/`C`/`D` 各占一行。
# 选项本身是图形时，这两类行都只是「标号壳」，必须回填图片，否则选项与图形彻底错位
# （线上第 1 题多个回合实测：空壳 / 无选项 / `\item A` 各出现过）。
# ⚠️ 只认「空 / 单个字母」：`A．106`、`\item 106 条` 这种带真实内容的选项绝不在此列，
#    否则回填会把正常选项串行。
_EMPTY_ITEM_LINE_RE = re.compile(
    r"^[ \t]*\\item[ \t]*(?:[A-Ha-hＡ-Ｈ][ \t]*[.．、,，:：)）]?)?[ \t]*$"
)
_BARE_LETTER_LINE_RE = re.compile(
    r"^[ \t]*[-*]?[ \t]*[A-Ha-hＡ-Ｈ][ \t]*[.．、,，:：)）]?[ \t]*$"
)

# 一行之内的图片语法（Markdown 与 LaTeX 两种形态），用于判断选项行是否「只有图」。
_IMAGE_SYNTAX_RE = re.compile(
    r"!\[[^\]]*\]\([^)]*\)|\\includegraphics(?:\[[^\]]*\])?\{[^}]*\}"
)

# 空壳【答案】：后面除了空白/全角空格/markdown 星号/中文标点什么都没有，
# 且**紧接着又一个【答案】**（否则那是「答案缺失」的真实信号，必须留着）。
_EMPTY_ANSWER_SHELL_RE = re.compile(
    r"【答案】[ \t　]*[：:。.、]?[ \t　*]*(?:\r?\n+[ \t　*]*)+"
    r"(?=(?:\*\*)?【答案】)"
)

# 正文里所有图片引用的 URL（两种形态各捕获一组），用于查「同一张图引用了两次」。
_IMAGE_REF_PATTERNS = (
    re.compile(r"!\[[^\]]*\]\(([^)]+)\)"),
    re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}"),
)


def _image_refs_in(text: str) -> list:
    urls = []
    for pattern in _IMAGE_REF_PATTERNS:
        urls.extend(match.group(1) for match in pattern.finditer(text or ""))
    return urls


def _backfill_choice_images(content: str, candidates, question_type: str = "", mark=None):
    r"""把「未被正文引用的选项图」按顺序回填进空壳选项行。

    返回 ``(新正文, 实际回填的图片 URL 列表)``。``\item`` 行上的图**必须留下**
    （见 :func:`_strip_image_reference`），但 AI 有时压根不在正文写图片引用，
    这时光靠剥离/保留逻辑救不回来——只能反向把 image_paths 里的选项图补回去。

    触发条件（缺一不做，避免误伤普通题）：
      ① 明确定义为选择题；
      ② 正文里存在**空壳选项行**（``\item`` 空壳，或裸字母行）；
      ③ 没有任何**文字选项**（``\item 106 条`` 这种）—— 有文字选项说明这是一道
         文字题，不该被塞图；「``\item`` 里只有一张图」是已填好的选项，不算文字选项；
      ④ **未被正文引用**的候选图数量 >= 空壳数量；
      ⑤ 至少有一张候选图还没出现在正文里 —— 全部已引用时无事可做，硬填会让同一张
         图渲染两次。
    ③④⑤ 都要看「实际内容」而不是「行有没有字」：实测 AI 会写漏其中**一个**选项的
    引用（四幅选项图只写三张、剩一个空壳 ``\item``），旧版第 ③⑤ 条一见正文里已有
    图片引用就直接放弃回填，选项 D 就永久是空壳。
    """
    if not content or question_type not in {"single_choice", "multi_choice"}:
        return content, []
    candidates = [u for u in (candidates or []) if u]
    if not candidates:
        return content, []
    unreferenced = [u for u in candidates if u not in content]
    if not unreferenced:
        return content, []

    lines = content.split("\n")
    in_choices = False
    empty_slots = []
    has_content_item = False
    has_option_material = False
    for idx, line in enumerate(lines):
        in_choices = _choices_env_state(line, in_choices)
        if "\\item" in line:
            has_option_material = True
            if _EMPTY_ITEM_LINE_RE.match(line):
                empty_slots.append(idx)
            elif _IMAGE_SYNTAX_RE.sub("", line).strip():
                # 去掉图片语法后仍有文字 = AI 写的是文字选项，整题不碰。
                has_content_item = True
        elif not in_choices and _BARE_LETTER_LINE_RE.match(line):
            # 裸字母行仅在**非** choices 环境内视为选项壳（环境内裸字母是选项内容）。
            has_option_material = True
            empty_slots.append(idx)
    if not has_option_material or not empty_slots or has_content_item:
        return content, []
    if len(unreferenced) < len(empty_slots):
        return content, []

    used = []
    for slot, image_url in zip(empty_slots, unreferenced):
        lines[slot] = f"\\item ![插图]({image_url})"
        used.append(image_url)
    if callable(mark):
        mark()
    return "\n".join(lines), used


def _synthesize_image_choices(content: str, candidates, question_type: str = ""):
    r"""选择题**整个选项区丢失**时的兜底：直接用候选图重建 choices 环境。

    另一种 AI 形态：正文只剩题干（连选项壳都没有），四张选项图全躺在
    ``referenced_images`` / image_paths 里。此时 :func:`_backfill_choice_images`
    够不着（没有可填的行），只能整体重建。

    触发条件（缺一不做）：
      ① 明确定义为选择题；② 正文没有任何选项材料（无 choices 环境、无 ``\\item``、
         无**带标点**的 A-H 标号行）；③ 正文没有任何图片引用；④ 候选图恰好构成
         常见的选项数（2 / 4）——数量对不上就不猜，宁可留给人工核对。
    """
    if not content or question_type not in {"single_choice", "multi_choice"}:
        return content, []
    if "![" in content or "\\includegraphics" in content:
        return content, []
    if "\\begin{choices}" in content or "\\item" in content:
        return content, []
    # 带标点的选项行（`A．xxx`）说明 AI 写了真选项，不是「选项丢失」，别重建。
    # ⚠️ 裸字母行（`A` 单独一行）**不算** —— 那是空壳，由回填逻辑处理。
    if re.search(r"^[ \t]*[-*]?\s*[A-H][\.、．]", content, re.MULTILINE):
        return content, []
    candidates = [u for u in (candidates or []) if u]
    if len(candidates) not in (2, 4):
        return content, []

    block = ["\\begin{choices}"]
    for image_url in candidates:
        block.append(f"\\item ![插图]({image_url})")
    block.append("\\end{choices}")
    return content.rstrip() + "\n\n" + "\n".join(block), list(candidates)


def _tmp_upload_dir() -> str:
    """Resolve the scratch upload dir at call time so tests can patch it.

    ``main`` keeps the authoritative value; importing it lazily avoids both a
    circular import at module load and a stale binding afterwards.
    """
    try:
        import main as _main

        return getattr(_main, "TMP_UPLOAD_DIR", None) or TMP_UPLOAD_DIR
    except Exception:  # pragma: no cover - defensive
        return TMP_UPLOAD_DIR


def post_process_pdf_parsed_questions(parsed_questions: list, paper_title: str, task_id: str = None, ocr_results: list = None) -> list:
    """PDF 专属解析卡片后处理：正则搜寻 /tmp/ 下的图片，以及将未解析的图n占位符智能映射回真实的裁剪插图图片，
    最后将其灌入 image_paths 数组中，并在 content 中静默清除以配合布局展示。支持文本重合度兜底映射，防大模型删除路径！"""
    import re
    import os
    import glob

    # 0. 规范化所有拆解题目的填空下划线为 \fillin 宏
    for q in parsed_questions:
        if q.get("content"):
            q["content"] = normalize_fillin_macro(q.get("content", ""))
        # answer_markdown 规范化：确保以【答案】开头
        am = q.get("answer_markdown") or ""
        if am.strip():
            stripped = am.lstrip()
            if not stripped.startswith("【答案】"):
                if q.get("question_type") in ("detailed_answer",):
                    q["answer_markdown"] = "【答案】见解析\n\n" + am
                else:
                    q["answer_markdown"] = "【答案】" + stripped
        # 每个【…】板块（答案/解析/分析/详解/点评…）独立成行，
        # 避免大模型把「【解析】【分析】」黏在同一行导致预览与导出拥挤。
        am = q.get("answer_markdown") or ""
        if am:
            am = re.sub(r"[ \t]*【", "\n\n【", am)
            am = re.sub(r"\n{3,}", "\n\n", am).strip()
            # MinerU/AI 会把答案标签重复输出（实测每道题都是「【答案】+空行+【答案】C」），
            # 上一步拆块后就成了两个独立板块：前端渲染出一行空答案，`has_answer` 判定也被
            # 带偏。只留有内容的那个；**后面紧跟的必须是又一个【答案】**才删，否则「答案
            # 缺失」这个信号会被抹掉。空白容忍全角空格、markdown 星号与中文标点。
            while True:
                collapsed = _EMPTY_ANSWER_SHELL_RE.sub("", am)
                if collapsed == am:
                    break
                am = collapsed
            q["answer_markdown"] = am

    # 1. 搜集该 PDF 任务在 tmp 文件夹中生成的所有物理裁剪图片，按生成时间（mtime）进行排序
    task_crop_urls = []
    if task_id:
        crop_pattern = os.path.join(_tmp_upload_dir(), f"pdf_crop_{task_id}_*.png")
        crop_files = glob.glob(crop_pattern)
        crop_files.sort(key=lambda x: os.path.getmtime(x))
        task_crop_urls = [f"/{UPLOAD_DIR_REL}/tmp/{os.path.basename(f)}" for f in crop_files]
        print(f"[PDF PostProcess] 发现任务 {task_id} 的实际裁剪图片 {len(task_crop_urls)} 张: {task_crop_urls}")

    # 2. 顺序提取出所有题目中未成功解析的插图占位符（例如 图1.png, 图2.png, 图1, 图2 等，特征是不以 /static/ 开头的图片引用路径）
    placeholders_in_order = []
    placeholder_seen = set()
    
    # 匹配 Markdown 图片格式: ![alt](url)
    md_pattern = r'!\[.*?\]\(([^)]+)\)'
    # 匹配 LaTeX 图片格式: \includegraphics[...]{path}
    latex_pattern = r'\\includegraphics(?:\[.*?\])?\{([^}]+)\}'
    
    for q in parsed_questions:
        for field in ["content", "answer_markdown"]:
            text_val = q.get(field, "")
            if isinstance(text_val, str):
                # 提取 Markdown 图片占位符
                for m in re.finditer(md_pattern, text_val):
                    url = m.group(1).strip()
                    if url and not url.startswith("/static/") and url not in placeholder_seen:
                        placeholder_seen.add(url)
                        placeholders_in_order.append(url)
                # 提取 LaTeX 图片占位符
                for m in re.finditer(latex_pattern, text_val):
                    url = m.group(1).strip()
                    if url and not url.startswith("/static/") and url not in placeholder_seen:
                        placeholder_seen.add(url)
                        placeholders_in_order.append(url)

    # 3. 建立占位符与物理裁剪图片路径的映射。
    #    裁剪文件名自带定位信息（pdf_crop_{task}_{page}_{idx}.png），因此**优先按文件名精确匹配**；
    #    只有模型自己编出来的图名（如 图1.png）才按出现顺序回落到剩余裁剪图队列。
    #    纯按顺序对接在多页并行 OCR 下并不可靠（mtime 顺序 ≠ 文档顺序）。
    mapping = {}
    crops_by_basename = {
        os.path.basename(url): url for url in task_crop_urls
    }
    used_basenames = set()
    for ph in placeholders_in_order:
        base = os.path.basename(ph.split("?")[0].split("#")[0]).strip()
        target = crops_by_basename.get(base)
        if target and base not in used_basenames:
            mapping[ph] = target
            used_basenames.add(base)

    leftover_crops = [
        url for url in task_crop_urls if os.path.basename(url) not in used_basenames
    ]
    leftover_idx = 0
    for ph in placeholders_in_order:
        if ph in mapping:
            continue
        if leftover_idx >= len(leftover_crops):
            break
        mapping[ph] = leftover_crops[leftover_idx]
        leftover_idx += 1
    if mapping:
        print(f"[PDF PostProcess] 成功建立占位符修复映射: {mapping}")

    # 4. 对每个题目卡片进行字段修补、占位符替换与资源晋升准备
    #    assigned_crops：一张裁剪图**只允许归属一道题**。这是业务硬约束——两题共用
    #    同一张图时，`image_paths` 是文件生命周期的唯一依据，删掉其中一题就会把
    #    另一题还在用的图一起清掉。将来若真要支持复合题拆分，必须由上游**复制**
    #    出一份新文件给新题，而不是共享同一个路径。
    assigned_crops = set()
    for q in parsed_questions:
        q["source"] = (q.get("source") or paper_title).strip()
        
        # 清理多余的双重转义 \n（AI 常把换行写成字面「反斜杠+n」）。
        # ⚠️ 判据用 `(?![a-z])` 而**不是** `(?![a-zA-Z])`：后者会放过 `\nA`，
        #    让「反斜杠+n+大写字母」残留成字面文本，随后被公式补定界符逻辑当成
        #    LaTeX 命令 `nA` 包成 `$\nA$`（实测 MinerU 路线第 2~5 题选项整段被污染成
        #    `$\nA$．2 条 B．4 条…`）。LaTeX 控制词是「反斜杠 + 连续字母」，命令名
        #    大小写敏感且**实际命令一律小写**（`\n` 开头的真命令只有 `\nabla`、
        #    `\neq`、`\nu`、`\newline`、`\not`、`\nmid` 这类小写形式），因此
        #    `\n` 后面跟小写字母时才可能是真命令，必须保留；跟大写字母 / 中文 /
        #    数字 / 标点时绝无可能是命令，一律按换行还原。
        for field in ["content", "answer_markdown"]:
            if field in q and isinstance(q[field], str):
                text = q[field]
                text = re.sub(r'\\n(?![a-z])', '\n', text)
                q[field] = text

        # 智能替换 Markdown 和 LaTeX 字段中的图片占位符
        for field in ["content", "answer_markdown"]:
            if field in q and isinstance(q[field], str):
                # 替换已建立映射的非标准路径
                for ph, real_url in mapping.items():
                    if ph in q[field]:
                        q[field] = q[field].replace(ph, real_url)
                        # 如果是 LaTeX 的 \includegraphics 语法，顺带转换为 Markdown 图片语法以供前端预览渲染
                        latex_img_pattern = r'\\includegraphics(?:\[.*?\])?\{' + re.escape(real_url) + r'\}'
                        q[field] = re.sub(latex_img_pattern, f'![插图]({real_url})', q[field])

        # 寻找本题正文中夹带的所有插图 URL。
        # 按**首次出现顺序**收集成 list（而非 set）：set 的迭代顺序受字符串哈希随机化
        # 影响，会让同一题的配图顺序在服务重启后漂移。
        # 两类形态都收：tmp 下的 PDF 裁剪图 + 静态根目录的 MinerU 落盘插图（见文件头注释）。
        found_crops = []
        seen_crops = set()
        for field in ["content", "answer_markdown"]:
            value = q.get(field)
            if not isinstance(value, str) or not value:
                continue
            for pattern in (_TMP_ASSET_REF_RE, _STAGED_IMAGE_REF_RE):
                for match in pattern.finditer(value):
                    url = match.group(0)
                    if url not in seen_crops:
                        seen_crops.add(url)
                        found_crops.append(url)

        # 记下「本题正文/解析里真的写了引用」的那批图：step 6 会把非选项引用从正文
        # 剥掉（改由 image_paths 作为题末插图渲染），剥完就再也查不出来了，而第 7 步
        # 的「配图数量偏多」自检必须区分「模型确实引用了这么多」与「被兜底塞进来这么多」。
        text_crops = list(found_crops)

        # 顺带检查 referenced_images 属性并应用修复映射。
        # 这里同样要覆盖两类形态：模型常把 MinerU 插图只列在这个字段里（正文不写引用）。
        for ref in q.get("referenced_images") or []:
            mapped_ref = str(mapping.get(ref, ref) or "")
            candidate = None
            staged = _STAGED_IMAGE_REF_RE.search(mapped_ref)
            if staged:
                candidate = staged.group(0)
            elif "/tmp/" in mapped_ref:
                normalized = f"/{UPLOAD_DIR_REL}/tmp/{os.path.basename(mapped_ref)}"
                if _TMP_ASSET_REF_RE.search(normalized):
                    candidate = normalized
            if candidate and candidate not in seen_crops:
                seen_crops.add(candidate)
                found_crops.append(candidate)

        # 跨题去重：已被前面题目认领的裁剪图不再给本题，避免「一图两题」。
        owned_crops = [url for url in found_crops if url not in assigned_crops]
        duplicated = [url for url in found_crops if url in assigned_crops]
        if duplicated:
            print(
                "[PDF PostProcess] 以下插图已被其它题目认领，本题不再重复挂载"
                f"（如需共用请复制为新文件）: {duplicated}"
            )
            # 既然本题不认领它们，正文里也不该留着引用 —— 否则界面上会渲染出一张
            # 属于**别的题**的图（选项行上的引用保留，理由见 _strip_image_reference）。
            content = q.get("content")
            if isinstance(content, str) and content:
                for url in duplicated:
                    content = _strip_image_reference(content, url)
                q["content"] = content
        assigned_crops.update(owned_crops)

        # 灌入 image_paths 作为独立配图卡片关联
        q["image_paths"] = owned_crops
        owned_set = set(owned_crops)
        q["_text_crops"] = [url for url in text_crops if url in owned_set]

    # 5. 极致兜底机制：如果大模型在拆题时完全删除了图片占位标记或路径，导致最终题目关联的图片为空，
    # 我们利用 3-shingle 文本重合度，将原始 PDF 物理页面产生的物理插图自动关联绑定回拆分出的题目！
    #    两条硬约束（缺失就会出现「末题把整卷的图都识别过来」）：
    #    ① 与任何页都没有文本重合时**不做兜底**——旧实现会退回第 0 页，把第 1 页的
    #       全部插图塞给这道对不上的题；
    #    ② 已被其它题目显式认领的裁剪图不得再被抢走，且每张图最多归属一道题。
    if ocr_results and task_id:
        def _crop_order(crop_url: str):
            match = re.search(r"pdf_crop_[A-Za-z0-9_-]+?_(\d+)_(\d+)\.png$", crop_url)
            return (int(match.group(1)), int(match.group(2))) if match else (0, 0)

        page_crops = {}
        # 页级裁剪图清单**必须来自磁盘**（step 1 的 task_crop_urls），不能从整页 OCR
        # 文本里正则捞：裁图完成后 ``[ILLUSTRATION_BOX: ...]`` 与占位符都会被
        # process_ocr_illustrations / step 6 抹掉，页文本里根本不存在 pdf_crop 路径
        # ——旧实现因此恒得到 {0: [], 1: []}，这道兜底从来没生效过，模型一旦把图片
        # 文件名整段丢掉，插图就彻底消失。
        for crop_url in task_crop_urls:
            order = _crop_order(crop_url)
            page_crops.setdefault(order[0], []).append(crop_url)
        for page_index in page_crops:
            page_crops[page_index].sort(key=_crop_order)

        print(f"[PDF PostProcess Failsafe] 每页识别到的插图关系: {page_crops}")

        # 为**每一道题**推断来源页（不只是没图的题）。前端的「配图总览」用它标注
        # 「本题疑似来自第 N 页」并直接调出该页原图，这样人工能对着原页核对
        # 「这题到底有没有图 / 图有没有被漏识别」，不必离开工作台翻原文件。
        # 与任何页都无文本重合时记 -1（前端显示「页码未知」）。
        for q in parsed_questions:
            q["_source_page"] = find_source_page_by_overlap(q.get("content", ""), ocr_results)

        def _crop_key(url) -> str:
            # 兜底比对一律用文件名：正文里模型写的 URL 前缀（/static/uploads/…）
            # 与磁盘清单（UPLOAD_DIR_REL，测试态是 /static/test_uploads/…）不一定同形，
            # 按整串比较会把同一张图当成两张不同的图重复分配。
            return os.path.basename(str(url or "").split("?")[0])

        claimed_crops = set()
        for q in parsed_questions:
            for path in (q.get("image_paths") or []):
                claimed_crops.add(_crop_key(path))

        # 每道「没有配图」的题按来源页排队，页内剩余裁剪图**一题一张轮转**分配。
        # 旧实现把整页剩余图一次性全塞给文档里最早的那道题 —— 兜底刚修好就生效的话，
        # 现象就是某题挂着四幅根本不属于自己的图（用户最反感的「图片被其他题引用」）。
        pending_by_page = {}
        for q in parsed_questions:
            if q.get("image_paths"):
                continue
            p_source = q.get("_source_page", -1)
            if p_source < 0:
                print(
                    "[PDF PostProcess Failsafe] 该题与任何 PDF 页均无文本重合，"
                    f"跳过图像兜底: {(q.get('content') or '')[:40]}..."
                )
                continue
            pending_by_page.setdefault(p_source, []).append(q)

        for p_source, queue in pending_by_page.items():
            remaining = [
                c for c in page_crops.get(p_source, []) if _crop_key(c) not in claimed_crops
            ]
            if not remaining:
                continue
            # **一题一张**：按文档顺序把页内剩余裁剪图依次发给缺图的题。
            # 旧实现把整页剩余图一次性全塞给最早的那道题，现象就是某题挂着四幅
            # 根本不属于自己的图（用户最反感的「图片被其他题引用」）。真需要第二幅
            # 的题由人工对着原页【手动截图】补，宁可少发也不要错发。
            for q, crop in zip(queue, remaining):
                claimed_crops.add(_crop_key(crop))
                q["image_paths"] = [crop]
                # 打标记：这批配图是「页级兜底」推断的，模型并未给出引用，导入后需人工核对。
                q["_image_review"] = sorted(set(q.get("_image_review") or []) | {"failsafe"})
                print(
                    f"[PDF PostProcess Failsafe] 成功通过重合度，将第 {p_source + 1} 页的插图"
                    f" {crop} 兜底分配给题目: {(q.get('content') or '')[:40]}..."
                )
            if len(remaining) > len(queue):
                print(
                    "[PDF PostProcess Failsafe] 该页剩余插图多于缺图题目数，"
                    f"未分配的 {len(remaining) - len(queue)} 张保持未归属，"
                    "请在原页对照【手动截图】补图。"
                )

        # 5c. 仍未归属的裁剪图：只有当它**夹在两道题的已认领插图之间**时，才并给前面那道。
        #     依据：后面那道题自己的图已经在它名下了，这张夹在中间的图不可能是它的，
        #     只能是前一道题的第二幅（四幅选项图之后紧跟一幅解析配图就是这一形态；模型
        #     一旦漏写引用，这张图就会变成孤儿文件被清理，用户看到的是「解析里的图凭空
        #     消失」）。页尾无后续认领的剩余图**不并**——那正是「一题吞一页」的形态。
        owner_by_key = {}
        for q in parsed_questions:
            for path in (q.get("image_paths") or []):
                owner_by_key[_crop_key(path)] = q
        for p_source in sorted(page_crops):
            ordered = page_crops[p_source]
            owners = [owner_by_key.get(_crop_key(c)) for c in ordered]
            last_owner = None
            chained = set()
            for position, (crop, owner) in enumerate(zip(ordered, owners)):
                if owner is not None:
                    last_owner = owner
                    continue
                if last_owner is None or id(last_owner) in chained:
                    continue
                if not any(
                    later is not None and later is not last_owner
                    for later in owners[position + 1:]
                ):
                    continue
                last_owner["image_paths"] = list(last_owner.get("image_paths") or []) + [crop]
                owner_by_key[_crop_key(crop)] = last_owner
                chained.add(id(last_owner))
                last_owner["_image_review"] = sorted(
                    set(last_owner.get("_image_review") or []) | {"failsafe"}
                )
                print(
                    f"[PDF PostProcess Failsafe] 第 {p_source + 1} 页的插图 {crop}"
                    " 夹在两题已认领插图之间，按文档顺序并给前一道题（需人工核对）："
                    f"{(last_owner.get('content') or '')[:40]}..."
                )

    # 6. 从 content 题干中静默移除已绑定至 image_paths 的图片语法，避免与配图条重复渲染。
    #    ⚠️ **选项图必须留下**：`A．![](url)` 里的图是选项本身的一部分，
    #    删了之后题干会变成「A．/B．/C．/D．」四个空壳（实测过），选项与图形对不上。
    for q in parsed_questions:
        content = q.get("content")
        if not isinstance(content, str) or not content:
            continue
        for crop_url in (q.get("image_paths") or []):
            content = _strip_image_reference(content, crop_url)
        q["content"] = content.strip()

    # 6.5 选项图回填（AI 把选项图引用全丢进 referenced_images / 只留 image_paths 时）。
    #     这是 step 6「选项图必须留下」的**反向补丁**：step 6 保证「写了引用就留住」，
    #     本步保证「压根没写引用也能补回来」。
    #     ⚠️ 必须放在 step 6 **之后**：AI 若把图写成独立的 `![...]` 行（不在选项行上），
    #     step 6 会把它们剥掉，此时才看得出「选项区缺图」。放在 step 6 之前，
    #     这些待剥的引用会让守卫误判成「已有引用」而放弃回填（实测 MinerU 路线踩过）。
    for q in parsed_questions:
        content = q.get("content")
        if not isinstance(content, str) or not content:
            continue
        q_type = q.get("question_type") or ""
        # 解析里已经用过的图不能拿来当选项：它属于解答，塞进选项会渲染两次。
        answer_text = q.get("answer_markdown") or ""
        candidates = [
            url for url in (q.get("image_paths") or [])
            if url and url not in answer_text
        ]

        def _mark_option_backfill():
            q["_image_review"] = sorted(set(q.get("_image_review") or []) | {"failsafe"})

        content, filled = _backfill_choice_images(
            content, candidates, q_type, mark=_mark_option_backfill
        )
        if not filled:
            content, filled = _synthesize_image_choices(content, candidates, q_type)
        if filled:
            q["content"] = content
            print(
                "[PDF PostProcess] 选项图回填：该题选项区缺少图片引用，"
                f"已按顺序补回 {len(filled)} 张选项图。"
            )

    # 6.6 选项区重复引用同一张图：视觉模型漏框了图形，AI 只能拿已有的两张凑四个选项。
    #     实测同一份卷子重跑时第 1 题变成「图1 图2 图1 图2」——卷面看起来是完整的，
    #     答案却必然错，比空壳选项更危险，所以必须打标记交给人核对。
    for idx, q in enumerate(parsed_questions, start=1):
        content = q.get("content")
        if not isinstance(content, str) or not content:
            continue
        counts = {}
        for url in _image_refs_in(content):
            key = os.path.basename(str(url).split("?")[0])
            counts[key] = counts.get(key, 0) + 1
        dupes = sorted(name for name, times in counts.items() if times > 1)
        if dupes:
            q["_image_review"] = sorted(set(q.get("_image_review") or []) | {"duplicate"})
            print(
                f"[配图自检] 第 {idx} 题的选项重复引用同一张图 {dupes}："
                "视觉模型漏识别了部分图形，请对照原页【手动截图】补图。"
            )

    # 7. 配图自检：给需要人工核对的题打标记（供卡片角标使用），并输出一行摘要。
    #    只在「配图是兜底推断来的」或「配图数量明显偏多」时提示——绝大多数题目本来就
    #    没有配图，对它报警只会淹没真正异常的那几道。
    loose_counts = []
    for q in parsed_questions:
        paths = q.get("image_paths") or []
        referenced = set(q.pop("_text_crops", None) or [])
        reasons = set(q.get("_image_review") or [])
        # 「数量偏多」只统计**没有出现在本题正文/解析里**的图（即靠推断挂上来的）。
        # 选择题四幅选项图 + 一幅解析配图是完全合法的形态（实测 1.pdf 第 1 题 5 张
        # 全部在正文里），把它们一起计入告警会让每道图形选择题都亮红角标，
        # 真正「吞了别题插图」的那道题反而被淹没。
        loose = [url for url in paths if url not in referenced]
        loose_counts.append(len(loose))
        if len(loose) >= _IMAGE_COUNT_REVIEW_THRESHOLD:
            reasons.add("many")
        if reasons:
            q["_image_review"] = sorted(reasons)
        else:
            q.pop("_image_review", None)

    review_notes = []
    for idx, q in enumerate(parsed_questions, start=1):
        reasons = q.get("_image_review") or []
        if not reasons:
            continue
        count = len(q.get("image_paths") or [])
        label = []
        if "failsafe" in reasons:
            label.append("由页级兜底推断")
        if "many" in reasons:
            label.append(f"{loose_counts[idx - 1]} 张正文未引用")
        if "duplicate" in reasons:
            label.append("选项重复引用同一张图")
        review_notes.append(f"第 {idx} 题（{count} 张，{'；'.join(label)}）")
    if review_notes:
        print("[配图自检] 以下题目的配图建议人工核对：" + "；".join(review_notes))
    else:
        print("[配图自检] 全部题目的配图归属未发现异常。")

    # 8. 入库前 LaTeX 归一化：与 TeX / 批量图片路线共用同一个出口函数，
    #    保证「卡片预览 == 写库结果」只有一份实现。
    return apply_storage_normalization(parsed_questions)


def apply_storage_normalization(parsed_questions: list) -> list:
    # 入库前 LaTeX 归一化（结构层 + 字符层一致）：各条录入路线（MinerU / 原生 / 视觉 OCR /
    # TeX / 批量图片）在这里把各自风格的原料统一成同一种 LaTeX 风味源码——图片
    # \\includegraphics{文件名}、表格 tabular、选项 \\begin{choices}、加粗 \\textbf、
    # 填空 \\fillin，再补数学定界符与压缩多余空行。调用点必须在图片收集/剥离之后，
    # 那时 image_paths 资源清单已生成，正文里只剩未归属内联图（含选项图）。
    # 必须与写库边界跑同一条链（normalize_for_storage，而非只做结构层或只做字符层）：
    # 拆卷卡片里用户会手改文字，保存时边界再把同一条幂等链跑一遍，所以卡片预览必须
    # 等于入库结果；否则同一次导入里「未改动的题」与「改过一个字的题」格式就不一致。
    for q in parsed_questions:
        q_type = q.get("question_type") or ""
        if q.get("content"):
            # 模型没按提示词剥离原卷题号时在这里兜底：题号归 paper_question_no，
            # 题干只留正文，否则 A4 预览与 PDF 会排成「1. 1. …」双编号。
            lead_no, q["content"] = normalize_stem_for_storage(q["content"], q_type)
            if lead_no and not str(q.get("paper_question_no") or "").strip():
                q["paper_question_no"] = lead_no
        if q.get("answer_markdown"):
            # 解析（answer）不是选项，不传 question_type，避免把解析里的「A．」误转选项。
            q["answer_markdown"] = normalize_for_storage(q["answer_markdown"], is_answer=True)
    return parsed_questions


def parse_paper_text_internal(
    latex_content: str,
    generate_answers_bool: bool
) -> list:
    """内部通用函数：调用选定的 LLM 接口，将 LaTeX 试卷内容解析拆分为结构化 JSON 卡片"""
    parse_model = os.getenv("PREFER_PARSE_MODEL") or os.getenv("DEEPSEEK_PARSE_MODEL", "deepseek-flash")
    provider = resolve_text_provider(parse_model)
    api_key = provider.api_key
    api_base = provider.api_base
    model_name = provider.model_name
    provider_name = provider.provider_label

    if not api_key:
        raise ValueError(f"未配置对应的 API Key ({provider.credential_label})，无法智能拆解试卷！请在工作台右上角设置面板进行配置。")

    system_instructions = build_pdf_parse_system_prompt(
        get_current_curriculum(),
        generate_answers_bool,
        difficulties=get_metadata_cache().get("difficulties"),
    )

    max_output_tokens = 65536

    data = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_instructions},
            {"role": "user", "content": latex_content}
        ],
        "response_format": {
            "type": "json_object"
        },
        "temperature": 0.2,
        "max_tokens": max_output_tokens
    }
    
    data = inject_reasoning_effort(data, provider.reasoning_effort)
    # 解析（试卷/TeX）是结构化任务：DeepSeek 关闭思考，避免 V4.1-Flash 默认
    # 的 high effort 拖慢批量解析。
    data = apply_deepseek_thinking_policy(
        data,
        model_name=model_name,
        api_base=api_base,
        provider_code=provider.provider_code,
        task="parse",
        thinking_enabled=False,
    )
    data = apply_bailian_thinking_policy(
        data,
        provider_code=provider.provider_code,
        model_name=model_name,
        task="parse",
    )
    
    response = post_chat_completion(
        provider,
        data,
        timeout=180,
        provider_name=provider_name,
    )
        
    res_json = response.json()
    raw_ai_text = res_json["choices"][0]["message"]["content"].strip()
    
    parsed_data = parse_ai_json(raw_ai_text, raw_markdown=latex_content)
    
    if isinstance(parsed_data, dict):
        if "questions" in parsed_data and isinstance(parsed_data["questions"], list):
            parsed_questions = parsed_data["questions"]
        elif "data" in parsed_data and isinstance(parsed_data["data"], list):
            parsed_questions = parsed_data["data"]
        else:
            parsed_questions = None
            for key, val in parsed_data.items():
                if isinstance(val, list):
                    parsed_questions = val
                    break
                if parsed_questions is None:
                    parsed_questions = [parsed_data]
    elif isinstance(parsed_data, list):
        parsed_questions = parsed_data
    else:
        raise Exception("AI 返回的 JSON 格式不正确，期望是一个数组或包含 questions 列表的对象。")
        
    # 强制进行静默净化：若未勾选自动生成答案，则对于没有带有 [EXTRACTED_ORIGINAL] 的解析和解答，将其强行抹平为空。
    for q in parsed_questions:
        ans = q.get("answer_markdown", "")
        if not ans:
            q["answer_markdown"] = ""
            continue
        if not generate_answers_bool:
            if "[EXTRACTED_ORIGINAL]" in ans:
                q["answer_markdown"] = ans.replace("[EXTRACTED_ORIGINAL]", "").strip()
            else:
                q["answer_markdown"] = ""
        else:
            q["answer_markdown"] = ans.replace("[EXTRACTED_ORIGINAL]", "").strip()

    # ⚠️ 这里**不再**单独跑字符层补定界符与解析紧凑化：那两步已经并入
    # normalize_for_storage，由紧随其后的 post_process 第 8 步与写库边界统一执行。
    # 拆题阶段先跑字符层、写库时又只跑结构层，正是「同一写法两种入库源码」和
    # 「管道表格被 $ 包死后丢行」的根因；模型漏掉的定界符由那条链负责补齐。
    return parsed_questions
