"""三条录入路径的入库 LaTeX 源码一致性（收敛性）回归测试。

用户的硬要求：单题编辑器手敲（含单题 OCR）、试卷拆解卡片（含卡片内手改文字）、
二次编辑保存 —— 这三条路径最终写进 `questions.content` / `questions.answer_markdown`
的 LaTeX 源码必须完全一致；同一条链反复保存也不许漂移。

因此这里钉三件事：
1. 收敛：同一道题，三条路径入库后字节相同。
2. 幂等：normalize_for_storage 对自身输出恒等（草稿箱重存、二次保存不走形）。
3. 保真：归一化不得吃掉表格行、图片、选项与填空宏。
"""
import random
import re
import shutil

import pytest

from main import LOCAL_TOKEN
from mathbank.latex_normalize import normalize_for_storage
from mathbank.math_markdown import (
    compact_solution_markdown,
    normalize_fillin_macro,
    normalize_question_math_markdown,
)

HDRS = {"X-Local-Token": LOCAL_TOKEN}

# (用例名, question_type, 题干原始输入, 解析原始输入)
# 输入刻意保留各条引擎的真实怪癖：字面 \n、页标、多余空行、裸公式、____、** 加粗、
# Markdown 图片、管道表格。
CASES = [
    (
        "naked_math_in_stem",
        "single_choice",
        "已知 $f(x)=x^2-2x+1$，则 f(x) 的最小值为（）\nA．0\nB．1\nC．-1\nD．2",
        "",
    ),
    (
        "fillin_underscores",
        "fill_in_blank",
        r"设 a=2, b=3，则 a*b = ____",
        "",
    ),
    (
        "fillin_already_macro",
        "fill_in_blank",
        r"设 a=2, b=3，则 a*b = \fillin",
        "",
    ),
    (
        "fillin_underline_form",
        "fill_in_blank",
        r"不等式 $2x>4$ 的解集为 \underline{\hspace{2em}} 。",
        "",
    ),
    (
        "literal_backslash_n_options",
        "single_choice",
        r"1. 计算 3+5 的结果是（）\nA．8\nB．9\nC．10\nD．11",
        "",
    ),
    (
        "page_marker_leftover",
        "detailed_answer",
        "<!-- MATHBANK_PDF_PAGE:1 -->\n已知 $a=1$，求 a 的值。",
        "解：因为 $x^2\\ge 0$，\n\n\n\n所以结论成立。",
    ),
    (
        "pipe_table_with_math",
        "detailed_answer",
        "某调查数据如下表：\n| 年份 | 2018 | 2019 |\n| --- | --- | --- |\n"
        "| 数量 | 160 | x_1 |\n若 $x_1>150$，求结论。",
        "",
    ),
    (
        "blank_run_before_choices",
        "single_choice",
        "题干（）\n\n\n\nA．1\nB．2\nC．3\nD．4",
        "",
    ),
    (
        "display_math_in_answer",
        "detailed_answer",
        r"求 $\dfrac{1}{2}$ 的值。",
        "$$x=1$$\n\n\n\n故得解。",
    ),
    (
        "real_pdf_page2_card",
        "single_choice",
        "2. 如图，在长方体 $ABCD-A_1B_1C_1D_1$ 的棱中，与棱 $AB$ 垂直的棱有（）\n\n\n\n"
        "\\begin{choices}\n\\item 2 条\n\\item 4 条\n\\item 6 条\n\\item 8 条\n\\end{choices}",
        "【答案】D\n\n【解析】共 $8$ 条．",
    ),
    (
        "markdown_inline_image",
        "single_choice",
        "如图，$\\angle A=30^\\circ$（）\n![图](/static/uploads/a.png)\nA．1\nB．2\nC．3\nD．4",
        "",
    ),
    (
        "markdown_bold_in_stem",
        "detailed_answer",
        "求**函数** $f(x)=\\sqrt{x}$ 的定义域。",
        "",
    ),
]


def _storage_chain(value: str, question_type: str = "", is_answer: bool = False) -> str:
    """路径② 在拆解阶段会先单独跑一遍字符层（图片批量拆卷卡片的预览期）。

    归一化链必须对这种「半成品」也收敛，否则同一次导入里改过字的题和没改的题就两种源码。
    """
    if not value:
        return value
    value = compact_solution_markdown(normalize_question_math_markdown(value)) if is_answer \
        else normalize_question_math_markdown(value)
    return normalize_for_storage(value, question_type, is_answer)


