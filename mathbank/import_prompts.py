"""批量录入提示词（第四录入方案）。

设计要点：**纪律层**与**契约层**分离。

- 纪律层（``DISCIPLINE_RULES``）是从望悠移植的通用行为约束——逐字转录、
  公式不许改写、反斜杠只写一个、全角转半角等。这部分与存储格式无关，
  两个模式共用，且原样保留望悠的表达（它的措辞经过实战，别改）。
- 契约层（``TEXTBOOK_*`` / ``EXAM_*``）必须按本系统的存储约定重写：
  数学分隔符是 ``$..$``/``$$..$$``（不是 ``\\(..\\)``）、题型是
  ``single_choice/multi_choice/fill_in_blank/detailed_answer``、选项写进
  ``\\begin{choices}`` 环境、插图是 ``\\includegraphics{basename}``。
  望悠的 schema（``kind: example/theorem``、``[[FIG:n]]``、大题 sections）
  在这里被翻译成我们的契约，**不直接沿用**。
"""

from __future__ import annotations

TEXTBOOK_MODE = "textbook"
EXAM_MODE = "exam"
VALID_MODES = (TEXTBOOK_MODE, EXAM_MODE)

# ---------------------------------------------------------------------------
# 纪律层：移植自望悠，两个模式共用。改这里等于改两块功能的判断标准，慎动。
# ---------------------------------------------------------------------------

DISCIPLINE_RULES = """公式纪律（违反任何一条，这一批都会被判失真退回）：
1. 公式一律保持原样。行内用 $...$，行间用 $$...$$（**不是 \\( \\) 或 \\[ \\]**）。
   不要把行内改成行间，也不要合并或拆分公式段。
2. 不要化简、改写、展开或「整理」公式：分式、上下标、**看似多余的分组花括号**、
   \\left/\\right、\\mathop/\\limits、array/cases/aligned 等结构必须原样保留。
3. 命令名前面的反斜杠只写一个。\\circ、\\theta、\\frac 这些命令，JSON 字符串里
   必须是单个反斜杠加命令名。写成两个反斜杠再接字母时，渲染器会当成换行符，
   于是 {D}^{\\circ} 会印成「换行 + circ 三个字母」——不报错，静默出错。
4. 不要新增条目，不要把一条拆成两条，不要合并两条。输入几条就回几条。
5. 逐字转录，不总结、不省略、不添加、不自己描述图的内容。

扫描件处理：
6. 常见全角字符一律转半角：Ｅｕｃｌｉｄ→Euclid、１．１→1.1、（　）→()。
7. 页眉、页脚、页码、水印不要转录。
8. 本页开头若是从上一页延续的内容，直接从断点处接着转录；本页结尾被截断的
   内容也转录到断点为止。不要补省略号，不要加任何衔接说明。
"""

# ---------------------------------------------------------------------------
# 契约层：按本系统 normalize_for_storage 的约定输出
# ---------------------------------------------------------------------------

_OUTPUT_CONTRACT = """输出严格的 JSON 对象，不要包 ``` 代码块，不要有任何解释文字。
结构：{"items": [ ... ]}，每个 item 的字段如下（缺字段就给空串/空数组，不要编造）：

- stem：题干正文。公式用 $...$（行内）/ $$...$$（行间）。
- question_type：只能是 single_choice / multi_choice / fill_in_blank / detailed_answer 四者之一。
- options：选项正文数组，**不含 A. B. C. 前缀**；非选择题给空数组 []。
- answer：答案。选择题只写选项字母（多选连写如 "AB"）；填空按空的顺序用「；」分隔；
  解答题写最终结论一句话。原文写了「故选 X」「故填 X」就照它写。
- analysis：解析正文。
- paper_question_no：卷内原题号（如 "1"、"17(1)"）。教材条目填「例 1.1.1」里的编号部分。
- label：条目标记原文（如「例 1.1.1」「定理 1.1.6」）；源码里没写编号就给空串。
- score：分值数字。卷面上没写就给 0，不要猜。
- figures：本条用到的插图文件名数组（只写文件名，不写路径）。没有就给 []。
- continues_next：本条是否在本页末尾被截断、下半部分在下一页（bool）。只有教材长题
  确实跨页时才给 true；单页自洽的题一律给 false。跨页题上下两半分别识别、由各给
  一条 item，系统会按相同 label 自动拼接，所以下半部分那条照样正常输出 stem/analysis。

插图必须用 \\includegraphics{文件名} 的形式嵌在 stem / analysis 的原文位置，
**不要用 Markdown 的 ![]() 语法**，也不要自己描述图里画了什么。
"""

TEXTBOOK_SYSTEM = """你在把一页中文数学教材/练习册的扫描图转录成结构化题目数据。

条目切分：
1. 凡以【例】【题】【定理】【命题】【推论】【引理】【注】【习题】开头的条目各算一条 item，
   正文讲解段落不算。
2. kind 记到 label 里（如「例 1.1.1」「定理 1.1.6」）；题号部分同时填进 paper_question_no。
3. question_type 一律填 detailed_answer——教材条目不区分题型；
   只有确实出现了 A/B/C/D 选项的，才按选择题填 single_choice / multi_choice。

""" + DISCIPLINE_RULES + """
""" + _OUTPUT_CONTRACT

