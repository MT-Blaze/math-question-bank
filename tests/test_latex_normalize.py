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
    _split_merged_choice_items,
    _split_merged_option_content,
    strip_empty_solution_choices,
    normalize_for_storage,
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


def test_ai_merged_options_in_existing_choices_get_split():
    """AI 直出的 choices 里把一个 \\item 挤了多个选项 → 程序侧兜底拆开。

    实测坏数据：``\\item 1 B. 1或3 C 3 D.-3``（还漏了首个 A. 标号），
    应拆成 1 / 1或3 / 3 / -3 四个独立 \\item。
    """
    md = (
        "已知集合 $A = \\{ 1,{a}^{2} - {4a},a - 4\\}$ ,且 $- 3 \\in A$ ,则 $a =$ (  )\n"
        "\\begin{choices}\n"
        "\\item 1 B. 1或3 C 3 D.-3\n"
        "\\end{choices}"
    )
    out = latexify_choices(md, "single_choice")
    assert out.count(r"\item ") == 4, out
    assert r"\item 1" in out and r"\item 1或3" in out
    assert r"\item 3" in out and r"\item -3" in out


def test_geometry_options_are_not_mistaken_for_merged_labels():
    """选项里含「点B/圆C」这类命名不得被当成选项标号拆开。"""
    md = (
        "如图所示，下列说法正确的是（  ）\n"
        "\\begin{choices}\n"
        "\\item 点 $B$ 在圆 $C$ 上\n"
        "\\item 点 $A$ 在圆 $D$ 外\n"
        "\\end{choices}"
    )
    out = latexify_choices(md, "single_choice")
    assert out.count(r"\item ") == 2, out
    assert "点 $B$ 在圆 $C$ 上" in out


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


def test_choice_label_dots_consumes_the_original_punctuation():
    r"""选项标号统一成全角点必须**吃掉**原标点，且对已统一的写法幂等。

    前视断言写法只替换标号字母、不消费后面的 `.`/`、`/`．`，于是
    `\item A. 甲` 会排成 `A．. 甲`、已经标准的 `\item B．` 会变成 `B．．`，
    既不幂等又把脏标点写进库与 PDF。裸标号（后面没有任何标点）保持原样。
    """

    from mathbank.latex_normalize import normalize_choice_label_dots

    env = "\\begin{choices}\n%s\n\\end{choices}"
    unified = normalize_choice_label_dots(env % "\\item A. 甲\n\\item B、乙\n\\item C，丙\n\\item D: 丁")
    assert unified == env % "\\item A． 甲\n\\item B．乙\n\\item C．丙\n\\item D． 丁", unified
    assert normalize_choice_label_dots(unified) == unified, "统一后必须幂等"
    bare = env % "\\item E\n\\item F．已有全角点"
    assert normalize_choice_label_dots(bare) == bare, normalize_choice_label_dots(bare)
    assert "．．" not in normalize_choice_label_dots(env % "\\item A．")


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


def test_normalize_choice_paren_macro():
    """选择题末尾作答空括号 -> \\paren；幂等；非选择题不动。"""
    from mathbank.latex_normalize import normalize_choice_paren_macro as f

    # 三种常见空括号形态都要归一
    assert f("已知 $x>0$，下列正确的是（）", "single_choice").endswith(r" \paren")
    assert f("下列正确的是(  )", "single_choice").endswith(r" \paren")
    assert f(r"下列正确的是 $(\quad)$", "single_choice").endswith(r" \paren")

    # 幂等：重复执行字节不变
    a = f("下列正确的是（）", "single_choice")
    assert f(a, "single_choice") == a

    # 含 choices 时只在它之前补，且数量唯一
    out = f("下列正确的是（）\n\\begin{choices}\n\\item 甲\n\\end{choices}", "single_choice")
    assert out.count(r"\paren") == 1
    assert r"\begin{choices}" in out
    # ⚠️ 位置比数量更重要：补到 \begin{choices} 之后就会变成选项内容
    assert out.index(r"\paren") < out.index(r"\begin{choices}")

    # 非选择题（含填空题）不动
    assert f("求解集。", "detailed_answer") == "求解集。"
    assert f("最小值为____", "fill_in_blank") == "最小值为____"

    # 空串安全
    assert f("", "single_choice") == ""