@pytest.mark.parametrize("name,qt,content,answer", CASES, ids=[c[0] for c in CASES])
def test_normalize_for_storage_is_idempotent(name, qt, content, answer):
    once = normalize_for_storage(content, qt)
    assert normalize_for_storage(once, qt) == once, f"{name}: 题干二次归一化漂移"
    if answer:
        once_a = normalize_for_storage(answer, is_answer=True)
        assert normalize_for_storage(once_a, is_answer=True) == once_a, f"{name}: 解析二次归一化漂移"


@pytest.mark.parametrize("name,qt,content,answer", CASES, ids=[c[0] for c in CASES])
def test_manual_and_parsed_paths_converge_in_memory(name, qt, content, answer):
    """纯函数层：手敲链 与 拆解链（先跑字符层）必须落到同一份源码。"""
    assert normalize_for_storage(content, qt) == _storage_chain(content, qt), name
    if answer:
        assert (normalize_for_storage(answer, is_answer=True)
                == _storage_chain(answer, "", True)), name


def _post(client, content, answer, qt):
    r = client.post(
        "/api/questions",
        headers=HDRS,
        data={
            "content": content,
            "question_type": qt,
            "category_compulsory": "必修一",
            "category_chapter": "第一章",
            "category_knowledge": "测试小节",
            "difficulty": "basic",
            "answer_markdown": answer,
            "image_paths": "[]",
            "sources": "[]",
        },
    )
    assert r.status_code == 200, r.text
    q = r.json()["question"]
    return q["id"], q["content"], q["answer_markdown"]


@pytest.mark.parametrize("name,qt,content,answer", CASES, ids=[c[0] for c in CASES])
def test_all_three_paths_store_identical_latex(client, name, qt, content, answer):
    """全栈层：POST(手敲) / POST(拆解产物) / PUT(二次编辑保存) 入库源码必须逐字节相同。"""
    id1, c1, a1 = _post(client, content, answer, qt)
    parsed_content = _storage_chain(content, qt)
    parsed_answer = _storage_chain(answer, "", True) if answer else ""
    id2, c2, a2 = _post(client, parsed_content, parsed_answer, qt)

    # 路径③：把路径①的结果改一个字再保存，格式不得走形。
    r = client.put(
        f"/api/questions/{id1}",
        headers=HDRS,
        data={
            "content": c1 + "\n\n补充：$b>0$",
            "answer_markdown": a1,
            "question_type": qt,
            "difficulty": "basic",
            "category_compulsory": "必修一",
            "category_chapter": "第一章",
            "category_knowledge": "测试小节",
            "image_paths": "[]",
            "sources": "[]",
        },
    )
    assert r.status_code == 200, r.text
    c3 = r.json()["question"]["content"]

    assert c1 == c2, f"{name}: 手敲与拆解入库题干不一致\n①={c1!r}\n②={c2!r}"
    assert a1 == a2, f"{name}: 手敲与拆解入库解析不一致\n①={a1!r}\n②={a2!r}"
    assert c3 == normalize_for_storage(c1 + "\n\n补充：$b>0$", qt), f"{name}: 二次保存后格式漂移"


def test_pipe_table_rows_survive_the_chain():
    """管道表格不得被补定界符包死后丢行（历史上真实发生过的数据损坏）。"""
    out = normalize_for_storage(CASES[6][2], "detailed_answer")
    assert "\\begin{tabular}" in out and "\\end{tabular}" in out
    for cell in ("2018", "2019", "160", "x_1"):
        assert cell in out, f"表格数据被吃掉: {cell}\n{out!r}"
    assert "$| 数量" not in out and "| 数量" not in out


def test_choice_lines_become_choices_env():
    out = normalize_for_storage(CASES[7][2], "single_choice")
    assert "\\begin{choices}" in out and out.count("\\item") == 4
    # 题干与选项之间的多余空行统一成一个自然段
    assert "\n\n\n" not in out


