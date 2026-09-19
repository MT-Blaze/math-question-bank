"""入库前 LaTeX 归一化的回归测试。

核心保证：三条 PDF 录入路线（MinerU / 原生文字公式提取 / 全图视觉 OCR）的「原料」
形态不同（MinerU 给 HTML 表格、原生/视觉给管道表格，图片与选项写法也各异），
但经过 normalize_content_for_latex_storage 后应得到**同一种 LaTeX 风味源码**——
图片 \\includegraphics{文件名}、表格 tabular、选项 \\begin{choices}、加粗 \\textbf。
这是「源码一致而非渲染一致」的根基。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mathbank.latex_normalize import (
    html_table_to_tabular,
    pipe_table_to_tabular,
    latexify_images,
    latexify_choices,
    latexify_bold,
    normalize_content_for_latex_storage,
)


def test_pipe_table_to_tabular():
    pipe = "| 周数 | 1 | 2 | 3 |\n| --- | --- | --- | --- |\n| 人数 | 30 | 40 | 50 |"
    out = pipe_table_to_tabular(pipe)
    assert out.startswith(r"\begin{tabular}{|c|c|c|c|}")
    assert "周数 & 1 & 2 & 3" in out
    assert "人数 & 30 & 40 & 50" in out
    assert out.strip().endswith(r"\end{tabular}")


def test_html_table_to_tabular():
    html = ("<table><tr><th>周数</th><th>1</th><th>2</th><th>3</th></tr>"
            "<tr><td>人数</td><td>30</td><td>40</td><td>50</td></tr></table>")
    out = html_table_to_tabular(html)
    assert out.startswith(r"\begin{tabular}{|c|c|c|c|}")
    assert "周数 & 1 & 2 & 3" in out
    assert "人数 & 30 & 40 & 50" in out


def test_html_and_pipe_table_normalize_identically():
    html = ("<table><tr><th>周数</th><th>1</th><th>2</th><th>3</th></tr>"
            "<tr><td>人数</td><td>30</td><td>40</td><td>50</td></tr></table>")
    pipe = "| 周数 | 1 | 2 | 3 |\n| --- | --- | --- | --- |\n| 人数 | 30 | 40 | 50 |"
    assert html_table_to_tabular(html) == pipe_table_to_tabular(pipe)


def test_three_routes_produce_identical_source():
    """同一道题分别走 MinerU(HTML表) 与原生(管道表) 路线，归一化后源码必须一致。"""
    table_html = ("<table><tr><th>周数</th><th>1</th><th>2</th><th>3</th></tr>"
                 "<tr><td>人数</td><td>30</td><td>40</td><td>50</td></tr></table>")
    table_pipe = "| 周数 | 1 | 2 | 3 |\n| --- | --- | --- | --- |\n| 人数 | 30 | 40 | 50 |"
    shared = ("某小区流感大爆发。**注意**防护。\n{table}\n"
              "A．106 B．105 C．104 D．103\n![](/static/uploads/mineru_x.jpg)")
    mineru = shared.format(table=table_html)
    native = shared.format(table=table_pipe)
    assert normalize_content_for_latex_storage(mineru) == normalize_content_for_latex_storage(native)


def test_images_both_forms_to_includegraphics():
    md = "如图 ![](/static/uploads/tmp/pdf_crop_abc_1_2.png) 所示"
    out = latexify_images(md)
    assert out == r"如图 \includegraphics[max width=0.85\linewidth,keepaspectratio]{pdf_crop_abc_1_2.png} 所示"


def test_choice_image_stays_inline():
    md = "A．![](/static/uploads/mineru_y.jpg) B．图2"
    out = latexify_images(md)
    assert "max width=0.42" in out
    assert "mineru_y.jpg" in out


def test_choices_wrapping():
    out = latexify_choices("A．106 B．105 C．104 D．103", "single_choice")
    assert r"\begin{choices}" in out
    assert out.count(r"\item ") == 4


def test_two_option_choice_splits_both_items():
    """两选项判断题「A．对 B．错」也必须拆成两个 \\item，不能吞掉 B 标号。"""
    out = latexify_choices("A．对 B．错", "single_choice")
    assert out.count(r"\item ") == 2
    assert r"\item 对" in out
    assert r"\item 错" in out


def test_blank_lines_between_options_stay_one_choices_block():
    """选项之间只隔空行时仍是**一个** choices 块（实测 MinerU 云路线四幅选项图各成一段）。

    旧实现在空行处 flush，四个选项变成四个独立 `\\begin{choices}`，每块都从 A 重新
    编号，网页与 PDF 版面全散。
    """
    md = (
        "下列各曲线中，不能表示 $y$ 是 $x$ 的函数的是（ ）\n\n"
        "A．![插图](/static/uploads/a.png)\n\n"
        "B．![插图](/static/uploads/b.png)\n\n"
        "C．![插图](/static/uploads/c.png)\n\n"
        "D．![插图](/static/uploads/d.png)\n"
    )
    out = latexify_choices(md, "single_choice")
    assert out.count(r"\begin{choices}") == 1, out
    assert out.count(r"\end{choices}") == 1, out
    assert out.count(r"\item ") == 4, out
    # 题干与选项之间仍留一个空行，选项块没有被挪到正文末尾
    assert "）\n\n  \\begin{choices}" in out, out


def test_blank_line_then_prose_still_closes_choices_block():
    """选项块之后接正文（哪怕隔着空行）必须收尾，不能把正文吃进选项。"""
    md = "A．1\n\nB．2\n\n则下列说法正确的是（ ）"
    out = latexify_choices(md, "single_choice")
    assert out.count(r"\begin{choices}") == 1
    assert out.count(r"\item ") == 2
    assert "则下列说法正确的是" in out.split(r"\end{choices}")[1]


def test_choices_not_wrapped_for_non_choice_question():
    """防误伤（Q3）：非选择题正文里的「A．」纯文本不得被转成选项环境。"""
    md = "设 A．为集合，D．为常数，下列正确的是（  ）"
    assert r"\begin{choices}" not in latexify_choices(md, "detailed_answer")
    # 未传 question_type 时保留旧行为（纯文本判据）
    out = latexify_choices("A．甲 B．乙 C．丙 D．丁", "")
    assert r"\begin{choices}" in out


def test_choices_inserted_in_place_not_at_tail():
    """选项块必须就地插入，不能被挪到正文末尾——否则尾随图会被挤到选项前，
    导出侧「尾随图右置」判定失效。"""
    md = "题干。\nA．甲 B．乙 C．丙 D．丁\n![](/static/uploads/mineru_z.jpg)"
    out = normalize_content_for_latex_storage(md, "single_choice")
    choices_pos = out.index(r"\begin{choices}")
    image_pos = out.index(r"\includegraphics")
    assert choices_pos < image_pos, "选项块应位于尾随图之前（就地插入）"


def test_bold_to_textbf():
    assert latexify_bold("这是 **重点** 内容") == r"这是 \textbf{重点} 内容"


def test_choices_item_image_gets_inline_width():
    r"""choices 环境内 `\item` 行上的选项图必须走内联限宽（0.42），不是通栏。

    OCR 提示词要求剥离 A-D 标号，产出 `\item ![插图](...)`；只认标号的判据
    会让选项图取通栏 0.85，夹在 exam-zh choices 横排里宽度突兀。
    """

    md = (
        "题干（ ）\n\n"
        "\\begin{choices}\n"
        "\\item ![插图](tmp/pdf_crop_t_0_0.png)\n"
        "\\item ![插图](tmp/pdf_crop_t_0_1.png)\n"
        "\\end{choices}"
    )
    out = normalize_content_for_latex_storage(md, "single_choice")
    assert r"max width=0.42\linewidth" in out
    assert r"max width=0.85\linewidth" not in out


def test_plain_image_keeps_full_width():
    r"""choices 环境**之外**的普通插图仍然是通栏 0.85，别被新判据收窄。"""

    md = "题干见图 ![](/static/uploads/mineru_a.jpg)"
    out = normalize_content_for_latex_storage(md, "single_choice")
    assert r"max width=0.85\linewidth" in out
    assert r"max width=0.42\linewidth" not in out


def test_bare_item_with_image_below_merges_and_gets_inline_width():
    r"""「裸 `\item` 一行、图形另起一行」要合并成同行，并按选项图内联限宽（0.42）。

    这是 AI 在 choices 环境内的主要排版形态（OCR 提示词要求剥离 A-D 标号）。
    合并必须发生在 latexify_images **之前**：图片限宽靠图片所在行的行首判据决定，
    合并晚了图片会被判成普通配图取 0.85 通栏，夹进 exam-zh choices 横排里顶掉布局。
    """

    md = (
        "题干（ ）\n\n"
        "\\begin{choices}\n"
        "\\item\n"
        "![插图](tmp/pdf_crop_t_0_0.png)\n"
        "\\item\n"
        "![插图](tmp/pdf_crop_t_0_1.png)\n"
        "\\end{choices}"
    )
    out = normalize_content_for_latex_storage(md, "single_choice")
    assert r"\item \includegraphics[max width=0.42\linewidth" in out
    assert r"max width=0.85\linewidth" not in out
    # 每个 \item 都拿到了自己的图，没有空壳
    assert out.count(r"\includegraphics") == 2


def test_bare_item_image_merge_is_idempotent_on_latex_form():
    r"""合并对已是 ``\includegraphics`` 的形态同样生效，且二次归一化保持稳定。"""

    md = (
        "\\begin{choices}\n"
        "\\item\n"
        r"\includegraphics{pdf_crop_t_0_0.png}" "\n"
        "\\end{choices}"
    )
    once = normalize_content_for_latex_storage(md, "single_choice")
    twice = normalize_content_for_latex_storage(once, "single_choice")
    assert once == twice
    assert r"\item \includegraphics[max width=0.42\linewidth" in once


def test_existing_choices_env_has_item_labels_stripped():
    r"""AI 直出 `\begin{choices}` 时，`\item` 行上的 A-H 标号必须剥掉。

    实测 **MinerU 路线**的模型直出 `\item A．2 条`，而原生 / 视觉 OCR 路线的
    提示词要求剥离标号、产出 `\item 2 条`。两者入库源码不一致会带来两个后果：
      ① exam-zh 的 choices 环境自己生成标号，导出后出现「A. A．2 条」双标号；
      ② 三条录入路线的入库 LaTeX 不一致，违背横向一致契约。
    """

    md = (
        "题干（ ）\n\n"
        "\\begin{choices}\n"
        "\\item A．2 条\n"
        "\\item B．4 条\n"
        "\\item C．6 条\n"
        "\\item D．8 条\n"
        "\\end{choices}"
    )
    out = normalize_content_for_latex_storage(md, "single_choice")
    assert out.count("\\item ") == 4
    for label in ("A．", "B．", "C．", "D．"):
        assert label not in out
    assert "\\item 2 条" in out and "\\item 8 条" in out


def test_existing_choices_env_strips_labels_from_image_items():
    r"""选项是图片时，标号同样要剥、图必须留（`\item A．\includegraphics{...}`）。"""

    md = (
        "题干（ ）\n\n"
        "\\begin{choices}\n"
        "\\item A．\\includegraphics[max width=0.42\\linewidth]{p0.png}\n"
        "\\item B．\\includegraphics[max width=0.42\\linewidth]{p1.png}\n"
        "\\end{choices}"
    )
    out = normalize_content_for_latex_storage(md, "single_choice")
    assert "\\includegraphics" in out
    assert "p0.png" in out and "p1.png" in out
    assert "A．" not in out and "B．" not in out


def test_existing_choices_env_keeps_bare_labels_without_content():
    r"""裸标号（`\item A` / `\item A．`，标号后无内容）不能被剥成空壳。

    剥标号的判据要求标号后**必须还有内容**；否则本就没有内容的行会被清空，
    用户看到「选项全空」——比留一个标号更糟。
    """

    md = (
        "题干（ ）\n\n"
        "\\begin{choices}\n"
        "\\item A\n"
        "\\item B．\n"
        "\\item 有内容的选项\n"
        "\\end{choices}"
    )
    out = normalize_content_for_latex_storage(md, "single_choice")
    assert "\\item A\n" in out
    assert "\\item B．\n" in out


def test_rowspan_html_table_kept_verbatim():
    """过于复杂的 HTML 表（rowspan）不强行转 tabular，避免产出错误 LaTeX。"""
    html = '<table><tr><td rowspan="2">a</td><td>b</td></tr><tr><td>c</td></tr></table>'
    assert html_table_to_tabular(html) == html


def _auth_headers():
    from main import LOCAL_TOKEN

    return {"X-Local-Token": LOCAL_TOKEN}


def test_manual_create_normalizes_at_persistence(client):
    """手动录入（POST /api/questions）入库前也经过结构归一化，任何路线存储形态一致。"""
    payload = {
        "content": "流感高发，**注意**防护。\n| 周数 | 1 | 2 |\n| --- | --- | --- |\n| 人数 | 30 | 40 |\nA．106 B．105 C．104 D．103",
        "answer_markdown": "答案为 **B**。参见 $x^2$。",
        "question_type": "single_choice",
        "difficulty": "medium",
        "source": "单元测试",
    }
    headers = _auth_headers()
    res = client.post("/api/questions", data=payload, headers=headers)
    assert res.status_code == 200, res.text
    qid = res.json()["question"]["id"]
    detail = client.get(f"/api/questions/{qid}", headers=headers).json()
    # content 入库后应为 LaTeX 风味：加粗 textbf、表格 tabular、选项 choices
    assert r"\textbf{注意}" in detail["content"]
    assert r"\begin{tabular}" in detail["content"]
    assert r"\begin{choices}" in detail["content"]
    # answer 同样归一化
    assert r"\textbf{B}" in detail["answer_markdown"]
    # 普通叙述保留、Markdown 文本骨架不被破坏
    assert "流感高发" in detail["content"]


def test_manual_update_normalizes_at_persistence(client):
    """手动编辑（PUT /api/questions/{id}）同样归一化，保证改后存储仍一致。"""
    headers = _auth_headers()
    create = client.post("/api/questions", data={
        "content": "原题纯文本 A．1 B．2",
        "answer_markdown": "答 **对**",
        "question_type": "single_choice",
        "difficulty": "easy",
        "source": "单元测试",
    }, headers=headers)
    qid = create.json()["question"]["id"]
    upd = client.put(f"/api/questions/{qid}", data={
        "content": "改后 **重点** 题。\n| a | b |\n| --- | --- |\n| 1 | 2 |",
        "answer_markdown": "改后答案 **错**",
        "question_type": "single_choice",
        "difficulty": "easy",
        "source": "单元测试",
    }, headers=headers)
    assert upd.status_code == 200, upd.text
    detail = client.get(f"/api/questions/{qid}", headers=headers).json()
    assert r"\textbf{重点}" in detail["content"]
    assert r"\begin{tabular}" in detail["content"]
    assert r"\textbf{错}" in detail["answer_markdown"]


def test_export_restore_tail_image_regains_figure_align():
    """导出侧还原：入库 LaTeX 图还原成 Markdown 图后，「尾随图右置」版面必须恢复。

    这是横向一致（入库 LaTeX）与纵向可用（导出排版）的接缝点。
    """
    from mathbank.latex_normalize import normalize_content_for_latex_storage as N
    from mathbank.paper_helper import (
        restore_storage_images_to_markdown as R,
        build_latex_document,
    )

    normalized = N(
        "某选择题题干文字。\nA．甲 B．乙 C．丙 D．丁\n![](/static/uploads/mineru_x.jpg)",
        "single_choice",
    )
    # 还原后 choices 外的图应变回 Markdown
    restored = R(normalized)
    assert "![](mineru_x.jpg)" in restored
    # choices 内的（此处无）不受影响；导出后应出现右置 minipage 形态
    data = [{"question": {
        "id": 424242, "content": restored, "question_type": "single_choice",
        "answer_markdown": "", "image_paths": ["/static/uploads/mineru_x.jpg"],
        "option_columns": 1,
    }, "score": 5}]
    tex = build_latex_document("测试卷", "", "exam", data, include_answers=False)
    seg = tex[tex.find("MathBank-Question-ID: 424242"):]
    assert r"\begin{minipage}[t]{5.2cm}" in seg, "尾随图右置版面未恢复"
    assert "mineru_x.jpg" in seg


def test_restore_leaves_choice_images_in_choices_env():
    """choices 环境内的选项图不得被还原成 Markdown（否则会被重排成居中块）。"""
    from mathbank.latex_normalize import normalize_content_for_latex_storage as N
    from mathbank.paper_helper import restore_storage_images_to_markdown as R

    normalized = N("某题。\nA．![](/static/uploads/mineru_y.jpg)\nB．图2", "single_choice")
    restored = R(normalized)
    # choices 内的 includegraphics 保留，无对应 Markdown 图
    assert r"\begin{choices}" in restored
    assert "![](" not in restored