def test_two_clean_choice_stem_parentheses_implementations_do_not_drift():
    """`paper_helper` 与 `latex_normalize` 各有一份同名实现（导出侧 / 入库侧）。

    两份是逐字复制的，行为必须**逐字节一致**——否则同一道题在「入库」与「导出」
    两条路上会得到不同源码（这正是当初把三处接入点排错顺序时被抓到的问题类型）。
    这里用一批真实形态钉住它们不漂移。
    """
    from mathbank.latex_normalize import clean_choice_stem_parentheses as inner
    from mathbank.paper_helper import clean_choice_stem_parentheses as outer

    cases = [
        "下列正确的是（）",                       # 全角空括号
        "下列正确的是(  )",                       # 半角空括号
        "下列正确的是（ ）",                      # 全角括号 + 半角空格
        "下列正确的是（　　）",                   # 已是全角空格括号
        r"下列正确的是 $(\quad)$",                # 行内数学里的 quad
        r"下列正确的是 （$\qquad$）",             # 全角括号 + 数学内 quad
        r"下列正确的是 $(\hspace{2em})$",         # hspace
        r"下列正确的是 \paren",                   # 已含 \paren
        "下列正确的是（）（）",                   # 多个空括号
        "下列正确的是",                           # 无括号（不得凭空加）
        "下列正确的是 $x>0$",                     # 结尾是公式，不得误伤
        "",                                      # 空串
    ]
    assert len(cases) >= 6
    for raw in cases:
        assert inner(raw) == outer(raw), f"两份 clean_choice_stem_parentheses 漂移: {raw!r}"


def test_choice_stem_cleanup_does_not_leave_double_parens():
    """题干末尾「手写空括号 + ``\\paren``」必须收敛成一个 ``\\paren``。

    2026-09-25 修。旧实现的病灶是**两步顺序反了**：先清末尾空括号（正则带 ``$``
    锚定），后删已有的 ``\\paren``。输入像 ``…（　）\\paren`` 时末尾是 ``\\paren``，
    末尾锚定落空、括号清不掉；等删掉 ``\\paren``，括号虽已成末尾却不再重清，
    归一化随后又无条件补一个 ``\\paren`` —— 于是落库成双括号。

    还有两档同样清不掉的常见形态：

    * **括号后跟句末标点**（``（　）.``）—— 中文题面几乎总在作答括号后写句号，
      而旧正则要求括号正好收在文本末尾；
    * **数学模式内的作答括号**（``$A \\cup B = (\\quad)$``）—— 空括号不紧贴前导
      ``$``，所以 ``math_wrapped_parens``（要求 ``$(``）与 ``plain_parens``（要求括号
      收尾）双双匹配不到。现已在末尾锚定里放行「一个收尾的 ``$``」。

    ⚠️ 刻意**不**放行「括号后接正文」（``（　）条件``）：那是嵌在句子中间的空括号，
    不是作答括号，库里 id=277/97/264/430 属这一类，留人工处理。
    """

    from mathbank.latex_normalize import clean_choice_stem_parentheses as clean
    from mathbank.latex_normalize import normalize_choice_paren_macro as f

    # ① 双括号必须被清成一个（档 1：括号紧贴末尾）
    assert clean("题干（ ）\\paren") == "题干"
    assert clean("题干（）\\paren") == "题干"
    assert clean("题干（　　）\\paren") == "题干"
    assert clean(r"题干 $(\quad)$ \paren") == "题干"
    assert clean("题干（ ）（）\\paren") == "题干"
    assert f("题干（ ）\\paren", "single_choice") == "题干 \\paren"
    assert f("题干（ ）\\paren", "single_choice").count(r"\paren") == 1

    # 用户报的两个真实形态
    assert f("设 $S=f(t)$，最近似的是（    ）\\paren", "single_choice") == (
        "设 $S=f(t)$，最近似的是 \\paren")
    assert f("则实数 $a$ 的取值范围是（    ）\\paren", "single_choice") == (
        "则实数 $a$ 的取值范围是 \\paren")

    # ② 数学模式内的作答括号：中文题面常见「$A \cup B = (\quad)$ \paren」，空括号
    #    **不紧贴**前导 $，所以 math_wrapped_parens 匹配不到，得靠「末尾允许一个
    #    收尾 $」兜住。括号前的空格会随括号一起被吃掉（与「题干 （ ）」同款行为），
    #    源码成「$A \cup B =$ \paren」—— $ 仍成对，渲染出来仍是「A ∪ B = （　　）」。
    assert clean(r"则 $A \cup B = (\quad)$ \paren") == r"则 $A \cup B =$"
    assert f(r"则 $A \cup B = (\quad)$ \paren", "single_choice") == r"则 $A \cup B =$ \paren"
    assert f(r"$S = (\quad)$ \paren", "single_choice") == r"$S =$ \paren"
    assert f(r"$x^2=1$ ，则 $A \cup B = (\quad)$ \paren", "single_choice") == (
        r"$x^2=1$ ，则 $A \cup B =$ \paren")
    # 「$( )$ 纯包裹」必须由 math_wrapped_parens 整段删除，不能留一对孤立的 $$
    assert clean(r"纯包裹 $(\quad)$ \paren") == "纯包裹"
    # 括号后还跟着正文（不满足末尾锚定）→ 一律不动
    assert clean(r"分母 $f(\quad)$ 不为零") == r"分母 $f(\quad)$ 不为零"

    # ③ 括号后跟句末标点也要清（档 2）；标点本身原样保留
    assert clean("题干（ ）.") == "题干."
    assert clean("题干（ ）. \\paren") == "题干."
    assert f("题干（ ）.", "single_choice") == "题干. \\paren"
    assert f("题干（ ）. \\paren", "single_choice") == "题干. \\paren"

    # ④ 零误伤：括号里有内容的一律不碰
    assert clean(r"f(x) \paren") == "f(x)"
    assert clean(r"(0,1) \paren") == "(0,1)"
    assert clean(r"集合 $\{x \mid x>0\}$") == r"集合 $\{x \mid x>0\}$"
    assert f(r"f(x) \paren", "single_choice") == r"f(x) \paren"
    assert f(r"(0,1) \paren", "single_choice") == r"(0,1) \paren"
    # 括号后接**正文**（嵌在句子中间）不匹配 —— 那是句中括号，不是作答括号。
    # 库里 id=277「的（）条件.」就属这一类，刻意留给人工审，不在这里自动清。
    assert clean("题干（ ）条件. \\paren") == "题干（ ）条件."

    # ⑤ 只有 \paren 的题零改动（库里 194 道，不能被这次修复碰到）
    assert f("题干 \\paren", "single_choice") == "题干 \\paren"
    assert clean("题干 \\paren") == "题干"

    # ⑥ 幂等 T(T(x)) == T(x)
    for raw in (
        "题干（ ）\\paren",
        "题干（ ）.",
        "题干（）\\paren",
        "题干（ ）. \\paren",
        "题干 \\paren",
        r"f(x) \paren",
        r"(0,1) \paren",
        "设 $S=f(t)$，最近似的是（    ）\\paren",
        r"则 $A \cup B = (\quad)$ \paren",
        r"$S = (\quad)$ \paren",
    ):
        once = f(raw, "single_choice")
        assert f(once, "single_choice") == once, f"归一化不幂等: {raw!r}"