def test_fillin_macro_and_images_and_bold_are_preserved():
    # 三种填空写法必须落成同一个 \fillin（同一句话，只换占位写法）
    forms = [
        "解集为 ____ 。",
        r"解集为 \fillin 。",
        r"解集为 \fillin[2cm] 。",
        r"解集为 \underline{\hspace{2em}} 。",
        r"解集为 \underline{\fillin} 。",
    ]
    outs = [normalize_for_storage(s, "fill_in_blank") for s in forms]
    assert all(o == outs[0] for o in outs), list(zip(forms, outs))
    assert "\\fillin" in outs[0] and "}" not in outs[0].replace("\\fillin", ""), outs[0]
    img = normalize_for_storage(CASES[10][2], "single_choice")
    assert "\\includegraphics" in img and "](" not in img
    assert "\\textbf{函数}" in normalize_for_storage(CASES[11][2], "detailed_answer")


def test_mineru_doubled_escapes_collapse_to_one_backslash():
    """MinerU 云路线整份 markdown 多转义一层，入库前必须压回单反斜杠。

    实测 `$B _ { l } C _ { \\\\mathit { l } }$`：`\\\\` 在数学模式是换行命令，后面的
    `mathit` 会被当正文排出，公式直接错。
    """
    doubled = r"在长方体 $ABCD-A_{1}B_{1}C_{1}D_{1}$ 中，$B _ { l } C _ { \\mathit { l } }$"
    out = normalize_for_storage(doubled, "single_choice")
    assert "\\\\mathit" not in out, out
    assert r"\mathit" in out, out
    # 合法硬换行（`\\` 后带空格）不得被吃掉
    table = "\\begin{tabular}{|c|c|}\n\\hline 1 & 2 \\\\ \\hline 3 & 4 \\hline\n\\end{tabular}"
    assert "2 \\\\ \\hline" in normalize_for_storage(table, "detailed_answer")
    # 幂等：跑两遍结果一致
    assert normalize_for_storage(out, "single_choice") == out


def test_choice_options_split_by_blank_lines_stay_one_block():
    """选项之间只隔空行时必须落成一个 choices 块（MinerU 云路线实测四段四块）。"""
    md = "下列各曲线中（ ）\n\nA．甲\n\nB．乙\n\nC．丙\n\nD．丁"
    out = normalize_for_storage(md, "single_choice")
    assert out.count("\\begin{choices}") == 1, out
    assert out.count("\\item") == 4, out


def test_page_markers_never_reach_storage():
    for raw in (CASES[5][2], CASES[5][3]):
        assert "MATHBANK_PDF_PAGE" not in normalize_for_storage(raw, "detailed_answer", True)


@pytest.mark.parametrize("raw,number,rest_start", [
    ("1.下列各曲线中（）", "1", "下列各曲线中"),
    ("2、如图，已知", "2", "如图，已知"),
    ("3．某地自 2018 年起", "3", "某地自 2018 年起"),
    ("  12.已知函数", "12", "已知函数"),
    (r"17(1) 已知数列 $\{a_n\}$", "17", "(1) 已知数列"),
])
def test_leading_question_number_is_stripped_from_stem(raw, number, rest_start):
    from mathbank.latex_normalize import normalize_stem_for_storage

    lead, text = normalize_stem_for_storage(raw, "single_choice")
    assert lead == number, (raw, lead)
    assert text.startswith(rest_start), (raw, text)
    # 再跑一次不得继续剥，也不得改动产物
    lead2, text2 = normalize_stem_for_storage(text, "single_choice")
    assert lead2 == "" and text2 == text


@pytest.mark.parametrize("untouched", [
    "3.5 的相反数是（）",           # 小数，不是题号
    "2019·全国高考题已知 $a=1$",     # 年份出处
    "（1）已知 $a_1=1$，求通项公式。",  # 解答题小问
    "$|a| + |b| = 1$，求范围",       # 绝对值，不是表格行
])
def test_number_extraction_does_not_eat_real_content(untouched):
    from mathbank.latex_normalize import normalize_stem_for_storage

    lead, text = normalize_stem_for_storage(untouched, "detailed_answer")
    assert lead == ""
    assert text == normalize_for_storage(untouched, "detailed_answer")


def test_stored_question_keeps_original_number_in_paper_question_no(client):
    """原题号必须落到 paper_question_no，且不得覆盖用户已填的值。"""
    _id, content, _a = _post(client, "8. 已知 $a=1$，求 a。", "", "detailed_answer")
    assert content.lstrip().startswith("已知"), content
    q = client.get(f"/api/questions/{_id}", headers=HDRS).json()
    q = q.get("question", q)
    assert str(q.get("paper_question_no")) == "8", q.get("paper_question_no")

    _id2, _c2, _a2 = _post(client, "9. 已知 $b=2$，求 b。", "", "detailed_answer")
    body = {
        "content": _c2, "answer_markdown": "", "question_type": "detailed_answer",
        "difficulty": "basic", "paper_question_no": "17(2)",
        "category_compulsory": "必修一", "category_chapter": "第一章",
        "category_knowledge": "测试小节", "image_paths": "[]", "sources": "[]",
    }
    r = client.put(f"/api/questions/{_id2}", data=body, headers=HDRS)
    assert r.status_code == 200, r.text
    assert r.json()["question"]["paper_question_no"] == "17(2)"


