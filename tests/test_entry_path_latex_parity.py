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

import pytest

from main import LOCAL_TOKEN
from mathbank.latex_normalize import normalize_for_storage
from mathbank.math_markdown import (
    compact_solution_markdown,
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


def test_page_markers_never_reach_storage():
    for raw in (CASES[5][2], CASES[5][3]):
        assert "MATHBANK_PDF_PAGE" not in normalize_for_storage(raw, "detailed_answer", True)


@pytest.mark.parametrize("raw,expected", [
    ("1.下列各曲线中（）", "1. 下列各曲线中（）"),
    ("2、如图，已知", "2. 如图，已知"),
    ("3．某地自 2018 年起", "3. 某地自 2018 年起"),
    ("  12.已知函数", "12. 已知函数"),
])
def test_leading_question_number_style_is_unified(raw, expected):
    out = normalize_for_storage(raw, "single_choice")
    assert out == expected
    assert normalize_for_storage(out, "single_choice") == out, "题号统一必须幂等"


@pytest.mark.parametrize("untouched", [
    "3.5 的相反数是（）",           # 小数，不是题号
    "2019·全国高考题已知 $a=1$",     # 年份出处
    "（1）已知 $a_1=1$，求通项公式。",  # 解答题小问
    "$|a| + |b| = 1$，求范围",       # 绝对值，不是表格行
])
def test_leading_number_rule_does_not_eat_real_content(untouched):
    assert normalize_for_storage(untouched, "detailed_answer") == untouched


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