# ── 选项列数（option_columns）：0=自适应，1/2/4=强制列数 ────────────────────
# 判据来自 exam-zh-choices.sty：\begin{choices}[columns=N] 的 N≥1 时跳过自动
# 列数计算（:153-154），从而实现「文字自适应 + 图片按题强制列数」共存。

_CHOICE_IMG = r"\includegraphics[max width=0.42\linewidth,max height=3.2cm,keepaspectratio]{opt.jpg}"
_STEM_IMG = r"\includegraphics[max width=0.85\linewidth]{stem.jpg}"


def _choice_src(items):
    """题干带一张图 + 一个 choices 环境（items 为各选项的内容）。"""
    body = "\n".join(r"\item " + it for it in items)
    return "题干见图 " + _STEM_IMG + "\n\\begin{choices}\n" + body + "\n\\end{choices}"


def test_option_columns_zero_keeps_choices_untouched():
    """0=自适应：不加 [columns=]、选项图保持 0.42 限宽（改动前的行为）。"""
    from mathbank.paper_helper import clean_content_for_latex

    out = clean_content_for_latex(
        _choice_src([_CHOICE_IMG] * 4), "single_choice", option_columns=0
    )
    assert r"\begin{choices}" in out
    assert "[columns=" not in out, "自适应模式不得注入 columns 参数"
    assert out.count(r"max width=0.42\linewidth") == 4


def test_option_columns_four_adds_columns_param():
    """4=强制 4 列：\begin{choices} 变成 \begin{choices}[columns=4]。"""
    from mathbank.paper_helper import clean_content_for_latex

    out = clean_content_for_latex(
        _choice_src([_CHOICE_IMG] * 4), "single_choice", option_columns=4
    )
    assert r"\begin{choices}[columns=4]" in out
    assert out.count("[columns=4]") == 1, "不得重复注入"


def test_option_columns_four_widens_choice_images():
    """强制列数时选项图从 max width=0.42 放宽成 width=0.8（否则被列宽压成几毫米）。"""
    from mathbank.paper_helper import clean_content_for_latex

    out = clean_content_for_latex(
        _choice_src([_CHOICE_IMG] * 4), "single_choice", option_columns=4
    )
    assert out.count(r"width=0.8\linewidth") == 4
    assert "0.42" not in out, "choices 内残留 0.42 会导致图过小"


def test_option_columns_four_leaves_stem_image_untouched():
    """题干里的图走的是 0.85 那条分支，强制列数绝不能波及。"""
    from mathbank.paper_helper import clean_content_for_latex

    out = clean_content_for_latex(
        _choice_src([_CHOICE_IMG] * 4), "single_choice", option_columns=4
    )
    assert out.count(r"max width=0.85\linewidth") == 1
    # 题干图不能变成 0.8（那是 choices 专属的改写）
    assert out.count(r"width=0.8\linewidth") == 4