# 固定种子的性质测试：把真实怪癖片段随机拼装，检查「合流 + 幂等」两条不变量。
FRAGMENTS = [
    r"已知 $f(x)=x^2-2x$，则 f(x) 的最小值为（）",
    r"设 a=2, b=3，则 a*b = ____",
    r"解集为 \underline{\hspace{2em}} 。",
    r"计算 3+5：\nA．8\nB．9",
    "<!-- MATHBANK_PDF_PAGE:2 -->\n求 $\\dfrac{1}{2}$。",
    "| 年份 | 2018 | 2019 |\n| --- | --- | --- |\n| 数量 | 160 | x_1 |",
    r"**重点**：$|a|+|b|=1$，则 a 与 b 的关系",
    r"如图 ![图](/static/uploads/a.png) 所示",
    "\\begin{choices}\n\\item 2 条\n\\item 4 条\n\\end{choices}",
    r"5. 已知数列 $\{a_n\}$ 满足 $a_1=1$，$a_{n+1}=2a_n$。",
    "$$x=1$$\n\n\n故得解。",
    r"（1）求通项公式；（2）求前 n 项和。",
    "\\begin{tabular}{|c|c|}\\hline x & y \\\\ \\hline 1 & 2 \\\\ \\hline\\end{tabular}",
    r"已知 $\alpha=30^\circ$，则 \tan\alpha = ___",
    r"求函数 $y=\sqrt{x-1}$ 的定义域为 ____，值域为 \fillin 。",
]
QUESTION_TYPES = ["single_choice", "multi_choice", "fill_in_blank", "detailed_answer"]


def test_normalization_chain_is_confluent_and_idempotent_on_random_combinations():
    rng = random.Random(20260919)
    for _ in range(300):
        raw = rng.choice(["1.", "2. ", "3、", ""]) + rng.choice(FRAGMENTS)
        qt = rng.choice(QUESTION_TYPES)
        for is_answer in (False, True):
            key = "" if is_answer else qt
            once = normalize_for_storage(raw, key, is_answer=is_answer)
            # 拆解/OCR 阶段先跑过字符层的半成品，经完整链后必须与直接入库完全一致
            pre = compact_solution_markdown(normalize_question_math_markdown(raw)) if is_answer \
                else normalize_question_math_markdown(raw)
            assert once == normalize_for_storage(pre, key, is_answer=is_answer), raw
            assert normalize_for_storage(once, key, is_answer=is_answer) == once, raw


@pytest.mark.skipif(not shutil.which("xelatex"), reason="xelatex not installed")
def test_normalized_source_compiles_without_double_numbering(client):
    """入库源码必须能被 XeLaTeX 真编译，且不出现「1. 1.」双编号。

    归一化只管文本一致还不够：exam-zh 与 A4 预览都会自行编号，题干残留原题号时
    实测会排成「1. 1. 下列各曲线中」。这里把三条路径共同的产物送去编译并抽文本核对。
    """
    import pymupdf

    ids = []
    for raw in (
        "1.下列各曲线中，不能表示 $y$ 是 $x$ 的函数的是（）\nA．甲\nB．乙\nC．丙\nD．丁",
        "2、如图，长方体 $ABCD-A_1B_1C_1D_1$ 中与棱 $AB$ 垂直的棱有（）\n"
        "A．2 条\nB．4 条\nC．6 条\nD．8 条",
    ):
        qid, _c, _a = _post(client, raw, "【答案】C\n\n【解析】由 $x_1=1$ 得结论。", "single_choice")
        ids.append(qid)

    r = client.post(
        "/api/paper/export/pdf",
        headers=HDRS,
        json={
            "title": "归一化源码编译验证", "subtitle": "", "paper_type": "exam",
            "target": "paper", "include_answers": False,
            "show_secret": False, "show_notice": False,
            "questions": [{"id": i, "score": 5} for i in ids],
        },
    )
    assert r.status_code == 200, r.text[:1200]
    assert r.content[:5] == b"%PDF-"

    text = "\n".join(page.get_text() for page in pymupdf.open(stream=r.content, filetype="pdf"))
    compact = re.sub(r"[ \t]+", " ", text)
    assert not re.search(r"(?<!\d)(\d{1,2})\.\s*(\1)\.(?!\d)", compact), "题干原题号与自动编号重复"
    for leaked in ("\\begin", "\\item", "\\includegraphics", "\\fillin"):
        assert leaked not in compact, f"原始 LaTeX 命令泄漏进 PDF: {leaked}"
    assert "下列各曲线中" in compact and "如图，长方体" in compact