EXAM_SYSTEM = """你在把一页中文数学试卷的扫描图转录成结构化题目数据。

卷面结构：
1. 按卷面上的原题号逐条切分，题号填进 paper_question_no（如 "1"、"17(1)"）。
2. 题干开头不要重复写题号——题号只放 paper_question_no 字段。
3. question_type 判定：有 A/B/C/D 且单选 → single_choice；明确「多选」或答案连写
   （如 "AB"）→ multi_choice；有下划线/空要填 → fill_in_blank；其余 → detailed_answer。
4. 卷面上写了分值（如「（本小题满分 13 分）」）就填进 score，没写就填 0，不要猜。
5. 选择题末尾的括号（如「（　　）」）不要写进 stem——系统会自动排版。

""" + DISCIPLINE_RULES + """
""" + _OUTPUT_CONTRACT

# ---------------------------------------------------------------------------
# 目录页（TOC）识别：只抽层级，不抽正文。教材模式在正文识别前先跑这一遍。
# ---------------------------------------------------------------------------

TOC_SYSTEM = """你在识别一页中文数学教材/练习册的目录页（Contents）。

目录页列出各章、节的编号与标题，以及它们所在的页码。你只抽目录层级，不要抽正文讲解。

输出严格的 JSON 对象，不要包 ``` 代码块，不要有任何解释文字：
{"chapters":[{"number":"1","title":"集合与函数概念","sections":[{"number":"1.1","title":"函数概念"},{"number":"1.2","title":"函数的基本性质"}]}]}

编号规则：
1. 用阿拉伯数字与点号：章是 "1"，节是 "1.1"，三级标题是 "1.1.2"。照原样抄，不要改写。
2. 标题照原样转录（含中文），不要总结、不要省略。
3. 某章没有细分小节，就给空 sections 数组；只有章一级就 sections 为空。
4. 有的目录把"第一篇""第二部分"也列出，这类顶层分卷若明显不是章，忽略它，只抽真正的章/节。

""" + DISCIPLINE_RULES

# 教材条目类型 -> 本系统题型。教材默认走详细解答；只有真出现选项才当选择题。
_TEXTBOOK_KIND_TO_TYPE = {
    "example": "detailed_answer",
    "theorem": "detailed_answer",
    "proposition": "detailed_answer",
    "corollary": "detailed_answer",
    "lemma": "detailed_answer",
    "remark": "detailed_answer",
    "exercise": "detailed_answer",
    "single": "single_choice",
    "multiple": "multi_choice",
    "fill": "fill_in_blank",
    "answer": "detailed_answer",
}

_EXAM_KIND_TO_TYPE = {
    "single": "single_choice",
    "单选": "single_choice",
    "multiple": "multi_choice",
    "多选": "multi_choice",
    "fill": "fill_in_blank",
    "填空": "fill_in_blank",
    "answer": "detailed_answer",
    "解答": "detailed_answer",
}


def map_kind_to_question_type(kind: str, mode: str, *, has_options: bool = False) -> str:
    """把望悠/卷面的 kind 翻成本系统的 question_type。

    教材模式里望悠的 example/theorem 等一律落到 detailed_answer；只有识别结果里
    真带了选项时才按选择题处理。整卷模式按单选/多选/填空/解答直接映射。
    """

    key = str(kind or "").strip().lower()
    if mode == EXAM_MODE:
        mapped = _EXAM_KIND_TO_TYPE.get(key) or _EXAM_KIND_TO_TYPE.get(str(kind).strip())
        if mapped:
            return mapped
        return "single_choice" if has_options else "detailed_answer"

    mapped = _TEXTBOOK_KIND_TO_TYPE.get(key)
    if mapped and mapped != "detailed_answer":
        return mapped
    if has_options:
        return "multi_choice" if len(str(kind).strip().upper()) > 1 else "single_choice"
    return "detailed_answer"


def render_choices(options: list) -> str:
    """把选项数组渲染成本系统的 choices 环境（不带 A. B. 前缀，由 exam-zh 编号）。"""

    cleaned = [str(o).strip() for o in (options or []) if str(o).strip()]
    if not cleaned:
        return ""
    body = "\n".join(r"\item " + opt for opt in cleaned)
    return "\\begin{choices}\n" + body + "\n\\end{choices}"


def render_figure(filename: str, *, on_option_line: bool = False) -> str:
    """渲染插图引用。

    选项行上的图必须内联限宽（否则会被重排成居中块，破坏选项横排）；
    其余用正文限宽。文件名只写 basename，真实路径由 image_paths 提供。
    """

    name = str(filename or "").strip()
    if not name:
        return ""
    width = "0.42\\linewidth" if on_option_line else "0.85\\linewidth"
    return f"\\includegraphics[width={width}]{{{name}}}"


def build_recognize_messages(
    mode: str,
    *,
    page_no: int | None = None,
    total_pages: int | None = None,
    extra_hint: str = "",
) -> list:
    """构造视觉识别请求的消息体（system + user）。"""

    if mode not in VALID_MODES:
        raise ValueError(f"未知录入模式: {mode}")
    system_prompt = EXAM_SYSTEM if mode == EXAM_MODE else TEXTBOOK_SYSTEM

    prefix = ""
    if page_no and total_pages:
        prefix = f"这是第 {page_no} / {total_pages} 页。"
    hint = f"\n{extra_hint}" if extra_hint else ""
    user_prompt = (
        prefix
        + "请按系统提示的规则转录本页，直接输出 JSON。"
        + hint
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_toc_messages(
    *,
    page_no: int | None = None,
    total_pages: int | None = None,
) -> list:
    """构造目录页（TOC）视觉识别请求的消息体。"""

    prefix = ""
    if page_no and total_pages:
        prefix = f"这是第 {page_no} / {total_pages} 页，是一页目录。"
    user_prompt = prefix + "请按系统提示的规则识别本页目录，直接输出 JSON。"
    return [
        {"role": "system", "content": TOC_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]