def test_option_columns_four_text_only_choices_only_adds_param():
    """纯文字选项：只加 [columns=4]，不凭空插入任何图片宽度改写。"""
    from mathbank.paper_helper import clean_content_for_latex

    out = clean_content_for_latex(
        _choice_src(["甲", "乙", "丙", "丁"]), "single_choice", option_columns=4
    )
    assert r"\begin{choices}[columns=4]" in out
    # 纯文字选项里没有任何图：不得凭空插入图片宽度改写，题干图也保持 0.85
    assert r"width=0.8\linewidth" not in out
    assert out.count(r"max width=0.85\linewidth") == 1
    for label in ("甲", "乙", "丙", "丁"):
        assert label in out


# ---------------------------------------------------------------------------
# 单个 \item 内多选项挤压：单标号形态（Q9）拆分 + 防误伤
# ---------------------------------------------------------------------------
# 背景：`_strip_item_choice_labels` 常把行首 A 剥掉，导致「隐含 A + 显式 D」的
# 挤压行只剩一个标号，被旧的 `len(marks) < 2` 判据挡在门外（Q9 实测漏拆）。
# 放宽到「≥1 标号即切」后必须防止正文里的数学变量被误判，故配下面 4 条防误伤闸门。


def test_merged_option_single_label_splits_q9_form():
    """Q9 形态 `<内容> D．<内容>`（只剩一个标号）→ 切成 2 段。"""
    got = _split_merged_option_content(
        "该地平均每年新增 10个湖泊 D．该地平均每年新增至少45个水质好湖泊"
    )
    assert got == [
        "该地平均每年新增 10个湖泊",
        "该地平均每年新增至少45个水质好湖泊",
    ]


def test_merged_option_single_label_splits_halfwidth_period():
    """半角 `<内容> B. <内容>`（只剩一个标号）→ 切成 2 段。"""
    assert _split_merged_option_content("内容 B. 内容") == ["内容", "内容"]


def test_merged_option_single_label_does_not_split_math_variable():
    """防误伤：正文里的数学变量 `点 B. 在圆上` 不得被当成选项标号切开。

    标号前紧邻的「点」只有 1 个汉字，是句子前缀而非选项内容——闸门 4 拦下。
    """
    assert _split_merged_option_content("点 B. 在圆上") == ["点 B. 在圆上"]


def test_merged_option_no_label_returns_single_segment():
    """无任何标号 → 不切，原样返回一段。"""
    assert _split_merged_option_content("内容") == ["内容"]


def test_merged_option_multi_label_still_splits():
    """回归：连续多标号 `甲 B．乙 C．丙 D．丁` 仍要切成 4 段（旧行为不退化）。"""
    assert _split_merged_option_content("甲 B．乙 C．丙 D．丁") == [
        "甲",
        "乙",
        "丙",
        "丁",
    ]


def test_merged_option_single_label_splits_q9_exact_in_choices_env():
    """Q9 精确复现：真实 choices 环境跑完应得 4 个 \\item（原为 3）。"""
    env = (
        "\\begin{choices}\n"
        "    \\item 该地水质差湖泊总量逐年递减\n"
        "    \\item 该地水质好湖泊总量逐年递增\n"
        "    \\item 该地平均每年新增 10个湖泊 D．该地平均每年新增至少45个水质好湖泊\n"
        "  \\end{choices}"
    )
    out = _split_merged_choice_items(env)
    assert out.count(r"\item ") == 4, out
    assert r"\item 该地平均每年新增 10个湖泊" in out
    assert r"\item 该地平均每年新增至少45个水质好湖泊" in out


# ---------------------------------------------------------------------------
# 行内选项（不在行首）：题干行中后部的 4 个 ABCD 选项 -> choices 环境
# ---------------------------------------------------------------------------
# 高中数学选择题恒为 4 个选项。AI 常把选项写在题干行中后部（「... 的最小值为
# A. 8 B. 9 C. 11 D. 13」），行首判据（_CHOICE_LABEL_LINE_RE 的 ^）认不出来，
# 选项就原样留在题干里。判据：恰好 A/B/C/D 四个标号，且每个标号后都紧跟内容
# （不能紧跟下一个标号），否则视为正文，不包装。


def test_inline_options_q8_form_wrapped_into_choices():
    """Q8 真实形态：题干行中后部的 A. 8 B. 9 C. 11 D. 13 -> 4 个 \\item。"""
    out = latexify_choices(
        "已知 $a>1$，则 $2a+b$ 的最小值为 A. 8 B. 9 C. 11 D. 13 \\paren",
        "single_choice",
    )
    assert out.count(r"\item ") == 4, out
    for label in ("8", "9", "11", "13"):
        assert r"\item " + label in out, out
    assert r"\begin{choices}" in out
    # 作答括号属于题干：落在 choices 之前，不落进任何选项
    assert r"\paren" in out
    assert out.index(r"\paren") < out.index(r"\begin{choices}"), out