# ---------------------------------------------------------------------------
# \underline{...} 只在「组内纯占位」时才转 \fillin（待办 #9 收尾）
# ---------------------------------------------------------------------------

BLANK_UNDERLINE_FORMS = [
    r"\underline{\hspace{1.5cm}}",
    r"\underline{\hspace{2em}}",
    r"\underline{\hspace*{2em}}",
    r"\underline{\hspace{1cm}\hspace{1cm}}",
    r"\underline{\quad}",
    r"\underline{\qquad}",
    r"\underline{   }",
    r"\underline{___}",
    r"\underline{}",
    r"\underline{  \hspace{1.5cm}  }",
]

CONTENT_UNDERLINE_FORMS = [
    r"\underline{x}",              # 单变量下标式记号
    r"\underline{a+b}",            # 表达式
    r"\underline{M}",              # 重复数位 / 共轭
    r"\underline{3}",
]

# 组内含**任何非 ASCII 字符**（哪怕包在 \text{} 里）的写法：_strip_underline_group
# 会原样保留，但完整链后面的字符层给裸 LaTeX 补 $ 时会在第一个非 ASCII 字符处截断 ——
# `\underline{重点内容}` 变 `$\underline{$重点内容}`、`\underline{\text{甲}}` 变
# `$\underline{\text{$甲}}`。那是字符层的既有缺陷、与本次收紧判据无关（旧实现根本不
# 产出这个形态，因为它把整组吃成 \fillin），所以这两个样本单独测 step-1，不去碰那条链。
CJK_CONTENT_UNDERLINE_FORMS = [
    r"\underline{重点内容}",
    r"\underline{\text{甲}}",
    r"\underline{\hspace{1cm} 且仅当 a}",
]


@pytest.mark.parametrize("form", BLANK_UNDERLINE_FORMS)
def test_blank_underline_group_still_becomes_fillin(form):
    """纯占位横线照旧收敛到 \fillin —— 收紧判据不许把正常填空漏掉。"""
    out = normalize_for_storage("解集为 " + form + " 。", "fill_in_blank")
    assert "\\fillin" in out, out
    assert r"\underline" not in out, out


@pytest.mark.parametrize("form", CONTENT_UNDERLINE_FORMS)
def test_content_underline_group_is_preserved_verbatim(form):
    r"""\underline{实际内容} 必须原样保留（隐患：旧实现无条件整组换成 \fillin）。

    \underline{M} 表重复数位、\underline{a+b} 是被强调的表达式，吃掉就是丢题干内容。
    """
    out = normalize_for_storage("求 " + form + " 的值", "fill_in_blank")
    assert form in out, out
    assert "\\fillin" not in out, out


@pytest.mark.parametrize("form", CJK_CONTENT_UNDERLINE_FORMS)
def test_cjk_content_underline_survives_the_step(form):
    r"""含裸中文的 \underline{...} 在填空规范化这一步必须原样留下。

    完整链上字符层会把它拦腰补 $（既有缺陷，见 CJK_CONTENT_UNDERLINE_FORMS 注释），
    所以这里只测 _strip_underline_group 本身：判据收紧后它不得再吃内容。
    """
    out = normalize_fillin_macro("求 " + form + " 的值")
    assert form in out, out
    assert "\\fillin" not in out, out


def test_dollar_wrapped_blank_underline_converts():
    r"""带 $ 包裹的 $\underline{\hspace{1.5cm}}$ 是原生提取路径的真实形态。"""
    out = normalize_for_storage(r"则 $f(x) =$ $\underline{\hspace{1.5cm}}$", "fill_in_blank")
    assert "\\fillin" in out, out
    assert r"\underline" not in out, out