def test_inline_options_q3_form_wrapped_into_choices():
    """Q3 真实形态：带单位的行内选项 -> 4 个 \\item。"""
    out = latexify_choices(
        "若小李同学年用水量为 $200 \\mathrm{~m}^{3}$ , 则应交水费为 "
        "A. 720 元 B. 540 元 C. 480 元 D. 560 元 \\paren",
        "single_choice",
    )
    assert out.count(r"\item ") == 4, out
    for label in ("720 元", "540 元", "480 元", "560 元"):
        assert r"\item " + label in out, out


def test_inline_options_dun_hao_wrapped_into_choices():
    """顿号标号 A、8 B、9 C、11 D、13 也要包装成 4 个 \\item。"""
    out = latexify_choices("A、8 B、9 C、11 D、13", "single_choice")
    assert out.count(r"\item ") == 4, out
    assert r"\item 8" in out and r"\item 13" in out


def test_inline_abcd_without_content_not_wrapped():
    """防误伤：4 个标号后紧跟另一个标号（无内容）-> 正文，不包装。"""
    out = latexify_choices("已知 A、B、C、D 是四个点", "single_choice")
    assert r"\begin{choices}" not in out, out
    assert out.count(r"\item ") == 0


def test_inline_only_two_labels_not_wrapped():
    """防误伤：只有 2 个标号 -> 不满足『恰好 4 个』，不包装。"""
    out = latexify_choices("点 A、点 B 在圆上", "single_choice")
    assert r"\begin{choices}" not in out, out
    assert out.count(r"\item ") == 0


def test_line_start_options_still_wrapped_regression():
    """回归：原有『行首 A．』形态仍正常包装（不受行内新判据影响）。"""
    out = latexify_choices("A．106 B．105 C．104 D．103", "single_choice")
    assert out.count(r"\item ") == 4, out
    for label in ("106", "105", "104", "103"):
        assert r"\item " + label in out, out
    # 两选项判断题（行首）仍拆两个
    two = latexify_choices("A．对 B．错", "single_choice")
    assert two.count(r"\item ") == 2, two


def test_split_merged_choice_items_unifies_item_indent():
    """choices 内所有 \\item 的前导缩进必须一致（拆出的项也要与同块对齐）。"""
    env = (
        "\\begin{choices}\n"
        "    \\item 甲\n"
        "  \\item 乙\n"                                  # 故意 2 空格
        "    \\item $\\forall x$ C. 命题“丙”\n"          # 合并项，拆成两条
        "  \\end{choices}"
    )
    out = _split_merged_choice_items(env)
    item_lines = [ln for ln in out.split("\n") if ln.strip().startswith("\\item")]
    indents = {len(ln) - len(ln.lstrip()) for ln in item_lines}
    assert indents == {4}, out
    assert out.count(r"\item ") == 4, out
    # 幂等：已统一的再跑一遍不变
    assert _split_merged_choice_items(out) == out


def test_solution_empty_choices_shell_is_purged():
    """解析里的空壳 choices 环境必须彻底删掉（2024 大庆卷第 2 题实测脏形）。

    MinerU 在解析区误留一个孤零零的「B.」标号行，选项包装器把它当成选项，于是
    分析段与详解段之间凭空多出一个只有空 \\item 的 choices 环境——卷面上是一片
    莫名其妙的空白选项。（板块标题本身已被扁平化成加粗短语，见 flatten 的用例。）
    """
    answer = (
        "【答案】D\n\n【解析】\n\n【分析】利用集合的交并补运算，及韦恩图求阴影部分对应的集合.\n\n"
        "B.\n\n"
        "【详解】由题设得 $(-2,0]$。\n\n故选：D"
    )
    out = normalize_for_storage(answer, is_answer=True)
    assert "choices" not in out, out
    assert "\\item" not in out, out
    # 层级扁平化：最外层只有一个【解析】，分析/详解降级为加粗短语，正文一字不删
    assert out.count("【解析】") == 1, out
    assert r"\textbf{分析：}" in out and r"\textbf{详解：}" in out, out
    assert "利用集合的交并补运算" in out and "故选：D" in out, out
    assert "【分析】" not in out and "【详解】" not in out, out
    # 幂等：删干净之后再跑一遍必须字节不变
    assert normalize_for_storage(out, is_answer=True) == out


def test_solution_real_choices_env_is_kept():
    """真选项环境不许被空壳清洗误删：带文字、带图片的都原样保留。"""
    for body in ("\\begin{choices}\n    \\item 2 条\n  \\end{choices}",
                 "\\begin{choices}\n    \\item \\includegraphics{a.jpg}\n  \\end{choices}"):
        text = "解析：\n\n" + body + "\n\n故选：A"
        out = normalize_for_storage(text, is_answer=True)
        assert "\\begin{choices}" in out and "\\item" in out, out
        assert out.count("choices") == 2, out