def test_choices_environment_keeps_its_own_item_structure():
    r"""选项行内的占位横线原位转 \fillin，不得破坏 choices 结构与选项数。"""
    src = (
        "\\begin{choices}\n"
        "    \\item 甲\\underline{\\hspace{1cm}}乙\n"
        "    \\item 丙\n"
        "\\end{choices}"
    )
    out = normalize_for_storage(src, "single_choice")
    assert "\\item 甲\\fillin乙" in out, out
    assert out.count("\\item") == 2, out
    assert "\\begin{choices}" in out and "\\end{choices}" in out, out


@pytest.mark.parametrize(
    "form", BLANK_UNDERLINE_FORMS + CONTENT_UNDERLINE_FORMS
)
def test_underline_conversion_is_idempotent(form):
    """两类形态都必须幂等：T(T(x)) == T(x)。"""
    once = normalize_for_storage("已知 " + form + " ，求 x", "fill_in_blank")
    assert normalize_for_storage(once, "fill_in_blank") == once, form


def test_five_fillin_writings_still_converge():
    r"""\underline{\fillin} 是历史入库形态，五种填空写法仍须落成同一源码。

    收紧判据时差点把它当成「有内容」漏掉 —— \fillin 本身就在占位白名单里。
    """
    forms = [
        "解集为 ____ 。",
        r"解集为 \fillin 。",
        r"解集为 \fillin[2cm] 。",
        r"解集为 \underline{\hspace{2em}} 。",
        r"解集为 \underline{\fillin} 。",
    ]
    outs = [normalize_for_storage(s, "fill_in_blank") for s in forms]
    assert len(set(outs)) == 1, list(zip(forms, outs))


# ---------------------------------------------------------------------------
# 待办 #25：字符层补 $ 不得在中文（非 ASCII）处把 LaTeX 命令拦腰截断
# ---------------------------------------------------------------------------

def _no_truncated_command(text: str) -> list[str]:
    r"""返回「$ 紧跟在 \command{ 之后」的坏形态列表（截断的直接证据）。"""
    return re.findall(r"\\w+\{\$", text)


@pytest.mark.parametrize(
    "source",
    [
        r"求 \underline{重点内容} 的值",
        r"求 \underline{\text{甲}} 的值",
        r"已知\text{甲乙丙}成立",
        r"\mathrm{甲}",
        r"解 \sqrt{甲}+1",
    ],
)
def test_non_ascii_command_argument_is_never_split_by_a_dollar(source):
    r"""``$\underline{$``、``$\text{$`` 这类形态必须彻底消失。

    ``_MATH_RUN_RE`` 的字符类只含 ASCII，中文天然是 run 断点；旧实现照样把
    半截命令包成 $，定界符落单、反斜杠命令被从参数中间切断。
    """
    out = normalize_for_storage(source, "fill_in_blank")
    assert not _no_truncated_command(out), out
    assert "$" not in out or out.count("$") % 2 == 0, out
    assert source in out, out          # 一字不改地留在题干里
    assert normalize_for_storage(out, "fill_in_blank") == out, out


@pytest.mark.parametrize(
    "source, expected",
    [
        (r"x=\frac{1}{2}", r"$x=\frac{1}{2}$"),
        (r"\sqrt{x}+1", r"$\sqrt{x}+1$"),
        (r"集合 \complement_{U}A", r"集合 $\complement_{U}A$"),
        (r"点 \overrightarrow{AB}=3", r"点 $\overrightarrow{AB}=3$"),
        ("x+y=1", "$x+y=1$"),
        ("已知 f(x)=x^2 求最小值", "已知 $f(x)=x^2$ 求最小值"),
    ],
)
def test_ascii_math_runs_keep_their_existing_wrapping(source, expected):
    r"""纯 ASCII 与中英混排的正常补 $ 行为不变；行内分式保持源码里的 ``\frac``。"""
    assert normalize_for_storage(source, "fill_in_blank") == expected


def test_trim_keeps_every_original_character():
    r"""裁剪只决定 $ 落在哪，绝不允许吞掉或改写任何原字符（分式一律原样保留）。"""
    for source in (
        r"求 \underline{重点内容} 的值",
        r"a^{2}+b^{2}=c^{2} 且 x>0",
        r"则 \frac{a}{b} 为最大值",
    ):
        out = normalize_for_storage(source, "fill_in_blank")
        assert out.replace("$", "") == source.replace("$", ""), (source, out)