def test_stem_empty_choices_shell_is_not_purged():
    """题干里的空壳环境是「选项丢了」的现场证据，必须留着给人看见/回填，不许静默删。"""
    stem = "如图（）\n\\begin{choices}\n    \\item \n  \\end{choices}"
    out = normalize_for_storage(stem, question_type="single_choice")
    assert "choices" in out, out


def test_complement_glyph_repaired_at_storage_time():
    r"""补集号自愈也挂在入库链上：卡片预览与题库源码里就不该留着 \check{\partial}。"""
    answer = "所以 $\\check{\\partial}_{R}(A\\cap B)=\\{x\\mid-2<x<5\\}$ ，阴影部分为 $\\check{\\mathrm{o}}_{\\mathrm{R}}(A\\cup B)$"
    out = normalize_for_storage(answer, is_answer=True)
    assert "\\complement_{R}(A\\cap B)" in out, out
    assert "\\complement_{\\mathrm{R}}(A\\cup B)" in out, out
    assert "\\partial" not in out and "\\check" not in out, out
    assert normalize_for_storage(out, is_answer=True) == out


def test_bare_math_variable_wrapped_at_storage_time():
    r"""#36 的变量包裹挂在入库链上：新题录进来就是 $p$、$q$，不等导出预检兜。

    必须晚于 normalize_question_math_markdown：字符层已经包好的 ``$p$`` 在 R10 这里
    因紧邻 ``$`` 被否决，两条规则不会互相加码。
    """
    stem = "若 p 是 q 的充分不必要条件，则实数 a 的取值范围为（）"
    out = normalize_for_storage(stem, "single_choice")

    assert "若 $p$ 是 $q$ 的充分不必要条件" in out, out
    assert r"实数 $a$ 的取值范围" in out, out
    assert normalize_for_storage(out, "single_choice") == out, "二次入库重复加码"


def test_bare_variable_wrapper_leaves_words_and_math_alone_at_storage_time():
    """入库侧的零误伤口径：英文单词、单位、函数名尾巴、全大写缩写都不该被拖进数学态。

    「集合 A」这类**汉字 + 单个大写字母**是要包的（见 test_bare_set_letter_wrapped…），
    所以这里的负例一律挑「左邻不是汉字」或「字母不单独成词」的形态。
    """
    for stem in ("长为 5 m 的绳子", "来源：PDF 原卷，AI 生成",
                 r"则 sin x 的最大值", "app 的 pH 值稳定"):
        out = normalize_for_storage(stem, "single_choice")
        assert "$" not in out, "误包成数学态：%s" % out

    already = "已知 $p$ 与 $ q $，求其交集"
    out = normalize_for_storage(already, "single_choice")
    assert out.count("$") == already.count("$"), out


# ---------------------------------------------------------------------------
# 解析越界拦截 / 层级扁平化 / 分段结构 cases 归一 / 裸集合字母（Bug 2~5）
# ---------------------------------------------------------------------------

def test_solution_truncated_at_swallowed_section_heading():
    r"""解析末尾把下一节大标题吞进来时，从那一行起整段丢弃（Bug 2）。"""
    answer = (
        "【答案】B\n\n【解析】由 $a+b=1$ 得最小值 $\\frac{1}{4}$ ，故选 B。\n\n"
        "三、填空题（每小题 5 分，共 20 分）\n\n12. 已知向量 $\\vec{a}=(1,2)$…"
    )
    out = normalize_for_storage(answer, is_answer=True)
    assert "三、填空题" not in out, out
    assert "已知向量" not in out, out
    assert out.endswith("故选 B。"), out
    assert normalize_for_storage(out, is_answer=True) == out, "二次入库走形"


def test_section_truncation_does_not_eat_real_solution_lines():
    """越界判据收紧到「××题」收尾：解析正文里的「三、由题意可知」与自述条目必须原样留着。"""
    kept = (
        "【答案】B\n\n【解析】三、由题意可知 $a=1$ 时成立。",
        "【答案】B\n\n【解析】分类讨论：一、当 $a>0$ 与二、当 $a<0$ 时取等。",
    )
    for answer in kept:
        out = normalize_for_storage(answer, is_answer=True)
        assert "三、由题意可知" in out or "一、当" in out, (answer, out)

    # 整条解析就是一行越界标题：那是「本题没解析」的真实信号，不许静默抹平。
    only_heading = "三、填空题（每小题 5 分，共 20 分）"
    assert normalize_for_storage(only_heading, is_answer=True) != "", only_heading


def test_section_truncation_handles_crlf_and_paper_volume_marker():
    r"""真实卷面是 CRLF，卷标与下一节标题连排时两行都要截掉。

    原文取自库中 2024 年试卷：``第II卷（非选择题，共90分）`` 后面紧跟
    ``三、填空题：本题共3小题，每小题5分，共20分.``。``split("\n")`` 留下的
    行尾 ``\r`` 会让带括号尾巴的卷标漏判，只剩下一节标题被截。
    """
    answer = (
        "【答案】ACD\r\n\r\n【解析】\r\n\r\n【分析】判断各选项。\r\n\r\n"
        "【详解】$g(x)$ 在 $(0,+\\infty)$ 单调递减，对.故选：ACD\r\n\r\n"
        "第II卷（非选择题，共90分）\r\n\r\n"
        "三、填空题：本题共3小题，每小题5分，共20分.\r\n\r\n"
        "13. 已知函数 $f(x)=x+1$，则 $f(0)=$ \\fillin."
    )
    out = normalize_for_storage(answer, is_answer=True)
    assert "第II卷" not in out, out
    assert "三、填空题" not in out, out
    assert "13. 已知函数" not in out, out
    assert out.endswith("故选：ACD"), out


def test_plain_answers_without_solution_structure_are_not_given_a_heading():
    r"""纯文字答案不是解析正文：不能凭空补【解析】把答案本身变成标题下的正文。"""
    for answer in ("答案解析内容", "见解析", "A"):
        assert normalize_for_storage(answer, is_answer=True) == answer


def test_already_flattened_bold_solution_gets_outer_jiexi():
    r"""上一轮已扁平化的卡片只剩 ``\textbf{分析：}``，这一轮必须在【答案】后补回【解析】。"""
    answer = (
        "【答案】B\n\n"
        "\\textbf{分析：}由题意得 $a=1$。\n\n"
        "\\textbf{详解：}代入验证。"
    )
    out = normalize_for_storage(answer, is_answer=True)
    assert out.count("【解析】") == 1, out
    assert out.startswith("【答案】B"), out
    assert "【解析】" in out.split(r"\textbf{分析：}")[0], out


def test_solution_headings_flattened_to_a_single_outer_jiexi():
    r"""【分析】/【详解】/【点睛】降级为加粗短语，最外层只留一个【解析】，正文一字不删（Bug 3）。"""
    answer = (
        "【答案】ACD\n\n【解析】\n\n【分析】利用基本不等式求最小值.\n\n"
        "【详解】由 $a+b=1$ 得 $\\frac{1}{4}$.\n\n【点睛】注意等号成立条件."
    )
    out = normalize_for_storage(answer, is_answer=True)
    assert out.count("【解析】") == 1, out
    assert "【分析】" not in out and "【详解】" not in out and "【点睛】" not in out, out
    assert r"\textbf{分析：}" in out and r"\textbf{详解：}" in out, out
    assert r"\textbf{点睛：}" in out, out
    # 正文一个字都不能少
    for fragment in ("利用基本不等式求最小值", "由 $a+b=1$ 得", "注意等号成立条件"):
        assert fragment in out, (fragment, out)
    # 【答案】是结构契约（答案字母提取按它定位），不许动
    assert out.startswith("【答案】ACD"), out
    assert "**" not in out, "解析里不许留双星号（库里唯一加粗写法是 \\textbf）"
    assert normalize_for_storage(out, is_answer=True) == out, "二次入库重复加码"


def test_solution_detail_heading_is_promoted_to_jiexi():
    """只有【详解】没有【解析】时，最外层标题归一成标准的【解析】。"""
    answer = "【答案】C\n\n【详解】对于 A，$f(x)$ 的定义域为 $\\mathbf{R}$，故 A 错误。"
    out = normalize_for_storage(answer, is_answer=True)
    assert "【解析】对于 A" in out, out
    assert "【详解】" not in out, out
    assert "定义域" in out, out
    assert normalize_for_storage(out, is_answer=True) == out


def test_flatten_keeps_subquestion_and_protocol_markers_readable():
    """小问标签与协议标记：小问标题降级加粗（信息留着），占位/待核对标记原文不动。"""
    answer = "【答案】\n\n【小问 1 详解】设 $f(x)=x^2$。\n\n【小问 2 详解】取 $x=1$。"
    out = normalize_for_storage(answer, is_answer=True)
    assert r"\textbf{小问 1 详解：}" in out and r"\textbf{小问 2 详解：}" in out, out
    assert "设 $f(x)=x^2$" in out, out

    stem = "已知函数 $f(x)$ 【插图待补: 图3】，则其定义域为"
    assert normalize_for_storage(stem, "single_choice") .count("【插图待补") == 1


def test_braced_aligned_and_array_are_rewritten_to_cases():
    r"""``\left\{ \begin{aligned} \right.`` / ``array{l}`` 一律改成 cases（Bug 4）。"""
    answer = (
        r"【答案】见解析" "\n\n【解析】"
        r"由题意得 $\left\{\begin{aligned}2a&=2\\ a+b&=0\end{aligned}\right.$ ，"
        r"且 $\left\{ \begin{array}{l} a > 0 \\ \Delta \leq 0 \end{array} \right.$ 恒成立。"
    )
    out = normalize_for_storage(answer, is_answer=True)
    assert "aligned" not in out, out
    assert r"\left\{" not in out and "array" not in out, out
    assert out.count(r"\begin{cases}") == 2, out
    assert r"2a&=2\\ a+b&=0" in out, "cases 里的 & 对齐必须原样保留"
    assert normalize_for_storage(out, is_answer=True) == out, "cases 归一不幂等"


def test_cases_wrapped_in_left_brace_is_unwrapped():
    r"""已经是 cases 却又套了一层 ``\left\{ … \right.`` 的，把外壳剥掉。"""
    stem = r"$f(x)=\left\{\begin{cases}2x^{2}-x+3,x\geq0\\ x+3,x<0\end{cases}\right.$"
    out = normalize_for_storage(stem, "detailed_answer")
    assert r"\left\{" not in out, out
    assert r"\begin{cases}" in out, out
    assert normalize_for_storage(out, "detailed_answer") == out


def test_set_braces_and_sign_tables_are_never_touched():
    r"""集合写法与带竖线的符号表不是分段结构：``\left\{ x \mid … \right\}`` 必须原样留着。"""
    for text in (
        r"已知集合 $A=\left\{ x \mid 1 < x < 3 \right\}$",
        r"$\left\{\begin{array}{|c|c|} \hline x & 0 \\ \hline \end{array}\right.$",
    ):
        out = normalize_for_storage(text, "single_choice")
        assert r"\left\{" in out, (text, out)
        assert "cases" not in out, (text, out)


def test_bare_set_letter_wrapped_at_storage_time():
    r"""「集合 A」「点 P」这类大写单字母进数学态，全大写缩写与答案字母免疫（Bug 5）。"""
    stem = "集合分别为 A 和 B，点 P 在圆 O 上，来源：PDF 原卷（AI 生成）"
    out = normalize_for_storage(stem, "single_choice")
    assert "集合分别为 $A$ 和 $B$" in out, out
    assert "点 $P$ 在圆 $O$ 上" in out, out
    for acronym in ("PDF", "AI"):
        assert "$%s$" % acronym not in out, out


def test_bare_set_letter_runs_from_the_real_bank_are_wrapped():
    r"""存量真实形态：``集合 A, B`` / ``集合A，B；`` / ``全集U，集合M，N``。

    大写分支为防「一半斜体一半正体」会放行枚举，但集合语境里每一枚都是数学对象；
    新增判据只锚定集合类关键词，选项枚举 ``判断A、C、D`` 与 ``集合 A = {1,2}`` 不动。
    """
    for raw, expected in (
        ("集合 A, B", "集合 $A$, $B$"),
        ("集合A，B；", "集合$A$，$B$；"),
        ("全集U，集合M，N满足条件", "全集$U$，集合$M$，$N$满足条件"),
        ("集合 A、B、C 中", "集合 $A$、$B$、$C$ 中"),
    ):
        out = normalize_for_storage(raw, "detailed_answer")
        assert expected in out, (raw, out)
        assert normalize_for_storage(out, "detailed_answer") == out, "二次入库走形"

    for untouched in ("元素与集合的关系判断A、C、D", "集合AB", "故选：C."):
        assert normalize_for_storage(untouched, "detailed_answer") == untouched, untouched
    # ``集合 A = {1,2}`` 走字符层的整式包裹（``$A = {1,2}$``），不是本规则的逐字母包裹。
    assert "集合 $A$ =" not in normalize_for_storage("集合 A = {1,2}", "detailed_answer")

    answer = "【答案】D\n\n【解析】故选：D"
    out = normalize_for_storage(answer, is_answer=True)
    assert out.startswith("【答案】D"), out
    assert "故选：D" in out, "答案宣告句式不许被包成数学态"
    assert normalize_for_storage(out, is_answer=True) == out


def test_bare_upper_wrapper_skips_label_contexts():
    r"""标签句式（编号 / 卷面答案字母）不包：「题目 A」「见右图 B」「选项 C」「第 2 组 D」。"""
    for stem in ("同时命中考向 1 与考向 2 的题目 A。", "只命中考向 2 的题目 B。",
                 "见右图 A 所示", "下列选项 D 正确", "第 2 组 A 的同学",
                 "正方形 ABCD 的对角线长"):
        out = normalize_for_storage(stem, "single_choice")
        assert "$A$" not in out and "$B$" not in out and "$D$" not in out, (stem, out)

    # 「对于 A，集合 M」是同一判据的正面对照：逗号后面接正文（集合）而不是接另一个字母。
    out = normalize_for_storage("对于 A，集合 M 中的元素 -1", "single_choice")
    assert "对于 $A$，集合 $M$ 中的元素" in out, out
